#!/usr/bin/env python3
"""Restart a BTC-K guardian component only after repeated health-check failures."""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen


STATE_PATH = Path("/run/btck-mainnet-health.json")
NODE_RPC_URL = "http://127.0.0.1:17110/rpc"
CHAIN_ID = "btck-mainnet-v1"


def fetch_json(url: str, timeout: float = 8.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_node() -> None:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": "guardian-health",
        "method": "getInfo",
        "params": {},
    }).encode("utf-8")
    request = Request(
        NODE_RPC_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8")).get("result", {})
    if result.get("network", {}).get("chain_id") != CHAIN_ID:
        raise RuntimeError("node returned the wrong chain_id")
    height = result.get("dag", {}).get("virtual_blue_order_length")
    if not isinstance(height, int) or height < 1:
        raise RuntimeError("node returned an invalid DAG height")


def check_stratum() -> None:
    status = fetch_json("http://127.0.0.1:3337/status")
    if not isinstance(status.get("workers"), list):
        raise RuntimeError("stratum status has no worker list")


def check_wallet_edge() -> None:
    stats = fetch_json("http://127.0.0.1:18088/api/stats")
    if not isinstance(stats.get("ledger_height"), int):
        raise RuntimeError("wallet edge returned an invalid ledger height")


def check_https() -> None:
    context = ssl._create_unverified_context()
    connection = http.client.HTTPSConnection("127.0.0.1", 443, timeout=8, context=context)
    try:
        connection.request("GET", "/", headers={"Host": "bitcoin-k.com"})
        response = connection.getresponse()
        response.read(1024)
        # The guardian intentionally returns 403 to unlisted source addresses
        # while it remains private. A completed TLS/HTTP response below 500
        # proves Nginx is serving; authorization is checked separately.
        if not 200 <= response.status < 500:
            raise RuntimeError(f"local HTTPS returned {response.status}")
    finally:
        connection.close()


def check_ssh() -> None:
    with socket.create_connection(("127.0.0.1", 22), timeout=5) as connection:
        connection.settimeout(5)
        banner = connection.recv(128)
    if not banner.startswith(b"SSH-"):
        raise RuntimeError("SSH port returned no protocol banner")


def load_state() -> dict[str, int]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {str(key): int(value) for key, value in raw.items()}
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, int]) -> None:
    fd, temporary = tempfile.mkstemp(prefix="btck-health-", dir=str(STATE_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restart(service: str) -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "restart", service],
        check=True,
        timeout=240,
    )


def main() -> None:
    checks: list[tuple[str, Callable[[], None], int, str]] = [
        ("node", check_node, 5, "btck-mainnet-node.service"),
        ("stratum", check_stratum, 3, "btck-mainnet-stratum.service"),
        ("wallet_edge", check_wallet_edge, 3, "btck-mainnet-wallet-edge.service"),
        ("https", check_https, 3, "nginx.service"),
        ("ssh", check_ssh, 3, "ssh.service"),
    ]
    state = load_state()
    for name, check, threshold, service in checks:
        try:
            check()
            if state.get(name, 0):
                print(f"{name}: recovered after {state[name]} failed check(s)")
            state[name] = 0
        except Exception as exc:  # A watchdog must isolate every component check.
            state[name] = state.get(name, 0) + 1
            print(f"{name}: failure {state[name]}/{threshold}: {exc}")
            if state[name] >= threshold:
                try:
                    restart(service)
                    print(f"{name}: restarted {service}")
                    state[name] = 0
                except Exception as restart_exc:
                    print(f"{name}: restart failed: {restart_exc}")

    save_state(state)
    if all(value == 0 for value in state.values()):
        print("all BTC-K guardian health checks passed")


if __name__ == "__main__":
    main()

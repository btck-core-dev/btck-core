#!/usr/bin/env python3
"""One-shot BTC-K public node and dual-pool launch health monitor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


EXPECTED_CHAIN_ID = "btck-mainnet-v1"
EXPECTED_GENESIS = "b9249be0f1b57021a9cfef8241a499c6e975a91e24d6a3fc22ce850000000000"
EXPECTED_FINGERPRINT = "305828370fdbd5c116dd264ab29c9ba908a0f2409ba9f2b5d7e865ea7177c4f0"
ZERO_HASH = "0" * 64


def fetch_json(url: str, timeout: float) -> tuple[dict, float]:
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "btck-public-launch-monitor/1"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return payload, round((time.monotonic() - started) * 1000, 1)


def tcp_check(host: str, port: int, timeout: float) -> float:
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout):
        pass
    return round((time.monotonic() - started) * 1000, 1)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_hash_chained(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = ZERO_HASH
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines:
            previous = str(json.loads(lines[-1]).get("record_hash", ""))
    record = dict(payload)
    record["previous_hash"] = previous
    canonical = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
    record["record_hash"] = hashlib.sha256(canonical).hexdigest()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def counter_delta(current: dict, previous: dict) -> tuple[dict, bool]:
    epoch = str(current.get("epoch", ""))
    previous_epoch = str(previous.get("counter_epoch", ""))
    keys = ("accepted", "rejected", "duplicate_retries")
    values = {key: int(current.get(key, 0) or 0) for key in keys}
    old = {key: int(previous.get(f"lifetime_{key}", 0) or 0) for key in keys}
    comparable = bool(epoch and epoch == previous_epoch) and all(
        values[key] >= old[key] for key in keys
    )
    if not comparable:
        return {key: 0 for key in keys}, False
    return {key: values[key] - old[key] for key in keys}, True


def evaluate_pool(
    name: str,
    payload: dict,
    expected_mode: str,
    max_rejection_rate: float,
    previous: dict,
    min_share_sample: int,
) -> tuple[dict, list[str]]:
    failures: list[str] = []
    mode = str(payload.get("pool_mode", "")).upper()
    if mode != expected_mode:
        failures.append(f"{name}: expected mode {expected_mode}, got {mode or 'missing'}")
    workers = payload.get("workers")
    if not isinstance(workers, list):
        failures.append(f"{name}: workers is not a list")
        workers = []
    counters = payload.get("share_counters", {})
    accepted = int(counters.get("accepted", 0) or 0)
    rejected = int(counters.get("rejected", 0) or 0)
    duplicate_retries = int(counters.get("duplicate_retries", 0) or 0)
    delta, comparable = counter_delta(counters, previous)
    interval_total = delta["accepted"] + delta["rejected"]
    rejection_rate = delta["rejected"] / interval_total if interval_total else 0.0
    if comparable and interval_total >= min_share_sample and rejection_rate > max_rejection_rate:
        failures.append(
            f"{name}: interval rejection rate {rejection_rate:.4f} exceeds {max_rejection_rate:.4f}"
        )
    fee = payload.get("fee_policy", {})
    if fee.get("schedule") != "5% on days 1-7; 3% on days 8-30; 1% from day 31 onward":
        failures.append(f"{name}: fee schedule missing or unexpected")
    return {
        "mode": mode,
        "active_workers": int(payload.get("active_workers", 0) or 0),
        "active_connections": int(payload.get("active_connections", 0) or 0),
        "counter_epoch": str(counters.get("epoch", "")),
        "lifetime_accepted": accepted,
        "lifetime_rejected": rejected,
        "lifetime_duplicate_retries": duplicate_retries,
        "interval_comparable": comparable,
        "interval_accepted": delta["accepted"],
        "interval_rejected": delta["rejected"],
        "interval_duplicate_retries": delta["duplicate_retries"],
        "interval_rejection_rate": rejection_rate,
        "rejected_by_reason": counters.get("rejected_by_reason", {}),
        "fee_rate": fee.get("current_fee_rate"),
        "measurement_age_seconds": payload.get("measurement_age_seconds"),
    }, failures


def read_resources(path: str = "/") -> dict:
    meminfo: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as source:
        for line in source:
            key, value = line.split(":", 1)
            meminfo[key] = int(value.strip().split()[0]) * 1024
    disk = os.statvfs(path)
    service_memory = {}
    for service in (
        "btckd.service",
        "btck-public-stratum-solo.service",
        "btck-public-stratum-pplns.service",
        "btck-mainnet-wallet-edge.service",
    ):
        current = Path("/sys/fs/cgroup/system.slice") / service / "memory.current"
        try:
            service_memory[service] = int(current.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            service_memory[service] = None
    return {
        "memory_available_bytes": meminfo.get("MemAvailable", 0),
        "swap_used_bytes": max(0, meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)),
        "disk_free_bytes": disk.f_bavail * disk.f_frsize,
        "disk_total_bytes": disk.f_blocks * disk.f_frsize,
        "service_memory_bytes": service_memory,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-url", default="http://127.0.0.1:16110/info")
    parser.add_argument("--solo-status-url", default="http://127.0.0.1:3337/status")
    parser.add_argument("--pplns-status-url", default="http://127.0.0.1:3338/status")
    parser.add_argument("--stratum-host", default="127.0.0.1")
    parser.add_argument("--stratum-port", action="append", type=int, default=[])
    parser.add_argument("--max-rejection-rate", type=float, default=0.05)
    parser.add_argument("--min-peers", type=int, default=0)
    parser.add_argument("--min-share-sample", type=int, default=20)
    parser.add_argument("--min-available-memory-mib", type=int, default=700)
    parser.add_argument("--min-disk-free-gib", type=float, default=8.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    args = parser.parse_args()
    ports = args.stratum_port or [3332, 3333, 3334, 3335, 3336]
    failures: list[str] = []
    warnings: list[str] = []
    latencies: dict[str, float] = {}
    previous = load_json(args.latest)

    try:
        node, latencies["node_http_ms"] = fetch_json(args.node_url, args.timeout)
    except Exception as exc:
        node = {}
        failures.append(f"node: {exc}")
    network = node.get("network", {})
    dag = node.get("dag", {})
    if network.get("chain_id") != EXPECTED_CHAIN_ID:
        failures.append("node: chain_id mismatch")
    if network.get("genesis_hash") != EXPECTED_GENESIS:
        failures.append("node: genesis hash mismatch")
    if network.get("consensus_fingerprint") != EXPECTED_FINGERPRINT:
        failures.append("node: consensus fingerprint mismatch")
    if int(dag.get("virtual_blue_order_length", 0) or 0) - 1 < 271_828:
        failures.append("node: below checkpoint height")
    if int(dag.get("issued_supply_sats", -1)) != int(dag.get("utxo_total_sats", -2)):
        failures.append("node: asset conservation failed")
    peers = int(node.get("p2p", {}).get("connected_peers", 0) or 0)
    if peers < args.min_peers:
        failures.append(f"node: connected peers {peers} below {args.min_peers}")
    if peers == 0:
        warnings.append("node: no connected backup peer")
    if bool(node.get("safeguards", {}).get("safe_mode", True)):
        failures.append("node: safe mode active or missing")

    pool_results: dict[str, dict] = {}
    for name, url, mode in (
        ("solo", args.solo_status_url, "SOLO"),
        ("pplns", args.pplns_status_url, "PPLNS"),
    ):
        try:
            payload, latencies[f"{name}_status_ms"] = fetch_json(url, args.timeout)
            pool_results[name], pool_failures = evaluate_pool(
                name,
                payload,
                mode,
                args.max_rejection_rate,
                previous.get("pools", {}).get(name, {}),
                args.min_share_sample,
            )
            failures.extend(pool_failures)
        except Exception as exc:
            pool_results[name] = {}
            failures.append(f"{name}: {exc}")

    for port in ports:
        try:
            latencies[f"stratum_{port}_tcp_ms"] = tcp_check(args.stratum_host, port, args.timeout)
        except Exception as exc:
            failures.append(f"stratum {port}: {exc}")

    resources = read_resources()
    if resources["memory_available_bytes"] < args.min_available_memory_mib * 1024 * 1024:
        failures.append("host: available memory below safety floor")
    if resources["disk_free_bytes"] < args.min_disk_free_gib * 1024 * 1024 * 1024:
        failures.append("host: free disk below safety floor")

    result = {
        "format": "btck-public-launch-monitor-v2",
        "captured_at": int(time.time()),
        "verification": "PASS" if not failures else "FAIL",
        "node": {
            "height": int(dag.get("virtual_blue_order_length", 0) or 0) - 1,
            "total_blocks": int(dag.get("total_blocks", 0) or 0),
            "tips_count": int(dag.get("tips_count", 0) or 0),
            "connected_peers": peers,
            "safe_mode": node.get("safeguards", {}).get("safe_mode"),
            "state_root": dag.get("state_root"),
            "issued_supply_sats": dag.get("issued_supply_sats"),
            "utxo_total_sats": dag.get("utxo_total_sats"),
        },
        "pools": pool_results,
        "resources": resources,
        "latencies": latencies,
        "warnings": warnings,
        "failures": failures,
    }
    atomic_json(args.latest, result)
    append_hash_chained(args.history, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if failures:
        raise SystemExit("public launch monitor failed")


if __name__ == "__main__":
    main()

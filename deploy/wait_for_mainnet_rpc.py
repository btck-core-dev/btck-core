#!/usr/bin/env python3
"""Wait until the local BTC-K MAINNET RPC has completed replay and is ready."""

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:17110/rpc")
    parser.add_argument("--chain-id", default="btck-mainnet-v1")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": "systemd-readiness",
        "method": "getInfo",
        "params": {},
    }).encode("utf-8")
    last_error = "RPC not ready"

    while time.monotonic() < deadline:
        try:
            request = Request(
                args.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=3) as response:
                result = json.loads(response.read().decode("utf-8")).get("result", {})
            network = result.get("network", {})
            order_length = result.get("dag", {}).get("virtual_blue_order_length")
            if network.get("chain_id") == args.chain_id and isinstance(order_length, int):
                print(f"BTC-K RPC ready on {args.chain_id} at order length {order_length}")
                return
            last_error = "RPC returned the wrong chain or an incomplete DAG status"
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(2)

    raise SystemExit(f"BTC-K RPC readiness timeout: {last_error}")


if __name__ == "__main__":
    main()

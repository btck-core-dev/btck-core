#!/usr/bin/env python3
"""Capture fail-closed readiness evidence from one locally reachable node."""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:16110/info")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-height", type=int, default=271_828)
    parser.add_argument("--min-peers", type=int, default=1)
    args = parser.parse_args()
    info = json.load(urlopen(args.url, timeout=10))
    network = info["network"]
    dag = info["dag"]
    height = int(dag["virtual_blue_order_length"]) - 1
    peers = int(info.get("p2p", {}).get("connected_peers", 0))
    failures = []
    if network.get("name") != "mainnet":
        failures.append("network is not mainnet")
    if height < args.min_height:
        failures.append("node has not reached required height")
    if peers < args.min_peers:
        failures.append("node lacks a live P2P peer")
    if int(dag["issued_supply_sats"]) != int(dag["utxo_total_sats"]):
        failures.append("node violates asset conservation")
    evidence = {
        "format": "btck-public-node-readiness-v1",
        "verification": "PASS" if not failures else "FAIL",
        "captured_at": int(time.time()),
        "node_id": args.node_id,
        "network": network["name"],
        "chain_id": network["chain_id"],
        "genesis_hash": network["genesis_hash"],
        "consensus_fingerprint": network["consensus_fingerprint"],
        "ledger_height": height,
        "selected_tip": dag["selected_tip"],
        "state_root": dag["state_root"],
        "connected_peers": peers,
        "issued_supply_sats": int(dag["issued_supply_sats"]),
        "utxo_total_sats": int(dag["utxo_total_sats"]),
        "pool_balance_sats": int(dag["pool_balance_sats"]),
        "failures": failures,
    }
    atomic_json(args.output, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if failures:
        raise SystemExit("node readiness capture failed")


if __name__ == "__main__":
    main()

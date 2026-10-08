#!/usr/bin/env python3
"""Freeze guardian mining at virtual blue height 271,828 and record evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


ROOT = Path("/home/ubuntu/btck-mainnet-guardian")
TARGET_HEIGHT = 271_828
EXPECTED_GENESIS = "b9249be0f1b57021a9cfef8241a499c6e975a91e24d6a3fc22ce850000000000"


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
        parent_stat = path.parent.stat()
        os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target-height", type=int, default=TARGET_HEIGHT)
    parser.add_argument("--no-stop", action="store_true", help="Record status only; never stop Stratum")
    args = parser.parse_args()
    info = json.load(urlopen("http://127.0.0.1:17110/info", timeout=4))
    if info["network"]["genesis_hash"] != EXPECTED_GENESIS:
        raise SystemExit("guardian monitor connected to the wrong genesis")
    heartbeat = {
        "captured_at": int(time.time()),
        "target_height": args.target_height,
        "max_blue_score": int(info["dag"]["max_blue_score"]),
        "virtual_blue_order_length": int(info["dag"]["virtual_blue_order_length"]),
        "total_blocks": int(info["dag"]["total_blocks"]),
        "selected_tip": info["dag"]["selected_tip"],
        "next_bits": int(info["dag"]["next_bits"]),
        "issued_supply_sats": int(info["dag"]["issued_supply_sats"]),
        "pool_balance_sats": int(info["dag"]["pool_balance_sats"]),
    }
    atomic_json(args.root / "evidence/guardian-heartbeat.json", heartbeat)
    if heartbeat["virtual_blue_order_length"] <= args.target_height or args.no_stop:
        return

    candidate_path = args.root / "evidence/checkpoint-271828-candidate.json"
    if candidate_path.exists():
        return
    subprocess.run(["systemctl", "stop", "btck-mainnet-stratum.service"], check=True)

    sys.path.insert(0, str(args.root / "src"))
    from config.network import load_network
    from node.daemon import BTCKDaemon

    network = load_network("mainnet", args.root / "params/networks/mainnet.guardian.json")
    daemon = BTCKDaemon(network=network, data_root=args.root / "data-root", persistent=True)
    if len(daemon.dag.virtual_blue_order) <= args.target_height:
        raise RuntimeError("chain replay did not contain the target virtual blue height")
    checkpoint_hash = daemon.dag.virtual_blue_order[args.target_height]
    block = daemon.dag.blocks[checkpoint_hash]
    payload = {
        "format": "btck-mainnet-checkpoint-candidate-v1",
        "status": "PENDING-INDEPENDENT-REPLAY",
        "captured_at": int(time.time()),
        "height": args.target_height,
        "block_hash": checkpoint_hash,
        "block_timestamp": block.timestamp,
        "genesis_hash": daemon.dag.genesis_hash,
        "consensus_fingerprint_stage_a": network.consensus_fingerprint,
        "state_root": daemon.dag.state_root(),
        "issued_supply_sats": daemon.dag.issued_supply_sats,
        "pool_balance_sats": daemon.dag.pool_balance_sats,
        "virtual_blue_order_length": len(daemon.dag.virtual_blue_order),
        "total_blocks": len(daemon.dag.blocks),
        "replayed_blocks": daemon.replayed_blocks,
    }
    atomic_json(candidate_path, payload)
    subprocess.run(["systemctl", "disable", "--now", "btck-mainnet-checkpoint.timer"], check=True)


if __name__ == "__main__":
    main()

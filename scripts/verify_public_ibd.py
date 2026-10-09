#!/usr/bin/env python3
"""Sync a fresh in-memory node from a public seed and emit IBD evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config.network import load_network
from node.blockdag import BlockDAG
from p2p.p2p_network import P2PNetwork


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


async def run(args) -> dict:
    network = load_network("mainnet", args.manifest)
    checkpoint_hash = network.hard_checkpoints.get(args.target_height)
    if not checkpoint_hash:
        raise SystemExit("manifest does not contain the target hard checkpoint")
    dag = BlockDAG(network=network)
    p2p = P2PNetwork(dag, p2p_host="127.0.0.1", p2p_port=0, seed_peers=[args.seed])
    started = time.monotonic()
    await p2p.start()
    try:
        deadline = started + args.timeout
        last_height = 0
        last_progress = time.monotonic()
        while len(dag.virtual_blue_order) - 1 < args.target_height:
            await asyncio.sleep(1)
            height = len(dag.virtual_blue_order) - 1
            if height > last_height:
                last_height = height
                last_progress = time.monotonic()
            if time.monotonic() - last_progress > args.stall_timeout:
                raise SystemExit(f"IBD stalled at height {height}")
            if time.monotonic() > deadline:
                raise SystemExit(f"IBD timeout at height {height}")
        actual = dag.virtual_blue_order[args.target_height]
        if actual != checkpoint_hash:
            raise SystemExit("fresh-node IBD checkpoint mismatch")
        info = dag.get_dag_info()
        if info["issued_supply_sats"] != info["utxo_total_sats"]:
            raise SystemExit("fresh-node IBD asset conservation failure")
        return {
            "format": "btck-public-ibd-v1",
            "verification": "PASS",
            "captured_at": int(time.time()),
            "seed": args.seed,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "target_height": args.target_height,
            "checkpoint_hash": checkpoint_hash,
            "network": network.name,
            "chain_id": network.chain_id,
            "genesis_hash": network.genesis.expected_hash,
            "consensus_fingerprint": network.consensus_fingerprint,
            "ledger_height": len(dag.virtual_blue_order) - 1,
            "total_blocks": len(dag.blocks),
            "state_root": dag.state_root(),
            "issued_supply_sats": info["issued_supply_sats"],
            "utxo_total_sats": info["utxo_total_sats"],
            "pool_balance_sats": info["pool_balance_sats"],
        }
    finally:
        await p2p.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seed", required=True, help="public seed as host:port")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-height", type=int, default=271_828)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--stall-timeout", type=float, default=120)
    args = parser.parse_args()
    evidence = asyncio.run(run(args))
    atomic_json(args.output, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

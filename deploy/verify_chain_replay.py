#!/usr/bin/env python3
"""Replay a restored chain database and emit consensus identity evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from node.daemon import BTCKDaemon


CONSENSUS_FIELDS = (
    "network",
    "chain_id",
    "genesis_hash",
    "consensus_fingerprint",
    "ledger_height",
    "total_blocks",
    "selected_tip",
    "tips",
    "state_root",
    "issued_supply_sats",
    "utxo_total_sats",
    "pool_balance_sats",
    "eco_fund_utxo_sats",
)


def compare_consensus(actual: dict, expected: dict) -> list[str]:
    mismatches = []
    for field in CONSENSUS_FIELDS:
        if field not in expected:
            mismatches.append(f"expected evidence missing {field}")
        elif actual.get(field) != expected[field]:
            mismatches.append(
                f"{field} mismatch: expected {expected[field]!r}, got {actual.get(field)!r}"
            )
    return mismatches


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="devnet")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--expect", type=Path, help="prior replay evidence to compare")
    parser.add_argument("--output", type=Path, help="atomically write machine evidence")
    parser.add_argument(
        "--write-snapshot",
        action="store_true",
        help="write a startup snapshot when the replay ends exactly at a durable checkpoint",
    )
    args = parser.parse_args()

    started = time.monotonic()
    daemon = BTCKDaemon(
        host="127.0.0.1",
        network=args.network,
        data_root=str(args.data_root),
        persistent=True,
    )
    info = daemon.dag.get_dag_info()
    result = {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "replayed_blocks": daemon.replayed_blocks,
        "ledger_height": len(daemon.dag.virtual_blue_order) - 1,
        "total_blocks": len(daemon.dag.blocks),
        "selected_tip": daemon.dag.selected_tip,
        "tips": sorted(daemon.dag.tips),
        "state_root": info["state_root"],
        "issued_supply_sats": info["issued_supply_sats"],
        "utxo_total_sats": info["utxo_total_sats"],
        "pool_balance_sats": info["pool_balance_sats"],
        "eco_fund_utxo_sats": info["eco_fund_utxo_sats"],
        "asset_conservation": info["issued_supply_sats"] == info["utxo_total_sats"],
        "network": info["network"],
        "chain_id": info["chain_id"],
        "genesis_hash": info["genesis_hash"],
        "consensus_fingerprint": daemon.network.consensus_fingerprint,
        "loaded_snapshot_sequence": daemon.chain_store.loaded_snapshot_sequence,
        "tail_replayed_blocks": daemon.chain_store.tail_replayed_blocks,
    }
    if args.write_snapshot:
        checkpoint = daemon.chain_store.latest_checkpoint()
        if checkpoint is None:
            raise SystemExit("cannot write snapshot: chain has no durable checkpoint")
        checkpoint_sequence, checkpoint_state_root = checkpoint
        if checkpoint_sequence != daemon.replayed_blocks:
            raise SystemExit(
                "cannot write snapshot: replay must end exactly at the latest durable checkpoint "
                f"({daemon.replayed_blocks} records replayed, checkpoint is {checkpoint_sequence})"
            )
        if info["state_root"] != checkpoint_state_root:
            raise SystemExit("cannot write snapshot: replayed state root does not match checkpoint")
        with daemon.chain_store._connect() as connection:
            row = connection.execute(
                "SELECT block_hash FROM accepted_blocks WHERE sequence = ?",
                (checkpoint_sequence,),
            ).fetchone()
        if row is None:
            raise SystemExit("cannot write snapshot: checkpoint block is missing")
        daemon.chain_store.write_snapshot(
            daemon.dag,
            checkpoint_sequence,
            str(row[0]),
            checkpoint_state_root,
        )
        result["snapshot_path"] = str(daemon.chain_store.snapshot_path)
        result["snapshot_sequence"] = checkpoint_sequence
    mismatches = []
    if args.expect:
        expected = json.loads(args.expect.read_text(encoding="utf-8"))
        if not isinstance(expected, dict):
            raise SystemExit("expected evidence must be a JSON object")
        mismatches = compare_consensus(result, expected)
    result["verification"] = "PASS" if result["asset_conservation"] and not mismatches else "FAIL"
    result["mismatches"] = mismatches
    if args.output:
        atomic_write(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["verification"] != "PASS":
        raise SystemExit("chain replay verification failed")


if __name__ == "__main__":
    main()

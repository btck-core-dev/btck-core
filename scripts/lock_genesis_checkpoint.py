#!/usr/bin/env python3
"""Build and independently replay a Stage-B MAINNET checkpoint candidate.

This tool never edits source code, approves a release, or opens a firewall. It
anchors the exact virtual-blue-order hash, replays the complete Stage-A journal
into a fresh fingerprint-bound database, and emits machine evidence. Run it in
two independent environments before issuing an approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config.network import load_network
from node.blockdag import BlockDAG
from node.chain_store import ChainStore, deserialize_block

TARGET_HEIGHT = 271_828


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--output-data-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--target-height", type=int, default=TARGET_HEIGHT)
    args = parser.parse_args()

    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    if candidate.get("status") != "PENDING-INDEPENDENT-REPLAY":
        raise SystemExit("checkpoint candidate is not pending independent replay")
    if int(candidate.get("height", -1)) != args.target_height:
        raise SystemExit("checkpoint candidate height mismatch")
    checkpoint_hash = str(candidate.get("block_hash", "")).lower()
    if len(checkpoint_hash) != 64 or any(ch not in "0123456789abcdef" for ch in checkpoint_hash):
        raise SystemExit("checkpoint candidate hash is malformed")

    source_network = load_network("mainnet", args.source_manifest)
    if candidate.get("genesis_hash") != source_network.genesis.expected_hash:
        raise SystemExit("candidate genesis does not match Stage-A manifest")
    if candidate.get("consensus_fingerprint_stage_a") != source_network.consensus_fingerprint:
        raise SystemExit("candidate fingerprint does not match Stage-A manifest")
    with sqlite3.connect(args.source_db) as connection:
        source_meta = dict(connection.execute("SELECT key, value FROM meta"))
    expected_source_identity = {
        "network": "mainnet",
        "chain_id": source_network.chain_id,
        "genesis_hash": source_network.genesis.expected_hash,
        "consensus_fingerprint": source_network.consensus_fingerprint,
    }
    for key, value in expected_source_identity.items():
        if source_meta.get(key) != value:
            raise SystemExit(f"source journal identity mismatch for {key}")

    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    manifest["consensus"]["hard_checkpoints"] = {str(args.target_height): checkpoint_hash}
    # Bind the public fingerprint to the audited consensus implementation,
    # not only genesis/DAA fields inherited from the private guardian phase.
    manifest["consensus"]["tokenomics"] = {
        "version": "2.4.1",
        "daily_quotas_sha256": "7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59",
        "combo_stage_boundary_liquidation": True,
        "post_mining_fee_split_percent": {"miner": 50, "combo": 50},
    }
    manifest["consensus"]["p2p_protocol_version"] = "1.2.0"
    manifest["release"] = {
        "approved": False,
        "approval_id": "",
        "note": "Stage-B candidate; requires two independent replay PASS records and explicit approval.",
    }
    atomic_json(args.output_manifest, manifest)

    replay_manifest = json.loads(json.dumps(manifest))
    replay_manifest["release"] = {
        "approved": True,
        "approval_id": "REPLAY-ONLY-NOT-A-RELEASE-APPROVAL",
        "note": "Ephemeral manifest used only for deterministic offline replay.",
    }
    with tempfile.TemporaryDirectory(prefix="btck-stage-b-manifest-") as temporary:
        replay_path = Path(temporary) / "mainnet.replay.json"
        replay_path.write_text(json.dumps(replay_manifest), encoding="utf-8")
        target_network = load_network("mainnet", replay_path)

    target_db = target_network.data_dir(args.output_data_root) / "chain.sqlite3"
    if target_db.exists() or target_db.with_name("chain.snapshot.v1.json.zlib").exists():
        raise SystemExit("output replay database already exists; use a fresh output-data-root")
    dag = BlockDAG(network=target_network)
    store = ChainStore(target_db, target_network)
    started = time.monotonic()
    records = 0
    checkpoint_seen = False
    with sqlite3.connect(args.source_db) as connection:
        rows = connection.execute(
            "SELECT payload_json, state_root FROM accepted_blocks ORDER BY sequence"
        )
        for payload_json, source_state_root in rows:
            block = deserialize_block(json.loads(payload_json), target_network)
            if not dag.replay_block(block):
                raise SystemExit(f"duplicate block during Stage-B replay: {block.hash}")
            records += 1
            if len(dag.virtual_blue_order) > args.target_height:
                actual = dag.virtual_blue_order[args.target_height]
                if actual != checkpoint_hash:
                    raise SystemExit(f"checkpoint mismatch: {actual} != {checkpoint_hash}")
                checkpoint_seen = True
            state_root = dag.state_root() if source_state_root else ""
            if source_state_root and state_root != source_state_root:
                raise SystemExit(f"state-root mismatch while replaying {block.hash}")
            store.append_block(block, state_root)

    if not checkpoint_seen:
        raise SystemExit("source journal did not reach the checkpoint height")
    info = dag.get_dag_info()
    if info["issued_supply_sats"] != info["utxo_total_sats"]:
        raise SystemExit("asset conservation failed after Stage-B replay")
    expected_final = {
        "state_root": dag.state_root(),
        "issued_supply_sats": info["issued_supply_sats"],
        "pool_balance_sats": info["pool_balance_sats"],
        "virtual_blue_order_length": len(dag.virtual_blue_order),
        "total_blocks": len(dag.blocks),
        "replayed_blocks": records,
    }
    for key, value in expected_final.items():
        if candidate.get(key) != value:
            raise SystemExit(f"candidate final-state mismatch for {key}")
    evidence = {
        "format": "btck-stage-b-independent-replay-v1",
        "verification": "PASS",
        "environment_id": args.environment_id,
        "captured_at": int(time.time()),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "records_replayed": records,
        "checkpoint_height": args.target_height,
        "checkpoint_hash": checkpoint_hash,
        "genesis_hash": target_network.genesis.expected_hash,
        "stage_a_consensus_fingerprint": source_network.consensus_fingerprint,
        "stage_b_consensus_fingerprint": target_network.consensus_fingerprint,
        "stage_b_manifest_sha256": sha256(args.output_manifest),
        "source_db_sha256": sha256(args.source_db),
        "state_root": dag.state_root(),
        "ledger_height": len(dag.virtual_blue_order) - 1,
        "total_blocks": len(dag.blocks),
        "issued_supply_sats": info["issued_supply_sats"],
        "utxo_total_sats": info["utxo_total_sats"],
        "pool_balance_sats": info["pool_balance_sats"],
    }
    atomic_json(args.evidence, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

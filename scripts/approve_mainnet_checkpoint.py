#!/usr/bin/env python3
"""Approve a Stage-B manifest only after two matching independent replays."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--replay-evidence", type=Path, action="append", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.candidate_manifest.resolve():
        raise SystemExit("approval output must not overwrite the reviewed candidate")
    if len(args.approval_id.strip()) < 16:
        raise SystemExit("approval-id must be an externally assigned, descriptive identifier")
    if len(args.replay_evidence) < 2:
        raise SystemExit("two independent replay records are required")

    manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    if manifest.get("network") != "mainnet" or manifest.get("release", {}).get("approved"):
        raise SystemExit("input must be an unapproved MAINNET Stage-B candidate")
    checkpoints = manifest.get("consensus", {}).get("hard_checkpoints", {})
    checkpoint_hash = checkpoints.get("271828")
    if len(checkpoints) != 1 or not isinstance(checkpoint_hash, str):
        raise SystemExit("candidate must contain exactly checkpoint 271828")
    candidate_sha = digest(args.candidate_manifest)

    records = [json.loads(path.read_text(encoding="utf-8")) for path in args.replay_evidence]
    if len({record.get("environment_id") for record in records}) != len(records):
        raise SystemExit("independent replay environment IDs must be unique")
    stable = (
        "checkpoint_hash", "stage_b_consensus_fingerprint", "genesis_hash",
        "source_db_sha256", "state_root", "ledger_height", "total_blocks",
        "issued_supply_sats", "utxo_total_sats", "pool_balance_sats",
    )
    baseline = {key: records[0].get(key) for key in stable}
    for record in records:
        if record.get("format") != "btck-stage-b-independent-replay-v1":
            raise SystemExit("unsupported replay evidence format")
        if record.get("verification") != "PASS":
            raise SystemExit("replay evidence is not PASS")
        if record.get("stage_b_manifest_sha256") != candidate_sha:
            raise SystemExit("replay evidence was produced from a different candidate manifest")
        if record.get("checkpoint_hash") != checkpoint_hash:
            raise SystemExit("replay checkpoint differs from manifest")
        if {key: record.get(key) for key in stable} != baseline:
            raise SystemExit("independent replay records disagree")
        if record.get("issued_supply_sats") != record.get("utxo_total_sats"):
            raise SystemExit("replay evidence violates asset conservation")

    manifest["release"] = {
        "approved": True,
        "approval_id": args.approval_id.strip(),
        "note": "Public Stage-B approval bound to two independent replay records.",
        "replay_evidence_sha256": {
            str(record["environment_id"]): digest(path)
            for path, record in sorted(
                zip(args.replay_evidence, records),
                key=lambda item: str(item[1]["environment_id"]),
            )
        },
    }
    atomic_json(args.output, manifest)
    print(json.dumps({
        "verification": "PASS",
        "output": str(args.output),
        "approved_manifest_sha256": digest(args.output),
        "checkpoint_hash": checkpoint_hash,
        "approval_id": args.approval_id.strip(),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

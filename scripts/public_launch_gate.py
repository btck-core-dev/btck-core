#!/usr/bin/env python3
"""Final fail-closed gate before BTC-K public ports are enabled."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from config.network import load_network


def load_pass(path: Path, expected_format: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != expected_format or payload.get("verification") != "PASS":
        raise SystemExit(f"evidence is not a {expected_format} PASS: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--trusted-release-public-key", type=Path, required=True)
    parser.add_argument("--node-evidence", type=Path, action="append", required=True)
    parser.add_argument("--ibd-evidence", type=Path, required=True)
    parser.add_argument("--recovery-evidence", type=Path, required=True)
    parser.add_argument("--external-probe-evidence", type=Path, required=True)
    parser.add_argument("--max-recovery-seconds", type=float, default=900)
    parser.add_argument("--max-evidence-age", type=int, default=600)
    args = parser.parse_args()
    network = load_network("mainnet", args.manifest)
    checkpoint_hash = network.hard_checkpoints.get(271_828)
    if not checkpoint_hash:
        raise SystemExit("approved manifest lacks checkpoint 271828")
    subprocess.run([
        sys.executable, str(ROOT / "scripts/verify_mainnet_release.py"), str(args.release_dir),
        "--trusted-public-key", str(args.trusted_release_public_key),
    ], check=True, stdout=subprocess.DEVNULL)

    nodes = [load_pass(path, "btck-public-node-readiness-v1") for path in args.node_evidence]
    if len(nodes) < 2 or len({node["node_id"] for node in nodes}) < 2:
        raise SystemExit("two distinct ready public nodes are required")
    expected = {
        "network": "mainnet",
        "chain_id": network.chain_id,
        "genesis_hash": network.genesis.expected_hash,
        "consensus_fingerprint": network.consensus_fingerprint,
    }
    now = int(time.time())
    for node in nodes:
        if any(node.get(key) != value for key, value in expected.items()):
            raise SystemExit("node evidence identity mismatch")
        if node.get("connected_peers", 0) < 1 or node.get("ledger_height", 0) < 271_828:
            raise SystemExit("node evidence lacks peer connectivity or checkpoint height")
        if now - int(node.get("captured_at", 0)) > args.max_evidence_age:
            raise SystemExit("node evidence is stale")

    ibd = load_pass(args.ibd_evidence, "btck-public-ibd-v1")
    if ibd.get("checkpoint_hash") != checkpoint_hash or ibd.get("consensus_fingerprint") != network.consensus_fingerprint:
        raise SystemExit("fresh-node IBD evidence identity mismatch")
    recovery = json.loads(args.recovery_evidence.read_text(encoding="utf-8"))
    if recovery.get("verification") != "PASS":
        raise SystemExit("recovery replay is not PASS")
    if recovery.get("consensus_fingerprint") != network.consensus_fingerprint:
        raise SystemExit("recovery replay identity mismatch")
    if float(recovery.get("elapsed_seconds", float("inf"))) > args.max_recovery_seconds:
        raise SystemExit("recovery replay exceeds the approved RTO")
    external = load_pass(args.external_probe_evidence, "btck-external-public-probe-v1")
    if now - int(external.get("checked_at", 0)) > args.max_evidence_age:
        raise SystemExit("external probe evidence is stale")
    print(json.dumps({
        "verification": "PASS",
        "checkpoint_hash": checkpoint_hash,
        "consensus_fingerprint": network.consensus_fingerprint,
        "ready_nodes": sorted(node["node_id"] for node in nodes),
        "fresh_ibd_height": ibd["ledger_height"],
        "recovery_elapsed_seconds": recovery["elapsed_seconds"],
        "external_endpoints": len(external["results"]),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

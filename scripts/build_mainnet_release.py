#!/usr/bin/env python3
"""Build a reproducible, checkpoint-bound BTC-K public MAINNET release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_release_candidate import (
    NODE_FILES, NODE_PREFIXES, POOL_FILES, POOL_PREFIXES,
    build_archive, select, tracked_files,
)
from config.network import load_network


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_replay(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "btck-stage-b-independent-replay-v1":
        raise SystemExit(f"unsupported replay evidence: {path}")
    if payload.get("verification") != "PASS":
        raise SystemExit(f"replay evidence is not PASS: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--replay-evidence", type=Path, action="append", required=True)
    parser.add_argument("--wallet-artifact", type=Path, required=True)
    parser.add_argument("--wallet-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/mainnet-release")
    args = parser.parse_args()

    if run("git", "status", "--porcelain"):
        raise SystemExit("refusing to build a public release from a dirty worktree")
    commit = run("git", "rev-parse", "HEAD")
    if args.tag not in run("git", "tag", "--points-at", "HEAD").splitlines():
        raise SystemExit("release tag must point exactly at HEAD")

    network = load_network("mainnet", args.manifest)
    checkpoint_hash = network.hard_checkpoints.get(271_828)
    if not checkpoint_hash or len(network.hard_checkpoints) != 1:
        raise SystemExit("approved MAINNET manifest must contain exactly checkpoint 271828")
    raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    tokenomics = raw_manifest.get("consensus", {}).get("tokenomics", {})
    if tokenomics != {
        "version": "2.4.1",
        "daily_quotas_sha256": "7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59",
        "combo_stage_boundary_liquidation": True,
        "post_mining_fee_split_percent": {"miner": 50, "combo": 50},
    }:
        raise SystemExit("MAINNET manifest does not bind the canonical v2.4.1 implementation")
    if raw_manifest.get("consensus", {}).get("p2p_protocol_version") != "1.2.0":
        raise SystemExit("MAINNET manifest does not bind the paginated IBD protocol")

    replay_records = [load_replay(path) for path in args.replay_evidence]
    if len(replay_records) < 2:
        raise SystemExit("at least two independent replay records are required")
    if len({item["environment_id"] for item in replay_records}) != len(replay_records):
        raise SystemExit("replay evidence environment_id values must be unique")
    expected = {
        "checkpoint_hash": checkpoint_hash,
        "stage_b_consensus_fingerprint": network.consensus_fingerprint,
        "genesis_hash": network.genesis.expected_hash,
    }
    for record in replay_records:
        for key, value in expected.items():
            if record.get(key) != value:
                raise SystemExit(f"replay evidence {key} mismatch")
    stable_fields = (
        "checkpoint_hash", "stage_b_consensus_fingerprint", "genesis_hash",
        "state_root", "ledger_height", "total_blocks", "issued_supply_sats",
        "utxo_total_sats", "pool_balance_sats", "source_db_sha256",
    )
    baseline = {key: replay_records[0].get(key) for key in stable_fields}
    if any({key: item.get(key) for key in stable_fields} != baseline for item in replay_records[1:]):
        raise SystemExit("independent replay records disagree")

    wallet_evidence = json.loads(args.wallet_evidence.read_text(encoding="utf-8"))
    if wallet_evidence.get("network") != "mainnet":
        raise SystemExit("wallet evidence network mismatch")
    if wallet_evidence.get("genesis_hash") != network.genesis.expected_hash:
        raise SystemExit("wallet evidence genesis mismatch")
    if wallet_evidence.get("consensus_fingerprint") != network.consensus_fingerprint:
        raise SystemExit("wallet evidence consensus fingerprint mismatch")
    if wallet_evidence.get("reproducible_build_verified") is not True:
        raise SystemExit("wallet artifact lacks two-build reproducibility evidence")
    wallet = args.wallet_artifact.resolve()
    if not wallet.is_file() or wallet_evidence.get("artifact_sha256") != sha256(wallet):
        raise SystemExit("wallet artifact hash does not match wallet evidence")

    files = tracked_files()
    required = {
        "scripts/lock_genesis_checkpoint.py", "scripts/approve_mainnet_checkpoint.py",
        "scripts/build_mainnet_release.py",
        "scripts/sign_mainnet_release.py", "scripts/verify_mainnet_release.py",
        "scripts/verify_public_ibd.py", "scripts/public_launch_gate.py",
        "deploy/capture_node_readiness.py", "deploy/external_public_probe.py",
        "packaging/reproducible_tar.py", "packaging/verify_wallet_reproducibility.py",
        "packaging/build_wallet_macos.sh", "packaging/WALLET_README.txt",
        "deploy/btck-external-public-probe.service", "deploy/btck-external-public-probe.timer",
        "deploy/verify_chain_replay.py", "src/tokenomics/canonical_daily_quotas.py",
    }
    if not required.issubset(files):
        raise SystemExit("release-critical files must be committed before building")
    node_files = select(files, NODE_FILES | required, NODE_PREFIXES)
    pool_files = select(files, POOL_FILES | required, POOL_PREFIXES)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    node_name = f"btck-node-{args.version}"
    pool_name = f"btck-pool-stack-{args.version}"
    node_archive = output / f"{node_name}.tar.gz"
    pool_archive = output / f"{pool_name}.tar.gz"
    install = (
        "# BTC-K public MAINNET release\n\n"
        f"Version: {args.version}\nCheckpoint #271828: {checkpoint_hash}\n"
        f"Consensus fingerprint: {network.consensus_fingerprint}\n"
        "Verify RELEASE_MANIFEST.json and SHA256SUMS before installation. "
        "Never expose JSON-RPC directly to the public Internet.\n"
    )
    build_archive(node_archive, node_name, node_files, install)
    build_archive(pool_archive, pool_name, pool_files, install)
    wallet_target = output / wallet.name
    shutil.copyfile(wallet, wallet_target)
    manifest_target = output / "mainnet.json"
    shutil.copyfile(args.manifest, manifest_target)
    artifacts = [node_archive, pool_archive, wallet_target, manifest_target]
    checksums = {path.name: sha256(path) for path in artifacts}
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
    )
    commit_epoch = int(run("git", "show", "-s", "--format=%ct", "HEAD"))
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(commit_epoch))
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"BTC-K-{args.version}",
        "documentNamespace": f"https://btck.invalid/spdx/{commit}",
        "creationInfo": {"created": created, "creators": ["Tool: btck-build-mainnet-release"]},
        "packages": [
            {
                "SPDXID": f"SPDXRef-Package-{index}",
                "name": name,
                "versionInfo": args.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": digest}],
            }
            for index, (name, digest) in enumerate(sorted(checksums.items()), 1)
        ],
    }
    (output / "SBOM.spdx.json").write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sbom_sha256 = sha256(output / "SBOM.spdx.json")
    release = {
        "format": "btck-public-mainnet-release-v1",
        "status": "PUBLIC-MAINNET-CHECKPOINT-BOUND",
        "version": args.version,
        "tag": args.tag,
        "source_commit": commit,
        "created": created,
        "genesis_hash": network.genesis.expected_hash,
        "checkpoint_height": 271_828,
        "checkpoint_hash": checkpoint_hash,
        "consensus_fingerprint": network.consensus_fingerprint,
        "release_approval_id": network.release_approval_id,
        "independent_replays": replay_records,
        "wallet_evidence": wallet_evidence,
        "sbom_sha256": sbom_sha256,
        "artifacts": checksums,
        "signature_status": "DETACHED-ED25519-REQUIRED-BEFORE-PUBLICATION",
    }
    (output / "RELEASE_MANIFEST.json").write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(release, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

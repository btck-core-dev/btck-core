#!/usr/bin/env python3
"""Verify a fixed-job ASIC result and create the approved guardian manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config.network import load_network
from consensus.genesis import build_pre_pow_hash, calculate_block_pow
from consensus.kheavyhash import generate_matrix, hash_to_target_int
from consensus.pow import compact_to_target
from mine_mainnet_genesis import atomic_write_json, parse_bits, parse_nonce


APPROVAL_RE = re.compile(r"^BTCK-GUARDIAN-[A-Z0-9-]{8,64}$")


def finalize(manifest_path: Path, result_path: Path, output_path: Path, timestamp: int, approval_id: str) -> dict:
    if not APPROVAL_RE.fullmatch(approval_id):
        raise ValueError("approval id must be a BTCK-GUARDIAN-* ceremony identifier")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if raw.get("network") != "mainnet" or raw.get("release", {}).get("approved") is not False:
        raise ValueError("input must be the locked MAINNET manifest")
    if result.get("format") != "btck-mainnet-genesis-result-v1":
        raise ValueError("unexpected genesis result format")
    genesis = raw["genesis"]
    bits = parse_bits(result["bits"])
    nonce = parse_nonce(result["nonce"])
    outputs = tuple(bytes.fromhex(value) for value in genesis["coinbase_output"])
    expected_identity = {
        "network": "mainnet",
        "chain_id": raw["chain_id"],
        "version": int(genesis["version"]),
        "timestamp": timestamp,
        "headline": genesis["headline"],
        "coinbase_output": genesis["coinbase_output"],
    }
    for key, expected in expected_identity.items():
        if result.get(key) != expected:
            raise ValueError(f"genesis result identity mismatch: {key}")
    if bits != parse_bits(raw["consensus"]["daa"]["initial_bits"]):
        raise ValueError("genesis bits do not match frozen DAA initial bits")
    payload_hash = hashlib.sha256(genesis["headline"].encode("utf-8")).digest()
    pre_pow_hash = build_pre_pow_hash(
        int(genesis["version"]), raw["chain_id"], [], float(timestamp), bits, outputs, payload_hash,
    )
    if result.get("pre_pow_hash") != pre_pow_hash.hex():
        raise ValueError("genesis result pre-PoW hash mismatch")
    pow_hash = calculate_block_pow(
        version=int(genesis["version"]), chain_id=raw["chain_id"], parents=[],
        timestamp=float(timestamp), bits=bits, nonce=nonce, coinbase_output=outputs,
        payload_hash=payload_hash, pre_pow_hash=pre_pow_hash, matrix=generate_matrix(pre_pow_hash),
    )
    if pow_hash.hex() != result.get("expected_hash"):
        raise ValueError("genesis result hash does not recompute")
    if hash_to_target_int(pow_hash) > compact_to_target(bits):
        raise ValueError("genesis result does not satisfy the network target")

    raw["genesis"].update({
        "timestamp": float(timestamp), "bits": bits, "nonce": hex(nonce), "expected_hash": pow_hash.hex(),
    })
    raw["features"]["stratum"] = True
    raw["release"] = {
        "approved": True,
        "approval_id": approval_id,
        "note": (
            "Approved only for the private 271,828-block guardian phase. "
            "Public release requires the independently replayed height-271828 checkpoint and a new approval."
        ),
    }
    atomic_write_json(output_path, raw)
    network = load_network("mainnet", output_path)
    return {
        "status": "PASS",
        "manifest": str(output_path.resolve()),
        "approval_id": network.release_approval_id,
        "genesis_hash": network.genesis.expected_hash,
        "consensus_fingerprint": network.consensus_fingerprint,
        "timestamp": int(network.genesis.timestamp),
        "bits": f"0x{network.genesis.bits:08x}",
        "nonce": hex(network.genesis.nonce),
        "stratum_enabled": network.feature_enabled("stratum"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timestamp", type=int, required=True)
    parser.add_argument("--approval-id", required=True)
    args = parser.parse_args()
    report = finalize(
        args.manifest.resolve(), args.result.resolve(), args.output.resolve(), args.timestamp, args.approval_id,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

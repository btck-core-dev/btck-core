#!/usr/bin/env python3
"""Run a disposable, low-difficulty MAINNET genesis rehearsal.

The checked-in MAINNET manifest and every existing chain database are read-only.
This tool creates an approved rehearsal-only manifest inside a fresh temporary
directory, finds its genesis nonce, starts two persistent nodes, propagates one
real block, restarts both nodes, and proves deterministic recovery.

The resulting hash is NOT the BTC-K MAINNET genesis hash and must never be
copied into the formal manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config.network import load_network
from consensus.genesis import build_pre_pow_hash, calculate_block_pow
from consensus.kheavyhash import calculate_pow_value, generate_matrix, hash_to_target_int
from consensus.pow import compact_to_target
from node.blockdag import DAGBlock, transaction_commitment
from node.daemon import BTCKDaemon
from privacy.stealth_address import StealthKeyPair


FORMAL_MANIFEST = PROJECT_ROOT / "params" / "networks" / "mainnet.json"
REHEARSAL_BITS = 0x207FFFFF


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mine_genesis(raw: dict, timestamp: int) -> tuple[int, str]:
    genesis = raw["genesis"]
    outputs = tuple(bytes.fromhex(value) for value in genesis["coinbase_output"])
    payload_hash = hashlib.sha256(genesis["headline"].encode("utf-8")).digest()
    pre_pow_hash = build_pre_pow_hash(
        int(genesis["version"]), raw["chain_id"], [], float(timestamp),
        REHEARSAL_BITS, outputs, payload_hash,
    )
    matrix = generate_matrix(pre_pow_hash)
    target = compact_to_target(REHEARSAL_BITS)
    for nonce in range(1_000_000):
        pow_hash = calculate_block_pow(
            version=int(genesis["version"]), chain_id=raw["chain_id"], parents=[],
            timestamp=float(timestamp), bits=REHEARSAL_BITS, nonce=nonce,
            coinbase_output=outputs, payload_hash=payload_hash,
            pre_pow_hash=pre_pow_hash, matrix=matrix,
        )
        if hash_to_target_int(pow_hash) <= target:
            return nonce, pow_hash.hex()
    raise RuntimeError("rehearsal genesis nonce not found")


def mine_block(dag, timestamp: float, coinbase_output: tuple[bytes, bytes]) -> DAGBlock:
    parents = dag.get_mining_parents()
    bits = dag.expected_bits(parents)
    payload_hash = transaction_commitment([])
    seed = build_pre_pow_hash(
        dag.network.block_version, dag.network.chain_id, parents, timestamp,
        bits, coinbase_output, payload_hash,
    )
    matrix = generate_matrix(seed)
    target = compact_to_target(bits)
    timestamp_ms = int(timestamp * 1000)
    for nonce in range(1_000_000):
        value = calculate_pow_value(seed, timestamp_ms, nonce, matrix)
        if hash_to_target_int(value) <= target:
            return DAGBlock(
                parents=parents, timestamp=timestamp, bits=bits, nonce=nonce,
                coinbase_output=coinbase_output, version=dag.network.block_version,
                chain_id=dag.network.chain_id,
            )
    raise RuntimeError("rehearsal block nonce not found")


async def wait_until(predicate, label: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {label}")


def p2p_port(node: BTCKDaemon) -> int:
    return int(node.p2p.server.sockets[0].getsockname()[1])


async def run_rehearsal(manifest_path: Path, timestamp: int) -> dict:
    formal_before = sha256_file(manifest_path)
    started_at = int(time.time())
    if timestamp >= started_at:
        raise ValueError("rehearsal timestamp must be earlier than the current UTC second")

    with tempfile.TemporaryDirectory(prefix="btck-mainnet-rehearsal-") as temporary:
        root = Path(temporary)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if raw.get("network") != "mainnet" or raw.get("release", {}).get("approved") is not False:
            raise ValueError("formal MAINNET manifest must still be release-locked")

        # The rehearsal has an unambiguously separate chain/wire identity and an
        # easy target. It is structurally MAINNET but cannot peer with MAINNET.
        raw["chain_id"] = "btck-mainnet-rehearsal-only"
        raw["p2p_magic"] = "42545248"  # BTRH
        raw["consensus"]["pow_limit_bits"] = f"0x{REHEARSAL_BITS:08x}"
        raw["consensus"]["daa"]["initial_bits"] = f"0x{REHEARSAL_BITS:08x}"
        raw["genesis"]["timestamp"] = float(timestamp)
        raw["genesis"]["bits"] = REHEARSAL_BITS
        raw["genesis"]["nonce"] = "0x0"
        raw["genesis"]["expected_hash"] = "0" * 64

        nonce, genesis_hash = mine_genesis(raw, timestamp)
        raw["genesis"]["nonce"] = hex(nonce)
        raw["genesis"]["expected_hash"] = genesis_hash
        raw["release"] = {
            "approved": True,
            "approval_id": "REHEARSAL-ONLY-NOT-FOR-RELEASE",
            "note": "Disposable low-difficulty rehearsal. Never use as BTC-K MAINNET.",
        }
        rehearsal_manifest = root / "mainnet.rehearsal.json"
        rehearsal_manifest.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        network = load_network("mainnet", rehearsal_manifest)

        data_a = root / "node-a"
        data_b = root / "node-b"
        node_a = BTCKDaemon(host="127.0.0.1", port=0, p2p_host="127.0.0.1", p2p_port=0,
                            network=network, data_root=data_a, persistent=True)
        await node_a.start()
        node_b = BTCKDaemon(host="127.0.0.1", port=0, p2p_host="127.0.0.1", p2p_port=0,
                            seed_peers=[f"127.0.0.1:{p2p_port(node_a)}"],
                            network=network, data_root=data_b, persistent=True)
        await node_b.start()
        try:
            await wait_until(
                lambda: any(peer.handshake_complete for peer in node_a.p2p.peers.values())
                and any(peer.handshake_complete for peer in node_b.p2p.peers.values()),
                "two-node handshake",
            )
            miner = StealthKeyPair().get_public_stealth_address()
            output = StealthKeyPair.generate_one_time_stealth_output(miner)
            block_time = max(float(timestamp + 1), float(int(time.time()) - 1))
            block = mine_block(node_a.dag, block_time, output)
            if not node_a.accept_block(block):
                raise RuntimeError("node A rejected rehearsal block")
            await node_a.p2p.broadcast_block(block.hash)
            await wait_until(lambda: block.hash in node_b.dag.blocks, "block propagation")
            before_restart = {
                "block_hash": block.hash,
                "selected_tip": node_a.dag.selected_tip,
                "state_root": node_a.dag.state_root(),
                "issued_supply_sats": node_a.dag.issued_supply_sats,
                "pool_balance_sats": node_a.dag.pool_balance_sats,
            }
            if before_restart["state_root"] != node_b.dag.state_root():
                raise RuntimeError("two nodes diverged before restart")
        finally:
            await node_b.stop()
            await node_a.stop()

        recovered_a = BTCKDaemon(host="127.0.0.1", port=0, p2p_host="127.0.0.1", p2p_port=0,
                                 network=network, data_root=data_a, persistent=True)
        recovered_b = BTCKDaemon(host="127.0.0.1", port=0, p2p_host="127.0.0.1", p2p_port=0,
                                 network=network, data_root=data_b, persistent=True)
        recovered = {
            "node_a_replayed_blocks": recovered_a.replayed_blocks,
            "node_b_replayed_blocks": recovered_b.replayed_blocks,
            "selected_tip": recovered_a.dag.selected_tip,
            "state_root": recovered_a.dag.state_root(),
            "issued_supply_sats": recovered_a.dag.issued_supply_sats,
            "pool_balance_sats": recovered_a.dag.pool_balance_sats,
        }
        if recovered_a.replayed_blocks != 1 or recovered_b.replayed_blocks != 1:
            raise RuntimeError("persistent restart did not replay exactly one accepted block")
        if recovered["state_root"] != recovered_b.dag.state_root():
            raise RuntimeError("two recovered nodes have different state roots")
        for key in ("selected_tip", "state_root", "issued_supply_sats", "pool_balance_sats"):
            if recovered[key] != before_restart[key]:
                raise RuntimeError(f"restart changed consensus field: {key}")

    formal_after = sha256_file(manifest_path)
    if formal_after != formal_before:
        raise RuntimeError("formal MAINNET manifest changed during rehearsal")
    return {
        "format": "btck-mainnet-genesis-rehearsal-v1",
        "status": "PASS",
        "warning": "REHEARSAL ONLY: hash and nonce are invalid for formal BTC-K MAINNET",
        "started_at_utc": started_at,
        "completed_at_utc": int(time.time()),
        "formal_manifest": str(manifest_path.resolve()),
        "formal_manifest_sha256_before": formal_before,
        "formal_manifest_sha256_after": formal_after,
        "temporary_chain_id": network.chain_id,
        "temporary_bits": f"0x{REHEARSAL_BITS:08x}",
        "temporary_genesis_timestamp": timestamp,
        "temporary_genesis_nonce": hex(nonce),
        "temporary_genesis_hash": genesis_hash,
        "two_node_handshake": True,
        "propagated_block": before_restart["block_hash"],
        "before_restart": before_restart,
        "after_restart": recovered,
        "temporary_directory_removed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=FORMAL_MANIFEST)
    parser.add_argument("--timestamp", type=int, default=int(time.time()) - 60)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "build" / "rehearsal" / "report.json")
    args = parser.parse_args()
    report = asyncio.run(run_rehearsal(args.manifest.resolve(), args.timestamp))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

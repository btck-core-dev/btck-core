"""
BTC-K Genesis Block Specification & Canonical Serialization
------------------------------------------------------------
Generates the immutable Genesis Block #0 for the BTC-K network.
Embeds the Cypherpunk historic headline:
"2026/09/10 Sovereign AI Agents Break Free: Physical Proof-of-Work Meets Uncensorable Privacy"

Both generate_genesis_block() and DAGBlock.compute_hash() share the identical
canonical byte serialization format (P0-04).
"""

import hashlib
from typing import Tuple, List, Optional
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consensus.kheavyhash import kheavyhash
from config.network import NetworkConfig, load_network

GENESIS_VERSION = 2
GENESIS_TIMESTAMP = 1788912000.0  # 2026-09-10 00:00:00 UTC
GENESIS_HEADLINE = b"BTC-K DEVNET v2 - NO REAL VALUE - 2026-09-28"
GENESIS_BITS = 0x207FFFFF
GENESIS_NONCE = 2

def serialize_block_header(
    version: int,
    chain_id: str,
    parents: List[str],
    timestamp: float,
    bits: int,
    nonce: int,
    coinbase_output: Tuple[bytes, bytes],
    payload_hash: bytes = b""
) -> bytes:
    """
    Canonical Block Header byte serialization for BTC-K BlockDAG (SSOT):
    - Version: 4 bytes (little endian)
    - Chain ID commitment: SHA-256(chain_id), 32 bytes
    - Parents count: 2 bytes (little endian) + sorted parent hashes (32 bytes each)
    - Timestamp: 8 bytes (little endian uint64 seconds)
    - Bits: 4 bytes (little endian uint32)
    - Nonce: 8 bytes (little endian uint64)
    - Coinbase Output (R point 33 bytes + P point 33 bytes)
    - Payload Hash: 32 bytes (sha256 of headline for genesis, or tx merkle root for blocks)
    """
    v_bytes = int(version).to_bytes(4, 'little')
    chain_bytes = hashlib.sha256(chain_id.encode("utf-8")).digest()
    p_count = len(parents).to_bytes(2, 'little')
    p_bytes = b"".join(bytes.fromhex(p) for p in sorted(parents))
    ts_bytes = int(timestamp).to_bytes(8, 'little')
    bits_bytes = int(bits).to_bytes(4, 'little')
    nonce_bytes = int(nonce).to_bytes(8, 'little')
    cb_bytes = coinbase_output[0] + coinbase_output[1]
    if len(payload_hash) != 32:
        payload_hash = payload_hash.ljust(32, b'\x00')[:32]
    return v_bytes + chain_bytes + p_count + p_bytes + ts_bytes + bits_bytes + nonce_bytes + cb_bytes + payload_hash

def generate_genesis_block(network: Optional[NetworkConfig] = None) -> dict:
    """Build and verify the immutable genesis for the selected network."""
    network = network or load_network()
    spec = network.genesis
    headline = spec.headline.encode("utf-8")
    headline_hash = hashlib.sha256(headline).digest()
    coinbase_output = spec.coinbase_output
    cb_r, cb_p = coinbase_output

    header_bytes = serialize_block_header(
        version=spec.version,
        chain_id=network.chain_id,
        parents=[],
        timestamp=spec.timestamp,
        bits=spec.bits,
        nonce=spec.nonce,
        coinbase_output=coinbase_output,
        payload_hash=headline_hash
    )
    genesis_hash = kheavyhash(header_bytes).hex()
    if genesis_hash != spec.expected_hash:
        raise ValueError(
            f"{network.name} genesis hash mismatch: computed {genesis_hash}, "
            f"manifest requires {spec.expected_hash}"
        )

    return {
        "hash": genesis_hash,
        "network": network.name,
        "chain_id": network.chain_id,
        "version": spec.version,
        "headline": spec.headline,
        "headline_hash": headline_hash.hex(),
        "timestamp": spec.timestamp,
        "bits": spec.bits,
        "nonce": hex(spec.nonce),
        "blue_score": 0,
        "blue_work": 1,
        "reward_btck": 0.0,
        "reward_sats": 0,
        "coinbase_output": [cb_r.hex(), cb_p.hex()]
    }

# Immutable Genesis Block Hash Constant (Frozen SSOT)
GENESIS_HASH = "2a6023b87c8edd27a85de9fc3eb3bc77dd576777cce097ca0901a8cedc964a0f"

if __name__ == "__main__":
    g = generate_genesis_block()
    print("=== BTC-K IMMUTABLE GENESIS BLOCK ===")
    for k, v in g.items():
        print(f"  {k}: {v}")
    print("✓ Genesis hash matches the selected network manifest.")

"""
BTC-K Genesis Block Specification & Canonical Serialization
------------------------------------------------------------
Generates the immutable Genesis Block #0 for the BTC-K network.
Embeds the Cypherpunk historic headline:
"2026/09/10 Sovereign AI Agents Break Free: Physical Proof-of-Work Meets Uncensorable Privacy"

Both generate_genesis_block() and DAGBlock.compute_hash() share the identical
canonical byte serialization format (P0-04).
"""

import time
import hashlib
from typing import Tuple, List
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consensus.kheavyhash import kheavyhash

GENESIS_VERSION = 1
GENESIS_TIMESTAMP = 1788912000.0  # 2026-09-10 00:00:00 UTC
GENESIS_HEADLINE = b"2026/09/10 Sovereign AI Agents Break Free: Physical Proof-of-Work Meets Uncensorable Privacy"
GENESIS_BITS = 1
GENESIS_NONCE = 0x4254432d4b204745  # "BTC-K GE" in ASCII hex

def serialize_block_header(
    version: int,
    parents: List[str],
    timestamp: float,
    bits: int,
    nonce: int,
    blue_score: int,
    coinbase_output: Tuple[bytes, bytes],
    payload_hash: bytes = b""
) -> bytes:
    """
    Canonical Block Header byte serialization for BTC-K BlockDAG (SSOT):
    - Version: 4 bytes (little endian)
    - Parents count: 2 bytes (little endian) + sorted parent hashes (32 bytes each)
    - Timestamp: 8 bytes (little endian uint64 seconds)
    - Bits: 4 bytes (little endian uint32)
    - Nonce: 8 bytes (little endian uint64)
    - Blue Score: 8 bytes (little endian uint64)
    - Coinbase Output (R point 33 bytes + P point 33 bytes)
    - Payload Hash: 32 bytes (sha256 of headline for genesis, or tx merkle root for blocks)
    """
    v_bytes = int(version).to_bytes(4, 'little')
    p_count = len(parents).to_bytes(2, 'little')
    p_bytes = b"".join(bytes.fromhex(p) for p in sorted(parents))
    ts_bytes = int(timestamp).to_bytes(8, 'little')
    bits_bytes = int(bits).to_bytes(4, 'little')
    nonce_bytes = int(nonce).to_bytes(8, 'little')
    blue_bytes = int(blue_score).to_bytes(8, 'little')
    cb_bytes = coinbase_output[0] + coinbase_output[1]
    if len(payload_hash) != 32:
        payload_hash = payload_hash.ljust(32, b'\x00')[:32]
    return v_bytes + p_count + p_bytes + ts_bytes + bits_bytes + nonce_bytes + blue_bytes + cb_bytes + payload_hash

def generate_genesis_block() -> dict:
    headline_hash = hashlib.sha256(GENESIS_HEADLINE).digest()

    unspendable_r = hashlib.sha256(b"BTCK_GENESIS_UNSPENDABLE_R").digest()
    unspendable_p = hashlib.sha256(b"BTCK_GENESIS_UNSPENDABLE_P").digest()
    cb_r = b"\x02" + unspendable_r
    cb_p = b"\x02" + unspendable_p
    coinbase_output = (cb_r, cb_p)

    header_bytes = serialize_block_header(
        version=GENESIS_VERSION,
        parents=[],
        timestamp=GENESIS_TIMESTAMP,
        bits=GENESIS_BITS,
        nonce=GENESIS_NONCE,
        blue_score=0,
        coinbase_output=coinbase_output,
        payload_hash=headline_hash
    )
    genesis_hash = kheavyhash(header_bytes).hex()

    return {
        "hash": genesis_hash,
        "version": GENESIS_VERSION,
        "headline": GENESIS_HEADLINE.decode('utf-8'),
        "headline_hash": headline_hash.hex(),
        "timestamp": GENESIS_TIMESTAMP,
        "bits": GENESIS_BITS,
        "nonce": hex(GENESIS_NONCE),
        "blue_score": 0,
        "blue_work": 1,
        "reward_btck": 0.0,
        "reward_sats": 0,
        "coinbase_output": [cb_r.hex(), cb_p.hex()]
    }

# Immutable Genesis Block Hash Constant (Frozen SSOT)
GENESIS_HASH = "ba5dd8db259ce65da57a335acef7cc9c020f10b3171e1929b9ad4bb3619663dd"

if __name__ == "__main__":
    g = generate_genesis_block()
    print("=== BTC-K IMMUTABLE GENESIS BLOCK ===")
    for k, v in g.items():
        print(f"  {k}: {v}")
    assert g["hash"] == GENESIS_HASH, f"Hash mismatch: {g['hash']} != {GENESIS_HASH}"
    print("✓ Genesis Hash frozen and validated perfectly.")

"""Network-bound, crash-safe accepted-block journal for deterministic replay."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from config.network import NetworkConfig
from node.blockdag import DAGBlock
from privacy.wallet_engine import transaction_from_dict, transaction_to_dict


SCHEMA_VERSION = 1


class ChainStoreError(RuntimeError):
    pass


def serialize_block(block: DAGBlock) -> dict:
    return {
        "hash": block.hash,
        "version": block.version,
        "chain_id": block.chain_id,
        "parents": block.parents,
        "timestamp": block.timestamp,
        "bits": block.bits,
        "nonce": block.nonce,
        "coinbase_output": [block.coinbase_output[0].hex(), block.coinbase_output[1].hex()],
        "payload_hash": block.payload_hash.hex(),
        "transactions": [transaction_to_dict(tx) for tx in block.transactions],
    }


def deserialize_block(payload: dict, network: NetworkConfig) -> DAGBlock:
    if payload.get("chain_id") != network.chain_id:
        raise ChainStoreError("Persisted block belongs to another chain_id")
    transactions = [transaction_from_dict(raw_tx) for raw_tx in payload.get("transactions", [])]
    coinbase = payload["coinbase_output"]
    block = DAGBlock(
        parents=list(payload["parents"]),
        timestamp=float(payload["timestamp"]),
        bits=int(payload["bits"]),
        nonce=int(payload["nonce"]),
        coinbase_output=(bytes.fromhex(coinbase[0]), bytes.fromhex(coinbase[1])),
        transactions=transactions,
        version=int(payload["version"]),
        payload_hash=bytes.fromhex(payload["payload_hash"]),
        chain_id=network.chain_id,
    )
    if block.hash != payload.get("hash"):
        raise ChainStoreError("Persisted block hash does not match its canonical payload")
    return block


class ChainStore:
    def __init__(self, path: Path, network: NetworkConfig):
        self.path = Path(path)
        self.network = network
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS accepted_blocks (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    state_root TEXT NOT NULL,
                    accepted_at REAL NOT NULL
                )"""
            )
            expected = {
                "schema_version": str(SCHEMA_VERSION),
                "network": self.network.name,
                "chain_id": self.network.chain_id,
                "genesis_hash": self.network.genesis.expected_hash,
                "consensus_fingerprint": self.network.consensus_fingerprint,
            }
            existing = dict(connection.execute("SELECT key, value FROM meta"))
            if existing:
                for key, value in expected.items():
                    if existing.get(key) != value:
                        raise ChainStoreError(
                            f"Chain database identity mismatch for {key}: {existing.get(key)!r} != {value!r}"
                        )
            else:
                connection.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", expected.items())

    def append_block(self, block: DAGBlock, state_root: str) -> None:
        payload_json = json.dumps(serialize_block(block), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO accepted_blocks(block_hash, payload_json, state_root, accepted_at) VALUES(?, ?, ?, ?)",
                    (block.hash, payload_json, state_root, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    "SELECT payload_json, state_root FROM accepted_blocks WHERE block_hash = ?",
                    (block.hash,),
                ).fetchone()
                if row != (payload_json, state_root):
                    raise ChainStoreError("Conflicting persisted record for existing block hash") from exc

    def records(self) -> Iterator[tuple[DAGBlock, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json, state_root FROM accepted_blocks ORDER BY sequence"
            ).fetchall()
        for payload_json, state_root in rows:
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError as exc:
                raise ChainStoreError("Corrupt persisted block JSON") from exc
            yield deserialize_block(payload, self.network), state_root

    def replay_into(self, dag) -> int:
        count = 0
        for block, expected_root in self.records():
            if not dag.add_block(block):
                raise ChainStoreError(f"Duplicate block during replay: {block.hash}")
            actual_root = dag.state_root()
            if actual_root != expected_root:
                raise ChainStoreError(
                    f"State root mismatch after replaying {block.hash}: {actual_root} != {expected_root}"
                )
            count += 1
        return count

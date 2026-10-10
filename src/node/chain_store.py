"""Network-bound, crash-safe accepted-block journal for deterministic replay."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import sqlite3
import tempfile
import time
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from config.network import NetworkConfig
from node.blockdag import DAGBlock
from node.utxo_set import UTXOEntry
from consensus.combo_state_machine import ComboEpoch, ComboStateMachine, LotteryResolution
from privacy.wallet_engine import transaction_from_dict, transaction_to_dict


SCHEMA_VERSION = 1
SNAPSHOT_FORMAT_VERSION = 1
SNAPSHOT_FILENAME = "chain.snapshot.v1.json.zlib"


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


def deserialize_block(payload: dict, network: NetworkConfig, *, verify_hash: bool = True) -> DAGBlock:
    if payload.get("chain_id") != network.chain_id:
        raise ChainStoreError("Persisted block belongs to another chain_id")
    transactions = [transaction_from_dict(raw_tx) for raw_tx in payload.get("transactions", [])]
    coinbase = payload["coinbase_output"]
    persisted_hash = str(payload.get("hash", ""))
    try:
        if len(bytes.fromhex(persisted_hash)) != 32:
            raise ValueError
    except ValueError as exc:
        raise ChainStoreError("Persisted block hash is not 32-byte hex") from exc
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
        _known_hash=None if verify_hash else persisted_hash,
    )
    if verify_hash and block.hash != persisted_hash:
        raise ChainStoreError("Persisted block hash does not match its canonical payload")
    return block


class ChainStore:
    def __init__(self, path: Path, network: NetworkConfig):
        self.path = Path(path)
        self.network = network
        self.checkpoint_interval = max(100, int(os.getenv("BTCK_STATE_ROOT_CHECKPOINT_INTERVAL", "1000")))
        self.snapshot_interval = max(1000, int(os.getenv("BTCK_CONSENSUS_SNAPSHOT_INTERVAL", "10000")))
        self.snapshot_path = self.path.with_name(SNAPSHOT_FILENAME)
        self.loaded_snapshot_sequence = 0
        self.tail_replayed_blocks = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        with self._connect() as connection:
            self._record_count = int(connection.execute("SELECT COUNT(*) FROM accepted_blocks").fetchone()[0])

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

    def should_checkpoint_next_block(self) -> bool:
        return (self._record_count + 1) % self.checkpoint_interval == 0

    def should_snapshot(self, sequence: int) -> bool:
        return sequence > 0 and sequence % self.snapshot_interval == 0

    def latest_checkpoint(self) -> tuple[int, str] | None:
        """Return the newest durable consensus checkpoint without replaying."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sequence, state_root FROM accepted_blocks "
                "WHERE state_root != '' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return (int(row[0]), str(row[1])) if row else None

    def append_block(self, block: DAGBlock, state_root: str = "") -> int:
        payload_json = json.dumps(serialize_block(block), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO accepted_blocks(block_hash, payload_json, state_root, accepted_at) VALUES(?, ?, ?, ?)",
                    (block.hash, payload_json, state_root, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    "SELECT sequence, payload_json, state_root FROM accepted_blocks WHERE block_hash = ?",
                    (block.hash,),
                ).fetchone()
                if row is None or row[1:] != (payload_json, state_root):
                    raise ChainStoreError("Conflicting persisted record for existing block hash") from exc
                sequence = int(row[0])
            else:
                self._record_count += 1
                sequence = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        return sequence

    @staticmethod
    def _entry_payload(entry: UTXOEntry) -> dict:
        return asdict(entry)

    def _snapshot_payload(self, dag, sequence: int, checkpoint_block_hash: str, state_root: str) -> dict:
        blocks = []
        consensus_fields = (
            "blue_work", "selected_parent", "merge_set", "merge_set_blues", "merge_set_reds",
            "blue_anticone_sizes", "blue_score", "is_combo", "minted_sats", "reward_sats",
            "miner_base_sats", "pool_deposit_sats", "eco_deposit_sats", "combo_payout_sats",
            "fees_sats",
        )
        for block_hash in sorted(dag.blocks):
            block = dag.blocks[block_hash]
            blocks.append({
                "block": serialize_block(block),
                "consensus": {field: getattr(block, field) for field in consensus_fields},
            })

        utxos = [
            {"txid": txid, "vout": vout, "entry": self._entry_payload(entry)}
            for (txid, vout), entry in sorted(dag.utxo_set.utxos.items())
        ]
        spent_undo = [
            {
                "block_hash": block_hash,
                "entries": [
                    {"txid": outpoint[0], "vout": outpoint[1], "entry": self._entry_payload(entry)}
                    for outpoint, entry in entries
                ],
            }
            for block_hash, entries in sorted(dag.utxo_set._block_spent_undo.items())
        ]
        created_undo = [
            {"block_hash": block_hash, "outpoints": [[txid, vout] for txid, vout in entries]}
            for block_hash, entries in sorted(dag.utxo_set._block_created_undo.items())
        ]
        epochs = [asdict(epoch) for _, epoch in sorted(dag.combo_machine.epochs.items())]
        return {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "identity": {
                "network": self.network.name,
                "chain_id": self.network.chain_id,
                "genesis_hash": self.network.genesis.expected_hash,
                "consensus_fingerprint": self.network.consensus_fingerprint,
            },
            "checkpoint": {
                "sequence": sequence,
                "block_hash": checkpoint_block_hash,
                "state_root": state_root,
            },
            "state": {
                "blocks": blocks,
                "tips": sorted(dag.tips),
                "selected_tip": dag.selected_tip,
                "selected_chain": dag.virtual_selected_parent_chain,
                "virtual_blue_order": dag.virtual_blue_order,
                "issued_supply_sats": dag.issued_supply_sats,
                "pool_balance_sats": dag.pool_balance_sats,
                "utxos": utxos,
                "block_spent_undo": spent_undo,
                "block_created_undo": created_undo,
                "block_fees": dict(sorted(dag.utxo_set._block_fees.items())),
                "block_accepted_txids": dict(sorted(dag.utxo_set._block_accepted_txids.items())),
                "combo": {
                    "active_epoch_id": dag.combo_machine.active_epoch_id,
                    "pending_draws": dag.combo_machine.pending_draws,
                    "epochs": epochs,
                },
            },
        }

    def write_snapshot(self, dag, sequence: int, checkpoint_block_hash: str, state_root: str) -> None:
        """Atomically persist a network-bound consensus image for tail-only replay."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT block_hash, state_root FROM accepted_blocks WHERE sequence = ?", (sequence,)
            ).fetchone()
        if row != (checkpoint_block_hash, state_root) or not state_root:
            raise ChainStoreError("Snapshot checkpoint does not match the durable journal")

        payload = self._snapshot_payload(dag, sequence, checkpoint_block_hash, state_root)
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope = {
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "payload": payload,
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(encoded, level=6)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.snapshot_path.name}.", dir=self.snapshot_path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.snapshot_path)
            directory_fd = os.open(self.snapshot_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _resolution_from_payload(raw: dict | None) -> LotteryResolution | None:
        return LotteryResolution(**raw) if raw else None

    def _load_snapshot(self, dag) -> int:
        if not self.snapshot_path.exists():
            return 0
        try:
            encoded = zlib.decompress(self.snapshot_path.read_bytes())
            envelope = json.loads(encoded)
            payload = envelope["payload"]
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (OSError, zlib.error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ChainStoreError("Consensus snapshot is unreadable or malformed") from exc
        digest = hashlib.sha256(payload_bytes).hexdigest()
        if not hmac.compare_digest(str(envelope.get("payload_sha256", "")), digest):
            raise ChainStoreError("Consensus snapshot content hash mismatch")
        if payload.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise ChainStoreError("Unsupported consensus snapshot format")
        expected_identity = {
            "network": self.network.name,
            "chain_id": self.network.chain_id,
            "genesis_hash": self.network.genesis.expected_hash,
            "consensus_fingerprint": self.network.consensus_fingerprint,
        }
        if payload.get("identity") != expected_identity:
            raise ChainStoreError("Consensus snapshot identity mismatch")

        checkpoint = payload.get("checkpoint", {})
        sequence = int(checkpoint.get("sequence", 0))
        checkpoint_hash = str(checkpoint.get("block_hash", ""))
        expected_root = str(checkpoint.get("state_root", ""))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT block_hash, state_root FROM accepted_blocks WHERE sequence = ?", (sequence,)
            ).fetchone()
        if row != (checkpoint_hash, expected_root) or not expected_root:
            raise ChainStoreError("Consensus snapshot checkpoint does not match the durable journal")

        try:
            state = payload["state"]
            restored_blocks = {}
            for item in state["blocks"]:
                block = deserialize_block(item["block"], self.network, verify_hash=False)
                for field, value in item["consensus"].items():
                    setattr(block, field, value)
                restored_blocks[block.hash] = block
            dag.blocks = restored_blocks
            dag.tips = set(state["tips"])
            dag.selected_tip = state["selected_tip"]
            dag.virtual_selected_parent_chain = list(state["selected_chain"])
            dag.virtual_blue_order = list(state["virtual_blue_order"])
            dag.issued_supply_sats = int(state["issued_supply_sats"])
            dag.pool_balance_sats = int(state["pool_balance_sats"])
            dag.utxo_set.utxos = {
                (item["txid"], int(item["vout"])): UTXOEntry(**item["entry"])
                for item in state["utxos"]
            }
            dag.utxo_set._block_spent_undo = {
                item["block_hash"]: [
                    ((entry["txid"], int(entry["vout"])), UTXOEntry(**entry["entry"]))
                    for entry in item["entries"]
                ]
                for item in state["block_spent_undo"]
            }
            dag.utxo_set._block_created_undo = {
                item["block_hash"]: [(outpoint[0], int(outpoint[1])) for outpoint in item["outpoints"]]
                for item in state["block_created_undo"]
            }
            dag.utxo_set._block_fees = {key: int(value) for key, value in state["block_fees"].items()}
            dag.utxo_set._block_accepted_txids = {
                key: list(value) for key, value in state["block_accepted_txids"].items()
            }
            combo = ComboStateMachine()
            combo.epochs = {}
            for raw_epoch in state["combo"]["epochs"]:
                raw_epoch = dict(raw_epoch)
                raw_epoch["resolution"] = self._resolution_from_payload(raw_epoch.get("resolution"))
                epoch = ComboEpoch(**raw_epoch)
                combo.epochs[epoch.epoch_id] = epoch
            combo.active_epoch_id = int(state["combo"]["active_epoch_id"])
            combo.pending_draws = [int(value) for value in state["combo"]["pending_draws"]]
            combo.resolutions_by_beacon_height = {
                epoch.resolution.beacon_height: epoch.resolution
                for epoch in combo.epochs.values() if epoch.resolution is not None
            }
            dag.combo_machine = combo
            dag.genesis_hash = self.network.genesis.expected_hash
            dag._ancestor_cache.clear()
        except (KeyError, TypeError, ValueError) as exc:
            raise ChainStoreError("Consensus snapshot state is malformed") from exc

        if dag.genesis_hash not in dag.blocks or dag.selected_tip not in dag.blocks:
            raise ChainStoreError("Consensus snapshot is missing genesis or selected tip")
        if not dag.tips or not dag.tips.issubset(dag.blocks):
            raise ChainStoreError("Consensus snapshot tips are invalid")
        if dag.utxo_set.total_value_sats() != dag.issued_supply_sats:
            raise ChainStoreError("Consensus snapshot violates asset conservation")
        if dag.utxo_set.value_by_kind_sats("jackpot_pool") != dag.pool_balance_sats:
            raise ChainStoreError("Consensus snapshot jackpot balance mismatch")
        if dag.state_root() != expected_root:
            raise ChainStoreError("Consensus snapshot state root mismatch")
        return sequence

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
        snapshot_sequence = self._load_snapshot(dag)
        with self._connect() as connection:
            records = connection.execute(
                "SELECT sequence, payload_json, state_root FROM accepted_blocks "
                "WHERE sequence > ? ORDER BY sequence", (snapshot_sequence,)
            ).fetchall()
            total_records = int(connection.execute("SELECT COUNT(*) FROM accepted_blocks").fetchone()[0])
        # The state root commits the complete DAG/UTXO/accounting prefix, so
        # verifying the newest populated checkpoint also verifies every older
        # record in that prefix. Recomputing every historical checkpoint made
        # startup quadratic as the chain grew.
        latest_checkpoint = next(
            (i for i in range(len(records) - 1, -1, -1) if records[i][2]),
            None,
        )

        for index, (_, payload_json, expected_root) in enumerate(records):
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError as exc:
                raise ChainStoreError("Corrupt persisted block JSON") from exc
            # The newest checkpoint commits every canonical header field and
            # derived consensus value in its prefix. Re-hash only the short
            # journal tail not yet covered by that commitment.
            block = deserialize_block(
                payload,
                self.network,
                verify_hash=latest_checkpoint is None or index > latest_checkpoint,
            )
            if not dag.replay_block(block):
                raise ChainStoreError(f"Duplicate block during replay: {block.hash}")
            if index == latest_checkpoint and dag.state_root() != expected_root:
                raise ChainStoreError(
                    f"State root mismatch after replaying checkpoint block {block.hash}"
                )
        self.loaded_snapshot_sequence = snapshot_sequence
        self.tail_replayed_blocks = len(records)
        return total_records

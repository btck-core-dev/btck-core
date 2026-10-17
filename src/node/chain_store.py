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
SNAPSHOT_V2_FORMAT_VERSION = 2
SNAPSHOT_V2_FILENAME = "chain.snapshot.v2.sqlite3"


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
        # Full state-root construction and snapshot serialization are both
        # proportional to the lifetime chain. They must not run in the block
        # acceptance call stack on a production node. Operators can retain the
        # legacy behavior for small development networks, but MAINNET uses the
        # explicit offline checkpoint workflow in verify_chain_replay.py.
        self.inline_checkpoints_enabled = os.getenv(
            "BTCK_INLINE_STATE_CHECKPOINTS", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.snapshot_path = self.path.with_name(SNAPSHOT_FILENAME)
        self.snapshot_v2_path = self.path.with_name(SNAPSHOT_V2_FILENAME)
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
        return (
            self.inline_checkpoints_enabled
            and (self._record_count + 1) % self.checkpoint_interval == 0
        )

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

    def seal_latest_checkpoint(self, state_root: str) -> tuple[int, str]:
        """Bind an offline-replayed tip to a durable state root.

        This method is intentionally separate from append_block: computing the
        root is expensive and must happen on an isolated, immutable copy. The
        target row must be the journal tip and must not already contain a
        different root.
        """
        if len(state_root) != 64:
            raise ChainStoreError("Checkpoint state root must be 32-byte hex")
        try:
            bytes.fromhex(state_root)
        except ValueError as exc:
            raise ChainStoreError("Checkpoint state root must be hexadecimal") from exc
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sequence, block_hash, state_root FROM accepted_blocks "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ChainStoreError("Cannot checkpoint an empty journal")
            sequence, block_hash, existing_root = int(row[0]), str(row[1]), str(row[2])
            if existing_root and not hmac.compare_digest(existing_root, state_root):
                raise ChainStoreError("Journal tip already has a different state root")
            connection.execute(
                "UPDATE accepted_blocks SET state_root = ? WHERE sequence = ?",
                (state_root, sequence),
            )
        return sequence, block_hash

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

    def write_snapshot_v2(self, dag, sequence: int, checkpoint_block_hash: str, state_root: str) -> None:
        """Write a row-streamed SQLite recovery image with bounded memory."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT block_hash, state_root FROM accepted_blocks WHERE sequence = ?", (sequence,)
            ).fetchone()
        if row != (checkpoint_block_hash, state_root) or not state_root:
            raise ChainStoreError("Snapshot checkpoint does not match the durable journal")

        self.snapshot_v2_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.snapshot_v2_path.name}.", dir=self.snapshot_v2_path.parent
        )
        os.close(fd)
        try:
            with sqlite3.connect(temporary_name) as snapshot:
                snapshot.execute("PRAGMA journal_mode=DELETE")
                snapshot.execute("PRAGMA synchronous=FULL")
                snapshot.executescript(
                    """
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE blocks (block_hash TEXT PRIMARY KEY, block_json TEXT NOT NULL, consensus_json TEXT NOT NULL);
                    CREATE TABLE tips (block_hash TEXT PRIMARY KEY);
                    CREATE TABLE selected_chain (position INTEGER PRIMARY KEY, block_hash TEXT NOT NULL);
                    CREATE TABLE blue_order (position INTEGER PRIMARY KEY, block_hash TEXT NOT NULL);
                    CREATE TABLE utxos (txid TEXT NOT NULL, vout INTEGER NOT NULL, entry_json TEXT NOT NULL, PRIMARY KEY(txid, vout));
                    CREATE TABLE spent_undo (block_hash TEXT NOT NULL, position INTEGER NOT NULL, txid TEXT NOT NULL, vout INTEGER NOT NULL, entry_json TEXT NOT NULL, PRIMARY KEY(block_hash, position));
                    CREATE TABLE created_undo (block_hash TEXT NOT NULL, position INTEGER NOT NULL, txid TEXT NOT NULL, vout INTEGER NOT NULL, PRIMARY KEY(block_hash, position));
                    CREATE TABLE block_fees (block_hash TEXT PRIMARY KEY, fee_sats INTEGER NOT NULL);
                    CREATE TABLE accepted_txids (block_hash TEXT NOT NULL, position INTEGER NOT NULL, txid TEXT NOT NULL, PRIMARY KEY(block_hash, position));
                    CREATE TABLE combo_state (id INTEGER PRIMARY KEY CHECK(id = 1), payload_json TEXT NOT NULL);
                    """
                )
                identity = {
                    "format_version": str(SNAPSHOT_V2_FORMAT_VERSION),
                    "network": self.network.name,
                    "chain_id": self.network.chain_id,
                    "genesis_hash": self.network.genesis.expected_hash,
                    "consensus_fingerprint": self.network.consensus_fingerprint,
                    "sequence": str(sequence),
                    "checkpoint_block_hash": checkpoint_block_hash,
                    "state_root": state_root,
                    "selected_tip": dag.selected_tip,
                    "issued_supply_sats": str(dag.issued_supply_sats),
                    "pool_balance_sats": str(dag.pool_balance_sats),
                }
                snapshot.executemany("INSERT INTO meta(key,value) VALUES(?,?)", identity.items())
                consensus_fields = (
                    "blue_work", "selected_parent", "merge_set", "merge_set_blues", "merge_set_reds",
                    "blue_anticone_sizes", "blue_score", "is_combo", "minted_sats", "reward_sats",
                    "miner_base_sats", "pool_deposit_sats", "eco_deposit_sats", "combo_payout_sats",
                    "fees_sats",
                )
                snapshot.executemany(
                    "INSERT INTO blocks VALUES(?,?,?)",
                    (
                        (
                            block_hash,
                            json.dumps(serialize_block(block), sort_keys=True, separators=(",", ":")),
                            json.dumps(
                                {field: getattr(block, field) for field in consensus_fields},
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                        for block_hash, block in sorted(dag.blocks.items())
                    ),
                )
                snapshot.executemany("INSERT INTO tips VALUES(?)", ((value,) for value in sorted(dag.tips)))
                snapshot.executemany(
                    "INSERT INTO selected_chain VALUES(?,?)", enumerate(dag.virtual_selected_parent_chain)
                )
                snapshot.executemany("INSERT INTO blue_order VALUES(?,?)", enumerate(dag.virtual_blue_order))
                snapshot.executemany(
                    "INSERT INTO utxos VALUES(?,?,?)",
                    (
                        (txid, vout, json.dumps(self._entry_payload(entry), sort_keys=True, separators=(",", ":")))
                        for (txid, vout), entry in sorted(dag.utxo_set.utxos.items())
                    ),
                )
                snapshot.executemany(
                    "INSERT INTO spent_undo VALUES(?,?,?,?,?)",
                    (
                        (block_hash, position, outpoint[0], outpoint[1], json.dumps(self._entry_payload(entry), sort_keys=True, separators=(",", ":")))
                        for block_hash, entries in sorted(dag.utxo_set._block_spent_undo.items())
                        for position, (outpoint, entry) in enumerate(entries)
                    ),
                )
                snapshot.executemany(
                    "INSERT INTO created_undo VALUES(?,?,?,?)",
                    (
                        (block_hash, position, outpoint[0], outpoint[1])
                        for block_hash, entries in sorted(dag.utxo_set._block_created_undo.items())
                        for position, outpoint in enumerate(entries)
                    ),
                )
                snapshot.executemany(
                    "INSERT INTO block_fees VALUES(?,?)", sorted(dag.utxo_set._block_fees.items())
                )
                snapshot.executemany(
                    "INSERT INTO accepted_txids VALUES(?,?,?)",
                    (
                        (block_hash, position, txid)
                        for block_hash, txids in sorted(dag.utxo_set._block_accepted_txids.items())
                        for position, txid in enumerate(txids)
                    ),
                )
                combo_payload = {
                    "active_epoch_id": dag.combo_machine.active_epoch_id,
                    "pending_draws": dag.combo_machine.pending_draws,
                    "epochs": [asdict(epoch) for _, epoch in sorted(dag.combo_machine.epochs.items())],
                }
                snapshot.execute(
                    "INSERT INTO combo_state VALUES(1,?)",
                    (json.dumps(combo_payload, sort_keys=True, separators=(",", ":")),),
                )
                snapshot.commit()
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ChainStoreError("New V2 snapshot failed SQLite quick_check")
            with open(temporary_name, "rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.snapshot_v2_path)
            directory_fd = os.open(self.snapshot_v2_path.parent, os.O_RDONLY)
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

    def _load_snapshot_v2(self, dag) -> int:
        try:
            uri = f"file:{self.snapshot_v2_path}?mode=ro"
            with sqlite3.connect(uri, uri=True) as snapshot:
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ChainStoreError("V2 consensus snapshot failed SQLite quick_check")
                meta = dict(snapshot.execute("SELECT key,value FROM meta"))
                expected = {
                    "format_version": str(SNAPSHOT_V2_FORMAT_VERSION),
                    "network": self.network.name,
                    "chain_id": self.network.chain_id,
                    "genesis_hash": self.network.genesis.expected_hash,
                    "consensus_fingerprint": self.network.consensus_fingerprint,
                }
                for key, value in expected.items():
                    if meta.get(key) != value:
                        raise ChainStoreError(f"V2 snapshot identity mismatch for {key}")
                sequence = int(meta["sequence"])
                checkpoint_hash = meta["checkpoint_block_hash"]
                expected_root = meta["state_root"]
                with self._connect() as journal:
                    row = journal.execute(
                        "SELECT block_hash,state_root FROM accepted_blocks WHERE sequence=?",
                        (sequence,),
                    ).fetchone()
                if row != (checkpoint_hash, expected_root) or not expected_root:
                    raise ChainStoreError("V2 snapshot checkpoint does not match the durable journal")

                restored_blocks = {}
                for block_hash, block_json, consensus_json in snapshot.execute(
                    "SELECT block_hash,block_json,consensus_json FROM blocks ORDER BY block_hash"
                ):
                    block = deserialize_block(json.loads(block_json), self.network, verify_hash=False)
                    if block.hash != block_hash:
                        raise ChainStoreError("V2 snapshot block key mismatch")
                    for field, value in json.loads(consensus_json).items():
                        setattr(block, field, value)
                    restored_blocks[block_hash] = block
                dag.blocks = restored_blocks
                dag.tips = {row[0] for row in snapshot.execute("SELECT block_hash FROM tips")}
                dag.selected_tip = meta["selected_tip"]
                dag.virtual_selected_parent_chain = [
                    row[0] for row in snapshot.execute("SELECT block_hash FROM selected_chain ORDER BY position")
                ]
                dag.virtual_blue_order = [
                    row[0] for row in snapshot.execute("SELECT block_hash FROM blue_order ORDER BY position")
                ]
                dag.issued_supply_sats = int(meta["issued_supply_sats"])
                dag.pool_balance_sats = int(meta["pool_balance_sats"])
                dag.utxo_set.utxos = {}
                for txid, vout, entry_json in snapshot.execute(
                    "SELECT txid,vout,entry_json FROM utxos ORDER BY txid,vout"
                ):
                    dag.utxo_set.utxos[(txid, int(vout))] = UTXOEntry(**json.loads(entry_json))
                dag.utxo_set._block_spent_undo = {}
                for block_hash, _position, txid, vout, entry_json in snapshot.execute(
                    "SELECT block_hash,position,txid,vout,entry_json FROM spent_undo ORDER BY block_hash,position"
                ):
                    dag.utxo_set._block_spent_undo.setdefault(block_hash, []).append(
                        ((txid, int(vout)), UTXOEntry(**json.loads(entry_json)))
                    )
                dag.utxo_set._block_created_undo = {}
                for block_hash, _position, txid, vout in snapshot.execute(
                    "SELECT block_hash,position,txid,vout FROM created_undo ORDER BY block_hash,position"
                ):
                    dag.utxo_set._block_created_undo.setdefault(block_hash, []).append((txid, int(vout)))
                dag.utxo_set._block_fees = {
                    block_hash: int(value)
                    for block_hash, value in snapshot.execute("SELECT block_hash,fee_sats FROM block_fees")
                }
                dag.utxo_set._block_accepted_txids = {}
                for block_hash, _position, txid in snapshot.execute(
                    "SELECT block_hash,position,txid FROM accepted_txids ORDER BY block_hash,position"
                ):
                    dag.utxo_set._block_accepted_txids.setdefault(block_hash, []).append(txid)
                combo_raw = json.loads(snapshot.execute("SELECT payload_json FROM combo_state WHERE id=1").fetchone()[0])
                combo = ComboStateMachine()
                combo.epochs = {}
                for raw_epoch in combo_raw["epochs"]:
                    raw_epoch["resolution"] = self._resolution_from_payload(raw_epoch.get("resolution"))
                    epoch = ComboEpoch(**raw_epoch)
                    combo.epochs[epoch.epoch_id] = epoch
                combo.active_epoch_id = int(combo_raw["active_epoch_id"])
                combo.pending_draws = [int(value) for value in combo_raw["pending_draws"]]
                combo.resolutions_by_beacon_height = {
                    epoch.resolution.beacon_height: epoch.resolution
                    for epoch in combo.epochs.values() if epoch.resolution is not None
                }
                dag.combo_machine = combo
                dag.genesis_hash = self.network.genesis.expected_hash
                dag._ancestor_cache.clear()
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, ChainStoreError):
                raise
            raise ChainStoreError("V2 consensus snapshot is unreadable or malformed") from exc

        if dag.genesis_hash not in dag.blocks or dag.selected_tip not in dag.blocks:
            raise ChainStoreError("V2 snapshot is missing genesis or selected tip")
        if not dag.tips or not dag.tips.issubset(dag.blocks):
            raise ChainStoreError("V2 snapshot tips are invalid")
        if dag.utxo_set.total_value_sats() != dag.issued_supply_sats:
            raise ChainStoreError("V2 snapshot violates asset conservation")
        if dag.utxo_set.value_by_kind_sats("jackpot_pool") != dag.pool_balance_sats:
            raise ChainStoreError("V2 snapshot jackpot balance mismatch")
        if dag.state_root() != expected_root:
            raise ChainStoreError("V2 snapshot state root mismatch")
        return sequence

    def _load_snapshot(self, dag) -> int:
        if self.snapshot_v2_path.exists():
            return self._load_snapshot_v2(dag)
        return self._load_snapshot_v1(dag)

    def _load_snapshot_v1(self, dag) -> int:
        if not self.snapshot_path.exists():
            return 0
        try:
            # Stream decompression to a temporary file. The previous one-shot
            # path simultaneously retained the 200+ MB compressed image, its
            # much larger JSON expansion and the parsed object graph.
            with tempfile.TemporaryFile(mode="w+b") as expanded:
                decompressor = zlib.decompressobj()
                with self.snapshot_path.open("rb") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        expanded.write(decompressor.decompress(chunk))
                expanded.write(decompressor.flush())
                expanded.seek(0)
                envelope = json.load(expanded)
            payload = envelope["payload"]
        except (OSError, zlib.error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ChainStoreError("Consensus snapshot is unreadable or malformed") from exc
        hasher = hashlib.sha256()
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"))
        for chunk in encoder.iterencode(payload):
            hasher.update(chunk.encode("utf-8"))
        digest = hasher.hexdigest()
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
            # Drop envelope framing before constructing live consensus objects,
            # then release each large decoded list entry as it is restored.
            state = payload.pop("state")
            payload.clear()
            envelope.clear()
            restored_blocks = {}
            raw_blocks = state.pop("blocks")
            for index, item in enumerate(raw_blocks):
                block = deserialize_block(item["block"], self.network, verify_hash=False)
                for field, value in item["consensus"].items():
                    setattr(block, field, value)
                restored_blocks[block.hash] = block
                raw_blocks[index] = None
            raw_blocks.clear()
            dag.blocks = restored_blocks
            dag.tips = set(state.pop("tips"))
            dag.selected_tip = state.pop("selected_tip")
            dag.virtual_selected_parent_chain = state.pop("selected_chain")
            dag.virtual_blue_order = state.pop("virtual_blue_order")
            dag.issued_supply_sats = int(state.pop("issued_supply_sats"))
            dag.pool_balance_sats = int(state.pop("pool_balance_sats"))

            dag.utxo_set.utxos = {}
            raw_utxos = state.pop("utxos")
            for index, item in enumerate(raw_utxos):
                dag.utxo_set.utxos[(item["txid"], int(item["vout"]))] = UTXOEntry(
                    **item["entry"]
                )
                raw_utxos[index] = None
            raw_utxos.clear()

            raw_spent = state.pop("block_spent_undo")
            restored_spent = {}
            for index, item in enumerate(raw_spent):
                restored_spent[item["block_hash"]] = [
                    ((entry["txid"], int(entry["vout"])), UTXOEntry(**entry["entry"]))
                    for entry in item["entries"]
                ]
                raw_spent[index] = None
            raw_spent.clear()
            dag.utxo_set._block_spent_undo = restored_spent

            raw_created = state.pop("block_created_undo")
            restored_created = {}
            for index, item in enumerate(raw_created):
                restored_created[item["block_hash"]] = [
                    (outpoint[0], int(outpoint[1])) for outpoint in item["outpoints"]
                ]
                raw_created[index] = None
            raw_created.clear()
            dag.utxo_set._block_created_undo = restored_created
            dag.utxo_set._block_fees = {
                key: int(value) for key, value in state.pop("block_fees").items()
            }
            dag.utxo_set._block_accepted_txids = {
                key: list(value) for key, value in state["block_accepted_txids"].items()
            }
            state.pop("block_accepted_txids")
            combo = ComboStateMachine()
            combo.epochs = {}
            combo_state = state.pop("combo")
            for raw_epoch in combo_state["epochs"]:
                raw_epoch = dict(raw_epoch)
                raw_epoch["resolution"] = self._resolution_from_payload(raw_epoch.get("resolution"))
                epoch = ComboEpoch(**raw_epoch)
                combo.epochs[epoch.epoch_id] = epoch
            combo.active_epoch_id = int(combo_state["active_epoch_id"])
            combo.pending_draws = [int(value) for value in combo_state["pending_draws"]]
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

"""Read-only wallet synchronization index built from a verified V2 snapshot.

The index is deliberately separate from consensus storage.  It can be built on
an isolated host, copied atomically to the public edge, and queried without
walking the node's lifetime undo records on every wallet page request.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict


INDEX_FORMAT = "btck-wallet-sync-index-v1"


class WalletSyncIndexError(RuntimeError):
    pass


def _readonly_uri(path: Path, *, immutable: bool = False) -> str:
    suffix = "&immutable=1" if immutable else ""
    return f"file:{path.resolve()}?mode=ro{suffix}"


def _read_meta(connection: sqlite3.Connection, schema: str = "main") -> dict[str, str]:
    try:
        return {
            str(key): str(value)
            for key, value in connection.execute(f"SELECT key,value FROM {schema}.meta")
        }
    except sqlite3.Error as exc:
        raise WalletSyncIndexError(f"wallet index metadata is unreadable: {exc}") from exc


def build_wallet_sync_index(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    """Build an atomic, non-secret history index from an immutable V2 snapshot."""
    snapshot_path = Path(snapshot_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    if not snapshot_path.is_file():
        raise WalletSyncIndexError(f"V2 snapshot not found: {snapshot_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(str(temporary), uri=True) as destination:
            destination.execute("PRAGMA journal_mode=DELETE")
            destination.execute("PRAGMA synchronous=FULL")
            destination.execute("PRAGMA temp_store=FILE")
            destination.execute("ATTACH DATABASE ? AS source", (_readonly_uri(snapshot_path, immutable=True),))
            source_meta = _read_meta(destination, "source")
            if source_meta.get("format_version") != "2":
                raise WalletSyncIndexError("source is not a BTC-K V2 snapshot")
            quick_check = destination.execute("PRAGMA source.quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise WalletSyncIndexError("source V2 snapshot failed SQLite quick_check")

            required_identity = (
                "network", "chain_id", "genesis_hash", "consensus_fingerprint",
            )
            if any(not source_meta.get(key) for key in required_identity):
                raise WalletSyncIndexError("source V2 snapshot is missing network identity")

            destination.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE blocks (
                    height INTEGER PRIMARY KEY,
                    block_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE created (
                    height INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    txid TEXT NOT NULL,
                    vout INTEGER NOT NULL,
                    amount_satk INTEGER NOT NULL,
                    R_ephem_hex TEXT NOT NULL,
                    P_onetime_hex TEXT NOT NULL,
                    block_height INTEGER NOT NULL,
                    is_coinbase INTEGER NOT NULL,
                    timelock_until_height INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY(height, position)
                );
                CREATE TABLE spent (
                    height INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    txid TEXT NOT NULL,
                    vout INTEGER NOT NULL,
                    PRIMARY KEY(height, position)
                );
                CREATE TEMP TABLE all_entries (
                    txid TEXT NOT NULL,
                    vout INTEGER NOT NULL,
                    entry_json TEXT NOT NULL,
                    PRIMARY KEY(txid, vout)
                ) WITHOUT ROWID;
                """
            )
            destination.execute(
                "INSERT INTO all_entries SELECT txid,vout,entry_json FROM source.utxos"
            )
            destination.execute(
                "INSERT OR IGNORE INTO all_entries "
                "SELECT txid,vout,entry_json FROM source.spent_undo"
            )
            destination.execute(
                "INSERT INTO blocks SELECT position,block_hash FROM source.blue_order ORDER BY position"
            )
            destination.execute(
                """
                INSERT INTO created
                SELECT bo.position, cu.position, cu.txid, cu.vout,
                       CAST(json_extract(a.entry_json, '$.amount_satk') AS INTEGER),
                       json_extract(a.entry_json, '$.R_ephem_hex'),
                       json_extract(a.entry_json, '$.P_onetime_hex'),
                       CAST(json_extract(a.entry_json, '$.block_height') AS INTEGER),
                       CAST(json_extract(a.entry_json, '$.is_coinbase') AS INTEGER),
                       CAST(COALESCE(json_extract(a.entry_json, '$.timelock_until_height'), 0) AS INTEGER),
                       COALESCE(json_extract(a.entry_json, '$.kind'), 'standard')
                  FROM source.created_undo AS cu
                  JOIN source.blue_order AS bo ON bo.block_hash = cu.block_hash
                  JOIN all_entries AS a ON a.txid = cu.txid AND a.vout = cu.vout
                 WHERE COALESCE(json_extract(a.entry_json, '$.R_ephem_hex'), '') != ''
                   AND COALESCE(json_extract(a.entry_json, '$.P_onetime_hex'), '') != ''
                 ORDER BY bo.position, cu.position
                """
            )
            destination.execute(
                """
                INSERT INTO spent
                SELECT bo.position, su.position, su.txid, su.vout
                  FROM source.spent_undo AS su
                  JOIN source.blue_order AS bo ON bo.block_hash = su.block_hash
                 ORDER BY bo.position, su.position
                """
            )
            tip = destination.execute(
                "SELECT height,block_hash FROM blocks ORDER BY height DESC LIMIT 1"
            ).fetchone()
            tip_height, tip_hash = (-1, "") if tip is None else (int(tip[0]), str(tip[1]))
            metadata = {
                "format": INDEX_FORMAT,
                "network": source_meta["network"],
                "chain_id": source_meta["chain_id"],
                "genesis_hash": source_meta["genesis_hash"],
                "consensus_fingerprint": source_meta["consensus_fingerprint"],
                "tip_height": str(tip_height),
                "tip_hash": tip_hash,
            }
            destination.executemany(
                "INSERT INTO meta(key,value) VALUES(?,?)", metadata.items()
            )
            destination.execute("DROP TABLE all_entries")
            destination.commit()
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise WalletSyncIndexError("new wallet synchronization index failed quick_check")
            destination.execute("DETACH DATABASE source")
        os.chmod(temporary, 0o644)
        os.replace(temporary, output_path)
        directory_fd = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {**metadata, "path": str(output_path)}
    except (OSError, sqlite3.Error, KeyError) as exc:
        if isinstance(exc, WalletSyncIndexError):
            raise
        raise WalletSyncIndexError(f"wallet synchronization index build failed: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


class WalletSyncIndex:
    def __init__(self, path: Path, expected_network: dict[str, str] | None = None):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise WalletSyncIndexError(f"wallet synchronization index not found: {self.path}")
        with self._connect() as connection:
            self.meta = _read_meta(connection)
            if self.meta.get("format") != INDEX_FORMAT:
                raise WalletSyncIndexError("unsupported wallet synchronization index format")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise WalletSyncIndexError("wallet synchronization index failed quick_check")
        self.tip_height = int(self.meta["tip_height"])
        if expected_network:
            for key in ("network", "chain_id", "genesis_hash", "consensus_fingerprint"):
                if self.meta.get(key) != str(expected_network.get(key, "")):
                    raise WalletSyncIndexError(f"wallet index {key} does not match this network")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(_readonly_uri(self.path, immutable=True), uri=True, timeout=5.0)

    @property
    def network_identity(self) -> dict[str, str]:
        return {
            "name": self.meta["network"],
            "chain_id": self.meta["chain_id"],
            "genesis_hash": self.meta["genesis_hash"],
            "consensus_fingerprint": self.meta["consensus_fingerprint"],
        }

    def get_page(self, from_height: int, limit: int, anchor_hash: str) -> Dict[str, Any] | None:
        """Return an indexed historical page, or None when the live tail owns it."""
        from_height = int(from_height)
        limit = max(1, min(int(limit), 1000))
        if from_height < 0:
            raise ValueError("from_height is outside the indexed ledger")
        if from_height > self.tip_height:
            return None
        with self._connect() as connection:
            if from_height > 0:
                row = connection.execute(
                    "SELECT block_hash FROM blocks WHERE height = ?", (from_height - 1,),
                ).fetchone()
                if row is None or str(row[0]) != str(anchor_hash):
                    raise ValueError("wallet sync cursor is stale after a canonical reorganization")
            end_height = min(self.tip_height + 1, from_height + limit)
            rows = connection.execute(
                "SELECT height,block_hash FROM blocks WHERE height >= ? AND height < ? ORDER BY height",
                (from_height, end_height),
            ).fetchall()
            created: dict[int, list[dict[str, Any]]] = {}
            for row in connection.execute(
                """SELECT height,txid,vout,amount_satk,R_ephem_hex,P_onetime_hex,
                          block_height,is_coinbase,timelock_until_height,kind
                     FROM created WHERE height >= ? AND height < ? ORDER BY height,position""",
                (from_height, end_height),
            ):
                created.setdefault(int(row[0]), []).append({
                    "txid": str(row[1]), "vout": int(row[2]), "amount_satk": int(row[3]),
                    "R_ephem_hex": str(row[4]), "P_onetime_hex": str(row[5]),
                    "height": int(row[6]), "is_coinbase": bool(row[7]),
                    "timelock_until_height": int(row[8]), "kind": str(row[9]),
                })
            spent: dict[int, list[dict[str, Any]]] = {}
            for height, txid, vout in connection.execute(
                "SELECT height,txid,vout FROM spent WHERE height >= ? AND height < ? ORDER BY height,position",
                (from_height, end_height),
            ):
                spent.setdefault(int(height), []).append({"txid": str(txid), "vout": int(vout)})
        blocks = [
            {
                "height": int(height), "hash": str(block_hash),
                "created": created.get(int(height), []),
                "spent": spent.get(int(height), []),
            }
            for height, block_hash in rows
        ]
        if len(blocks) != end_height - from_height:
            raise WalletSyncIndexError("wallet index contains a discontinuous block range")
        next_height = end_height
        return {
            "format": "btck-wallet-sync-v1",
            "network": self.network_identity,
            "from_height": from_height,
            "next_height": next_height,
            "anchor_hash": blocks[-1]["hash"] if blocks else str(anchor_hash),
            # The static prefix is never terminal: the next request verifies
            # and consumes the small live tail from btckd.
            "tip_height": self.tip_height + 1,
            "tip_hash": self.meta["tip_hash"],
            "done": False,
            "unspent_only": False,
            "source": "static-index",
            "blocks": blocks,
        }

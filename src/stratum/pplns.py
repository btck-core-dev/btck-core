#!/usr/bin/env python3
"""BTC-K Stratum Pool PPLNS Accounting and Settlement Engine.

PPLNS (Pay Per Last N Shares):
1. In PPLNS mode, all block rewards are routed to the central Pool Treasury address.
2. The mining pool records all valid miner shares (weighted by difficulty) into an
   acid-compliant SQLite database with WAL mode.
3. When a block is successfully solved and accepted on-chain:
   - Pool fee (e.g. 1% - 5%) is deducted.
   - Finder bonus (e.g. 0.5%) is optionally credited to the winning worker.
   - The remaining net reward is proportionally split across all miners who contributed
     shares in the Last N Shares sliding window (W_miner / W_total).
   - Miners' unpaid balances are credited atomically.
4. AutoPayoutWorker periodically scans on-chain UTXOs and automatically generates,
   signs, and broadcasts confidential stealth transactions to miners whose unpaid
   balances reach the configured payout threshold (e.g., 5 BTC-K).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from contextlib import closing
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tokenomics.emission_schedule import SATK_PER_COIN, compute_beacon_xor_winner
from tokenomics.anti_hopping import AntiHoppingGate
from privacy.wallet_engine import StealthWallet, transaction_to_dict, transaction_from_dict
from node.rpc_client import RPCError

from stratum.pplns_rewards import allocate_reward

logger = logging.getLogger("btck-pplns")


@dataclass
class MinerBalance:
    wallet_address: str
    unpaid_satk: int
    paid_satk: int
    total_shares_weight: float
    total_shares_count: int
    last_share_timestamp: float
    payout_threshold_satk: int

    @property
    def unpaid_btck(self) -> float:
        return self.unpaid_satk / SATK_PER_COIN

    @property
    def paid_btck(self) -> float:
        return self.paid_satk / SATK_PER_COIN


class PPLNSEngine:
    """Core PPLNS sliding-window accounting ledger."""

    def __init__(
        self,
        db_path: str | Path = "data/pool_accounting.db",
        window_size: int = 10000,
        finder_bonus_rate: float = 0.005,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.window_size = max(100, window_size)
        self.finder_bonus_rate = finder_bonus_rate
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL,
                    worker_name TEXT NOT NULL,
                    difficulty REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    block_hash_settled TEXT DEFAULT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_shares_timestamp ON shares(timestamp);
                CREATE INDEX IF NOT EXISTS idx_shares_wallet ON shares(wallet_address);
                CREATE INDEX IF NOT EXISTS idx_shares_worker
                    ON shares(wallet_address, worker_name);
                CREATE INDEX IF NOT EXISTS idx_shares_settled ON shares(block_hash_settled);

                CREATE TABLE IF NOT EXISTS miners (
                    wallet_address TEXT PRIMARY KEY,
                    unpaid_satk INTEGER DEFAULT 0,
                    paid_satk INTEGER DEFAULT 0,
                    total_shares_weight REAL DEFAULT 0.0,
                    total_shares_count INTEGER DEFAULT 0,
                    last_share_timestamp REAL DEFAULT 0.0,
                    payout_threshold_satk INTEGER DEFAULT 500000000
                );

                CREATE TABLE IF NOT EXISTS blocks (
                    block_hash TEXT PRIMARY KEY,
                    height INTEGER NOT NULL,
                    reward_satk INTEGER NOT NULL,
                    pool_fee_satk INTEGER NOT NULL,
                    finder_bonus_satk INTEGER NOT NULL,
                    distributed_satk INTEGER NOT NULL,
                    finder_wallet TEXT NOT NULL,
                    finder_worker TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    shares_in_window INTEGER NOT NULL,
                    window_weight REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pplns_block_allocations (
                    block_hash TEXT PRIMARY KEY,
                    weights_json TEXT NOT NULL,
                    fee_rate TEXT NOT NULL,
                    bonus_rate TEXT NOT NULL,
                    credits_json TEXT NOT NULL,
                    first_share_id INTEGER NOT NULL,
                    last_share_id INTEGER NOT NULL,
                    canonical_height INTEGER,
                    updated_at REAL NOT NULL,
                    allocation_kind TEXT NOT NULL DEFAULT 'window'
                );
                CREATE TABLE IF NOT EXISTS pplns_reward_holds (
                    block_hash TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    observed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS payouts (
                    txid TEXT PRIMARY KEY,
                    wallet_address TEXT NOT NULL,
                    amount_satk INTEGER NOT NULL,
                    fee_satk INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_payouts_wallet ON payouts(wallet_address);

                CREATE TABLE IF NOT EXISTS payout_intents (
                    txid TEXT PRIMARY KEY,
                    wallet_address TEXT NOT NULL,
                    amount_satk INTEGER NOT NULL,
                    fee_satk INTEGER NOT NULL,
                    transaction_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'prepared',
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_payout_per_wallet
                    ON payout_intents(wallet_address) WHERE status != 'confirmed';

                CREATE TABLE IF NOT EXISTS pool_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS combo_qualification_audits (
                    epoch_id INTEGER PRIMARY KEY,
                    stage_id INTEGER NOT NULL,
                    winner_block_hash TEXT NOT NULL,
                    finder_wallet TEXT NOT NULL,
                    finder_worker TEXT NOT NULL,
                    qualified INTEGER NOT NULL,
                    duty_cycle REAL NOT NULL,
                    reason TEXT NOT NULL,
                    epoch_start_time REAL NOT NULL,
                    epoch_end_time REAL NOT NULL,
                    audited_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS combo_entitlements (
                    epoch_id INTEGER NOT NULL,
                    tranche_index INTEGER NOT NULL,
                    attributed_block_hash TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    worker_name TEXT NOT NULL,
                    amount_satk INTEGER NOT NULL,
                    unlock_height INTEGER NOT NULL,
                    credited INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (epoch_id, tranche_index)
                );

                CREATE TABLE IF NOT EXISTS combo_public_winners (
                    winner_block_hash TEXT PRIMARY KEY,
                    epoch_id INTEGER NOT NULL,
                    draw_height INTEGER NOT NULL,
                    worker_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    hashrate_hps REAL NOT NULL,
                    hashrate_window_seconds REAL NOT NULL,
                    block_timestamp REAL NOT NULL,
                    captured_at REAL NOT NULL
                );
                """
            )
            block_columns = {row[1] for row in conn.execute("PRAGMA table_info(blocks)")}
            for column, declaration in (
                ("mode", "TEXT NOT NULL DEFAULT 'PPLNS'"),
                ("is_pool_fee", "INTEGER NOT NULL DEFAULT 0"),
                ("payout_address", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in block_columns:
                    conn.execute(f"ALTER TABLE blocks ADD COLUMN {column} {declaration}")
            combo_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(combo_qualification_audits)")
            }
            for column, declaration in (
                ("attributed_block_hash", "TEXT NOT NULL DEFAULT ''"),
                ("attributed_wallet", "TEXT NOT NULL DEFAULT ''"),
                ("attributed_worker", "TEXT NOT NULL DEFAULT ''"),
                ("jackpot_amount_satk", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in combo_columns:
                    conn.execute(
                        f"ALTER TABLE combo_qualification_audits ADD COLUMN {column} {declaration}"
                    )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_blocks_finder
                    ON blocks(finder_wallet, finder_worker);
                CREATE INDEX IF NOT EXISTS idx_blocks_timestamp
                    ON blocks(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_blocks_fee_recipient
                    ON blocks(is_pool_fee, payout_address);
                """
            )

    def record_combo_public_winners(
        self,
        resolutions: List[dict],
        hashrate_window_seconds: float = 600.0,
    ) -> List[dict]:
        """Persist wallet-free winner attribution for the public Combo history.

        Hashrate is the accepted-share average for the exact wallet/worker pair
        during the window immediately preceding its winning block. Only the
        worker label, mining mode and aggregate rate are copied to the public
        table; payout addresses and connection metadata remain private.
        """
        window = max(60.0, float(hashrate_window_seconds))
        recorded: List[dict] = []
        with self._get_connection() as conn:
            known = {
                str(row["winner_block_hash"])
                for row in conn.execute(
                    "SELECT winner_block_hash FROM combo_public_winners"
                )
            }
            for resolution in resolutions:
                winner_hash = str(resolution.get("winner_block_hash", ""))
                if len(winner_hash) != 64 or winner_hash in known:
                    continue
                block = conn.execute(
                    """
                    SELECT finder_wallet, finder_worker, timestamp, mode
                    FROM blocks WHERE block_hash = ?
                    """,
                    (winner_hash,),
                ).fetchone()
                if not block:
                    continue
                block_timestamp = float(block["timestamp"] or 0.0)
                share_totals = conn.execute(
                    """
                    SELECT COALESCE(SUM(difficulty), 0.0) AS difficulty_total
                    FROM shares
                    WHERE wallet_address = ? AND worker_name = ?
                      AND timestamp > ? AND timestamp <= ?
                    """,
                    (
                        str(block["finder_wallet"]),
                        str(block["finder_worker"]),
                        block_timestamp - window,
                        block_timestamp,
                    ),
                ).fetchone()
                hashrate_hps = (
                    float(share_totals["difficulty_total"] or 0.0)
                    * (2**32)
                    / window
                )
                item = {
                    "winner_block_hash": winner_hash,
                    "epoch_id": int(resolution.get("epoch_id", 0) or 0),
                    "draw_height": int(resolution.get("draw_height", 0) or 0),
                    "worker_name": str(block["finder_worker"] or "unattributed"),
                    "mode": str(block["mode"] or "UNKNOWN").upper(),
                    "hashrate_hps": hashrate_hps,
                    "hashrate_window_seconds": window,
                    "block_timestamp": block_timestamp,
                    "captured_at": time.time(),
                }
                conn.execute(
                    """
                    INSERT INTO combo_public_winners (
                        winner_block_hash, epoch_id, draw_height, worker_name,
                        mode, hashrate_hps, hashrate_window_seconds,
                        block_timestamp, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(item.values()),
                )
                known.add(winner_hash)
                recorded.append(item)
        return recorded

    def get_or_create_launch_time(self, requested: float = 0.0) -> float:
        """Return a restart-stable pool launch timestamp."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM pool_meta WHERE key = 'launch_time'").fetchone()
            if requested > 0.0:
                launch_time = float(requested)
                conn.execute(
                    "INSERT OR REPLACE INTO pool_meta(key, value) VALUES('launch_time', ?)",
                    (repr(launch_time),),
                )
                return launch_time
            if row:
                return float(row["value"])
            launch_time = time.time()
            conn.execute(
                "INSERT INTO pool_meta(key, value) VALUES('launch_time', ?)",
                (repr(launch_time),),
            )
            return launch_time

    def record_share(
        self,
        wallet_address: str,
        worker_name: str,
        difficulty: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record an accepted valid share."""
        ts = time.time() if timestamp is None else timestamp
        numeric_difficulty = float(difficulty)
        if not wallet_address or not worker_name:
            raise ValueError("Share wallet and worker must be non-empty")
        if not math.isfinite(numeric_difficulty) or numeric_difficulty <= 0:
            raise ValueError("Share difficulty must be a finite positive number")
        if not math.isfinite(float(ts)):
            raise ValueError("Share timestamp must be finite")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO shares (wallet_address, worker_name, difficulty, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (wallet_address, worker_name, numeric_difficulty, ts),
            )
            conn.execute(
                """
                INSERT INTO miners (wallet_address, total_shares_weight, total_shares_count, last_share_timestamp)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    total_shares_weight = total_shares_weight + excluded.total_shares_weight,
                    total_shares_count = total_shares_count + 1,
                    last_share_timestamp = excluded.last_share_timestamp
                """,
                (wallet_address, numeric_difficulty, ts),
            )

    @staticmethod
    def _worker_id(wallet_address: str, worker_name: str) -> str:
        return f"{wallet_address}\x1f{worker_name}"

    def evaluate_combo_anti_hopping(
        self,
        *,
        epoch_id: int,
        stage_id: int,
        epoch_start_time: float,
        epoch_end_time: float,
    ) -> dict:
        """Evaluate the official pool's durable Share ledger for one Combo epoch.

        This is deliberately a pool payout gate, not node consensus input.
        Private Stratum shares are not globally observable; feeding them into
        BlockDAG consensus would make nodes choose different winners. L1 keeps
        using public kHeavyHash blocks, while a pool treasury only attributes a
        won Combo to workers that passed this reproducible >=95% duty-cycle gate.
        """
        gate = AntiHoppingGate.create_for_stage(stage_id, int(epoch_id), min_duty_cycle=0.95)
        start = float(epoch_start_time)
        end = float(epoch_end_time)
        if end <= start:
            raise ValueError("Combo epoch end time must be after start time")

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT wallet_address, worker_name, timestamp
                FROM shares
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp, id
                """,
                (start, end),
            ).fetchall()

        identities: Dict[str, Tuple[str, str]] = {}
        for row in rows:
            wallet = str(row["wallet_address"])
            worker = str(row["worker_name"])
            worker_id = self._worker_id(wallet, worker)
            identities[worker_id] = (wallet, worker)
            slot = int((float(row["timestamp"]) - start) // gate.slot_duration_seconds)
            slot = max(0, min(gate.total_slots - 1, slot))
            gate.record_asic_share(worker_id, slot, payout_address=wallet)

        workers = []
        for worker_id, (wallet, worker) in sorted(identities.items()):
            qualified, duty_cycle, reason = gate.evaluate_worker_qualification(
                worker_id, gate.total_slots - 1
            )
            workers.append({
                "wallet_address": wallet,
                "worker_name": worker,
                "qualified": qualified,
                "duty_cycle": duty_cycle,
                "reason": reason,
            })
        return {
            "epoch_id": int(epoch_id),
            "stage_id": int(stage_id),
            "total_slots": gate.total_slots,
            "workers": workers,
        }

    def reconcile_combo_qualifications(
        self, resolutions: List[dict], current_height: int = 0
    ) -> List[dict]:
        """Attribute pool-owned Combo wins and create height-locked entitlements.

        The L1 winner decides whether this pool treasury received the jackpot.
        Inside that pool, the same future beacon is re-applied only to blocks
        found by >=95%-qualified workers. This prevents a late rental worker
        from receiving the pool's Combo without putting private Share data into
        global consensus.
        """
        audited = []
        with self._get_connection() as conn:
            known = {
                int(row["epoch_id"])
                for row in conn.execute("SELECT epoch_id FROM combo_qualification_audits")
            }

        for resolution in resolutions:
            epoch_id = int(resolution.get("epoch_id", 0))
            if epoch_id <= 0 or epoch_id in known:
                continue
            winner_hash = str(resolution.get("winner_block_hash", ""))
            with self._get_connection() as conn:
                winner = conn.execute(
                    "SELECT finder_wallet, finder_worker FROM blocks WHERE block_hash = ?",
                    (winner_hash,),
                ).fetchone()
            # A different pool or solo miner won this L1 epoch; this pool has
            # no payout to attribute and therefore creates no local audit row.
            if not winner:
                continue

            result = self.evaluate_combo_anti_hopping(
                epoch_id=epoch_id,
                stage_id=int(resolution.get("stage_id", 1)),
                epoch_start_time=float(resolution["epoch_start_timestamp"]),
                epoch_end_time=float(resolution["draw_timestamp"]),
            )
            finder_wallet = str(winner["finder_wallet"])
            finder_worker = str(winner["finder_worker"])
            qualified_workers = {
                self._worker_id(item["wallet_address"], item["worker_name"]): item
                for item in result["workers"]
                if item["qualified"]
            }
            with self._get_connection() as conn:
                pool_blocks = conn.execute(
                    """
                    SELECT block_hash, finder_wallet, finder_worker
                    FROM blocks
                    WHERE timestamp >= ? AND timestamp <= ? AND is_pool_fee = 0
                    ORDER BY height, block_hash
                    """,
                    (
                        float(resolution["epoch_start_timestamp"]),
                        float(resolution["draw_timestamp"]),
                    ),
                ).fetchall()
            eligible_blocks = [
                row for row in pool_blocks
                if self._worker_id(str(row["finder_wallet"]), str(row["finder_worker"]))
                in qualified_workers
                and len(str(row["block_hash"])) == 64
            ]
            attributed = None
            if eligible_blocks:
                winner_bytes, _ = compute_beacon_xor_winner(
                    [bytes.fromhex(str(row["block_hash"])) for row in eligible_blocks],
                    bytes.fromhex(str(resolution["beacon_hash"])),
                    epoch_id,
                )
                attributed = next(
                    row for row in eligible_blocks
                    if str(row["block_hash"]) == winner_bytes.hex()
                )

            original = next(
                (
                    item for item in result["workers"]
                    if item["wallet_address"] == finder_wallet
                    and item["worker_name"] == finder_worker
                ),
                None,
            )
            decision = {
                "qualified": attributed is not None,
                "duty_cycle": float(original["duty_cycle"]) if original else 0.0,
                "reason": (
                    "Qualified pool candidate selected by canonical future beacon"
                    if attributed is not None
                    else "No >=95% qualified pool candidate; Combo remains undistributed"
                ),
            }
            attributed_hash = str(attributed["block_hash"]) if attributed else ""
            attributed_wallet = str(attributed["finder_wallet"]) if attributed else ""
            attributed_worker = str(attributed["finder_worker"]) if attributed else ""
            jackpot_amount = int(resolution.get("jackpot_amount_satk", 0))
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO combo_qualification_audits (
                        epoch_id, stage_id, winner_block_hash, finder_wallet,
                        finder_worker, qualified, duty_cycle, reason,
                        epoch_start_time, epoch_end_time, audited_at,
                        attributed_block_hash, attributed_wallet,
                        attributed_worker, jackpot_amount_satk
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        epoch_id,
                        int(resolution.get("stage_id", 1)),
                        winner_hash,
                        finder_wallet,
                        finder_worker,
                        1 if decision["qualified"] else 0,
                        float(decision["duty_cycle"]),
                        str(decision["reason"]),
                        float(resolution["epoch_start_timestamp"]),
                        float(resolution["draw_timestamp"]),
                        time.time(),
                        attributed_hash,
                        attributed_wallet,
                        attributed_worker,
                        jackpot_amount,
                    ),
                )
                if attributed is not None:
                    for tranche in resolution.get("tranches", []):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO combo_entitlements (
                                epoch_id, tranche_index, attributed_block_hash,
                                wallet_address, worker_name, amount_satk,
                                unlock_height, credited
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                epoch_id,
                                int(tranche["tranche_index"]),
                                attributed_hash,
                                attributed_wallet,
                                attributed_worker,
                                int(tranche["amount_satk"]),
                                int(tranche["unlock_block_height"]),
                            ),
                        )
            record = {
                "epoch_id": epoch_id,
                "winner_block_hash": winner_hash,
                "finder_wallet": finder_wallet,
                "finder_worker": finder_worker,
                "attributed_block_hash": attributed_hash,
                "attributed_wallet": attributed_wallet,
                "attributed_worker": attributed_worker,
                **decision,
            }
            audited.append(record)
            known.add(epoch_id)
        if current_height > 0:
            self.release_mature_combo_entitlements(current_height)
        return audited

    def release_mature_combo_entitlements(self, current_height: int) -> List[dict]:
        """Atomically credit only Combo tranches whose consensus height unlocked."""
        released = []
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM combo_entitlements
                WHERE credited = 0 AND unlock_height <= ?
                ORDER BY unlock_height, epoch_id, tranche_index
                """,
                (int(current_height),),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO miners (wallet_address, unpaid_satk)
                    VALUES (?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        unpaid_satk = unpaid_satk + excluded.unpaid_satk
                    """,
                    (row["wallet_address"], int(row["amount_satk"])),
                )
                conn.execute(
                    """
                    UPDATE combo_entitlements SET credited = 1
                    WHERE epoch_id = ? AND tranche_index = ? AND credited = 0
                    """,
                    (int(row["epoch_id"]), int(row["tranche_index"])),
                )
                released.append({
                    "epoch_id": int(row["epoch_id"]),
                    "tranche_index": int(row["tranche_index"]),
                    "wallet_address": str(row["wallet_address"]),
                    "amount_satk": int(row["amount_satk"]),
                    "unlock_height": int(row["unlock_height"]),
                })
        return released

    def process_block(
        self,
        block_hash: str,
        height: int,
        reward_satk: int,
        pool_fee_rate: float,
        finder_wallet: str = "",
        finder_worker: str = "",
        window_size: Optional[int] = None,
        block_timestamp: Optional[float] = None,
    ) -> dict:
        """Settle a found block via PPLNS.

        Divides net block reward proportionally across the last N shares.
        """
        N = window_size or self.window_size
        fee_rate = Decimal(str(pool_fee_rate))
        if not fee_rate.is_finite():
            raise ValueError("Pool fee rate must be finite")
        fee_rate = max(Decimal("0"), min(Decimal("1"), fee_rate))
        with closing(self._get_connection()) as conn, conn:
            # Freeze the share window and its credits in the same transaction.
            conn.execute('BEGIN IMMEDIATE')
            # Check if block already processed
            existing = conn.execute("SELECT block_hash FROM blocks WHERE block_hash = ?", (block_hash,)).fetchone()
            if existing:
                logger.warning("Block %s already settled in PPLNS database", block_hash)
                return {"error": "already_settled", "block_hash": block_hash}

            # Fetch the Last N shares up to now
            cur = conn.execute(
                """
                SELECT id, wallet_address, difficulty FROM shares
                ORDER BY id DESC
                LIMIT ?
                """,
                (N,),
            )
            rows = cur.fetchall()

            total_window_weight_decimal = sum(
                (Decimal(str(r["difficulty"])) for r in rows), Decimal("0")
            )
            total_window_weight = float(total_window_weight_decimal)
            miner_weights: Dict[str, Decimal] = {}
            for r in rows:
                w = r["wallet_address"]
                miner_weights[w] = miner_weights.get(w, Decimal("0")) + Decimal(str(r["difficulty"]))

            pool_fee_satk, finder_bonus_satk, miner_credits = allocate_reward(
                reward_satk, miner_weights, finder_wallet, fee_rate, self.finder_bonus_rate)
            total_distributed = sum(miner_credits.values())
            for wallet, credit in miner_credits.items():
                conn.execute(
                    "INSERT INTO miners(wallet_address,unpaid_satk) VALUES(?,?) "
                    "ON CONFLICT(wallet_address) DO UPDATE SET unpaid_satk=unpaid_satk+excluded.unpaid_satk",
                    (wallet, credit))

            # Record block settlement
            conn.execute(
                """
                INSERT INTO blocks (
                    block_hash, height, reward_satk, pool_fee_satk, finder_bonus_satk,
                    distributed_satk, finder_wallet, finder_worker, timestamp,
                    shares_in_window, window_weight
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_hash,
                    height,
                    reward_satk,
                    pool_fee_satk,
                    finder_bonus_satk,
                    total_distributed,
                    finder_wallet,
                    finder_worker,
                    time.time() if block_timestamp is None else float(block_timestamp),
                    len(rows),
                    total_window_weight,
                ),
            )

            conn.execute(
                "INSERT INTO pplns_block_allocations VALUES(?,?,?,?,?,?,?,NULL,?,'window')",
                (block_hash, json.dumps({w:str(v) for w,v in miner_weights.items()},sort_keys=True),
                 str(fee_rate), str(self.finder_bonus_rate), json.dumps(miner_credits,sort_keys=True),
                 int(rows[-1]['id']) if rows else 0, int(rows[0]['id']) if rows else 0, time.time()))

            # Mark window shares with block hash
            if rows:
                min_id = rows[-1]["id"]
                max_id = rows[0]["id"]
                conn.execute(
                    """
                    UPDATE shares SET block_hash_settled = ?
                    WHERE id BETWEEN ? AND ? AND block_hash_settled IS NULL
                    """,
                    (block_hash, min_id, max_id),
                )

        summary = {
            "block_hash": block_hash,
            "height": height,
            "reward_satk": reward_satk,
            "reward_btck": reward_satk / SATK_PER_COIN,
            "pool_fee_satk": pool_fee_satk,
            "finder_bonus_satk": finder_bonus_satk,
            "distributed_satk": total_distributed,
            "shares_in_window": len(rows),
            "window_weight": total_window_weight,
            "miners_paid_count": len(miner_credits),
            "miner_credits": {w: c / SATK_PER_COIN for w, c in miner_credits.items()},
        }
        logger.info(
            "💰 PPLNS Settle Block #%d (%s): Reward %.4f BTC-K | PoolFee: %.4f | Distributed: %.4f across %d miners",
            height,
            block_hash[:16],
            reward_satk / SATK_PER_COIN,
            pool_fee_satk / SATK_PER_COIN,
            total_distributed / SATK_PER_COIN,
            len(miner_credits),
        )
        return summary

    def has_reward_reconciliation_hold(self, connection=None) -> bool:
        if connection is None:
            with closing(self._get_connection()) as conn, conn:
                return self.has_reward_reconciliation_hold(conn)
        # A legacy ledger must not be silently reinterpreted using today's
        # share window. Operators must import independently verified proofs.
        missing = connection.execute(
            "SELECT 1 FROM blocks b LEFT JOIN pplns_block_allocations a "
            "ON a.block_hash=b.block_hash WHERE b.mode='PPLNS' AND a.block_hash IS NULL LIMIT 1").fetchone()
        return bool(missing or connection.execute('SELECT 1 FROM pplns_reward_holds LIMIT 1').fetchone())

    def reconcile_pplns_block_rewards(self, canonical_blocks: List[dict]) -> int:
        """Adjust credits using the ORIGINAL window, never current shares.

        Unknown legacy allocations and debits conflicting with a payment are
        held for explicit reconciliation. Existing paid balances are untouched.
        """
        updated = 0
        with closing(self._get_connection()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            for block in canonical_blocks:
                block_hash = str(block.get('hash', ''))
                row = conn.execute(
                    "SELECT b.*,a.weights_json,a.fee_rate,a.bonus_rate,a.credits_json,a.allocation_kind,a.canonical_height AS reward_verified_height "
                    "FROM blocks b LEFT JOIN pplns_block_allocations a ON a.block_hash=b.block_hash "
                    "WHERE b.block_hash=? AND b.mode='PPLNS'", (block_hash,)).fetchone()
                if row is None:
                    continue
                if row['weights_json'] is None:
                    conn.execute('INSERT OR REPLACE INTO pplns_reward_holds VALUES(?,?,?)',
                                 (block_hash,'legacy share allocation needs verified reconstruction',time.time()))
                    continue
                if any(type(block.get(key)) is not int or block[key] < 0
                       for key in ('miner_base_sats', 'fees_sats', 'height')):
                    raise ValueError('Canonical reward fields must be nonnegative integers')
                amount = block['miner_base_sats'] + block['fees_sats']
                held = conn.execute('SELECT 1 FROM pplns_reward_holds WHERE block_hash=?',(block_hash,)).fetchone()
                if amount == row['reward_satk'] and block['height'] == row['reward_verified_height'] and not held:
                    continue
                if row['allocation_kind'] == 'legacy_fixed':
                    # An explicitly approved conservative legacy allocation is
                    # fixed to its proven canonical amount, never guessed from
                    # an ambiguous historical window or recalculated later.
                    if amount != row['reward_satk']:
                        conn.execute('INSERT OR REPLACE INTO pplns_reward_holds VALUES(?,?,?)',
                                     (block_hash,'approved legacy reward changed; new proof required',time.time()))
                    else:
                        conn.execute('UPDATE pplns_block_allocations SET canonical_height=?,updated_at=? WHERE block_hash=?',
                                     (int(block.get('height',row['height'])),time.time(),block_hash))
                        conn.execute('DELETE FROM pplns_reward_holds WHERE block_hash=?',(block_hash,))
                    continue
                fee, bonus, credits = allocate_reward(amount,json.loads(row['weights_json']),
                    row['finder_wallet'],row['fee_rate'],row['bonus_rate'])
                prior = json.loads(row['credits_json'])
                if sum(prior.values()) != row['distributed_satk']:
                    raise RuntimeError('Stored PPLNS allocation disagrees with its block')
                deltas = {w:credits.get(w,0)-prior.get(w,0) for w in set(credits)|set(prior)}
                blocked = False
                for wallet, delta in deltas.items():
                    if delta >= 0:
                        continue
                    account = conn.execute('SELECT unpaid_satk FROM miners WHERE wallet_address=?',(wallet,)).fetchone()
                    pending = conn.execute("SELECT 1 FROM payout_intents WHERE wallet_address=? AND status!='confirmed' LIMIT 1",(wallet,)).fetchone()
                    if account is None or account[0]+delta < 0 or pending:
                        blocked = True
                        break
                if blocked:
                    conn.execute('INSERT OR REPLACE INTO pplns_reward_holds VALUES(?,?,?)',
                                 (block_hash,'reward debit conflicts with paid or reserved funds',time.time()))
                    continue
                for wallet, delta in deltas.items():
                    if delta:
                        conn.execute("INSERT INTO miners(wallet_address,unpaid_satk) VALUES(?,?) "
                                     "ON CONFLICT(wallet_address) DO UPDATE SET unpaid_satk=unpaid_satk+excluded.unpaid_satk",(wallet,delta))
                conn.execute('UPDATE blocks SET reward_satk=?,pool_fee_satk=?,finder_bonus_satk=?,distributed_satk=?,height=? WHERE block_hash=?',
                             (amount,fee,bonus,sum(credits.values()),int(block.get('height',row['height'])),block_hash))
                conn.execute('UPDATE pplns_block_allocations SET credits_json=?,canonical_height=?,updated_at=? WHERE block_hash=?',
                             (json.dumps(credits,sort_keys=True),int(block.get('height',row['height'])),time.time(),block_hash))
                conn.execute('DELETE FROM pplns_reward_holds WHERE block_hash=?',(block_hash,))
                updated += int(amount != row['reward_satk'])
        return updated

    def record_solo_block(
        self,
        block_hash: str,
        height: int,
        reward_satk: int,
        finder_wallet: str,
        finder_worker: str,
        payout_address: str,
        is_pool_fee: bool,
        timestamp: Optional[float] = None,
    ) -> None:
        """Persist an accepted SOLO block for transparent pool telemetry.

        This is an operational attribution ledger only.  Consensus issuance
        remains exclusively owned by the node.
        """
        reward = max(0, int(reward_satk))
        fee_satk = reward if is_pool_fee else 0
        distributed_satk = 0 if is_pool_fee else reward
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO blocks (
                    block_hash, height, reward_satk, pool_fee_satk,
                    finder_bonus_satk, distributed_satk, finder_wallet,
                    finder_worker, timestamp, shares_in_window, window_weight,
                    mode, is_pool_fee, payout_address
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 0, 0.0, 'SOLO', ?, ?)
                """,
                (
                    block_hash,
                    int(height),
                    reward,
                    fee_satk,
                    distributed_satk,
                    finder_wallet,
                    finder_worker,
                    time.time() if timestamp is None else float(timestamp),
                    1 if is_pool_fee else 0,
                    payout_address,
                ),
            )

    def reconcile_solo_block_rewards(self, canonical_blocks: List[dict]) -> int:
        """Reconcile initially-red DAG submissions after they enter blue order."""
        updated = 0
        with self._get_connection() as conn:
            for block in canonical_blocks:
                block_hash = str(block.get("hash", ""))
                if not block_hash:
                    continue
                reward_satk = int(block.get("miner_base_sats", 0)) + int(block.get("fees_sats", 0))
                row = conn.execute(
                    "SELECT is_pool_fee FROM blocks WHERE block_hash = ? AND mode = 'SOLO'",
                    (block_hash,),
                ).fetchone()
                if not row:
                    continue
                is_pool_fee = bool(row["is_pool_fee"])
                conn.execute(
                    """
                    UPDATE blocks
                    SET reward_satk = ?,
                        pool_fee_satk = ?,
                        distributed_satk = ?
                    WHERE block_hash = ?
                    """,
                    (
                        reward_satk,
                        reward_satk if is_pool_fee else 0,
                        0 if is_pool_fee else reward_satk,
                        block_hash,
                    ),
                )
                updated += 1
        return updated

    def get_dashboard_stats(self, active_window_seconds: float = 600.0) -> dict:
        """Return persistent, measured Stratum statistics for the dashboard."""
        window = max(60.0, float(active_window_seconds))
        now = time.time()
        cutoff = now - window
        with self._get_connection() as conn:
            share_rows = conn.execute(
                """
                SELECT wallet_address, worker_name,
                       COUNT(*) AS shares_total,
                       SUM(difficulty) AS difficulty_total,
                       MAX(timestamp) AS last_share_timestamp
                FROM shares
                GROUP BY wallet_address, worker_name
                ORDER BY last_share_timestamp DESC
                """
            ).fetchall()
            window_rows = conn.execute(
                """
                SELECT wallet_address, worker_name,
                       COUNT(*) AS shares_window,
                       SUM(difficulty) AS difficulty_window,
                       MAX(timestamp) AS last_share_timestamp
                FROM shares
                WHERE timestamp >= ?
                GROUP BY wallet_address, worker_name
                """,
                (cutoff,),
            ).fetchall()
            block_rows = conn.execute(
                """
                SELECT finder_wallet, finder_worker,
                       COUNT(*) AS accepted_blocks,
                       SUM(CASE WHEN is_pool_fee = 0 THEN 1 ELSE 0 END) AS miner_blocks,
                       SUM(CASE WHEN is_pool_fee = 1 THEN 1 ELSE 0 END) AS fee_blocks,
                       SUM(CASE WHEN is_pool_fee = 0 THEN distributed_satk ELSE 0 END) AS miner_reward_satk
                FROM blocks
                GROUP BY finder_wallet, finder_worker
                """
            ).fetchall()
            totals = conn.execute(
                """
                SELECT COUNT(*) AS accepted_blocks,
                       COALESCE(SUM(is_pool_fee), 0) AS fee_blocks,
                       COALESCE(SUM(pool_fee_satk), 0) AS fee_satk,
                       COALESCE(SUM(distributed_satk), 0) AS distributed_satk
                FROM blocks
                """
            ).fetchone()
            fee_recipients = [dict(row) for row in conn.execute(
                """
                SELECT payout_address,
                       COUNT(*) AS fee_blocks,
                       COALESCE(SUM(pool_fee_satk), 0) AS fee_satk,
                       MIN(timestamp) AS first_timestamp,
                       MAX(timestamp) AS last_timestamp
                FROM blocks
                WHERE is_pool_fee = 1
                GROUP BY payout_address
                ORDER BY fee_satk DESC
                """
            ).fetchall()]
            recent_blocks = [dict(row) for row in conn.execute(
                """
                SELECT block_hash, height, reward_satk, pool_fee_satk,
                       distributed_satk, finder_wallet, finder_worker,
                       timestamp, mode, is_pool_fee, payout_address
                FROM blocks ORDER BY timestamp DESC LIMIT 250
                """
            ).fetchall()]
            combo_public_winners = [dict(row) for row in conn.execute(
                """
                SELECT winner_block_hash, epoch_id, draw_height, worker_name,
                       mode, hashrate_hps, hashrate_window_seconds,
                       block_timestamp
                FROM combo_public_winners
                ORDER BY draw_height DESC, epoch_id DESC
                """
            ).fetchall()]

        window_map = {
            (row["wallet_address"], row["worker_name"]): row
            for row in window_rows
        }
        block_map = {
            (row["finder_wallet"], row["finder_worker"]): row
            for row in block_rows
        }
        workers = []
        for row in share_rows:
            key = (row["wallet_address"], row["worker_name"])
            window_row = window_map.get(key)
            blocks = block_map.get(key)
            window_diff = float(window_row["difficulty_window"] or 0.0) if window_row else 0.0
            workers.append({
                "wallet_address": row["wallet_address"],
                "worker_name": row["worker_name"],
                "shares_total": int(row["shares_total"] or 0),
                "shares_window": int(window_row["shares_window"] or 0) if window_row else 0,
                "hashrate_hps": window_diff * (2**32) / window,
                "last_share_timestamp": float(row["last_share_timestamp"] or 0.0),
                "accepted_blocks": int(blocks["accepted_blocks"] or 0) if blocks else 0,
                "miner_blocks": int(blocks["miner_blocks"] or 0) if blocks else 0,
                "fee_blocks": int(blocks["fee_blocks"] or 0) if blocks else 0,
                "miner_reward_satk": int(blocks["miner_reward_satk"] or 0) if blocks else 0,
            })

        return {
            "window_seconds": window,
            "workers": workers,
            "accepted_blocks": int(totals["accepted_blocks"] or 0),
            "fee_blocks": int(totals["fee_blocks"] or 0),
            "fee_satk": int(totals["fee_satk"] or 0),
            "distributed_satk": int(totals["distributed_satk"] or 0),
            "fee_recipients": fee_recipients,
            "pool_hashrate_hps": sum(worker["hashrate_hps"] for worker in workers),
            "recent_blocks": recent_blocks,
            "combo_public_winners": combo_public_winners,
        }

    def get_miner(self, wallet_address: str) -> Optional[MinerBalance]:
        """Fetch miner accounting state."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM miners WHERE wallet_address = ?", (wallet_address,)).fetchone()
            if not row:
                return None
            return MinerBalance(
                wallet_address=row["wallet_address"],
                unpaid_satk=row["unpaid_satk"],
                paid_satk=row["paid_satk"],
                total_shares_weight=row["total_shares_weight"],
                total_shares_count=row["total_shares_count"],
                last_share_timestamp=row["last_share_timestamp"],
                payout_threshold_satk=row["payout_threshold_satk"],
            )

    def get_pending_payouts(self, default_threshold_satk: int = 500_000_000) -> List[dict]:
        """List all miners eligible for automatic payout."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT wallet_address, unpaid_satk, payout_threshold_satk
                FROM miners
                WHERE unpaid_satk >= CASE
                    WHEN payout_threshold_satk > 0 THEN payout_threshold_satk
                    ELSE ?
                END
                ORDER BY unpaid_satk DESC
                """,
                (default_threshold_satk,),
            ).fetchall()
            return [
                {
                    "wallet_address": r["wallet_address"],
                    "unpaid_satk": r["unpaid_satk"],
                    "unpaid_btck": r["unpaid_satk"] / SATK_PER_COIN,
                    "threshold_satk": r["payout_threshold_satk"],
                }
                for r in rows
            ]

    def get_payout_intents(self,limit=None) -> List[dict]:
        with self._get_connection() as conn:
            query="SELECT * FROM payout_intents WHERE status != 'confirmed' ORDER BY created_at,txid"
            return [dict(row) for row in conn.execute(query+(' LIMIT ?' if limit is not None else ''),
                                                      (int(limit),) if limit is not None else ())]

    def remember_payout_locator(self, txid, height, block_hash):
        """Retain a canonical receipt locator without settling any liability."""
        if type(height) is not int or height < 0 or not isinstance(block_hash, str) or len(block_hash) != 64:
            raise ValueError('invalid payout confirmation locator')
        bytes.fromhex(block_hash)
        with closing(self._get_connection()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT transaction_json FROM payout_intents WHERE txid=? AND status!='confirmed'", (txid,)).fetchone()
            if row:
                body = json.loads(row[0])
                body['_confirmation_locator'] = {'height': height, 'block_hash': block_hash}
                conn.execute('UPDATE payout_intents SET transaction_json=? WHERE txid=?',
                             (json.dumps(body, sort_keys=True), txid))

    def prepare_payout(self, tx, wallet_address, amount_satk, fee_satk):
        """Persist the EXACT signed transaction before any network write.

        An unknown broadcast outcome must never generate another random tx.
        Transactions are public data; no signing/view keys enter this table.
        """
        body=transaction_to_dict(tx)
        inputs={(i['prev_txid'],int(i['prev_vout'])) for i in body['inputs']}
        if not 0<len(inputs)<=100 or len(body['inputs'])!=len(inputs):
            raise ValueError('payout inputs outside safe transaction bounds')
        with self._get_connection() as conn:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('BEGIN IMMEDIATE')
            if self.has_reward_reconciliation_hold(conn):
                raise ValueError('PPLNS rewards require reconciliation before payment')
            row=conn.execute('SELECT unpaid_satk FROM miners WHERE wallet_address=?',(wallet_address,)).fetchone()
            if not row or not 0<amount_satk<=row[0] or fee_satk<0:
                raise ValueError('payout exceeds recorded liability')
            pending=conn.execute("SELECT transaction_json FROM payout_intents WHERE status!='confirmed'").fetchall()
            for old in pending:
                reserved={(i['prev_txid'],int(i['prev_vout'])) for i in json.loads(old[0])['inputs']}
                if inputs & reserved:
                    raise ValueError('payout input already reserved')
            conn.execute('INSERT INTO payout_intents VALUES(?,?,?,?,?,?,?)',
                (tx.txid,wallet_address,amount_satk,fee_satk,json.dumps(body,sort_keys=True),'prepared',time.time()))

    def record_payout(self, txid: str, wallet_address: str, amount_satk: int, fee_satk: int = 10_000) -> None:
        """Record an executed payout on-chain transaction and deduct from unpaid balance."""
        with self._get_connection() as conn:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT wallet_address,amount_satk,fee_satk FROM payouts WHERE txid=?',(txid,)).fetchone()
            if old:
                if tuple(old)!=(wallet_address,amount_satk,fee_satk):
                    raise ValueError('payout identity conflicts with recorded transaction')
                return
            balance=conn.execute('SELECT unpaid_satk FROM miners WHERE wallet_address=?',(wallet_address,)).fetchone()
            if not balance or not 0<amount_satk<=balance[0]:
                raise ValueError('payout exceeds recorded unpaid balance')
            conn.execute(
                """
                INSERT INTO payouts (txid, wallet_address, amount_satk, fee_satk, timestamp, status)
                VALUES (?, ?, ?, ?, ?, 'broadcasted')
                """,
                (txid, wallet_address, amount_satk, fee_satk, time.time()),
            )
            conn.execute(
                """
                UPDATE miners
                SET unpaid_satk = MAX(0, unpaid_satk - ?),
                    paid_satk = paid_satk + ?
                WHERE wallet_address = ?
                """,
                (amount_satk, amount_satk, wallet_address),
            )
            conn.execute("UPDATE payout_intents SET status='confirmed' WHERE txid=?",(txid,))
            conn.execute("UPDATE payouts SET status='confirmed' WHERE txid=?",(txid,))
        logger.info(
            "💸 Recorded PPLNS payout to %s: %.4f BTC-K (txid: %s)",
            wallet_address[:18] + "...",
            amount_satk / SATK_PER_COIN,
            txid[:16],
        )

    def get_pool_stats(self, active_window_seconds: float = 600.0) -> dict:
        """Fetch general pool statistics for web dashboard or API."""
        now = time.time()
        cutoff = now - active_window_seconds
        with self._get_connection() as conn:
            active_row = conn.execute(
                """
                SELECT COUNT(DISTINCT wallet_address) as active_miners,
                       COUNT(DISTINCT worker_name) as active_workers,
                       SUM(difficulty) as window_diff,
                       COUNT(id) as window_shares
                FROM shares
                WHERE timestamp >= ?
                """,
                (cutoff,),
            ).fetchone()

            tot_miners = conn.execute("SELECT COUNT(*) FROM miners").fetchone()[0]
            tot_blocks = conn.execute("SELECT COUNT(*), SUM(reward_satk), SUM(distributed_satk) FROM blocks").fetchone()
            tot_paid = conn.execute("SELECT SUM(amount_satk) FROM payouts").fetchone()[0] or 0
            unpaid_liability = conn.execute("SELECT SUM(unpaid_satk) FROM miners").fetchone()[0] or 0

            window_diff = active_row["window_diff"] or 0.0
            # Kaspa / kHeavyHash hashrate estimation:
            # H = (sum(diff) * 2^32) / delta_seconds (approx 4.295 GH per unit diff)
            hashrate_hps = 0.0
            if active_window_seconds > 0 and window_diff > 0:
                hashrate_hps = (window_diff * (2**32)) / active_window_seconds

            return {
                "active_miners": active_row["active_miners"] or 0,
                "active_workers": active_row["active_workers"] or 0,
                "total_miners": tot_miners,
                "estimated_hashrate_th": hashrate_hps / 1e12,
                "blocks_found": tot_blocks[0] or 0,
                "total_reward_btck": (tot_blocks[1] or 0) / SATK_PER_COIN,
                "total_distributed_btck": (tot_blocks[2] or 0) / SATK_PER_COIN,
                "total_paid_btck": tot_paid / SATK_PER_COIN,
                "unpaid_liabilities_btck": unpaid_liability / SATK_PER_COIN,
                "pplns_window_size": self.window_size,
            }


class AutoPayoutWorker:
    """Asynchronous background worker that executes automated batch settlements.

    Signs offline transactions using the Pool Treasury Stealth Wallet and broadcasts
    to the BTC-K blockDAG node via RPC.
    """

    def __init__(
        self,
        engine: PPLNSEngine,
        treasury_wallet: Optional[StealthWallet],
        node_rpc,
        payout_utc_hours: Tuple[int, ...] = (0, 6, 12, 18),
        default_threshold_satk: int = 500_000_000,
        tx_fee_satk: int = 10_000,
        treasury_sync=None,
    ):
        self.engine = engine
        self.treasury_wallet = treasury_wallet
        self.node_rpc = node_rpc
        self.treasury_sync = treasury_sync
        normalized_hours = tuple(sorted(set(int(hour) for hour in payout_utc_hours)))
        if not normalized_hours or any(hour < 0 or hour > 23 for hour in normalized_hours):
            raise ValueError("payout UTC hours must contain values from 0 through 23")
        self.payout_utc_hours = normalized_hours
        self.default_threshold_satk = default_threshold_satk
        self.tx_fee_satk = tx_fee_satk
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._cycle_lock=asyncio.Lock()
        self.synced_height=None
        enabled=os.getenv('BTCK_PPLNS_AUTOPAYOUT_ENABLED','1').strip()
        if enabled not in ('0','1'):
            raise ValueError('BTCK_PPLNS_AUTOPAYOUT_ENABLED must be0or1')
        self.payout_enabled=enabled=='1'

    async def sync_treasury_utxos(self) -> Optional[int]:
        """Page through current outputs without constructing a full-node UTXO response."""
        if not self.treasury_wallet or not self.node_rpc:
            return 0
        try:
            if self.treasury_sync is not None:
                candidate, height = await asyncio.to_thread(self.treasury_sync, self.treasury_wallet)
                self.treasury_wallet.utxos = candidate.utxos
                self.synced_height = height
                return self.treasury_wallet.get_balance_satk()
            # Rebuild from current unspent outputs.  The bounded page size keeps
            # both the node response and the pool's transient allocations small.
            candidate=StealthWallet(self.treasury_wallet.keypair,'isolated-treasury-scan')
            from_height = 0
            anchor_hash = ""
            identity=None
            while True:
                page = await self.node_rpc.call(
                    "getWalletSyncPage",
                    {
                        "from_height": from_height,
                        "anchor_hash": anchor_hash,
                        "limit": 1000,
                        "unspent_only": True,
                    },
                )
                if page.get('unspent_only') is not True or int(page.get('from_height',-1))!=from_height:
                    raise RuntimeError('invalid treasury scan page contract')
                if identity is None:
                    identity=page.get('network')
                if not identity or identity!=page.get('network'):
                    raise RuntimeError('treasury network identity changed during scan')
                next_height=int(page.get('next_height',-1))
                blocks=page.get('blocks',[])
                if next_height!=from_height+len(blocks):
                    raise RuntimeError('non-contiguous treasury page')
                for offset,block in enumerate(blocks):
                    if block.get('height')!=from_height+offset or len(str(block.get('hash','')))!=64:
                        raise RuntimeError('non-contiguous treasury block cursor')
                expected_anchor=blocks[-1]['hash'] if blocks else anchor_hash
                if page.get('anchor_hash')!=expected_anchor:
                    raise RuntimeError('treasury end anchor mismatch')
                raw_outputs = [
                    output
                    for block in page.get("blocks", [])
                    for output in block.get("created", [])
                ]
                # Elliptic-curve ownership checks are CPU-bound. Keep them off
                # the Stratum event loop so share readers remain responsive.
                await asyncio.to_thread(
                    candidate.scan_chain_outputs,
                    raw_outputs,
                )
                if page.get("done"):
                    height=int(page['tip_height'])
                    if next_height!=height+1:
                        raise RuntimeError('treasury scan terminated before current tip')
                    self.treasury_wallet.utxos=candidate.utxos
                    self.synced_height=height
                    break
                next_height = int(page.get("next_height", from_height))
                next_anchor = str(page.get("anchor_hash", ""))
                if next_height <= from_height or not next_anchor:
                    raise RuntimeError("wallet sync cursor did not advance")
                from_height = next_height
                anchor_hash = next_anchor
            bal = self.treasury_wallet.get_balance_satk()
            return bal
        except Exception as exc:
            logger.warning("Failed to sync treasury UTXOs: %s", exc)
            self.synced_height=None
            return None

    async def run_payout_cycle(self) -> List[dict]:
        if not self.payout_enabled:
            return []
        async with self._cycle_lock:
            return await self._run_payout_cycle()

    async def _confirm_intent(self,intent):
        params = {'txid': intent['txid']}
        locator = json.loads(intent['transaction_json']).get('_confirmation_locator')
        if locator:
            params.update(locator)
        try:
            status=await self.node_rpc.call('getTransaction',params)
        except RPCError as exc:
            if not locator or str(exc) != 'canonical transaction locator mismatch':
                raise
            # A short reorganization invalidates the saved locator. Recheck
            # only the node's bounded recent window, retaining the same txid.
            status = await self.node_rpc.call('getTransaction', {'txid': intent['txid']})
        if (status.get('txid') == intent['txid'] and status.get('status') == 'confirmed'
                and type(status.get('height')) is int and isinstance(status.get('block_hash'), str)):
            await asyncio.to_thread(self.engine.remember_payout_locator, intent['txid'],
                                    status['height'], status['block_hash'])
        if (status.get('txid')==intent['txid'] and status.get('status')=='confirmed'
                and int(status.get('confirmations',0))>=6):
            await asyncio.to_thread(self.engine.record_payout,intent['txid'],intent['wallet_address'],
                                    intent['amount_satk'],intent['fee_satk'])
            return True
        return False

    async def _broadcast_intent(self,intent):
        # Parse persisted public transaction; never reconstruct a new signed tx
        # after a timeout, rejection or process restart.
        tx=transaction_from_dict(json.loads(intent['transaction_json']))
        if tx.txid!=intent['txid'] or not await asyncio.to_thread(StealthWallet.verify_transaction,tx):
            raise ValueError('persisted payout transaction failed verification')
        result=await self.node_rpc.call('submitTransaction',{'transaction':transaction_to_dict(tx)})
        if result.get('accepted'):
            with self.engine._get_connection() as conn:
                conn.execute("UPDATE payout_intents SET status='broadcasted' WHERE txid=? AND status='prepared'",(tx.txid,))
            return await self._confirm_intent(intent)
        return False

    async def _run_payout_cycle(self) -> List[dict]:
        """Execute one payout sweep for all eligible miners."""
        if not self.treasury_wallet:
            return []

        # Do not request and scan the full UTXO set when there is nothing to
        # pay. This is the normal state of a newly launched PPLNS pool.
        settled=[]
        intents=await asyncio.to_thread(self.engine.get_payout_intents,16)
        if await asyncio.to_thread(self.engine.has_reward_reconciliation_hold):
            # A previously broadcast payment may still confirm. Observe it,
            # but do not scan, sign, or send another transaction under a hold.
            for intent in intents:
                try:
                    if await self._confirm_intent(intent):
                        settled.append({'wallet':intent['wallet_address'],'txid':intent['txid'],'status':'confirmed'})
                except Exception:
                    pass
            logger.warning('PPLNS payment held: block rewards need reconciliation')
            return settled
        for intent in intents:
            try:
                if await self._confirm_intent(intent) or await self._broadcast_intent(intent):
                    settled.append({'wallet':intent['wallet_address'],'amount_btck':intent['amount_satk']/SATK_PER_COIN,
                                    'txid':intent['txid'],'status':'success'})
            except Exception as exc:
                logger.warning('Pending payout remains reserved (%s)',type(exc).__name__)
        eligible = await asyncio.to_thread(
            self.engine.get_pending_payouts,
            self.default_threshold_satk,
        )
        if not eligible:
            return settled

        if await self.sync_treasury_utxos() is None:
            return settled
        intents=await asyncio.to_thread(self.engine.get_payout_intents)
        reserved={(i['prev_txid'],int(i['prev_vout'])) for intent in intents
                  for i in json.loads(intent['transaction_json'])['inputs']}
        pending_wallets={intent['wallet_address'] for intent in intents}
        for utxo in self.treasury_wallet.utxos:
            if (utxo.txid,utxo.vout) in reserved:
                utxo.spent=True

        payout_results = settled
        for item in eligible[:16]:
            wallet_addr = item["wallet_address"]
            if wallet_addr in pending_wallets:
                continue
            unpaid_satk = item["unpaid_satk"]
            usable=sorted((u for u in self.treasury_wallet.utxos
                if self.treasury_wallet.is_utxo_spendable(u,self.synced_height)),
                key=lambda u:(-u.amount_satk,u.txid,u.vout))[:100]
            amount_satk=min(unpaid_satk,sum(u.amount_satk for u in usable)-self.tx_fee_satk)
            if amount_satk<min(unpaid_satk,self.default_threshold_satk):
                logger.warning('No mature, unreserved treasury funds available for payout')
                continue

            try:
                # Create and sign stealth transfer
                signer=StealthWallet(self.treasury_wallet.keypair,'bounded-payout-signer')
                signer.utxos=[copy.copy(u) for u in usable]
                tx = await asyncio.to_thread(signer.create_and_sign_transfer,
                    recipient_stealth_addr=wallet_addr,
                    amount_satk=amount_satk,
                    fee_satk=self.tx_fee_satk,
                    current_height=self.synced_height,
                )
                await asyncio.to_thread(self.engine.prepare_payout,tx,wallet_addr,amount_satk,self.tx_fee_satk)
                used={(i.prev_txid,i.prev_vout) for i in tx.inputs}
                for u in self.treasury_wallet.utxos:
                    if (u.txid,u.vout) in used:u.spent=True
                intent={'txid':tx.txid,'wallet_address':wallet_addr,'amount_satk':amount_satk,
                        'fee_satk':self.tx_fee_satk,'transaction_json':json.dumps(transaction_to_dict(tx))}
                if await self._broadcast_intent(intent):
                    payout_results.append(
                        {
                            "wallet": wallet_addr,
                            "amount_btck": amount_satk / SATK_PER_COIN,
                            "txid": tx.txid,
                            "status": "success",
                        }
                    )
            except Exception as exc:
                logger.error('Payout remains unpaid/reserved after error: %s',type(exc).__name__)

        return payout_results

    def seconds_until_next_utc_batch(self, now_timestamp: float | None = None) -> float:
        """Return the delay to the next strictly-future fixed UTC batch."""
        now = datetime.fromtimestamp(
            time.time() if now_timestamp is None else now_timestamp,
            tz=timezone.utc,
        )
        for hour in self.payout_utc_hours:
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return (candidate - now).total_seconds()
        tomorrow = (now + timedelta(days=1)).replace(
            hour=self.payout_utc_hours[0], minute=0, second=0, microsecond=0
        )
        return (tomorrow - now).total_seconds()

    async def _loop(self) -> None:
        next_batch = time.time() + self.seconds_until_next_utc_batch()
        while self.running:
            try:
                # Restart recovery and confirmation polling must not wait six
                # hours. This path only handles already durable intents.
                await self.observe_pending_payments()
            except Exception as exc:
                logger.error('Pending payment observation failed: %s', type(exc).__name__)
            await asyncio.sleep(min(30.0, max(0.0, next_batch - time.time())))
            if not self.running:
                break
            if time.time() >= next_batch:
                try:
                    await self.run_payout_cycle()
                except Exception as exc:
                    logger.error('AutoPayout batch failed: %s', type(exc).__name__)
                next_batch = time.time() + self.seconds_until_next_utc_batch()

    async def observe_pending_payments(self):
        """Confirm/retry saved transactions without preparing a new payment."""
        if not self.payout_enabled:
            return []
        async with self._cycle_lock:
            settled = []
            held = await asyncio.to_thread(self.engine.has_reward_reconciliation_hold)
            for intent in await asyncio.to_thread(self.engine.get_payout_intents, 16):
                try:
                    confirmed = await self._confirm_intent(intent)
                    if not confirmed and not held:
                        confirmed = await self._broadcast_intent(intent)
                    if confirmed:
                        settled.append(intent['txid'])
                except Exception as exc:
                    logger.warning('Saved payout remains reserved: %s', type(exc).__name__)
            return settled

    def start(self) -> None:
        if not self.payout_enabled:
            logger.info('PPLNS automatic payouts DISABLED by operator; no scan/sign/broadcast task started')
            return
        if self.running or not self.treasury_wallet:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "PPLNS AutoPayout worker active (Threshold: %.2f BTC-K, UTC batches: %s)",
            self.default_threshold_satk / SATK_PER_COIN,
            ",".join(f"{hour:02d}:00" for hour in self.payout_utc_hours),
        )

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

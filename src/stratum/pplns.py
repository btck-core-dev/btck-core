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
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tokenomics.emission_schedule import SATK_PER_COIN
from privacy.wallet_engine import StealthWallet, transaction_to_dict

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

                CREATE TABLE IF NOT EXISTS payouts (
                    txid TEXT PRIMARY KEY,
                    wallet_address TEXT NOT NULL,
                    amount_satk INTEGER NOT NULL,
                    fee_satk INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_payouts_wallet ON payouts(wallet_address);

                CREATE TABLE IF NOT EXISTS pool_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
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
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO shares (wallet_address, worker_name, difficulty, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (wallet_address, worker_name, float(difficulty), ts),
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
                (wallet_address, float(difficulty), ts),
            )

    def process_block(
        self,
        block_hash: str,
        height: int,
        reward_satk: int,
        pool_fee_rate: float,
        finder_wallet: str = "",
        finder_worker: str = "",
        window_size: Optional[int] = None,
    ) -> dict:
        """Settle a found block via PPLNS.

        Divides net block reward proportionally across the last N shares.
        """
        N = window_size or self.window_size
        fee_rate = max(0.0, min(1.0, float(pool_fee_rate)))
        pool_fee_satk = int(reward_satk * fee_rate)

        # Optional finder bonus
        finder_bonus_satk = 0
        if finder_wallet and self.finder_bonus_rate > 0.0:
            finder_bonus_satk = int(reward_satk * self.finder_bonus_rate)

        distributable_satk = max(0, reward_satk - pool_fee_satk - finder_bonus_satk)

        with self._get_connection() as conn:
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

            total_window_weight = sum(r["difficulty"] for r in rows)
            miner_weights: Dict[str, float] = {}
            for r in rows:
                w = r["wallet_address"]
                miner_weights[w] = miner_weights.get(w, 0.0) + r["difficulty"]

            miner_credits: Dict[str, int] = {}
            total_distributed = 0

            if total_window_weight > 0 and distributable_satk > 0:
                for wallet, weight in miner_weights.items():
                    share_fraction = weight / total_window_weight
                    credit = int(distributable_satk * share_fraction)
                    if credit > 0:
                        miner_credits[wallet] = credit
                        total_distributed += credit

                # Credit finder bonus to finder
                if finder_wallet and finder_bonus_satk > 0:
                    miner_credits[finder_wallet] = miner_credits.get(finder_wallet, 0) + finder_bonus_satk
                    total_distributed += finder_bonus_satk

                # Credit miners' unpaid balances
                for wallet, credit in miner_credits.items():
                    conn.execute(
                        """
                        INSERT INTO miners (wallet_address, unpaid_satk)
                        VALUES (?, ?)
                        ON CONFLICT(wallet_address) DO UPDATE SET
                            unpaid_satk = unpaid_satk + excluded.unpaid_satk
                        """,
                        (wallet, credit),
                    )
            elif finder_wallet and reward_satk > pool_fee_satk:
                # If window was completely empty (very rare / cold start), credit all net reward to finder
                credit = reward_satk - pool_fee_satk
                miner_credits[finder_wallet] = credit
                total_distributed = credit
                conn.execute(
                    """
                    INSERT INTO miners (wallet_address, unpaid_satk)
                    VALUES (?, ?)
                    ON CONFLICT(wallet_address) DO UPDATE SET
                        unpaid_satk = unpaid_satk + excluded.unpaid_satk
                    """,
                    (finder_wallet, credit),
                )

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
                    time.time(),
                    len(rows),
                    total_window_weight,
                ),
            )

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

    def record_payout(self, txid: str, wallet_address: str, amount_satk: int, fee_satk: int = 10_000) -> None:
        """Record an executed payout on-chain transaction and deduct from unpaid balance."""
        with self._get_connection() as conn:
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
        payout_interval_seconds: float = 60.0,
        default_threshold_satk: int = 500_000_000,
        tx_fee_satk: int = 10_000,
    ):
        self.engine = engine
        self.treasury_wallet = treasury_wallet
        self.node_rpc = node_rpc
        self.payout_interval_seconds = payout_interval_seconds
        self.default_threshold_satk = default_threshold_satk
        self.tx_fee_satk = tx_fee_satk
        self.running = False
        self._task: Optional[asyncio.Task] = None

    async def sync_treasury_utxos(self) -> int:
        """Scan node UTXO set for available treasury funds."""
        if not self.treasury_wallet or not self.node_rpc:
            return 0
        try:
            utxo_resp = await self.node_rpc.call("getUTXOSet")
            raw_outputs = utxo_resp.get("outputs", [])
            self.treasury_wallet.scan_chain_outputs(raw_outputs)
            bal = self.treasury_wallet.get_balance_satk()
            return bal
        except Exception as exc:
            logger.warning("Failed to sync treasury UTXOs: %s", exc)
            return 0

    async def run_payout_cycle(self) -> List[dict]:
        """Execute one payout sweep for all eligible miners."""
        if not self.treasury_wallet:
            return []

        await self.sync_treasury_utxos()
        available_satk = self.treasury_wallet.get_balance_satk()
        eligible = self.engine.get_pending_payouts(self.default_threshold_satk)
        if not eligible:
            return []

        payout_results = []
        for item in eligible:
            wallet_addr = item["wallet_address"]
            unpaid_satk = item["unpaid_satk"]
            needed_satk = unpaid_satk + self.tx_fee_satk

            if available_satk < needed_satk:
                logger.warning(
                    "Treasury balance insufficient for payout: have %.4f BTC-K, need %.4f BTC-K for miner %s",
                    available_satk / SATK_PER_COIN,
                    needed_satk / SATK_PER_COIN,
                    wallet_addr[:16],
                )
                continue

            try:
                # Create and sign stealth transfer
                tx = self.treasury_wallet.create_and_sign_transfer(
                    recipient_stealth_addr=wallet_addr,
                    amount_satk=unpaid_satk,
                    fee_satk=self.tx_fee_satk,
                )
                # Broadcast to node
                broadcast_res = await self.node_rpc.call(
                    "submitTransaction",
                    {"transaction": transaction_to_dict(tx)},
                )
                if broadcast_res.get("accepted"):
                    self.engine.record_payout(tx.txid, wallet_addr, unpaid_satk, self.tx_fee_satk)
                    available_satk -= needed_satk
                    payout_results.append(
                        {
                            "wallet": wallet_addr,
                            "amount_btck": unpaid_satk / SATK_PER_COIN,
                            "txid": tx.txid,
                            "status": "success",
                        }
                    )
                else:
                    logger.warning("Node rejected payout transaction %s: %s", tx.txid, broadcast_res)
            except Exception as exc:
                logger.error("Error executing payout to %s: %s", wallet_addr, exc)

        return payout_results

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.run_payout_cycle()
            except Exception as exc:
                logger.error("AutoPayout loop encountered error: %s", exc)
            await asyncio.sleep(self.payout_interval_seconds)

    def start(self) -> None:
        if self.running or not self.treasury_wallet:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "🚀 PPLNS AutoPayout worker active (Threshold: %.2f BTC-K, Interval: %ds)",
            self.default_threshold_satk / SATK_PER_COIN,
            int(self.payout_interval_seconds),
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

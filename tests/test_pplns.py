#!/usr/bin/env python3
import asyncio
import os
import shutil
import tempfile
import unittest

from stratum.pplns import PPLNSEngine, AutoPayoutWorker
from tokenomics.emission_schedule import SATK_PER_COIN
from privacy.wallet_engine import StealthWallet


class TestPPLNSEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_pool.db")
        self.engine = PPLNSEngine(db_path=self.db_path, window_size=100, finder_bonus_rate=0.01)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_share_recording_and_miner_stats(self):
        w1 = "btck1s_miner_one"
        w2 = "btck1s_miner_two"

        self.engine.record_share(w1, "ks01", 512.0)
        self.engine.record_share(w1, "ks01", 512.0)
        self.engine.record_share(w2, "ks02", 1024.0)

        m1 = self.engine.get_miner(w1)
        self.assertIsNotNone(m1)
        self.assertEqual(m1.total_shares_count, 2)
        self.assertAlmostEqual(m1.total_shares_weight, 1024.0)

        m2 = self.engine.get_miner(w2)
        self.assertIsNotNone(m2)
        self.assertEqual(m2.total_shares_count, 1)
        self.assertAlmostEqual(m2.total_shares_weight, 1024.0)

        stats = self.engine.get_pool_stats(active_window_seconds=300)
        self.assertEqual(stats["active_miners"], 2)
        self.assertEqual(stats["active_workers"], 2)

    def test_block_settlement_proportions(self):
        # 3 miners with different weights:
        # Alice (diff 100), Bob (diff 300), Charlie (diff 600) -> Total weight = 1000
        # Alice = 10%, Bob = 30%, Charlie = 60%
        alice = "btck1s_alice"
        bob = "btck1s_bob"
        charlie = "btck1s_charlie"

        self.engine.record_share(alice, "worker1", 100.0)
        self.engine.record_share(bob, "worker2", 300.0)
        self.engine.record_share(charlie, "worker3", 600.0)

        reward_btck = 5.0
        reward_satk = int(reward_btck * SATK_PER_COIN)
        pool_fee_rate = 0.05  # 5% pool fee
        # Finder bonus = 1% (0.01) -> 0.05 BTC-K to Charlie
        # Pool fee = 5% (0.05) -> 0.25 BTC-K
        # Distributable = 94% (0.94) -> 4.70 BTC-K
        # Alice (10%) = 0.47 BTC-K
        # Bob (30%) = 1.41 BTC-K
        # Charlie (60%) = 2.82 BTC-K + 0.05 BTC-K bonus = 2.87 BTC-K

        res = self.engine.process_block(
            block_hash="test_block_hash_1",
            height=100,
            reward_satk=reward_satk,
            pool_fee_rate=pool_fee_rate,
            finder_wallet=charlie,
            finder_worker="worker3",
        )

        self.assertEqual(res["pool_fee_satk"], int(reward_satk * 0.05))
        self.assertEqual(res["finder_bonus_satk"], int(reward_satk * 0.01))

        m_alice = self.engine.get_miner(alice)
        m_bob = self.engine.get_miner(bob)
        m_charlie = self.engine.get_miner(charlie)

        self.assertAlmostEqual(m_alice.unpaid_btck, 0.47, places=4)
        self.assertAlmostEqual(m_bob.unpaid_btck, 1.41, places=4)
        self.assertAlmostEqual(m_charlie.unpaid_btck, 2.87, places=4)

        # Total distributed + fee should equal total reward (within integer rounding precision)
        total_accounted = m_alice.unpaid_satk + m_bob.unpaid_satk + m_charlie.unpaid_satk + res["pool_fee_satk"]
        self.assertTrue(abs(total_accounted - reward_satk) <= 5)

    def test_payout_threshold_and_recording(self):
        miner = "btck1s_dave"
        self.engine.record_share(miner, "ks0", 500.0)

        # Settle block giving Dave 6 BTC-K
        self.engine.process_block(
            block_hash="test_block_hash_2",
            height=101,
            reward_satk=int(6.0 * SATK_PER_COIN),
            pool_fee_rate=0.0,
            finder_wallet=miner,
        )

        pending = self.engine.get_pending_payouts(default_threshold_satk=int(5.0 * SATK_PER_COIN))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["wallet_address"], miner)
        self.assertGreaterEqual(pending[0]["unpaid_btck"], 5.0)

        # Record payout
        self.engine.record_payout(
            txid="fake_txid_12345",
            wallet_address=miner,
            amount_satk=int(5.0 * SATK_PER_COIN),
            fee_satk=10_000,
        )

        m_after = self.engine.get_miner(miner)
        self.assertAlmostEqual(m_after.paid_btck, 5.0, places=4)
        self.assertAlmostEqual(m_after.unpaid_btck, 1.0, places=4)

        # Now pending should be empty since unpaid is 1.0 < 5.0
        pending_after = self.engine.get_pending_payouts(default_threshold_satk=int(5.0 * SATK_PER_COIN))
        self.assertEqual(len(pending_after), 0)

    def test_database_reopen_preserves_balances_and_launch_time(self):
        wallet = "btck1s_restart_safe"
        launch_time = self.engine.get_or_create_launch_time(1_700_000_000.0)
        self.engine.record_share(wallet, "worker", 128.0, timestamp=1_700_000_001.0)
        self.engine.process_block(
            block_hash="restart-safe-block",
            height=200,
            reward_satk=5 * SATK_PER_COIN,
            pool_fee_rate=0.05,
            finder_wallet=wallet,
        )
        before = self.engine.get_miner(wallet)

        reopened = PPLNSEngine(db_path=self.db_path, window_size=100, finder_bonus_rate=0.01)
        after = reopened.get_miner(wallet)
        self.assertEqual(reopened.get_or_create_launch_time(), launch_time)
        self.assertEqual(after.unpaid_satk, before.unpaid_satk)
        self.assertEqual(after.total_shares_count, 1)

    def test_duplicate_block_settlement_is_idempotent(self):
        wallet = "btck1s_idempotent"
        self.engine.record_share(wallet, "worker", 128.0)
        first = self.engine.process_block(
            block_hash="same-block",
            height=201,
            reward_satk=5 * SATK_PER_COIN,
            pool_fee_rate=0.05,
            finder_wallet=wallet,
        )
        balance = self.engine.get_miner(wallet).unpaid_satk
        second = self.engine.process_block(
            block_hash="same-block",
            height=201,
            reward_satk=5 * SATK_PER_COIN,
            pool_fee_rate=0.05,
            finder_wallet=wallet,
        )
        self.assertNotIn("error", first)
        self.assertEqual(second["error"], "already_settled")
        self.assertEqual(self.engine.get_miner(wallet).unpaid_satk, balance)

    def test_combo_anti_hopping_uses_durable_share_slots(self):
        start = 1_700_000_000.0
        honest = "btck1s_combo_honest"
        hopper = "btck1s_combo_hopper"
        for slot in range(72):
            if slot not in (15, 45):
                self.engine.record_share(
                    honest, "ks0-a", 128.0, timestamp=start + slot * 600 + 1
                )
        for slot in (70, 71):
            self.engine.record_share(
                hopper, "rental", 128.0, timestamp=start + slot * 600 + 1
            )

        result = self.engine.evaluate_combo_anti_hopping(
            epoch_id=1,
            stage_id=1,
            epoch_start_time=start,
            epoch_end_time=start + 43_200,
        )
        decisions = {
            (item["wallet_address"], item["worker_name"]): item
            for item in result["workers"]
        }
        self.assertTrue(decisions[(honest, "ks0-a")]["qualified"])
        self.assertFalse(decisions[(hopper, "rental")]["qualified"])

        winner_hash = "11" * 32
        beacon_hash = "22" * 32
        self.engine.process_block(
            block_hash=winner_hash,
            height=43_200,
            reward_satk=5 * SATK_PER_COIN,
            pool_fee_rate=0.0,
            finder_wallet=honest,
            finder_worker="ks0-a",
            block_timestamp=start + 43_199,
        )
        resolution = {
            "epoch_id": 1,
            "stage_id": 1,
            "winner_block_hash": winner_hash,
            "beacon_hash": beacon_hash,
            "jackpot_amount_satk": 1_000_000,
            "tranches": [
                {"tranche_index": 0, "amount_satk": 500_000, "unlock_block_height": 43_300},
                {"tranche_index": 1, "amount_satk": 500_000, "unlock_block_height": 216_100},
            ],
            "epoch_start_timestamp": start,
            "draw_timestamp": start + 43_200,
        }
        first = self.engine.reconcile_combo_qualifications([resolution])
        second = self.engine.reconcile_combo_qualifications([resolution])
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["qualified"])
        self.assertEqual(second, [])
        before = self.engine.get_miner(honest).unpaid_satk
        released = self.engine.release_mature_combo_entitlements(43_300)
        self.assertEqual(len(released), 1)
        self.assertEqual(self.engine.get_miner(honest).unpaid_satk, before + 500_000)
        self.assertEqual(self.engine.release_mature_combo_entitlements(43_300), [])


class MockNodeRPC:
    def __init__(self, utxos):
        self.utxos = utxos
        self.submitted_txs = []

    async def call(self, method: str, params: dict = None):
        if method == "getUTXOSet":
            return {"outputs": self.utxos}
        elif method == "submitTransaction":
            self.submitted_txs.append(params["transaction"])
            return {"accepted": True, "txid": params["transaction"]["txid"]}
        raise ValueError(f"Unknown RPC method: {method}")


class TestAutoPayoutWorker(unittest.IsolatedAsyncioTestCase):
    async def test_auto_payout_cycle(self):
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_worker_pool.db")
        try:
            engine = PPLNSEngine(db_path=db_path, window_size=100)
            treasury = StealthWallet.generate("PoolTreasury")
            miner = StealthWallet.generate("Miner1")

            # 1. Simulate treasury receiving coinbase funds on chain (50 BTC-K)
            from privacy.stealth_address import StealthKeyPair
            r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(treasury.address)
            mock_utxo = {
                "txid": "11" * 32,
                "vout": 0,
                "amount_satk": int(50.0 * SATK_PER_COIN),
                "R_ephem_hex": r_ephem.hex(),
                "P_onetime_hex": p_onetime.hex(),
                "height": 50,
            }
            mock_rpc = MockNodeRPC(utxos=[mock_utxo])

            # 2. Credit miner with 10 BTC-K unpaid balance in PPLNS engine
            engine.record_share(miner.address, "worker1", 1000.0)
            engine.process_block(
                block_hash="test_block_3",
                height=51,
                reward_satk=int(10.0 * SATK_PER_COIN),
                pool_fee_rate=0.0,
                finder_wallet=miner.address,
            )

            worker = AutoPayoutWorker(
                engine=engine,
                treasury_wallet=treasury,
                node_rpc=mock_rpc,
                payout_interval_seconds=1.0,
                default_threshold_satk=int(5.0 * SATK_PER_COIN),
                tx_fee_satk=10_000,
            )

            # 3. Run one payout cycle
            payouts = await worker.run_payout_cycle()
            self.assertEqual(len(payouts), 1)
            self.assertEqual(payouts[0]["wallet"], miner.address)
            self.assertAlmostEqual(payouts[0]["amount_btck"], 10.0)
            self.assertEqual(len(mock_rpc.submitted_txs), 1)

            # Check engine balance updated
            m = engine.get_miner(miner.address)
            self.assertEqual(m.unpaid_satk, 0)
            self.assertEqual(m.paid_satk, int(10.0 * SATK_PER_COIN))

        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()

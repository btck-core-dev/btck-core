"""Frozen-window reward changes and fail-closed payment boundaries."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stratum.pplns import PPLNSEngine, AutoPayoutWorker
from stratum.pplns_rewards import allocate_reward


class RewardReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'pool.db'
        self.engine = PPLNSEngine(self.path, finder_bonus_rate=0)

    def tearDown(self):
        import gc
        gc.collect()
        self.directory.cleanup()

    def mine(self, reward=0):
        self.engine.record_share('alice','a',1)
        self.engine.record_share('bob','b',3)
        self.engine.process_block('block',10,reward,.05,'bob','b')

    def canonical(self, amount):
        return [{'hash':'block','height':11,'miner_base_sats':amount,'fees_sats':0}]

    def balance(self, wallet):
        return self.engine.get_miner(wallet).unpaid_satk

    def test_delayed_reward_uses_original_weights_and_fee_after_restart(self):
        self.mine()
        self.engine.record_share('mallory','late',10000)
        reopened = PPLNSEngine(self.path, finder_bonus_rate=.25)
        self.assertEqual(reopened.reconcile_pplns_block_rewards(self.canonical(1000)),1)
        self.assertEqual(self.balance('alice'),238)
        self.assertEqual(self.balance('bob'),712)
        self.assertEqual(self.balance('mallory'),0)
        self.assertEqual(reopened.reconcile_pplns_block_rewards(self.canonical(1000)),0)
        self.assertEqual(self.balance('alice')+self.balance('bob'),950)
        self.assertFalse(reopened.has_reward_reconciliation_hold())

    def test_reward_decrease_and_explicit_zero_reverse_only_original_credits(self):
        self.mine(1000)
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(400)),1)
        self.assertEqual(self.balance('alice')+self.balance('bob'),380)
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(0)),1)
        self.assertEqual(self.balance('alice')+self.balance('bob'),0)

    def test_debit_cannot_erase_paid_money_or_create_negative_unpaid(self):
        self.mine(1000)
        self.engine.record_payout('confirmed','alice',238)
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(0)),0)
        self.assertEqual(self.balance('bob'),712)
        self.assertEqual(self.engine.get_miner('alice').paid_satk,238)
        self.assertTrue(self.engine.has_reward_reconciliation_hold())
        with self.engine._get_connection() as c:
            self.assertEqual(c.execute("SELECT reward_satk FROM blocks WHERE block_hash='block'").fetchone()[0],1000)

    def test_legacy_block_without_original_window_is_held_without_guessing(self):
        self.mine(1000)
        with self.engine._get_connection() as c:
            c.execute('DELETE FROM pplns_block_allocations')
        self.assertTrue(self.engine.has_reward_reconciliation_hold())
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(2000)),0)
        self.assertEqual(self.balance('alice')+self.balance('bob'),950)

    def test_corrupt_original_credit_rolls_back_the_whole_reconciliation(self):
        self.mine(1000)
        with self.engine._get_connection() as c:
            c.execute("UPDATE pplns_block_allocations SET credits_json='{}'")
        with self.assertRaisesRegex(RuntimeError,'disagrees'):
            self.engine.reconcile_pplns_block_rewards(self.canonical(2000))
        self.assertEqual(self.balance('alice')+self.balance('bob'),950)

    def test_approved_fixed_legacy_credit_is_not_reinterpreted_on_reward_change(self):
        self.mine(1000)
        with self.engine._get_connection() as c:
            c.execute("UPDATE pplns_block_allocations SET allocation_kind='legacy_fixed',weights_json='{}'")
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(1000)),0)
        self.assertFalse(self.engine.has_reward_reconciliation_hold())
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(1100)),0)
        self.assertTrue(self.engine.has_reward_reconciliation_hold())
        self.assertEqual(self.balance('alice')+self.balance('bob'),950)

    def test_pending_payment_blocks_a_reward_debit(self):
        self.mine(1000)
        with self.engine._get_connection() as c:
            c.execute("INSERT INTO payout_intents VALUES('reserved','alice',100,0,'{}','prepared',0)")
        self.assertEqual(self.engine.reconcile_pplns_block_rewards(self.canonical(0)),0)
        self.assertTrue(self.engine.has_reward_reconciliation_hold())
        self.assertEqual(self.balance('alice')+self.balance('bob'),950)

    def test_malformed_canonical_reward_cannot_clear_a_balance(self):
        self.mine(1000)
        for block in [{'hash':'block'}, {'hash':'block','height':11,'miner_base_sats':1.5,'fees_sats':0}]:
            with self.assertRaises(ValueError):
                self.engine.reconcile_pplns_block_rewards([block])
            self.assertEqual(self.balance('alice')+self.balance('bob'),950)

    def test_allocator_conserves_small_rewards_and_tie_breaks_deterministically(self):
        for reward in range(51):
            a=allocate_reward(reward,{'b':'1','a':'1'},'b','.05','.005')
            b=allocate_reward(reward,{'a':'1','b':'1'},'b','.05','.005')
            self.assertEqual(a,b)
            self.assertEqual(a[0]+sum(a[2].values()),reward)
        for invalid in [-1,1.5,True]:
            with self.assertRaises(ValueError):allocate_reward(invalid,{},'',0,0)


class RewardPaymentHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_reconciliation_hold_prevents_all_scan_sign_and_broadcast(self):
        from tests.test_pplns_payment_safety import PaymentSafetyTests
        with tempfile.TemporaryDirectory() as directory:
            worker,miner,rpc=PaymentSafetyTests().setup_worker(directory,number=2)
            worker.engine.record_share(miner.address,'legacy',128)
            worker.engine.process_block('legacy',1,0,.05,miner.address,'legacy')
            with worker.engine._get_connection() as c:c.execute('DELETE FROM pplns_block_allocations')
            async def forbidden(*args):raise AssertionError('Payment hold reached RPC')
            rpc.call=forbidden
            self.assertEqual(await worker.run_payout_cycle(),[])
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk,0)

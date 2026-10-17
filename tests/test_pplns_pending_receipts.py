"""Pending confirmations are independent of UTC new-payment batches."""
import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import stratum.pplns as pool
import tests.test_pplns_payment_safety as payment_safety


class PendingReceiptTests(unittest.IsolatedAsyncioTestCase):
    def worker(self, root):
        return payment_safety.PaymentSafetyTests.setup_worker(self, root, number=2)

    async def test_pending_confirmation_settles_without_rescan_or_new_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            worker, miner, rpc = self.worker(root)
            rpc.confirmations = 0
            await worker.run_payout_cycle()
            tx = rpc.submitted[0]
            worker.sync_treasury_utxos = AsyncMock(side_effect=AssertionError('new scan'))
            rpc.confirmations = 6
            self.assertEqual(len(await worker.observe_pending_payments()), 1)
            self.assertEqual(await worker.observe_pending_payments(), [])
            self.assertEqual(rpc.submitted, [tx])
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk, 1_000_000_000)
            worker.sync_treasury_utxos.assert_not_called()

    async def test_receipt_locator_survives_restart_and_preserves_signed_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            worker, miner, rpc = self.worker(root)
            rpc.confirmations = 0
            await worker.run_payout_cycle()
            original = rpc.submitted[0]
            old_call = rpc.call
            calls = []
            async def canonical(method, params):
                if method == 'getTransaction':
                    calls.append(dict(params))
                    return {'txid': original['txid'], 'status': 'confirmed',
                            'confirmations': rpc.confirmations, 'height': 123,
                            'block_hash': 'ab' * 32}
                return await old_call(method, params)
            rpc.call = canonical
            rpc.confirmations = 1
            await worker.observe_pending_payments()
            intent = worker.engine.get_payout_intents()[0]
            locator = json.loads(intent['transaction_json'])['_confirmation_locator']
            self.assertEqual(locator, {'height': 123, 'block_hash': 'ab' * 32})
            restarted = pool.AutoPayoutWorker(worker.engine, worker.treasury_wallet, rpc)
            rpc.confirmations = 6
            await restarted.observe_pending_payments()
            self.assertEqual(calls[-1], {'txid': original['txid'], **locator})
            self.assertTrue(all(tx == original for tx in rpc.submitted))
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk, 1_000_000_000)

    async def test_reward_hold_observes_confirmations_but_never_rebroadcasts(self):
        with tempfile.TemporaryDirectory() as root:
            worker, miner, rpc = self.worker(root)
            rpc.confirmations = 0
            await worker.run_payout_cycle()
            with patch.object(worker.engine, 'has_reward_reconciliation_hold', return_value=True):
                self.assertEqual(await worker.observe_pending_payments(), [])
                self.assertEqual(len(rpc.submitted), 1)
                rpc.confirmations = 6
                self.assertEqual(len(await worker.observe_pending_payments()), 1)
            self.assertEqual(len(rpc.submitted), 1)

    async def test_reorg_rechecks_recent_window_without_new_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            worker, miner, rpc = self.worker(root)
            rpc.confirmations = 0
            await worker.run_payout_cycle()
            txid = rpc.submitted[0]['txid']
            worker.engine.remember_payout_locator(txid, 123, 'ab' * 32)
            calls = []
            async def reorg(method, params):
                self.assertEqual(method, 'getTransaction')
                calls.append(dict(params))
                if 'height' in params:
                    raise pool.RPCError('canonical transaction locator mismatch')
                return {'txid': txid, 'status': 'confirmed', 'confirmations': 6,
                        'height': 124, 'block_hash': 'cd' * 32}
            rpc.call = reorg
            self.assertEqual(await worker.observe_pending_payments(), [txid])
            self.assertEqual(len(rpc.submitted), 1)
            self.assertEqual(calls[-1], {'txid': txid})

    async def test_disabled_worker_cannot_poll_or_broadcast(self):
        with tempfile.TemporaryDirectory() as root:
            worker, miner, rpc = self.worker(root)
            with patch.dict('os.environ', {'BTCK_PPLNS_AUTOPAYOUT_ENABLED': '0'}):
                disabled = pool.AutoPayoutWorker(worker.engine, worker.treasury_wallet, rpc)
            with patch.object(worker.engine, 'get_payout_intents', side_effect=AssertionError('ledger access')):
                self.assertEqual(await disabled.observe_pending_payments(), [])
            self.assertEqual(rpc.submitted, [])

    async def test_pending_poll_wakes_within30_seconds_not_next_sixhour_batch(self):
        worker = pool.AutoPayoutWorker(None, None, None)
        worker.running = True
        worker.observe_pending_payments = AsyncMock()
        worker.run_payout_cycle = AsyncMock()
        worker.seconds_until_next_utc_batch = lambda: 21600
        now = [100.0]
        delays = []
        async def sleep(delay):
            delays.append(delay)
            now[0] += delay
            worker.running = False
        with patch.object(pool, 'time', SimpleNamespace(time=lambda: now[0])), \
             patch.object(pool, 'asyncio', SimpleNamespace(sleep=sleep)):
            await worker._loop()
        worker.observe_pending_payments.assert_awaited_once()
        worker.run_payout_cycle.assert_not_called()
        self.assertEqual(delays, [30.0])

    async def test_new_payment_cycle_runs_only_when_batch_becomes_due(self):
        worker = pool.AutoPayoutWorker(None, None, None)
        worker.running = True
        worker.observe_pending_payments = AsyncMock()
        async def batch():
            worker.running = False
        worker.run_payout_cycle = AsyncMock(side_effect=batch)
        worker.seconds_until_next_utc_batch = lambda: 5
        now = [100.0]
        async def sleep(delay):
            now[0] += delay
        with patch.object(pool, 'time', SimpleNamespace(time=lambda: now[0])), \
             patch.object(pool, 'asyncio', SimpleNamespace(sleep=sleep)):
            await worker._loop()
        worker.run_payout_cycle.assert_awaited_once()
        self.assertEqual(now[0], 105.0)


if __name__ == '__main__':
    unittest.main()

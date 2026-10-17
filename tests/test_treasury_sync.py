"""Encrypted checkpoint reuse, canonical spends, and bounded payment scans."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tools import btck_cli
from privacy.wallet_engine import StealthWallet, UTXO
from privacy.stealth_address import StealthKeyPair
from stratum.pplns import AutoPayoutWorker
from stratum.treasury_sync import CachedTreasurySync


class TreasurySyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.wallet = StealthWallet.generate('isolated-treasury')
        r, p = StealthKeyPair.generate_one_time_stealth_output(self.wallet.address)
        self.coin = UTXO('11' * 32, 0, 1_000_000, r.hex(), p.hex(), 1)
        self.wallet.utxos = [self.coin]
        self.wallet._source_path = self.root / 'wallet'
        self.wallet._unlock_password = 'isolated-cache-test-password'
        self.cache = self.root / 'wallet.sync.json'
        self.password = self.root / 'password'
        self.password.write_text(self.wallet._unlock_password)
        self.password.chmod(0o600)
        self.state = {'next_height': 101, 'anchor_hash': 'aa' * 32,
                      'network': dict(btck_cli.APPROVED_NETWORKS['mainnet'])}
        btck_cli._save_sync_cache(self.wallet, self.state)

    def tearDown(self):
        self.directory.cleanup()

    def page(self, *, done=True, spent=False):
        return {'network': self.state['network'], 'from_height': 101,
                'next_height': 102, 'anchor_hash': 'bb' * 32,
                'tip_height': 101 if done else 102, 'done': done,
                'blocks': [{'height': 101, 'hash': 'bb' * 32, 'created': [],
                            'spent': [{'txid': self.coin.txid, 'vout': 0}] if spent else []}]}

    def adapter(self, **kwargs):
        return CachedTreasurySync(self.cache, self.password, **kwargs)

    def test_payment_scan_reuses_checkpoint_and_applies_confirmed_spend_without_writing(self):
        original = self.cache.read_bytes()
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=self.page(spent=True)) as fetch:
            candidate, height = self.adapter()(self.wallet)
        self.assertEqual(fetch.call_args.args[1]['next_height'], 101)
        self.assertEqual(height, 101)
        self.assertEqual(candidate.utxos, [])
        self.assertEqual(self.wallet.utxos, [self.coin])
        self.assertEqual(self.cache.read_bytes(), original)

    def test_updater_persists_encrypted_cursor_and_spent_removal(self):
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=self.page(spent=True)):
            self.adapter(persist=True)(self.wallet)
        reopened = StealthWallet(self.wallet.keypair, 'reopened')
        reopened._source_path = self.wallet._source_path
        reopened._unlock_password = self.wallet._unlock_password
        self.assertEqual(btck_cli._load_sync_cache(reopened)['next_height'], 102)
        self.assertEqual(reopened.utxos, [])
        self.assertNotIn(self.wallet._unlock_password.encode(), self.cache.read_bytes())

    def test_page_budget_cannot_publish_partial_funds(self):
        before = self.cache.read_bytes()
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=self.page(done=False)) as fetch:
            with self.assertRaisesRegex(btck_cli.CLIError, 'incomplete'):
                self.adapter(max_pages=1)(self.wallet)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.wallet.utxos, [self.coin])

    def test_stale_checkpoint_without_undo_never_restarts_from_genesis(self):
        with patch.object(btck_cli, '_fetch_sync_page_remote', side_effect=btck_cli.CLIError('stale cursor')) as fetch:
            with self.assertRaisesRegex(btck_cli.CLIError, 'full rescan is disabled'):
                self.adapter()(self.wallet)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1]['next_height'], 101)

    def test_bounded_updater_keeps_progress_but_cannot_authorize_payment(self):
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=self.page(done=False)):
            with self.assertRaisesRegex(btck_cli.CLIError, 'incomplete'):
                self.adapter(max_pages=1, persist=True)(self.wallet)
        state = btck_cli._load_sync_cache(self.wallet)
        self.assertEqual(state['next_height'], 102)

    def test_false_completion_cannot_publish_an_old_balance_as_current(self):
        page = self.page()
        page['tip_height'] = 1000
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=page):
            with self.assertRaisesRegex(ValueError, 'canonical tip'):
                self.adapter()(self.wallet)

    def test_missing_checkpoint_never_fetches_history(self):
        self.cache.unlink()
        with patch.object(btck_cli, '_fetch_sync_page_remote') as fetch:
            with self.assertRaises(FileNotFoundError):
                self.adapter()(self.wallet)
        fetch.assert_not_called()

    def test_wrong_wallet_cache_is_rejected_before_network_access(self):
        other = StealthWallet.generate('unrelated')
        with patch.object(btck_cli, '_fetch_sync_page_remote') as fetch:
            with self.assertRaisesRegex(btck_cli.CLIError, 'different wallet'):
                self.adapter()(other)
        fetch.assert_not_called()

    def test_wrong_password_does_not_reset_or_replace_checkpoint(self):
        before = self.cache.read_bytes()
        self.password.write_text('wrong-isolated-password')
        with patch.object(btck_cli, '_fetch_sync_page_remote') as fetch:
            with self.assertRaises(btck_cli.CLIError):
                self.adapter()(self.wallet)
        self.assertEqual(self.cache.read_bytes(), before)
        fetch.assert_not_called()

    def test_worker_uses_incremental_provider_and_never_requests_genesis_rpc(self):
        rpc = AsyncMock()
        worker = AutoPayoutWorker(None, self.wallet, rpc, treasury_sync=self.adapter())
        with patch.object(btck_cli, '_fetch_sync_page_remote', return_value=self.page()):
            self.assertEqual(asyncio.run(worker.sync_treasury_utxos()), 1_000_000)
        rpc.call.assert_not_called()
        self.assertEqual(worker.synced_height, 101)
        with patch.object(btck_cli, '_fetch_sync_page_remote', side_effect=TimeoutError('isolated outage')):
            self.assertIsNone(asyncio.run(worker.sync_treasury_utxos()))
        self.assertEqual(worker.treasury_wallet.utxos, [self.coin])
        self.assertIsNone(worker.synced_height)


if __name__ == '__main__':
    unittest.main()

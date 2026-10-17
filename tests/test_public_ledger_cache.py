import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import btck_cli
from btck_wallet_gui import WalletApp
from public_ledger_cache import PublicLedgerCache

NETWORK=dict(btck_cli.APPROVED_NETWORKS['mainnet'])


def page():
    return dict(format='btck-wallet-sync-v1',network=NETWORK,from_height=0,next_height=1,
                anchor_hash='ab'*32,tip_height=0,done=True,
                blocks=[dict(height=0,hash='ab'*32,created=[],spent=[])])


class SharedLedgerTests(unittest.TestCase):
    def test_second_wallet_reuses_public_page_without_redownload(self):
        with tempfile.TemporaryDirectory() as root:
            tip=dict(network=NETWORK,tip_height=0,tip_hash='ab'*32)
            args=SimpleNamespace(public_ledger_cache=PublicLedgerCache(Path(root)/'cache.db','https://wallet.example',NETWORK),
                                 verified_sync_tip=tip,sync_page_size=1000)
            state=dict(next_height=0,anchor_hash='')
            with patch.object(btck_cli,'_fetch_sync_page_remote',return_value=page()) as remote:
                btck_cli._fetch_sync_page(args,state)
                second=btck_cli._fetch_sync_page(args,state)
            self.assertEqual(remote.call_count,1)
            self.assertEqual(second['source'],'shared-local-ledger')

    def test_old_terminal_metadata_does_not_hide_new_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            cache=PublicLedgerCache(Path(root)/'cache.db','https://wallet.example',NETWORK)
            cache.put(page(),1000,'')
            result=cache.get(0,1000,'',dict(network=NETWORK,tip_height=100,tip_hash='cd'*32))
            self.assertFalse(result['done'])
            self.assertEqual(result['tip_height'],100)
            self.assertIsNone(cache.get(0,1000,'',dict(network=NETWORK,tip_height=0,tip_hash='cd'*32)))

    def test_no_private_or_ownership_fields_are_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'cache.db'
            cache=PublicLedgerCache(path,'https://wallet.example',NETWORK)
            payload=page();payload['view_priv']='secret-value';payload['blocks'][0]['wallet_balance']=123
            cache.put(payload,1000,'')
            data=path.read_bytes()
            for prohibited in (b'view_priv',b'secret-value',b'wallet_balance'):
                self.assertNotIn(prohibited,data)

    def test_source_network_and_anchor_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'cache.db'
            first=PublicLedgerCache(path,'https://one.example',NETWORK)
            first.put(page(),1000,'')
            second=PublicLedgerCache(path,'https://two.example',NETWORK)
            self.assertIsNone(second.get(0,1000,'',dict(network=NETWORK,tip_height=0,tip_hash='ab'*32)))
            self.assertIsNone(first.get(0,1000,'cd'*32,dict(network=NETWORK,tip_height=0,tip_hash='ab'*32)))
            with self.assertRaises(ValueError):
                first.get(0,1000,'',dict(network={'name':'wrong'},tip_height=0,tip_hash='ab'*32))
            first.clear()
            self.assertIsNone(first.get(0,1000,'',dict(network=NETWORK,tip_height=0,tip_hash='ab'*32)))

    def test_broken_public_cache_falls_back_to_network(self):
        broken=Mock();broken.get.side_effect=ValueError('corrupt disposable cache')
        args=SimpleNamespace(public_ledger_cache=broken,verified_sync_tip={},sync_page_size=1000)
        with patch.object(btck_cli,'_fetch_sync_page_remote',return_value=page()) as remote:
            self.assertEqual(btck_cli._fetch_sync_page(args,dict(next_height=0,anchor_hash='')),page())
        remote.assert_called_once()
        self.assertIsNone(args.public_ledger_cache)


class LiveWalletCursorTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(_closing=False,_password_prompt_active=False,_wallet_generation=1,wallet=object(),height=10,
            cache_height=20,ledger_tip_hash='ab'*32,_tip_check_in_progress=True,_auto_sync_failures=0,
            sync_wallet=Mock(),_schedule_auto_sync=Mock(),_refresh_balance=Mock(),
            _set_busy=Mock(),_set_sync_progress=Mock(),height_label=Mock(),status_var=Mock(),
            _log=Mock(),CYAN='cyan',GOLD='gold')

    def test_shared_or_partial_cache_cannot_hide_private_scan_lag(self):
        app=self.app()
        WalletApp._auto_tip_finished(app,1,dict(tip_height=20,tip_hash='cd'*32,network=NETWORK))
        app.sync_wallet.assert_called_once()

    def test_same_height_reorg_triggers_sync(self):
        app=self.app()
        WalletApp._auto_tip_finished(app,1,dict(tip_height=10,tip_hash='cd'*32,network=NETWORK))
        app.sync_wallet.assert_called_once()

    def test_unverified_static_completion_is_not_live_or_sendable(self):
        app=self.app()
        WalletApp._sync_finished(app,dict(selected_height=10,selected_hash='ab'*32,
            network_tip_height=20,live_verified=False),NETWORK,1,app.wallet)
        self.assertEqual(app.height,-1)
        self.assertEqual(app.cache_height,10)
        self.assertEqual(app._set_sync_progress.call_args.args[1],'CATCHING UP')

    def test_verified_completion_is_live(self):
        app=self.app()
        WalletApp._sync_finished(app,dict(selected_height=10,selected_hash='ab'*32,
            network_tip_height=10,live_verified=True),NETWORK,1,app.wallet)
        self.assertEqual(app.height,10)
        self.assertEqual(app._set_sync_progress.call_args.args[1],'LIVE')

    def test_old_wallet_callback_cannot_update_new_wallet(self):
        app=self.app()
        WalletApp._sync_finished(app,dict(selected_height=999),NETWORK,0,app.wallet)
        app._refresh_balance.assert_not_called()

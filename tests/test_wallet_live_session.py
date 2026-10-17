from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
import btck_cli
from privacy.wallet_engine import StealthWallet
from privacy.stealth_address import StealthKeyPair

NETWORK=dict(btck_cli.APPROVED_NETWORKS['mainnet'])

class WalletLiveSessionTests(unittest.TestCase):
    def page(self,state,created=(),spent=()):
        height=state['next_height'];h=f'{height+1:064x}'
        return {'network':NETWORK,'from_height':height,'next_height':height+1,
                'anchor_hash':h,'tip_height':height,'done':True,
                'blocks':[{'height':height,'hash':h,'created':list(created),'spent':list(spent)}]}

    def test_cursor_only_refresh_reuses_memory_then_checkpoints_periodically(self):
        with tempfile.TemporaryDirectory() as root:
            wallet=StealthWallet.generate('disposable')
            wallet._source_path=Path(root)/'wallet.json';wallet._unlock_password='disposable-test-password'
            state={'next_height':100,'anchor_hash':'ab'*32,'network':NETWORK}
            btck_cli._save_sync_cache(wallet,state)
            args=SimpleNamespace(sync_workers=1,live_session_cache=True)
            load=btck_cli._load_sync_cache;save=btck_cli._save_sync_cache
            with patch.object(btck_cli,'_load_sync_cache',wraps=load) as loads,\
                 patch.object(btck_cli,'_save_sync_cache',wraps=save) as saves,\
                 patch.object(btck_cli,'_fetch_sync_page',side_effect=lambda a,s:self.page(s)),\
                 patch.object(btck_cli.time,'monotonic',return_value=100):
                btck_cli.scan_wallet(args,wallet);btck_cli.scan_wallet(args,wallet)
                self.assertEqual(loads.call_count,1);self.assertEqual(saves.call_count,0)
                self.assertEqual(wallet._live_sync_state['next_height'],102)
                self.assertEqual(load(wallet)['next_height'],100)
                with patch.object(btck_cli.time,'monotonic',return_value=131):
                    btck_cli.scan_wallet(args,wallet)
                self.assertEqual(saves.call_count,1)
                self.assertEqual(load(wallet)['next_height'],103)
            # Different wallet object/session always loads its own encrypted
            # state; no cursor/balance may come from the prior wallet object.
            other=StealthWallet.generate('independent')
            with patch.object(btck_cli,'_fetch_sync_page',side_effect=lambda a,s:self.page(s)):
                snapshot,_=btck_cli.scan_wallet(args,other)
            self.assertEqual(snapshot['selected_height'],0)
            self.assertEqual(other.get_balance_satk(),0)

    def test_owned_credit_spend_and_failed_save_are_not_deferred(self):
        wallet=StealthWallet.generate('disposable')
        args=SimpleNamespace(sync_workers=1,live_session_cache=True)
        wallet._live_sync_state={'next_height':100,'anchor_hash':'ab'*32,'network':NETWORK}
        wallet._live_sync_saved_at=100
        r,p=StealthKeyPair.generate_one_time_stealth_output(wallet.address)
        output={'txid':'cd'*32,'vout':0,'amount_satk':123456789,'R_ephem_hex':r.hex(),
                'P_onetime_hex':p.hex(),'height':100,'is_coinbase':False}
        pages=lambda a,s:self.page(s,created=[output])
        with patch.object(btck_cli,'_fetch_sync_page',side_effect=pages),\
             patch.object(btck_cli.time,'monotonic',return_value=101),\
             patch.object(btck_cli,'_save_sync_cache',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):btck_cli.scan_wallet(args,wallet)
        self.assertEqual(wallet.get_balance_satk(),123456789)
        self.assertEqual(wallet._live_sync_state['next_height'],101)
        self.assertTrue(wallet._live_sync_dirty)
        with patch.object(btck_cli,'_fetch_sync_page',side_effect=lambda a,s:self.page(s)),\
             patch.object(btck_cli.time,'monotonic',return_value=102),\
             patch.object(btck_cli,'_save_sync_cache') as save:
            btck_cli.scan_wallet(args,wallet);save.assert_called_once()
        self.assertFalse(wallet._live_sync_dirty)
        with patch.object(btck_cli,'_fetch_sync_page',side_effect=lambda a,s:self.page(s,spent=[{'txid':'cd'*32,'vout':0}])),\
             patch.object(btck_cli.time,'monotonic',return_value=103),\
             patch.object(btck_cli,'_save_sync_cache') as save:
            btck_cli.scan_wallet(args,wallet);save.assert_called_once()
        self.assertEqual(wallet.get_balance_satk(),0)

    def test_live_reorg_immediately_saves_rewound_state(self):
        wallet=StealthWallet.generate('disposable');args=SimpleNamespace(sync_workers=1,live_session_cache=True)
        wallet._live_sync_state={'next_height':2,'anchor_hash':'bb'*32,'network':NETWORK,
            '_undo':[{'height':1,'hash':'bb'*32,'previous_hash':'aa'*32,'created':[],'spent':[]}]}
        wallet._live_sync_saved_at=100
        requests=[]
        def fetch(a,s):
            requests.append(s['next_height'])
            if len(requests)==1:raise btck_cli.CLIError('cursor stale after canonical reorganization')
            return self.page(s)
        with patch.object(btck_cli,'_fetch_sync_page',side_effect=fetch),\
             patch.object(btck_cli.time,'monotonic',return_value=101),\
             patch.object(btck_cli,'_save_sync_cache') as save:
            btck_cli.scan_wallet(args,wallet)
        self.assertEqual(requests,[2,1]);save.assert_called_once()
        self.assertEqual(save.call_args.args[1]['next_height'],1)

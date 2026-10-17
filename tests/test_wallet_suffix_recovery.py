import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
import btck_cli
from privacy.wallet_engine import StealthWallet,UTXO
from public_ledger_cache import PublicLedgerCache

NETWORK=dict(btck_cli.APPROVED_NETWORKS['mainnet'])

def utxo(txid,amount):
    return UTXO(txid=txid,vout=0,amount_satk=amount,R_ephem_hex='02'+'11'*32,
                P_onetime_hex='03'+'22'*32,block_height=90)

class WalletSuffixRecoveryTests(unittest.TestCase):
    def state(self,spent):
        return {'next_height':102,'anchor_hash':'bb'*32,'network':NETWORK,'_undo':[
            {'height':100,'hash':'aa'*32,'previous_hash':'99'*32,'created':[['new',0]],'spent':[]},
            {'height':101,'hash':'bb'*32,'previous_hash':'aa'*32,'created':[],
             'spent':[vars(spent)]}]}

    def test_rollback_removes_orphan_credit_and_restores_spend(self):
        spent=utxo('old',10); current={('new',0):utxo('new',20)}
        state=btck_cli._rewind_wallet_suffix(self.state(spent),current)
        self.assertEqual(state['next_height'],100)
        self.assertEqual(state['anchor_hash'],'99'*32)
        self.assertEqual(set(current),{('old',0)})
        self.assertEqual(current[('old',0)].amount_satk,10)

    def test_discontinuous_undo_never_mutates_balance(self):
        current={('new',0):utxo('new',20)};state=self.state(utxo('old',10))
        state['_undo'][0]['hash']='wrong'
        with self.assertRaises(btck_cli.CLIError):
            btck_cli._rewind_wallet_suffix(state,current)
        self.assertEqual(set(current),{('new',0)})

    def test_real_encrypted_undo_survives_reopen_without_plaintext(self):
        with tempfile.TemporaryDirectory() as root:
            wallet=StealthWallet.generate('disposable')
            wallet._source_path=Path(root)/'wallet.json';wallet._unlock_password='disposable-test-password'
            state=self.state(utxo('private-owned-spent-output',10))
            btck_cli._save_sync_cache(wallet,state)
            loaded=btck_cli._load_sync_cache(wallet)
            self.assertEqual(loaded['_undo'],state['_undo'])
            self.assertNotIn('private-owned-spent-output',Path(str(wallet._source_path)+'.sync.json').read_text())

    def test_scan_recovers_suffix_not_genesis_and_preserves_balance(self):
        wallet=StealthWallet.generate('disposable');wallet.utxos=[utxo('new',20)]
        state=self.state(utxo('old',10));requests=[]
        def fetch(args,cursor):
            requests.append(cursor['next_height'])
            if len(requests)==1:
                raise btck_cli.CLIError('cursor stale after canonical reorganization')
            return {'network':NETWORK,'from_height':100,'next_height':101,'anchor_hash':'cc'*32,
                    'tip_height':100,'done':True,'blocks':[{'height':100,'hash':'cc'*32,'created':[],'spent':[]}]}
        with patch.object(btck_cli,'_load_sync_cache',return_value=state),\
             patch.object(btck_cli,'_save_sync_cache'),patch.object(btck_cli,'_fetch_sync_page',side_effect=fetch):
            snapshot,_=btck_cli.scan_wallet(SimpleNamespace(sync_workers=1),wallet)
        self.assertEqual(requests,[102,100])
        self.assertEqual(snapshot['selected_height'],100)
        self.assertEqual([(u.txid,u.amount_satk) for u in wallet.utxos],[('old',10)])

    def test_shared_prefix_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            cache=PublicLedgerCache(Path(root)/'public.sqlite3','https://example.test',NETWORK)
            def page(start,hash_value):
                return {'network':NETWORK,'from_height':start,'next_height':start+1,'anchor_hash':hash_value,
                        'blocks':[{'height':start,'hash':hash_value,'spent':[],'created':[]}]}
            cache.put(page(0,'aa'*32),1,'')
            cache.put(page(100,'bb'*32),1,'old-anchor')
            cache.discard_from(100)
            tip={'network':NETWORK,'tip_height':200,'tip_hash':'cc'*32}
            self.assertIsNotNone(cache.get(0,1,'',tip))
            self.assertIsNone(cache.get(100,1,'old-anchor',tip))

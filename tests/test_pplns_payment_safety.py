"""Disposable wallets/ledgers only. No production access or broadcasts."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet,UTXO,transaction_from_dict
from stratum.pplns import PPLNSEngine,AutoPayoutWorker
from tokenomics.emission_schedule import SATK_PER_COIN

class RPC:
    def __init__(self,outputs):
        self.outputs=outputs;self.submitted=[];self.confirmations=6
        self.timeout_on_submit=False;self.fail_second_page=False
    async def call(self,method,params):
        if method=='getWalletSyncPage':
            if params['from_height']>0:raise TimeoutError('synthetic page failure')
            return {'from_height':0,'next_height':1,'anchor_hash':'11'*32,
                'network':{'name':'mainnet'},'tip_height':0,'done':not self.fail_second_page,
                'unspent_only':True,'blocks':[{'height':0,'hash':'11'*32,'created':self.outputs,'spent':[]}]}
        if method=='submitTransaction':
            self.submitted.append(params['transaction'])
            if self.timeout_on_submit:raise TimeoutError('synthetic lost reply AFTER acceptance')
            return {'accepted':True,'txid':params['transaction']['txid']}
        if method=='getTransaction':
            return {'txid':params['txid'],'status':'confirmed' if self.confirmations else 'mempool',
                    'confirmations':self.confirmations}
        raise AssertionError(method)

class PaymentSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setup_worker(self,root,number=1,liability=10):
        treasury=StealthWallet.generate('disposable-treasury');miner=StealthWallet.generate('disposable-miner')
        r,p=StealthKeyPair.generate_one_time_stealth_output(treasury.address)
        outputs=[{'txid':f'{i+1:064x}','vout':0,'amount_satk':10*SATK_PER_COIN,
                  'R_ephem_hex':r.hex(),'P_onetime_hex':p.hex(),'height':0} for i in range(number)]
        engine=PPLNSEngine(Path(root)/'accounts.sqlite3')
        with engine._get_connection() as conn:
            conn.execute('INSERT INTO miners(wallet_address,unpaid_satk) VALUES(?,?)',
                         (miner.address,liability*SATK_PER_COIN))
        rpc=RPC(outputs);worker=AutoPayoutWorker(engine,treasury,rpc)
        return worker,miner,rpc

    async def test_partial_scan_keeps_previous_wallet_and_never_pays(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root)
            prior=UTXO(txid='old',vout=0,amount_satk=123,R_ephem_hex='',P_onetime_hex='',block_height=0)
            worker.treasury_wallet.utxos=[prior];rpc.fail_second_page=True
            self.assertEqual(await worker.run_payout_cycle(),[])
            self.assertEqual(worker.treasury_wallet.utxos,[prior])
            self.assertIsNone(worker.synced_height)
            self.assertEqual(rpc.submitted,[])
            self.assertEqual(worker.engine.get_miner(miner.address).unpaid_satk,10*SATK_PER_COIN)

    async def test_large_liability_uses_at_most100_inputs_and_keeps_remainder(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root,number=128,liability=1200)
            result=await worker.run_payout_cycle()
            self.assertEqual(len(result),1)
            tx=transaction_from_dict(rpc.submitted[0])
            self.assertEqual(len(tx.inputs),100)
            self.assertTrue(StealthWallet.verify_transaction(tx))
            self.assertEqual(worker.engine.get_miner(miner.address).unpaid_satk,200*SATK_PER_COIN+10000)

    async def test_ambiguous_broadcast_survives_restart_without_a_new_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root,number=2)
            rpc.confirmations=0;rpc.timeout_on_submit=True
            self.assertEqual(await worker.run_payout_cycle(),[])
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk,0)
            intent=worker.engine.get_payout_intents()[0]
            self.assertEqual(json.loads(intent['transaction_json']),rpc.submitted[0])
            rpc.timeout_on_submit=False
            restarted=AutoPayoutWorker(PPLNSEngine(Path(root)/'accounts.sqlite3'),worker.treasury_wallet,rpc)
            self.assertEqual(await restarted.run_payout_cycle(),[])
            self.assertEqual(len(rpc.submitted),2)
            self.assertEqual(rpc.submitted[0],rpc.submitted[1])
            self.assertEqual(restarted.engine.get_miner(miner.address).unpaid_satk,10*SATK_PER_COIN)
            rpc.confirmations=6
            self.assertEqual(len(await restarted.run_payout_cycle()),1)
            self.assertEqual(await restarted.run_payout_cycle(),[])
            self.assertEqual(restarted.engine.get_miner(miner.address).paid_satk,10*SATK_PER_COIN)
            self.assertEqual(len(rpc.submitted),2)

    async def test_immature_coinbase_is_not_spent(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root,number=2)
            for output in rpc.outputs:output['is_coinbase']=True
            self.assertEqual(await worker.run_payout_cycle(),[])
            self.assertEqual(rpc.submitted,[])

    async def test_recording_same_transaction_is_idempotent_and_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root,number=2)
            result=await worker.run_payout_cycle();txid=result[0]['txid']
            worker.engine.record_payout(txid,miner.address,10*SATK_PER_COIN)
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk,10*SATK_PER_COIN)
            with self.assertRaises(ValueError):
                worker.engine.record_payout(txid,miner.address,10*SATK_PER_COIN-1)

    async def test_disabled_payment_mode_never_scans_signs_or_broadcasts(self):
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root)
            with patch.dict('os.environ',{'BTCK_PPLNS_AUTOPAYOUT_ENABLED':'0'}):
                disabled=AutoPayoutWorker(worker.engine,worker.treasury_wallet,rpc)
            disabled.start()
            self.assertFalse(disabled.running)
            self.assertEqual(await disabled.run_payout_cycle(),[])
            self.assertEqual(rpc.submitted,[])

    async def test_real_simnet_accepts_bounded_payment_without_marking_mempool_as_paid(self):
        from node.daemon import BTCKDaemon
        from node.rpc_client import InProcessNodeRPCClient
        from node.utxo_set import UTXOEntry
        with tempfile.TemporaryDirectory() as root:
            worker,miner,rpc=self.setup_worker(root,number=2)
            # Fund a disposable in-process simnet state, never a production node.
            daemon=BTCKDaemon(host='127.0.0.1',network='simnet')
            genesis=daemon.dag.genesis_hash
            for item in rpc.outputs:
                point=(item['txid'],item['vout'])
                daemon.dag.utxo_set.utxos[point]=UTXOEntry(amount_satk=item['amount_satk'],
                    R_ephem_hex=item['R_ephem_hex'],P_onetime_hex=item['P_onetime_hex'],block_height=0)
                daemon.dag.utxo_set._block_created_undo.setdefault(genesis,[]).append(point)
            daemon.dag.issued_supply_sats=daemon.dag.utxo_set.total_value_sats()
            worker.node_rpc=InProcessNodeRPCClient(daemon)
            self.assertEqual(await worker.run_payout_cycle(),[])
            self.assertEqual(len(daemon.mempool),1)
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk,0)
            self.assertEqual(worker.engine.get_miner(miner.address).unpaid_satk,10*SATK_PER_COIN)
            self.assertEqual(len(worker.engine.get_payout_intents()),1)
            from consensus.kheavyhash import hash_to_target_int
            from consensus.pow import compact_to_target
            from node.blockdag import DAGBlock
            recipient=StealthWallet.generate('disposable-block-miner')
            for _ in range(6):
                template=await worker.node_rpc.call('getBlockTemplate',{})
                coinbase=StealthKeyPair.generate_one_time_stealth_output(recipient.address)
                transactions=[transaction_from_dict(t) for t in template['transactions']]
                for nonce in range(10000):
                    block=DAGBlock(template['parents'],template['timestamp'],template['target_bits'],nonce,
                        coinbase,transactions=transactions,version=template['version'],chain_id=template['chain_id'])
                    if hash_to_target_int(bytes.fromhex(block.hash))<=compact_to_target(block.bits):break
                else:self.fail('simnet PoW fixture failed')
                accepted=await worker.node_rpc.call('submitBlock',{'parents':template['parents'],
                    'timestamp':template['timestamp'],'bits':template['target_bits'],'nonce':nonce,
                    'version':template['version'],'chain_id':template['chain_id'],
                    'coinbase_output':[p.hex() for p in coinbase],'transactions':template['transactions']})
                self.assertTrue(accepted['accepted'])
            self.assertEqual(len(await worker.run_payout_cycle()),1)
            self.assertEqual(worker.engine.get_miner(miner.address).unpaid_satk,0)
            self.assertEqual(worker.engine.get_miner(miner.address).paid_satk,10*SATK_PER_COIN)
            self.assertEqual(worker.engine.get_payout_intents(),[])

    async def test_two_worker_instances_cannot_broadcast_conflicting_new_payments(self):
        with tempfile.TemporaryDirectory() as root:
            one,miner,rpc=self.setup_worker(root,number=2)
            rpc.confirmations=0
            two=AutoPayoutWorker(PPLNSEngine(Path(root)/'accounts.sqlite3'),
                                StealthWallet(one.treasury_wallet.keypair,'same-disposable-key'),rpc)
            await asyncio.gather(one.run_payout_cycle(),two.run_payout_cycle())
            self.assertEqual(len(rpc.submitted),1)
            self.assertEqual(len(one.engine.get_payout_intents()),1)

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.kheavyhash import hash_to_target_int
from consensus.pow import compact_to_target
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient, RPCError
from node.utxo_set import UTXOEntry
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet, transaction_from_dict, transaction_to_dict


class TestNodeTransactionRPC(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.daemon = BTCKDaemon(host="127.0.0.1", network="simnet")
        self.rpc = InProcessNodeRPCClient(self.daemon)
        self.sender = StealthWallet.generate("sender")
        r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(self.sender.address)
        self.daemon.dag.utxo_set.utxos[("11" * 32, 0)] = UTXOEntry(
            amount_satk=1_000_000,
            P_onetime_hex=p_onetime.hex(),
            R_ephem_hex=r_ephem.hex(),
            block_height=0,
            kind="standard",
        )
        self.daemon.dag.issued_supply_sats = 1_000_000

    async def test_submit_template_and_confirmation_status(self):
        snapshot = await self.rpc.call("getUTXOSet")
        self.sender.scan_chain_outputs(snapshot["outputs"])
        recipient = StealthWallet.generate("recipient")
        tx = self.sender.create_and_sign_transfer(recipient.address, 400_000, 10_000)
        submitted = await self.rpc.call("submitTransaction", {"transaction": transaction_to_dict(tx)})
        self.assertEqual(submitted["status"], "mempool")
        self.assertEqual((await self.rpc.call("getTransaction", {"txid": tx.txid}))["status"], "mempool")

        template = await self.rpc.call("getBlockTemplate")
        self.assertEqual(len(template["transactions"]), 1)
        transactions = [transaction_from_dict(raw) for raw in template["transactions"]]
        coinbase = StealthKeyPair.generate_one_time_stealth_output(recipient.address)
        from node.blockdag import DAGBlock
        nonce = 0
        while True:
            block = DAGBlock(
                template["parents"], template["timestamp"], template["target_bits"], nonce,
                coinbase, transactions=transactions, version=template["version"], chain_id=template["chain_id"],
            )
            if hash_to_target_int(bytes.fromhex(block.hash)) <= compact_to_target(block.bits):
                break
            nonce += 1
        result = await self.rpc.call("submitBlock", {
            "parents": template["parents"], "timestamp": template["timestamp"],
            "bits": template["target_bits"], "nonce": nonce,
            "version": template["version"], "chain_id": template["chain_id"],
            "coinbase_output": [coinbase[0].hex(), coinbase[1].hex()],
            "transactions": template["transactions"],
        })
        self.assertTrue(result["accepted"])
        status = await self.rpc.call("getTransaction", {"txid": tx.txid})
        self.assertEqual(status["status"], "confirmed")
        self.assertEqual(status["height"], 1)
        self.assertEqual(status["confirmations"], 1)
        self.assertNotIn(tx.txid, self.daemon.mempool)

    async def test_conflicting_mempool_spend_is_rejected(self):
        snapshot = await self.rpc.call("getUTXOSet")
        self.sender.scan_chain_outputs(snapshot["outputs"])
        recipient = StealthWallet.generate("recipient")
        tx = self.sender.create_and_sign_transfer(recipient.address, 400_000, 10_000)
        await self.rpc.call("submitTransaction", {"transaction": transaction_to_dict(tx)})
        second_wallet = StealthWallet(self.sender.keypair, "same-keys")
        second_wallet.scan_chain_outputs(snapshot["outputs"])
        conflict = second_wallet.create_and_sign_transfer(recipient.address, 300_000, 10_000)
        with self.assertRaises(RPCError):
            await self.rpc.call("submitTransaction", {"transaction": transaction_to_dict(conflict)})

    async def test_submit_dedup_uses_consensus_receipts_without_block_reads(self):
        from types import SimpleNamespace
        class NoBlockReads(dict):
            def __getitem__(self, key):
                raise AssertionError('submit duplicate lookup loaded a block body')
        genesis = self.daemon.dag.genesis_hash
        self.daemon.dag.blocks = NoBlockReads(self.daemon.dag.blocks)
        self.daemon.dag.utxo_set._block_accepted_txids[genesis] = ['77' * 32]
        result = self.daemon.submit_transaction(
            SimpleNamespace(txid='77' * 32, inputs=[None], outputs=[None]))
        self.assertEqual(result['status'], 'confirmed')

    async def test_transaction_lookup_never_loads_lifetime_block_bodies(self):
        class NoBlockReads(dict):
            def __getitem__(self, key):
                raise AssertionError('transaction lookup loaded unrelated block body')
        genesis = self.daemon.dag.genesis_hash
        self.daemon.dag.virtual_blue_order = ['old-history'] * 560000 + [genesis] * 2048
        self.daemon.dag.blocks = NoBlockReads(self.daemon.dag.blocks)
        calls = []
        def accepted(block_hash):
            self.assertEqual(block_hash, genesis)
            calls.append(block_hash)
            return []
        self.daemon.dag.utxo_set.get_block_accepted_txids = accepted
        result = await self.rpc.call('getTransaction', {'txid': '33' * 32})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['lookup_scope'], 'recent_2048')
        self.assertEqual(len(calls), 2048)

    async def test_historical_transaction_locator_checks_only_one_canonical_block(self):
        genesis = self.daemon.dag.genesis_hash
        txid = '44' * 32
        self.daemon.dag.virtual_blue_order = [genesis] + ['later-history'] * 560000
        self.daemon.dag.utxo_set._block_accepted_txids[genesis] = [txid]
        result = await self.rpc.call('getTransaction', {
            'txid': txid, 'height': 0, 'block_hash': genesis})
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['confirmations'], 560001)
        with self.assertRaises(RPCError):
            await self.rpc.call('getTransaction', {'txid': txid, 'height': 1, 'block_hash': genesis})

    async def test_transaction_presence_without_consensus_acceptance_is_not_confirmation(self):
        from types import SimpleNamespace
        genesis = self.daemon.dag.genesis_hash
        txid = '55' * 32
        self.daemon.dag.blocks[genesis].transactions = [SimpleNamespace(txid=txid)]
        result = await self.rpc.call('getTransaction', {'txid': txid})
        self.assertEqual(result['status'], 'unknown')

    async def test_mempool_maturity_uses_virtual_blue_height(self):
        entry = self.daemon.dag.utxo_set.utxos[("11" * 32, 0)]
        entry.is_coinbase = True
        entry.block_height = 100
        genesis = self.daemon.dag.genesis_hash
        self.daemon.dag.virtual_selected_parent_chain = [genesis]
        self.daemon.dag.virtual_blue_order = [genesis] * 7_301
        snapshot = await self.rpc.call("getUTXOSet")
        self.sender.scan_chain_outputs(snapshot["outputs"])
        recipient = StealthWallet.generate("blue-height-recipient")
        tx = self.sender.create_and_sign_transfer(recipient.address, 400_000, 10_000)
        result = await self.rpc.call("submitTransaction", {"transaction": transaction_to_dict(tx)})
        self.assertEqual(result["status"], "mempool")

    async def test_wallet_page_does_not_walk_lifetime_spent_values_when_outputs_are_live(self):
        class GuardedUndo(dict):
            def values(self):
                raise AssertionError("wallet page performed a lifetime undo scan")

        genesis = self.daemon.dag.genesis_hash
        outpoint = ("22" * 32, 0)
        entry = UTXOEntry(
            amount_satk=123,
            P_onetime_hex="03" + "22" * 32,
            R_ephem_hex="02" + "11" * 32,
            block_height=0,
        )
        self.daemon.dag.utxo_set.utxos[outpoint] = entry
        self.daemon.dag.utxo_set._block_created_undo.setdefault(genesis, []).append(outpoint)
        self.daemon.dag.utxo_set._block_spent_undo = GuardedUndo(
            self.daemon.dag.utxo_set._block_spent_undo
        )

        page = self.daemon.get_wallet_sync_page({
            "from_height": 0, "limit": 1, "anchor_hash": "",
        })
        self.assertTrue(any(item["txid"] == outpoint[0] for item in page["blocks"][0]["created"]))


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

"""Withdrawal and on-chain burn bridge regression tests."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../sdk")))

from gateway.ai_rpc_gateway import AgentLedger
from gateway.l2_chain_bridge import L2ChainBridge
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet
from tokenomics.provable_burn import is_provable_burn_output


class FakeBridgeNodeRPC:
    def __init__(self, outputs):
        self.outputs = outputs
        self.transactions = {}
        self.submit_count = 0
        self.fail_methods = set()
        self.chain_id = "btck-mainnet-v1"
        self.virtual_blue_order_length = 101

    async def call(self, method, params=None):
        if method in self.fail_methods:
            raise TimeoutError(f"injected {method} timeout")
        if method == "getInfo":
            return {
                "network": {"chain_id": self.chain_id},
                "dag": {"virtual_blue_order_length": self.virtual_blue_order_length},
            }
        if method == "getUTXOSet":
            return {"selected_height": 100, "outputs": self.outputs}
        if method == "getTransaction":
            txid = params["txid"]
            value = self.transactions.get(txid, "unknown")
            if isinstance(value, dict):
                return {"txid": txid, **value}
            return {"txid": txid, "status": value}
        if method == "submitTransaction":
            tx = params["transaction"]
            self.submit_count += 1
            self.transactions[tx["txid"]] = "mempool"
            return {"accepted": True, "txid": tx["txid"], "status": "mempool"}
        raise AssertionError(f"unexpected RPC method: {method}")


def funded_wallet(amount=100_000):
    wallet = StealthWallet.generate("l2-treasury")
    funding_r, funding_p = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
    output = {
        "txid": "ef" * 32,
        "vout": 0,
        "amount_satk": amount,
        "R_ephem_hex": funding_r.hex(),
        "P_onetime_hex": funding_p.hex(),
        "height": 1,
        "is_coinbase": False,
        "kind": "standard",
    }
    return wallet, output


class TestL2ChainBridge(unittest.IsolatedAsyncioTestCase):
    def make_ledger(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return AgentLedger(os.path.join(temp.name, "ledger.json"))

    async def test_withdrawal_is_signed_broadcast_and_idempotent(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])
        destination = StealthWallet.generate("destination").address

        result = await bridge.withdraw("wd-1", "agent", destination, 20_000, 1_000)
        self.assertEqual(result["status"], "BROADCAST")
        self.assertEqual(result["network_height"], 100)
        self.assertEqual(result["network_year"], 1)
        self.assertEqual(ledger.get_balance("agent"), 9_000)
        self.assertEqual(node.submit_count, 1)

        replay = await bridge.withdraw("wd-1", "agent", destination, 20_000, 1_000)
        self.assertEqual(replay["txid"], result["txid"])
        self.assertEqual(ledger.get_balance("agent"), 9_000)
        self.assertEqual(node.submit_count, 1)

    async def test_prebroadcast_failure_refunds_paid_balance(self):
        wallet, output = funded_wallet(amount=5_000)
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])
        destination = StealthWallet.generate("destination").address

        result = await bridge.withdraw("wd-no-funds", "agent", destination, 20_000, 1_000)
        self.assertEqual(result["status"], "FAILED_REFUNDED")
        self.assertEqual(ledger.get_balance("agent"), 30_000)
        self.assertEqual(node.submit_count, 0)

    async def test_uncredited_treasury_output_is_never_spent(self):
        wallet, output = funded_wallet(amount=100_000)
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1")
        destination = StealthWallet.generate("destination").address

        result = await bridge.withdraw("wd-uncredited", "agent", destination, 20_000, 1_000)
        self.assertEqual(result["status"], "FAILED_REFUNDED")
        self.assertEqual(ledger.get_balance("agent"), 30_000)
        self.assertEqual(node.submit_count, 0)

    async def test_burn_uses_nums_output_and_separate_fee_reserve(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.l2_burned_satk = 20_000
        ledger.deposit("btck:l2:fee-reserve", 1_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])

        result = await bridge.broadcast_burn(
            "burn-1", 20_000, 1_000, "btck:l2:fee-reserve"
        )
        self.assertEqual(result["status"], "BROADCAST")
        self.assertEqual(ledger.l2_burn_committed_satk, 20_000)
        self.assertEqual(ledger.get_balance("btck:l2:fee-reserve"), 0)
        burn_output = result["transaction"]["outputs"][0]
        self.assertTrue(is_provable_burn_output(
            burn_output["R_ephem_hex"], burn_output["P_onetime_hex"]
        ))

    async def test_presign_rpc_failure_refunds_withdrawal(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        node.fail_methods.add("getInfo")
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])

        result = await bridge.withdraw(
            "wd-rpc-down", "agent", StealthWallet.generate("destination").address,
            20_000, 1_000,
        )
        self.assertEqual(result["status"], "FAILED_REFUNDED")
        self.assertEqual(ledger.get_balance("agent"), 30_000)
        self.assertEqual(node.submit_count, 0)

    async def test_presign_rpc_failure_refunds_burn_and_fee(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        node.fail_methods.add("getUTXOSet")
        ledger = self.make_ledger()
        ledger.l2_burned_satk = 20_000
        ledger.deposit("btck:l2:fee-reserve", 1_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])

        result = await bridge.broadcast_burn(
            "burn-rpc-down", 20_000, 1_000, "btck:l2:fee-reserve"
        )
        self.assertEqual(result["status"], "FAILED_REFUNDED")
        self.assertEqual(ledger.l2_burn_committed_satk, 0)
        self.assertEqual(ledger.get_balance("btck:l2:fee-reserve"), 1_000)

    async def test_unknown_submit_effect_retains_signed_transaction_and_retries_same_tx(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        node.fail_methods.add("submitTransaction")
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])
        destination = StealthWallet.generate("destination").address

        with self.assertRaises(TimeoutError):
            await bridge.withdraw("wd-unknown", "agent", destination, 20_000, 1_000)
        signed = ledger.l2_withdrawals["wd-unknown"]
        self.assertEqual(signed["status"], "SIGNED")
        original_txid = signed["txid"]
        self.assertEqual(ledger.get_balance("agent"), 9_000)

        node.fail_methods.clear()
        retried = await bridge.withdraw("wd-unknown", "agent", destination, 20_000, 1_000)
        self.assertEqual(retried["status"], "BROADCAST")
        self.assertEqual(retried["txid"], original_txid)
        self.assertEqual(ledger.get_balance("agent"), 9_000)

    async def test_broadcast_record_reconciles_until_confirmation_threshold(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(
            node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)],
            settlement_confirmations=3,
        )
        destination = StealthWallet.generate("destination").address

        first = await bridge.withdraw("wd-confirm", "agent", destination, 20_000, 1_000)
        txid = first["txid"]
        node.transactions[txid] = {
            "status": "confirmed", "confirmations": 2, "block_hash": "aa" * 32,
        }
        shallow = await bridge.withdraw("wd-confirm", "agent", destination, 20_000, 1_000)
        self.assertEqual(shallow["status"], "BROADCAST")
        self.assertEqual(shallow["confirmations"], 2)

        node.transactions[txid]["confirmations"] = 3
        confirmed = await bridge.withdraw("wd-confirm", "agent", destination, 20_000, 1_000)
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["confirmations"], 3)
        self.assertEqual(node.submit_count, 1)

    async def test_reorg_or_mempool_eviction_rebroadcasts_identical_transaction(self):
        wallet, output = funded_wallet()
        node = FakeBridgeNodeRPC([output])
        ledger = self.make_ledger()
        ledger.deposit("agent", 30_000)
        bridge = L2ChainBridge(node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)])
        destination = StealthWallet.generate("destination").address

        first = await bridge.withdraw("wd-reorg", "agent", destination, 20_000, 1_000)
        node.transactions[first["txid"]] = "unknown"
        replay = await bridge.withdraw("wd-reorg", "agent", destination, 20_000, 1_000)

        self.assertEqual(replay["txid"], first["txid"])
        self.assertEqual(replay["transaction"], first["transaction"])
        self.assertEqual(ledger.get_balance("agent"), 9_000)
        self.assertEqual(node.submit_count, 2)

    async def test_wrong_chain_or_invalid_height_refunds_before_signing(self):
        for label, mutate in (
            ("wrong-chain", lambda node: setattr(node, "chain_id", "btck-simnet-v3")),
            ("bad-height", lambda node: setattr(node, "virtual_blue_order_length", 0)),
        ):
            with self.subTest(label=label):
                wallet, output = funded_wallet()
                node = FakeBridgeNodeRPC([output])
                mutate(node)
                ledger = self.make_ledger()
                ledger.deposit("agent", 30_000)
                bridge = L2ChainBridge(
                    node, wallet, ledger, "btck-mainnet-v1", [(output["txid"], 0)]
                )
                result = await bridge.withdraw(
                    f"wd-{label}", "agent", StealthWallet.generate("destination").address,
                    20_000, 1_000,
                )
                self.assertEqual(result["status"], "FAILED_REFUNDED")
                self.assertEqual(ledger.get_balance("agent"), 30_000)
                self.assertEqual(node.submit_count, 0)


if __name__ == "__main__":
    unittest.main()

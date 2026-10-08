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

    async def call(self, method, params=None):
        if method == "getInfo":
            return {
                "network": {"chain_id": "btck-mainnet-v1"},
                "dag": {"virtual_blue_order_length": 101},
            }
        if method == "getUTXOSet":
            return {"selected_height": 100, "outputs": self.outputs}
        if method == "getTransaction":
            txid = params["txid"]
            return {"txid": txid, "status": self.transactions.get(txid, "unknown")}
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


if __name__ == "__main__":
    unittest.main()

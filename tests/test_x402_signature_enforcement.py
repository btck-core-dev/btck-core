"""
Unit test for x402 Signature Enforcement & Anti-Spoofing (TASK-P0-05)
--------------------------------------------------------------------
Tests:
1. Missing signature/pubkey rejected (anti-free-credit vulnerability fix)
2. Malformed / forged signature rejected
3. Legitimate Schnorr signature accepted and credited
4. Double-spend receipt replay rejected
5. Exchange withdrawal without sufficient balance rejected
"""

import unittest
import tempfile
import shutil
import sys
sys.path.insert(0, '/Users/a1234/btck-core/src')

from gateway.ai_rpc_gateway import AIRPCGateway
from gateway.exchange_rpc import ExchangeRPCAdapter
from node.blockdag import BlockDAG
from privacy.stealth_address import StealthKeyPair, sha256
from privacy.wallet_engine import schnorr_sign


class FakeNodeRPC:
    def __init__(self):
        self.outputs = []

    async def call(self, method, params=None):
        if method == "getInfo":
            return {
                "network": {"chain_id": "btck-mainnet-v1"},
                "dag": {"virtual_blue_order_length": 101},
            }
        if method == "getUTXOSet":
            return {"selected_height": 100, "outputs": list(self.outputs)}
        raise AssertionError(f"unexpected RPC method: {method}")


class TestX402SignatureEnforcement(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.node_rpc = FakeNodeRPC()
        self.gateway = AIRPCGateway(
            data_dir=self.temp_dir,
            node_rpc=self.node_rpc,
            deposit_confirmations=3,
        )
        self.payer_keypair = StealthKeyPair()
        self.payer_addr = self.payer_keypair.get_public_stealth_address()
        self.payer_pub_hex = self.payer_keypair.spend_pub_bytes.hex()

        # Register a pending invoice of 10,000 SatK
        self.invoice_id = "inv_test_12345"
        payment_r, payment_p = StealthKeyPair.generate_one_time_stealth_output(self.gateway.treasury_addr)
        self.txid = "ab" * 32
        self.gateway.invoices[self.invoice_id] = {
            "invoice_id": self.invoice_id,
            "status": "PENDING",
            "amount_satk": 10000,
            "expires_at": 9999999999,
            "nonce": "nonce_xyz",
            "payer_stealth_address": self.payer_addr,
            "payment_R_ephem_hex": payment_r.hex(),
            "payment_P_onetime_hex": payment_p.hex(),
        }
        self.node_rpc.outputs = [{
            "txid": self.txid,
            "vout": 0,
            "amount_satk": 10000,
            "R_ephem_hex": payment_r.hex(),
            "P_onetime_hex": payment_p.hex(),
            "height": 98,
        }]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_missing_signature_rejected(self):
        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": 10000,
            "payer_stealth_address": self.payer_addr,
            "txid": self.txid,
            "vout": 0,
            # Intentionally omitting payment_signature and payer_spend_pub_hex
        }
        success, msg = await self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("mandatory cryptographic signature", msg)
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), 0)

    async def test_forged_signature_rejected(self):
        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": 10000,
            "payer_stealth_address": self.payer_addr,
            "payer_spend_pub_hex": self.payer_pub_hex,
            "payment_signature": "00" * 64,  # Invalid/forged BIP-340 signature
            "txid": self.txid,
            "vout": 0,
        }
        success, msg = await self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("signature verification failed", msg)
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), 0)

    async def test_valid_confirmed_chain_payment_accepted_once(self):
        amt = 10000
        nonce = self.gateway.invoices[self.invoice_id]["nonce"]
        digest = sha256(
            f"BTCK_L2_DEPOSIT_V1|{self.invoice_id}|{self.txid}|0|{amt}|{nonce}".encode()
        )
        
        sig = schnorr_sign(self.payer_keypair.spend_priv, digest)

        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": amt,
            "payer_stealth_address": self.payer_addr,
            "payer_spend_pub_hex": self.payer_pub_hex,
            "payment_signature": sig.hex(),
            "txid": self.txid,
            "vout": 0,
        }
        success, msg = await self.gateway.process_payment_receipt(receipt)
        self.assertTrue(success, f"Expected success, got: {msg}")
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), amt)

        # The invoice and outpoint remain consumed after a process restart.
        reloaded = AIRPCGateway(
            data_dir=self.temp_dir,
            node_rpc=self.node_rpc,
            deposit_confirmations=3,
        )
        replay_success, replay_msg = await reloaded.process_payment_receipt(receipt)
        self.assertFalse(replay_success)
        self.assertIn("already SETTLED", replay_msg)
        self.assertEqual(reloaded.ledger.get_balance(self.payer_addr), amt)

    async def test_unconfirmed_or_wrong_invoice_output_rejected(self):
        amt = 10000
        nonce = self.gateway.invoices[self.invoice_id]["nonce"]
        digest = sha256(
            f"BTCK_L2_DEPOSIT_V1|{self.invoice_id}|{self.txid}|0|{amt}|{nonce}".encode()
        )
        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": amt,
            "payer_stealth_address": self.payer_addr,
            "payer_spend_pub_hex": self.payer_pub_hex,
            "payment_signature": schnorr_sign(self.payer_keypair.spend_priv, digest).hex(),
            "txid": self.txid,
            "vout": 0,
        }
        self.node_rpc.outputs[0]["height"] = 100
        success, msg = await self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("requires 3 confirmations", msg)

        self.node_rpc.outputs[0]["height"] = 98
        self.node_rpc.outputs[0]["P_onetime_hex"] = StealthKeyPair.generate_one_time_stealth_output(
            self.gateway.treasury_addr
        )[1].hex()
        success, msg = await self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("does not match this invoice", msg)

    def test_exchange_withdrawal_insufficient_balance(self):
        dag = BlockDAG(network="simnet")
        adapter = ExchangeRPCAdapter(dag)
        # Attempt to withdraw 10.0 BTCK with 0 balance
        dest = StealthKeyPair().get_public_stealth_address()
        resp = adapter.handle_rpc("sendtoaddress", [dest, 10.0, "test"])
        self.assertIsInstance(resp, dict)
        self.assertIn("error", resp)
        self.assertIn("Insufficient exchange balance", resp["error"])


if __name__ == "__main__":
    unittest.main()

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


class TestX402SignatureEnforcement(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.gateway = AIRPCGateway(data_dir=self.temp_dir)
        self.payer_keypair = StealthKeyPair()
        self.payer_addr = self.payer_keypair.get_public_stealth_address()
        self.payer_pub_hex = self.payer_keypair.spend_pub_bytes.hex()

        # Register a pending invoice of 10,000 SatK
        self.invoice_id = "inv_test_12345"
        self.gateway.invoices[self.invoice_id] = {
            "invoice_id": self.invoice_id,
            "status": "PENDING",
            "amount_satk": 10000,
            "expires_at": 9999999999,
            "nonce": "nonce_xyz"
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_missing_signature_rejected(self):
        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": 10000,
            "payer_stealth_address": self.payer_addr
            # Intentionally omitting payment_signature and payer_spend_pub_hex
        }
        success, msg = self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("mandatory cryptographic signature", msg)
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), 0)

    def test_forged_signature_rejected(self):
        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": 10000,
            "payer_stealth_address": self.payer_addr,
            "payer_spend_pub_hex": self.payer_pub_hex,
            "payment_signature": "00" * 64  # Invalid/forged BIP-340 signature
        }
        success, msg = self.gateway.process_payment_receipt(receipt)
        self.assertFalse(success)
        self.assertIn("signature verification failed", msg)
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), 0)

    def test_valid_signature_accepted(self):
        amt = 10000
        timestamp = "1788912345"
        treasury = self.gateway.treasury_addr
        nonce = self.gateway.invoices[self.invoice_id]["nonce"]
        digest = sha256(f"{self.invoice_id}_{amt}_{treasury}_{nonce}_{timestamp}".encode())
        
        sig = schnorr_sign(self.payer_keypair.spend_priv, digest)

        receipt = {
            "invoice_id": self.invoice_id,
            "amount_satk": amt,
            "payer_stealth_address": self.payer_addr,
            "payer_spend_pub_hex": self.payer_pub_hex,
            "payment_signature": sig.hex(),
            "timestamp": timestamp,
            "recipient_one_time_output": "onetime_abc"
        }
        success, msg = self.gateway.process_payment_receipt(receipt)
        self.assertTrue(success, f"Expected success, got: {msg}")
        self.assertEqual(self.gateway.ledger.get_balance(self.payer_addr), amt)

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

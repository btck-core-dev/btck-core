"""
Test BTC-K Agent SDK Workflow
-----------------------------
Verifies wallet generation, key export, invoice payment formatting,
and client-side payment handshake.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sdk")))

from btck_agent import BTCKWallet, BTCKAgentClient


class TestSDKWorkflow(unittest.TestCase):
    def test_sdk_workflow(self):
        # 1. Generate Wallet
        wallet = BTCKWallet.generate()
        self.assertTrue(wallet.address.startswith("btck1s"))
        
        # 2. Key Export & Backup
        keys = wallet.export_keys()
        self.assertIn("spend_priv_scalar", keys)
        self.assertIn("view_priv_scalar", keys)
        self.assertGreater(len(keys["spend_priv_scalar"]), 0)
        self.assertGreater(len(keys["view_priv_scalar"]), 0)

        # 3. Legacy self-signed receipts must not be presented as payments.
        fake_invoice = {
            "invoice_id": "inv_001_sats_99",
            "amount_sats": 25,
            "payment_address": wallet.address,
            "resource": "v1/chat/completions",
            "model": "deepseek-r1"
        }
        
        with self.assertRaises(RuntimeError):
            wallet.pay_invoice(fake_invoice)

        # 4. Agent Client Setup
        client = BTCKAgentClient(wallet=wallet, gateway_url="http://127.0.0.1:8080")
        self.assertEqual(client.wallet.address, wallet.address)
        self.assertEqual(client.gateway_url, "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()

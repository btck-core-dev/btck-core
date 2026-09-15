"""
Test BTC-K Agent SDK Workflow
-----------------------------
Verifies wallet generation, key export, invoice payment formatting,
and client-side payment handshake.
"""

import sys
import unittest

sys.path.insert(0, '/Users/a1234/btck-core/sdk')

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

        # 3. Simulate HTTP 402 Invoice Payment
        fake_invoice = {
            "invoice_id": "inv_001_sats_99",
            "amount_sats": 25,
            "payment_address": wallet.address,
            "resource": "v1/chat/completions",
            "model": "deepseek-r1"
        }
        
        receipt = wallet.pay_invoice(fake_invoice)
        self.assertEqual(receipt["status"], "PAID")
        self.assertEqual(receipt["amount_sats"], 25)
        self.assertEqual(len(receipt["recipient_one_time_output"]), 66)  # 33 bytes hex
        self.assertEqual(len(receipt["ephemeral_pubkey"]), 66)  # 33 bytes hex

        # 4. Agent Client Setup
        client = BTCKAgentClient(wallet=wallet, gateway_url="http://127.0.0.1:8080")
        self.assertEqual(client.wallet.address, wallet.address)
        self.assertEqual(client.gateway_url, "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()

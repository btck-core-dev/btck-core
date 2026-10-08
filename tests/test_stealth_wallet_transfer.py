"""
Test BTC-K Stealth Wallet Transfer Lifecycle
---------------------------------------------
Verifies generation, block scanning, offline Schnorr signing,
and recipient balance updates.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from privacy.wallet_engine import StealthWallet, StealthTransaction, SATK_PER_COIN
from privacy.stealth_address import StealthKeyPair


class TestStealthWalletTransfer(unittest.TestCase):
    def test_address_validation_checks_checksum_and_curve_points(self):
        wallet = StealthWallet.generate("validator")
        self.assertTrue(StealthKeyPair.validate_stealth_address(wallet.address))
        replacement = "1" if wallet.address[-1] != "1" else "2"
        self.assertFalse(
            StealthKeyPair.validate_stealth_address(wallet.address[:-1] + replacement)
        )
        self.assertFalse(StealthKeyPair.validate_stealth_address("btck1s-invalid"))

    def test_full_stealth_transfer_lifecycle(self):
        founder = StealthWallet.generate("Founder")
        alice = StealthWallet.generate("Alice")
        bob = StealthWallet.generate("Attacker")

        # 1. Simulate mining
        R_mine, P_mine = StealthKeyPair.generate_one_time_stealth_output(founder.address)
        block_outputs = [{
            "txid": "aa" * 32,
            "vout": 0,
            "amount_satk": 50 * SATK_PER_COIN,
            "R_ephem_hex": R_mine.hex(),
            "P_onetime_hex": P_mine.hex(),
            "height": 10
        }]

        # 2. View Key scan
        founder.scan_chain_outputs(block_outputs)
        self.assertEqual(founder.get_balance_btck(), 50.0)
        self.assertEqual(bob.scan_chain_outputs(block_outputs), [])

        # 3. Transfer
        tx = founder.create_and_sign_transfer(alice.address, int(20 * SATK_PER_COIN), fee_satk=5000)
        self.assertTrue(StealthWallet.verify_transaction(tx))

        # 4. Recipient scan
        alice_outputs = [{
            "txid": tx.txid,
            "vout": 0,
            "amount_satk": tx.outputs[0].amount_satk,
            "R_ephem_hex": tx.outputs[0].R_ephem_hex,
            "P_onetime_hex": tx.outputs[0].P_onetime_hex,
            "height": 11
        }]
        alice.scan_chain_outputs(alice_outputs)
        self.assertEqual(alice.get_balance_btck(), 20.0)


if __name__ == "__main__":
    unittest.main()

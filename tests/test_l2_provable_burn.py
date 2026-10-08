"""Canonical L2 burn-vector and signed-transaction tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet
from tokenomics.provable_burn import burn_output_hex, is_provable_burn_output


class TestL2ProvableBurn(unittest.TestCase):
    def test_burn_vector_is_frozen_and_reproducible(self):
        burn_r, burn_p = burn_output_hex()
        self.assertEqual(burn_r, "03954dd9940171c8509c2f2c796802f9e410bde80dd79f9bea69db30a964c39038")
        self.assertEqual(burn_p, "03fb85216614cdf67a059c7c3128a788207d3320aa426d535cfd7a12c9e2c4aaee")
        self.assertTrue(is_provable_burn_output(burn_r, burn_p))

    def test_wallet_signs_burn_to_exact_nums_output(self):
        wallet = StealthWallet.generate("burn-source")
        funding_r, funding_p = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
        wallet.scan_chain_outputs([{
            "txid": "cd" * 32,
            "vout": 0,
            "amount_satk": 100_000,
            "R_ephem_hex": funding_r.hex(),
            "P_onetime_hex": funding_p.hex(),
            "height": 1,
        }])

        tx = wallet.create_and_sign_provable_burn(20_000, fee_satk=1_000)
        self.assertTrue(StealthWallet.verify_transaction(tx))
        self.assertEqual(tx.outputs[0].amount_satk, 20_000)
        self.assertTrue(is_provable_burn_output(
            tx.outputs[0].R_ephem_hex,
            tx.outputs[0].P_onetime_hex,
        ))


if __name__ == "__main__":
    unittest.main()

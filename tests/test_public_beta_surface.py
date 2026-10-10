"""Public-beta explorer and Stratum identity safety checks."""

import unittest
from unittest.mock import patch

from dataclasses import replace

from config.network import load_network
from stratum.stratum_server import (
    BTCKStratumServer,
    MAINNET_PERSONAL_ADDRESS,
    MAINNET_POOL_TREASURY_ADDRESS,
    resolve_pool_treasury_address,
    validate_worker_name,
)
from web_explorer_server import _search_recent_blocks


class TestPublicBetaSurface(unittest.TestCase):
    def test_worker_names_are_safe_for_public_display(self):
        self.assertEqual(validate_worker_name("rig-01_primary"), "rig-01_primary")
        for invalid in ("", "bad.name", "<script>", "space name", "x" * 65):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_worker_name(invalid)

    def test_recent_block_search_never_invents_a_result(self):
        stats = {
            "blocks": [
                {"height": 42, "full_hash": "ab" * 32, "timestamp": 1.0},
            ]
        }
        self.assertTrue(_search_recent_blocks("#42", stats)["found"])
        self.assertTrue(_search_recent_blocks("abababab", stats)["found"])
        missing = _search_recent_blocks("43", stats)
        self.assertFalse(missing["found"])
        self.assertEqual(missing["scope"], "recent_node_window")

    def test_mainnet_pool_address_is_explicit_and_frozen(self):
        mainnet_like = replace(load_network("devnet"), name="mainnet")
        with self.assertRaises(ValueError):
            resolve_pool_treasury_address(mainnet_like, None)
        with self.assertRaises(ValueError):
            resolve_pool_treasury_address(mainnet_like, "btck1s-wrong")
        self.assertEqual(
            resolve_pool_treasury_address(mainnet_like, MAINNET_POOL_TREASURY_ADDRESS),
            MAINNET_POOL_TREASURY_ADDRESS,
        )

    def test_guardian_payout_override_is_narrow_and_frozen(self):
        guardian = replace(
            load_network("simnet"),
            name="mainnet",
            release_approval_id="BTCK-GUARDIAN-20261008-1500-CST",
            hard_checkpoints={},
        )
        environment = {
            "BTCK_POOL_FEE_ADDRESS": MAINNET_POOL_TREASURY_ADDRESS,
            "BTCK_GUARDIAN_PAYOUT_ADDRESS": MAINNET_PERSONAL_ADDRESS,
        }
        with patch.dict("os.environ", environment, clear=True):
            server = BTCKStratumServer(network=guardian)
        self.assertEqual(server.guardian_payout_address, MAINNET_PERSONAL_ADDRESS)

        with patch.dict("os.environ", {"BTCK_GUARDIAN_PAYOUT_ADDRESS": MAINNET_PERSONAL_ADDRESS}, clear=True):
            with self.assertRaisesRegex(ValueError, "pre-checkpoint MAINNET guardian phase"):
                BTCKStratumServer(network="simnet")


if __name__ == "__main__":
    unittest.main()

"""Dashboard difficulty labels must use the active network's own PoW scale."""

import unittest

from config.network import load_network
from consensus.pow import compact_to_target, target_to_compact
from web_explorer_server import _public_miner_id, _relative_network_difficulty


class TestWebExplorerMetrics(unittest.TestCase):
    def test_pow_limit_is_one_x_difficulty(self):
        network = load_network()
        self.assertEqual(_relative_network_difficulty(network.pow_limit_bits), 1.0)

    def test_harder_target_has_higher_relative_difficulty(self):
        network = load_network()
        limit = compact_to_target(network.pow_limit_bits)
        harder_bits = target_to_compact(limit // 2)
        self.assertAlmostEqual(_relative_network_difficulty(harder_bits), 2.0, places=5)

    def test_public_miner_id_does_not_disclose_pool_identity(self):
        address = "btck1s7ZM1fHRH3jXjXazpfLdrF4eCW5gysbMW8Lx73L1CXX5phimDrY6RRKrSnbU1xc1KjnwUUdK4xQ7pqfCjoXhags1159M37pa"
        public_id = _public_miner_id(address, "ks01")
        self.assertRegex(public_id, r"^miner-[0-9a-f]{12}$")
        self.assertEqual(public_id, _public_miner_id(address, "ks01"))
        self.assertNotIn(address, public_id)
        self.assertNotIn("ks01", public_id)
        self.assertNotEqual(public_id, _public_miner_id(address, "ks02"))

"""Dashboard difficulty labels must use the active network's own PoW scale."""

import unittest

import web_explorer_server as explorer
from config.network import load_network
from consensus.pow import compact_to_target, target_to_compact
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet, UTXO, transaction_to_dict
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

    def test_address_lookup_scopes_same_named_workers_to_exact_wallet(self):
        wanted = "btck1s-wanted"
        other = "btck1s-other"
        original_solo = explorer._CACHED_POOL_STATUS
        original_pplns = explorer._CACHED_PPLNS_STATUS
        try:
            explorer._CACHED_POOL_STATUS = {
                "pool_mode": "SOLO",
                "workers": [
                    {"wallet_address": wanted, "worker_name": "ks001", "online": True, "connections": 1, "hashrate_hps": 200e9},
                    {"wallet_address": other, "worker_name": "ks001", "online": True, "connections": 1, "hashrate_hps": 900e9},
                    {"wallet_address": wanted, "worker_name": "disconnected-worker", "online": False, "connections": 0, "hashrate_hps": 900e9},
                ],
            }
            explorer._CACHED_PPLNS_STATUS = {
                "pool_mode": "PPLNS",
                "workers": [
                    {"wallet_address": wanted, "worker_name": "01", "online": True, "connections": 1, "hashrate_hps": 100e9},
                ],
            }
            result = explorer.build_miner_lookup(wanted)
            self.assertTrue(result["found"])
            self.assertEqual(result["worker_count"], 2)
            self.assertEqual(result["total_hashrate_hps"], 300e9)
            self.assertEqual({item["worker_name"] for item in result["workers"]}, {"ks001", "01"})
            self.assertNotIn(wanted, repr(result))
            self.assertNotIn(other, repr(result))
        finally:
            explorer._CACHED_POOL_STATUS = original_solo
            explorer._CACHED_PPLNS_STATUS = original_pplns

    def test_public_wallet_relay_accepts_only_valid_mainnet_signed_package(self):
        sender = StealthWallet.generate("relay-sender")
        recipient = StealthWallet.generate("relay-recipient")
        ephemeral, onetime = StealthKeyPair.generate_one_time_stealth_output(sender.address)
        sender.utxos = [UTXO(
            txid="11" * 32,
            vout=0,
            amount_satk=200_000,
            R_ephem_hex=ephemeral.hex(),
            P_onetime_hex=onetime.hex(),
            block_height=1,
        )]
        tx = sender.create_and_sign_transfer(recipient.address, 100_000, 10_000)
        network = load_network()
        package = {
            "format": "btck-signed-transfer-v1",
            "package_id": "22" * 32,
            "network": {
                "name": network.name,
                "chain_id": network.chain_id,
                "genesis_hash": network.genesis.expected_hash,
                "consensus_fingerprint": network.consensus_fingerprint,
            },
            "transaction": transaction_to_dict(tx),
        }
        canonical = explorer.validate_signed_wallet_package(package)
        self.assertEqual(canonical["txid"], tx.txid)
        package["network"] = {**package["network"], "chain_id": "wrong-chain"}
        with self.assertRaisesRegex(ValueError, "network identity"):
            explorer.validate_signed_wallet_package(package)

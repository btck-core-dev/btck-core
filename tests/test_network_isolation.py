"""Security regression tests for devnet/testnet/mainnet isolation."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config.network import DisabledFeatureError, MainnetReleaseError, load_network, require_feature
from node.blockdag import BlockDAG
from node.daemon import BTCKDaemon
from p2p.p2p_network import P2PNetwork, PeerConnection


class _MemoryWriter:
    def __init__(self):
        self.closed = False
        self.buffer = bytearray()

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class TestNetworkIsolation(unittest.TestCase):
    def test_default_network_is_devnet(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BTCK_NETWORK", None)
            config = load_network()

        self.assertEqual(config.name, "devnet")
        self.assertFalse(config.is_mainnet)

    def test_networks_have_distinct_consensus_identity(self):
        devnet = load_network("devnet")
        testnet = load_network("testnet")

        self.assertNotEqual(devnet.chain_id, testnet.chain_id)
        self.assertNotEqual(devnet.p2p_magic, testnet.p2p_magic)
        self.assertNotEqual(devnet.genesis.expected_hash, testnet.genesis.expected_hash)
        self.assertNotEqual(devnet.data_subdir, testnet.data_subdir)

        self.assertNotEqual(BlockDAG(network=devnet).genesis_hash, BlockDAG(network=testnet).genesis_hash)

    def test_data_paths_are_namespaced_even_with_shared_root(self):
        with tempfile.TemporaryDirectory() as root:
            dev_path = load_network("devnet").data_dir(Path(root))
            test_path = load_network("testnet").data_dir(Path(root))

        self.assertNotEqual(dev_path, test_path)
        self.assertEqual(dev_path.name, "devnet")
        self.assertEqual(test_path.name, "testnet")

    def test_mainnet_is_locked_without_approved_manifest(self):
        with self.assertRaises(MainnetReleaseError):
            load_network("mainnet")

    def test_safe_stratum_enabled_but_unsafe_prototypes_disabled(self):
        self.assertEqual(require_feature("stratum", "devnet").name, "devnet")
        with self.assertRaises(DisabledFeatureError):
            require_feature("ai_payments", "testnet")

    def test_daemon_exposes_network_identity(self):
        daemon = BTCKDaemon(host="127.0.0.1", network="testnet")
        info = daemon.get_node_info()

        self.assertEqual(info["network"]["name"], "testnet")
        self.assertEqual(info["network"]["chain_id"], load_network("testnet").chain_id)
        self.assertEqual(info["network"]["genesis_hash"], daemon.dag.genesis_hash)
        self.assertTrue(info["network"]["data_dir"].endswith("/testnet"))


class TestP2PNetworkIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_cross_network_handshake_is_rejected(self):
        local = P2PNetwork(BlockDAG(network="testnet"), p2p_host="127.0.0.1")
        writer = _MemoryWriter()
        peer = PeerConnection(None, writer, "devnet-peer", local.network_magic)
        devnet = BlockDAG(network="devnet")

        await local._process_msg(peer, {
            "type": "HANDSHAKE",
            "payload": {
                "network": devnet.network.name,
                "chain_id": devnet.network.chain_id,
                "consensus_fingerprint": devnet.network.consensus_fingerprint,
                "genesis_hash": devnet.genesis_hash,
                "blue_score": 0,
            },
        })

        self.assertTrue(peer.banned)
        self.assertTrue(writer.closed)
        self.assertFalse(peer.handshake_complete)


if __name__ == "__main__":
    unittest.main()

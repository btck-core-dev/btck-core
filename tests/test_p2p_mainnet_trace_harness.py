"""Check the offline protocol harness boundaries without accessing production."""
import tempfile
from pathlib import Path
import unittest

from config.network import load_network
from node.blockdag import BlockDAG
from node.chain_store import ChainStore
from p2p import p2p_network
from scripts.verify_p2p_mainnet_trace import load_fixture, exercise
from tests.pow_helpers import mine_block


class OfflineHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_readonly_fixture_and_candidate_dependency_admission(self):
        dag = BlockDAG(network='simnet')
        output = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'source.sqlite3'
            store = ChainStore(path, dag.network)
            for i in range(20):
                block = mine_block(dag, parents=dag.get_tips(), timestamp=1788912100+i,
                                   coinbase_output=output)
                self.assertTrue(dag.add_block(block))
                store.append_block(block)
            before = path.read_bytes()
            fixture = load_fixture(path, 16)
            self.assertEqual(path.read_bytes(), before)
            result = await exercise(p2p_network, fixture, network_config=load_network('simnet'))
            self.assertEqual(result['scope'], 'protocol_dependencies_only_not_full_consensus')
            self.assertEqual(result['prefix_sequence'], 4)
            self.assertEqual(result['required_blocks'], 16)
            self.assertEqual(result['admitted'], 16)
            self.assertEqual(result['missing'], 0)
            self.assertIsNone(result['error'])
            self.assertLessEqual(result['peak_outstanding'], 16)
            self.assertEqual(path.read_bytes(), before)

    def test_empty_prefix_refused(self):
        with tempfile.TemporaryDirectory() as root:
            dag = BlockDAG(network='simnet')
            path = Path(root)/'empty.sqlite3'
            ChainStore(path, dag.network)
            with self.assertRaises(ValueError):
                load_fixture(path, 20)

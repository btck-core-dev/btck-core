import tempfile
from pathlib import Path
import unittest
from node.daemon import BTCKDaemon
from deploy.standby_node import prepare_standby
from p2p.standby_sync import StandbySyncNetwork
from tests.pow_helpers import mine_block
from wallet_sync_index import WalletSyncIndex


class StandbyStartupTests(unittest.TestCase):
    def test_managed_policy_starts_closed_without_source_configuration(self):
        from p2p.managed_sync import ManagedSyncNetwork
        with tempfile.TemporaryDirectory() as root:
            node=BTCKDaemon(network='simnet',data_root=root,persistent=True)
            output=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32))
            block=mine_block(node.dag,parents=node.dag.get_tips(),timestamp=1788912100,
                             coinbase_output=output)
            self.assertTrue(node.accept_block(block))
            prepare_standby(node,managed=True,health_file=Path(root)/'absent-health.json',
                            managed_peer_file=Path(root)/'absent-peers.json')
            self.assertIsInstance(node.p2p,ManagedSyncNetwork)
            self.assertFalse(node.p2p._health_allowed())
            self.assertFalse(node.p2p.connection_requests)

    def test_sealed_snapshot_avoids_tail_replay_and_builds_index(self):
        with tempfile.TemporaryDirectory() as root:
            node = BTCKDaemon(network='simnet',data_root=root,persistent=True)
            output=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32))
            for i in range(4):
                block=mine_block(node.dag,parents=node.dag.get_tips(),timestamp=1788912100+i,coinbase_output=output)
                self.assertTrue(node.accept_block(block))
            index=Path(root)/'wallet-index.sqlite3'
            sequence,state_root=prepare_standby(node,wallet_index=index)
            self.assertEqual(sequence,4)
            self.assertIsInstance(node.p2p,StandbySyncNetwork)
            self.assertEqual(node.p2p.seed_peers,[])
            reopened=BTCKDaemon(network='simnet',data_root=root,persistent=True)
            self.assertEqual(reopened.chain_store.loaded_snapshot_sequence,4)
            self.assertEqual(reopened.chain_store.tail_replayed_blocks,0)
            self.assertEqual(reopened.dag.state_root(),state_root)
            self.assertEqual(WalletSyncIndex(index).tip_height,len(node.dag.virtual_blue_order)-1)

    def test_active_node_cannot_seal_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            node=BTCKDaemon(network='simnet',data_root=root)
            node.running=True
            with self.assertRaisesRegex(RuntimeError,'non-serving'):
                prepare_standby(node)

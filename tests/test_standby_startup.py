import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from node.daemon import BTCKDaemon
from deploy.standby_node import prepare_standby
from p2p.standby_sync import StandbySyncNetwork
from tests.pow_helpers import mine_block
from wallet_sync_index import WalletSyncIndex


class StandbyStartupTests(unittest.TestCase):
    def test_explicit_empty_index_environment_disables_only_auxiliary_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            sequence,state_root=self.sealed_fixture(root)
            node=BTCKDaemon(network='simnet',data_root=root,persistent=True)
            with patch('deploy.standby_node.build_wallet_sync_index',side_effect=AssertionError('disabled auxiliary index')):
                self.assertEqual(prepare_standby(node,wallet_index='',reuse_loaded_snapshot=True),
                                 (sequence,state_root))

    def sealed_fixture(self, root):
        node=BTCKDaemon(network='simnet',data_root=root,persistent=True)
        output=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32))
        block=mine_block(node.dag,parents=node.dag.get_tips(),timestamp=1788912100,
                         coinbase_output=output)
        self.assertTrue(node.accept_block(block))
        return prepare_standby(node)

    def test_verified_complete_snapshot_is_not_rehashed_or_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            sequence,state_root=self.sealed_fixture(root)
            # Constructor really loads and validates the snapshot before reuse.
            node=BTCKDaemon(network='simnet',data_root=root,persistent=True)
            self.assertEqual(node.chain_store.loaded_snapshot_sequence,sequence)
            self.assertEqual(node.chain_store.tail_replayed_blocks,0)
            with patch.object(node.dag,'state_root',side_effect=AssertionError('duplicate full hash')), \
                 patch.object(node.chain_store,'seal_latest_checkpoint',side_effect=AssertionError('duplicate seal')), \
                 patch.object(node.chain_store,'write_snapshot_v2',side_effect=AssertionError('duplicate snapshot')):
                self.assertEqual(prepare_standby(node,reuse_loaded_snapshot=True),(sequence,state_root))
            self.assertIsInstance(node.p2p,StandbySyncNetwork)

    def test_new_tail_still_requires_normal_sealing(self):
        with tempfile.TemporaryDirectory() as root:
            self.sealed_fixture(root)
            node=BTCKDaemon(network='simnet',data_root=root,persistent=True)
            block=mine_block(node.dag,parents=node.dag.get_tips(),timestamp=1788912101,
                coinbase_output=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32)))
            self.assertTrue(node.accept_block(block))
            with patch.object(node.chain_store,'seal_latest_checkpoint',wraps=node.chain_store.seal_latest_checkpoint) as seal:
                sequence,_=prepare_standby(node,reuse_loaded_snapshot=True)
                self.assertEqual(sequence,2)
                seal.assert_called_once()

    def test_reuse_flag_cannot_bypass_constructor_snapshot_validation(self):
        import sqlite3
        from node.chain_store import ChainStoreError
        with tempfile.TemporaryDirectory() as root:
            self.sealed_fixture(root)
            path=Path(root)/'simnet/chain.snapshot.v2.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute("UPDATE meta SET value=? WHERE key='state_root'",('0'*64,))
            with self.assertRaises(ChainStoreError):
                BTCKDaemon(network='simnet',data_root=root,persistent=True)

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

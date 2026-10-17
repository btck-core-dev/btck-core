"""Crash/restart recovery and network-bound chain database tests."""

import asyncio
from dataclasses import asdict
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config.network import load_network
from node.chain_store import ChainStore, ChainStoreError
from node.daemon import BTCKDaemon
from tests.pow_helpers import mine_block


COINBASE = (bytes.fromhex("02" + "77" * 32), bytes.fromhex("03" + "88" * 32))


class TestChainPersistence(unittest.TestCase):
    def test_inline_checkpoints_are_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "chain.sqlite3")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BTCK_INLINE_STATE_CHECKPOINTS", None)
                store = ChainStore(path, load_network("simnet"))
                store._record_count = 99
                store.checkpoint_interval = 100
                self.assertFalse(store.should_checkpoint_next_block())
            with patch.dict(os.environ, {"BTCK_INLINE_STATE_CHECKPOINTS": "1"}):
                store = ChainStore(path, load_network("simnet"))
                store._record_count = 99
                store.checkpoint_interval = 100
                self.assertTrue(store.should_checkpoint_next_block())

    def test_300k_boundary_does_not_run_inline_checkpoint_or_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BTCK_INLINE_STATE_CHECKPOINTS", None)
                daemon = BTCKDaemon(
                    host="127.0.0.1", network="simnet", data_root=root, persistent=True
                )
            daemon.chain_store._record_count = 299_999
            timestamp = daemon.dag.blocks[daemon.dag.genesis_hash].timestamp
            block = mine_block(
                daemon.dag,
                parents=[daemon.dag.selected_tip],
                timestamp=timestamp + 1,
                coinbase_output=COINBASE,
            )
            with patch.object(
                daemon.dag, "state_root", side_effect=AssertionError("inline state root")
            ), patch.object(
                daemon.chain_store,
                "write_snapshot",
                side_effect=AssertionError("inline snapshot"),
            ):
                self.assertTrue(daemon.accept_block(block))
            self.assertEqual(daemon.chain_store._record_count, 300_000)

    @staticmethod
    def _legacy_state_root(dag):
        blocks = []
        for block_hash in sorted(dag.blocks):
            block = dag.blocks[block_hash]
            blocks.append({
                "hash": block_hash,
                "parents": sorted(block.parents),
                "selected_parent": block.selected_parent,
                "merge_set": sorted(block.merge_set),
                "merge_set_blues": block.merge_set_blues,
                "merge_set_reds": block.merge_set_reds,
                "blue_anticone_sizes": dict(sorted(block.blue_anticone_sizes.items())),
                "blue_score": block.blue_score,
                "blue_work": block.blue_work,
                "minted_sats": block.minted_sats,
                "miner_base_sats": block.miner_base_sats,
                "eco_deposit_sats": block.eco_deposit_sats,
                "pool_deposit_sats": block.pool_deposit_sats,
                "combo_payout_sats": block.combo_payout_sats,
                "fees_sats": block.fees_sats,
                "accepted_txids": dag.utxo_set.get_block_accepted_txids(block_hash),
            })
        utxos = [
            {"txid": txid, "vout": vout, **asdict(entry)}
            for (txid, vout), entry in sorted(dag.utxo_set.utxos.items())
        ]
        epochs = {
            str(epoch_id): asdict(epoch)
            for epoch_id, epoch in sorted(dag.combo_machine.epochs.items())
        }
        payload = {
            "chain_id": dag.network.chain_id,
            "blocks": blocks,
            "tips": sorted(dag.tips),
            "selected_tip": dag.selected_tip,
            "selected_chain": dag.virtual_selected_parent_chain,
            "virtual_blue_order": dag.virtual_blue_order,
            "issued_supply_sats": dag.issued_supply_sats,
            "pool_balance_sats": dag.pool_balance_sats,
            "utxos": utxos,
            "combo_epochs": epochs,
            "combo_pending": sorted(dag.combo_machine.pending_draws),
            "combo_active": dag.combo_machine.active_epoch_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def test_streaming_state_root_is_byte_compatible_with_legacy_format(self):
        with tempfile.TemporaryDirectory() as root:
            daemon = BTCKDaemon(
                host="127.0.0.1", network="simnet", data_root=root, persistent=True
            )
            timestamp = daemon.dag.blocks[daemon.dag.genesis_hash].timestamp
            for height in range(1, 5):
                block = mine_block(
                    daemon.dag,
                    parents=[daemon.dag.selected_tip],
                    timestamp=timestamp + height,
                    coinbase_output=COINBASE,
                    nonce=height * 101,
                )
                self.assertTrue(daemon.accept_block(block))
            self.assertEqual(daemon.dag.state_root(), self._legacy_state_root(daemon.dag))

    def test_restart_replays_identical_dag_utxo_and_state_root(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(
                host="127.0.0.1",
                network="simnet",
                data_root=root,
                persistent=True,
            )
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            for height in range(1, 4):
                block = mine_block(
                    first.dag,
                    parents=[first.dag.selected_tip],
                    timestamp=timestamp + height,
                    coinbase_output=COINBASE,
                    nonce=height * 100,
                )
                self.assertTrue(first.accept_block(block))

            expected_info = first.dag.get_dag_info()
            expected_utxos = dict(first.dag.utxo_set.utxos)

            # No stop hook is used: constructing a new process view simulates a
            # crash/restart from only the durable SQLite journal.
            restarted = BTCKDaemon(
                host="127.0.0.1",
                network="simnet",
                data_root=root,
                persistent=True,
            )

            self.assertEqual(restarted.replayed_blocks, 3)
            self.assertEqual(restarted.dag.get_dag_info(), expected_info)
            self.assertEqual(restarted.dag.utxo_set.utxos, expected_utxos)

    def test_database_identity_cannot_be_reused_by_another_network(self):
        with tempfile.TemporaryDirectory() as root:
            database = os.path.join(root, "shared.sqlite3")
            ChainStore(database, load_network("simnet"))
            with self.assertRaisesRegex(ChainStoreError, "identity mismatch"):
                ChainStore(database, load_network("testnet"))

    def test_state_root_corruption_stops_replay(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(
                host="127.0.0.1",
                network="simnet",
                data_root=root,
                persistent=True,
            )
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            block = mine_block(
                first.dag,
                parents=[first.dag.genesis_hash],
                timestamp=timestamp + 1,
                coinbase_output=COINBASE,
            )
            first.accept_block(block)
            database = first.chain_store.path
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE accepted_blocks SET state_root = ?",
                    ("00" * 32,),
                )

            with self.assertRaisesRegex(ChainStoreError, "State root mismatch"):
                BTCKDaemon(
                    host="127.0.0.1",
                    network="simnet",
                    data_root=root,
                    persistent=True,
                )

    def test_verified_snapshot_restores_prefix_and_replays_only_tail(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            for height in range(1, 4):
                block = mine_block(
                    first.dag,
                    parents=[first.dag.selected_tip],
                    timestamp=timestamp + height,
                    coinbase_output=COINBASE,
                    nonce=height * 100,
                )
                self.assertTrue(first.accept_block(block))
            root_hash = first.dag.state_root()
            sequence = first.chain_store._record_count
            with sqlite3.connect(first.chain_store.path) as connection:
                connection.execute(
                    "UPDATE accepted_blocks SET state_root = ? WHERE sequence = ?", (root_hash, sequence)
                )
            first.chain_store.write_snapshot(first.dag, sequence, first.dag.selected_tip, root_hash)

            tail = mine_block(
                first.dag,
                parents=[first.dag.selected_tip],
                timestamp=timestamp + 4,
                coinbase_output=COINBASE,
                nonce=400,
            )
            self.assertTrue(first.accept_block(tail))
            expected_root = first.dag.state_root()

            restarted = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            self.assertEqual(restarted.replayed_blocks, 4)
            self.assertEqual(restarted.chain_store.loaded_snapshot_sequence, 3)
            self.assertEqual(restarted.chain_store.tail_replayed_blocks, 1)
            self.assertEqual(restarted.dag.state_root(), expected_root)

    def test_v2_snapshot_restores_prefix_and_replays_only_tail(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            for height in range(1, 4):
                block = mine_block(
                    first.dag,
                    parents=[first.dag.selected_tip],
                    timestamp=timestamp + height,
                    coinbase_output=COINBASE,
                    nonce=height * 103,
                )
                self.assertTrue(first.accept_block(block))
            root_hash = first.dag.state_root()
            sequence, checkpoint_hash = first.chain_store.seal_latest_checkpoint(root_hash)
            first.chain_store.write_snapshot_v2(
                first.dag, sequence, checkpoint_hash, root_hash
            )
            tail = mine_block(
                first.dag,
                parents=[first.dag.selected_tip],
                timestamp=timestamp + 4,
                coinbase_output=COINBASE,
                nonce=412,
            )
            self.assertTrue(first.accept_block(tail))
            expected_root = first.dag.state_root()

            restarted = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            self.assertEqual(restarted.replayed_blocks, 4)
            self.assertEqual(restarted.chain_store.loaded_snapshot_sequence, 3)
            self.assertEqual(restarted.chain_store.tail_replayed_blocks, 1)
            self.assertEqual(restarted.dag.state_root(), expected_root)

    def test_corrupt_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            block = mine_block(
                first.dag, parents=[first.dag.genesis_hash], timestamp=timestamp + 1,
                coinbase_output=COINBASE,
            )
            self.assertTrue(first.accept_block(block))
            root_hash = first.dag.state_root()
            with sqlite3.connect(first.chain_store.path) as connection:
                connection.execute("UPDATE accepted_blocks SET state_root = ? WHERE sequence = 1", (root_hash,))
            first.chain_store.write_snapshot(first.dag, 1, block.hash, root_hash)
            contents = bytearray(first.chain_store.snapshot_path.read_bytes())
            contents[len(contents) // 2] ^= 0x01
            first.chain_store.snapshot_path.write_bytes(contents)

            with self.assertRaisesRegex(ChainStoreError, "snapshot"):
                BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)

    def test_corrupt_v2_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            first = BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)
            timestamp = first.dag.blocks[first.dag.genesis_hash].timestamp
            block = mine_block(
                first.dag, parents=[first.dag.genesis_hash], timestamp=timestamp + 1,
                coinbase_output=COINBASE,
            )
            self.assertTrue(first.accept_block(block))
            root_hash = first.dag.state_root()
            sequence, checkpoint_hash = first.chain_store.seal_latest_checkpoint(root_hash)
            first.chain_store.write_snapshot_v2(
                first.dag, sequence, checkpoint_hash, root_hash
            )
            with sqlite3.connect(first.chain_store.snapshot_v2_path) as snapshot:
                snapshot.execute(
                    "UPDATE blocks SET consensus_json = '{}' WHERE block_hash = ?",
                    (block.hash,),
                )

            with self.assertRaisesRegex(ChainStoreError, "snapshot|state root"):
                BTCKDaemon(host="127.0.0.1", network="simnet", data_root=root, persistent=True)


if __name__ == "__main__":
    unittest.main()

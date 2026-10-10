"""Crash/restart recovery and network-bound chain database tests."""

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config.network import load_network
from node.chain_store import ChainStore, ChainStoreError
from node.daemon import BTCKDaemon
from tests.pow_helpers import mine_block


COINBASE = (bytes.fromhex("02" + "77" * 32), bytes.fromhex("03" + "88" * 32))


class TestChainPersistence(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

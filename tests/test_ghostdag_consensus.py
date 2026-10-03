"""Adversarial GHOSTDAG ordering and K-cluster regression tests."""

import unittest
from dataclasses import replace

from config.network import load_network
from node.blockdag import BlockDAG
from tests.pow_helpers import mine_block


COINBASE = (bytes.fromhex("02" + "11" * 32), bytes.fromhex("03" + "22" * 32))


def mine_child(dag, parents, timestamp, nonce=0):
    return mine_block(
        dag,
        parents=parents,
        timestamp=timestamp,
        coinbase_output=COINBASE,
        nonce=nonce,
    )


class TestGhostDAGConsensus(unittest.TestCase):
    def test_equal_work_tips_converge_independent_of_arrival_order(self):
        dag_a = BlockDAG(network="simnet")
        dag_b = BlockDAG(network="simnet")
        timestamp = dag_a.blocks[dag_a.genesis_hash].timestamp + 1
        left = mine_child(dag_a, [dag_a.genesis_hash], timestamp, 0)
        right = mine_child(dag_a, [dag_a.genesis_hash], timestamp, left.nonce + 1)
        left_clone = mine_child(dag_b, [dag_b.genesis_hash], timestamp, left.nonce)
        right_clone = mine_child(dag_b, [dag_b.genesis_hash], timestamp, right.nonce)
        self.assertEqual(left.hash, left_clone.hash)
        self.assertEqual(right.hash, right_clone.hash)

        dag_a.add_block(left)
        dag_a.add_block(right)
        dag_b.add_block(right_clone)
        dag_b.add_block(left_clone)

        self.assertEqual(dag_a.selected_tip, max(left.hash, right.hash))
        self.assertEqual(dag_a.selected_tip, dag_b.selected_tip)
        self.assertEqual(dag_a.state_root(), dag_b.state_root())

    def test_diamond_merge_is_blue_within_k_cluster(self):
        dag = BlockDAG(network="simnet")
        base_time = dag.blocks[dag.genesis_hash].timestamp
        left = mine_child(dag, [dag.genesis_hash], base_time + 1, 0)
        right = mine_child(dag, [dag.genesis_hash], base_time + 1, left.nonce + 1)
        dag.add_block(left)
        dag.add_block(right)
        merge = mine_child(dag, [left.hash, right.hash], base_time + 2, right.nonce + 1)
        dag.add_block(merge)

        selected_parent = max((left, right), key=lambda b: (b.blue_work, b.hash))
        other = right if selected_parent is left else left
        self.assertEqual(merge.selected_parent, selected_parent.hash)
        self.assertEqual(merge.merge_set, [other.hash])
        self.assertEqual(set(merge.merge_set_blues), {selected_parent.hash, other.hash})
        self.assertEqual(merge.merge_set_reds, [])
        self.assertEqual(merge.blue_anticone_sizes[selected_parent.hash], 1)
        self.assertEqual(merge.blue_anticone_sizes[other.hash], 1)

    def test_k_zero_marks_parallel_merge_block_red(self):
        network = replace(load_network("simnet"), ghostdag_k=0)
        dag = BlockDAG(network=network)
        base_time = dag.blocks[dag.genesis_hash].timestamp
        left = mine_child(dag, [dag.genesis_hash], base_time + 1, 0)
        right = mine_child(dag, [dag.genesis_hash], base_time + 1, left.nonce + 1)
        dag.add_block(left)
        dag.add_block(right)
        merge = mine_child(dag, [left.hash, right.hash], base_time + 2, right.nonce + 1)
        dag.add_block(merge)

        self.assertEqual(merge.merge_set_blues, [merge.selected_parent])
        self.assertEqual(len(merge.merge_set_reds), 1)
        self.assertEqual(merge.blue_score, dag.blocks[merge.selected_parent].blue_score + 1)
        red_hash = merge.merge_set_reds[0]
        self.assertNotIn(red_hash, dag.virtual_blue_order)
        self.assertEqual(dag.blocks[red_hash].minted_sats, 0)

    def test_merge_set_contains_transitive_non_selected_past(self):
        dag = BlockDAG(network="simnet")
        base_time = dag.blocks[dag.genesis_hash].timestamp
        left_1 = mine_child(dag, [dag.genesis_hash], base_time + 1, 0)
        dag.add_block(left_1)
        left_2 = mine_child(dag, [left_1.hash], base_time + 2, left_1.nonce + 1)
        dag.add_block(left_2)
        right_1 = mine_child(dag, [dag.genesis_hash], base_time + 1, left_2.nonce + 1)
        dag.add_block(right_1)
        merge = mine_child(dag, [left_2.hash, right_1.hash], base_time + 3, right_1.nonce + 1)
        dag.add_block(merge)

        if merge.selected_parent == left_2.hash:
            self.assertEqual(merge.merge_set, [right_1.hash])
        else:
            self.assertEqual(set(merge.merge_set), {left_1.hash, left_2.hash})


if __name__ == "__main__":
    unittest.main()

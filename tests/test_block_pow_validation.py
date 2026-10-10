"""Consensus regressions for canonical headers, targets and pre-state PoW checks."""

import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.pow import (
    InvalidTarget,
    compact_to_target,
    hash_meets_target,
    target_to_compact,
)
from node.blockdag import DAGBlock, BlockDAG
from tests.pow_helpers import mine_block


COINBASE = (bytes.fromhex("02" + "11" * 32), bytes.fromhex("03" + "22" * 32))


def find_invalid_pow_block(dag, *, nonce=0):
    for candidate_nonce in range(nonce, nonce + 100_000):
        block = DAGBlock(
            parents=[dag.genesis_hash],
            timestamp=dag.blocks[dag.genesis_hash].timestamp + 1,
            bits=dag.network.pow_limit_bits,
            nonce=candidate_nonce,
            coinbase_output=COINBASE,
            version=dag.network.block_version,
            chain_id=dag.network.chain_id,
        )
        if not hash_meets_target(
            bytes.fromhex(block.hash), block.bits, dag.network.pow_limit_bits
        ):
            return block
    raise AssertionError("Could not construct invalid test proof of work")


class TestProofOfWorkValidation(unittest.TestCase):
    def test_compact_target_round_trip_and_invalid_encoding(self):
        bits = 0x207FFFFF
        self.assertEqual(target_to_compact(compact_to_target(bits)), bits)
        with self.assertRaises(InvalidTarget):
            compact_to_target(0)
        with self.assertRaises(InvalidTarget):
            compact_to_target(0x20800001)

    def test_invalid_pow_is_rejected_before_any_state_change(self):
        dag = BlockDAG(network="simnet")
        block = find_invalid_pow_block(dag)
        before = (
            len(dag.blocks),
            set(dag.tips),
            dag.selected_tip,
            dag.issued_supply_sats,
            len(dag.utxo_set.utxos),
        )

        with self.assertRaisesRegex(ValueError, "proof of work"):
            dag.add_block(block)

        after = (
            len(dag.blocks),
            set(dag.tips),
            dag.selected_tip,
            dag.issued_supply_sats,
            len(dag.utxo_set.utxos),
        )
        self.assertEqual(after, before)

    def test_derived_blue_score_does_not_change_mined_hash(self):
        dag = BlockDAG(network="simnet")
        block = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=dag.blocks[dag.genesis_hash].timestamp + 1,
            coinbase_output=COINBASE,
        )
        mined_hash = block.hash

        self.assertTrue(dag.add_block(block))
        self.assertEqual(block.hash, mined_hash)
        self.assertEqual(block.compute_hash(), mined_hash)
        self.assertGreater(block.blue_score, 0)

    def test_wrong_bits_and_chain_id_are_rejected(self):
        dag = BlockDAG(network="simnet")
        valid = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=dag.blocks[dag.genesis_hash].timestamp + 1,
            coinbase_output=COINBASE,
        )

        valid.bits = 0x1D00FFFF
        valid.hash = valid.compute_hash()
        with self.assertRaisesRegex(ValueError, "target bits"):
            dag.add_block(valid)

        wrong_chain = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=dag.blocks[dag.genesis_hash].timestamp + 1,
            coinbase_output=COINBASE,
            nonce=100,
        )
        wrong_chain.chain_id = "attacker-chain"
        wrong_chain.hash = wrong_chain.compute_hash()
        with self.assertRaisesRegex(ValueError, "chain_id"):
            dag.add_block(wrong_chain)

    def test_payload_tampering_is_rejected(self):
        dag = BlockDAG(network="simnet")
        block = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=dag.blocks[dag.genesis_hash].timestamp + 1,
            coinbase_output=COINBASE,
        )
        block.payload_hash = b"\xff" * 32

        with self.assertRaisesRegex(ValueError, "payload commitment"):
            dag.add_block(block)


if __name__ == "__main__":
    unittest.main()

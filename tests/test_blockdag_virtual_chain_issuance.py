"""
Unit test for BlockDAG Virtual Selected Chain Issuance (TASK-P0-01 & P0-06)
--------------------------------------------------------------------------
Verifies:
1. Single-chain sequential issuance: adding parallel blocks at height 1 DOES NOT double-mint subsidy.
2. Convergence & Order-invariance: different network arrival order of parallel blocks yields identical convergent state and supply.
3. Coinbase maturity rule (120 blocks) prevents premature spending.
"""

import unittest
import sys
sys.path.insert(0, '/Users/a1234/btck-core/src')

from node.blockdag import BlockDAG, DAGBlock
from tokenomics.emission_schedule import get_base_block_reward_sats, SATS_PER_COIN
from privacy.wallet_engine import TxInput, TxOutput, StealthTransaction, schnorr_sign, sha256
from tests.pow_helpers import mine_block

class TestBlockDAGVirtualChainIssuance(unittest.TestCase):

    def setUp(self):
        self.cb1 = (bytes.fromhex("02" + "11"*32), bytes.fromhex("02" + "22"*32))
        self.cb2 = (bytes.fromhex("02" + "33"*32), bytes.fromhex("02" + "44"*32))
        self.cb3 = (bytes.fromhex("02" + "55"*32), bytes.fromhex("02" + "66"*32))

    def test_parallel_blocks_no_duplicate_minting(self):
        dag = BlockDAG()
        g_hash = dag.genesis_hash

        # Block 1A: parent is Genesis
        b1a = mine_block(dag,
            parents=[g_hash],
            timestamp=1788912001.0,
            nonce=101,
            coinbase_output=self.cb1
        )
        dag.add_block(b1a)
        
        expected_h1_reward = get_base_block_reward_sats(1)
        self.assertEqual(dag.issued_supply_sats, expected_h1_reward)
        self.assertEqual(len(dag.virtual_selected_parent_chain), 2)
        
        # Block 1B: parallel block, ALSO parent is Genesis
        b1b = mine_block(dag,
            parents=[g_hash],
            timestamp=1788912001.5,
            nonce=102,
            coinbase_output=self.cb2
        )
        dag.add_block(b1b)

        # CRITICAL P0-01 ASSERTION:
        # Parallel block 1B must NOT double-mint height 1's subsidy!
        self.assertEqual(dag.issued_supply_sats, expected_h1_reward,
                         f"Parallel block caused duplicate minting! {dag.issued_supply_sats} != {expected_h1_reward}")
        self.assertEqual(len(dag.blocks), 3)  # Genesis, 1A, 1B

    def test_arrival_order_convergence(self):
        # Scenario 1: 1A then 1B, then Block 2 merges [1A, 1B]
        dag1 = BlockDAG()
        g = dag1.genesis_hash
        b1a = mine_block(dag1, parents=[g], timestamp=1788912001.0, nonce=101, coinbase_output=self.cb1)
        b1b = mine_block(dag1, parents=[g], timestamp=1788912001.0, nonce=102, coinbase_output=self.cb2)
        dag1.add_block(b1a)
        dag1.add_block(b1b)
        b2_1 = mine_block(dag1, parents=[b1a.hash, b1b.hash], timestamp=1788912002.0, nonce=201, coinbase_output=self.cb3)
        dag1.add_block(b2_1)

        # Scenario 2: 1B then 1A, then Block 2 merges [1A, 1B]
        dag2 = BlockDAG()
        b1a_clone = mine_block(dag2, parents=[g], timestamp=1788912001.0, nonce=101, coinbase_output=self.cb1)
        b1b_clone = mine_block(dag2, parents=[g], timestamp=1788912001.0, nonce=102, coinbase_output=self.cb2)
        dag2.add_block(b1b_clone)
        dag2.add_block(b1a_clone)
        b2_2 = mine_block(dag2, parents=[b1a_clone.hash, b1b_clone.hash], timestamp=1788912002.0, nonce=201, coinbase_output=self.cb3)
        dag2.add_block(b2_2)

        # Both DAGs must have identical selected tip and identical issued supply
        self.assertEqual(dag1.selected_tip, dag2.selected_tip)
        self.assertEqual(dag1.issued_supply_sats, dag2.issued_supply_sats)
        self.assertEqual(
            dag1.issued_supply_sats,
            sum(get_base_block_reward_sats(height) for height in (1, 2, 3)),
        )
        self.assertEqual(dag1.virtual_blue_order, dag2.virtual_blue_order)

    def test_coinbase_maturity_lock(self):
        dag = BlockDAG()
        g = dag.genesis_hash

        # Block 1 mined
        b1 = mine_block(dag, parents=[g], timestamp=1788912001.0, nonce=101, coinbase_output=self.cb1)
        dag.add_block(b1)

        cb_outpoint = (f"cb_{b1.hash}", 0)
        self.assertTrue(dag.utxo_set.has_utxo(cb_outpoint[0], cb_outpoint[1]))

        # Try to spend Coinbase at height 2 (only 1 confirmation, requires 120!)
        premature_tx = StealthTransaction(
            version=1,
            inputs=[TxInput(prev_txid=cb_outpoint[0], prev_vout=cb_outpoint[1], amount_satk=b1.reward_sats, P_onetime_hex=self.cb1[1].hex(), signature_hex="00"*64)],
            outputs=[TxOutput(amount_satk=b1.reward_sats - 1000, R_ephem_hex=self.cb2[0].hex(), P_onetime_hex=self.cb2[1].hex())],
            lock_time=0
        )
        valid, err, _ = dag.utxo_set.validate_transaction(premature_tx, current_height=2)
        self.assertFalse(valid)
        self.assertIn("Coinbase maturity violation", err)

if __name__ == "__main__":
    unittest.main()

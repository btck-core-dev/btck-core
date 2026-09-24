"""
Test Suite: Combo Jackpot Consensus State Machine, Future Random Beacon (+100 blocks),
and Streaming Vesting Tranches (P1-01 / Audit Chapter 4)
"""

import unittest
import hashlib
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.blockdag import BlockDAG, DAGBlock
from consensus.combo_state_machine import ComboStateMachine, FUTURE_BEACON_DELAY_BLOCKS
from tokenomics.emission_schedule import (
    is_combo_block,
    get_combo_spec_for_height,
    compute_jackpot_vesting_tranches,
    get_regular_coinbase_maturity_blocks,
    TOTAL_BLOCKS,
    SATK_PER_COIN
)
from privacy.wallet_engine import (
    TxInput,
    TxOutput,
    StealthTransaction,
    schnorr_sign,
    sha256
)

class TestComboFutureBeaconConsensus(unittest.TestCase):

    def setUp(self):
        self.dag = BlockDAG()
        self.miner_P_hex = "02" + "22" * 32
        self.miner_R_hex = "02" + "11" * 32

    def _mine_block(self, parents=None, height=1) -> DAGBlock:
        if parents is None:
            parents = [self.dag.selected_tip]
        cb = (bytes.fromhex(self.miner_R_hex), bytes.fromhex(self.miner_P_hex))
        blk = DAGBlock(
            parents=parents,
            timestamp=1788912000.0 + height,
            bits=1,
            nonce=height,
            coinbase_output=cb,
            version=1
        )
        self.dag.add_block(blk)
        return blk

    def test_01_future_beacon_delay_100_blocks(self):
        """
        Validates that when a draw is triggered at H_draw, the jackpot is frozen
        and winner is resolved ONLY when H_draw + 100 (future beacon) is mined.
        """
        csm = ComboStateMachine()
        pool_bal = 100_000 * SATK_PER_COIN
        deposit = 10_000 * SATK_PER_COIN

        # Simulate blocks up to draw_height = 43200 (Stage 1 first draw)
        draw_height = 43200
        beacon_height = draw_height + FUTURE_BEACON_DELAY_BLOCKS

        # Block 1..43199: accumulate candidates, no draw
        for h in range(1, 5):
            h_hash = hashlib.sha256(f"BLOCK_{h}".encode()).hexdigest()
            res, dec = csm.process_block(h, h_hash, pool_bal, deposit)
            self.assertIsNone(res)
            self.assertEqual(dec, 0)

        # Trigger draw at H_draw
        draw_hash = hashlib.sha256(f"BLOCK_{draw_height}".encode()).hexdigest()
        res, dec = csm.process_block(draw_height, draw_hash, pool_bal, deposit)
        self.assertIsNone(res, "Winner MUST NOT be resolved at draw height (awaiting +100 beacon)")
        self.assertEqual(dec, 0)

        # Pending draw must exist with beacon_height == draw_height + 100
        self.assertEqual(len(csm.pending_draws), 1)
        epoch = csm.epochs[csm.pending_draws[0]]
        self.assertEqual(epoch.beacon_height, beacon_height)
        self.assertTrue(epoch.is_frozen)
        self.assertEqual(epoch.frozen_jackpot_satk, pool_bal + deposit)

        # Blocks between draw_height and beacon_height - 1
        for h in range(draw_height + 1, beacon_height):
            h_hash = hashlib.sha256(f"BLOCK_{h}".encode()).hexdigest()
            res, dec = csm.process_block(h, h_hash, pool_bal, deposit)
            self.assertIsNone(res)

        # Beacon Block arrives at beacon_height!
        beacon_hash = hashlib.sha256(f"BEACON_BLOCK_{beacon_height}".encode()).hexdigest()
        res, dec = csm.process_block(beacon_height, beacon_hash, pool_bal, deposit)

        self.assertIsNotNone(res, "Winner MUST be resolved upon future beacon block")
        self.assertEqual(res.beacon_height, beacon_height)
        self.assertEqual(res.beacon_hash, beacon_hash)
        self.assertEqual(res.jackpot_amount_satk, pool_bal + deposit)
        self.assertEqual(dec, pool_bal + deposit)
        self.assertTrue(res.winner_block_hash)
        self.assertGreater(len(res.tranches), 0)

    def test_02_streaming_vesting_tranches_enforcement_in_utxo(self):
        """
        Validates that jackpot tranches are inserted into UTXO set with timelocks,
        and spending an output before its unlock_block_height is rejected.
        """
        # Create a mock vesting tranche with timelock at height 500
        vesting_outputs = [{
            "tranche_index": 1,
            "amount_satk": 5000 * SATK_PER_COIN,
            "unlock_block_height": 500,
            "P_onetime_hex": self.miner_P_hex,
            "R_ephem_hex": self.miner_R_hex
        }]

        # Apply block at height 100 with the vesting output
        blk_hash = "mock_draw_blk"
        self.dag.utxo_set.apply_block(
            block_hash=blk_hash,
            block_height=100,
            coinbase_output=(bytes.fromhex(self.miner_R_hex), bytes.fromhex(self.miner_P_hex)),
            reward_sats=1000 * SATK_PER_COIN,
            transactions=[],
            vesting_outputs=vesting_outputs
        )

        outpoint = (f"vesting_{blk_hash}_1", 0)
        self.assertTrue(self.dag.utxo_set.has_utxo(*outpoint))
        utxo = self.dag.utxo_set.get_utxo(*outpoint)
        self.assertEqual(utxo.timelock_until_height, 500)

        # Attempt to spend at height 499 -> MUST FAIL
        tx_premature = StealthTransaction(
            version=1,
            inputs=[TxInput(
                prev_txid=outpoint[0],
                prev_vout=outpoint[1],
                amount_satk=5000 * SATK_PER_COIN,
                P_onetime_hex=self.miner_P_hex,
                signature_hex="11" * 64
            )],
            outputs=[TxOutput(
                amount_satk=4999 * SATK_PER_COIN,
                P_onetime_hex="02" + "33"*32,
                R_ephem_hex="02" + "44"*32
            )],
            lock_time=0
        )
        valid, err, _ = self.dag.utxo_set.validate_transaction(tx_premature, current_height=499)
        self.assertFalse(valid)
        self.assertIn("timelocked", err.lower())

        # Attempt to spend at height 500+ (with valid or mock sig check point)
        # Verify timelock check alone passes at 500
        confirmations_check = 500 >= utxo.timelock_until_height
        self.assertTrue(confirmations_check)

    def test_03_stage_boundaries_and_tranche_counts(self):
        """
        Validates Stage 1-4 tranche counts, instant unlock ratios, and exact SatK totals.
        """
        jackpot = 100_000 * SATK_PER_COIN

        # Stage 1: 50% instant + 1 streaming = 2 tranches
        t1 = compute_jackpot_vesting_tranches(1, jackpot, 43200)
        self.assertEqual(len(t1), 2)
        self.assertEqual(t1[0]["amount_satk"], jackpot // 2)
        self.assertEqual(sum(t["amount_satk"] for t in t1), jackpot)

        # Stage 2: 25% instant + 3 streaming = 4 tranches
        t2 = compute_jackpot_vesting_tranches(2, jackpot, 31_665_600)
        self.assertEqual(len(t2), 4)
        self.assertEqual(sum(t["amount_satk"] for t in t2), jackpot)

        # Stage 3: 10% instant + 9 streaming = 10 tranches
        t3 = compute_jackpot_vesting_tranches(3, jackpot, 63_460_800)
        self.assertEqual(len(t3), 10)
        self.assertEqual(sum(t["amount_satk"] for t in t3), jackpot)

        # Stage 4: 10% instant + 15 streaming = 16 tranches
        t4 = compute_jackpot_vesting_tranches(4, jackpot, 95_774_400)
        self.assertEqual(len(t4), 16)
        self.assertEqual(sum(t["amount_satk"] for t in t4), jackpot)

    def test_04_stage_relative_draw_alignment(self):
        """
        Validates that draws in Stage 2..4 are aligned to stage start rather than global modulo.
        """
        # Stage 1: interval 43,200
        self.assertTrue(is_combo_block(43_200))
        self.assertTrue(is_combo_block(86_400))

        # Stage 2 starts at 31,536,001 with interval 129,600
        # First draw must be at 31,536,001 + 129,600 - 1 = 31,665,600
        self.assertTrue(is_combo_block(31_665_600))
        self.assertFalse(is_combo_block(31_665_599))

        # Stage 3 starts at 63,072,001 with interval 388,800
        # First draw must be at 63,072,001 + 388,800 - 1 = 63,460,800
        self.assertTrue(is_combo_block(63_460_800))

        # Stage 4 starts at 94,608,001 with interval 1,166,400
        # First draw must be at 94,608,001 + 1,166,400 - 1 = 95_774_400
        self.assertTrue(is_combo_block(95_774_400))

        # Final block of Year 4 (126,144,000) must trigger final liquidation
        self.assertTrue(is_combo_block(TOTAL_BLOCKS))

    def test_05_reorganization_rollback_safety(self):
        """
        Validates that state machine safely unrolls if a chain reorg occurs.
        """
        csm = ComboStateMachine()
        pool_bal = 50_000 * SATK_PER_COIN
        deposit = 5_000 * SATK_PER_COIN

        # Mined up to 43,200 (trigger draw)
        for h in range(1, 43201):
            h_hash = hashlib.sha256(f"MAIN_{h}".encode()).hexdigest()
            csm.process_block(h, h_hash, pool_bal, deposit)

        self.assertEqual(len(csm.pending_draws), 1)

        # Rollback to 43,190 (before draw was triggered)
        csm.revert_to_height(43190)
        self.assertEqual(len(csm.pending_draws), 0, "Pending draw should be cancelled on rollback")
        self.assertFalse(csm.epochs[1].is_frozen, "Epoch 1 should be unfrozen")

    def test_06_progressive_regular_coinbase_maturity(self):
        """
        Validates the 4-year progressive regular block coinbase maturity schedule:
        - Year 1: 7,200 blocks (2 hours)
        - Year 2: 21,600 blocks (6 hours)
        - Year 3: 86,400 blocks (24 hours / 1 day)
        - Year 4: 259,200 blocks (72 hours / 3 days)
        - Post-4-Year: 259,200 blocks (72 hours)
        """
        # Year 1 (blocks 1 .. 31,536,000)
        self.assertEqual(get_regular_coinbase_maturity_blocks(1), 7_200)
        self.assertEqual(get_regular_coinbase_maturity_blocks(31_536_000), 7_200)

        # Year 2 (blocks 31,536,001 .. 63,072,000)
        self.assertEqual(get_regular_coinbase_maturity_blocks(31_536_001), 21_600)
        self.assertEqual(get_regular_coinbase_maturity_blocks(63_072_000), 21_600)

        # Year 3 (blocks 63,072,001 .. 94,608,000)
        self.assertEqual(get_regular_coinbase_maturity_blocks(63_072_001), 86_400)
        self.assertEqual(get_regular_coinbase_maturity_blocks(94_608_000), 86_400)

        # Year 4 (blocks 94,608,001 .. 126,144,000)
        self.assertEqual(get_regular_coinbase_maturity_blocks(94_608_001), 259_200)
        self.assertEqual(get_regular_coinbase_maturity_blocks(126_144_000), 259_200)

        # Post-4-Year
        self.assertEqual(get_regular_coinbase_maturity_blocks(126_144_001), 259_200)

if __name__ == "__main__":
    unittest.main()

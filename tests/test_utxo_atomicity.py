"""Atomic selected-chain UTXO and reorganization regression tests."""

import copy
import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.blockdag import BlockDAG
from node.utxo_set import UTXOEntry, UTXOSet
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthTransaction, StealthWallet, TxInput, TxOutput
from tests.pow_helpers import mine_block


COINBASE_A = (bytes.fromhex("02" + "11" * 32), bytes.fromhex("03" + "22" * 32))
COINBASE_B = (bytes.fromhex("02" + "33" * 32), bytes.fromhex("03" + "44" * 32))


def make_two_conflicting_transactions():
    owner = StealthWallet.generate("owner")
    recipient_a = StealthWallet.generate("recipient-a")
    recipient_b = StealthWallet.generate("recipient-b")
    prev_txid = "ab" * 32
    amount = 1_000_000
    r_point, p_point = StealthKeyPair.generate_one_time_stealth_output(owner.address)
    owner.scan_chain_outputs([{
        "txid": prev_txid,
        "vout": 0,
        "amount_satk": amount,
        "R_ephem_hex": r_point.hex(),
        "P_onetime_hex": p_point.hex(),
        "height": 1,
    }])
    tx_a = owner.create_and_sign_transfer(recipient_a.address, 500_000, fee_satk=1_000)
    owner.utxos[0].spent = False
    tx_b = owner.create_and_sign_transfer(recipient_b.address, 400_000, fee_satk=1_000)
    entry = UTXOEntry(
        amount_satk=amount,
        P_onetime_hex=p_point.hex(),
        R_ephem_hex=r_point.hex(),
        block_height=1,
        is_coinbase=False,
    )
    return (prev_txid, 0), entry, tx_a, tx_b


class TestUTXOAtomicity(unittest.TestCase):
    def test_cross_transaction_double_spend_leaves_no_partial_state(self):
        utxos = UTXOSet()
        outpoint, entry, tx_a, tx_b = make_two_conflicting_transactions()
        utxos.utxos[outpoint] = entry
        before = copy.deepcopy(utxos.utxos)

        applied, error = utxos.apply_block(
            block_hash="ff" * 32,
            block_height=10,
            coinbase_output=COINBASE_A,
            reward_sats=123,
            transactions=[tx_a, tx_b],
        )

        self.assertFalse(applied)
        self.assertIn("already spent", error)
        self.assertEqual(utxos.utxos, before)
        self.assertEqual(utxos._block_spent_undo, {})
        self.assertEqual(utxos._block_created_undo, {})

    def test_side_branch_does_not_touch_selected_utxo_view(self):
        dag = BlockDAG()
        genesis_time = dag.blocks[dag.genesis_hash].timestamp
        selected = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=genesis_time + 1,
            coinbase_output=COINBASE_A,
        )
        dag.add_block(selected)
        before_utxos = copy.deepcopy(dag.utxo_set.utxos)
        before_undo = copy.deepcopy(dag.utxo_set._block_created_undo)

        side = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=genesis_time + 2,
            coinbase_output=COINBASE_B,
            nonce=100,
        )
        dag.add_block(side)

        self.assertEqual(dag.selected_tip, selected.hash)
        self.assertEqual(dag.utxo_set.utxos, before_utxos)
        self.assertEqual(dag.utxo_set._block_created_undo, before_undo)

    def test_failed_reorg_restores_every_consensus_view(self):
        dag = BlockDAG()
        genesis_time = dag.blocks[dag.genesis_hash].timestamp
        selected = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=genesis_time + 1,
            coinbase_output=COINBASE_A,
        )
        dag.add_block(selected)

        invalid_tx = StealthTransaction(
            version=1,
            inputs=[TxInput(
                prev_txid="ee" * 32,
                prev_vout=0,
                amount_satk=100,
                P_onetime_hex=COINBASE_A[1].hex(),
                signature_hex="00" * 65,
            )],
            outputs=[TxOutput(
                amount_satk=90,
                R_ephem_hex=COINBASE_B[0].hex(),
                P_onetime_hex=COINBASE_B[1].hex(),
            )],
            lock_time=0,
        )
        invalid_tx.finalize()
        side = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=genesis_time + 2,
            coinbase_output=COINBASE_B,
            transactions=[invalid_tx],
            nonce=100,
        )
        dag.add_block(side)
        before = (
            set(dag.blocks),
            set(dag.tips),
            dag.selected_tip,
            dag.issued_supply_sats,
            copy.deepcopy(dag.utxo_set.utxos),
        )

        child = mine_block(
            dag,
            parents=[side.hash],
            timestamp=genesis_time + 3,
            coinbase_output=COINBASE_B,
            nonce=200,
        )
        with self.assertRaisesRegex(ValueError, "invalid transaction"):
            dag.add_block(child)

        after = (
            set(dag.blocks),
            set(dag.tips),
            dag.selected_tip,
            dag.issued_supply_sats,
            dag.utxo_set.utxos,
        )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

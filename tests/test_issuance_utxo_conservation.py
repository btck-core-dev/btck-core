"""Every issued SatK must exist in an explicit, reversible UTXO."""

import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.blockdag import BlockDAG
from node.utxo_set import UTXOEntry, UTXOSet
from tests.pow_helpers import mine_block
from tokenomics.emission_schedule import compute_block_issuance
from tokenomics.emission_schedule import TOTAL_BLOCKS, TOTAL_HARD_CAP_SATK
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet


MINER_OUTPUT = (bytes.fromhex("02" + "11" * 32), bytes.fromhex("03" + "22" * 32))


class TestIssuanceUTXOConservation(unittest.TestCase):
    def test_every_new_sat_exists_in_an_explicit_utxo(self):
        dag = BlockDAG(network="simnet")
        genesis_time = dag.blocks[dag.genesis_hash].timestamp
        block = mine_block(
            dag,
            parents=[dag.genesis_hash],
            timestamp=genesis_time + 1,
            coinbase_output=MINER_OUTPUT,
        )
        dag.add_block(block)

        self.assertEqual(dag.utxo_set.total_value_sats(), dag.issued_supply_sats)
        self.assertEqual(
            dag.utxo_set.value_by_kind_sats("jackpot_pool"),
            dag.pool_balance_sats,
        )
        self.assertEqual(
            dag.utxo_set.value_by_kind_sats("eco"),
            block.eco_deposit_sats,
        )
        self.assertEqual(
            dag.utxo_set.value_by_kind_sats("miner"),
            block.miner_base_sats,
        )
        self.assertEqual(
            block.miner_base_sats
            + block.eco_deposit_sats
            + block.pool_deposit_sats,
            block.minted_sats,
        )

    def test_scheduled_draw_does_not_pay_before_future_beacon(self):
        opening_pool = 123_456
        result = compute_block_issuance(
            block_height=43_200,
            block_hash=b"\x01" * 32,
            current_issued_sats=0,
            current_pool_balance_sats=opening_pool,
            force_combo=True,
        )

        self.assertTrue(result.is_combo)
        self.assertEqual(result.combo_payout_sats, 0)
        self.assertEqual(
            result.new_pool_balance_sats,
            opening_pool + result.pool_deposit_sats,
        )

    def test_pool_to_vesting_conversion_is_exact_and_reversible(self):
        utxos = UTXOSet()
        utxos.utxos[("pool_a", 0)] = UTXOEntry(
            amount_satk=60,
            P_onetime_hex="",
            R_ephem_hex="",
            block_height=1,
            is_coinbase=True,
            kind="jackpot_pool",
        )
        utxos.utxos[("pool_b", 0)] = UTXOEntry(
            amount_satk=40,
            P_onetime_hex="",
            R_ephem_hex="",
            block_height=2,
            is_coinbase=True,
            kind="jackpot_pool",
        )
        before_total = utxos.total_value_sats()
        vesting = [
            {
                "tranche_index": 0,
                "amount_satk": 70,
                "unlock_block_height": 10,
                "R_ephem_hex": MINER_OUTPUT[0].hex(),
                "P_onetime_hex": MINER_OUTPUT[1].hex(),
            },
            {
                "tranche_index": 1,
                "amount_satk": 30,
                "unlock_block_height": 20,
                "R_ephem_hex": MINER_OUTPUT[0].hex(),
                "P_onetime_hex": MINER_OUTPUT[1].hex(),
            },
        ]

        applied, error = utxos.apply_block(
            block_hash="cc" * 32,
            block_height=3,
            coinbase_output=MINER_OUTPUT,
            reward_sats=0,
            transactions=[],
            vesting_outputs=vesting,
            pool_payout_sats=100,
        )

        self.assertTrue(applied, error)
        self.assertEqual(utxos.value_by_kind_sats("jackpot_pool"), 0)
        self.assertEqual(utxos.value_by_kind_sats("vesting"), 100)
        self.assertEqual(utxos.total_value_sats(), before_total)

        utxos.revert_block("cc" * 32)
        self.assertEqual(utxos.value_by_kind_sats("jackpot_pool"), 100)
        self.assertEqual(utxos.value_by_kind_sats("vesting"), 0)
        self.assertEqual(utxos.total_value_sats(), before_total)

    def test_post_mining_fees_are_split_into_miner_and_combo_utxos(self):
        utxos = UTXOSet()
        sender = StealthWallet.generate("post-mining-sender")
        r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(sender.address)
        txid = "ab" * 32
        utxos.utxos[(txid, 0)] = UTXOEntry(
            amount_satk=1_000_000,
            P_onetime_hex=p_onetime.hex(),
            R_ephem_hex=r_ephem.hex(),
            block_height=1,
        )
        sender.scan_chain_outputs([{
            "txid": txid,
            "vout": 0,
            "amount_satk": 1_000_000,
            "P_onetime_hex": p_onetime.hex(),
            "R_ephem_hex": r_ephem.hex(),
            "height": 1,
        }])
        recipient = StealthWallet.generate("post-mining-recipient")
        tx = sender.create_and_sign_transfer(recipient.address, 400_000, 101)
        issuance = compute_block_issuance(
            TOTAL_BLOCKS + 1,
            b"\x22" * 32,
            TOTAL_HARD_CAP_SATK,
            0,
            total_fee_sats=101,
        )
        applied, error = utxos.apply_block(
            block_hash="dd" * 32,
            block_height=TOTAL_BLOCKS + 1,
            coinbase_output=MINER_OUTPUT,
            reward_sats=0,
            transactions=[tx],
            pool_deposit_sats=issuance.pool_deposit_sats,
            expected_miner_fee_sats=issuance.miner_fee_sats,
            fee_pool_deposit_sats=issuance.combo_fee_deposit_sats,
        )
        self.assertTrue(applied, error)
        self.assertEqual(utxos.value_by_kind_sats("miner"), 51)
        self.assertEqual(utxos.value_by_kind_sats("jackpot_pool"), 50)
        self.assertEqual(utxos.total_value_sats(), 1_000_000)

if __name__ == "__main__":
    unittest.main()

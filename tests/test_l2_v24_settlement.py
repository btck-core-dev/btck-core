"""Regression tests for the frozen v2.4 L2 five-way settlement."""

import os
import sys
import tempfile
import time
import unittest
import json
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../sdk")))

from gateway.ai_rpc_gateway import AgentLedger
from worker.pricing_engine import TieredPricingEngine
from tokenomics.emission_schedule import BLOCKS_PER_YEAR


class TestL2V24Settlement(unittest.TestCase):
    EXPECTED = {
        1: (60, 10, 15, 5, 10),
        2: (50, 10, 25, 10, 5),
        3: (40, 10, 30, 15, 5),
        4: (35, 5, 40, 20, 0),
    }

    def make_ledger(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return AgentLedger(os.path.join(temp.name, "ledger.json"))

    def settle(self, ledger, settlement_id, payer="payer", cost=1, year=1):
        return ledger.settle_l2_payment(
            settlement_id=settlement_id,
            payer=payer,
            cost_satk=cost,
            provider_address="provider",
            standby_account="standby",
            combo_account="combo",
            ecosystem_account="ecosystem",
            network_year=year,
        )

    def test_exact_schedule_for_each_year(self):
        for year, expected in self.EXPECTED.items():
            with self.subTest(year=year):
                split = TieredPricingEngine.calculate_revenue_split(100, year)
                actual = tuple(split[key] for key in (
                    "miner_satk", "standby_satk", "combo_satk", "burn_satk", "eco_satk"
                ))
                self.assertEqual(actual, expected)
                self.assertEqual(sum(actual), 100)

    def test_network_year_is_derived_from_canonical_l1_height(self):
        cases = {
            0: 1,
            1: 1,
            BLOCKS_PER_YEAR: 1,
            BLOCKS_PER_YEAR + 1: 2,
            2 * BLOCKS_PER_YEAR: 2,
            2 * BLOCKS_PER_YEAR + 1: 3,
            3 * BLOCKS_PER_YEAR: 3,
            3 * BLOCKS_PER_YEAR + 1: 4,
            4 * BLOCKS_PER_YEAR: 4,
            4 * BLOCKS_PER_YEAR + 1: 4,
        }
        for height, expected_year in cases.items():
            with self.subTest(height=height):
                self.assertEqual(TieredPricingEngine.network_year_for_height(height), expected_year)
        for invalid in (-1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    TieredPricingEngine.network_year_for_height(invalid)

    def test_live_gpu_demo_uses_canonical_split_without_legacy_85_percent(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "chat_live_gpu.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("TieredPricingEngine.calculate_revenue_split", source)
        self.assertIn("TieredPricingEngine.network_year_for_height", source)
        self.assertNotIn("BTCK_L2_NETWORK_YEAR", source)
        self.assertNotIn("total_satk * 0.85", source)
        self.assertNotIn("实收 (85%)", source)

    def test_one_satk_payments_converge_to_exact_year_one_percentages(self):
        ledger = self.make_ledger()
        ledger.deposit("payer", 100)
        for index in range(100):
            result = self.settle(ledger, f"job-{index}")
            self.assertEqual(result["conserved_satk"], 1)

        self.assertEqual(ledger.get_balance("payer"), 0)
        self.assertEqual(ledger.get_balance("provider"), 60)
        self.assertEqual(ledger.get_balance("standby"), 10)
        self.assertEqual(ledger.get_balance("combo"), 15)
        self.assertEqual(ledger.l2_burned_satk, 5)
        self.assertEqual(ledger.get_balance("ecosystem"), 10)

    def test_sandbox_voucher_cannot_become_provider_income(self):
        ledger = self.make_ledger()
        ledger.vouchers["payer"] = {
            "remaining_satk": 10,
            "expires_at": time.time() + 60,
            "status": "ACTIVE",
        }
        result = self.settle(ledger, "voucher-job", cost=10)

        self.assertEqual(result["sandbox_voucher_satk"], 10)
        self.assertEqual(result["paid_satk"], 0)
        self.assertEqual(result["conserved_satk"], 0)
        self.assertEqual(ledger.get_balance("provider"), 0)
        self.assertEqual(ledger.get_balance("standby"), 0)
        self.assertEqual(ledger.get_balance("combo"), 0)
        self.assertEqual(ledger.get_balance("ecosystem"), 0)
        self.assertEqual(ledger.l2_burned_satk, 0)

    def test_persistent_settlement_id_prevents_double_payment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "ledger.json")
            ledger = AgentLedger(path)
            ledger.deposit("payer", 100)
            first = self.settle(ledger, "same-job", cost=100)
            balances_after_first = dict(ledger.balances)
            burned_after_first = ledger.l2_burned_satk

            # Re-open the ledger to prove the replay guard survives a restart.
            reloaded = AgentLedger(path)
            replay = self.settle(reloaded, "same-job", cost=100)
            self.assertFalse(first["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(reloaded.balances, balances_after_first)
            self.assertEqual(reloaded.l2_burned_satk, burned_after_first)

            with self.assertRaisesRegex(ValueError, "different payment details"):
                self.settle(reloaded, "same-job", cost=99)

    def test_corrupt_or_negative_persistent_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "ledger.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{torn")
            with self.assertRaisesRegex(RuntimeError, "Refusing to start"):
                AgentLedger(path)

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"balances": {"agent": -1}}, handle)
            with self.assertRaisesRegex(RuntimeError, "negative paid balance"):
                AgentLedger(path)


if __name__ == "__main__":
    unittest.main()

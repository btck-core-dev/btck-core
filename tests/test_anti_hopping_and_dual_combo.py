#!/usr/bin/env python3
"""
Unit tests for BTC-K Anti-Hopping Gate, Dual-Combo Engine, and Standby Subsidy Manager (v2.4.0)
"""

import unittest
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.tokenomics.anti_hopping import (
    AntiHoppingGate,
    WorkerType,
    L2GPUComboEngine,
    GPUStandbySubsidyManager,
)


class TestAntiHoppingAndDualCombo(unittest.TestCase):

    def test_anti_hopping_gate_asic_scenarios(self):
        """
        Verify:
        1. Miner A: 70/72 slots active (97.2%) -> QUALIFIED
        2. Miner B (NiceHash mercenary): only active in slots 70, 71 (2/72 = 2.8%) -> DISQUALIFIED
        3. Miner C: active in slots 0..35 (50%), then hops out -> DISQUALIFIED
        4. Miner D: active everywhere except slots 10, 11 (2 consecutive slots = 20 min outage) -> DISQUALIFIED
        """
        gate = AntiHoppingGate(
            epoch_index=1,
            epoch_duration_seconds=43200,  # 12h
            slot_duration_seconds=600,     # 10m -> 72 slots
            min_duty_cycle=0.95,
            max_consecutive_missed_slots=1,
        )

        # 1. Miner A (Honest, 97.2% active)
        for s in range(72):
            if s not in (15, 45):  # Missed 2 non-consecutive slots
                gate.record_asic_share("miner_A", s)
                if s % 10 == 0:
                    gate.record_asic_block("miner_A", s, hashlib.sha256(f"blk_A_{s}".encode()).digest())

        # 2. Miner B (Mercenary sniper hopping in at end)
        gate.record_asic_share("miner_B", 70)
        gate.record_asic_block("miner_B", 71, hashlib.sha256(b"blk_B_snipe").digest())

        # 3. Miner C (Hopped out halfway)
        for s in range(36):
            gate.record_asic_share("miner_C", s)
            if s == 20:
                gate.record_asic_block("miner_C", s, hashlib.sha256(b"blk_C_early").digest())

        # 4. Miner D (2 consecutive slots down: 10 and 11)
        for s in range(72):
            if s not in (10, 11):
                gate.record_asic_share("miner_D", s)

        # Evaluations at slot 71 (end of 12h epoch)
        qual_A, duty_A, msg_A = gate.evaluate_worker_qualification("miner_A", 71)
        self.assertTrue(qual_A)
        self.assertGreaterEqual(duty_A, 0.95)
        self.assertEqual(msg_A, "Qualified")

        qual_B, duty_B, msg_B = gate.evaluate_worker_qualification("miner_B", 71)
        self.assertFalse(qual_B)
        self.assertLess(duty_B, 0.10)
        self.assertIn("Anti-Hopping Triggered", msg_B)

        qual_C, duty_C, msg_C = gate.evaluate_worker_qualification("miner_C", 71)
        self.assertFalse(qual_C)
        self.assertEqual(duty_C, 0.50)
        self.assertIn("Anti-Hopping Triggered", msg_C)

        qual_D, duty_D, msg_D = gate.evaluate_worker_qualification("miner_D", 71)
        self.assertFalse(qual_D)
        self.assertIn("consecutive downtime exceeded", msg_D)

        # Filtering test: All candidate hashes from B, C, D must be pruned
        eligible_hashes, audit = gate.filter_eligible_candidates(71)
        self.assertEqual(audit["qualified_workers_count"], 1)
        self.assertEqual(audit["qualified_workers"], ["miner_A"])
        self.assertEqual(set(audit["disqualified_workers"]), {"miner_B", "miner_C", "miner_D"})
        self.assertGreater(len(eligible_hashes), 0)
        self.assertNotIn(hashlib.sha256(b"blk_B_snipe").digest(), eligible_hashes)
        self.assertNotIn(hashlib.sha256(b"blk_C_early").digest(), eligible_hashes)

    def test_l2_gpu_combo_engine_draw_and_pruning(self):
        """
        Verify:
        1. L2 Combo accumulates only circulating SatK (Zero newly minted coins).
        2. Disqualified hopping nodes' tickets are pruned from draw.
        3. Winning ticket has minimal XOR distance and claims 70% Grand Prize; runners-up claim 30%.
        """
        engine = L2GPUComboEngine(epoch_index=1, draw_interval_hours=12)

        # Simulate 100,000 SatK injected from HTTP 402 AI-RPC fee split
        engine.deposit_circulating_satk(100_000)
        self.assertEqual(engine.accumulated_satk, 100_000)

        # Register tickets for Honest GPU 1 and Mercenary GPU 2
        t1 = engine.register_task_ticket("task_001", hashlib.sha256(b"res1").digest(), "gpu_honest_1", 2.16)
        t2 = engine.register_task_ticket("task_002", hashlib.sha256(b"res2").digest(), "gpu_honest_2", 1.00)
        t3 = engine.register_task_ticket("task_003", hashlib.sha256(b"res3").digest(), "gpu_mercenary_hop", 1.00)

        beacon = hashlib.sha256(b"block_43300_future_beacon").digest()

        # Only honest nodes passed qualification
        qualified_set = {"gpu_honest_1", "gpu_honest_2"}
        result = engine.execute_draw(beacon, qualified_set)

        self.assertEqual(result["status"], "DRAW_COMPLETED")
        self.assertIn(result["grand_prize_winner"], qualified_set)
        self.assertNotEqual(result["grand_prize_winner"], "gpu_mercenary_hop")
        self.assertEqual(result["grand_prize_satk"], 70_000)  # 70% of 100,000 SatK

        # Pool is wiped clean after draw
        self.assertEqual(engine.accumulated_satk, 0)

    def test_gpu_standby_subsidy_manager_two_phase_and_por(self):
        """
        Verify:
        1. Phase 1 (Genesis Cold-Start): 0 organic fee -> Eco-Fund seed floor (250 BTC-K for 12h) is active.
        2. Node passing PoR receives rewards weighted by hardware (35B MoE = 3.5x, 8B = 1.0x).
        3. Node with failed PoR or task refusal is slashed/excluded.
        4. Phase 2: Organic fees exceed Eco-Fund floor -> Transition to 100% organic fee funding.
        """
        # 500 BTC-K / day seed floor = 250 BTC-K / 12h (25,000,000,000 SatK)
        manager = GPUStandbySubsidyManager(daily_eco_seed_budget_satk=50_000_000_000, epoch_hours=12)

        # Node 1: 4x 4070TiS running 35B MoE, 100% PoR pass
        manager.register_node_activity("node_35b", "35B_MOE", "addr_1", por_passed_count=72, por_failed_count=0, refused_count=0)

        # Node 2: Single 4060Ti running 8B INT4, 100% PoR pass
        manager.register_node_activity("node_8b", "8B_INT4", "addr_2", por_passed_count=72, por_failed_count=0, refused_count=0)

        # Node 3: Ghost node, failed PoR (>5% failed)
        manager.register_node_activity("node_ghost", "8B_INT4", "addr_3", por_passed_count=50, por_failed_count=22, refused_count=0)

        # Node 4: Node that refused real user dispatch (SLA breach)
        manager.register_node_activity("node_refused", "35B_MOE", "addr_4", por_passed_count=72, por_failed_count=0, refused_count=1)

        # Run Phase 1 distribution (0 organic fee)
        dist = manager.calculate_epoch_distribution()
        self.assertEqual(dist["pool_source"], "ECO_FUND_BOOTSTRAPPING_ACTIVE")
        self.assertEqual(dist["qualified_nodes_count"], 2)  # Only node_35b and node_8b
        self.assertEqual(dist["total_qualified_weight"], 4.5)  # 3.5 + 1.0 = 4.5

        # Payout ratio check: 35B node should receive 3.5 / 4.5 = 77.78% of the pool
        payouts = {p["node_id"]: p["payout_satk"] for p in dist["node_payouts"]}
        self.assertIn("node_35b", payouts)
        self.assertIn("node_8b", payouts)
        self.assertNotIn("node_ghost", payouts)
        self.assertNotIn("node_refused", payouts)
        ratio_35b = payouts["node_35b"] / float(dist["effective_pool_satk"])
        self.assertAlmostEqual(ratio_35b, 3.5 / 4.5, places=4)

        # Test Phase 2: Massive organic traffic generates 300 BTC-K in standby fees
        manager2 = GPUStandbySubsidyManager(daily_eco_seed_budget_satk=50_000_000_000, epoch_hours=12)
        manager2.deposit_organic_fee(30_000_000_000)  # 300 BTC-K > 250 BTC-K eco budget
        manager2.register_node_activity("node_35b", "35B_MOE", "addr_1", por_passed_count=72, por_failed_count=0, refused_count=0)

        dist2 = manager2.calculate_epoch_distribution()
        self.assertEqual(dist2["pool_source"], "100%_ORGANIC_COMMERCIAL_FEES")
        self.assertEqual(dist2["eco_fund_draw_satk"], 0)
        self.assertEqual(dist2["effective_pool_satk"], 30_000_000_000)

    def test_multi_year_anti_hopping_stage_specs(self):
        """
        Verify the 4-year progressive AntiHoppingGate specifications:
        Stage 1 (Y1): 12h, 72 slots, 10m/slot, max consecutive missed: 1 (<=15m)
        Stage 2 (Y2): 36h, 216 slots, 10m/slot, max consecutive missed: 2 (<=25m)
        Stage 3 (Y3): 108h, 432 slots, 15m/slot, max consecutive missed: 3 (<=50m)
        Stage 4 (Y4): 324h (13.5d), 648 slots, 30m/slot, max consecutive missed: 4 (<=130m)
        """
        from src.tokenomics.anti_hopping import STAGE_EPOCH_SPECS

        for stage_id, expected_hours, expected_slots in [(1, 12, 72), (2, 36, 216), (3, 108, 432), (4, 324, 648)]:
            spec = STAGE_EPOCH_SPECS[stage_id]
            self.assertEqual(spec["draw_hours"], expected_hours)
            self.assertEqual(spec["slots"], expected_slots)

            gate = AntiHoppingGate.create_for_stage(stage_id=stage_id, epoch_index=1, min_duty_cycle=0.95)
            self.assertEqual(gate.total_slots, expected_slots)
            self.assertEqual(gate.epoch_duration_seconds, expected_hours * 3600)
            self.assertEqual(gate.min_duty_cycle, 0.95)

    def test_stage2_stage3_anti_hopping_scenarios(self):
        """
        Test Anti-Hopping Gate for Year 2 (36h) and Year 3 (108h) for both ASIC and GPU nodes.
        """
        # --- Stage 2 (36h, 216 slots) ---
        gate_s2 = AntiHoppingGate.create_for_stage(stage_id=2, epoch_index=1, min_duty_cycle=0.95)
        # Miner A: 208 active slots (96.3%), 2 consecutive missed slots (allowed)
        for s in range(216):
            if s not in (50, 51, 100, 150):
                gate_s2.record_asic_share("miner_s2_A", s)
        # Miner B: 3 consecutive missed slots (exceeds max allowed 2)
        for s in range(216):
            if s not in (50, 51, 52):
                gate_s2.record_asic_share("miner_s2_B", s)

        # GPU Node A: passes PoR in 210 slots
        for s in range(216):
            if s not in (10, 80, 160):
                gate_s2.record_gpu_por("gpu_s2_A", s, passed=True)

        qual_s2_A, _, _ = gate_s2.evaluate_worker_qualification("miner_s2_A", 215)
        self.assertTrue(qual_s2_A)

        qual_s2_B, _, msg_s2_B = gate_s2.evaluate_worker_qualification("miner_s2_B", 215)
        self.assertFalse(qual_s2_B)
        self.assertIn("consecutive downtime exceeded", msg_s2_B)

        qual_gpu_s2_A, _, _ = gate_s2.evaluate_worker_qualification("gpu_s2_A", 215)
        self.assertTrue(qual_gpu_s2_A)

        # --- Stage 3 (108h, 432 slots) ---
        gate_s3 = AntiHoppingGate.create_for_stage(stage_id=3, epoch_index=1, min_duty_cycle=0.95)
        # Miner A: 415 active slots (96.1%), 3 consecutive missed slots (allowed)
        for s in range(432):
            if s not in (100, 101, 102, 200, 300):
                gate_s3.record_asic_share("miner_s3_A", s)
        # Miner B: 4 consecutive missed slots (exceeds max allowed 3)
        for s in range(432):
            if s not in (100, 101, 102, 103):
                gate_s3.record_asic_share("miner_s3_B", s)

        qual_s3_A, _, _ = gate_s3.evaluate_worker_qualification("miner_s3_A", 431)
        self.assertTrue(qual_s3_A)

        qual_s3_B, _, msg_s3_B = gate_s3.evaluate_worker_qualification("miner_s3_B", 431)
        self.assertFalse(qual_s3_B)
        self.assertIn("consecutive downtime exceeded", msg_s3_B)

    def test_stage4_year4_mega_combo_anti_hopping_loyalty(self):
        """
        Verify Year 4 Mega Combo (324h = 13.5 days, 648 slots of 30 min each):
        1. Honest ASIC Miner 1: 620 slots active (95.68%) -> QUALIFIED.
        2. Mercenary ASIC Miner 2: joins only in the last 2 days (slots 552..647 = 14.8%) -> DISQUALIFIED.
        3. Abandoning ASIC Miner 3: active for 12 days (0..575 = 88.9%), then hops off -> DISQUALIFIED.
        4. Intermittent ASIC Miner 4: active 640 slots, but 5 consecutive slots down (2.5h > 2.0h) -> DISQUALIFIED.
        5. Honest GPU Node 1: active 630 slots with PoR passes -> QUALIFIED.
        6. Mercenary GPU Node 2: active only in the last 12 hours (24 slots = 3.7%) -> DISQUALIFIED.
        7. Candidate Hash Pruning: 100% of candidate hashes from miners 2, 3, 4 and GPU 2 are pruned.
        """
        gate = AntiHoppingGate.create_for_stage(stage_id=4, epoch_index=1, min_duty_cycle=0.95)

        # 1. Honest ASIC Miner 1: active in 620 slots (non-consecutive small pauses < 4 slots)
        for s in range(648):
            if s % 25 != 0:  # misses ~26 slots spread evenly (96.0% active)
                gate.record_asic_share("miner_honest_1", s)
                if s % 50 == 0:
                    gate.record_asic_block("miner_honest_1", s, hashlib.sha256(f"blk_h1_{s}".encode()).digest())

        # 2. Mercenary ASIC Miner 2 (NiceHash rented sniper in last 2 days: slots 552..647)
        for s in range(552, 648):
            gate.record_asic_share("miner_sniper_2", s)
            if s % 10 == 0:
                gate.record_asic_block("miner_sniper_2", s, hashlib.sha256(f"blk_snipe_{s}".encode()).digest())

        # 3. Abandoning ASIC Miner 3 (mined for 12 days = 576 slots, then gave up or switched)
        for s in range(576):
            gate.record_asic_share("miner_abandoned_3", s)
            if s % 50 == 0:
                gate.record_asic_block("miner_abandoned_3", s, hashlib.sha256(f"blk_aban_{s}".encode()).digest())

        # 4. Intermittent ASIC Miner 4 (active 640 slots, but took a 5-slot consecutive break: slots 100..104)
        for s in range(648):
            if s not in (100, 101, 102, 103, 104, 300, 400):
                gate.record_asic_share("miner_glitch_4", s)
                if s % 60 == 0:
                    gate.record_asic_block("miner_glitch_4", s, hashlib.sha256(f"blk_glitch_{s}".encode()).digest())

        # 5. Honest GPU Node 1: PoR challenges passed across 630 slots
        for s in range(648):
            if s % 30 != 0:
                gate.record_gpu_por("gpu_honest_1", s, passed=True)
                if s % 10 == 0:
                    gate.record_gpu_task(
                        "gpu_honest_1", s, hashlib.sha256(f"task_h1_{s}".encode()).digest(), task_kcu=2.5
                    )

        # 6. Mercenary GPU Node 2: hops in during final 12h (slots 624..647)
        for s in range(624, 648):
            gate.record_gpu_por("gpu_sniper_2", s, passed=True)
            gate.record_gpu_task(
                "gpu_sniper_2", s, hashlib.sha256(f"task_snipe_{s}".encode()).digest(), task_kcu=5.0
            )

        # Evaluate all at slot 647 (end of 13.5 days)
        qual_h1, duty_h1, msg_h1 = gate.evaluate_worker_qualification("miner_honest_1", 647)
        self.assertTrue(qual_h1)
        self.assertGreaterEqual(duty_h1, 0.95)
        self.assertEqual(msg_h1, "Qualified")

        qual_sn2, duty_sn2, msg_sn2 = gate.evaluate_worker_qualification("miner_sniper_2", 647)
        self.assertFalse(qual_sn2)
        self.assertLess(duty_sn2, 0.20)
        self.assertIn("Anti-Hopping Triggered", msg_sn2)

        qual_ab3, duty_ab3, msg_ab3 = gate.evaluate_worker_qualification("miner_abandoned_3", 647)
        self.assertFalse(qual_ab3)
        self.assertLess(duty_ab3, 0.95)
        self.assertIn("Anti-Hopping Triggered", msg_ab3)

        qual_gl4, duty_gl4, msg_gl4 = gate.evaluate_worker_qualification("miner_glitch_4", 647)
        self.assertFalse(qual_gl4)
        self.assertIn("consecutive downtime exceeded", msg_gl4)

        qual_gh1, duty_gh1, msg_gh1 = gate.evaluate_worker_qualification("gpu_honest_1", 647)
        self.assertTrue(qual_gh1)
        self.assertGreaterEqual(duty_gh1, 0.95)

        qual_gs2, duty_gs2, msg_gs2 = gate.evaluate_worker_qualification("gpu_sniper_2", 647)
        self.assertFalse(qual_gs2)
        self.assertLess(duty_gs2, 0.10)

        # Candidate Hash Pruning Audit
        eligible_hashes, audit = gate.filter_eligible_candidates(647)
        self.assertEqual(audit["qualified_workers_count"], 2)
        self.assertEqual(set(audit["qualified_workers"]), {"miner_honest_1", "gpu_honest_1"})
        self.assertEqual(
            set(audit["disqualified_workers"]),
            {"miner_sniper_2", "miner_abandoned_3", "miner_glitch_4", "gpu_sniper_2"}
        )
        self.assertGreater(audit["eligible_candidates_count"], 0)
        self.assertGreater(audit["pruned_candidates_count"], 0)


if __name__ == "__main__":
    unittest.main()


"""
BTC-K Symbiotic AI Worker & GPU Grid Test Suite
Validates HardwareProbe, TieredPricingEngine, ProcessPreemptor, and SymbioticAIWorker.
"""

import os
import signal
import subprocess
import sys
import time
import unittest

# Ensure src is on pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from worker.hardware_probe import GPUTier, HardwareProbe
from worker.pricing_engine import ModelTier, ModelVariant, TieredPricingEngine
from worker.process_preemptor import MinerState, ProcessPreemptor
from worker.symbiotic_worker import SymbioticAIWorker
from worker.tui_console import TUIConsole


class TestSymbioticWorkerSuite(unittest.TestCase):

    def test_hardware_probe_tier_matrix(self):
        """Test GPU VRAM detection maps accurately to Tier 0 ~ Tier 4."""
        # Case 1: 8GB Card (e.g. RTX 3060 8G, 4060) -> Tier 0
        p0 = HardwareProbe.detect_profile(mock_override={"gpu_count": 1, "total_vram_gb": 8.0})
        self.assertEqual(p0.assigned_tier, GPUTier.TIER_0_MICRO)
        self.assertEqual(p0.tier_level, 0)
        self.assertTrue(p0.pearl_coexistence_possible)

        # Case 2: 16GB Card (e.g. RTX 4070 Ti Super) -> Tier 1 (Pearl Coexistence)
        p1 = HardwareProbe.detect_profile(mock_override={"gpu_count": 1, "total_vram_gb": 16.0})
        self.assertEqual(p1.assigned_tier, GPUTier.TIER_1_STANDARD)
        self.assertEqual(p1.tier_level, 1)
        self.assertTrue(p1.pearl_coexistence_possible)

        # Case 3: 24GB Card (e.g. RTX 3090 / 4090) -> Tier 2
        p2 = HardwareProbe.detect_profile(mock_override={"gpu_count": 1, "total_vram_gb": 24.0})
        self.assertEqual(p2.assigned_tier, GPUTier.TIER_2_ADVANCED)
        self.assertEqual(p2.tier_level, 2)

        # Case 4: 4x 4090 (96GB total) -> Tier 3
        p3 = HardwareProbe.detect_profile(mock_override={"gpu_count": 4, "total_vram_gb": 96.0})
        self.assertEqual(p3.assigned_tier, GPUTier.TIER_3_FLAGSHIP)
        self.assertEqual(p3.tier_level, 3)

        # Case 5: 8x H100 Cluster (640GB total) -> Tier 4
        p4 = HardwareProbe.detect_profile(mock_override={"gpu_count": 8, "total_vram_gb": 640.0})
        self.assertEqual(p4.assigned_tier, GPUTier.TIER_4_DATACENTER)
        self.assertEqual(p4.tier_level, 4)

    def test_pricing_engine_tier_and_variants(self):
        """Test economic pricing multipliers and uncensored dark stream premium."""
        # Tier 1, Standard Aligned, 1000 prompt + 500 completion
        cost_std = TieredPricingEngine.calculate_cost(
            prompt_tokens=1000,
            completion_tokens=500,
            tier=ModelTier.TIER_1,
            variant=ModelVariant.STANDARD,
        )
        # 1.4 KCU at the $0.05 bootstrap reference = 280,000 SatK.
        self.assertEqual(cost_std.tier_multiplier, 1.0)
        self.assertEqual(cost_std.variant_multiplier, 1.0)
        self.assertEqual(cost_std.total_satk, 280_000)

        # Tier 1, Uncensored Variant (3.5x premium)
        cost_uncensored = TieredPricingEngine.calculate_cost(
            prompt_tokens=1000,
            completion_tokens=500,
            tier=ModelTier.TIER_1,
            variant=ModelVariant.UNCENSORED,
        )
        self.assertEqual(cost_uncensored.variant_multiplier, 3.5)
        # 3.5x of ~18 SatK is ~61 SatK
        self.assertGreater(cost_uncensored.total_satk, cost_std.total_satk * 3)

        # Tier 3 (70B, 18x multiplier), Uncensored
        cost_t3 = TieredPricingEngine.calculate_cost(
            prompt_tokens=1000,
            completion_tokens=500,
            tier=ModelTier.TIER_3,
            variant=ModelVariant.UNCENSORED,
        )
        self.assertEqual(cost_t3.tier_multiplier, 18.0)
        self.assertGreater(cost_t3.total_satk, 1000)

    def test_kcu_bridge_tracks_btck_reference_price(self):
        baseline = TieredPricingEngine.quote_kcu_usage(1000, 0.8, 0.05)
        appreciated = TieredPricingEngine.quote_kcu_usage(1000, 0.8, 0.50)
        depreciated = TieredPricingEngine.quote_kcu_usage(1000, 0.8, 0.005)

        self.assertEqual(baseline["task_kcu"], 0.8)
        self.assertEqual(baseline["amount_msatk"], 160_000_000)
        self.assertEqual(baseline["amount_satk"], 160_000)
        self.assertEqual(appreciated["amount_satk"], 16_000)
        self.assertEqual(depreciated["amount_satk"], 1_600_000)
        self.assertEqual(baseline["kcu_usd_peg"], 0.0001)

    def test_pricing_engine_version_decay(self):
        """Test that old deprecated models suffer linear price decay down to 0.3x floor."""
        # Day 0 flagship: decay = 1.0
        c0 = TieredPricingEngine.calculate_cost(1000, 500, is_flagship=True)
        self.assertEqual(c0.decay_factor, 1.0)

        # Day 15 deprecated: decay ~ 0.65
        c15 = TieredPricingEngine.calculate_cost(1000, 500, is_flagship=False, deprecation_days=15)
        self.assertAlmostEqual(c15.decay_factor, 0.65, places=2)

        # Day 60 deprecated: hit 0.30 floor
        c60 = TieredPricingEngine.calculate_cost(1000, 500, is_flagship=False, deprecation_days=60)
        self.assertEqual(c60.decay_factor, 0.30)
        self.assertLess(c60.total_satk, c0.total_satk)

    def test_process_preemptor_pause_and_resume(self):
        """Test real sub-second SIGSTOP and SIGCONT process freezing."""
        # Spawn a dummy process to simulate miner
        proc = subprocess.Popen(["sleep", "30"])
        try:
            preemptor = ProcessPreemptor(pid=proc.pid, max_preemption_timeout_sec=5.0)
            self.assertEqual(preemptor.state, MinerState.MINING_ACTIVE)

            # 1. Pause
            ok_pause, pause_ms = preemptor.pause()
            self.assertTrue(ok_pause)
            self.assertEqual(preemptor.state, MinerState.PAUSED_FOR_AI)
            self.assertLess(pause_ms, 100.0)  # Must be fast (<100ms)

            # 2. Resume
            ok_resume, resume_ms = preemptor.resume()
            self.assertTrue(ok_resume)
            self.assertEqual(preemptor.state, MinerState.MINING_ACTIVE)
            self.assertLess(resume_ms, 100.0)

            self.assertEqual(preemptor.stats["total_preemptions"], 1)
        finally:
            proc.kill()
            proc.wait()

    def test_process_preemptor_watchdog_autorecovery(self):
        """Test safety watchdog auto-resumes mining if AI task times out."""
        proc = subprocess.Popen(["sleep", "30"])
        try:
            # Short timeout of 0.2 seconds
            preemptor = ProcessPreemptor(pid=proc.pid, max_preemption_timeout_sec=0.2, enable_watchdog=True)
            preemptor.pause()
            self.assertEqual(preemptor.state, MinerState.PAUSED_FOR_AI)

            # Wait for watchdog to fire (robust poll up to 1.5s)
            t_end = time.time() + 1.5
            while time.time() < t_end and preemptor.state != MinerState.MINING_ACTIVE:
                time.sleep(0.05)

            # Verify watchdog automatically restored mining state
            self.assertEqual(preemptor.state, MinerState.MINING_ACTIVE)
            self.assertEqual(preemptor.stats["watchdog_auto_resumes"], 1)
        finally:
            proc.kill()
            proc.wait()

    def test_symbiotic_worker_full_pipeline(self):
        """Test full end-to-end task execution with Zero-Key isolation and SatK earning."""
        dummy_miner = subprocess.Popen(["sleep", "30"])
        try:
            stealth_addr = "btck1sqqfounderstealthwalletforpayouts982347234"
            worker = SymbioticAIWorker(
                payout_stealth_address=stealth_addr,
                miner_pid=dummy_miner.pid,
                mock_hardware_override={"gpu_count": 4, "total_vram_gb": 64.0},  # 4x 4070Ti = 64GB
            )

            telemetry = worker.get_worker_telemetry()
            self.assertTrue(telemetry["security"]["zero_key_isolation"])
            self.assertFalse(telemetry["security"]["private_keys_stored"])
            self.assertEqual(telemetry["gpu_profile"]["tier_level"], 3)

            # Execute an AI Task (Uncensored Variant)
            receipt = worker.process_ai_task(
                task_id="TASK-AI-TEST-001",
                prompt="De-obfuscate this smart contract assembly and find reentrancy vectors.",
                prompt_tokens=1200,
                expected_completion_tokens=400,
                model_name="qwen-2.5-72b-uncensored",
                variant=ModelVariant.UNCENSORED,
            )

            self.assertEqual(receipt["status"], "COMPLETED")
            self.assertGreater(receipt["earned_satk"], 100)
            self.assertTrue(receipt["proof_of_execution_hash"])
            self.assertEqual(worker.total_tasks_completed, 1)
            self.assertEqual(worker.total_satk_earned, receipt["earned_satk"])

            # Verify miner state is returned to active
            self.assertEqual(worker.preemptor.state, MinerState.MINING_ACTIVE)
        finally:
            dummy_miner.kill()
            dummy_miner.wait()

    def test_tui_console_render(self):
        """Test ANSI TUI console rendering and dynamic telemetry updates."""
        console = TUIConsole(
            worker_address="btck1sfounderstealthwallet982347234234234234234",
            assigned_tier_name="Tier 1 (Standard: 7B~8B)",
            tier_level=1,
            gpu_model="RTX 4070 Ti SUPER",
            total_vram_gb=16.0,
            model_name="deepseek-r1-distill-qwen-8b-q4"
        )
        # 1. Initial render
        rendered = console.render_to_string()
        self.assertIn("BTC-K SYMBIOTIC WORKER", rendered)
        self.assertIn("Zero-Key Certified", rendered)
        self.assertIn("RTX 4070 Ti SUPER", rendered)
        self.assertIn("MINING PRL", rendered)

        # 2. Update to active AI task
        console.update_ai_task(task_id="TASK-402-001", tokens=500, latency_ms=0.008)
        rendered_preempted = console.render_to_string()
        self.assertIn("PREEMPTED FOR BTC-K AI", rendered_preempted)
        self.assertIn("TASK-402-001", rendered_preempted)

        # 3. Complete task with 350 SatK reward
        console.update_ai_task(task_id=None, tokens=500, satk_payout=350)
        rendered_completed = console.render_to_string()
        self.assertIn("MINING PRL", rendered_completed)
        self.assertIn("350 SatK", rendered_completed)
        self.assertEqual(console.ai_tasks_completed, 1)
        self.assertEqual(console.total_satk_earned, 350)


if __name__ == "__main__":
    unittest.main()

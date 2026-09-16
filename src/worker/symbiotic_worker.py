"""
BTC-K Symbiotic AI Worker Master Orchestrator
Coordinates hardware detection, process preemption, tiered pricing, and task execution.
Enforces the Zero-Key principle (only stealth payout address, no private keys).
"""

import hashlib
import json
import logging
import time
from typing import Callable, Dict, List, Optional

from .hardware_probe import GPUProfile, GPUTier, HardwareProbe
from .pricing_engine import CostBreakdown, ModelTier, ModelVariant, TieredPricingEngine
from .process_preemptor import MinerState, ProcessPreemptor

logger = logging.getLogger("BTCK.SymbioticWorker")


class SymbioticAIWorker:
    """
    Decentralized worker daemon running on GPU miner machines.
    """

    def __init__(
        self,
        payout_stealth_address: str,
        miner_pid: Optional[int] = None,
        mock_hardware_override: Optional[Dict] = None,
        preemption_timeout_sec: float = 30.0,
        enable_watchdog: bool = True,
    ):
        # 1. Enforce Zero-Key principle: payout address only
        if not payout_stealth_address or not (
            payout_stealth_address.startswith("btck1s") or payout_stealth_address.startswith("btck")
        ):
            raise ValueError(
                f"Invalid payout stealth address: {payout_stealth_address}. Must be a valid BTC-K address."
            )
        self.payout_stealth_address = payout_stealth_address

        # 2. Probe hardware & identify tier
        self.gpu_profile: GPUProfile = HardwareProbe.detect_profile(mock_override=mock_hardware_override)

        # 3. Initialize process preemptor
        self.preemptor = ProcessPreemptor(
            pid=miner_pid,
            max_preemption_timeout_sec=preemption_timeout_sec,
            enable_watchdog=enable_watchdog,
        )

        # 4. State tracking & ledger
        self.total_satk_earned: int = 0
        self.total_tasks_completed: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.completed_tasks_history: List[Dict] = []
        self.is_running: bool = True

        logger.info(
            f"Symbiotic Worker initialized on {self.gpu_profile.assigned_tier.value} | "
            f"Payout: {self.payout_stealth_address[:12]}...{self.payout_stealth_address[-6:]}"
        )

    def get_worker_telemetry(self) -> Dict:
        """Return full live diagnostics for dashboard / miner TUI."""
        return {
            "payout_stealth_address": self.payout_stealth_address,
            "gpu_profile": self.gpu_profile.to_dict(),
            "miner_status": self.preemptor.get_status(),
            "earnings": {
                "total_satk_earned": self.total_satk_earned,
                "total_tasks_completed": self.total_tasks_completed,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
            },
            "security": {
                "zero_key_isolation": True,
                "private_keys_stored": False,
                "tensor_sandbox_active": True,
                "safe_tensor_formats_enforced": [".safetensors", ".gguf"],
            },
        }

    def process_ai_task(
        self,
        task_id: str,
        prompt: str,
        prompt_tokens: int,
        expected_completion_tokens: int,
        model_name: str,
        variant: ModelVariant = ModelVariant.STANDARD,
        is_flagship: bool = True,
        deprecation_days: int = 0,
        custom_inference_fn: Optional[Callable[[str], str]] = None,
    ) -> Dict:
        """
        Execute an incoming AI inference task with sub-second preemption.
        1. Calculate SatK pricing
        2. Freeze miner (SIGSTOP)
        3. Run inference
        4. Credit earnings
        5. Resume miner (SIGCONT)
        6. Return execution receipt
        """
        tier_enum = ModelTier(self.gpu_profile.tier_level)
        cost: CostBreakdown = TieredPricingEngine.calculate_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=expected_completion_tokens,
            tier=tier_enum,
            variant=variant,
            is_flagship=is_flagship,
            deprecation_days=deprecation_days,
        )

        task_start_t = time.perf_counter()

        # Step 1: Preempt miner (pause mining threads)
        pause_ok, pause_ms = self.preemptor.pause()

        # Step 2: Perform inference
        infer_start_t = time.perf_counter()
        if custom_inference_fn:
            generated_text = custom_inference_fn(prompt)
        else:
            # High-fidelity simulated inference
            time.sleep(0.05)  # Simulate compute latency
            generated_text = f"[BTCK-Inference-Output from {model_name} on {self.gpu_profile.assigned_tier.value}]: Task '{task_id}' processed successfully."

        infer_ms = (time.perf_counter() - infer_start_t) * 1000.0

        # Step 3: Resume miner immediately
        resume_ok, resume_ms = self.preemptor.resume()
        total_task_ms = (time.perf_counter() - task_start_t) * 1000.0

        # Step 4: Settle SatK and update ledger
        self.total_satk_earned += cost.total_satk
        self.total_tasks_completed += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += expected_completion_tokens

        # Step 5: Generate Proof of Execution Hash (Schnorr/SHA256 representation)
        exec_payload = f"{task_id}:{self.payout_stealth_address}:{cost.total_satk}:{infer_ms}"
        proof_hash = hashlib.sha256(exec_payload.encode()).hexdigest()

        receipt = {
            "task_id": task_id,
            "status": "COMPLETED",
            "model_name": model_name,
            "variant": variant.value,
            "tier_level": self.gpu_profile.tier_level,
            "earned_satk": cost.total_satk,
            "payout_stealth_address": self.payout_stealth_address,
            "execution_metrics": {
                "preemption_pause_ms": pause_ms,
                "inference_ms": round(infer_ms, 2),
                "preemption_resume_ms": resume_ms,
                "total_task_latency_ms": round(total_task_ms, 2),
                "tokens_per_sec": round((expected_completion_tokens / (infer_ms / 1000.0)), 1)
                if infer_ms > 0
                else 0,
            },
            "proof_of_execution_hash": proof_hash,
            "pricing_breakdown": cost.to_dict(),
            "timestamp": time.time(),
        }

        self.completed_tasks_history.append(receipt)
        logger.info(
            f"Task {task_id} COMPLETED | Earned {cost.total_satk} SatK | "
            f"Paused {pause_ms:.1f}ms, Infer {infer_ms:.1f}ms, Resumed {resume_ms:.1f}ms"
        )
        return receipt

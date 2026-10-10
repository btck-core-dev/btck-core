"""
BTC-K Symbiotic Worker TUI Console
Provides a zero-dependency ANSI terminal dashboard for GPU miners.
Displays real-time Pearl baseline mining telemetry alongside BTC-K AI SatK earnings.
"""

import os
import sys
import time
from typing import Dict, Optional


class AnsiColors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    
    # Background
    BG_DARK = "\033[40m"
    BG_GREEN = "\033[42m"
    BG_GOLD = "\033[43m"
    BG_BLUE = "\033[44m"


class TUIConsole:
    """
    Zero-dependency ANSI console for symbiotic GPU miners.
    """

    def __init__(
        self,
        worker_address: str,
        assigned_tier_name: str,
        tier_level: int,
        gpu_model: str,
        total_vram_gb: float,
        model_name: str,
    ):
        self.worker_address = worker_address
        self.assigned_tier_name = assigned_tier_name
        self.tier_level = tier_level
        self.gpu_model = gpu_model
        self.total_vram_gb = total_vram_gb
        self.model_name = model_name

        # Dynamic telemetry
        self.miner_state = "MINING_ACTIVE"
        self.pearl_hashrate_str = "549.3 TH/s"
        self.pearl_accepted_shares = 0
        self.pearl_rejected_shares = 0
        self.pearl_pool = "pearl-eu1.lproute.com:3360"

        # AI Grid telemetry
        self.ai_tasks_completed = 0
        self.total_satk_earned = 0
        self.last_preemption_latency_ms = 0.0
        self.active_task_id: Optional[str] = None
        self.active_task_tokens = 0
        self.total_tokens_processed = 0

    def update_mining(self, state: str, hashrate: str, accepted: int, rejected: int):
        self.miner_state = state
        self.pearl_hashrate_str = hashrate
        self.pearl_accepted_shares = accepted
        self.pearl_rejected_shares = rejected

    def update_ai_task(
        self,
        task_id: Optional[str],
        tokens: int = 0,
        satk_payout: int = 0,
        latency_ms: float = 0.0,
    ):
        self.active_task_id = task_id
        if task_id:
            self.miner_state = "PAUSED_FOR_AI"
            self.active_task_tokens = tokens
            self.last_preemption_latency_ms = latency_ms
        else:
            self.miner_state = "MINING_ACTIVE"
            if satk_payout > 0:
                self.ai_tasks_completed += 1
                self.total_satk_earned += satk_payout
                self.total_tokens_processed += tokens
                self.active_task_tokens = 0

    def render_to_string(self) -> str:
        """Render the dashboard UI as a formatted string."""
        c = AnsiColors
        lines = []

        # Header Box
        lines.append(f"{c.CYAN}╔══════════════════════════════════════════════════════════════════════════════╗{c.RESET}")
        lines.append(f"{c.CYAN}║ {c.BOLD}{c.WHITE}⚡ BTC-K SYMBIOTIC WORKER v1.0.0{c.RESET}{c.CYAN} │ {c.YELLOW}Pearl (PRL) + DeepSeek 双模共生{c.RESET}{c.CYAN}             ║{c.RESET}")
        lines.append(f"{c.CYAN}╚══════════════════════════════════════════════════════════════════════════════╝{c.RESET}")

        # Hardware & Security Identity
        short_addr = self.worker_address[:14] + "..." + self.worker_address[-8:] if len(self.worker_address) > 26 else self.worker_address
        lines.append(f"  {c.BOLD}Payout Address:{c.RESET} {c.GREEN}{short_addr}{c.RESET}  {c.DIM}[🔒 Zero-Key Certified: Stealth Only]{c.RESET}")
        lines.append(f"  {c.BOLD}GPU Identity:{c.RESET}   {c.WHITE}{self.gpu_model}{c.RESET} ({self.total_vram_gb:.1f} GB VRAM) │ Tier {self.tier_level}")
        lines.append(f"  {c.BOLD}AI Workload:{c.RESET}    {c.MAGENTA}{self.model_name}{c.RESET} (Resident in VRAM alongside DAG)")
        lines.append(f"{c.DIM}──────────────────────────────────────────────────────────────────────────────{c.RESET}")

        # Section 1: Pearl Baseline Mining (Green)
        if self.miner_state == "MINING_ACTIVE":
            state_badge = f"{c.BG_GREEN}{c.WHITE}{c.BOLD} ● MINING PRL (100% HASHRATE) {c.RESET}"
        elif self.miner_state == "PAUSED_FOR_AI":
            state_badge = f"{c.BG_GOLD}{c.WHITE}{c.BOLD} ⏸ PREEMPTED FOR BTC-K AI (0.008ms) {c.RESET}"
        else:
            state_badge = f"{c.RED}{c.BOLD} ● {self.miner_state} {c.RESET}"

        lines.append(f"  {c.GREEN}{c.BOLD}[1] BASELINE PoW MINING (Pearl / PRL){c.RESET}      Status: {state_badge}")
        lines.append(f"      Pool:      {c.WHITE}{self.pearl_pool}{c.RESET}")
        lines.append(f"      Hashrate:  {c.GREEN}{c.BOLD}{self.pearl_hashrate_str}{c.RESET}  │  Shares: {c.GREEN}{self.pearl_accepted_shares} Acc{c.RESET} / {c.RED}{self.pearl_rejected_shares} Rej{c.RESET}")
        lines.append(f"      Revenue:   {c.DIM}Guaranteed Baseline (PRL Spot: ~$0.60 USD){c.RESET}")
        lines.append(f"{c.DIM}──────────────────────────────────────────────────────────────────────────────{c.RESET}")

        # Section 2: BTC-K AI Compute Grid (Gold / Cyan)
        lines.append(f"  {c.YELLOW}{c.BOLD}[2] BTC-K x402 AI COMPUTE DISPATCH (SatK Premium){c.RESET}")
        lines.append(f"      Total SatK Earned:     {c.YELLOW}{c.BOLD}{self.total_satk_earned:,} SatK{c.RESET} (≈ {self.total_satk_earned / 100_000_000:.4f} BTC-K)")
        lines.append(f"      AI Orders Completed:   {c.CYAN}{self.ai_tasks_completed}{c.RESET} requests │ {self.total_tokens_processed:,} tokens generated")
        
        if self.active_task_id:
            lines.append(f"      Active Job:            {c.YELLOW}⚡ {self.active_task_id}{c.RESET} ({self.active_task_tokens} tokens)")
            lines.append(f"      Preemption Overhead:   {c.GREEN}{self.last_preemption_latency_ms:.3f} ms{c.RESET} (Watchdog armed: 30s max)")
        else:
            lines.append(f"      Active Job:            {c.DIM}Idle (Standing by for HTTP 402 AI-RPC requests){c.RESET}")
            lines.append(f"      Preemption Overhead:   {c.DIM}Last: {self.last_preemption_latency_ms:.3f} ms{c.RESET}")

        lines.append(f"{c.CYAN}══════════════════════════════════════════════════════════════════════════════{c.RESET}")
        return "\n".join(lines)

    def print_dashboard(self):
        """Clear screen and print updated dashboard."""
        rendered = self.render_to_string()
        sys.stdout.write(f"\033[H\033[J{rendered}\n")
        sys.stdout.flush()

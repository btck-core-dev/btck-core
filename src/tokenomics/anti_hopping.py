"""
BTC-K Anti-Hopping Duty-Cycle Gate & Dual Combo Engine (v2.4.0)
--------------------------------------------------------------
Implements:
1. Anti-Hopping Continuous Duty-Cycle Gate (>=95% / >=99% uptime requirement)
   - Prevents mercenary hashrate (NiceHash / profit switchers) from sniping Combo jackpots.
   - Enforces time sunk cost and extreme loyalty lock-in for both ASIC and GPU nodes.
   - Disqualifies hopping miners and prunes their tickets, amplifying honest miners' odds.
2. Dual Combo Architecture:
   - L1 ASIC Combo: Native Minted Block Subsidy Issuance (BTC-K).
   - L2 GPU Combo: 100% Circulating Real AI Traffic Fee Split (SatK), ZERO inflation.
3. Cold-Start GPU Standby Subsidy & Proof of Readiness (PoR):
   - Two-phase transition: Eco-Fund seed floor -> 100% organic fee self-sustainability.
   - Interactive VRAM residency challenge (TTFT < 2.0s) prevents Sybil ghost nodes.
   - Tiered hardware capacity weighting and SLA slashing for dispatch refusal.
"""

import time
import math
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Set, Any


class WorkerType(str, Enum):
    ASIC_MINER = "ASIC_MINER"
    GPU_NODE = "GPU_NODE"


@dataclass
class SlotActivityRecord:
    slot_index: int
    shares_submitted: int = 0
    blocks_found: int = 0
    por_challenges_passed: int = 0
    por_challenges_failed: int = 0
    tasks_completed: int = 0
    tasks_refused: int = 0


@dataclass
class WorkerEpochTracker:
    worker_id: str
    worker_type: WorkerType
    payout_address: str
    hardware_tier: str = "35B_MOE"  # e.g., 8B, 35B_MOE, 72B, 405B
    hardware_weight: float = 3.5     # Weight multiplier for standby pool
    slots: Dict[int, SlotActivityRecord] = field(default_factory=dict)
    is_disqualified: bool = False
    disqualification_reason: str = ""
    candidate_hashes: List[bytes] = field(default_factory=list)
    accumulated_kcu: float = 0.0


# 4-Year Progressive Anti-Hopping Stage Specifications (12h -> 36h -> 108h -> 324h)
STAGE_EPOCH_SPECS = {
    1: {"stage_name": "Phase 1 (Year 1)", "draw_hours": 12, "duration_seconds": 12 * 3600, "slot_seconds": 600, "slots": 72, "max_consecutive_missed": 1},
    2: {"stage_name": "Phase 2 (Year 2)", "draw_hours": 36, "duration_seconds": 36 * 3600, "slot_seconds": 600, "slots": 216, "max_consecutive_missed": 2},
    3: {"stage_name": "Phase 3 (Year 3)", "draw_hours": 108, "duration_seconds": 108 * 3600, "slot_seconds": 900, "slots": 432, "max_consecutive_missed": 3},
    4: {"stage_name": "Phase 4 (Year 4)", "draw_hours": 324, "duration_seconds": 324 * 3600, "slot_seconds": 1800, "slots": 648, "max_consecutive_missed": 4},
}


class AntiHoppingGate:
    """
    Duty-Cycle Enforcement Gate across ALL 4 Years (12h, 36h, 108h, 324h):
    Partitions an epoch into discrete micro-slots.
    Requires miners/nodes to maintain >=95.0% (or configurable >=99.0%) active slot participation
    without exceeding the maximum consecutive inactivity threshold across every stage.
    """

    def __init__(
        self,
        epoch_index: int,
        epoch_duration_seconds: int = 43200,  # Default 12 hours (Stage 1)
        slot_duration_seconds: int = 600,     # Default 10 minutes per slot
        min_duty_cycle: float = 0.95,         # 95% continuous online requirement across all stages
        max_consecutive_missed_slots: int = 1, # Max allowable consecutive missed slots
    ):
        self.epoch_index = epoch_index
        self.epoch_duration_seconds = epoch_duration_seconds
        self.slot_duration_seconds = slot_duration_seconds
        self.total_slots = max(1, epoch_duration_seconds // slot_duration_seconds)
        self.min_duty_cycle = min_duty_cycle
        self.max_consecutive_missed_slots = max_consecutive_missed_slots
        self.workers: Dict[str, WorkerEpochTracker] = {}

    @classmethod
    def create_for_stage(
        cls,
        stage_id: int,
        epoch_index: int,
        min_duty_cycle: float = 0.95
    ) -> "AntiHoppingGate":
        """Factory method to construct an AntiHoppingGate calibrated for any of the 4 years."""
        spec = STAGE_EPOCH_SPECS.get(stage_id, STAGE_EPOCH_SPECS[1])
        return cls(
            epoch_index=epoch_index,
            epoch_duration_seconds=spec["duration_seconds"],
            slot_duration_seconds=spec["slot_seconds"],
            min_duty_cycle=min_duty_cycle,
            max_consecutive_missed_slots=spec["max_consecutive_missed"],
        )

    def get_or_create_worker(
        self,
        worker_id: str,
        worker_type: WorkerType = WorkerType.ASIC_MINER,
        payout_address: str = "",
        hardware_tier: str = "35B_MOE",
        hardware_weight: float = 3.5,
    ) -> WorkerEpochTracker:
        if worker_id not in self.workers:
            self.workers[worker_id] = WorkerEpochTracker(
                worker_id=worker_id,
                worker_type=worker_type,
                payout_address=payout_address,
                hardware_tier=hardware_tier,
                hardware_weight=hardware_weight,
            )
        return self.workers[worker_id]

    def record_asic_share(self, worker_id: str, slot_index: int, payout_address: str = "") -> None:
        """Record an accepted ASIC share in the given micro-slot."""
        w = self.get_or_create_worker(worker_id, WorkerType.ASIC_MINER, payout_address)
        if slot_index not in w.slots:
            w.slots[slot_index] = SlotActivityRecord(slot_index=slot_index)
        w.slots[slot_index].shares_submitted += 1

    def record_asic_block(self, worker_id: str, slot_index: int, block_hash: bytes, payout_address: str = "") -> None:
        """Record an ASIC mined block and add to worker candidate hashes."""
        w = self.get_or_create_worker(worker_id, WorkerType.ASIC_MINER, payout_address)
        if slot_index not in w.slots:
            w.slots[slot_index] = SlotActivityRecord(slot_index=slot_index)
        w.slots[slot_index].blocks_found += 1
        w.slots[slot_index].shares_submitted += 1
        w.candidate_hashes.append(block_hash)

    def record_gpu_task(
        self,
        worker_id: str,
        slot_index: int,
        receipt_hash: bytes,
        task_kcu: float,
        payout_address: str = "",
        hardware_tier: str = "35B_MOE",
        hardware_weight: float = 3.5,
    ) -> None:
        """Record a completed GPU inference task."""
        w = self.get_or_create_worker(worker_id, WorkerType.GPU_NODE, payout_address, hardware_tier, hardware_weight)
        if slot_index not in w.slots:
            w.slots[slot_index] = SlotActivityRecord(slot_index=slot_index)
        w.slots[slot_index].tasks_completed += 1
        w.candidate_hashes.append(receipt_hash)
        w.accumulated_kcu += task_kcu

    def record_gpu_por(self, worker_id: str, slot_index: int, passed: bool, payout_address: str = "") -> None:
        """Record an interactive Proof of Readiness (PoR) probe result."""
        w = self.get_or_create_worker(worker_id, WorkerType.GPU_NODE, payout_address)
        if slot_index not in w.slots:
            w.slots[slot_index] = SlotActivityRecord(slot_index=slot_index)
        if passed:
            w.slots[slot_index].por_challenges_passed += 1
        else:
            w.slots[slot_index].por_challenges_failed += 1

    def record_gpu_refusal(self, worker_id: str, slot_index: int) -> None:
        """Record an unauthorized task refusal or timeout (causes immediate disqualification)."""
        w = self.get_or_create_worker(worker_id, WorkerType.GPU_NODE)
        if slot_index not in w.slots:
            w.slots[slot_index] = SlotActivityRecord(slot_index=slot_index)
        w.slots[slot_index].tasks_refused += 1
        w.is_disqualified = True
        w.disqualification_reason = f"SLA Breach: Refused / timed out dispatched task in slot {slot_index}"

    def evaluate_worker_qualification(self, worker_id: str, current_slot: int) -> Tuple[bool, float, str]:
        """
        Evaluates whether a worker satisfies the anti-hopping duty-cycle gate.
        Returns: (is_qualified, duty_cycle_ratio, detail_message)
        """
        w = self.workers.get(worker_id)
        if not w:
            return False, 0.0, "Worker not registered"

        if w.is_disqualified:
            return False, 0.0, f"Disqualified: {w.disqualification_reason}"

        evaluated_slots = max(1, min(self.total_slots, current_slot + 1))
        active_slots_count = 0
        consecutive_missed = 0
        max_consecutive_seen = 0

        for s in range(evaluated_slots):
            rec = w.slots.get(s)
            is_slot_active = False
            if rec:
                if w.worker_type == WorkerType.ASIC_MINER:
                    is_slot_active = (rec.shares_submitted > 0 or rec.blocks_found > 0)
                else:  # GPU_NODE
                    is_slot_active = (
                        rec.tasks_completed > 0 or (rec.por_challenges_passed > 0 and rec.por_challenges_failed == 0)
                    )

            if is_slot_active:
                active_slots_count += 1
                consecutive_missed = 0
            else:
                consecutive_missed += 1
                if consecutive_missed > max_consecutive_seen:
                    max_consecutive_seen = consecutive_missed

        duty_cycle = active_slots_count / float(evaluated_slots)

        # Check consecutive outage violation
        if max_consecutive_seen > self.max_consecutive_missed_slots:
            reason = f"Anti-Hopping Triggered: Max consecutive downtime exceeded ({max_consecutive_seen} slots > {self.max_consecutive_missed_slots} allowed)"
            return False, round(duty_cycle, 4), reason

        # Check aggregate duty cycle threshold
        if duty_cycle < self.min_duty_cycle:
            reason = f"Anti-Hopping Triggered: Duty cycle {duty_cycle*100:.1f}% below minimum {self.min_duty_cycle*100:.1f}%"
            return False, round(duty_cycle, 4), reason

        return True, round(duty_cycle, 4), "Qualified"

    def filter_eligible_candidates(
        self,
        current_slot: int
    ) -> Tuple[List[bytes], Dict[str, Any]]:
        """
        Prunes all candidates produced by hopping / disqualified miners.
        Returns:
            (eligible_candidate_hashes, audit_summary)
        """
        eligible_hashes: List[bytes] = []
        qualified_workers: List[str] = []
        disqualified_workers: List[str] = []
        pruned_hashes_count = 0

        for wid, w in self.workers.items():
            is_qual, ratio, msg = self.evaluate_worker_qualification(wid, current_slot)
            if is_qual:
                qualified_workers.append(wid)
                eligible_hashes.extend(w.candidate_hashes)
            else:
                disqualified_workers.append(wid)
                pruned_hashes_count += len(w.candidate_hashes)

        summary = {
            "total_workers": len(self.workers),
            "qualified_workers_count": len(qualified_workers),
            "disqualified_workers_count": len(disqualified_workers),
            "qualified_workers": qualified_workers,
            "disqualified_workers": disqualified_workers,
            "eligible_candidates_count": len(eligible_hashes),
            "pruned_candidates_count": pruned_hashes_count,
        }
        return eligible_hashes, summary


class L2GPUComboEngine:
    """
    Manages L2 GPU Combo Jackpot:
    - 100% Circulating Real SatK from HTTP 402 AI-RPC fee splits (15% in Y1 -> 40% in Y4).
    - ZERO newly minted BTC-K inflation.
    - Uses shared Future Random Beacon (H_end + 100) + Kademlia XOR geometric distance.
    - Distribution: 70% Grand Prize to minimum XOR distance winner, 30% split across top 2-10 runners-up.
    """

    def __init__(self, epoch_index: int, draw_interval_hours: int = 12):
        self.epoch_index = epoch_index
        self.draw_interval_hours = draw_interval_hours
        self.accumulated_satk = 0
        self.task_tickets: List[Tuple[bytes, str, float]] = []  # (ticket_hash, worker_id, kcu)

    def deposit_circulating_satk(self, satk_amount: int) -> None:
        """Incorporate real fee SatK from completed AI-RPC task."""
        if satk_amount > 0:
            self.accumulated_satk += satk_amount

    def register_task_ticket(
        self,
        task_id: str,
        result_digest: bytes,
        worker_id: str,
        task_kcu: float,
        nonce: int = 0
    ) -> bytes:
        """
        Generate deterministic ticket hash:
        TicketHash = SHA256(task_id || result_digest || worker_id || nonce)
        """
        raw = f"{task_id}:{worker_id}:{nonce}".encode() + result_digest
        ticket_hash = hashlib.sha256(raw).digest()
        self.task_tickets.append((ticket_hash, worker_id, task_kcu))
        return ticket_hash

    def execute_draw(
        self,
        beacon_hash: bytes,
        qualified_workers_set: Set[str]
    ) -> Dict[str, Any]:
        """
        Draw L2 GPU Combo winners:
        - Filters tickets belonging only to qualified workers.
        - Calculates XOR distance to beacon_hash.
        - Allocates 70% to Grand Winner, 30% equally to runners-up (rank 2 to 10).
        """
        if self.accumulated_satk <= 0 or not self.task_tickets:
            return {
                "status": "NO_FUNDS_OR_TICKETS",
                "accumulated_satk": self.accumulated_satk,
                "grand_prize_winner": None,
                "grand_prize_satk": 0,
                "runners_up": [],
            }

        # Filter eligible tickets
        eligible_tickets = [
            (th, wid, kcu) for th, wid, kcu in self.task_tickets if wid in qualified_workers_set
        ]

        if not eligible_tickets:
            return {
                "status": "ALL_CANDIDATES_DISQUALIFIED_ROLLOVER",
                "accumulated_satk": self.accumulated_satk,
                "grand_prize_winner": None,
                "grand_prize_satk": 0,
                "runners_up": [],
                "note": "All hopping tickets pruned. Accumulated balance rolls over to next epoch.",
            }

        beacon_seed = hashlib.sha256(beacon_hash + self.epoch_index.to_bytes(8, "little")).digest()
        beacon_int = int.from_bytes(beacon_seed, "big")

        scored_tickets = []
        for th, wid, kcu in eligible_tickets:
            t_int = int.from_bytes(th, "big")
            dist = t_int ^ beacon_int
            scored_tickets.append((dist, wid, th.hex()))

        scored_tickets.sort(key=lambda x: x[0])

        grand_winner = scored_tickets[0][1]
        grand_prize_satk = int(self.accumulated_satk * 0.70)
        remaining_pool = self.accumulated_satk - grand_prize_satk

        # Runners-up (up to 9 additional unique workers)
        seen_workers = {grand_winner}
        runners_up = []
        for dist, wid, th_hex in scored_tickets[1:]:
            if wid not in seen_workers:
                seen_workers.add(wid)
                runners_up.append(wid)
            if len(runners_up) >= 9:
                break

        runners_up_payouts = []
        if runners_up:
            per_runner_satk = remaining_pool // len(runners_up)
            for r_wid in runners_up:
                runners_up_payouts.append({"worker_id": r_wid, "payout_satk": per_runner_satk})
        else:
            # If only 1 worker participated, they get 100%
            grand_prize_satk = self.accumulated_satk
            remaining_pool = 0

        payout_result = {
            "status": "DRAW_COMPLETED",
            "epoch_index": self.epoch_index,
            "total_jackpot_satk": self.accumulated_satk,
            "grand_prize_winner": grand_winner,
            "grand_prize_satk": grand_prize_satk,
            "winning_ticket_distance": hex(scored_tickets[0][0]),
            "runners_up_count": len(runners_up),
            "runners_up_payouts": runners_up_payouts,
        }

        # Clear pool upon successful distribution
        self.accumulated_satk = 0
        return payout_result


class GPUStandbySubsidyManager:
    """
    Cold-Start GPU Standby Subsidy Manager:
    Solves the Cold-Start Catch-22:
    - Phase 1 (Genesis Eco-Fund Seed Drip): Fixed daily budget (e.g. 500 BTC-K/day = 50,000,000,000 SatK/day)
      covering baseline electricity costs (15~20 CNY/day per 4-card machine) to maintain online GPU depth.
    - Phase 2 (Self-Sustaining Commercial Flow): Automatically transitions to 100% organic fee 10% split
      once real-world API fee accumulation exceeds the Eco-Fund subsidy floor.
    - Proof of Readiness (PoR) requirement: TTFT < 2.0s confirms VRAM residency; ghost nodes receive 0.
    """

    # Model Hardware Capacity Weights
    TIER_WEIGHTS = {
        "8B_INT4": 1.0,
        "14B_INT4": 1.85,
        "35B_MOE": 3.5,
        "72B_INT4": 10.0,
        "405B_FP8": 35.0,
    }

    def __init__(
        self,
        daily_eco_seed_budget_satk: int = 50_000_000_000,  # 500 BTC-K / day seed subsidy floor
        epoch_hours: int = 12,
    ):
        self.epoch_hours = epoch_hours
        self.epoch_eco_budget_satk = int(daily_eco_seed_budget_satk * (epoch_hours / 24.0))
        self.organic_fee_standby_satk = 0
        self.active_nodes: Dict[str, Dict[str, Any]] = {}

    def deposit_organic_fee(self, standby_satk: int) -> None:
        """Deposit the 10% standby cut from organic AI-RPC settlement."""
        if standby_satk > 0:
            self.organic_fee_standby_satk += standby_satk

    def register_node_activity(
        self,
        node_id: str,
        hardware_tier: str,
        payout_address: str,
        por_passed_count: int,
        por_failed_count: int,
        refused_count: int,
    ) -> None:
        """Register a node's availability and PoR performance for the epoch."""
        weight = self.TIER_WEIGHTS.get(hardware_tier, 1.0)
        self.active_nodes[node_id] = {
            "node_id": node_id,
            "hardware_tier": hardware_tier,
            "payout_address": payout_address,
            "weight": weight,
            "por_passed": por_passed_count,
            "por_failed": por_failed_count,
            "refused": refused_count,
        }

    def calculate_epoch_distribution(self) -> Dict[str, Any]:
        """
        Compute Standby Subsidy Payouts:
        Effective Pool = max(epoch_eco_budget_satk, organic_fee_standby_satk)
        """
        # Determine pool source
        if self.organic_fee_standby_satk >= self.epoch_eco_budget_satk:
            pool_source = "100%_ORGANIC_COMMERCIAL_FEES"
            effective_pool_satk = self.organic_fee_standby_satk
            eco_fund_draw_satk = 0
        else:
            pool_source = "ECO_FUND_BOOTSTRAPPING_ACTIVE"
            effective_pool_satk = self.epoch_eco_budget_satk
            eco_fund_draw_satk = self.epoch_eco_budget_satk - self.organic_fee_standby_satk

        # Filter nodes passing PoR threshold (>=95% success, 0 task refusals)
        qualified_nodes = []
        total_qualified_weight = 0.0

        for nid, info in self.active_nodes.items():
            if info["refused"] > 0:
                continue  # SLA breach: task refusal slashes standby pay
            total_probes = info["por_passed"] + info["por_failed"]
            if total_probes == 0:
                continue
            pass_rate = info["por_passed"] / float(total_probes)
            if pass_rate >= 0.95:
                qualified_nodes.append(info)
                total_qualified_weight += info["weight"]

        payouts: List[Dict[str, Any]] = []
        if total_qualified_weight > 0 and effective_pool_satk > 0:
            for node in qualified_nodes:
                share_ratio = node["weight"] / total_qualified_weight
                node_satk = int(effective_pool_satk * share_ratio)
                payouts.append({
                    "node_id": node["node_id"],
                    "payout_address": node["payout_address"],
                    "hardware_tier": node["hardware_tier"],
                    "weight": node["weight"],
                    "payout_satk": node_satk,
                    "payout_btck": round(node_satk / 1e8, 6),
                })

        return {
            "pool_source": pool_source,
            "effective_pool_satk": effective_pool_satk,
            "organic_fee_satk": self.organic_fee_standby_satk,
            "eco_fund_draw_satk": eco_fund_draw_satk,
            "total_registered_nodes": len(self.active_nodes),
            "qualified_nodes_count": len(qualified_nodes),
            "total_qualified_weight": round(total_qualified_weight, 2),
            "node_payouts": payouts,
        }

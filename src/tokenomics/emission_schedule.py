"""
BTC-K Canonical Tokenomics & 4-Year Rational Smooth Decay Engine (SSOT v2.2.0)
------------------------------------------------------------------------------
Implements the definitive, mathematical consensus specification (Doc 29 & Canonical Whitepaper):
1. 21,000,000.00000000 BTC-K (2,100,000,000,000,000 SatK) Absolute Hard Cap (0 SatK drift).
2. 4-Year Total Mining Lifespan (1460 Days, 126,144,000 Blocks @ 1.0 bps).
3. Rational Smooth Decay: R(t) = R0 / (1 + k * t_day) where k = 0.15 day^-1.
4. Deterministic uint64 integer SatK daily quota table (No floating-point consensus drift).
5. Block #0 (Genesis) has strictly 0.00000000 BTC-K coinbase issuance.
6. Post-4-Year Era (Block 126,144,001+): Subsidy strictly drops to 0 SatK; network transitions to fee-driven continuous BlockDAG.
7. Tiered 4-Stage Combo Jackpot (Year 1: 20% / 12h, Year 2: 30% / 36h, Year 3: 40% / 108h, Year 4: 50% / 324h).
8. Future Random Beacon (+100 blocks) + Kademlia XOR Geometric Distance Winner Selection.
9. Solvency Invariant: Total Minted == Total Miner Payouts + Remaining Pool Balance (Zero inflation).
"""

import os
import json
import math
import hashlib
from dataclasses import dataclass
from typing import Tuple, List, Dict, Any, Optional

# =====================================================================
# 1. Global Accounting Constants (Integer SatK, 100M SatK = 1 BTC-K)
# =====================================================================
SATK_PER_COIN = 100_000_000
SATS_PER_COIN = SATK_PER_COIN  # Backward compatibility alias
TOTAL_HARD_CAP_SATK = 21_000_000 * SATK_PER_COIN  # 2,100,000,000,000,000 SatK (2.1 Quadrillion)
TOTAL_HARD_CAP_SATS = TOTAL_HARD_CAP_SATK

BLOCKS_PER_DAY = 86_400  # 1.0 second per block
LIFESPAN_DAYS = 1460     # 4 years (365 * 4 = 1460 days)
TOTAL_BLOCKS = LIFESPAN_DAYS * BLOCKS_PER_DAY  # 126,144,000 blocks
BLOCKS_PER_YEAR = 365 * BLOCKS_PER_DAY        # 31,536,000 blocks
COMBO_MULTIPLIER = 10.0                        # Backward compatibility alias

# Mathematical Rational Decay Parameters
K_PER_DAY = 0.15  # Daily damping factor (k = 0.15 day^-1)
LN_220 = math.log(1.0 + K_PER_DAY * LIFESPAN_DAYS)  # ln(1 + 0.15 * 1460) = ln(220)

# Genesis 72-Hour Security Reserve Target
GENESIS_72H_BLOCKS = 72 * 3600  # 259,200 blocks (3 days)

# =====================================================================
# 2. Deterministic Integer SatK Issuance Table (SSOT Daily Quotas)
# =====================================================================
def _generate_canonical_daily_quotas() -> List[int]:
    """
    Deterministically computes the daily integer SatK quotas for all 1460 days.
    Guarantees mathematically that sum(daily_quotas) == TOTAL_HARD_CAP_SATK with ZERO drift.
    """
    quotas: List[int] = []
    prev_cumulative = 0
    for d in range(LIFESPAN_DAYS):
        if d == LIFESPAN_DAYS - 1:
            # Final day absorbs any fractional rounding, guaranteeing exact 2.1 Quadrillion
            cur_cumulative = TOTAL_HARD_CAP_SATK
        else:
            # Cumulative integral fraction: F(d) = ln(1 + k*(d+1)) / ln(220)
            target_frac = math.log(1.0 + K_PER_DAY * (d + 1)) / LN_220
            cur_cumulative = int(round(TOTAL_HARD_CAP_SATK * target_frac))
        
        daily_amount = cur_cumulative - prev_cumulative
        quotas.append(daily_amount)
        prev_cumulative = cur_cumulative

    return quotas

DAILY_QUOTAS_SATK: List[int] = _generate_canonical_daily_quotas()
assert len(DAILY_QUOTAS_SATK) == LIFESPAN_DAYS, "Daily quotas length mismatch!"
assert sum(DAILY_QUOTAS_SATK) == TOTAL_HARD_CAP_SATK, "Hard cap conservation violated!"

# Canonical SHA-256 Checksum of the 1460 daily quota schedule
CANONICAL_DAILY_QUOTAS_SHA256 = hashlib.sha256(json.dumps(DAILY_QUOTAS_SATK).encode()).hexdigest()
assert CANONICAL_DAILY_QUOTAS_SHA256 == "7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59"

# =====================================================================
# 3. Combo Tiered Stages & Draw Schedules (Audit v3 & Doc 29)
# =====================================================================
@dataclass
class ComboStageSpec:
    stage_id: int
    year: int
    start_block: int
    end_block: int
    combo_ratio: float
    draw_interval_blocks: int
    draw_interval_hours: int
    maturation_hours: int
    maturation_blocks: int
    instant_unlock_ratio: float
    streaming_tranches: int

COMBO_STAGES: List[ComboStageSpec] = [
    ComboStageSpec(1, 1, 1, 31_536_000, 0.20, 43_200, 12, 24, 86_400, 1.00, 1),
    ComboStageSpec(2, 2, 31_536_001, 63_072_000, 0.30, 129_600, 36, 72, 259_200, 0.34, 2),
    ComboStageSpec(3, 3, 63_072_001, 94_608_000, 0.40, 388_800, 108, 216, 777_600, 0.10, 9),
    ComboStageSpec(4, 4, 94_608_001, 126_144_000, 0.50, 1_166_400, 324, 648, 2_332_800, 0.10, 9),
]

def get_combo_spec_for_height(block_height: int) -> Tuple[int, float, int]:
    """
    Returns (stage_id, combo_ratio, draw_interval_blocks) for a given height.
    For post-4-year era (block > 126,144,000), returns fee lottery specs.
    """
    if block_height <= 0:
        return (1, 0.20, 43_200)
    for stage in COMBO_STAGES:
        if stage.start_block <= block_height <= stage.end_block:
            return (stage.stage_id, stage.combo_ratio, stage.draw_interval_blocks)
    # Post-4-Year fee era (Subsidy is 0; transaction fee lottery)
    return (5, 0.50, 1_166_400)

# =====================================================================
# 4. Block Budget & Subsidy Query Engine
# =====================================================================
def get_block_budget_sats(block_height: int) -> int:
    """
    Returns the protocol nominal block issuance budget in uint64 integer SatK.
    
    Invariants:
    - Block #0 (Genesis): strictly 0 SatK.
    - Blocks 1..126,144,000: strictly derived from DAILY_QUOTAS_SATK.
    - Blocks 126,144,001+: strictly 0 SatK (subsidy fully terminated).
    """
    if block_height < 0:
        raise ValueError("Block height cannot be negative")
    if block_height == 0:
        return 0  # Genesis Block #0 has 0 coinbase issuance
    if block_height > TOTAL_BLOCKS:
        return 0  # Post-4-year subsidy era is strictly 0

    day_idx = (block_height - 1) // BLOCKS_PER_DAY
    offset_in_day = (block_height - 1) % BLOCKS_PER_DAY
    daily_quota = DAILY_QUOTAS_SATK[day_idx]
    
    base_per_block = daily_quota // BLOCKS_PER_DAY
    remainder = daily_quota % BLOCKS_PER_DAY
    
    # Deterministic integer distribution of remainder SatK
    if offset_in_day < remainder:
        return base_per_block + 1
    return base_per_block

def get_base_block_reward_sats(block_height: int) -> int:
    """Nominal block subsidy budget for the given height."""
    return get_block_budget_sats(block_height)

# =====================================================================
# 5. Future Random Beacon & XOR Geometric Distance Lottery Selection
# =====================================================================
def compute_beacon_xor_winner(
    candidate_block_hashes: List[bytes],
    beacon_hash: bytes,
    epoch_index: int = 1
) -> Tuple[bytes, int]:
    """
    Anti-Monopoly Future Random Beacon with Kademlia XOR Geometric Distance Selection.
    
    Properties:
    1. Distance(BlockHash_i, BeaconSeed) = BlockHash_i XOR BeaconSeed
    2. Winner = argmin_i(Distance_i) across all valid blocks in drawing epoch window.
    3. Guarantees uniform geometric probability space for every valid candidate block.
    """
    if not candidate_block_hashes:
        raise ValueError("Candidate block list cannot be empty")
    
    # Deterministic Beacon Seed mixing epoch index
    beacon_seed = hashlib.sha256(beacon_hash + epoch_index.to_bytes(8, 'little')).digest()
    beacon_int = int.from_bytes(beacon_seed, 'big')
    
    best_dist = -1
    winning_hash = candidate_block_hashes[0]
    
    for block_hash in candidate_block_hashes:
        block_int = int.from_bytes(block_hash, 'big')
        dist = block_int ^ beacon_int
        if best_dist == -1 or dist < best_dist:
            best_dist = dist
            winning_hash = block_hash
            
    return winning_hash, best_dist

def is_combo_block(block_height: int, block_hash: bytes = b"") -> bool:
    """
    Evaluates whether the given block is a Combo Jackpot draw block.
    Aligns to the start of each stage, and triggers a final liquidation draw at the final block of Year 4.
    """
    if block_height <= 0:
        return False
    if block_height == TOTAL_BLOCKS:
        return True  # Final liquidation draw sweeps any remaining pool balance
    for stage in COMBO_STAGES:
        if stage.start_block <= block_height <= stage.end_block:
            offset = block_height - stage.start_block + 1
            return (offset % stage.draw_interval_blocks) == 0
    return False

def compute_jackpot_vesting_tranches(
    stage_id: int,
    jackpot_amount_satk: int,
    draw_block_height: int
) -> List[Dict[str, Any]]:
    """
    Generates the streaming vesting schedule for jackpot payouts to eliminate secondary market dumps.
    Total of all tranches amounts strictly equals jackpot_amount_satk.
    """
    for spec in COMBO_STAGES:
        if spec.stage_id == stage_id:
            tranches = []
            instant_amount = int(jackpot_amount_satk * spec.instant_unlock_ratio)
            streaming_amount = jackpot_amount_satk - instant_amount
            
            # Initial instant unlock at draw_block_height
            tranches.append({
                "tranche_index": 0,
                "unlock_block_height": draw_block_height,
                "amount_satk": instant_amount,
                "is_instant": True
            })
            
            if spec.streaming_tranches >= 1 and streaming_amount > 0:
                tranche_interval = spec.maturation_blocks // spec.streaming_tranches
                per_tranche = streaming_amount // spec.streaming_tranches
                rem = streaming_amount % spec.streaming_tranches
                
                for t in range(1, spec.streaming_tranches + 1):
                    amt = per_tranche + (1 if t <= rem else 0)
                    tranches.append({
                        "tranche_index": t,
                        "unlock_block_height": draw_block_height + t * tranche_interval,
                        "amount_satk": amt,
                        "is_instant": False
                    })
            return tranches

    # Default fallback: 100% unlocked after standard maturity
    return [{
        "tranche_index": 0,
        "unlock_block_height": draw_block_height + 86400,
        "amount_satk": jackpot_amount_satk,
        "is_instant": True
    }]

# =====================================================================
# 6. Consensus Issuance & Jackpot Accounting Result
# =====================================================================
@dataclass
class BlockIssuanceResult:
    block_height: int
    is_combo: bool
    budget_sats: int               # Protocol nominal budget
    minted_sats: int               # Actual newly minted amount (strictly <= remaining cap)
    miner_base_sats: int           # Immediate base payout to miner (budget - pool_deposit)
    pool_deposit_sats: int         # Ratio deposit into consensus jackpot pool
    combo_payout_sats: int         # Jackpot bonus paid out of pool balance
    miner_total_payout_sats: int   # miner_base_sats + combo_payout_sats
    new_pool_balance_sats: int     # Updated consensus jackpot pool balance
    new_issued_supply_sats: int    # Updated cumulative issued supply

def compute_block_issuance(
    block_height: int,
    block_hash: bytes,
    current_issued_sats: int,
    current_pool_balance_sats: int,
    force_combo: bool = False
) -> BlockIssuanceResult:
    """
    Core Consensus Combo Jackpot Pool and Supply Invariant Engine.
    
    Mathematical Rules:
    1. minted_sats = min(get_block_budget_sats(height), TOTAL_HARD_CAP_SATK - current_issued_sats)
    2. stage_id, combo_ratio, draw_interval = get_combo_spec_for_height(height)
    3. pool_deposit_sats = int(minted_sats * combo_ratio)
    4. miner_base_sats = minted_sats - pool_deposit_sats (strictly satisfies miner_base + pool_deposit == minted_sats)
    5. available_pool = current_pool_balance_sats + pool_deposit_sats
    6. is_combo = force_combo or is_combo_block(height, block_hash)
    7. If is_combo:
         combo_payout_sats = available_pool (awarded to the winning block of this epoch)
       Else:
         combo_payout_sats = 0
    8. new_pool_balance_sats = available_pool - combo_payout_sats
    9. miner_total_payout_sats = miner_base_sats + combo_payout_sats
    10. new_issued_supply_sats = current_issued_sats + minted_sats
    
    Strict Consensus Invariants:
    - minted_sats is INDEPENDENT of combo! Zero tokens are ever created from thin air.
    - miner_base_sats + pool_deposit_sats == minted_sats
    - miner_total_payout_sats + new_pool_balance_sats == current_pool_balance_sats + minted_sats
    - new_issued_supply_sats <= TOTAL_HARD_CAP_SATK
    - new_pool_balance_sats >= 0
    """
    if block_height < 0:
        raise ValueError("Block height cannot be negative")
    
    budget = get_block_budget_sats(block_height)
    remaining_cap = max(0, TOTAL_HARD_CAP_SATK - current_issued_sats)
    minted = min(budget, remaining_cap)
    
    _, combo_ratio, _ = get_combo_spec_for_height(block_height)
    pool_deposit = int(minted * combo_ratio)
    miner_base = minted - pool_deposit
    
    available_pool = current_pool_balance_sats + pool_deposit
    is_combo = force_combo or is_combo_block(block_height, block_hash)
    
    if is_combo and available_pool > 0:
        combo_payout = available_pool
    else:
        combo_payout = 0
        
    new_pool_balance = available_pool - combo_payout
    miner_total = miner_base + combo_payout
    new_issued_supply = current_issued_sats + minted
    
    # Assert consensus invariants
    assert miner_base + pool_deposit == minted, "Minted partition invariant breached"
    assert miner_total + new_pool_balance == current_pool_balance_sats + minted, "Jackpot solvency conservation breached"
    assert new_issued_supply <= TOTAL_HARD_CAP_SATK, "Total hard cap breached"
    assert new_pool_balance >= 0, "Negative pool balance detected"
    
    return BlockIssuanceResult(
        block_height=block_height,
        is_combo=is_combo,
        budget_sats=budget,
        minted_sats=minted,
        miner_base_sats=miner_base,
        pool_deposit_sats=pool_deposit,
        combo_payout_sats=combo_payout,
        miner_total_payout_sats=miner_total,
        new_pool_balance_sats=new_pool_balance,
        new_issued_supply_sats=new_issued_supply
    )

def get_block_reward_sats(
    block_height: int,
    block_hash: bytes,
    current_pool_balance_sats: Optional[int] = None
) -> Tuple[int, bool]:
    """Returns (miner_payout_sats, is_combo)."""
    budget_sats = get_block_budget_sats(block_height)
    is_combo = is_combo_block(block_height, block_hash)
    _, combo_ratio, _ = get_combo_spec_for_height(block_height)
    pool_dep = int(budget_sats * combo_ratio)
    miner_base = budget_sats - pool_dep
    
    if current_pool_balance_sats is not None:
        available = current_pool_balance_sats + pool_dep
        payout = miner_base + (available if is_combo else 0)
        return payout, is_combo
        
    return budget_sats, is_combo

def get_block_reward_btck(
    block_height: int,
    block_hash: bytes,
    current_pool_balance_sats: Optional[int] = None
) -> Tuple[float, bool]:
    """Returns (miner_payout_btck, is_combo) in human-readable coin units."""
    sats, is_combo = get_block_reward_sats(block_height, block_hash, current_pool_balance_sats)
    return sats / SATK_PER_COIN, is_combo


"""
BTC-K Consensus Combo Jackpot State Machine (P1-01 / Chapter 4)
--------------------------------------------------------------
Features:
1. Deterministic Epoch State Machine with candidate block accumulation.
2. Future Random Beacon (+100 blocks delay):
   - At draw height H_draw: candidate window freezes, jackpot balance freezes.
   - At beacon height H_draw + 100: block hash at H_beacon acts as the ungrindable beacon seed.
3. Kademlia XOR Geometric Distance winner selection (argmin distance).
4. Streaming Vesting Tranches creation (1 instant + streaming batches over maturation period).
5. Stage-boundary alignment (Stage 1: 43200, Stage 2: 129600, Stage 3: 388800, Stage 4: 1166400).
6. Final Liquidation Sweep at Block #126,144,000 ensuring consensus pool balance terminates at 0.
7. Full Reversibility: Reorganization unrolls state changes cleanly.
"""

from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
import hashlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tokenomics.emission_schedule import (
    is_combo_block,
    get_combo_spec_for_height,
    compute_beacon_xor_winner,
    compute_jackpot_vesting_tranches,
    TOTAL_BLOCKS,
    SATK_PER_COIN
)

FUTURE_BEACON_DELAY_BLOCKS = 100

@dataclass
class LotteryResolution:
    epoch_id: int
    stage_id: int
    draw_height: int
    beacon_height: int
    beacon_hash: str
    winner_block_hash: str
    winner_distance: int
    jackpot_amount_satk: int
    tranches: List[Dict[str, Any]]

@dataclass
class ComboEpoch:
    epoch_id: int
    stage_id: int
    start_height: int
    draw_height: int = 0
    beacon_height: int = 0
    frozen_jackpot_satk: int = 0
    candidates: List[str] = field(default_factory=list)
    is_frozen: bool = False
    is_resolved: bool = False
    resolution: Optional[LotteryResolution] = None

class ComboStateMachine:
    """
    Consensus state machine orchestrating fair, beacon-grounded jackpot draws.
    """
    def __init__(self):
        self.active_epoch_id: int = 1
        self.epochs: Dict[int, ComboEpoch] = {}
        self.pending_draws: List[int] = []  # List of epoch_ids awaiting beacon block
        self.resolutions_by_beacon_height: Dict[int, LotteryResolution] = {}
        
        # Initialize epoch 1
        stage_id, _, _ = get_combo_spec_for_height(1)
        self.epochs[1] = ComboEpoch(
            epoch_id=1,
            stage_id=stage_id,
            start_height=1
        )

    def process_block(
        self,
        block_height: int,
        block_hash: str,
        current_pool_balance_satk: int,
        pool_deposit_satk: int
    ) -> Tuple[Optional[LotteryResolution], int]:
        """
        Advances the lottery state machine with the newly added block to the virtual selected chain.
        Returns:
            (LotteryResolution or None, pool_deduction_satk)
        """
        if block_height <= 0:
            return None, 0

        current_epoch = self.epochs[self.active_epoch_id]
        if not current_epoch.is_frozen:
            current_epoch.candidates.append(block_hash)

        # 1. Check if current block triggers a scheduled jackpot draw
        is_draw = is_combo_block(block_height)
        if is_draw and not current_epoch.is_frozen:
            # Freeze active epoch
            current_epoch.draw_height = block_height
            current_epoch.beacon_height = block_height + FUTURE_BEACON_DELAY_BLOCKS
            current_epoch.frozen_jackpot_satk = current_pool_balance_satk + pool_deposit_satk
            current_epoch.is_frozen = True
            self.pending_draws.append(current_epoch.epoch_id)

            # Start next epoch
            next_epoch_id = self.active_epoch_id + 1
            next_stage_id, _, _ = get_combo_spec_for_height(block_height + 1)
            self.epochs[next_epoch_id] = ComboEpoch(
                epoch_id=next_epoch_id,
                stage_id=next_stage_id,
                start_height=block_height + 1
            )
            self.active_epoch_id = next_epoch_id

        # 2. Check if any frozen epoch matches current block as its future beacon
        resolved_epoch_id = None
        for eid in self.pending_draws:
            epoch = self.epochs[eid]
            if epoch.beacon_height == block_height:
                resolved_epoch_id = eid
                break

        if resolved_epoch_id is not None:
            self.pending_draws.remove(resolved_epoch_id)
            epoch = self.epochs[resolved_epoch_id]

            # Compute winner using future beacon block hash
            beacon_bytes = bytes.fromhex(block_hash)
            candidate_bytes = [bytes.fromhex(c) for c in epoch.candidates]

            winning_bytes, min_dist = compute_beacon_xor_winner(
                candidate_bytes,
                beacon_bytes,
                epoch.epoch_id
            )
            winning_hash = winning_bytes.hex()

            # Compute streaming vesting tranches
            tranches = compute_jackpot_vesting_tranches(
                epoch.stage_id,
                epoch.frozen_jackpot_satk,
                block_height
            )

            resolution = LotteryResolution(
                epoch_id=epoch.epoch_id,
                stage_id=epoch.stage_id,
                draw_height=epoch.draw_height,
                beacon_height=block_height,
                beacon_hash=block_hash,
                winner_block_hash=winning_hash,
                winner_distance=min_dist,
                jackpot_amount_satk=epoch.frozen_jackpot_satk,
                tranches=tranches
            )

            epoch.is_resolved = True
            epoch.resolution = resolution
            self.resolutions_by_beacon_height[block_height] = resolution

            return resolution, epoch.frozen_jackpot_satk

        return None, 0

    def revert_to_height(self, target_height: int):
        """
        Rollback state machine to target_height during a chain reorganization.
        """
        # Revert resolutions that occurred after target_height
        beacons_to_remove = [h for h in self.resolutions_by_beacon_height if h > target_height]
        for h in beacons_to_remove:
            res = self.resolutions_by_beacon_height.pop(h)
            epoch = self.epochs.get(res.epoch_id)
            if epoch:
                epoch.is_resolved = False
                epoch.resolution = None
                if epoch.epoch_id not in self.pending_draws:
                    self.pending_draws.append(epoch.epoch_id)

        # Revert draws that froze after target_height
        epochs_to_unfreeze = []
        for eid, epoch in list(self.epochs.items()):
            if epoch.is_frozen and epoch.draw_height > target_height:
                epochs_to_unfreeze.append(eid)

        for eid in epochs_to_unfreeze:
            if eid in self.pending_draws:
                self.pending_draws.remove(eid)
            # Delete any newer epochs created after this one
            newer_eids = [k for k in self.epochs if k > eid]
            for nk in newer_eids:
                self.epochs.pop(nk, None)
            
            epoch = self.epochs[eid]
            epoch.is_frozen = False
            epoch.draw_height = 0
            epoch.beacon_height = 0
            epoch.frozen_jackpot_satk = 0
            self.active_epoch_id = eid

        # Candidate hashes are consensus state too.  Trim each surviving epoch
        # to the number of canonical heights that still belong to it so a
        # same-height replacement cannot leave an orphan candidate behind.
        for epoch in self.epochs.values():
            last_kept_height = min(
                target_height,
                epoch.draw_height if epoch.is_frozen else target_height,
            )
            keep_count = max(0, last_kept_height - epoch.start_height + 1)
            del epoch.candidates[keep_count:]
        self.pending_draws.sort()

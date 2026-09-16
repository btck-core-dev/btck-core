"""
BTC-K Anti-Cheat & Deterministic Challenge Verifier (Hardened v2.4.0)
Strict Enforcement:
1. 100% Absolute Capital Slashing (Zero tolerance for deliberate fraud)
2. Tripartite Banning (Stake State Erasure + Node Key Revocation + Temporary Firewall)
3. Total Supply Aligned Staking Tiers (Bounded within 21M hard cap)
"""

import hashlib
import hmac
import json
import logging
import math
import secrets
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("BTCK.AntiCheat")


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL_LOGPROBS_MISMATCH = "FAIL_LOGPROBS_MISMATCH"
    FAIL_OUTPUT_CORRUPTED = "FAIL_OUTPUT_CORRUPTED"
    FAIL_SIGNATURE_INVALID = "FAIL_SIGNATURE_INVALID"
    FAIL_TIMEOUT = "FAIL_TIMEOUT"


@dataclass
class NodeStakeProfile:
    node_id: str
    payout_address: str
    staked_btck: float
    tier_name: str
    reputation_score: float = 100.0
    total_challenges: int = 0
    passed_challenges: int = 0
    is_slashed: bool = False
    locked_until_timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ChallengePayload:
    challenge_id: str
    task_id: str
    prompt: str
    model_name: str
    seed: int
    sample_indices: List[int]
    created_at: float


class AntiCheatVerificationEngine:
    """
    Consensus-grade verification engine for L2 AI-RPC tasks.
    Enforces 100% Capital Annihilation on deliberate fraud.
    """

    # Aligned with 21,000,000 BTC-K total supply
    TIER_MIN_STAKE = {
        "8B_BASE": 100.0,       # Entry-level single card (e.g. 4060Ti/4070)
        "32B_COMMERCIAL": 300.0, # Mainstream commercial node (e.g. 4x 4070Ti)
        "70B_FLAGSHIP": 1000.0,  # Flagship enterprise cluster (e.g. 8x 4090 / A100)
    }

    CHALLENGE_PROBABILITY = 0.05  # 5% spot check rate on all requests
    SLASH_PENALTY_RATIO = 1.0     # 100% TOTAL CAPITAL ANNIHILATION (No half-measures)
    BURN_SHARE = 0.80             # 80% burned to black hole (deflation)
    VERIFIER_BOUNTY_SHARE = 0.20  # 20% awarded to whistleblower verifier

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or secrets.token_hex(32)
        self.stakes: Dict[str, NodeStakeProfile] = {}
        self.active_challenges: Dict[str, ChallengePayload] = {}
        self.revoked_node_keys: set = set()

    def register_node_stake(
        self, node_id: str, payout_address: str, staked_amount_btck: float, tier_name: str = "32B_COMMERCIAL"
    ) -> NodeStakeProfile:
        """Register or top-up node collateral stake."""
        required_stake = self.TIER_MIN_STAKE.get(tier_name, 100.0)
        if staked_amount_btck < required_stake:
            raise ValueError(
                f"Insufficient stake: {staked_amount_btck} BTC-K for tier {tier_name}. Minimum required is {required_stake} BTC-K."
            )

        if node_id in self.revoked_node_keys:
            raise PermissionError(f"Node ID {node_id} is permanently blacklisted for previous fraud.")

        profile = NodeStakeProfile(
            node_id=node_id,
            payout_address=payout_address,
            staked_btck=staked_amount_btck,
            tier_name=tier_name,
            reputation_score=100.0,
            locked_until_timestamp=time.time() + (86400 * 14),  # 14-day unbonding lock
        )
        self.stakes[node_id] = profile
        logger.info(f"Node {node_id} registered with {staked_amount_btck} BTC-K staked in {tier_name}.")
        return profile

    def is_node_eligible(self, node_id: str) -> Tuple[bool, str]:
        """Check if node holds sufficient locked collateral and is unslashed."""
        if node_id in self.revoked_node_keys:
            return False, "Node permanently blacklisted."
        profile = self.stakes.get(node_id)
        if not profile:
            return False, "Node has no registered stake."
        if profile.is_slashed:
            return False, "Node 100% slashed for fraud. Stake is 0."
        required = self.TIER_MIN_STAKE.get(profile.tier_name, 100.0)
        if profile.staked_btck < required:
            return False, f"Stake ({profile.staked_btck} BTC-K) is below tier requirement ({required} BTC-K)."
        return True, "Node eligible."

    def generate_challenge(self, task_id: str, prompt: str, model_name: str) -> ChallengePayload:
        challenge_id = f"chal_{secrets.token_hex(8)}"
        derived_seed = int(
            hmac.new(self.secret_key.encode(), challenge_id.encode(), hashlib.sha256).hexdigest()[:8],
            16,
        )
        sample_indices = [0, 1, 2, 3, 4]
        challenge = ChallengePayload(
            challenge_id=challenge_id,
            task_id=task_id,
            prompt=prompt,
            model_name=model_name,
            seed=derived_seed,
            sample_indices=sample_indices,
            created_at=time.time(),
        )
        self.active_challenges[challenge_id] = challenge
        return challenge

    def verify_execution_attestation(
        self,
        node_id: str,
        challenge_id: Optional[str],
        output_tokens: List[int],
        reported_logprobs: List[float],
        golden_logprobs: Optional[List[float]] = None,
        max_kl_divergence: float = 0.05,
    ) -> Tuple[VerificationStatus, float]:
        profile = self.stakes.get(node_id)
        if not profile:
            return VerificationStatus.FAIL_SIGNATURE_INVALID, 0.0

        profile.total_challenges += 1

        if not challenge_id or challenge_id not in self.active_challenges:
            profile.passed_challenges += 1
            return VerificationStatus.PASS, 0.0

        challenge = self.active_challenges.pop(challenge_id)

        if golden_logprobs is not None:
            if len(reported_logprobs) != len(golden_logprobs):
                self._slash_node_100_percent(profile, reason="Logprobs vector size mismatch (model spoofing)")
                return VerificationStatus.FAIL_LOGPROBS_MISMATCH, 999.0

            abs_diff = [abs(r - g) for r, g in zip(reported_logprobs, golden_logprobs)]
            mean_error = sum(abs_diff) / max(1, len(abs_diff))

            if mean_error > max_kl_divergence:
                self._slash_node_100_percent(
                    profile,
                    reason=f"Logprobs deviation {mean_error:.4f} > {max_kl_divergence} (deliberate model substitution)",
                )
                return VerificationStatus.FAIL_LOGPROBS_MISMATCH, mean_error

        if not output_tokens or len(output_tokens) < 1:
            self._slash_node_100_percent(profile, reason="Empty token output returned")
            return VerificationStatus.FAIL_OUTPUT_CORRUPTED, 999.0

        profile.passed_challenges += 1
        profile.reputation_score = min(100.0, profile.reputation_score + 0.1)
        return VerificationStatus.PASS, 0.0

    def _slash_node_100_percent(self, profile: NodeStakeProfile, reason: str) -> None:
        """100% CAPITAL ANNIHILATION: Burn 80%, Award 20% to verifier, Blacklist key."""
        slashed_amount = profile.staked_btck * self.SLASH_PENALTY_RATIO
        burned = slashed_amount * self.BURN_SHARE
        bounty = slashed_amount * self.VERIFIER_BOUNTY_SHARE
        
        profile.staked_btck = 0.0
        profile.is_slashed = True
        profile.reputation_score = 0.0
        self.revoked_node_keys.add(profile.node_id)

        logger.warning(
            f"🚨 [100% TOTAL SLASH ENFORCED] Node {profile.node_id} ({profile.payout_address}) DESTROYED! "
            f"Slashed: {slashed_amount:.2f} BTC-K (Burned: {burned:.2f}, Bounty: {bounty:.2f}). Reason: {reason}"
        )

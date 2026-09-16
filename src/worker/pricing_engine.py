"""
BTC-K Tiered Pricing Engine
Calculates compute costs in SatK based on hardware tier, model variant (standard vs uncensored),
and version lifecycle decay factor.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


class ModelTier(int, Enum):
    TIER_0 = 0  # 1.5B ~ 3B / Embedding (0.5x)
    TIER_1 = 1  # 7B ~ 8B (1.0x Baseline)
    TIER_2 = 2  # 14B ~ 32B (5.0x Advanced)
    TIER_3 = 3  # 70B ~ 72B (18.0x Flagship)
    TIER_4 = 4  # 405B ~ 671B MoE (50.0x Supercluster)


class ModelVariant(str, Enum):
    STANDARD = "standard"                  # 1.0x
    UNCENSORED = "uncensored_abliterated"  # 3.5x Unrestricted Geek Premium


@dataclass
class CostBreakdown:
    total_satk: int
    prompt_satk: int
    completion_satk: int
    base_price_per_1k: int
    tier_multiplier: float
    variant_multiplier: float
    decay_factor: float
    effective_rate_per_1k: float

    def to_dict(self) -> Dict:
        return {
            "total_satk": self.total_satk,
            "prompt_satk": self.prompt_satk,
            "completion_satk": self.completion_satk,
            "base_price_per_1k": self.base_price_per_1k,
            "tier_multiplier": self.tier_multiplier,
            "variant_multiplier": self.variant_multiplier,
            "decay_factor": round(self.decay_factor, 4),
            "effective_rate_per_1k": round(self.effective_rate_per_1k, 2),
        }


class TieredPricingEngine:
    """
    Economic engine for pricing decentralized AI compute.
    """

    # 10 SatK per 1,000 tokens as base baseline
    BASE_SATK_PER_1K = 10

    TIER_MULTIPLIERS = {
        ModelTier.TIER_0: 0.5,
        ModelTier.TIER_1: 1.0,
        ModelTier.TIER_2: 5.0,
        ModelTier.TIER_3: 18.0,
        ModelTier.TIER_4: 50.0,
    }

    VARIANT_MULTIPLIERS = {
        ModelVariant.STANDARD: 1.0,
        ModelVariant.UNCENSORED: 3.5,
    }

    # Completion tokens cost 1.5x of input prompt tokens (reasoning FLOPs overhead)
    COMPLETION_PREMIUM = 1.5

    # Minimum floor for old deprecated model versions
    DECAY_FLOOR = 0.30

    @classmethod
    def calculate_decay_factor(cls, is_flagship: bool = True, deprecation_days: int = 0) -> float:
        """
        Calculate version age decay:
        - Active flagship models: 1.0x
        - Deprecated older models: decays by ~2.33% daily over 30 days down to 0.3x floor.
        """
        if is_flagship or deprecation_days <= 0:
            return 1.0

        decay = 1.0 - (deprecation_days * (0.70 / 30.0))
        return max(cls.DECAY_FLOOR, round(decay, 4))

    @classmethod
    def calculate_cost(
        cls,
        prompt_tokens: int,
        completion_tokens: int,
        tier: ModelTier = ModelTier.TIER_1,
        variant: ModelVariant = ModelVariant.STANDARD,
        is_flagship: bool = True,
        deprecation_days: int = 0,
    ) -> CostBreakdown:
        """
        Calculate the exact SatK requirement for an inference job.
        """
        tier_mult = cls.TIER_MULTIPLIERS.get(tier, 1.0)
        variant_mult = cls.VARIANT_MULTIPLIERS.get(variant, 1.0)
        decay = cls.calculate_decay_factor(is_flagship, deprecation_days)

        effective_multiplier = tier_mult * variant_mult * decay
        effective_rate_per_1k = cls.BASE_SATK_PER_1K * effective_multiplier

        # Calculate cost per token (SatK is an integer, so we scale and round to uint64 SatK)
        # Prompt tokens:
        prompt_cost = (prompt_tokens / 1000.0) * effective_rate_per_1k
        # Completion tokens (with FLOPs premium):
        completion_cost = (completion_tokens / 1000.0) * effective_rate_per_1k * cls.COMPLETION_PREMIUM

        prompt_satk = max(1, int(round(prompt_cost))) if prompt_tokens > 0 else 0
        completion_satk = max(1, int(round(completion_cost))) if completion_tokens > 0 else 0
        total_satk = prompt_satk + completion_satk

        return CostBreakdown(
            total_satk=total_satk,
            prompt_satk=prompt_satk,
            completion_satk=completion_satk,
            base_price_per_1k=cls.BASE_SATK_PER_1K,
            tier_multiplier=tier_mult,
            variant_multiplier=variant_mult,
            decay_factor=decay,
            effective_rate_per_1k=effective_rate_per_1k,
        )

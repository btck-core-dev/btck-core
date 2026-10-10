"""
BTC-K Tiered Pricing & Dynamic BigTech Oracle Engine
---------------------------------------------------
Calculates compute costs in SatK based on canonical relative KCU multipliers (8B = 1.00 KCU),
anchored to real-time commercial BigTech API benchmarks (OpenAI / Anthropic) via a dynamic oracle.

Key Tokenomics Pillars:
- Base Canonical Standard: Qwen 8B Standard = 1.00 KCU (1.0x baseline anchor)
- 100% Forbidden: Never hardcode absolute fiat prices into consensus or models
- Dynamic BigTech Oracle (Elastic Peg):
    * Tracks commercial prices (GPT-4o, GPT-4o-mini, Claude 3.5 Sonnet)
    * When BigTech drops prices (-30%, -50% price wars), BTC-K automatically tracks and drops
    * When BigTech increases prices (+20%), BTC-K automatically tracks and increases
    * Standard models maintain 20% ~ 33% (1/5 ~ 1/3) of commercial benchmarks
    * Uncensored / Abliterated models maintain 50% ~ 60%+ of commercial benchmarks
    * Hardware Power Floor: Safeguards miner electricity costs (never dumps below cost)
- v2.4 Five-Way Revenue Distribution: provider, standby, AI Combo, burn, and ecosystem
- Sub-7B Anti-Spam Floor: Strict penalty floor (0.10 KCU) to eliminate toy model hash farming
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from enum import Enum
from math import gcd
from typing import Dict, Tuple, Optional, Any

from tokenomics.emission_schedule import BLOCKS_PER_YEAR


class ModelTier(int, Enum):
    TIER_0 = 0  # 1.5B ~ 3B / Embedding (Anti-spam penalized 0.1x~0.25x)
    TIER_1 = 1  # 7B ~ 8B (1.0x Baseline)
    TIER_2 = 2  # 14B ~ 35B MoE (5.0x Advanced)
    TIER_3 = 3  # 70B ~ 72B (18.0x Flagship)
    TIER_4 = 4  # 405B ~ 671B MoE (50.0x Supercluster)


class ModelVariant(str, Enum):
    STANDARD = "standard"                  # 1.0x
    UNCENSORED = "uncensored_abliterated"  # 3.0x~3.5x Unrestricted Geek Premium


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
    task_kcu: float = 0.0
    usd_cost: float = 0.0
    miner_satk: int = 0
    standby_satk: int = 0
    combo_satk: int = 0
    burn_satk: int = 0
    eco_satk: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_satk": self.total_satk,
            "prompt_satk": self.prompt_satk,
            "completion_satk": self.completion_satk,
            "base_price_per_1k": self.base_price_per_1k,
            "tier_multiplier": self.tier_multiplier,
            "variant_multiplier": self.variant_multiplier,
            "decay_factor": round(self.decay_factor, 4),
            "effective_rate_per_1k": round(self.effective_rate_per_1k, 2),
            "task_kcu": round(self.task_kcu, 4),
            "usd_cost": round(self.usd_cost, 6),
            "miner_satk": self.miner_satk,
            "standby_satk": self.standby_satk,
            "combo_satk": self.combo_satk,
            "burn_satk": self.burn_satk,
            "eco_satk": self.eco_satk,
        }


# Canonical Immutable Relative KCU Multiplier Table (8B = 1.00 KCU Baseline)
CANONICAL_RELATIVE_KCU_TABLE = {
    "toy_1b_3b": {
        "name": "1.5B ~ 3B 玩具模型",
        "relative_kcu": 0.10,
        "is_uncensored": False,
        "benchmark_tier": "entry",
        "description": "Anti-spam penalty floor (0.1x baseline)",
    },
    "qwen_8b_standard": {
        "name": "Qwen 8B (标准4bit)",
        "relative_kcu": 1.00,
        "is_uncensored": False,
        "benchmark_tier": "entry",
        "description": "Canonical network baseline (1.0x anchor)",
    },
    "qwen_8b_uncensored": {
        "name": "Qwen 8B (越狱去对齐)",
        "relative_kcu": 3.00,
        "is_uncensored": True,
        "benchmark_tier": "entry",
        "description": "Uncensored free instruction stream (3.0x premium)",
    },
    "qwen_14b_standard": {
        "name": "Qwen 14B (单卡16G甜品)",
        "relative_kcu": 1.85,
        "is_uncensored": False,
        "benchmark_tier": "mid",
        "description": "Sweetspot 16G single-card worker (1.85x)",
    },
    "qwen_32b_standard": {
        "name": "Qwen 32B (主流密集大模型)",
        "relative_kcu": 4.59,
        "is_uncensored": False,
        "benchmark_tier": "mid",
        "description": "High-intelligence dense commercial core (4.59x)",
    },
    "qwen_35b_moe_standard": {
        "name": "Qwen 35B-A3B (4卡4070TiS)",
        "relative_kcu": 2.16,
        "is_uncensored": False,
        "benchmark_tier": "mid",
        "description": "High-throughput symbiotic MoE flagship (2.16x)",
    },
    "qwen_35b_moe_uncensored": {
        "name": "Qwen 35B-A3B (越狱去对齐)",
        "relative_kcu": 6.47,
        "is_uncensored": True,
        "benchmark_tier": "mid",
        "description": "Geek uncensored dark stream (6.47x, 3.0x premium)",
    },
    "qwen_72b_standard": {
        "name": "Qwen 72B / Llama 70B",
        "relative_kcu": 11.21,
        "is_uncensored": False,
        "benchmark_tier": "flagship",
        "description": "Flagship 4x 4090 full-stack reasoning (11.21x)",
    },
    "qwen_72b_uncensored": {
        "name": "Qwen 72B (越狱去对齐)",
        "relative_kcu": 33.63,
        "is_uncensored": True,
        "benchmark_tier": "flagship",
        "description": "Top-tier offensive security & audit (33.63x, 3.0x premium)",
    },
    "deepseek_v3_standard": {
        "name": "DeepSeek-V3 (671B 满血)",
        "relative_kcu": 52.03,
        "is_uncensored": False,
        "benchmark_tier": "flagship",
        "description": "Datacenter supercluster full intelligence (52.03x)",
    },
    "deepseek_r1_cot": {
        "name": "DeepSeek-R1 (深度推理 CoT)",
        "relative_kcu": 104.05,
        "is_uncensored": True,
        "benchmark_tier": "flagship",
        "description": "Full chain-of-thought crown jewel (104.05x, 2.0x CoT premium)",
    },
}


class BigTechDynamicOracle:
    """
    Decentralized Oracle tracking BigTech commercial API pricing and calculating dynamic KCU peg.
    Provides elastic floating linkage: when BigTech drops prices, BTC-K drops proportionally.
    """

    # Commercial benchmark base prices for single task (800 in, 300 out)
    BASE_BENCHMARKS = {
        "entry": {"name": "GPT-4o-mini", "call_usd": 0.000300},
        "mid": {"name": "GPT-4o", "call_usd": 0.005000},
        "flagship": {"name": "Claude 3.5 Sonnet", "call_usd": 0.006900},
    }

    # Minimum physical electricity cost floor per task to protect miners from suicidal price dumping
    MINING_COST_FLOOR_USD = 0.000020

    @classmethod
    def get_dynamic_vkcu(cls, tier: str = "mid", commercial_factor: float = 1.0) -> float:
        """
        Calculate dynamic 1 KCU fiat value for a specific tier given BigTech price factor:
        - BigTech factor 0.70 = 30% price war cut -> V_KCU decreases by 30%
        - BigTech factor 1.20 = 20% price hike -> V_KCU increases by 20%
        """
        factor = max(0.20, float(commercial_factor))
        bench = cls.BASE_BENCHMARKS.get(tier, cls.BASE_BENCHMARKS["mid"])
        b_price = bench["call_usd"] * factor

        if tier == "entry":
            # 8B Standard (1.00 KCU) = 26.7% of GPT-4o-mini
            v_kcu = (b_price * 0.267) / 1.00
        elif tier == "mid":
            # 35B MoE Standard (2.16 KCU) = 25.0% of GPT-4o
            v_kcu = (b_price * 0.250) / 2.16
        else:
            # 72B Standard (11.21 KCU) = 26.1% of Claude 3.5 Sonnet
            v_kcu = (b_price * 0.261) / 11.21

        return max(cls.MINING_COST_FLOOR_USD, round(v_kcu, 8))

    @classmethod
    def get_model_quote(
        cls,
        model_key: str,
        commercial_factor: float = 1.0,
        btck_price_usd: float = 0.05,
    ) -> Dict[str, Any]:
        """
        Get full dynamic quote for a model under current BigTech pricing climate.
        """
        model_meta = CANONICAL_RELATIVE_KCU_TABLE.get(model_key)
        if not model_meta:
            raise ValueError(f"Unknown model key: {model_key}")

        rel_kcu = model_meta["relative_kcu"]
        tier = model_meta["benchmark_tier"]
        v_kcu = cls.get_dynamic_vkcu(tier, commercial_factor)
        usd_price = rel_kcu * v_kcu

        price_btck = max(0.000001, float(btck_price_usd))
        satk_price = max(1, int(round((usd_price * 1e8) / price_btck)))

        bench = cls.BASE_BENCHMARKS[tier]
        bench_cur = bench["call_usd"] * commercial_factor
        ratio_pct = (usd_price / bench_cur) * 100.0

        return {
            "model_key": model_key,
            "name": model_meta["name"],
            "relative_kcu": rel_kcu,
            "dynamic_v_kcu": v_kcu,
            "usd_price": round(usd_price, 6),
            "satk_price": satk_price,
            "commercial_benchmark": bench["name"],
            "commercial_price": round(bench_cur, 6),
            "ratio_to_commercial_pct": round(ratio_pct, 1),
        }


class TieredPricingEngine:
    """
    Economic engine for pricing decentralized AI compute with KCU anchoring and dynamic oracle support.
    """

    # KCU is the stable compute-value bridge. SatK is derived at quote time
    # from the reviewed BTC-K reference price; it is never the service anchor.
    KCU_USD_PEG = Decimal("0.00010")
    BOOTSTRAP_BTCK_PRICE_USD = Decimal("0.05")
    MICRO_KCU_PER_KCU = 1_000_000
    MSATK_PER_SATK = 1_000
    MSATK_PER_BTCK = 100_000_000_000
    BASE_KCU_PER_1K = Decimal("0.8")

    # 4-Year Progressive Revenue Schedule (Aligning with L1 BlockDAG Halving & Combo Stages):
    PROGRESSIVE_REVENUE_SCHEDULE = {
        1: {"miner": 0.60, "standby": 0.10, "combo": 0.15, "burn": 0.05, "eco": 0.10, "draw_hours": 12},
        2: {"miner": 0.50, "standby": 0.10, "combo": 0.25, "burn": 0.10, "eco": 0.05, "draw_hours": 36},
        3: {"miner": 0.40, "standby": 0.10, "combo": 0.30, "burn": 0.15, "eco": 0.05, "draw_hours": 108},
        4: {"miner": 0.35, "standby": 0.05, "combo": 0.40, "burn": 0.20, "eco": 0.00, "draw_hours": 324},
    }

    # Default Year 1 Allocation (for backward compatibility)
    REVENUE_SPLIT = PROGRESSIVE_REVENUE_SCHEDULE[1]

    @classmethod
    def network_year_for_height(cls, virtual_blue_height: int) -> int:
        """Derive the frozen L2 allocation year from canonical L1 height.

        Genesis is height 0. Heights 1..BLOCKS_PER_YEAR are year one and
        subsequent complete 365-day height windows select years two to four.
        The year-four L2 schedule remains in force after L1 issuance ends.
        """
        if not isinstance(virtual_blue_height, int) or isinstance(virtual_blue_height, bool):
            raise ValueError("virtual_blue_height must be a non-negative integer")
        if virtual_blue_height < 0:
            raise ValueError("virtual_blue_height must be a non-negative integer")
        if virtual_blue_height == 0:
            return 1
        return min(4, ((virtual_blue_height - 1) // BLOCKS_PER_YEAR) + 1)

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
    def kcu_to_satk(cls, task_kcu: float, btck_price_usd: float = 0.05) -> int:
        """
        Convert KCU to whole on-chain SatK, rounding upward so a quote is
        never underfunded. Sub-SatK L2 accounting uses kcu_to_msatk().
        """
        msatk = cls.kcu_to_msatk(task_kcu, btck_price_usd)
        return max(1, (msatk + cls.MSATK_PER_SATK - 1) // cls.MSATK_PER_SATK)

    @classmethod
    def satk_to_kcu(cls, satk: int, btck_price_usd: float = 0.05) -> float:
        """Convert SatK back to KCU"""
        return cls.msatk_to_kcu(int(satk) * cls.MSATK_PER_SATK, btck_price_usd)

    @classmethod
    def kcu_to_msatk(cls, task_kcu: float, btck_price_usd: float = 0.05) -> int:
        """
        Convert KCU to mSatK (millisatoshis, 1 SatK = 1,000 mSatK, 1 BTC-K = 10^11 mSatK).
        Supports ultra-high coin prices ($10,000 ~ $1,000,000+ USDT) with sub-cent precision.
        mSatK = round(task_kcu * 10,000,000 / btck_price_usd)
        """
        try:
            kcu = Decimal(str(task_kcu))
            price = Decimal(str(btck_price_usd))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("KCU and BTC-K price must be decimal numbers") from exc
        if not kcu.is_finite() or kcu <= 0:
            raise ValueError("task_kcu must be positive and finite")
        if not price.is_finite() or price <= 0:
            raise ValueError("btck_price_usd must be positive and finite")
        value = (kcu * cls.KCU_USD_PEG * cls.MSATK_PER_BTCK / price)
        return max(1, int(value.to_integral_value(rounding=ROUND_HALF_UP)))

    @classmethod
    def msatk_to_kcu(cls, msatk: int, btck_price_usd: float = 0.05) -> float:
        """Convert integer internal mSatK back to KCU for receipts."""
        if not isinstance(msatk, int) or isinstance(msatk, bool) or msatk < 0:
            raise ValueError("msatk must be a non-negative integer")
        price = Decimal(str(btck_price_usd))
        if not price.is_finite() or price <= 0:
            raise ValueError("btck_price_usd must be positive and finite")
        kcu = Decimal(msatk) * price / (cls.KCU_USD_PEG * cls.MSATK_PER_BTCK)
        return float(kcu)

    @classmethod
    def quote_kcu_usage(
        cls,
        estimated_units: int,
        rate_kcu_per_1k: float,
        btck_price_usd: float = 0.05,
    ) -> Dict[str, Any]:
        """Return an integer-safe KCU -> mSatK -> SatK quote.

        micro-KCU and mSatK are retained in receipts for deterministic quotes.
        The current settlement ledger and L1 invoices use whole SatK, rounded
        upward; sub-SatK accumulation is a separate, audit-gated upgrade.
        """
        if not isinstance(estimated_units, int) or isinstance(estimated_units, bool) or estimated_units <= 0:
            raise ValueError("estimated_units must be a positive integer")
        rate = Decimal(str(rate_kcu_per_1k))
        if not rate.is_finite() or rate <= 0:
            raise ValueError("rate_kcu_per_1k must be positive and finite")
        task_kcu = rate * Decimal(estimated_units) / Decimal(1000)
        micro_kcu = int(
            (task_kcu * cls.MICRO_KCU_PER_KCU).to_integral_value(rounding=ROUND_CEILING)
        )
        normalized_kcu = Decimal(micro_kcu) / cls.MICRO_KCU_PER_KCU
        amount_msatk = cls.kcu_to_msatk(normalized_kcu, btck_price_usd)
        amount_satk = max(1, (amount_msatk + cls.MSATK_PER_SATK - 1) // cls.MSATK_PER_SATK)
        return {
            "rate_kcu_per_1k": float(rate),
            "task_kcu_micros": micro_kcu,
            "task_kcu": float(normalized_kcu),
            "kcu_usd_peg": float(cls.KCU_USD_PEG),
            "btck_reference_price_usd": float(Decimal(str(btck_price_usd))),
            "amount_msatk": amount_msatk,
            "amount_satk": amount_satk,
            "onchain_rounding_msatk": amount_satk * cls.MSATK_PER_SATK - amount_msatk,
        }

    @classmethod
    def msatk_to_satk(cls, msatk: int) -> Tuple[int, int]:
        """Convert mSatK into (satk_integer, msatk_remainder) for on-chain settlement."""
        return divmod(msatk, 1000)

    @classmethod
    def calculate_revenue_split(cls, total_satk: int, year: int = 1) -> Dict[str, Any]:
        """
        Calculate progressive revenue distribution for a given network year (1-4).
        Aligns combo draw frequency with L1 mining (12h -> 36h -> 108h -> 324h).
        """
        if not isinstance(total_satk, int) or isinstance(total_satk, bool) or total_satk < 0:
            raise ValueError("total_satk must be a non-negative integer")
        if year not in cls.PROGRESSIVE_REVENUE_SCHEDULE:
            raise ValueError("year must be in the canonical range 1..4")
        sched = cls.PROGRESSIVE_REVENUE_SCHEDULE[year]
        # Convert the frozen decimal ratios once, then keep all value movement
        # in integer SatK. This avoids floating-point accounting drift.
        ratio_bps = {
            key: int(round(float(sched[key]) * 10_000))
            for key in ("miner", "standby", "combo", "burn", "eco")
        }
        if sum(ratio_bps.values()) != 10_000:
            raise RuntimeError("invalid v2.4 revenue schedule: ratios do not total 100%")
        fields = ("miner", "standby", "combo", "burn", "eco")
        divisor = 0
        for field in fields:
            divisor = gcd(divisor, ratio_bps[field])
        weights = {field: ratio_bps[field] // divisor for field in fields}
        cycle_size = sum(weights.values())

        # Smooth weighted round-robin creates a deterministic integer-SatK
        # allocation cycle. Every prefix is conserved, no recipient ever needs
        # a later negative correction, and every complete cycle exactly matches
        # the frozen v2.4 percentages.
        current = {field: 0 for field in fields}
        cycle = []
        for _ in range(cycle_size):
            for field in fields:
                current[field] += weights[field]
            selected = max(fields, key=lambda field: current[field])
            cycle.append(selected)
            current[selected] -= cycle_size

        full_cycles, remainder = divmod(total_satk, cycle_size)
        amounts = {field: full_cycles * weights[field] for field in fields}
        for selected in cycle[:remainder]:
            amounts[selected] += 1
        miner = amounts["miner"]
        standby = amounts["standby"]
        combo = amounts["combo"]
        burn = amounts["burn"]
        eco = amounts["eco"]
        return {
            "miner_satk": miner,
            "standby_satk": standby,
            "combo_satk": combo,
            "burn_satk": burn,
            "eco_satk": eco,
            "draw_hours": sched["draw_hours"],
            "total_miner_ecosystem_satk": miner + standby + combo,
        }

    @classmethod
    def calculate_4d_kcu(
        cls,
        param_b: float,
        generation_factor: float = 1.0,
        quant_factor: float = 1.0,
        uncensored: bool = False,
        prompt_tokens: int = 800,
        completion_tokens: int = 300,
    ) -> float:
        """
        4-Dimensional KCU Formulation (Doc 26 & ADR-007 compliant):
        KCU = K_param * K_gen * K_quant * K_type * ((Prompt + 1.5 * Completion) / 1000)
        """
        if param_b < 7.0:
            k_param = 0.10  # Anti-spam floor
        elif param_b < 14.0:
            k_param = 0.50
        elif param_b < 40.0:
            k_param = 7.50
        elif param_b < 100.0:
            k_param = 11.00
        else:
            k_param = 30.00

        k_type = 2.10 if uncensored else 1.00
        workload = (prompt_tokens + cls.COMPLETION_PREMIUM * completion_tokens) / 1000.0
        return round(k_param * generation_factor * quant_factor * k_type * workload, 4)

    @classmethod
    def calculate_cost(
        cls,
        prompt_tokens: int,
        completion_tokens: int,
        tier: ModelTier = ModelTier.TIER_1,
        variant: ModelVariant = ModelVariant.STANDARD,
        is_flagship: bool = True,
        deprecation_days: int = 0,
        btck_price_usd: float = 0.05,
        network_year: int = 1,
    ) -> CostBreakdown:
        """
        Calculate the exact SatK requirement for an inference job.
        Preserves legacy compatibility while incorporating KCU and progressive 4-year economics.
        """
        tier_mult = cls.TIER_MULTIPLIERS.get(tier, 1.0)
        variant_mult = cls.VARIANT_MULTIPLIERS.get(variant, 1.0)
        decay = cls.calculate_decay_factor(is_flagship, deprecation_days)

        effective_multiplier = tier_mult * variant_mult * decay
        effective_rate_per_1k = float(cls.BASE_KCU_PER_1K) * effective_multiplier
        prompt_kcu = (prompt_tokens / 1000.0) * effective_rate_per_1k
        completion_kcu = (
            (completion_tokens / 1000.0)
            * effective_rate_per_1k
            * cls.COMPLETION_PREMIUM
        )
        task_kcu = prompt_kcu + completion_kcu
        quote = cls.quote_kcu_usage(
            max(1, int(prompt_tokens + cls.COMPLETION_PREMIUM * completion_tokens)),
            effective_rate_per_1k,
            btck_price_usd,
        )
        total_satk = int(quote["amount_satk"])
        prompt_satk = cls.kcu_to_satk(prompt_kcu, btck_price_usd) if prompt_tokens > 0 else 0
        completion_satk = cls.kcu_to_satk(completion_kcu, btck_price_usd) if completion_tokens > 0 else 0
        usd_cost = float(Decimal(str(task_kcu)) * cls.KCU_USD_PEG)
        split = cls.calculate_revenue_split(total_satk, year=network_year)

        return CostBreakdown(
            total_satk=total_satk,
            prompt_satk=prompt_satk,
            completion_satk=completion_satk,
            base_price_per_1k=float(cls.BASE_KCU_PER_1K),
            tier_multiplier=tier_mult,
            variant_multiplier=variant_mult,
            decay_factor=decay,
            effective_rate_per_1k=effective_rate_per_1k,
            task_kcu=task_kcu,
            usd_cost=usd_cost,
            miner_satk=split["miner_satk"],
            standby_satk=split["standby_satk"],
            combo_satk=split["combo_satk"],
            burn_satk=split["burn_satk"],
            eco_satk=split["eco_satk"],
        )

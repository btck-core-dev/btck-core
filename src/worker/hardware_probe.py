"""
BTC-K Hardware Probe Module
Auto-detects GPU hardware, VRAM, architecture, and maps to the canonical Tier Matrix.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class GPUTier(str, Enum):
    TIER_0_MICRO = "Tier 0 (Micro Edge: 1.5B~3B / Embedding)"
    TIER_1_STANDARD = "Tier 1 (Standard: 7B~8B / Pearl Coexistence)"
    TIER_2_ADVANCED = "Tier 2 (Advanced: 14B~32B Reasoning)"
    TIER_3_FLAGSHIP = "Tier 3 (Flagship: 70B~72B Architecture)"
    TIER_4_DATACENTER = "Tier 4 (Supercluster: 405B~671B MoE)"


@dataclass
class GPUProfile:
    gpu_count: int
    gpu_models: List[str]
    total_vram_gb: float
    per_card_vram_gb: float
    assigned_tier: GPUTier
    tier_level: int
    recommended_model: str
    supported_models: List[str]
    pearl_coexistence_possible: bool
    pearl_dag_requirement_gb: float = 4.8
    notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "gpu_count": self.gpu_count,
            "gpu_models": self.gpu_models,
            "total_vram_gb": round(self.total_vram_gb, 2),
            "per_card_vram_gb": round(self.per_card_vram_gb, 2),
            "assigned_tier": self.assigned_tier.value,
            "tier_level": self.tier_level,
            "recommended_model": self.recommended_model,
            "supported_models": self.supported_models,
            "pearl_coexistence_possible": self.pearl_coexistence_possible,
            "notes": self.notes,
        }


class HardwareProbe:
    """
    Automated hardware probe for NVIDIA GPUs, Apple Silicon, and fallback environments.
    """

    PEARL_DAG_GB = 4.8

    TIER_SPECS = {
        0: {
            "name": GPUTier.TIER_0_MICRO,
            "min_card_vram_gb": 6.0,
            "models": ["qwen-2.5-1.5b", "qwen-2.5-3b", "llama-3.2-3b", "bge-m3-embedding"],
            "default_model": "qwen-2.5-3b-q4",
            "desc": "微型边缘节点：处理语义嵌入与路由打杂",
        },
        1: {
            "name": GPUTier.TIER_1_STANDARD,
            "min_card_vram_gb": 12.0,
            "models": ["deepseek-r1-distill-qwen-8b", "llama-3.1-8b", "qwen-2.5-7b"],
            "default_model": "deepseek-r1-distill-qwen-8b-q4",
            "desc": "标准交互节点：Pearl挖矿黄金共生段位",
        },
        2: {
            "name": GPUTier.TIER_2_ADVANCED,
            "min_card_vram_gb": 24.0,
            "models": ["qwen-2.5-32b", "deepseek-r1-distill-qwen-32b", "qwen-2.5-14b"],
            "default_model": "qwen-2.5-32b-q4",
            "desc": "深度推理节点：复杂工作流与长文本推理",
        },
        3: {
            "name": GPUTier.TIER_3_FLAGSHIP,
            "min_total_vram_gb": 48.0,
            "models": ["qwen-2.5-72b", "llama-3.3-70b", "deepseek-r1-distill-llama-70b"],
            "default_model": "qwen-2.5-72b-q4",
            "desc": "旗舰专家节点：全栈编码与智能合约审计",
        },
        4: {
            "name": GPUTier.TIER_4_DATACENTER,
            "min_total_vram_gb": 320.0,
            "models": ["deepseek-v3-671b-moe", "deepseek-r1-671b-moe", "llama-3.1-405b"],
            "default_model": "deepseek-r1-671b-moe-fp8",
            "desc": "超级算力集群：满血大模型暗流托管",
        },
    }

    @classmethod
    def probe_nvidia_smi(cls) -> Optional[Tuple[int, List[str], float, float]]:
        """
        Query nvidia-smi for GPU names and VRAM.
        Returns (gpu_count, gpu_models, total_vram_gb, per_card_vram_gb).
        """
        nvidia_smi_path = shutil.which("nvidia-smi")
        if not nvidia_smi_path:
            return None

        try:
            cmd = [
                nvidia_smi_path,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=3).decode("utf-8")
            lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
            if not lines:
                return None

            models = []
            vrams = []
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    models.append(parts[0])
                    vrams.append(float(parts[1]) / 1024.0)  # Convert MiB to GiB

            if not vrams:
                return None

            gpu_count = len(vrams)
            total_vram = sum(vrams)
            per_card_vram = vrams[0]
            return gpu_count, models, total_vram, per_card_vram
        except Exception:
            return None

    @classmethod
    def probe_macos(cls) -> Optional[Tuple[int, List[str], float, float]]:
        """
        Detect Apple Silicon unified memory on macOS.
        """
        try:
            output = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode("utf-8").strip()
            total_bytes = int(output)
            total_gb = total_bytes / (1024**3)

            cpu_brand = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=2).decode("utf-8").strip()
            return 1, [f"Apple Silicon ({cpu_brand})"], total_gb, total_gb
        except Exception:
            return None

    @classmethod
    def detect_profile(cls, mock_override: Optional[Dict] = None) -> GPUProfile:
        """
        Auto-detect the hardware profile and classify tier.
        Allows mock_override for testing and local simulation.
        """
        if mock_override:
            gpu_count = mock_override.get("gpu_count", 1)
            gpu_models = mock_override.get("gpu_models", ["Mock GPU"])
            total_vram_gb = mock_override.get("total_vram_gb", 16.0)
            per_card_vram_gb = mock_override.get("per_card_vram_gb", total_vram_gb / gpu_count)
        else:
            # 1. Try NVIDIA SMI
            nv = cls.probe_nvidia_smi()
            if nv:
                gpu_count, gpu_models, total_vram_gb, per_card_vram_gb = nv
            else:
                # 2. Try macOS Apple Silicon
                mac = cls.probe_macos()
                if mac:
                    gpu_count, gpu_models, total_vram_gb, per_card_vram_gb = mac
                else:
                    # 3. Generic fallback
                    gpu_count = 1
                    gpu_models = ["Generic Compute Host (Fallback)"]
                    total_vram_gb = 16.0
                    per_card_vram_gb = 16.0

        # Classify Tier:
        # Check from highest to lowest
        assigned_tier = GPUTier.TIER_0_MICRO
        tier_level = 0

        if total_vram_gb >= cls.TIER_SPECS[4]["min_total_vram_gb"]:
            tier_level = 4
            assigned_tier = GPUTier.TIER_4_DATACENTER
        elif total_vram_gb >= cls.TIER_SPECS[3]["min_total_vram_gb"]:
            tier_level = 3
            assigned_tier = GPUTier.TIER_3_FLAGSHIP
        elif per_card_vram_gb >= cls.TIER_SPECS[2]["min_card_vram_gb"]:
            tier_level = 2
            assigned_tier = GPUTier.TIER_2_ADVANCED
        elif per_card_vram_gb >= cls.TIER_SPECS[1]["min_card_vram_gb"]:
            tier_level = 1
            assigned_tier = GPUTier.TIER_1_STANDARD
        else:
            tier_level = 0
            assigned_tier = GPUTier.TIER_0_MICRO

        spec = cls.TIER_SPECS[tier_level]
        recommended_model = spec["default_model"]
        supported_models = spec["models"]

        # Pearl coexistence check:
        # Pearl requires ~4.8 GB DAG. If single card has >= 8.0 GB, it can co-exist.
        pearl_coexist = per_card_vram_gb >= (cls.PEARL_DAG_GB + 2.0)

        notes = f"{spec['desc']} | 显存: {per_card_vram_gb:.1f}G/卡 (共 {total_vram_gb:.1f}G)"

        return GPUProfile(
            gpu_count=gpu_count,
            gpu_models=gpu_models,
            total_vram_gb=total_vram_gb,
            per_card_vram_gb=per_card_vram_gb,
            assigned_tier=assigned_tier,
            tier_level=tier_level,
            recommended_model=recommended_model,
            supported_models=supported_models,
            pearl_coexistence_possible=pearl_coexist,
            pearl_dag_requirement_gb=cls.PEARL_DAG_GB,
            notes=notes,
        )

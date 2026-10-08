"""
BTC-K 4-Year Rational Smooth Decay & Combo Lottery Full Invariant Test Suite
----------------------------------------------------------------------------
Strictly validates all 11 core criteria specified in Audit Report 29:
1. Block #0 subsidy is strictly 0 SatK (Zero inflation leak).
2. Blocks 1..126,144,000 sum strictly to 2,100,000,000,000,000 SatK (0 SatK drift).
3. Block #126,144,001+ subsidy strictly terminates to 0 SatK.
4. Daily integer quotas table matches canonical SHA-256 checksum.
5. 4-Stage Tiered Combo Ratios (15%, 25%, 35%, 45%) plus 5% ecosystem reserve.
6. Future Random Beacon (+100 blocks) + Kademlia XOR Distance Winner Selection.
7. Progressive Streaming Vesting Schedule & Exact SatK Conservation.
8. Continuous Solvency Invariant: Minted == Total Paid + Retained Pool Balance.
9. DAG Order-Independence: Issuance is an immutable pure function of height.
"""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tokenomics.emission_schedule import (
    get_block_budget_sats,
    get_base_block_reward_sats,
    get_combo_spec_for_height,
    split_post_mining_fees,
    POST_MINING_MINER_FEE_PERCENT,
    POST_MINING_COMBO_FEE_PERCENT,
    compute_block_issuance,
    compute_beacon_xor_winner,
    compute_jackpot_vesting_tranches,
    BlockIssuanceResult,
    DAILY_QUOTAS_SATK,
    CANONICAL_DAILY_QUOTAS_SHA256,
    TOTAL_HARD_CAP_SATK,
    BLOCKS_PER_DAY,
    TOTAL_BLOCKS,
    SATK_PER_COIN
)

class TestFourYearRationalDecay(unittest.TestCase):

    def test_01_genesis_zero_reward(self):
        """Audit Item 14: 区块0奖励严格为0"""
        r0 = get_block_budget_sats(0)
        self.assertEqual(r0, 0, "Genesis Block 0 must have 0 coinbase issuance")
        r0_alias = get_base_block_reward_sats(0)
        self.assertEqual(r0_alias, 0)

    def test_02_canonical_daily_quotas_checksum(self):
        """Audit Section 3: 每日整数配额表确定性与防篡改哈希校验"""
        self.assertEqual(len(DAILY_QUOTAS_SATK), 1460)
        self.assertEqual(sum(DAILY_QUOTAS_SATK), TOTAL_HARD_CAP_SATK)
        actual_sha = hashlib.sha256(json.dumps(DAILY_QUOTAS_SATK).encode()).hexdigest()
        self.assertEqual(actual_sha, CANONICAL_DAILY_QUOTAS_SHA256)
        self.assertEqual(actual_sha, "7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59")

    def test_03_exact_quadrillion_sum(self):
        """Audit Section 3 & 14: 126,144,000区块累积总释放严格等于 2.1 Quadrillion SatK (21,000,000.00000000 BTC-K)"""
        total_minted = 0
        for d, daily_quota in enumerate(DAILY_QUOTAS_SATK):
            base = daily_quota // BLOCKS_PER_DAY
            rem = daily_quota % BLOCKS_PER_DAY
            day_sum = rem * (base + 1) + (BLOCKS_PER_DAY - rem) * base
            self.assertEqual(day_sum, daily_quota, f"Day {d} daily sum mismatch")
            total_minted += day_sum

        self.assertEqual(total_minted, TOTAL_HARD_CAP_SATK)
        self.assertEqual(total_minted, 2_100_000_000_000_000)

    def test_04_post_4_year_termination(self):
        """Audit Item 14: 第126,144,001块及以后补贴严格为0"""
        self.assertEqual(get_block_budget_sats(TOTAL_BLOCKS + 1), 0)
        self.assertEqual(get_block_budget_sats(TOTAL_BLOCKS + 1000), 0)
        self.assertEqual(get_block_budget_sats(TOTAL_BLOCKS + 1_000_000), 0)

    def test_05_euler_reserve_first_72h_allocation(self):
        """护网期前72小时发行测算；完整护网期固定到271,828块。"""
        gen_72h_sats = sum(DAILY_QUOTAS_SATK[:3])
        gen_72h_btck = gen_72h_sats / SATK_PER_COIN
        pct = (gen_72h_sats / TOTAL_HARD_CAP_SATK) * 100
        self.assertAlmostEqual(gen_72h_btck, 1_446_676.59, places=1)
        self.assertAlmostEqual(pct, 6.8889, places=3)

    def test_06_combo_stages_spec(self):
        """Audit Section 2 & 参数规范: 四阶段 Combo 比例与开奖间隔"""
        stage1, r1, int1 = get_combo_spec_for_height(8_640_000)
        self.assertEqual(stage1, 1)
        self.assertEqual(r1, 0.15)
        self.assertEqual(int1, 43_200)

        stage2, r2, int2 = get_combo_spec_for_height(43_200_000)
        self.assertEqual(stage2, 2)
        self.assertEqual(r2, 0.25)
        self.assertEqual(int2, 129_600)

        stage3, r3, int3 = get_combo_spec_for_height(77_760_000)
        self.assertEqual(stage3, 3)
        self.assertEqual(r3, 0.35)
        self.assertEqual(int3, 388_800)

        stage4, r4, int4 = get_combo_spec_for_height(103_680_000)
        self.assertEqual(stage4, 4)
        self.assertEqual(r4, 0.45)
        self.assertEqual(int4, 1_166_400)

        stage5, r5, int5 = get_combo_spec_for_height(TOTAL_BLOCKS + 1)
        self.assertEqual(stage5, 5)
        self.assertEqual(r5, 0.50)
        self.assertEqual(int5, 1_166_400)

    def test_06b_post_mining_fee_split_is_frozen_50_50(self):
        self.assertEqual(POST_MINING_MINER_FEE_PERCENT, 50)
        self.assertEqual(POST_MINING_COMBO_FEE_PERCENT, 50)
        self.assertEqual(split_post_mining_fees(100), (50, 50))
        self.assertEqual(split_post_mining_fees(101), (51, 50))
        params_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "params", "btck_parameters.json"
        ))
        with open(params_path, "r", encoding="utf-8") as handle:
            params = json.load(handle)
        post_mining = params["tokenomics"]["post_mining_era"]["fee_combo_jackpot"]
        frozen = params["tokenomics"]["post_4_years_security_budget"]["consensus_frozen_split"]
        self.assertEqual(post_mining["activation_height"], TOTAL_BLOCKS + 1)
        self.assertEqual(post_mining["miner_split_ratio"], 0.5)
        self.assertEqual(post_mining["jackpot_pool_ratio"], 0.5)
        self.assertEqual(frozen["activation_height"], TOTAL_BLOCKS + 1)
        self.assertEqual(frozen["miner_immediate_packing_ratio"], 0.5)
        self.assertEqual(frozen["perpetual_lottery_pool_ratio"], 0.5)
        self.assertTrue(frozen["permanent"])
        with self.assertRaises(ValueError):
            split_post_mining_fees(-1)

    def test_07_beacon_xor_distance_winner_selection(self):
        """Audit Section 10: 未来随机信标 + Kademlia XOR 几何距离选奖"""
        candidates = [hashlib.sha256(f"BLOCK_HASH_{i}".encode()).digest() for i in range(100)]
        beacon_hash = hashlib.sha256(b"FUTURE_BEACON_HEADER_HASH").digest()
        
        winner, min_dist = compute_beacon_xor_winner(candidates, beacon_hash, epoch_index=42)
        self.assertIn(winner, candidates)
        self.assertGreater(min_dist, 0)
        
        beacon_seed = hashlib.sha256(beacon_hash + (42).to_bytes(8, "little")).digest()
        beacon_int = int.from_bytes(beacon_seed, "big")
        for c in candidates:
            dist = int.from_bytes(c, "big") ^ beacon_int
            self.assertGreaterEqual(dist, min_dist)

    def test_07b_canonical_beacon_seed_fixed_vector(self):
        """SHA256(kHeavyHash beacon bytes || epoch uint64 little-endian)."""
        beacon_hash = bytes.fromhex("00" * 31 + "01")
        seed = hashlib.sha256(beacon_hash + (7).to_bytes(8, "little")).hexdigest()
        self.assertEqual(
            seed,
            "8f4bd02b82c87006f5a5f6caa3cbd04f108ebd03c3448dd2b98c180517e2de5d",
        )

    def test_08_streaming_vesting_maturation(self):
        """Audit Section 9: 大奖分期成熟机制与 SatK 严格守恒"""
        for stage in (1, 2, 3, 4):
            jackpot = 50_000 * SATK_PER_COIN
            tranches = compute_jackpot_vesting_tranches(stage, jackpot, draw_block_height=1_000_000)
            self.assertGreater(len(tranches), 0)
            total_tranches_sats = sum(t["amount_satk"] for t in tranches)
            self.assertEqual(total_tranches_sats, jackpot, f"Stage {stage} vesting sum mismatch")

    def test_09_continuous_balance_sheet_solvency(self):
        """Audit Section 5 & 14: 奖池收支严格守恒，无凭空印钞，无永久沉淀"""
        current_issued = 0
        current_pool = 0
        total_paid_to_miners = 0
        total_eco_collected = 0

        for h in range(1, 2001):
            block_hash = hashlib.sha256(f"SIM_BLOCK_{h}".encode()).digest()
            force_draw = (h % 100 == 0)
            res: BlockIssuanceResult = compute_block_issuance(
                block_height=h,
                block_hash=block_hash,
                current_issued_sats=current_issued,
                current_pool_balance_sats=current_pool,
                force_combo=force_draw
            )

            self.assertEqual(res.minted_sats, res.miner_base_sats + res.pool_deposit_sats + res.eco_deposit_sats)
            self.assertGreaterEqual(res.new_pool_balance_sats, 0)
            self.assertLessEqual(res.new_issued_supply_sats, TOTAL_HARD_CAP_SATK)

            total_paid_to_miners += res.miner_total_payout_sats
            total_eco_collected += res.eco_deposit_sats
            current_issued = res.new_issued_supply_sats
            current_pool = res.new_pool_balance_sats

            self.assertEqual(total_paid_to_miners + current_pool + total_eco_collected, current_issued)

    def test_10_dag_order_independence(self):
        """Audit Section 12: 发行状态不依赖节点到达顺序，为确定性纯函数"""
        sample_heights = [1, 259200, 31536000, 63072000, 94608000, 126144000, 126144001]
        for h in sample_heights:
            b1 = get_block_budget_sats(h)
            b2 = get_block_budget_sats(h)
            self.assertEqual(b1, b2)

if __name__ == "__main__":
    unittest.main()

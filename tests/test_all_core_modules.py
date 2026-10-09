"""
BTC-K Master Core Test Suite
----------------------------
Executes end-to-end verification across all core pillars:
1. Tokenomics & 583-Day Flash Halving Curve (Zero Height-0 Leakage, Exact 21M Cap)
2. Consensus Combo Jackpot Pool Solvency & Strict Conservation Invariant
3. kHeavyHash ASIC PoW Engine
4. Dual-Key Stealth Address Privacy & One-time Pubkey Recovery
5. AI-RPC Pay-per-Token HTTP 402 Gateway
"""

import sys
import os
import hashlib
import unittest
import tempfile
import shutil

# Add src to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tokenomics.emission_schedule import (
    get_base_block_reward_sats,
    get_block_reward_sats,
    get_block_reward_btck,
    is_combo_block,
    compute_block_issuance,
    BlockIssuanceResult,
    get_combo_spec_for_height,
    compute_beacon_xor_winner,
    compute_jackpot_vesting_tranches,
    DAILY_QUOTAS_SATK,
    CANONICAL_DAILY_QUOTAS_SHA256,
    TOTAL_HARD_CAP_SATK,
    TOTAL_HARD_CAP_SATS,
    SATK_PER_COIN,
    SATS_PER_COIN,
    BLOCKS_PER_DAY,
    LIFESPAN_DAYS,
    TOTAL_BLOCKS,
    COMBO_STAGES
)
from privacy.stealth_address import StealthKeyPair
from gateway.ai_rpc_gateway import AIRPCGateway
from consensus.kheavyhash import kheavyhash


class TestAllCoreModules(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tokenomics(self):
        # 1. Check Genesis Block 0 (Must be strictly 0 SatK to avoid inflation leak)
        r0 = get_base_block_reward_sats(0)
        self.assertEqual(r0, 0, f"Genesis Block 0 reward must be 0, got {r0 / SATS_PER_COIN}")

        # 2. Check Day 0 Initial Block Reward (~6.298 BTC-K / block)
        r_day0 = get_base_block_reward_sats(1)
        self.assertEqual(r_day0, 629_815_765, f"Day 0 initial reward mismatch: got {r_day0}")

        # 3. Check Genesis 72-Hour Security Reserve (Blocks 1..259,200)
        gen_72h_sats = sum(DAILY_QUOTAS_SATK[:3])
        gen_72h_btck = gen_72h_sats / SATS_PER_COIN
        self.assertTrue(1_446_670 <= gen_72h_btck <= 1_446_680, f"Genesis 72h mismatch: {gen_72h_btck}")

        # 4. Check Year 1, 2, 3, 4 Annual Emissions
        y1_sats = sum(DAILY_QUOTAS_SATK[0:365])
        y2_sats = sum(DAILY_QUOTAS_SATK[365:730])
        y3_sats = sum(DAILY_QUOTAS_SATK[730:1095])
        y4_sats = sum(DAILY_QUOTAS_SATK[1095:1460])
        self.assertGreater(y1_sats, y2_sats)
        self.assertGreater(y2_sats, y3_sats)
        self.assertGreater(y3_sats, y4_sats)

        # 5. Check Day 1459 Final Block Reward (~0.0307 BTC-K / block)
        r_final = get_base_block_reward_sats(TOTAL_BLOCKS)
        self.assertIn(r_final, (3_073_557, 3_073_558), f"Final block reward mismatch: got {r_final}")

        # 6. Check Post-4-Year Era (Block 126,144,001+) Strictly Terminated to 0
        r_post = get_base_block_reward_sats(TOTAL_BLOCKS + 1)
        self.assertEqual(r_post, 0, f"Post-4-year subsidy must be strictly 0, got {r_post}")

        # 7. Rigorous Mathematical Convergence Proof of EXACT 21,000,000.00000000 Hard Cap
        total_protocol_supply = sum(DAILY_QUOTAS_SATK)
        self.assertEqual(total_protocol_supply, TOTAL_HARD_CAP_SATK, f"Total cap error: {total_protocol_supply}")
        self.assertEqual(total_protocol_supply, 2_100_000_000_000_000, "Must be exactly 2.1 Quadrillion SatK")
        self.assertEqual(len(DAILY_QUOTAS_SATK), 1460, "Must be exactly 1460 days")

    def test_combo_jackpot_and_pool_solvency(self):
        # 2.1 Test Tiered Stages & Draw Schedules
        stage1, ratio1, interval1 = get_combo_spec_for_height(100_000)
        self.assertEqual(stage1, 1)
        self.assertEqual(ratio1, 0.15)
        self.assertEqual(interval1, 43_200)

        stage2, ratio2, interval2 = get_combo_spec_for_height(35_000_000)
        self.assertEqual(stage2, 2)
        self.assertEqual(ratio2, 0.25)
        self.assertEqual(interval2, 129_600)

        stage3, ratio3, interval3 = get_combo_spec_for_height(70_000_000)
        self.assertEqual(stage3, 3)
        self.assertEqual(ratio3, 0.35)
        self.assertEqual(interval3, 388_800)

        stage4, ratio4, interval4 = get_combo_spec_for_height(100_000_000)
        self.assertEqual(stage4, 4)
        self.assertEqual(ratio4, 0.45)
        self.assertEqual(interval4, 1_166_400)

        # 2.2 Future Random Beacon + XOR Geometric Distance Winner Selection
        candidates = [hashlib.sha256(f"CANDIDATE_BLOCK_{i}".encode()).digest() for i in range(50)]
        beacon_hash = hashlib.sha256(b"BEACON_BLOCK_HEADER_PLUS_100").digest()
        winner_hash, min_dist = compute_beacon_xor_winner(candidates, beacon_hash, epoch_index=1)
        self.assertIn(winner_hash, candidates)
        self.assertGreaterEqual(min_dist, 0)

        # 2.3 Streaming Vesting Schedule Calculation
        tranches = compute_jackpot_vesting_tranches(stage_id=4, jackpot_amount_satk=20_620 * SATS_PER_COIN, draw_block_height=100_000_000)
        self.assertEqual(len(tranches), 16)  # 1 instant + 15 streaming tranches (16 total)
        total_tranches_satk = sum(t["amount_satk"] for t in tranches)
        self.assertEqual(total_tranches_satk, 20_620 * SATS_PER_COIN)

        # 2.4 Pool Solvency & Zero-Inflation Invariant Test across simulated blocks
        current_issued = 0
        current_pool = 0
        total_miner_received = 0
        total_eco_received = 0
        
        for h in range(1, 1001):
            fake_hash = hashlib.sha256(f"BLOCK_{h}".encode()).digest()
            force_draw = (h % 50 == 0)
            res: BlockIssuanceResult = compute_block_issuance(
                block_height=h,
                block_hash=fake_hash,
                current_issued_sats=current_issued,
                current_pool_balance_sats=current_pool,
                force_combo=force_draw
            )
            
            self.assertEqual(res.minted_sats, res.miner_base_sats + res.pool_deposit_sats + res.eco_deposit_sats)
            self.assertGreaterEqual(res.new_pool_balance_sats, 0, "Jackpot pool went negative!")
            self.assertLessEqual(res.new_issued_supply_sats, TOTAL_HARD_CAP_SATK)
            
            total_miner_received += res.miner_total_payout_sats
            total_eco_received += res.eco_deposit_sats
            current_issued = res.new_issued_supply_sats
            current_pool = res.new_pool_balance_sats
            
            self.assertEqual(
                total_miner_received + current_pool + total_eco_received, current_issued,
                f"Supply mismatch: miner({total_miner_received}) + pool({current_pool}) + eco({total_eco_received}) != issued({current_issued})"
            )

    def test_privacy_stealth(self):
        miner = StealthKeyPair()
        public_addr = miner.get_public_stealth_address()

        R_ephem, P_out = StealthKeyPair.generate_one_time_stealth_output(public_addr)

        # Verification by miner
        is_mine = miner.is_my_stealth_output(R_ephem, P_out)
        self.assertTrue(is_mine, "Miner failed to claim own stealth output")
        
        # Verification by adversary (should be False)
        adversary = StealthKeyPair()
        self.assertFalse(adversary.is_my_stealth_output(R_ephem, P_out), "Adversary falsely claimed stealth output")

    def test_gateway(self):
        gw = AIRPCGateway(data_dir=self.temp_dir)
        agent_key = "agent_0x9876543210"
        
        # Expect 402
        req1 = gw.handle_request(agent_key, "deepseek-r1", "Explain quantum computing in 5 words.")
        self.assertEqual(req1["status_code"], 402)

        # Fund the quote produced by the KCU -> SatK value bridge.
        required_satk = req1["invoice"]["amount_satk"]
        gw.ledger.deposit(agent_key, required_satk)
        req2 = gw.handle_request(agent_key, "deepseek-r1", "Explain quantum computing in 5 words.")
        self.assertEqual(req2["status_code"], 200)
        cost = req2["usage"]["cost_sats"]
        rem = req2["usage"]["remaining_balance_sats"]
        self.assertEqual(cost + rem, required_satk)

    def test_kheavyhash_consensus(self):
        test_hdr = b"BTC-K_BLOCK_00000001_KASPA_ASIC_HEADER_PAYLOAD"
        h = kheavyhash(test_hdr)
        self.assertEqual(len(h), 32)


if __name__ == "__main__":
    unittest.main()

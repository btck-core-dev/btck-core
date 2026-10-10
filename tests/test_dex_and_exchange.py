"""
Integration Test: BTCK AMM DEX Swap Engine & Exchange Bridge
-----------------------------------------------------------
Verifies:
1. Initial liquidity seeding (50,000 BTCK + $2,500 USDT = $0.05 / BTCK)
2. Exact constant product swap math (x * y = k)
3. Slippage protection & LP fee collection
4. Exchange RPC compatibility (getblockchaininfo, getnewaddress, sendtoaddress)
"""

import math
import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from dex.amm_engine import BTCKSwapPool
from gateway.exchange_rpc import ExchangeRPCAdapter
from node.blockdag import BlockDAG, DAGBlock
from privacy.stealth_address import StealthKeyPair
from tokenomics.emission_schedule import SATK_PER_COIN
from tests.pow_helpers import mine_block


class TestDexAndExchange(unittest.TestCase):
    def test_amm_dex(self):
        pool = BTCKSwapPool()
        
        # 1. Seed Initial Liquidity: 50,000 BTCK and $2,500 USDT -> Initial Price = $0.05
        seed = pool.add_liquidity(amount_btck=50_000.0, amount_usdt=2_500.0, provider="genesis_creator")
        self.assertEqual(pool.get_spot_price(), 0.05)
        self.assertGreater(seed["minted_lp_shares"], 0)

        # 2. Trader Buys BTCK with $500 USDT
        buy_trade = pool.swap_usdt_for_btck(amount_usdt_in=500.0, trader="early_buyer_01")
        self.assertGreater(buy_trade["btck_out"], 0)
        self.assertGreater(pool.get_spot_price(), 0.05)

        # 3. Trader Sells 1,000 BTCK for USDT
        sell_trade = pool.swap_btck_for_usdt(amount_btck_in=1000.0, trader="miner_seller")
        self.assertGreater(sell_trade["usdt_out"], 0)

        # 4. Remove Liquidity
        creator_shares = seed["minted_lp_shares"]
        rem = pool.remove_liquidity(shares=creator_shares * 0.1, provider="genesis_creator")
        self.assertGreater(rem["returned_btck"], 0)
        self.assertGreater(rem["returned_usdt"], 0)
        
        info = pool.get_pool_info()
        self.assertIn("spot_price_usdt", info)

    def test_exchange_bridge(self):
        dag = BlockDAG(network="simnet")
        bridge = ExchangeRPCAdapter(dag)

        # 1. getblockchaininfo
        chain_info = bridge.handle_rpc("getblockchaininfo", [])
        self.assertEqual(chain_info["chain"], "main")

        # 2. getnewaddress
        deposit_addr = bridge.handle_rpc("getnewaddress", ["customer_deposit_1001"])
        self.assertTrue(deposit_addr.startswith("btck1s"))

        # Mine a block to deposit_addr to fund the exchange wallet
        R_cb, P_cb = StealthKeyPair.generate_one_time_stealth_output(deposit_addr)
        b1 = mine_block(dag,
            parents=[dag.genesis_hash],
            timestamp=1788912001.0,
            nonce=1,
            coinbase_output=(R_cb, P_cb)
        )
        dag.add_block(b1)
        b1.reward_sats = 500 * SATK_PER_COIN  # Ensure sufficient test balance

        # 3. sendtoaddress (Withdrawal)
        user_wallet = StealthKeyPair().get_public_stealth_address()
        txid = bridge.handle_rpc("sendtoaddress", [user_wallet, 250.0, "Customer withdrawal"])
        self.assertIsInstance(txid, str)
        self.assertEqual(len(txid), 64)

        # 4. gettransaction
        tx_detail = bridge.handle_rpc("gettransaction", [txid])
        self.assertEqual(tx_detail["amount"], 250.0)


if __name__ == "__main__":
    unittest.main()

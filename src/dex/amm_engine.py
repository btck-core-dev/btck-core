"""
BTCK Sovereign Automated Market Maker (AMM DEX Engine)
------------------------------------------------------
Implements Uniswap-v2 constant product formula: x * y = k
Enables decentralized, non-custodial trading between BTCK and USDT.
Features LP token minting/burning, 0.3% swap fee to liquidity providers,
slippage protection, and real-time price discovery.
"""

import math
import time
from typing import Dict, Any, Tuple, Optional

class BTCKSwapPool:
    def __init__(self, pool_id: str = "BTCK-USDT", fee_basis_points: int = 30):
        self.pool_id = pool_id
        self.fee_rate = fee_basis_points / 10000.0 # 0.003 (0.3% fee)
        
        # Reserves
        self.reserve_btck: float = 0.0
        self.reserve_usdt: float = 0.0
        
        # Liquidity Provider (LP) state
        self.total_lp_shares: float = 0.0
        self.lp_balances: Dict[str, float] = {} # provider_addr -> shares
        
        # Metrics & History
        self.total_volume_usdt: float = 0.0
        self.total_trades: int = 0
        self.trade_history: list = []

    def get_spot_price(self) -> float:
        """Returns price of 1 BTCK in USDT"""
        if self.reserve_btck == 0:
            return 0.0
        return self.reserve_usdt / self.reserve_btck

    def add_liquidity(self, amount_btck: float, amount_usdt: float, provider: str) -> Dict[str, Any]:
        """
        Add liquidity to the pool. Mints LP shares proportionally.
        First deposit sets the initial pool ratio.
        """
        if amount_btck <= 0 or amount_usdt <= 0:
            raise ValueError("Amounts must be positive")

        if self.total_lp_shares == 0:
            # Initial liquidity deposit (Geometric mean)
            shares = math.sqrt(amount_btck * amount_usdt)
        else:
            # Proportional shares based on current reserves
            share_btck = (amount_btck / self.reserve_btck) * self.total_lp_shares
            share_usdt = (amount_usdt / self.reserve_usdt) * self.total_lp_shares
            shares = min(share_btck, share_usdt)

        self.reserve_btck += amount_btck
        self.reserve_usdt += amount_usdt
        self.total_lp_shares += shares
        self.lp_balances[provider] = self.lp_balances.get(provider, 0.0) + shares

        return {
            "status": "success",
            "provider": provider,
            "added_btck": amount_btck,
            "added_usdt": amount_usdt,
            "minted_lp_shares": shares,
            "total_lp_shares": self.total_lp_shares,
            "spot_price_usdt": self.get_spot_price()
        }

    def remove_liquidity(self, shares: float, provider: str) -> Dict[str, Any]:
        """Burn LP shares and withdraw proportional BTCK and USDT"""
        if shares <= 0 or self.lp_balances.get(provider, 0.0) < shares:
            raise ValueError("Insufficient LP shares to withdraw")

        fraction = shares / self.total_lp_shares
        amount_btck = self.reserve_btck * fraction
        amount_usdt = self.reserve_usdt * fraction

        self.reserve_btck -= amount_btck
        self.reserve_usdt -= amount_usdt
        self.total_lp_shares -= shares
        self.lp_balances[provider] -= shares

        return {
            "status": "success",
            "provider": provider,
            "burned_shares": shares,
            "returned_btck": amount_btck,
            "returned_usdt": amount_usdt,
            "remaining_lp_shares": self.lp_balances[provider]
        }

    def swap_usdt_for_btck(self, amount_usdt_in: float, min_btck_out: float = 0.0, trader: str = "anon") -> Dict[str, Any]:
        """
        Buyer sends USDT, receives BTCK:
        dy = (y * dx_with_fee) / (x + dx_with_fee)
        """
        if amount_usdt_in <= 0:
            raise ValueError("Input amount must be positive")
        if self.reserve_btck <= 0 or self.reserve_usdt <= 0:
            raise RuntimeError("Liquidity pool is uninitialized")

        amount_in_with_fee = amount_usdt_in * (1.0 - self.fee_rate)
        numerator = self.reserve_btck * amount_in_with_fee
        denominator = self.reserve_usdt + amount_in_with_fee
        amount_btck_out = numerator / denominator

        if amount_btck_out < min_btck_out:
            raise RuntimeError(f"Slippage exceeded: expected at least {min_btck_out} BTCK, got {amount_btck_out}")

        initial_price = self.get_spot_price()
        
        # Execute state transition
        self.reserve_usdt += amount_usdt_in
        self.reserve_btck -= amount_btck_out
        
        new_price = self.get_spot_price()
        price_impact_pct = ((new_price - initial_price) / initial_price) * 100.0

        self.total_volume_usdt += amount_usdt_in
        self.total_trades += 1
        
        trade_record = {
            "type": "BUY_BTCK",
            "trader": trader,
            "usdt_in": amount_usdt_in,
            "btck_out": amount_btck_out,
            "execution_price": amount_usdt_in / amount_btck_out,
            "price_impact_pct": price_impact_pct,
            "timestamp": time.time()
        }
        self.trade_history.append(trade_record)
        return trade_record

    def swap_btck_for_usdt(self, amount_btck_in: float, min_usdt_out: float = 0.0, trader: str = "anon") -> Dict[str, Any]:
        """
        Seller sends BTCK, receives USDT:
        dx = (x * dy_with_fee) / (y + dy_with_fee)
        """
        if amount_btck_in <= 0:
            raise ValueError("Input amount must be positive")
        if self.reserve_btck <= 0 or self.reserve_usdt <= 0:
            raise RuntimeError("Liquidity pool is uninitialized")

        amount_in_with_fee = amount_btck_in * (1.0 - self.fee_rate)
        numerator = self.reserve_usdt * amount_in_with_fee
        denominator = self.reserve_btck + amount_in_with_fee
        amount_usdt_out = numerator / denominator

        if amount_usdt_out < min_usdt_out:
            raise RuntimeError(f"Slippage exceeded: expected at least {min_usdt_out} USDT, got {amount_usdt_out}")

        initial_price = self.get_spot_price()
        
        # Execute state transition
        self.reserve_btck += amount_btck_in
        self.reserve_usdt -= amount_usdt_out
        
        new_price = self.get_spot_price()
        price_impact_pct = ((initial_price - new_price) / initial_price) * 100.0

        self.total_volume_usdt += amount_usdt_out
        self.total_trades += 1

        trade_record = {
            "type": "SELL_BTCK",
            "trader": trader,
            "btck_in": amount_btck_in,
            "usdt_out": amount_usdt_out,
            "execution_price": amount_usdt_out / amount_btck_in,
            "price_impact_pct": price_impact_pct,
            "timestamp": time.time()
        }
        self.trade_history.append(trade_record)
        return trade_record

    def get_pool_info(self) -> Dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "reserve_btck": round(self.reserve_btck, 4),
            "reserve_usdt": round(self.reserve_usdt, 2),
            "spot_price_usdt": round(self.get_spot_price(), 6),
            "total_liquidity_usdt": round(self.reserve_usdt * 2, 2),
            "total_lp_shares": round(self.total_lp_shares, 4),
            "total_trades": self.total_trades,
            "total_volume_usdt": round(self.total_volume_usdt, 2)
        }

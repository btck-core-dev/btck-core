# WhatToMine (WTM) Listing Application Package

This document outlines the complete technical, hardware, and economic profile for listing **Bitcoin K (BTCK)** on [WhatToMine](https://whattomine.com).

---

## 1. Core Algorithm & Hardware Profiles

* **Algorithm**: `kHeavyHash`
* **Target Block Time**: `1.000` second
* **Hashrate Formula**: $\text{Hashrate (H/s)} = \text{Network Difficulty} \times 2^{32} / 1.0\text{s}$

### Standard ASIC Benchmark Profiles

| Hardware Model | Nominal Hashrate | Power Consumption | Efficiency |
| :--- | :--- | :--- | :--- |
| **IceRiver KS0** | 100 GH/s | 65 W | 0.65 J/GH |
| **IceRiver KS0 Pro** | 200 GH/s | 100 W | 0.50 J/GH |
| **IceRiver KS0 Ultra** | 400 GH/s | 100 W | 0.25 J/GH |
| **IceRiver KS1** | 1,000 GH/s (1 TH/s) | 600 W | 0.60 J/GH |
| **IceRiver KS2** | 2,000 GH/s (2 TH/s) | 1,200 W | 0.60 J/GH |
| **IceRiver KS3 / KS3M** | 6,000 ~ 8,000 GH/s | 3,200 W | 0.40 ~ 0.53 J/GH |
| **Antminer KS3** | 8,300 GH/s (8.3 TH/s) | 3,188 W | 0.38 J/GH |
| **IceRiver KS5L / KS5M** | 12,000 ~ 15,000 GH/s | 3,400 W | 0.23 ~ 0.28 J/GH |
| **Antminer KS5 / KS5 Pro**| 20,000 ~ 21,000 GH/s | 3,150 W | 0.15 J/GH |

---

## 2. Emission & Daily Reward Formula

BTC-K operates on a 4-year rational continuous decay model. Unlike 4-year halving step reductions, the daily block reward follows:

$$R(t) = \frac{R_0}{1 + 0.15 \cdot t_{\text{day}}}$$

* **Initial Day 0 Reward**: 6.2982 BTCK / block (Miner base payout: 5.0385 BTCK, 80%)
* **Blocks Per Day**: 86,400 blocks (1.0s interval)
* **Initial Daily Miner Emission (Day 0)**: $86,400 \times 5.0385 = 435,326.4\text{ BTCK/day}$
* **Combo Jackpot Pool (20%)**: $86,400 \times 1.2596 = 108,829.4\text{ BTCK/day}$ (disbursed in 12h epochs)

### WTM Calculation Formula
$$\text{Estimated Daily Coins for Worker} = \frac{\text{Worker Hashrate}}{\text{Network Hashrate}} \times \text{Daily Miner Base Emission}$$

---

## 3. Market Pricing & DEX Integration

* **Native Asset**: Bitcoin K (`BTCK`)
* **Wrapped ERC-20 Asset**: Wrapped BTC-K (`wBTCK`)
* **Primary DEX Pair**: `wBTCK / USDT` (Uniswap V3 on Base or Arbitrum)
* **DEX Pair Contract**: To be populated upon Phase 5 launch
* **Price Oracles / Trackers**:
  - DEXScreener: `https://dexscreener.com/base/...`
  - GeckoTerminal: `https://geckoterminal.com/base/pools/...`
  - CoinGecko: `https://www.coingecko.com/en/coins/bitcoin-k`

---

## 4. Stratum & Network Data Sources

* **Network Difficulty API**: `https://pool.btck.network/api/stats` (field: `network.difficulty`)
* **Network Hashrate API**: `https://pool.btck.network/api/stats` (field: `network.hashrate_raw`)
* **Block Height API**: `https://pool.btck.network/api/stats` (field: `network.height`)
* **Block Explorer API**: `https://explorer.btck.network/api/v1/network`

---

## 5. Contact & Support

* **Contact Email**: `listings@btck.network`
* **Telegram**: `https://t.me/btck_official`
* **Twitter**: `@BitcoinK_Net`

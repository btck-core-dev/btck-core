# MiningPoolStats (MPS) Listing Application Package

This document contains the complete, canonical data payload required to submit **Bitcoin K (BTCK)** for listing on [MiningPoolStats](https://miningpoolstats.stream).

---

## 1. General Coin Profile

| Field | Value | Notes |
| :--- | :--- | :--- |
| **Coin Name** | Bitcoin K | Full official name |
| **Ticker Symbol** | `BTCK` | Universal 4-character symbol |
| **Consensus Mechanism** | Proof-of-Work (PoW) | GhostDAG ($K=18$) |
| **Mining Algorithm** | `kHeavyHash` | Matrix multiplication over $64 \times 64$ integer field |
| **ASIC Compatibility** | 100% Native Kaspa ASICs | IceRiver (KS0-KS5), Antminer (KS3/KS5) |
| **Block Time / Cadence** | 1.000 second | 1.000 blocks per second (BPS) |
| **Block Reward** | 6.29815765 BTCK budget at block 1 | 80% miner + 15% Combo + 5% ecosystem reserve |
| **Max / Hard Cap** | 21,000,000.00000000 BTCK | $2,100,000,000,000,000$ SatK (Zero drift) |
| **Premine / ICO** | Genesis block 0 SatK; no ICO/private allocation | Disclosed controlled guardian mining through block 271,828; not a block-0 open-mining fair launch |
| **Genesis Checkpoint** | Block `#271,828` | Euler constant mapping ($e = 2.71828...$) |
| **Official Website** | `https://btck.network` | Primary portal |
| **Block Explorer** | `https://explorer.btck.network` | Real-time DAG visualizer & tx search |
| **Source Code Repository**| `https://github.com/btck-org/btck-core` | Open-source MIT licensed |
| **Bitcointalk ANN** | `https://bitcointalk.org/index.php?topic=...` | Official announcement thread |

---

## 2. Mining Pool Standard API Specification

MiningPoolStats crawls mining pools using standard JSON endpoints. The official BTC-K Stratum Pool exposes this endpoint at:

```
GET https://pool.btck.network/api/stats
```

### Response Schema (JSON)

```json
{
  "pool": {
    "name": "Official BTC-K Stratum Pool",
    "url": "https://pool.btck.network",
    "fee_percent": 0.0,
    "fee_type": "PPLNS / SOLO",
    "stratum": "stratum+tcp://pool.btck.network:3333",
    "port": 3333,
    "min_payout_btck": 1.0,
    "hashrate_raw": 375000000000,
    "hashrate_formatted": "375 GH/s",
    "miners_count": 3,
    "workers_count": 3
  },
  "network": {
    "algorithm": "kHeavyHash",
    "height": 272500,
    "difficulty": 450.25,
    "hashrate_raw": 375000000000,
    "hashrate_formatted": "375 GH/s",
    "block_time_sec": 1.0,
    "last_block_hash": "a4f8e219...",
    "last_block_time": 1789668000
  },
  "tokenomics": {
    "total_supply": 1505000.0,
    "max_supply": 21000000.0,
    "block_reward_base": 5.0385,
    "combo_reserve": 1.2596
  }
}
```

---

## 3. Official Stratum Endpoints for Crawlers

* **Primary Stratum URL**: `stratum+tcp://pool.btck.network:3333`
* **Backup Stratum URL**: `stratum+tcp://backup-pool.btck.network:3333`
* **Supported Rig Types**: IceRiver KS0 / KS0 Pro / KS0 Ultra / KS1 / KS2 / KS3 / KS3M / KS5L, Bitmain Antminer KS3 / KS5
* **Variable Difficulty**: Enabled (Vardiff range: 5.0 to 5000.0)

---

## 4. Contact & Verification Channels

* **Core Team Email**: `core@btck.network` / `pool@btck.network`
* **Discord Handle**: `@btck_dev`
* **Telegram Contact**: `https://t.me/btck_official`
* **Twitter / X Handle**: `@BitcoinK_Net`

---

## 5. Submission Checklist

- [x] Genesis Checkpoint `#271,828` passed and locked into consensus.
- [x] Official pool running for > 24 hours with valid blocks and zero rejected shares.
- [x] Public block explorer online with verified DAG blocks and difficulty metrics.
- [x] Pool API endpoint `/api/stats` accessible over HTTPS.
- [x] GitHub repository public and licensed under MIT.

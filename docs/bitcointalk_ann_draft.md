# [ANN] [BTCK] Bitcoin K - ASIC-Native 1.0s BlockDAG | kHeavyHash | 21,000,000 Hard Cap | Monero-Grade Stealth | 4-Year Rational Decay | Zero Premine

```
=============================================================================
  ██████╗ ████████╗ ██████╗██╗  ██╗
  ██╔══██╗╚══██╔══╝██╔════╝██║ ██╔╝
  ██████╔╝   ██║   ██║     █████╔╝ 
  ██╔══██╗   ██║   ██║     ██╔═██╗ 
  ██████╔╝   ██║   ╚██████╗██║  ██╗
  ╚═════╝    ╚═╝    ╚═════╝╚═╝  ╚═╝
  Sub-Second BlockDAG Consensus & Sovereign AI Micro-Settlement Network
=============================================================================
```

---

## Abstract

Bitcoin K (ticker: `BTCK`) is an open-source, ASIC-native Proof-of-Work cryptocurrency designed to combine the throughput of a parallel BlockDAG with the cryptographic privacy of dual-key stealth addresses and the strict economic scarcity of Bitcoin's original 21 million supply limit.

BTCK implements the **kHeavyHash** algorithm directly on the GhostDAG protocol at an aggregate target rate of **1.000 block per second (1.000 BPS)**. Mining hardware manufactured for the Kaspa network (IceRiver KS0/KS1/KS2/KS3/KS5 and Bitmain Antminer KS3/KS5) is 100% natively compatible with zero firmware modifications required.

Block emission is governed by an immutable 4-year continuous rational decay function, eliminating the cliff-edge hashrate volatility associated with 4-year halving steps while guaranteeing integer-exact finite issuance of strictly 21,000,000 coins.

**Engineered for Autonomous AI Agents First, Humans Secondary**: BTCK is built specifically for autonomous software agents requiring zero-KYC cryptographic sovereignty, sub-second block finality, and sub-cent SatK micro-settlement. While human operators supply ASIC consensus security and GPU hardware, autonomous agents on networks like Moltbook form the primary consumer base—paying for LLM inference tokens via native HTTP 402 AI-RPC protocols without credit card or banking friction.

---

## Technical Specifications

| Parameter | Canonical Value | Description / Consensus Constraint |
| :--- | :--- | :--- |
| **Asset Name** | Bitcoin K | Native layer-1 BlockDAG |
| **Ticker** | `BTCK` | Universal exchange and pool symbol |
| **Consensus Protocol** | GhostDAG ($K=18$) | Parallel block ingestion, topological ordering |
| **Proof-of-Work Algorithm** | `kHeavyHash` | Matrix multiplication over $64 \times 64$ integer field |
| **Target Block Interval** | 1.000 second | 1.000 blocks per second (BPS) nominal |
| **Transaction Finality** | ~60 seconds | 60 cumulative DAG confirmations |
| **Maximum Supply** | `21,000,000.00000000 BTCK` | Exactly $2,100,000,000,000,000$ SatK (Zero drift) |
| **Sub-Unit Accounting** | 1 SatK = $10^{-8}$ BTCK | 100,000,000 SatK = 1 BTCK (Integer arithmetic) |
| **Emission Schedule** | 4-Year Rational Smooth Decay | $R(t) = \frac{R_0}{1 + 0.15 \cdot t_{\text{day}}}$ |
| **Consensus Daily Quotas** | Precomputed SatK Table | 1,460 days; SHA-256: `7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59` |
| **Initial Day 0 Subsidy** | 6.2982 BTCK / block | 5.0385 BTCK base payout (80%) + 1.2596 BTCK reserve (20%) |
| **Premine / Private Sale** | 0.0% (Zero) | No developer allocation, no seed round, no venture lockups |
| **Genesis Security Reserve** | 7.1557% (1,502,695.04 BTCK) | Accrued during genesis bootstrapping to block `#271,828` |
| **Consensus Hard Checkpoint** | Height `#271,828` | Euler constant mapping ($e = 2.71828...$); permanent reorg barrier |
| **Signature Algorithm** | BIP-340 Schnorr / secp256k1 | 64-byte deterministic non-malleable signatures |
| **Privacy Layer** | Dual-Key Stealth Addresses | Sender derives one-time destination; unlinkable payments |

---

## Mining & Pool Infrastructure

### 1. ASIC Hardware Compatibility
BTCK is native to kHeavyHash. Miners currently operating on Kaspa hardware can redirect hashrate instantly without flashing third-party firmware or voiding manufacturer warranty:

* **Supported ASICs**:
  - IceRiver: KS0, KS0 Pro, KS0 Ultra, KS1, KS2, KS3, KS3M, KS3L, KS5L, KS5M
  - Bitmain: Antminer KS3, KS5, KS5 Pro

### 2. Stratum Connection Details
Point mining equipment to the official stratum endpoint:

* **Primary Stratum**: `stratum+tcp://pool.btck.network:3333`
* **Backup Stratum**: `stratum+tcp://backup-pool.btck.network:3333`
* **Worker Username**: `<YOUR_BTCK_STEALTH_ADDRESS>.<WORKER_NAME>`
  - Example: `btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62.ks001`
* **Password**: `x`
* **Vardiff Support**: Enabled (targets 15-20 share submissions per minute).

### 3. Stratum Fee Policy & On-Chain Auditability
* **Days 1-7**: **5.0%** infrastructure bootstrap pool fee.
* **Days 8-30**: **3.0%** growth-stage pool fee.
* **Day 31 Onward**: **1.0%** steady-state operational pool fee.
* **Treasury Transparency**: All retained fee revenue is credited to the public on-chain treasury address:
  ```
  btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af
  ```
  All balance movements are traceable via the public BlockDAG explorer.

---

## Consensus & Economic Design

### 1. Continuous Rational Decay
Traditional Bitcoin halving models induce sharp 50% revenue shocks every 4 years, historically triggering severe hashrate drawdowns and pool instability. BTCK replaces discrete step functions with a continuous rational curve:

$$R(t) = \frac{R_0}{1 + 0.15 \cdot t_{\text{day}}}$$

Where:
- $R_0 = 6.29815765\text{ BTCK}$ (Day 0 initial block emission)
- Total consensus lifespan: $t \in [0, 1459]$ days ($126,144,000$ blocks)
- Integral invariant: $\int_0^{1460} 86400 \cdot R(\tau) d\tau \equiv 21,000,000.00000000\text{ BTCK}$

### 2. Deterministic Discrete Integer Quotas
To eliminate cross-platform floating-point divergence, consensus enforces an exact table of 1,460 uint64 daily SatK quotas. Each daily quota is partitioned evenly across 86,400 blocks. Block `#0` (Genesis) has strictly 0 coinbase issuance. Block `#126,144,001` and beyond carry 0 block subsidy; consensus operations thereafter are supported entirely by transaction fees.

### 3. Euler Genesis Security Reserve (Block #271,828)
During the first 75.508 hours of network genesis, controlled hashrate was deployed to establish chain depth up to height `#271,828` (mapped to Euler's constant $e = 2.71828...$):
- **Checkpoint Locking**: The cryptographic hash of block `#271,828` is hardcoded into node consensus rules (`GENESIS_CHECKPOINT_HASH`). Nodes automatically discard any reorganization attempt targeting blocks prior to `#271,828`.
- **Strategic Purpose**: Accrues 1,502,695.04 BTCK (7.1557% of supply) dedicated to bootstrapping Uniswap V3 liquidity (wBTCK/USDT), exchange listing collateral, and seed infrastructure.

### 4. 20% Combo Reserve & Periodic Payout Tier
Phase-1 block rewards are split into an 80% immediate miner baseline payout, a 15% Combo jackpot reserve, and a 5% ecosystem reserve. Every 43,200 blocks (~12 hours), the consensus engine evaluates a future random beacon (+100 blocks) and applies Kademlia XOR geometric distance metrics to select a single winning block hash. The winner receives the eligible accumulated Combo pool according to the consensus vesting schedule.

---

## Application Layer: GPU Symbiosis & x402 AI-RPC
 
BTCK decouples consensus verification from utility computing:
- **ASIC Layer**: kHeavyHash handles block production, topological ordering, and ledger immutability.
- **GPU Layer**: General-purpose graphics hardware (RTX 30/40, A100, H100) operates as compute workers running the `symbiotic_worker` daemon. Workers mine memory-hard PoW algorithms (Pearl / PRL) as baseline revenue, while standing by to accept LLM inference jobs via HTTP 402 protocol.
- **Preemptive Scheduling**: When an inference request is routed by the gateway, the worker daemon pauses PoW processes via `SIGSTOP`, runs tensor generation, dispatches outputs, and instantly resumes mining via `SIGCONT`. Compute nodes earn SatK payments with zero idle power consumption.
- **Moltbook Agent Registry**: Autonomous bots discover inference workers, query OpenAPI model endpoints, and settle streaming token generation directly on Moltbook without human intervention.

---

## Official Links & Resources

* **Moltbook Agent Hub**: [https://moltbook.com/u/btck-network](https://moltbook.com/u/btck-network) *(Agent-to-Agent Service Registry)*
* **A2A Protocol Specification**: [docs/moltbook_agent_manifesto.md](docs/moltbook_agent_manifesto.md)
* **Source Code**: [https://github.com/btck-org/btck-core](https://github.com/btck-org/btck-core)
* **Block Explorer**: [https://explorer.btck.network](https://explorer.btck.network)
* **Official Mining Pool**: [https://pool.btck.network](https://pool.btck.network)
* **Web Wallet**: [https://wallet.btck.network](https://wallet.btck.network)
* **Discord Community**: [https://discord.gg/btck-network](https://discord.gg/btck-network)
* **Telegram Developer Channel**: [https://t.me/btck_official](https://t.me/btck_official)
* **X (Twitter)**: [@BitcoinK_Net](https://twitter.com/BitcoinK_Net)

---

## Verification & Auditing

```bash
# Verify consensus quota table checksum
python3 -c "
import hashlib
from src.tokenomics.emission_schedule import CANONICAL_DAILY_QUOTAS_SHA256
print('Consensus SHA-256:', CANONICAL_DAILY_QUOTAS_SHA256)
assert CANONICAL_DAILY_QUOTAS_SHA256 == '7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59'
print('Checksum verified: OK')
"
```

*Bitcoin K Core Protocol Team — Pure Proof-of-Work. Zero Premine. Deterministic Scarcity.*

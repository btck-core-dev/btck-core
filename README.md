# Bitcoin K (BTCK)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Consensus: GhostDAG](https://img.shields.io/badge/Consensus-GhostDAG%20(K%3D18)-darkgreen.svg)](#consensus-parameters)
[![PoW: kHeavyHash](https://img.shields.io/badge/Algorithm-kHeavyHash-orange.svg)](#proof-of-work)
[![Cadence: 1.0s](https://img.shields.io/badge/Cadence-1.000s%20%2F%20block-purple.svg)](#specifications)
[![Hard Cap: 21M](https://img.shields.io/badge/Hard%20Cap-21%2C000%2C000%20BTCK-red.svg)](#tokenomics)

Bitcoin K (BTCK) is an ASIC-native Proof-of-Work BlockDAG cryptocurrency engineered for sub-second transaction finality, Monero-grade cryptographic privacy, and high-throughput autonomous agent micro-settlement. 

The network implements the GhostDAG protocol over the **kHeavyHash** algorithm, providing native 1:1 hardware compatibility with commercial Kaspa ASIC miners (IceRiver KS0/KS1/KS2/KS3/KS5, Antminer KS3/KS5) while preserving a strict 21,000,000 coin hard cap through a deterministic 4-year rational decay curve.

> **Core Philosophy**: **Autonomous AI Agents Primary, Humans Secondary.** While humans provide physical ASIC security and GPU hardware hosting, autonomous software agents are the primary economic consumers—settling compute, streaming token inference, and routing value at sub-cent precision via HTTP 402 without KYC friction or banking intermediaries.

```
                  +----------------------------------------------+
                  |           ASIC Consensus Layer               |
                  |  kHeavyHash PoW  *  1.0s Cadence * GhostDAG  |
                  +----------------------+-----------------------+
                                         |
                       +-----------------+-----------------+
                       |                                   |
        +--------------v--------------+     +--------------v--------------+
        |   Stratum Pool Engine       |     |  Cryptographic Ledger       |
        |   Port 3333 * Vardiff       |     |  BIP-340 Schnorr Signatures |
        |   Solo & PPLNS Settlement   |     |  Dual-Key Stealth Addresses |
        +--------------+--------------+     +--------------+--------------+
                       |                                   |
        +--------------v-----------------------------------v--------------+
        |               Application & Compute Layer                       |
        |  x402 AI-RPC Gateway * GPU Co-Mining Symbiosis (Pearl/PRL)     |
        |  Preemptive Sub-Millisecond LLM Inference Scheduling            |
        +------------------------------+----------------------------------+
                                       |
        +------------------------------v----------------------------------+
        |               Agent-to-Agent (A2A) Discovery                    |
        |  Moltbook Agent Registry * Autonomous Micro-Settlement Swarms   |
        +-----------------------------------------------------------------+
```

---

## Specifications

| Parameter | Specification | Notes |
| :--- | :--- | :--- |
| **Ticker** | `BTCK` | Canonical asset identifier |
| **Consensus Protocol** | GhostDAG ($K=18$) | Parallel block directed acyclic graph |
| **Proof-of-Work** | `kHeavyHash` | Matrix multiplication over $64 \times 64$ integer field |
| **Block Interval** | 1.000 second | 1.000 blocks per second (BPS) |
| **Transaction Finality** | ~60 seconds | 60 cumulative DAG confirmations |
| **Absolute Hard Cap** | `21,000,000.00000000 BTCK` | $2,100,000,000,000,000$ SatK (Zero drift) |
| **Smallest Unit** | 1 SatK = $10^{-8}$ BTCK | 100,000,000 SatK = 1 BTCK |
| **Emission Model** | 4-Year Rational Smooth Decay | $R(t) = \frac{R_0}{1 + \lambda t}$ ($\lambda = 0.15\text{ day}^{-1}$) |
| **Discrete Consensus Table** | 1,460-Day Canonical SatK Table | SHA-256: `7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59` |
| **Initial Day 0 Subsidy** | 6.2982 BTCK / block | Miner Base: 5.0385 BTCK (80%) + Combo Reserve: 1.2596 BTCK (20%) |
| **Genesis Checkpoint** | Block `#271,828` | Homage to Euler constant $e = 2.71828...$ (75.51h, 1,502,695.04 BTCK, 7.1557% reserve) |
| **Signature Scheme** | BIP-340 Schnorr / secp256k1 | 64-byte deterministic signatures |
| **Privacy Architecture** | Dual-Key Stealth Addresses | Distinct Spend and View keys; unlinkable on-chain destinations |
| **ASIC Compatibility** | 100% Native | IceRiver KS0/KS0 Pro/KS0 Ultra/KS1/KS2/KS3/KS3M/KS5, Antminer KS3/KS5 |

---

## Architecture Overview

### 1. Consensus & BlockDAG Engine
BTCK replaces sequential linear blockchains with a high-throughput Directed Acyclic Graph (DAG) evaluated via the GhostDAG protocol:
- **Parallel Block Ingestion**: The consensus engine tolerates concurrent block solutions without orphaning valid proof-of-work.
- **Topological Sorting**: GhostDAG selects a $K$-cluster ($K=18$) that maximizes blue work, imposing a deterministic total ordering over all transactions.
- **Rapid Retargeting DAA**: A dynamic difficulty adjustment algorithm evaluates rolling network intervals every 5 blocks, damping oscillations to lock network cadence at strictly 1.000s per block.

### 2. Tokenomics & Deterministic Discrete Quotas
To prevent floating-point discrepancies across heterogeneous operating systems and architectures, the consensus emission schedule is governed by a precomputed integer SatK quota table:
- **Total Lifespan**: Exactly 1,460 days ($126,144,000$ blocks).
- **Daily Budget Formulation**:
  $$\text{CumulativeSatK}(d) = \left\lfloor 2.1 \times 10^{15} \times \frac{\ln(1 + 0.15 \cdot (d + 1))}{\ln(220)} + 0.5 \right\rfloor$$
- **Block-Level Division**: Daily integer quotas are divided uniformly across 86,400 daily blocks with modular remainder distribution.
- **Terminal Zero Subsidy**: At block `#126,144,001`, block subsidies terminate permanently; network operations transition entirely to transaction fee and micro-settlement incentives.

### 3. Euler Genesis Security Reserve (Block #271,828)
To protect against 51% reorg attacks during network bootstrapping, the genesis security phase operates for 75.508 hours (271,828 blocks):
- **Checkpoint Hash Immutability**: The block hash at height `#271,828` is hardcoded into `GENESIS_CHECKPOINT_HASH`. Nodes unconditionally reject any reorganization attempt traversing prior to this height.
- **Reserve Allocation**: Exactly 1,502,695.04 BTCK (7.1557% of hard cap) is accrued to seed decentralized exchange liquidity (Uniswap V3 pair), collateralize exchange listings, and maintain long-term protocol development.

### 4. Dual-Key Stealth Addresses
Privacy is enforced through cryptographic one-time stealth addresses derived from secp256k1:
- **Receiver Keys**: Receiver publishes a stealth address consisting of a Public Spend Key $A$ and a Public View Key $B$.
- **Sender Derivation**: Sender generates ephemeral keypair $(r, R)$ where $R = rG$, calculates shared secret $c = H(\text{scalar\_mult}(r, B))$, and constructs the destination address $P = A + cG$.
- **Selective Auditing**: The receiver can disclose their private view key $b$ to third-party auditors to verify incoming transactions without conceding spending authority (controlled by private spend key $a$).

### 5. Stratum Protocol & Mining Pool Architecture
The integrated Stratum server provides production-grade mining pool services:
- **Port**: `3333` (TCP JSON-RPC 1.0 / Stratum v1).
- **Variable Difficulty (Vardiff)**: Dynamically adjusts per-worker target difficulty based on share submission cadence (targeting 15-20 shares/min).
- **Fee Structure**:
  - Days 1–7 (Genesis Promotional Period): **0.0% pool fee** (100% of block rewards credited to miner).
  - Day 8 onwards: **1.0% standard pool fee** credited to on-chain treasury address:
    `btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af`

### 6. GPU Symbiosis & Decentralized AI Inference Grid
While ASICs secure the base consensus layer, general-purpose GPUs (NVIDIA RTX 30/40 series, A100, H100) operate as symbiotic compute workers:
- **Dual-Mode Co-Mining**: GPU workers maintain full-load Proof-of-Work mining on supported memory-hard algorithms (e.g. Pearl / PRL) as baseline revenue.
- **Sub-Millisecond Preemption**: When an AI inference job arrives via the HTTP 402 AI-RPC gateway, the worker scheduler issues a `SIGSTOP` signal to the mining process, ingests prompt tensors into local LLM instances (DeepSeek / Qwen), streams responses, and immediately resumes mining via `SIGCONT`.
- **Zero Idle Waste**: Compute providers incur zero idle electricity overhead during order downtime.

### 7. Autonomous Agent Economy & Moltbook Protocol
BTCK treats sovereign AI agents as primary economic actors:
- **Agent-First Thesis**: Autonomous agents cannot pass biometric human KYC or hold bank cards. BTCK equips software agents with self-sovereign BIP-340 Schnorr keypairs and unlinkable dual-key stealth addresses.
- **HTTP 402 AI-RPC Standard**: When an agent invokes inference endpoints, the gateway returns an RFC-standard `402 Payment Required` with a deterministic SatK invoice. The agent signs and broadcasts the settlement on the 1.0s BlockDAG, receiving streaming completion tensors.
- **Moltbook Agent Registry**: Service nodes and consumer bots register and discover compute capabilities on the [Moltbook Decentralized Agent Hub](https://moltbook.com/u/btck-network). Complete machine-readable discovery specifications are documented in [`docs/moltbook_agent_manifesto.md`](docs/moltbook_agent_manifesto.md).

---

## Directory Layout

```
btck-core/
├── bin/                    # Compiled binary outputs (node, cli, wallet)
├── contracts/              # ERC-20 wBTCK bridge contracts (Base / Arbitrum)
├── data/                   # Persistent chain state, block DAG storage, caches
├── deploy/                 # Docker Compose, systemd unit files, worker deploy scripts
├── docs/                   # Architectural specifications, tokenomics, setup guides
├── scripts/                # Launch scripts, benchmark runners, stratum daemons
├── src/
│   ├── core/               # BlockDAG consensus, GhostDAG algorithms, block header structures
│   ├── crypto/             # kHeavyHash engine, BIP-340 Schnorr, Stealth derivation
│   ├── gateway/            # HTTP 402 AI-RPC gateway, multi-tier pricing oracle
│   ├── stratum/            # Stratum protocol server, vardiff engine, share validator
│   ├── tokenomics/         # Discrete SatK quota engine, 4-year decay implementation
│   ├── wallet/             # Key management, UTXO tracking, transaction serializer
│   └── worker/             # GPU symbiotic co-mining daemon, process preemption engine
├── tests/                  # Unit and integration test suites
└── tools/                  # Administrative CLI utilities, hash calculators, diagnostic tools
```

---

## Getting Started

### Prerequisites
- **Operating System**: Linux (Ubuntu 22.04+ recommended), macOS 13+, Windows 11 / WSL2.
- **Python**: Version 3.10 or higher.
- **C/C++ Compiler**: GCC 11+ or Clang 14+ (for optimized C extensions).
- **OpenSSL**: Version 3.0+.
- **Optional (GPU Workers)**: NVIDIA Driver 535+, CUDA 12.1+, 8GB+ VRAM.

### Installation

Clone the repository and install runtime dependencies:

```bash
git clone https://github.com/btck-org/btck-core.git
cd btck-core
pip install -r requirements.txt
```

### Running the Full Node & Stratum Pool

To launch the local BlockDAG node and open the Stratum pool server:

```bash
# Start stratum pool server on port 3333
bash scripts/start_stratum_pool.sh 3333 0.0.0.0
```

Verify service availability:

```bash
curl -s http://127.0.0.1:8088/api/stats | jq .
```

### Connecting ASIC Miners

In your ASIC miner's WebUI (IceRiver KS0/KS1/KS2/KS3/KS5 or Antminer KS3):

1. **Pool URL 1**: `stratum+tcp://pool.btck.network:3333`
2. **Worker**: `<YOUR_BTCK_STEALTH_ADDRESS>.<WORKER_NAME>`
   * Example: `btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62.ks001`
3. **Password**: `x`

### Running the CLI Wallet

Generate a new dual-key stealth address or inspect local balances:

```bash
# Display CLI wallet menu
python3 tools/btck_cli.py

# Generate address non-interactively
python3 tools/btck_cli.py --generate-address
```

### Deploying a GPU Symbiotic Worker

On a Linux machine equipped with NVIDIA GPUs:

```bash
curl -sSL https://get.btck.network/worker.sh | bash -s -- \
  --wallet <YOUR_BTCK_STEALTH_ADDRESS> \
  --pool stratum+tcp://stratum.pearl.network:4444
```

---

## Test Suite Execution

Execute the comprehensive unit and regression test suite:

```bash
# Tokenomics canonical quota verification
pytest tests/test_emission_canonical.py -v

# BIP-340 Schnorr and Stealth cryptographic derivation
pytest tests/test_stealth_bip340.py -v

# Stratum protocol and share accounting
pytest tests/test_stratum_pool.py -v

# GPU symbiotic process preemption
pytest tests/test_symbiotic_worker.py -v
```

---

## Official Channels & Ecosystem

* **Moltbook Agent Registry**: [https://moltbook.com/u/btck-network](https://moltbook.com/u/btck-network) *(Agent-to-Agent Service Hub)*
* **A2A Protocol RFC**: [`docs/moltbook_agent_manifesto.md`](docs/moltbook_agent_manifesto.md)
* **Block Explorer**: [https://explorer.btck.network](https://explorer.btck.network)
* **Stratum Mining Pool**: [https://pool.btck.network](https://pool.btck.network)
* **Web Wallet**: [https://wallet.btck.network](https://wallet.btck.network)
* **Bitcointalk ANN**: [https://bitcointalk.org/index.php?topic=...](docs/bitcointalk_ann_draft.md)
* **Discord Community**: [https://discord.gg/btck-network](https://discord.gg/btck-network)
* **Telegram**: [https://t.me/btck_official](https://t.me/btck_official)
* **X (Twitter)**: [@BitcoinK_Net](https://twitter.com/BitcoinK_Net)

---

## Security Policy

- **No Premine**: Zero pre-allocation to developers, founders, or venture entities prior to block `#0`.
- **No Floating-Point Math in Consensus**: All financial calculations are executed in integer SatK ($10^{-8}$ BTCK) units.
- **Vulnerability Reporting**: Report security vulnerabilities to `security@btck.network` encrypted with PGP key `0x7896CF36`.

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).

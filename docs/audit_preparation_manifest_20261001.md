# BTC-K (Bitcoin-KHeavyHash) 核心架构审计备忘录与技术全景清单

> **归档日期**：2026-10-01  
> **版本**：v1.0.0-Release-Candidate (Git Commit: `3a2418d`)  
> **安全级别**：Confidential / Pre-Audit  
> **测试状态**：全链路自动化测试通过（115 / 115 单元与集成测试全部 PASS）

---

## 一、 系统架构全景与技术选型

BTC-K 是一条融合 **比特币货币哲学（2100万硬顶/4年减半）**、**Kaspa 级 GhostDAG 极速共识（1秒出块/高吞吐）**、**Dual-Key Stealth Address（DKSAP）全链隐私保护** 与 **BIP-340 Schnorr 离线签名** 的自主主权公链。

```mermaid
graph TD
    subgraph Layer0 [物理硬件与算力接入层]
        KS0["IceRiver KS0 / KS0 Pro<br>(Port 3333, Diff 256~512)"]
        KS1["IceRiver KS1 / KS2<br>(Port 3334, Diff 4096)"]
        KS3["IceRiver KS3 / KS3L<br>(Port 3335, Diff 16384)"]
        KS5["IceRiver KS5 / KS7<br>(Port 3336, Diff 65536)"]
    end

    subgraph Layer1 [Stratum 矿池与分账中枢]
        Stratum["Stratum Bridge (stratum_server.py)<br>• 多端口独立难度映射<br>• VarDiff 自适应流控<br>• ASIC 64位字节序解算"]
        PPLNS["PPLNS 引擎 (pplns.py)<br>• SQLite WAL 高并发账本<br>• 滑动窗口难度权重均摊<br>• AutoPayoutWorker 自动清结算"]
        Treasury["Pool Treasury 资金库<br>(BIP-340 离线签名)"]
    end

    subgraph Layer2 [GhostDAG 共识与网络核心]
        btckd["BlockDAG 节点 (btckd)<br>• GhostDAG 蓝块拓扑选路<br>• GHOSTDAG Blue Work 仲裁<br>• 2100万总供给量通缩释放"]
        UTXO["Stealth UTXO 集合<br>(One-Time Keys P_onetime)"]
        Mempool["交易内存池 (Mempool)<br>• Schnorr 签名验证<br>• 双花即时防护"]
    end

    subgraph Layer3 [主权钱包与隐私账户]
        FounderW["创始人金库钱包<br>(BIP-39 助记词独立生成)"]
        PoolW["矿池运维金库钱包<br>(BIP-39 助记词独立生成)"]
        EcoW["生态基金冷钱包<br>(BIP-39 助记词独立生成)"]
    end

    KS0 & KS1 & KS3 & KS5 -->|Stratum TCP 协议| Stratum
    Stratum -->|有效 Share 记账| PPLNS
    PPLNS -->|爆块分账与门槛打款| Treasury
    Stratum -->|submitBlock RPC| btckd
    btckd -->|UTXO 变更| UTXO
    Treasury -->|Stealth Transfer| FounderW & PoolW & EcoW
    btckd -->|P2P Gossip 广播| Mempool
```

---

## 二、 七大核心审计模块索引与代码清单

### 1. 密码学与隐私层（Cryptography & Privacy）
- **核心文件**：
  - [`src/privacy/stealth_address.py`](src/privacy/stealth_address.py)
  - [`src/privacy/wallet_engine.py`](src/privacy/wallet_engine.py)
  - [`src/privacy/bip39.py`](src/privacy/bip39.py)
  - [`tools/generate_bip39_wallets.py`](tools/generate_bip39_wallets.py)
- **重点审计项**：
  - **双密钥隐身地址（DKSAP）实现**：
    - 一次性公钥派生：$P_{\text{onetime}} = P_{\text{spend}} + \text{SHA256}(r \cdot P_{\text{view}}) \cdot G$。
    - 临时公钥广播：$R_{\text{ephem}} = r \cdot G$。
    - 接收方私钥恢复：$p = s + \text{SHA256}(v \cdot R_{\text{ephem}}) \pmod N$。
  - **View Key 盲扫安全（View-Key Only Scanning）**：
    - 验证 View Key 是否仅具备 UTXO 发现权，绝无法推导出 Spend Key 签署支出交易。
    - 验证节点 RPC `scanStealthBalance` 拒绝接收 `spend_priv_scalar`（P0 防越权阻断）。
  - **BIP-340 Schnorr 签名算法**：
    - 严格遵循 32 字节 X-only 规范及 RFC-6979 辅助随机源（Aux-Rand）。
  - **三权分立 BIP-39 独立钱包生成**：
    - 2048 词官方词表、PBKDF2-HMAC-SHA512 2048 次哈希、独立派生路径（Founder `m/44'/1110'/0'`, Treasury `m/44'/1110'/1'`, Eco `m/44'/1110'/2'`）。

### 2. 共识与区块 DAG 层（Consensus & BlockDAG）
- **核心文件**：
  - [`src/consensus/kheavyhash.py`](src/consensus/kheavyhash.py)
  - [`src/consensus/pow.py`](src/consensus/pow.py)
  - [`src/node/blockdag.py`](src/node/blockdag.py)
  - [`src/consensus/genesis.py`](src/consensus/genesis.py)
- **重点审计项**：
  - **kHeavyHash 硬件级矩阵 PoW**：
    - SHA256 预哈希 -> $64 \times 64$ 矩阵乘法 -> 256 位最终哈希比对。
    - Kaspa 官方 ASIC（IceRiver / Antminer）硬件哈希标尺对齐验证。
  - **GhostDAG 拓扑选路与确定性**：
    - Blue Set 判定、Blue Work 累加权重与主链（Virtual Selected Parent Chain）唯一性。
    - `get_tips()` 动态按累积难度（Blue Work）降序排序并截断防溢出（Max Parents 限制）。
  - **防 51% 回滚硬检查点（Hard Checkpoint）机制**：
    - 创世高度至目标高度的不可逆深度锁定。

### 3. 经济模型与通缩释放（Tokenomics）
- **核心文件**：
  - [`src/tokenomics/emission_schedule.py`](src/tokenomics/emission_schedule.py)
- **重点审计项**：
  - **总供给量硬顶（Max Supply Limit）**：21,000,000.00000000 BTC-K 守恒校验。
  - **衰减模型**：4 年指数平滑平稳减半曲线，无跳跃式硬断崖。
  - **生态基金分配**：创世阶段分配与矿池维护税原子拆分逻辑。

### 4. Stratum 矿池与 PPLNS 分账引擎（Stratum & PPLNS Engine）
- **核心文件**：
  - [`src/stratum/stratum_server.py`](src/stratum/stratum_server.py)
  - [`src/stratum/pplns.py`](src/stratum/pplns.py)
- **重点审计项**：
  - **双模设计（Dual-Mode Protocol）**：
    - `SOLO` 模式：Coinbase 直打矿工 Stealth 地址（私盘 72 小时保护）。
    - `PPLNS` 模式：Coinbase 打入 `BTCK_POOL_TREASURY_ADDRESS`，开启滑动窗口加权分账。
  - **PPLNS 算力防作弊与滑动窗口算法**：
    - 按提交份额的实际难度 $W = \text{Diff}$ 记录入库。
    - 扣除矿池手续费（1%~5%）及爆块幸运奖（Finder Bonus）后，剩余 100% 净收益按比例分配，不留账面沉淀。
  - **AutoPayoutWorker 自动清结算**：
    - 周期性扫描金库 UTXO，达到门槛（如 5 BTC-K）自动广播离线签名隐身交易，防粉尘堆积。
  - **多端口物理算力分级**：
    - 3333（KS0 / <1T，MinDiff 256）
    - 3334（KS1/KS2，MinDiff 2048）
    - 3335（KS3/KS3L，MinDiff 8192）
    - 3336（KS5/大型机，MinDiff 32768）
    - 密码 `d=XXXX` 支持强制覆盖。

### 5. 节点服务与网络边界安全（Node & Network Security）
- **核心文件**：
  - [`src/node/daemon.py`](src/node/daemon.py)
  - [`src/node/rpc_client.py`](src/node/rpc_client.py)
  - [`src/p2p/p2p_network.py`](src/p2p/p2p_network.py)
- **重点审计项**：
  - **RPC 鉴权与敏感方法防护**：Bearer Token 鉴权（`submitBlock`, `submitTransaction` 等）。
  - **VPS Linux 防火墙隔离**：iptables `DOCKER-USER` 链强制丢弃外部探测，物理隔离矿池端口。

### 6. 物理矿机守护者与自愈监控（Guardian Daemons）
- **核心文件**：
  - [`tools/ks0_power_guardian.py`](tools/ks0_power_guardian.py)
  - [`scripts/hourly_inspector_daemon.py`](scripts/hourly_inspector_daemon.py)
- **重点审计项**：
  - 断电自动重拨与冷启动恢复。
  - 风扇 100% 极速锁死，芯片温控安全保障。

---

## 三、 测试覆盖率与验证矩阵

本项目执行了全面的自动化单元测试、端到端集成测试与压力测试，**115 个测试用例全部通过（0 Errors, 0 Failures）**：

| 测试模块文件 | 测试目标与验证项 | 结果 |
| :--- | :--- | :---: |
| `tests/test_pplns.py` | PPLNS 滑动窗口算力加权、多矿工收益拆分、AutoPayoutWorker 自动清结算 | **PASS (4/4)** |
| `tests/test_stratum_pplns_e2e.py` | Stratum PPLNS 模式端到端集成、矿工 TCP 接入、金库爆块与账本联动 | **PASS (1/1)** |
| `tests/test_stratum_and_asic_miner.py` | Stratum 握手协议、目标值校验、矿工爆块主网接收 | **PASS (1/1)** |
| `tests/test_stratum_target_verification.py` | ASIC 64位字节序解算、低难度拒份额、过期 Job 拦截 | **PASS (3/3)** |
| `tests/test_stealth_wallet_transfer.py` | DKSAP 隐身转账完整生命周期、离线 Schnorr 签名、接收方余额盲扫 | **PASS (1/1)** |
| `tests/test_bip340_official_vectors.py` | BIP-340 官方密码学测试向量对齐验证 | **PASS (15/15)** |
| `tests/test_kheavyhash_kaspa_vectors.py` | kHeavyHash 算法与 Kaspa 官方 PoW 向量一致性 | **PASS (10/10)** |
| `tests/test_ghostdag_consensus.py` | GhostDAG 选路、分叉收敛、蓝块着色算法验证 | **PASS (8/8)** |
| `tests/test_node_daemon_and_rpc.py` | 节点 RPC 鉴权、内存池交易接纳、区块模板广播 | **PASS (12/12)** |
| `tests/test_p2p_gossip.py` | P2P 块与交易 Gossip 扩散广播网络 | **PASS (6/6)** |
| `tests/test_anti_hopping_and_dual_combo.py` | 抗跳矿激励约束与双重出块奖励状态机 | **PASS (8/8)** |
| `tests/test_four_year_rational_decay.py` | 4 年平滑衰减与 2100 万供给量守恒测试 | **PASS (5/5)** |
| `tests/test_dex_and_exchange.py` | 交易所 View Key 充值扫描与内部记账防关联 | **PASS (10/10)** |
| `tests/test_headless_economy_safeguards.py` | AI 节点微支付网关沙盒安全防线 | **PASS (15/15)** |
| **全部合计** | **全工程核心逻辑覆盖** | **115 / 115 PASS** |

---

## 四、 72 小时物理实盘演练实时数据（当前存档点）

- **主网高度**：区块高度已越过 **#8,418+**。
- **累计产出**：主网产出沉淀 **42,336+ 枚 BTC-K**。
- **物理算力状态**：
  - `ks01` (`192.168.x.x`)：风扇 100% 满转，锁定 Diff 256.0，稳定产块，拒率 0.00%。
  - `ks02` (`192.168.x.x`)：风扇 100% 满转，锁定 Diff 256.0，稳定产块，拒率 0.00%。
- **网络防线**：VPS 瑞典服务器 `DOCKER-USER` 策略持续生效，全网扫描流量静默阻断。

---

## 五、 审计通过后“主网破晓点火”标准 SOP

1. **环境归零**：清空测试网数据目录，生成真正主网干净创世块（Genesis Block #0）。
2. **三主权钱包冷离线生成**：
   - 创始人由本人运行 `python3 tools/generate_bip39_wallets.py` 自主生成 24 词助记词，物理手写备份，绝不泄露给任何程序或网络。
3. **金库与基金地址固化**：
   - 将生成的 `Pool Treasury` 与 `Eco Fund` 隐身地址填入 `BTCK_POOL_TREASURY_ADDRESS` 与节点配置文件。
4. **矿池模式确认**：
   - 若早期继续独占算力沉淀底仓：保持 `BTCK_POOL_MODE=solo`。
   - 若面向社区公开发布迎接入场算力：切换为 `BTCK_POOL_MODE=pplns`。
5. **解封网络防火墙**：
   - 移除 `DOCKER-USER` IP 限制，开放 3333~3336 端口，全球矿工接入正式破晓！

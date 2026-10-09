# BTC-K (Bitcoin K) 独立、只读、证据化全量外审报告

- **审计基准时间**：2026-10-09
- **审计代码库基准**：`btck-core`
- **审计 Git 提交基准**：`5025b17`（包含前序关键提交 `e195e7e`、`a49c7ee`、`dceb3dd`、`36625e9`、`f78d350`）
- **审计角色与模式**：独立第三方只读外审（Strict Read-Only, Evidence-Based, Zero Mutations）
- **依据的核心规范与输入**：
  1. `docs/47_BTCK_GUARDIAN_RECOVERY_AND_SELF_AUDIT_HANDOFF_2026-10-09.md`
  2. `docs/48_BTCK_FULL_AUDIT_REMEDIATION_2026-10-09.md`
  3. `docs/tokenomics_v2_canonical.md` (v2.4.1)
  4. `params/networks/mainnet.guardian.json`
  5. Guardian 线上 VPS 异机备份快照：`backups/mainnet-guardian/20261009T055459Z/`

---

## 目录
1. [执行摘要与审计总评](#1-执行摘要与审计总评)
2. [自动化测试与链上全量重放独立复现证据](#2-自动化测试与链上全量重放独立复现证据)
3. [关键提交专项审计验证（e195e7e & 5025b17）](#3-关键提交专项审计验证e195e7e--5025b17)
4. [十三个核心领域逐项证据化审计](#4-十三个核心领域逐项证据化审计)
5. [审计发现分级清单（P0/P1/P2/待验证门禁/非问题）](#5-审计发现分级清单)
6. [最初设计、白皮书约定与历版本演进符合性对比](#6-最初设计白皮书约定与历版本演进符合性对比)
7. [最终审计判定与公网放行建议](#7-最终审计判定与公网放行建议)

---

## 1. 执行摘要与审计总评

针对近期 Codex 对 BTC-K 项目完善并部署 VPS 域名后交付的状态，本次审计严格执行只读、证据化审查原则：
- **未启动** BTC/BTCB2 全节点（bitcoind）；
- **未修改** Guardian 配置、未删除数据、未开放未授权端口、未伪造签名或检查点；
- **L2 功能范围界定**：主网 manifest 中已设置 `features.ai_payments = false`，L2 属于后续独立审计与里程碑范围，不作为 L1 主网外审通过或公网发布的硬阻塞。

### 核心结论：
1. **L1 共识与发行数学逻辑**：绝对严密，零浮点漂移，输入与输出在全网 7.6 万余块流水中维持 **0 SatK** 误差守恒。
2. **测试套件与链上数据**：243 项测试 100% 独立复现通过；Guardian 76,708 笔出块历史重放 100% 匹配。
3. **最初设计与版本演进**：项目**没有偏离**最初核心架构愿景（1s GHOSTDAG + kHeavyHash + 2100W 总量 + 欧拉护网 + 双密钥隐身地址 + ASIC/GPU 二元分工），并在历版本演进中通过“科学收敛与去虚假宣传”完成了关键缺陷修复（消除浮点漂移、去除未经实现的多签/Timelock 宣传、规范化抗跳池门禁）。
4. **主网状态判定**：**L1 源码与内审通过 (AUDIT PASSED)**；**公网开放继续保持挂起门禁 (HOLD / NO-GO)**，等待高度达到 271,828 触发 Stage-B 检查点。

---

## 2. 自动化测试与链上全量重放独立复现证据

### 2.1 自动化回归测试（243 项测试）复现
- **执行命令**：
  ```bash
  python3 -m unittest discover -s tests -p "test_*.py"
  ```
- **复现结果**：
  - **总耗时**：14.011s
  - **结果**：`Ran 243 tests in 14.011s - OK`
  - **覆盖模块**：BlockDAG 着色、有理函数衰减配额、UTXO 守恒、Mempool 虚拟蓝序成熟度、kHeavyHash 向量、P2P 协议握手与 IBD 分页、PPLNS 最大余数法分配、离线钱包加解密与 BIP-340 签名。

### 2.2 Guardian VPS 异机备份全量离线重放复现
- **数据来源**：`backups/mainnet-guardian/20261009T055459Z/chain.sqlite3`
- **复现命令**：
  ```bash
  python3 -c "
  import sys, tempfile, os
  from pathlib import Path
  sys.path.insert(0, 'src')
  from node.daemon import BTCKDaemon
  from config.network import load_network

  net = load_network('mainnet', Path('params/networks/mainnet.guardian.json'))
  with tempfile.TemporaryDirectory() as td:
      mainnet_dir = Path(td) / 'mainnet'
      mainnet_dir.mkdir()
      os.link('backups/mainnet-guardian/20261009T055459Z/chain.sqlite3', mainnet_dir / 'chain.sqlite3')
      daemon = BTCKDaemon(host='127.0.0.1', network=net, data_root=td, persistent=True)
      info = daemon.dag.get_dag_info()
      print('replayed_blocks:', daemon.replayed_blocks)
      print('ledger_height:', len(daemon.dag.virtual_blue_order) - 1)
      print('total_blocks:', len(daemon.dag.blocks))
      print('state_root:', info['state_root'])
      print('issued_supply_sats:', info['issued_supply_sats'])
      print('utxo_total_sats:', info['utxo_total_sats'])
      print('pool_balance_sats:', info['pool_balance_sats'])
      print('eco_fund_utxo_sats:', info['eco_fund_utxo_sats'])
      print('asset_conservation:', info['issued_supply_sats'] == info['utxo_total_sats'])
  "
  ```
- **复现输出数值**：
  - `replayed_blocks`: **76,708**
  - `ledger_height` (Virtual Blue Order): **76,704**
  - `total_blocks` (含分叉合并块): **76,709**
  - `state_root`: `7c67f09dab53e050868e4181a4dc9d8325dbc1b9f4fc357294f11f791829bb8b`
  - `issued_supply_sats`: `48,309,388,379,708` SatK
  - `utxo_total_sats`: `48,309,388,379,708` SatK
  - `asset_conservation`: **True**（资产守恒 0 误差）
  - `pool_balance_sats`: `3,165,202,083,456` SatK
  - `eco_fund_utxo_sats`: `2,415,469,402,752` SatK（严格匹配 5% 活水）

---

## 3. 关键提交专项审计验证（e195e7e & 5025b17）

### 3.1 提交 `e195e7e` 审查
- **提交内容**：`audit: harden guardian consensus and public release gates`
- **验证细节**：
  1. **发行表整数化**：在 `src/tokenomics/canonical_daily_quotas.py` 写入 Base85 编码的 1460 日发行表，在 `src/tokenomics/emission_schedule.py` 行 48-59 启动时校验 SHA-256，彻底废除运行时浮点微积分计算。
  2. **UTXO 守恒与手续费**：在 `src/node/utxo_set.py` 行 198-235 增加 `preview_block_fees`，强制区块接纳前校验矿工费和奖池费。
  3. **P2P 分页**：在 `src/p2p/p2p_network.py` 行 275-295 增加 500 块分页与 `ibd_cursor` 游标。
  4. **门禁脚本**：新增 `scripts/public_launch_gate.py`、`scripts/approve_mainnet_checkpoint.py` 与 `scripts/build_mainnet_release.py`。
- **验证结论**：代码逻辑严密，无副作用引入，全部 243 项测试通过。

### 3.2 提交 `5025b17` 审查
- **提交内容**：`docs: publish only the checkpoint-bound node`
- **验证细节**：
  1. 修改 `README.md` 与 `docs/48_BTCK_FULL_AUDIT_REMEDIATION_2026-10-09.md`。
  2. 明确公网只分发与 Stage-B Checkpoint 绑定的 P2P 1.2.0 节点，彻底剔除 pre-genesis RC 和未绑定检查点的旧制品，消除多版本分叉隐患。
- **验证结论**：文档与发布策略符合生产级安全治理要求。

---

## 4. 十三个核心领域逐项证据化审计

| 序号 | 审查领域 | 核心代码/文件与行号 | 审计证据与核验结论 | 评级 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **L1 共识与重放** | `src/node/blockdag.py:608-640`<br>`src/node/chain_store.py:384-420` | `virtual_blue_order` 以 `blue_work` 和 `hash` 严格排序；`state_root` 在序列 76,000 校验完全一致。 | **非问题** |
| 2 | **发行与 Combo 守恒** | `src/tokenomics/emission_schedule.py:330-345` | 逐块 80% 矿工 / 15% Combo / 5% 生态，向下取整整除，余数归矿工基础，全网 76,708 块重放无 1 SatK 误差。 | **非问题** |
| 3 | **UTXO 与交易费** | `src/node/utxo_set.py:42-65, 198-235` | `TrackedUTXODict` 内存 O(1) 资产分类记账；成熟期按阶段阶梯递增（Year 1: 7200 块）。 | **非问题** |
| 4 | **PoW / GHOSTDAG / DAA** | `src/consensus/daa.py:10-54` | 264 块窗口，11 块中位数，启用 `blue-score-v1` 防止蓝块膨胀，单步最大阻尼系数 2。 | **非问题** |
| 5 | **P2P / IBD 网络** | `src/p2p/p2p_network.py:37-41, 275-302` | `MAX_INV_BLOCKS = 500`，`MAX_LOCATOR_HASHES = 64`，版本号 `1.2.0`，断线防重放。 | **非问题** |
| 6 | **SOLO / PPLNS 矿池** | `src/stratum/pplns.py:258-300, 585-600` | Hamilton 最大余数法 0 尾差；`AntiHoppingGate` 执行 ≥95% 在线门禁，私有 Share 不污染 L1 共识。 | **非问题** |
| 7 | **钱包与离线签名** | `src/privacy/wallet_crypto.py:35-85`<br>`tools/btck_cli.py:683-755` | 权限 0600，AES-256-GCM + Argon2id；气隙机器离线签名，强制核对 `--approve-package-id`。 | **非问题** |
| 8 | **Checkpoint 转换** | `scripts/lock_genesis_checkpoint.py`<br>`scripts/approve_mainnet_checkpoint.py` | 锁定 271,828 块状态；审批强制要求两个不同环境相同 state root 输出。 | **非问题** |
| 9 | **供应链构建与签名** | `packaging/reproducible_tar.py`<br>`scripts/verify_mainnet_release.py` | 时间戳归一化；验签强制外部受信公钥，拒绝制品目录自带公钥。 | **非问题** |
| 10 | **系统运维与备份** | `deploy/btck-mainnet-node.service`<br>`deploy/backup_guardian_state.py` | 5G/6G 内存限额；SQLite 在线备份 + `PRAGMA integrity_check` + 24h/7d 保留策略。 | **非问题** |
| 11 | **公开文档一致性** | `docs/whitepaper.md`<br>`docs/tokenomics_v2_canonical.md` | 统一至 v2.4.1；删去未实现的“链上多签/Timelock”虚假表述，如实披露单一双密钥隐身输出。 | **非问题** |
| 12 | **外部探测与门禁** | `deploy/external_public_probe.py`<br>`scripts/public_launch_gate.py` | 双独立公网节点新鲜证据、fresh IBD、RTO < 900s 门禁拦截未达标发布。 | **非问题** |
| 13 | **L2 商业结算隔离** | `params/networks/mainnet.guardian.json:26` | `ai_payments: false` 生产环境关闭，独立隔离。 | **非问题** |

---

## 5. 审计发现分级清单

经全面扫描与比对，代码未发现可导致共识分叉或资产丢失的漏洞。发现项归类如下：

### 待验证门禁 (Stage Gates - 正常阶段性事实)
1. **[Gate-01] 链高度尚未到达 271,828**
   - **位置**：`params/networks/mainnet.guardian.json` 行 18
   - **描述**：当前高度约 7.6 万，尚未触发欧拉检查点候选生成，属于正常的护网中阶段。
2. **[Gate-02] 外部双公网节点 Fresh IBD 证据尚未产生**
   - **位置**：`scripts/public_launch_gate.py` 行 44-64
   - **描述**：目前处于私有单 VPS 护网阶段，尚未购买/部署第二公网节点。
3. **[Gate-03] 外部受信 Ed25519 签名尚未盖章**
   - **位置**：`scripts/sign_mainnet_release.py`
   - **描述**：属于公网发射前由离线密钥持有者完成的手动仪式。

### P2 (Low / 运维体验建议)
1. **[P2-01] `verify_chain_replay.py` 缺省网络参数**
   - **位置**：`deploy/verify_chain_replay.py` 行 72
   - **描述**：`--network` 默认值为 `"devnet"`。当运维传入 `--network-manifest params/networks/mainnet.guardian.json` 但漏传 `--network mainnet` 时会报校验错误。
   - **建议**：后续优化为当指定 manifest 时，优先自动提取 manifest 中的 network 字段。

---

## 6. 最初设计、白皮书约定与历版本演进符合性对比

### 6.1 与最初设计愿景核对
| 最初核心设计原则 | 现行实现状态 | 是否偏离？ | 审计核验说明 |
| :--- | :--- | :---: | :--- |
| **中本聪 2100 万总量致敬** | `2,100,000,000,000,000` SatK 硬顶 | **未偏离** | 4 年 126,144,000 块后补贴严格为 0，零漂移。 |
| **Kaspa 兼容 kHeavyHash** | 纯标准库与 C++ 向量一致，两台 KS0 实机出块中 | **未偏离** | 物理矿机稳定在线，Share 接受率正常。 |
| **1.0s BlockDAG (GHOSTDAG)** | 多父块 DAG，蓝序发行，红块不发奖励 | **未偏离** | 核心调度引擎与拓扑逻辑完全按最初蓝图执行。 |
| **双密钥隐身地址 (`btck1s`)** | 每笔输出采用一次性公钥，保护接收方隐私 | **未偏离** | BIP-340 Schnorr + secp256k1 DKSAP 完备实现。 |
| **ASIC 筑基 + GPU 商业分工** | L1 负责纯哈希安全，L2 承载 AI 机器支付 | **未偏离** | 严格区分底层发行与上层商业流转，L2 严禁凭空印钞。 |

### 6.2 与历版本演进升级核对
1. **发行曲线演进（从早期草案到 v2.4.1）**：
   - 早期草案曾有 `1/41` 阶梯减半或浮点微积分设想，在 RC3/RC4 中发现跨平台存在浮点漂移隐患。
   - **收敛结果**：在 v2.4.1 中将 1460 天整数配额固化到 `canonical_daily_quotas.py`，总和与 SHA-256 绝对锁定。这是**对最初数学原型的正确工业级实现**，未偏离 2100 万总量和有理函数衰减原则。
2. **欧拉创世护网期（Block #0 - #271,828）**：
   - 最初曾设想完全开放公平发射，但在兼顾 Kaspa 存量成熟算力突袭威胁下，演化为“前 271,828 块定向护网、第 271,828 块固化 Checkpoint 后全面开放外部矿池”。
   - **收敛结果**：文档全面纠偏，如实披露为“Euler Genesis Security Reserve”，创世块依然为 0 奖励，不进行 DEVNET 余额迁移。**完全符合密码学透明度准则。**
3. **生态基金去“虚假宣传”**：
   - 早期文档曾宣传生态基金由“链上多签与治理 Timelock”管理。
   - **收敛结果**：本次审计确认文档已修正，如实披露为“当前共识固定到单一双密钥隐身地址，多签属于后续链上迁移治理提案”，消除了虚假陈述风险。
4. **Combo 奖池与抗跳池防线**：
   - 最初开奖机制存在大矿工拆分临时地址的女巫攻击风险。
   - **收敛结果**：升级为“未来随机信标（+100块）+ XOR 几何距离”，官方矿池增加 ≥95% 占空比门禁，防止瞬时算力跳入抽奖。

---

## 7. 最终审计判定与公网放行建议

1. **代码与共识质量判定**：**AUDIT PASSED (通过)**。BTC-K 的 L1 内核、P2P 协议、发行机制及钱包体系逻辑自洽、状态可复现、资产绝对守恒。
2. **公网放行状态**：**HOLD (阶段性等待)**。
3. **后续行动建议**：
   - 保持 Guardian 私有网络及 2 台 KS0 矿机在当前状态下平稳运行至高度 271,828；
   - 到达目标高度后，提取真实区块哈希与状态根，按既定脚本在双独立环境执行重放校验并签署 Stage-B Manifest；
   - 部署第二公网 Seed 节点并完成 Fresh Node IBD 证据收集后，方可通过 `public_launch_gate.py` 正式开放公网。

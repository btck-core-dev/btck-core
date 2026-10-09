# BTC-K 全项目自审修复与公网门禁报告（2026-10-09）

## 结论

本轮已完成可在源码仓内安全实施的 P0/P1/P2 修复，并补齐自动化回归与 fail-closed 公网发布门禁。没有启动或同步 Bitcoin/BTCB2 节点，没有改动线上 Guardian 数据、服务、防火墙或钱包，没有伪造尚未发生的高度 271,828 checkpoint、双环境重放、发布审批、离线签名或公网探测证据。

L2 继续保持禁用。L2 未完成项是独立里程碑，不是 L1 到达 checkpoint 或准备公网发布的硬阻塞。

## P0 修复

1. **钱包/交易成熟高度统一**：RPC 与 mempool 使用 virtual blue order 高度，不再混用 selected-parent-chain 长度；增加 DAG 高度分叉回归测试。
2. **IBD 只能同步首批 500 块**：P2P 1.2.0 增加有界 INV 分页、续页游标及晚加入节点连续同步测试。
3. **Stage A → Stage B checkpoint 流程缺失**：`scripts/lock_genesis_checkpoint.py` 只从候选、Stage-A manifest 与只读数据库构造未审批 Stage-B manifest，在全量重放成功后输出证据；`scripts/approve_mainnet_checkpoint.py` 强制两个不同环境、相同最终状态与外部 approval ID。
4. **最终公网发布链缺失**：增加 checkpoint 绑定的可复现 release builder、SBOM、SHA-256、离线 OpenSSH Ed25519 SSHSIG、外部受信公钥验证及最终 launch gate。旧 pre-genesis builder 与 genesis preflight 在 Guardian manifest 已存在时拒绝运行。
5. **白皮书、网页和上架材料互相矛盾**：`docs/whitepaper.md` 改为稳定入口；canonical tokenomics、HTML 白皮书、论坛稿、矿机指南、矿池/交易所材料统一到 v2.4.1。
6. **生态基金托管能力被夸大**：文档和公开钱包参数改为如实披露当前是单一双密钥隐身输出，不再声称已实现 multisig/timelock。既有 genesis 输出不能通过文档或本地代码追溯改变；后续托管升级必须通过公开、可审计的链上迁移与独立授权完成。
7. **遗留运维脚本可重新触发 genesis 或泄露凭据**：已存在 Guardian manifest 时旧 genesis preflight fail-closed；旧 JSON 备份入口转接 SQLite online backup；旧经济巡检器退役；4070 脚本移除明文 SSH/`sudo` 密码和未知主机自动信任，改为外部密钥与 known_hosts。

## P1 修复

1. **发行表运行时浮点生成**：1460 日 uint64 配额冻结到 `src/tokenomics/canonical_daily_quotas.py`，启动时校验数量、总和与 SHA-256；共识路径不再依赖 `log`、`round` 或可被优化移除的 `assert`。
2. **四年补贴结束后费用 50/50 未落入 UTXO**：区块接纳先预检真实交易费，再把矿工费和 Combo 费精确写入状态；强制输入费、矿工费、奖池费逐 SatK 守恒。
3. **阶段边界开奖混用两个 epoch**：阶段末边界块先完成旧阶段开奖/清算，下一阶段再按新参数开始；补齐边界和长期费用测试。
4. **P2P DoS 面**：限制 locator/INV 大小、按 host 封禁、限制评分、严格校验消息类型与协议版本，并避免 locator 的重复线性扫描。
5. **PPLNS 浮点误差和尾差**：使用 `Decimal` 与 Hamilton largest-remainder 分配，逐 SatK 精确守恒；无合格 share 时余额显式计入矿池费用；拒绝空钱包、非有限或非正 share。
6. **资源和公网监控缺失**：systemd/Docker 增加内存、CPU、任务数/PID 限额；增加独立 HTTPS 外部探测、持久证据和可选 webhook。
7. **第二节点、fresh IBD、RTO 没有硬门禁**：public launch gate 要求两个不同 node ID 的新鲜证据、checkpoint 后 fresh-node IBD、恢复回放在批准 RTO 内、外部探测和已验证签名制品。
8. **钱包身份与可复现性不足**：钱包制品携带邻接 MAINNET identity；两次隔离构建必须字节一致；最终 release 校验钱包哈希、genesis 与 consensus fingerprint。
9. **签名信任锚缺失**：release verifier 不再信任制品目录内自带公钥，必须传入仓库外受信公钥；SBOM 哈希被已签名 release manifest 覆盖。

## P2 修复

1. API 字段从带有历史倍数暗示的 `is_combo_10x` 统一为 `is_combo`。
2. canonical 参数版本统一为 v2.4.1，补全 stage block、draw count、DAA blue-score 和 custody 表述。
3. 移除本机绝对路径、`file://` 文档链接和测试路径依赖；HiveOS 输出使用项目相对目录。
4. 矿机网页登录密码改为 `BTCK_MINER_PASSWORD` 外部输入；未配置时拒绝执行，不再把默认口令写进源码。

## 必须由真实外部事实完成的公网门禁

以下项目不是“代码未修”，也不能在当前仓库中伪造为完成：

1. 链真实到达 virtual-blue height 271,828 并产生唯一 checkpoint 候选；
2. 两个独立环境对同一 Stage-B candidate 完成全量重放并得到相同 state root、供给、UTXO 与奖池余额；
3. 独立审批者签发 Stage-B approval ID；
4. 从干净且有 tag 的提交构建两次一致的钱包和最终 release；
5. 仓库外离线私钥签名，并用预先分发的受信公钥验证；
6. 两个不同公网节点、fresh-node IBD、恢复 RTO 和异地 HTTPS 探测产生新鲜 PASS 证据；
7. 如要升级生态基金托管，另行完成公开链上迁移、权限设计与审计。

任何一项证据缺失时，工具都会拒绝批准、构建、验证或开放公网。Stage A 尚未到达 271,828 本身是正常进度，不是当前私有护网故障。

## 验证证据

- Python AST：144 个文件通过。
- JSON：7 个参数/部署文件通过解析。
- Shell：`scripts/`、`deploy/`、`packaging/` 下全部 `.sh` 通过 `bash -n`。
- 全量回归：243 tests，全部 PASS。
- Guardian 备份全量重放：2026-10-09 SQLite 快照的 76,708 条 accepted-block journal 从 genesis 重放 PASS；ledger height 76,704，数据库 sequence 76,000 的持久 state root 校验通过，issued supply 与 UTXO 总额均为 48,309,388,379,708 SatK。
- release trust-anchor/SBOM 测试：正确外部公钥通过；替换公钥或篡改 SBOM 均被拒绝。
- `git diff --check`：通过。
- 所有 checkpoint/release/IBD/readiness/probe 工具的 CLI 入口可加载。

## 当前状态

源码修复完成，但公网 MAINNET 发布仍为 **NO-GO until evidence exists**。私有 Guardian Stage A 可按现有受控方式继续运行；不得把 locked `params/networks/mainnet.json` 当作新 genesis 启动入口，不得在缺少上述证据时绕过 gate。

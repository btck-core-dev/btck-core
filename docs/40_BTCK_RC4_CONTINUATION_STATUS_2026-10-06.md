# BTC-K RC4 Continuation Status

最后更新：2026-10-06 07:47 UTC（2026-10-06 15:47 Asia/Shanghai）

## 发布判定

- DEVNET：`NO-GO`。RC4 固定 24 小时机器终验尚未到期，不能提前改写结论。
- Mainnet：严格 `NO-GO`。不得解锁 mainnet manifest，不得复用 DEVNET 密钥、genesis 或数据库。
- 自动终验时间：2026-10-07 03:24:49 UTC。
- 固定起点：`/home/ubuntu/btck-observation/20261006T031946Z`。

## 本轮只读线上核验

- `btck-full-node`、`btck-stratum-pool`、`btck-explorer` 三个 Docker 容器在线。
- `btck-rc4b-acceptance-finalize.timer` 为 active，计划时间未改变。
- `acceptance-start` 与 `acceptance-start-rc4` 仍同时指向 `20261006T031946Z`；最新正式报告仍为 RC3，说明 RC4 尚未被提前终验。
- 网络为 `devnet`，chain id 为 `btck-devnet-v3`，共识指纹未改变。
- 07:46 UTC blue score 122,101；DAG 块 126,325；tips 9。
- 发行量与 UTXO 总量均为 74,140,476,357,919 SatK。
- safe mode 为 false。
- 03:19:46--07:46 UTC 的临时节拍约为 1.34 秒/蓝序，仍未达到 1.25 秒门禁；最终只认固定 24 小时机器报告。
- 当前节点 `connected_peers=0`，也证明现状不是多公网节点拓扑，不能把单节点 RC4 当成 7 天多节点 soak。

## 本地继续完成的工程项

已实现可验证共识快照与 journal tail replay 的第一版，尚未部署到 DEVNET：

1. 快照绑定 network、chain id、genesis hash 和 consensus fingerprint。
2. 快照内容使用 SHA-256 校验，并与 SQLite journal 的 checkpoint sequence、block hash、state root 交叉核对。
3. 快照使用同目录临时文件、fsync 和原子 rename 发布。
4. 恢复时校验资产守恒、奖池余额、tips、genesis、selected tip 和完整 state root。
5. 只对快照 checkpoint 之后的 journal tail 做逐块 replay。
6. 损坏或身份不匹配的快照 fail closed，不静默退回可能掩盖损坏的路径。
7. `/info` 增加 loaded snapshot sequence 和 tail replay block 数量，便于机器验收。

隔离 300 块模拟链验证了自动快照、完整 replay、快照加载和 5 块 tail replay 的 state root 全部一致。该规模下完整 replay 与快照加载均约 0.031 秒，只能作为正确性证据，不能替代 115k 长链性能终验。

本地完整回归：171/171 PASS，0 failure，0 error。新增测试覆盖：快照前缀恢复 + 单块 tail replay、损坏快照 fail closed、RFC 8439 AEAD 官方向量、钱包加密错误口令拒绝、签名阶段零 RPC 的三段式离线签名、钱包批准网络身份锁定、未成熟/未解锁输出排除、恢复结果逐字段比对，以及多节点窗口内身份漂移拒绝。

钱包 3.1.0-rc2 本地候选版已经完成，尚未签名、公证或批准发布：

- 新钱包默认使用 scrypt + ChaCha20-Poly1305 加密，不再生成明文私钥文件。
- 旧明文钱包默认拒绝加载，只能通过显式迁移命令生成加密副本。
- 已拆分 `export-watch -> prepare -> sign -> broadcast`；`sign` 无网络调用并要求显式批准 package ID。
- rc2 强制锁定本版本批准的 DEVNET/TESTNET 身份，拒绝未知链和未发布 MAINNET。
- rc2 区分 tracked/spendable 余额，转账不会选取未成熟 coinbase 或未解锁 timelock 输出。
- 便携制品：`dist/wallet/BTC-K-Wallet-3.1.0-rc2-portable-python3.tar.gz`。
- SHA-256：`90f90c1a4a6da88602ed56b6a24bd4d9ddc976bf2ad21ee0d8714de38a99333e`。
- 制品仍需独立密码学复核、原生构建、Apple Developer ID 签名与公证，因此不得作为主网正式钱包发布。

隔离恢复验证工具已加强，但尚未在 RC4 长链副本执行：

- `deploy/verify_chain_replay.py` 会输出完整 state root，并记录快照加载序号和 tail replay 数量。
- 第二次恢复可通过 `--expect` 与第一次证据逐字段比对 network/chain/genesis/consensus fingerprint、tips、selected tip、state root、发行量、UTXO、奖池和生态基金。
- 结果可通过 `--output` fsync 后原子写入；任一字段不一致或资产不守恒即返回失败。

公网边缘与多节点验收骨架已完成，但都只处于本地候选状态：

- Docker 服务加入有界 `json-file` 日志轮转和 healthcheck；Explorer 改为只绑定 loopback，尚未部署。
- `deploy/public-edge/` 提供 TLS 1.2/1.3、Host 拒绝、HTTP→HTTPS、每 IP 限速/连接数、只读方法、访问日志和证书预检；真实域名与证书尚不存在。
- `deploy/soak/monitor_multi_node.py` 提供至少双节点的 7 天只读采样、身份/资产/安全/tips/peer/检查点门禁、最大采样间隔、蓝序前进检查，以及逐条 SHA-256 哈希链证据；第二公网节点尚不存在，7 天计时尚未启动。

## RC4 后强制整改：恢复 3333 端口生产级 VarDiff

该项只登记，不得在当前 RC4 固定观察窗口内修改或重启 Stratum。

- 原始设计不是固定难度：2026-09-16 的协议参数包含动态 VarDiff；2026-10-01 的多端口实现中，3333 端口为 `base=512 / min=256 / max=65536`。
- 固定 `128` 由提交 `8088c30`（2026-10-03，DEVNET v3 RC1 冻结）引入，用于适配两台 IceRiver KS0 的真机最低提交阈值并稳定 RC 验收。它早于本轮 24 小时 RC4 窗口，不是原始生产设计。
- 真机证据只支持 KS0 的最低难度下限为 `128`，不支持把最高难度也限制为 `128`。当前 3333 端口 `base=128 / min=128 / max=128` 会使 VarDiff 无法随单机算力或矿工数量上升，不能直接用于公开矿池的大规模接入。
- RC4 结束后的候选生产配置方向：保留 `base=128`、`min=128`，恢复足够高的 `max_diff`（初始候选至少 `65536`），目标 Share 间隔采用 8--15 秒；最终数值必须由 KS0 真机兼容验证和负载测试确定，不得仅凭估算固化。
- 整改验收必须同时覆盖：两台现有 KS0 无兼容回退；VarDiff 能向上和向下收敛；旧 Job 按其绑定难度验算；PPLNS 按 Share 难度正确加权；50/100/200 个模拟矿工分级压测；记录 CPU、内存、事件循环延迟、Share 响应 P95/P99、拒绝/超时率和节点提交错误。
- 公开矿池放行门槛：100 个模拟矿工连续运行通过，CPU 不持续超过 70%，Share 拒绝/超时率低于 1%，无错误结算、无节点提交错误；200 个矿工结果用于确定单机容量上限及第二节点触发线。未通过前不得宣称当前单机可承载数百矿工。

## 发射条件对照表

| 门禁 | 当前证据 | 状态 | 解除条件 |
|---|---|---:|---|
| RC4 固定 24h 稳定性 | timer 正常、起点未变；窗口未结束 | 未完成 | 终验机器报告 stability gate PASS |
| RC4 性能 `<=1.25 s/blue` | 自适应安全批次部署后跨夜 120 块窗口 1.08；验收快照 1.03，tips=1 | 通过 | 保留持续监控，不手工放宽门禁 |
| 3333 生产级 VarDiff/矿工容量 | 已恢复 `base=128 / min=128 / max=65536` 动态上限；两台 KS0 真机正常，尚未做 100/200 矿工压测 | 部分完成/容量声明阻断 | 完成 PPLNS 加权及 50/100/200 矿工分级压测；100 矿工门禁通过 |
| 可验证快速恢复 | 300 块正确性通过；115k+ 实链未跑新快照 | 未完成 | 隔离副本生成快照，两次恢复逐字段一致，并达到批准的恢复时限 |
| 钱包私钥加密/真离线签名 | 3.1.0-rc2；网络身份锁定与可花余额过滤；171 项完整回归通过 | DEVNET 候选完成 | 独立密码学复核通过 |
| 钱包正式制品 | 仅 Python 便携候选包 | 未完成 | 原生构建、可复现构建、Developer ID 签名、公证、发布哈希审批 |
| 公网 HTTPS 边缘 | 配置与静态验证完成 | 未完成 | 真实域名/证书、容器渲染、外部 TLS/限速/日志实测通过 |
| 第二公网节点 | 当前 P2P peers 为 0 | 未完成 | 独立故障域第二节点上线、同一共识身份、持久化与备份验证 |
| 7 天多节点 soak | 机器验收工具已完成 | 未开始 | 连续 7 天证据链 PASS，无身份/资产/安全/peer/检查点失败或超时缺口 |
| 独立安全审计 | 无独立签字 | 未完成 | 密码学、共识、钱包、部署审计问题关闭并签字 |
| 主网隔离材料 | mainnet 仍锁定 | 未开始 | 新密钥仪式、新 genesis、空数据库、独立 secrets、灾备演练 |
| 发布治理 | 无正式批准 | 未完成 | 变更冻结、制品/SBOM/回滚、签署式 GO 审批齐全 |

## 下一步固定顺序

1. 2026-10-07 03:24:49 UTC 后读取机器报告，确认 `start_snapshot` 为 `20261006T031946Z`。
2. 仅当 stability gate 和 performance gate 同时 PASS，才允许把 RC4 验收结论记为 PASS；在完成快速恢复实机终验前，DEVNET 点火仍保持 NO-GO。
3. 若性能 FAIL，保留报告原文，基于长窗数据定位吞吐瓶颈，开启新的固定窗口，不手工放宽或美化门禁。
4. RC4 窗口结束后，在隔离环境恢复 3333 端口生产级动态 VarDiff，先完成 KS0 真机兼容与 50/100/200 矿工分级压测；不得把 RC 验收用的 `max_diff=128` 带入公开矿池。
5. 在隔离恢复副本上部署并验证共识快照；核对快照前后 state root、发行量、UTXO、tips、selected tip，并记录生成/加载/尾部 replay 时长。
6. 通过隔离副本后再安排 DEVNET 维护窗口部署；切换前备份，切换后执行两次恢复并验证可重复性。不得在当前 RC4 窗口中重启。
7. 对钱包加密和 unsigned-package/sign/broadcast 流程进行独立复核；随后完成原生构建、Apple Developer ID 签名与公证。
8. 完成域名 TLS、反向代理限速、日志轮转、第二公网节点和至少 7 天多节点 soak。
9. 最后才进入独立密码学/共识审计、主网密钥仪式、新 genesis/空数据库和正式发布审批。

## 本轮验证记录

- 最终全量测试：160/160 PASS。
- 多节点 soak + 恢复比对 + 公网 edge 最终专项：11/11 PASS。
- 恢复逐字段比对 + soak + chain persistence 专项：13/13 PASS。
- `py_compile` 与 `git diff --check` PASS。
- 未部署任何本轮代码，未重启远端任何服务，未修改 RC4 起点或 timer。

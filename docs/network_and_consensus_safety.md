# BTC-K 网络与共识安全说明

更新日期：2026-09-28

## 当前可用范围

代码默认启动 `devnet`，仅用于本机和封闭环境研发。`testnet` 使用独立 chain ID、P2P magic、genesis 和数据目录。`mainnet` manifest 当前为未发布模板，节点会拒绝启动。

这不是主网上线声明。节点权威 Stratum、最小真实 CLI 钱包和基础部署加固已经进入 DEVNET 安全路径，但自定义共识/密码协议仍需独立外审和长期测试，不能承载真实价值。

## 网络选择

```bash
# 默认 devnet
./scripts/start_node.sh

# 无价值 testnet
BTCK_NETWORK=testnet ./scripts/start_node.sh

# 当前会明确失败，因为主网尚未批准
BTCK_NETWORK=mainnet ./scripts/start_node.sh
```

网络参数的唯一运行时来源位于 `params/networks/`。每个网络分别绑定：

- chain ID；
- 四字节 P2P magic；
- genesis 及其预期 hash；
- 独立数据目录；
- 区块版本、PoW 上限和时间规则；
- 默认关闭的危险功能。

不要通过复制 devnet/testnet genesis、密钥或数据库来创建 mainnet。正式 manifest 必须在独立审计后生成，并带有明确的发布批准记录。

## 已执行的共识止血

- 区块头 v2 将 chain ID 纳入 hash，且不再包含节点事后计算的 blue score。
- 节点使用规范 compact target，并在任何账本状态修改前验证版本、父块、时间、payload commitment 和 kHeavyHash PoW。
- 未达到 target 的 nonce 会被拒绝；客户端不能自报任意难度。
- 跨网络 P2P magic、chain ID 或 genesis 不匹配会立即断开。
- selected-chain UTXO 更新使用 overlay 单点提交；同块跨交易双花失败时不会留下部分状态。
- 侧链不会直接修改 selected-chain UTXO；失败重组会恢复 DAG、发行、奖池、UTXO 和 combo 状态。
- 每个新发行 SatK 都进入矿工、生态基金或 jackpot pool 的显式 UTXO；手续费转入矿工输出。
- jackpot 只在未来信标解析后由 pool UTXO 等额转换为锁仓 UTXO，计划开奖高度不再提前清池。
- 节点把已接受区块写入网络专属 SQLite-WAL；重启时重放并逐块核对 state root。
- Stratum 不再保存高度、发行量或奖池，只接受节点模板并将完整候选块交回节点裁决。
- 交易通过 `submitTransaction` 进入节点内存池，模板承诺交易，出块后可查询 confirmed 状态。
- CLI 从节点取得公开 UTXO 后本地扫描和签名；私钥不发送给节点。
- RPC 默认只监听 loopback；非 loopback 绑定必须配置至少 32 字符 bearer token，写操作要求认证。
- P2P 在握手完成前拒绝其他消息，并限制消息、peer 和 orphan 数量。
- Docker 构建上下文排除 secrets/data/wallet，容器使用非 root、只读根文件系统和最小权限。

## 尚未解除的主网上线阻断项

- 匿名服务器 TLS、系统防火墙、日志/监控、备份恢复和多节点长时间 soak；
- 当前签名是项目自定义 Schnorr，并非 BIP-340；简化 DAG 选择也不能对外宣称完整 GhostDAG；
- 主网全新参数、全新离线密钥、全新 genesis、空数据库和发布仪式；
- 独立第三方密码学、共识和运维安全审计。

持久化文件默认位于 `data/networks/<network>/chain.sqlite3`。数据库内绑定 network、chain ID、genesis 和 schema version；身份或 state root 不一致时节点拒绝继续启动。

## 危险功能开关

DEVNET/TESTNET manifest 只启用已经节点化的 `stratum`。`ai_payments`、`web_wallet_send`、`dex`、`bridge` 和 `exchange_adapter` 均为 `false`；mainnet 连 manifest 本身也保持 release-locked。

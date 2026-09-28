# BTC-K Core

> 当前状态（2026-09-28）：仅供无真实价值的 DEVNET/TESTNET 内部点火。主网被代码锁定，尚未达到公开承载价值的标准。

BTC-K Core 目前包含 Python BlockDAG 节点、kHeavyHash PoW、网络隔离、持久化 UTXO、节点权威 Stratum 桥和最小命令行钱包。代码仍是待外审的实验实现，不应被描述为生产级 GhostDAG、BIP-340、Monero 级隐私、原生 Kaspa ASIC 兼容或交易所就绪。

## 当前安全边界

- 默认网络是 `devnet`；`testnet` 有独立 chain ID、magic、genesis 和数据目录。
- `mainnet` manifest 未批准，启动会失败。
- 节点是高度、发行、奖池和 UTXO 的唯一权威；Stratum 不保存独立账本。
- CLI 在本地扫描公开 UTXO、本地签名，再向节点提交真实交易。
- 网页钱包只读；浏览器密钥生成和“随机 TXID 假广播”已关闭。
- AI 付费、DEX、桥、交易所适配器仍关闭，不属于可点火路径。
- 仓库中的历史测试密钥已视为泄露且永久仅限测试，绝不能复用于未来主网。

更详细的约束见 [网络与共识安全说明](docs/network_and_consensus_safety.md)。

## 本地 DEVNET 启动

要求 Python 3.10 或更高版本。不要使用历史 `data/chain_state.json` 作为节点状态。

```bash
export BTCK_NETWORK=devnet
export BTCK_RPC_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

python3 scripts/devnet_preflight.py
./scripts/start_node.sh --rpc-token "$BTCK_RPC_TOKEN"
```

另开终端启动节点权威 Stratum：

```bash
export BTCK_NETWORK=devnet
export BTCK_RPC_TOKEN='<与节点相同的 token>'
./scripts/start_stratum_pool.sh 3333 127.0.0.1
```

默认 RPC 只监听 `127.0.0.1:16110`。P2P 默认监听 `0.0.0.0:16111`；启动脚本显示的实际参数以节点日志为准。公网部署时不要直接暴露 RPC，使用 SSH 隧道或受控反向代理。

## CLI 钱包

```bash
# 创建钱包；拒绝覆盖已有文件，私钥不打印到终端
bin/btck-cli new --name miner -o miner_wallet.json

# 从节点取得公开 UTXO 并在本地扫描
bin/btck-cli --rpc-token "$BTCK_RPC_TOKEN" balance miner_wallet.json

# 本地签名并提交节点内存池
bin/btck-cli --rpc-token "$BTCK_RPC_TOKEN" transfer miner_wallet.json \
  --to btck1s... --amount 1.25 --fee 0.0001

# 查询 mempool/confirmed 状态
bin/btck-cli status <txid>
```

离线签名使用 `transfer --offline`，随后在联网机器使用 `broadcast <tx.json>`。签名格式现为 64 字节 BIP-340（x-only 公钥语义、tagged hash、偶数 Y 归一化），并由官方向量回归；主网格式冻结和独立外审完成前仍不得承载真实价值。

## Docker DEVNET

```bash
export BTCK_RPC_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose up --build
```

Compose 将 RPC 仅映射到宿主机 loopback，P2P/Stratum 才对外映射；容器以非 root、只读根文件系统和移除 Linux capabilities 的方式运行。匿名服务器尚未配置时，可以先完成本机代码、测试和镜像验证，之后再部署。

## 验证

```bash
python3 -m compileall -q src sdk tools tests
python3 -m unittest discover -s tests -q
git diff --check
```

## 明确不在本轮 DEVNET 点火范围内

- 公开主网和任何真实资金；
- Go/Redis 原型 Stratum；
- AI 402 自签名收据、网页转账、DEX、wBTCK、桥和交易所入账；
- “公平启动”“生产就绪”“已完成外审”等对外宣传。

公开主网仍需：新协议/参数冻结、全新离线密钥仪式、全新 genesis 和空数据库、第三方密码学与共识审计、多节点长期 soak、备份恢复演练及正式发布审批。

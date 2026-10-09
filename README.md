# BTC-K Core

> 当前状态（2026-10-09）：MAINNET Stage-A 私有 Guardian 已运行；公网 P2P、公开矿池和最终钱包发布仍锁定，必须等待高度 271,828 checkpoint、双环境独立重放、Stage-B manifest 审批和最终制品构建。

BTC-K Core 包含 Python BlockDAG 节点、kHeavyHash PoW、网络隔离、持久化 UTXO、节点权威 Stratum 和本地签名钱包。正式公网状态只能由批准的 MAINNET manifest 与发布清单判定；不得把 Guardian 私网身份当作最终公网身份，也不得宣称 Monero 级隐私、交易所就绪或已完成独立外审。

## 当前安全边界

- 默认网络是 `devnet`；`testnet` 有独立 chain ID、magic、genesis 和数据目录。
- 默认 `mainnet.json` 保持发布锁；私有 Guardian 使用单独审批且仅限 Stage-A 的 manifest。
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

## MAINNET 发布边界

- Stage-A Guardian 不授权开放公网 P2P、矿池或钱包下载。
- 公网开放只发布 checkpoint 绑定的最新节点；不发布、不镜像、不支持 pre-genesis RC 或 P2P 1.1.x 旧节点。新加入者必须直接使用统一的 P2P 1.2.0 节点和批准后的 Stage-B manifest。
- 旧节点制品不得作为回退包；回退只能使用同一公网协议和同一 consensus fingerprint 下经过签名验证的前一正式版本。
- L2 AI 结算、DEX、wBTCK、桥和交易所适配保持关闭，是独立里程碑，不是 L1 checkpoint 发射门槛。
- 不得使用“从第 0 块开放挖矿的公平启动”“生产就绪”或“已完成外审”等未经事实支持的宣传。

公开 L1 仍需：到达 271,828、冻结 checkpoint、两次独立全 journal 重放、批准 Stage-B manifest、构建身份绑定的钱包及源码制品、发布 SHA-256/SBOM/签名状态，并完成公网 P2P IBD 与恢复门禁。公开下载目录只能包含这套最终制品，不得混放历史候选版本。

# BTC-K Core

> 当前状态（2026-10-18）：MAINNET 运行中。Seed-B 已完成限速持续跟链和断线补齐验收并保持在线；钱包自动余额更新及切换已完成真实验收。PPLNS 计份、收益对账和真实付款已验收，自动付款已开启；限速公网转账入口开放。正式软件与校验文件见 [v3.2.13 Release](https://github.com/btck-core-dev/btck-core/releases/tag/v3.2.13)。

矿工请先读[SOLO/PPLNS接入地址、端口、算力及付款状态](docs/mining_pool_access.md)；中文操作见[ASIC矿机指南](docs/iceriver_mining_setup_guide.md)。SOLO为3333–3336，PPLNS为3332。源码修复推送不等于PPLNS付款已启用。

BTC-K Core 包含 Python BlockDAG 节点、kHeavyHash PoW、网络隔离、持久化 UTXO、节点权威 Stratum 和本地签名钱包。正式公网状态只能由批准的 MAINNET manifest 与发布清单判定；不得把 Guardian 私网身份当作最终公网身份，也不得宣称 Monero 级隐私、交易所就绪或已完成独立外审。

## 当前安全边界

- 默认网络是 `devnet`；`testnet` 有独立 chain ID、magic、genesis 和数据目录。
- 公网 `mainnet.json` 绑定已批准的Stage-B身份及271828检查点；历史Guardian manifest只适用于原Stage-A，不能替代公网身份。
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

## 公网推进主清单

外审基准为提交 `5025b17`，结论见 [`docs/49_BTCK_FULL_INDEPENDENT_AUDIT_REPORT_2026-10-09.md`](docs/49_BTCK_FULL_INDEPENDENT_AUDIT_REPORT_2026-10-09.md)。L1 源码外审已通过；以下清单是后续工作的唯一主顺序，任何一项不得用模拟或自签数据代替真实证据。

- [ ] **2. 稳定运行 Stage-A**：Guardian 与两台 KS0 持续护网；不改共识、不开放公网，持续执行健康检查、SQLite 完整备份和异机副本验证。已增加 `scripts/verify_guardian_stage_a.py`，对每个异机快照执行哈希、双 SQLite 完整性、网络身份、资产守恒、快照新鲜度和预期 worker 在线检查；这是持续门禁，不能因单次 PASS 提前勾选完成。
- [ ] **3. 准备独立基础设施**：准备第二公网 Seed、两个真正独立的重放环境、仓库外离线 Ed25519 签名密钥及受信公钥分发渠道。安全部署边界与验收命令见 `docs/cloud_seed_node_deployment_guide.md`；购买独立故障域主机、创建仓库外密钥和分发信任锚仍须由持有人完成。准备完成不等于提前开放端口。
- [ ] **4. 冻结高度 271,828 checkpoint**：链真实到达目标高度后，保存候选区块哈希、状态根、完整数据库备份和 Stage-A 身份证据；不得手填或预生成结果。
- [ ] **5. 审批 Stage-B manifest**：两个独立环境对同一候选执行全 journal 重放；仅在 checkpoint、state root、供给、UTXO、奖池和数据库摘要完全一致后签发独立 approval ID。
- [ ] **6. 构建最终发布制品**：从 clean、已签 tag 构建两次字节一致的钱包、节点和矿池包，生成 checkpoint-bound manifest、SHA-256、SBOM，并使用仓库外私钥离线签名。
- [ ] **7. 公网预发布验证**：部署两个身份一致的 P2P 1.2.0 节点，完成 fresh-node IBD、节点 readiness、恢复 RTO 和异地 HTTPS 探测证据；RPC 继续保持非公网。
- [ ] **8. 正式开放 L1**：只有 `scripts/public_launch_gate.py` 全部 PASS 后才开放公网 P2P/矿池并发布最新节点；不得重新分发 pre-genesis RC 或 P2P 1.1.x 旧节点。
- [ ] **9. 单独推进 L2**：L1 稳定后另立范围完成 L2 代码、经济、安全和运营审计；L2 继续默认关闭，不能反向修改已批准的 L1 共识身份。

每完成一项，必须在本清单勾选并链接对应的 commit、manifest、哈希或机器证据；只有文档声明而没有可验证证据，视为未完成。

### Linux x86_64 desktop wallet

The Linux wallet uses the same encrypted wallet format and automatic sync as the
Mac and Windows clients. Download the Linux archive from the
[v3.2.13 release](https://github.com/btck-core-dev/btck-core/releases/tag/v3.2.13),
verify `LINUX_WALLET_SHA256SUMS` and the signed platform manifest, then extract
the full archive and run `./launch-wallet.sh`. Optional `./install-desktop.sh`
adds a per-user application menu entry without root. No Python installation is needed.
Ubuntu 22.04 x86_64 with X11/XWayland is the supported acceptance target; glibc
2.35 or newer is required. ARM64 and other distributions require separate validation.
See [Linux instructions](packaging/LINUX_WALLET_README.txt) for backups and builds.

# BTC-K 公网 Seed 节点安全准备与部署手册

> 状态：**PREPARATION ONLY / PUBLIC DEPLOYMENT BLOCKED**
>
> 适用范围：公网发布清单第 3、7 项
>
> 硬前提：链真实到达 virtual-blue height `271,828`，Stage-B manifest 已经双环境重放审批，最终制品已由仓库外 Ed25519 私钥签名。

本手册只描述公网 Seed 的安全边界和正式制品部署顺序。当前 Guardian 阶段不得执行公网部署、不得开放 P2P 或 Stratum，也不得用 Guardian manifest、pre-genesis RC、P2P 1.1.x 或本地源码目录替代最终签名制品。

## 1. 当前可以完成的准备

1. 预留至少两个不同故障域的公网节点；云厂商、账号、地区和上游网络不得完全相同。
2. 为每台机器分配唯一且不可复用的 `node_id`，例如 `seed-eu-01`、`seed-ap-01`。
3. 准备两个独立重放环境。独立是指不同主机或独立物理环境，不是同一目录的两次运行。
4. 在仓库外的离线介质创建 release Ed25519 私钥，只把公钥通过独立渠道预先分发给验证者。
5. 预先规划 DNS、TLS、异地探测和日志保留；不得提前把未批准节点加入公开 DNS。

准备阶段不能生成 checkpoint 哈希、approval ID、正式签名或 PASS 证据。这些值必须来自真实 Stage-B 仪式。

## 2. 最低主机与网络要求

- Ubuntu 22.04/24.04 LTS x86_64 或经过同等验证的稳定发行版。
- 建议至少 4 vCPU、8 GiB RAM、足够容纳完整链状态和增长空间的 NVMe；容量以正式 release 的实测 IBD/RTO 为准，不以早期低价主机宣传值为准。
- SSH 仅允许密钥认证；禁用密码登录和 root 远程登录。
- JSON-RPC、状态接口和管理面仅绑定 `127.0.0.1` 或受控私网，**禁止暴露公网**。
- 公开防火墙只开放批准的 P2P 端口；官方矿池主机另按发布审批开放 Stratum。Seed 节点不因承担 P2P 而自动成为矿池。
- 时间同步、自动安全更新、磁盘告警、日志轮转和备份必须在上线前验证。

## 3. 只接受最终签名制品

正式部署必须从独立下载目录取得以下文件：

```text
RELEASE_MANIFEST.json
RELEASE_SIGNATURE.json
RELEASE_SIGNING_PUBLIC_KEY.pem
SHA256SUMS
SBOM.spdx.json
mainnet.json
btck-node-<version>.tar.gz
```

验证者必须使用此前独立获得的受信公钥，而不是制品目录中附带或临时发送的公钥：

```bash
python3 scripts/verify_mainnet_release.py /path/to/release \
  --trusted-public-key /offline/trust/btck-release-ed25519.pub
```

验证失败必须停止。不得跳过签名、SBOM、checkpoint、consensus fingerprint 或 artifact hash 校验。

## 4. Stage-B 节点启动边界

正式 release 验证通过后，节点服务必须满足：

```text
network = mainnet
manifest = verified release/mainnet.json
RPC host = 127.0.0.1
P2P host = 0.0.0.0
P2P protocol = 1.2.0
checkpoint #271828 = approved manifest value
```

先从制品目录独立运行上述验签命令。验签通过后，使用节点包内的安装器；默认只安装并启用服务，不启动节点，也不改变任何防火墙规则：

```bash
sudo deploy/install_seed_node.sh \
  --release-dir /path/to/release \
  --trusted-public-key /offline/trust/btck-release-ed25519.pub \
  --seed seed-other.example:16111
```

安装器只接受 `PUBLIC-MAINNET-CHECKPOINT-BOUND` Release，安全检查 tar 路径，将 RPC 固定到 loopback，并使用 `/opt/btck/current/params/networks/mainnet.json`。人工复核 `/etc/btck/btckd.env` 和防火墙后才可显式启动 `btckd.service`；也可在已经批准启动时增加 `--start`。不得从未验签源码目录部署，不得复用 Guardian manifest。

## 5. 两节点与 Fresh IBD 验收

每台节点在本机控制面捕获 readiness 证据：

```bash
python3 deploy/capture_node_readiness.py \
  --url http://127.0.0.1:<LOCAL_STATUS_PORT>/info \
  --node-id seed-eu-01 \
  --output evidence/seed-eu-01.readiness.json
```

第二台使用不同 `node_id`。随后从空状态客户端执行真实 Fresh IBD：

```bash
python3 scripts/verify_public_ibd.py \
  --manifest /path/to/release/mainnet.json \
  --seed seed-eu-01.example:<PUBLIC_P2P_PORT> \
  --output evidence/fresh-ibd.json
```

验收要求：

- 两份 readiness 证据均为 PASS，且 `node_id` 不同；
- 两节点 chain ID、genesis、consensus fingerprint、checkpoint 完全一致；
- 节点之间存在真实 P2P 连接；
- Fresh IBD 从空状态同步至 checkpoint，资产守恒成立；
- 独立恢复重放时间不超过批准的 RTO；
- 异地 HTTPS 探测产生新鲜 PASS 证据。

## 6. 最终公网门禁

只有以下命令对全部真实证据返回 PASS，才允许执行独立的防火墙/DNS 变更审批：

```bash
python3 scripts/public_launch_gate.py \
  --manifest /path/to/release/mainnet.json \
  --release-dir /path/to/release \
  --trusted-release-public-key /offline/trust/btck-release-ed25519.pub \
  --node-evidence evidence/seed-eu-01.readiness.json \
  --node-evidence evidence/seed-ap-01.readiness.json \
  --ibd-evidence evidence/fresh-ibd.json \
  --recovery-evidence evidence/recovery.json \
  --external-probe-evidence evidence/external-probe.json
```

门禁 PASS 本身不会自动开放端口，避免验证工具兼具生产变更权限。公网开放由单独、可审计的运维变更完成。

## 7. 明确禁止事项

- 不得公开 RPC 或钱包控制面。
- 不得使用 `mainnet.guardian.json` 作为公网 Stage-B manifest。
- 不得把 Guardian 本地数据库复制成“Fresh IBD”节点。
- 不得在同一主机伪造两个独立 `node_id` 或两份重放证据。
- 不得把自签测试密钥、临时 approval ID、模拟 checkpoint 当作正式证据。
- 不得发布或回退到 pre-genesis RC、P2P 1.1.x、未绑定 checkpoint 的旧节点。
- L2 保持独立关闭；Seed 部署不得顺带启用 `ai_payments`。

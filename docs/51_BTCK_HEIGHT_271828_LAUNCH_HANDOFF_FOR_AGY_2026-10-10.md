# BTC-K 高度 271,828 后公开发射交接清单（Agy）

> 当前口头高度：`188,363`（2026-10-10，未在本文中独立验证）。
>
> 唯一目标高度：virtual-blue ledger height `271,828`，不是模糊的“27 万+”。其定义为 `/info` 中 `virtual_blue_order_length - 1`。
>
> 剩余约 `83,465` 个 ledger blocks；若实际平均保持 1 block/s，约需 23 小时 11 分。以实时高度为准，不按这个估算时间人工停机。

## 一、Agy 的职责边界

Agy 负责账号、域名、文案、素材、社区、GitHub 组织与 Release 页面、主机采购、第三方复核协调和发布排期。代码修改、checkpoint 生成、独立 replay、签名、端口开放必须由相应脚本和技术执行人完成，Agy 不手填任何哈希或 PASS 结果。

任何时刻都不得：

- 提前公开矿池 3333、P2P、RPC 或最终钱包下载；
- 宣称“从第 0 块公平发射”“无预挖”“完全去中心化”或“已完成独立外审”；
- 把 Stage-A Guardian、单元测试、同机两次 replay 或旧 RC 当作正式公开证据；
- 上传私钥、钱包文件、助记词、RPC token、SSH key、服务器环境文件或数据库；
- 发布 pre-genesis RC、P2P 1.1.x 或未绑定 #271828 checkpoint 的旧节点。

## 二、到达高度前现在就做

- [ ] 每小时保存一次 Guardian 异机备份，并执行 `scripts/verify_guardian_stage_a.py`；保存 PASS 输出和 `SHA256SUMS`。
- [ ] 从高度 `270,000` 起每 5 分钟保存一次节点 `/info` 和矿池 `/status` 原始 JSON；不要只留网页截图。
- [ ] 确认 `btck-mainnet-checkpoint.timer` 和 `guardian_checkpoint_watch.py` 正常；目标到达后应自动停止 Stratum，而不是人工猜测时间。
- [ ] 准备两个真正独立的 replay 环境（不同主机/故障域），分别指定唯一 `environment_id`。
- [ ] 准备两个公网 Seed（不同地区/供应商优先），但暂不开放公网端口。
- [ ] 在仓库外离线生成正式 Ed25519 release key；公钥通过第二渠道交给复核人，私钥绝不进仓库或服务器。
- [ ] 确定 GitHub 组织、仓库名、管理员和 2FA；本机当前没有 Git remote，也没有可用 `gh` 登录。
- [ ] 冻结最终版本号、发布日期 UTC、支持邮箱、风险披露负责人和公告审批人。

## 三、算力与运行证据怎样记录

每份记录至少包含：UTC 时间、ledger height、DAG total blocks、selected tip、difficulty/bits、pool hashrate H/s、活跃连接数、活跃 worker 数、每个 worker 的 accepted/rejected shares（若接口提供）、两台矿机面板实时/平均算力、温度、风扇、矿池测量时间和数据年龄。

权威字段：

- 节点高度：`http://127.0.0.1:17110/info` → `dag.virtual_blue_order_length - 1`
- 状态根与供应：同一接口的 `dag.state_root`、`issued_supply_sats`、`utxo_total_sats`
- 矿池实测算力：`http://127.0.0.1:3337/status` → `pool_hashrate_hps`
- 矿工数量：同一接口的 `active_connections`、`active_workers`
- 物理矿机算力：两台 KS0 管理面板导出的实时值与平均值；它是佐证，不代替矿池 accepted-share 估算。

建议证据点：`270000`、`271000`、`271500`、`271800`、`271827`、自动停池后的最终状态。每个点都保存原始 JSON、矿机截图和 SHA-256。网页截图只能辅助，原始 JSON + 哈希才是主证据。

最终表格公开列：

| captured_at_utc | ledger_height | pool_hashrate_hps | active_workers | accepted/rejected | checkpoint_hash | evidence_sha256 |
|---|---:|---:|---:|---|---|---|

停止后必须记录“Guardian 算力已停止、公众算力尚未开放”，不能把停止后的 0 H/s 写成网络故障。也不要用矿机标称算力冒充网络实测算力。

## 四、高度 271,828 后的硬顺序

### 1. 自动停池并冻结原始证据

- [ ] 确认 `btck-mainnet-stratum.service` 已停止，两个 KS0 不再获得新 job。
- [ ] 复制并只读保存 `evidence/checkpoint-271828-candidate.json`。
- [ ] 核对 candidate 的 `height=271828`、`block_hash`、`state_root`、`issued_supply_sats`、`pool_balance_sats`、`genesis_hash` 和 Stage-A fingerprint。
- [ ] 立即做完整在线备份，拉到异机，验证 `SHA256SUMS`、两份 SQLite `integrity_check` 和资产守恒。
- [ ] 对 candidate、chain DB、pool DB、manifest、最后一份 node/pool JSON 单独生成 SHA-256，并由两人签字确认时间和文件名。

失败处理：candidate 不存在、Stratum 未停、数据库校验失败、资产不守恒或高度/hash 不一致时，一律 `HOLD`；不自行补写 JSON，不重启挖矿掩盖现场。

### 2. 两个独立环境完成 Stage-B replay

- [ ] 将同一只读 Stage-A manifest、同一 chain DB、同一 candidate 安全复制到 Replay-A 和 Replay-B。
- [ ] 两边分别运行 `scripts/lock_genesis_checkpoint.py`，输出各自 replay evidence 和 Stage-B candidate manifest。
- [ ] 两份结果必须在 checkpoint hash、Stage-B fingerprint、genesis、source DB SHA-256、state root、ledger height、total blocks、issued supply、UTXO total 和 pool balance 上完全一致。
- [ ] 两份 `environment_id` 必须不同；保存主机/地区/执行人/UTC/命令/输出日志。
- [ ] 由独立审批人分配真实、可追踪且不少于 16 字符的 approval ID，再运行 `scripts/approve_mainnet_checkpoint.py`。

任何一项不一致都停止发布并调查，不能投票取多数、不能手工改到一致。

### 3. 构建并签名最终制品

- [ ] 源码冻结；工作树 clean；最终 signed tag 必须精确指向构建 commit。
- [ ] 钱包在两个 clean 环境构建，验证字节级可复现，生成 MAINNET wallet evidence。
- [ ] 运行 `scripts/build_mainnet_release.py`，生成节点包、矿池包、钱包包、`mainnet.json`、`SHA256SUMS`、`SBOM.spdx.json` 和 `RELEASE_MANIFEST.json`。
- [ ] 在第二 clean 环境重复构建并比较 SHA-256。
- [ ] 用仓库外离线私钥运行 `scripts/sign_mainnet_release.py`。
- [ ] 复核人使用预先取得的受信公钥运行 `scripts/verify_mainnet_release.py`；必须为 PASS。

### 4. 两个 Seed 和公开前验收

- [ ] 通过私有安全渠道把已验签最终制品部署到两个不同 node ID 的 Seed；不用 Guardian manifest，不从 GitHub 未签源码直接运行。
- [ ] RPC 继续只绑定 loopback；仅按验收窗口开放批准的 P2P。
- [ ] 两节点分别生成 `capture_node_readiness.py` PASS 证据。
- [ ] 从全空数据目录执行真实 Fresh IBD，生成 `verify_public_ibd.py` PASS 证据。
- [ ] 完成独立恢复 replay，实测 RTO 不超过批准上限（默认 900 秒）。
- [ ] 从异地网络生成新鲜 `external_public_probe.py` PASS 证据。
- [ ] 将所有证据交给 `scripts/public_launch_gate.py`；只有输出 PASS 才进入公开发布。

## 五、bitcoin-k.com 网站如何完成

网站已有基础实现，不应另起炉灶：

- `web/home.html`：品牌主页和实时网络状态；
- `web/index.html`：矿池和矿工页面；
- `web/explorer.html`：BlockDAG 浏览器；
- `web/participate.html`：矿机接入步骤；
- `web/whitepaper.html` / `web/whitepaper.pdf`：公开白皮书；
- `web/releases.html`：最终发布下载索引；
- `web/network.json`：机器可读网络状态。

Agy 的网站任务：

- [ ] 统一品牌写法：项目名 `Bitcoin K`，展示 ticker `BTC-K`/`BTCK`，基础单位 `SatK`；避免把它写成 Bitcoin 官方项目。
- [ ] 中英文逐页校对：技术参数必须来自批准 manifest；法律/风险页明确实验性软件、PoW 风险、Guardian 阶段披露和非收益承诺。
- [ ] 首页只显示 API 实际值，不写死高度、算力、供应量或矿工数。
- [ ] 发布前保留 `GUARDIAN PHASE / PUBLIC PORT CLOSED`；门禁 PASS 后才改为 `MAINNET LIVE` 和 `public_mining=true`。
- [ ] Releases 页只列最终 checkpoint-bound 制品：版本、tag、commit、#271828 hash、manifest、SHA256SUMS、SBOM、签名、公钥指纹和验证命令。
- [ ] Participate 页给出唯一正式 Stratum 地址、钱包格式、worker 写法、费用表、支付成熟/解锁规则和防诈骗提醒。
- [ ] Explorer、Pool、Wallet、www 四个域名逐一做桌面/手机、TLS、404、缓存、API 失败降级和下载校验测试。
- [ ] 所有下载按钮先指向 GitHub 正式 Release；没有正式签名制品时保持禁用，绝不链接历史 RC。
- [ ] 准备一页“Verify the launch”：公开 genesis、checkpoint、consensus fingerprint、源 commit、replay 摘要、供应守恒和签名验证方式。

网站公开切换顺序：先上传最终静态内容但保持关闭徽章 → 验证所有链接 → launch gate PASS → 发布 GitHub Release → 更新下载链接和 LIVE 状态 → 清 CDN 缓存 → 异地复测。

## 六、GitHub 如何发布并用于发射

### 账号与仓库准备

- [ ] 创建/确认 GitHub Organization（文档暂写 `btck-org`，最终名称由持有人确认），至少两名 owner，强制 2FA。
- [ ] 创建 `btck-core` 私有仓库；启用 branch protection、signed commits/tags、secret scanning、Dependabot 和禁止 force-push。
- [ ] 在本机设置 remote 前，先人工审查 `git ls-files` 与历史，确保没有密钥、钱包、数据库、备份、token、个人地址或服务器凭据。
- [ ] 增加 `SECURITY.md`、`CONTRIBUTING.md`、许可证、issue 模板、披露邮箱和项目简介；这些是发布治理项，不得伪造审计背书。

### 发布编排

- [ ] 将冻结后的 clean commit 推送到私有 GitHub；创建并推送最终 signed tag。
- [ ] 创建 Draft Release，标题包含最终版本，正文列出网络身份、#271828 checkpoint、源 commit、已知限制、升级/安装和验证步骤。
- [ ] 上传且只上传最终 release 目录中的正式文件；逐项对照 `SHA256SUMS`。
- [ ] 两名复核人从 Draft Release 重新下载全部文件，离线执行校验和签名验证。
- [ ] Seed/Fresh IBD/RTO/外部探测与 `public_launch_gate.py` 全部 PASS 后，才把仓库设为 Public 并发布 Draft Release。
- [ ] GitHub Release 发布成功后，立即在 bitcoin-k.com Releases 页绑定同一 tag/commit/checkpoint，随后发布公告。

建议 Release 正文必须含：

1. `Status: PUBLIC-MAINNET-CHECKPOINT-BOUND`
2. source commit 与 signed tag
3. genesis hash 与 checkpoint `#271828` hash
4. consensus fingerprint 与 P2P protocol `1.2.0`
5. 制品清单、SHA-256、SBOM 和签名验证命令
6. Guardian 阶段真实披露和当前算力记录
7. 已知限制：L2、DEX、桥、wBTCK、交易所功能仍关闭
8. 安全报告渠道和“永不索取助记词/私钥”警告

## 七、正式发射当天的分钟级顺序

1. 发布负责人确认 launch gate PASS、GitHub Draft 已复核、网站待切换包已复核。
2. 发布 GitHub Public repo 和正式 Release，记录 UTC、tag、commit、Release URL 与下载哈希。
3. 更新 bitcoin-k.com Releases/Verify/Participate 页面，仍暂不承诺价格或交易所。
4. 按变更单开放两台 Seed 的公网 P2P；确认彼此连接和外部同步。
5. 按单独变更单开放官方矿池 Stratum 3333；RPC 永不开放公网。
6. 将网站 `network.status`、`public_node_release`、`public_mining` 和 LIVE 徽章切换为真实状态。
7. 从至少两个外部网络复测主页、浏览器、矿池、钱包、下载、签名和 Fresh IBD。
8. 发布 Bitcointalk/X/Discord 等公告；所有渠道引用同一 GitHub Release 和 checkpoint，不重新上传不同文件。
9. 发射后 0–2 小时每 5 分钟记录高度、算力、worker、accepted/rejected、P2P peers、延迟与错误；2–24 小时每小时留证。

## 八、Agy 最终交付包

- GitHub 组织/仓库权限表与 2FA 截图（不含恢复码）；
- 最终域名/DNS/TLS/服务清单；
- 网站逐页中英文文案、素材授权表、手机与桌面验收截图；
- Guardian 算力公开记录 CSV/JSON、原始证据哈希及说明；
- GitHub Release 正文、公告文案、FAQ、风险披露和反诈骗模板；
- Seed/Replay/签名/审批负责人名单及 UTC 发射排班；
- 一份 GO/NO-GO 表。只要 checkpoint、双 replay、正式签名、两 Seed、Fresh IBD、RTO、外部探测或 launch gate 任一项不是 PASS，就填 `NO-GO`。

## 九、唯一完成标准

“代码已上传”“网页能打开”“矿机绿灯”“高度超过 27 万”都不等于发射完成。只有真实 #271828 checkpoint、两次独立 replay、批准的 Stage-B manifest、可复现且离线签名的最终制品、两个公网节点、Fresh IBD、恢复 RTO、异地探测和 `public_launch_gate.py` 同时 PASS，才允许公开下载、开放矿池并宣布 BTC-K MAINNET LIVE。

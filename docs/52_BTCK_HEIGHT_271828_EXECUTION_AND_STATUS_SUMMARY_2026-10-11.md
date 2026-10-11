# BTC-K 高度 271,828 发射准备状态（2026-10-11）

本文只记录已核验事实。实时高度以 Guardian `/info` 中
`virtual_blue_order_length - 1` 为准；任何预计时间都不是触发条件。

## 已核验完成

- Guardian 节点、Stratum、checkpoint watcher、健康检查与小时备份正常运行。
- checkpoint watcher 每 15 秒检查一次；只在 ledger height **精确达到
  271,828** 时停止 Stratum 并生成 candidate，不提前生成 checkpoint。
- 私有网站保持 `private_guardian`，公开矿池和公开节点开关均为 `false`。
- 两个 worker 正常在线；网页统计提供矿池估计算力与 accepted/rejected 计数。
- 从高度 270,000 起，Guardian 每 5 分钟保存一次原始统计 JSON，并生成同名
  SHA-256 sidecar；271,828 candidate 仍由独立 watcher 生成。
- Guardian 每小时生成数据库备份并校验 SHA-256；Mac 定时拉取副本。
- GitHub 私有仓库已启用 Dependabot 告警与自动修复，治理文件已加入代码库。

## 明确未完成，不得写成 PASS

- GitHub 双 owner / Organization：因新组织账号受限，暂由现有老账号托管；此项
  不伪造完成状态，公开前仍需第二名 owner、2FA 与恢复流程。
- 高度 271,828 尚未到达时，不存在正式 checkpoint、Stage-B replay、批准记录、
  checkpoint-bound release、正式 signed tag 或 GitHub Release。
- 两个公网 Seed、Fresh IBD、RTO、外部探针和 `public_launch_gate.py` 尚未 PASS。
- 未经最终门禁，不得把仓库改为 Public，不得开放公网 P2P/Stratum/RPC，不得把
  网站切换为 `MAINNET LIVE`。

## 到达 271,828 后的不可跳步顺序

1. 确认 Stratum 已自动停止，candidate 高度/hash/state root/供应字段齐全。
2. 立即冻结数据库与原始 JSON，异机拉取备份并验证 SHA-256、SQLite 完整性和
   资产守恒；失败即 `HOLD`。
3. 在两个不同 `environment_id` 的独立主机执行 Stage-B replay；所有共识字段必须
   完全一致，再由独立审批人批准。
4. 冻结源码，在两个 clean 环境完成可复现构建，生成 SBOM、manifest 与
   `SHA256SUMS`，再用仓库外 Ed25519 私钥签名。
5. 将已验签制品部署到两个 Seed，完成 Fresh IBD、恢复 RTO 与异地探测。
6. 运行 `scripts/public_launch_gate.py`。只有全部 PASS，才创建 signed tag、发布
   GitHub Release、开放批准的 P2P/Stratum 端口并切换网站状态；RPC 永不公开。

详细字段、命令边界、网站和 GitHub 发布清单见
`docs/51_BTCK_HEIGHT_271828_LAUNCH_HANDOFF_FOR_AGY_2026-10-10.md`。

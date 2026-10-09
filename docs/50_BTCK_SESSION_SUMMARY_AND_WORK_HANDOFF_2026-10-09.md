# BTC-K 外审后推进记录（2026-10-09）

## 1. 基准结论

- 独立外审基准报告：`docs/49_BTCK_FULL_INDEPENDENT_AUDIT_REPORT_2026-10-09.md`。
- L1 源码审计通过；公网发布继续为 `HOLD / NO-GO`，直至真实 Stage-B 证据齐全。
- 第 2–9 项的唯一主清单位于仓库根目录 `README.md`。本文件记录执行进展，不取代主清单。
- L2 在 MAINNET 继续关闭，不是 L1 公网发布的硬阻塞。

## 2. 已完成的当前可执行工作

1. 修复外审唯一 P2：`deploy/verify_chain_replay.py` 在提供 manifest 时自动读取网络名，并加入回归测试。提交：`adf3c43`。
2. 官方矿池页面新增 Combo 归属规则板块，明确以下边界：
   - 95% 门禁依据 `wallet_address + worker_name` 的 accepted-share 时隙，不声称识别物理矿机；
   - 门禁是官方池内部归属政策，不是 L1 共识；
   - 不合格只失去官方池 Combo 资格，普通 PPLNS 收益不被没收；
   - 无合格候选时 Combo 保持未分配；
   - Solo 与其他矿池的链上中奖结果不受官方池干预；
   - 不承诺固定倍数或利润，并公开四阶段周期、缺席限制、解锁和费率。
3. 新增 `scripts/verify_guardian_stage_a.py`，以只读方式验证拉取快照的：
   - `SHA256SUMS`；
   - 链库与 PPLNS 库 `PRAGMA integrity_check`；
   - MAINNET 身份与 consensus fingerprint；
   - 发行/UTXO 守恒；
   - 快照新鲜度；
   - 预期 Guardian worker 在线状态。
4. 重写 `docs/cloud_seed_node_deployment_guide.md`，删除旧版“公开 RPC、直接同步源码、运行已封禁安装器”等不安全指引。新手册只接受 checkpoint-bound、外部受信公钥验签的最终制品。
5. 全量回归结果：`248 tests / PASS`。该结果包含 L1、P2P、PPLNS、checkpoint/release、钱包以及保持禁用状态的 L2 代码测试，不代表真实公网或 L2 生产验收。

## 3. Stage-A 当前证据状态

- 最近可用本地异机快照：`backups/mainnet-guardian/20261009T150601Z/`。
- 该快照哈希、双 SQLite 完整性、网络身份、资产守恒均通过；快照中 `ks01`、`ks02` 均在线，virtual-blue height 为 `109,819`，Stage-A 验证结果为 `PASS`。
- 本轮首次重新拉取时，管理出口不在白名单而发生 SSH banner 超时；切回既定英国出口后，SSH 与备份拉取恢复。该事件未触发节点或矿池重启。
- 矿机侧端口 `3333` 仍可连接，用户现场观察矿机绿灯持续闪烁。此事实只证明矿机/Stratum 路径仍活动，不能替代节点、备份和网页控制面的完整健康证据。

## 4. 网页不可访问的诊断

- `bitcoin-k.com`、`pool.bitcoin-k.com`、`explorer.bitcoin-k.com` 和 `wallet.bitcoin-k.com` 仍解析到 `185.130.45.240`，不是 DNS 丢失。
- Guardian 防火墙与 Nginx 当前只允许既定管理出口 `51.195.136.143/32` 访问 80/443；SSH 同样受管理来源限制。
- 本轮管理出口一度变化，因此 80/443 和 SSH 被丢弃，而允许矿机来源访问的 3333 仍正常；切回白名单出口后四个 HTTPS 域名均恢复 `200`。
- 访问恢复后，提交 `6eaefda` 的 `web/index.html` 已带旧文件备份原子部署到 Guardian；线上 SHA-256 为 `306b195d05048b40f38f864a236a2cf714e3afa3e848bc88365de89d7979f447`，Combo 规则标记可由 HTTPS 获取，节点、Stratum、wallet edge 与 Nginx 均保持 active。
- `www.bitcoin-k.com` 没有 DNS A/CNAME，当前证书和 Nginx 也不包含 `www`；裸域 `https://bitcoin-k.com/home` 正常。修复 `www` 需先增加 DNS，再扩展证书并配置到裸域的 301 跳转。
- 恢复管理访问应切回既定出口，或从 VPS 控制台把新的固定管理出口加入白名单；禁止为了恢复页面而把 SSH、RPC 或 Guardian HTTPS 无条件开放全网。

## 5. 第 2–9 项实际状态

| 项目 | 当前状态 | 本轮推进 | 仍需真实外部事实 |
|---|---|---|---|
| 2. Stage-A 稳定运行 | 持续中 / 最新证据 PASS | 新增快照完整性和新鲜度门禁；高度 109,819 的新鲜异机快照通过验证 | 持续运行并保持后续快照新鲜，单次 PASS 不代表该持续项完成 |
| 3. 独立基础设施 | 部分准备 | 完成安全部署和验签运行手册 | 购买/指定第二故障域节点、两个独立重放环境、仓库外签名密钥 |
| 4. 冻结 checkpoint | 等待 | 现有锁定工具及 fail-closed 测试通过 | 链真实到达 271,828 |
| 5. 审批 Stage-B | 等待 | 双环境一致性和独立 environment ID 门禁测试通过 | 两次真实独立全 journal 重放与外部 approval ID |
| 6. 最终制品 | 等待 | 可复现构建、SBOM、外部信任锚测试通过 | clean signed tag、两次真实构建、离线正式签名 |
| 7. 公网预发布 | 等待 | readiness、Fresh IBD、RTO、外部探测工具链回归通过 | 两个真实公网节点和 fresh IBD/异地探测证据 |
| 8. 正式开放 L1 | 阻塞 | `public_launch_gate.py` 继续 fail-closed | 第 4–7 项全部真实 PASS 后的独立运维审批 |
| 9. 单独推进 L2 | 代码测试可做、生产关闭 | L2 相关测试纳入 248 项全量回归 | 独立范围、真实金库、小额主网彩排、外部安全与运营审计 |

## 6. 不得提前声称完成的事项

- 不得预填 checkpoint 哈希、Stage-B approval ID 或正式签名。
- 不得把同一主机的两次运行描述成两个独立环境。
- 不得把旧快照、模拟网络或单元测试描述成公网 Fresh IBD。
- 不得把公网网页开放等同于 L1 P2P/矿池公网发布。
- 不得把官方池内部 95% 门禁描述成能够取消 Solo 或其他矿池的链上中奖结果。

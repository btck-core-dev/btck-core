# BTC-K RC4 Session Handoff

记录时间：2026-10-06 03:40:38 UTC（2026-10-06 11:40:38 Asia/Shanghai）

## 当前结论

- RC3 固定 24 小时窗口结论为 `CONDITIONAL_PASS`：稳定性通过，性能未达门禁。
- RC4 长链性能和恢复修复已部署到 DEVNET，链身份和共识指纹未改变。
- RC4 新的 24 小时固定窗口正在运行，不得在终验前随意重启节点或 Stratum。
- DEVNET 点火仍是 `NO-GO`；主网继续严格 `NO-GO`。

## 线上现状

VPS：`ubuntu@185.130.45.240`，项目目录：`/home/ubuntu/btck-devnet`。

2026-10-06 03:40:38 UTC 只读核验：

- `btck-full-node`、`btck-stratum-pool`、`btck-explorer` 全部在线。
- 已重放持久化块：113,734。
- blue score：111,110；DAG 总块：115,329；tips：4。
- 发行量与 UTXO 总量均为 68,068,074,518,022 SatK，资产守恒。
- 最新持久化 checkpoint 序号：115,000。
- checkpoint root：`f435dc6f59f18d1dbd2de67f56f29e5847540403d314d349293b6260f0b0c1f9`。
- safe mode：`false`。
- 在线矿工/连接：2 / 2。
- Explorer 当时短窗节拍：1.33 秒/块，不代表最终 24 小时结论。

## 新 24 小时验收窗口

- 固定起点：`/home/ubuntu/btck-observation/20261006T031946Z`
- 起点蓝序：110,216
- 起点 DAG 块：114,435
- 起点 tips：4
- 起点在线矿工：2
- 起点 traceback / ERROR：0 / 0
- `acceptance-start` 和 `acceptance-start-rc4` 都指向上述快照。
- 自动终验 timer：`btck-rc4b-acceptance-finalize.timer`
- 计划执行：2026-10-07 03:24:49 UTC（2026-10-07 11:24:49 Asia/Shanghai）。
- 终验将生成新的 `acceptance-report-*.json`，并更新 `acceptance-report-latest.json`。

## 本次已完成的修复

1. 修复验收汇总器因不可读/损坏快照整体失败的问题，增加机器可读门禁与拒绝率。
2. DAA 预期难度仅遍历必要窗口，不再重建整条 selected chain。
3. 空挖矿块不再复制整个 UTXO 字典；总发行量和分类总量改为 O(1) 跟踪。
4. DAG 祖先查询按 blue work 剪枝。
5. 持久化 replay 仅校验最新有效 checkpoint root，对 checkpoint 覆盖的记录避免重复 header hash。
6. 普通 selected-tip 扩展改为原地 append/extend，不再每块复制生命周期链列表并重建全链 set。
7. `/info` 和 `getBlockDagInfo` 不再同步重算全链 state root，而是返回已验证 checkpoint root 和明确的 checkpoint 序号；实测 `/info` 响应约 3.4 ms。
8. 恢复校验脚本 `deploy/verify_chain_replay.py` 已加入仓库。
9. 94,039 块的隔离恢复 replay 从 47,859.874 秒降到 1,846.993 秒，约 25.9 倍提速，两次状态根和资产总量一致。
10. 113k 线上 DAG 实际重启约 48 分钟，能正确恢复，但仍不算快速恢复。

## 性能实验记录

- RC3 24 小时：1.510452 秒/蓝序，性能 FAIL。
- RC4、4 秒 Stratum 模板刷新、311 秒短窗：235 蓝序，1.323404 秒/蓝序，tips 3→2。
- 试验将模板刷新降到 2 秒：311 秒仅 207 蓝序，1.502415 秒/蓝序，tips 2→7。
- 2 秒调优明显恶化，已完整回滚到 4 秒；失败试验不在正式 24 小时窗口内。
- 当前性能仍不能宣布 PASS，以 2026-10-07 的固定窗口机器报告为准。

## 备份、回滚和证据

- 最新在线备份：`/home/ubuntu/btck-backups/live-state/20261006T032022Z`
- 部署前代码备份：`/home/ubuntu/btck-backups/code-pre-rc4-20261005T013340Z`
- 恢复副本：`/home/ubuntu/btck-replay-rc4/devnet/chain.sqlite3`
- 优化后恢复结果：`/home/ubuntu/btck-replay-fast2-result.json`
- RC3 权威报告：`/home/ubuntu/btck-observation/acceptance-report-20261004T134200Z.json`
- RC4 终验前不要更改 `acceptance-start`。

## 钱包交付

- 归档：`dist/wallet/BTC-K-Wallet-3.0.0-rc3-portable-python3.tar.gz`
- SHA-256：`bcf13700cfbc92e8b6a49b3ccb6438650d7b9d521fd02a56409c30e4de36857c`
- 已通过创建钱包、读取钱包、文件权限 0600 冒烟测试。
- 当前是 Python 3.10+ `.pyz` 便携包，未签名、未公证。
- 钱包 JSON 仍是 0600 明文私钥；`transfer --offline` 仍需联网获取 UTXO，不是真正冷签名。

## 未解除的阻断项

1. RC4 新 24 小时窗口尚未结束，性能门禁是 `<= 1.25 秒/蓝序`。
2. 113k DAG 重启仍需约 48 分钟，必须实现可验证共识快照和 journal tail replay。
3. 钱包需口令加密、真正 unsigned-package/sign/broadcast 离线流程、原生构建、代码签名和公证。
4. 尚无域名 TLS、反向代理限速、访问日志轮转和第二公网节点长时 soak 闭环证据。
5. 主网仍需独立密码学/共识审计、新密钥仪式、新 genesis/空数据库、正式发布审批和至少 7 天多节点 soak。

## 测试状态

- 核心修复后全量回归曾完整通过 144/144。
- 新增在线 checkpoint 状态接口修复后，chain persistence 和 virtual-chain 针对性测试通过。
- Stratum 回滚配置后目标验证 14/14 通过。
- 最后一次全量套件受当时本地受限环境禁止 bind `127.0.0.1:0` 影响，12 个网络测试以 `PermissionError` 结束，不是业务断言失败。

## 下一 Session 首要操作

1. 先只读检查 timer，不要重启服务：

   ```bash
   ssh -i ~/.ssh/btck_deploy_ed25519 ubuntu@185.130.45.240 \
     'systemctl list-timers --all --no-pager | grep btck-rc4b-acceptance-finalize'
   ```

2. 2026-10-07 03:24:49 UTC 后检查终验报告：

   ```bash
   ssh -i ~/.ssh/btck_deploy_ed25519 ubuntu@185.130.45.240 \
     'readlink -f /home/ubuntu/btck-observation/acceptance-report-latest.json && \
      cat /home/ubuntu/btck-observation/acceptance-report-latest.json'
   ```

3. 确认报告的 `start_snapshot` 对应 `20261006T031946Z`；若仍指向 RC3 报告，先检查 transient service 日志：

   ```bash
   ssh -i ~/.ssh/btck_deploy_ed25519 ubuntu@185.130.45.240 \
     'systemctl status btck-rc4b-acceptance-finalize.service --no-pager; \
      journalctl -u btck-rc4b-acceptance-finalize.service --no-pager -n 100'
   ```

4. 只有当机器报告同时满足稳定性和性能门禁时，才能将 DEVNET 状态改为 PASS。否则继续修复，不手工美化结论。
5. 优先下一个工程项应是可验证共识快照，其次是钱包加密与真正离线签名。

## 工作区注意事项

- 当前修复均未提交，工作树是 dirty；不要 reset/checkout 用户修改。
- 主要未提交文件包括 `blockdag.py`、`chain_store.py`、`daemon.py`、`utxo_set.py`、验收汇总器、回放校验脚本、测试、钱包封装和文档。
- 不要运行 BTCB2 `bitcoind`；本交接文档仅涉及 BTC-K DEVNET。

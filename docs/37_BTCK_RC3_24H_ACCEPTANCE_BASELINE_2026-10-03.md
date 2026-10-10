# BTC-K RC3 24 小时真机验收基线与当日变更记录

记录日期：2026-10-03（Asia/Shanghai）

## 1. 24 小时从哪个时间点计算

本轮 24 小时验收的唯一数据起点是 VPS 上已固定并带 SHA-256 校验的观测目录：

`/home/ubuntu/btck-observation/20261003T133540Z`

`acceptance-start` 符号链接固定指向该目录。时间换算如下：

- UTC：2026-10-03 13:35:40
- 北京时间（UTC+8）：2026-10-03 21:35:40
- 起始蓝序账本高度：15,343
- 起始 DAG 块数：19,561
- 起始在线矿工数：2
- 起始 10 分钟矿池算力估算：约 733.01 GH/s
- 起始 Combo 奖池：14,494.89480852 BTCK
- 起始生态基金：4,831.63160284 BTCK
- 起始矿池手续费累计：4,121.51437434 BTCK

24 小时不是从矿机重置时间、Explorer 重启时间、最近一次人工快照或最终定时器启用时间重新计算。后续部署仅涉及 Explorer、证据采集和在线备份，没有重启全节点或 Stratum，因此不会重置本轮数据起点。

## 2. 最终报告时间

最终汇总定时器已使用 systemd override 锁定到：

- UTC：2026-10-04 13:42:00
- 北京时间（UTC+8）：2026-10-04 21:42:00

最终采样相对起点约为 24 小时 6 分 20 秒。多出的约 6 分钟用于越过完整 24 小时边界并生成最终快照；汇总器仍以 `acceptance-start` 指向的 `20261003T133540Z` 为首个样本，以最终定时器生成的快照为末个样本。

## 3. 2026-10-03 已完成修改

1. 部署 RC3 动态 DAG 波次与旧 tip 清理策略，解决 tips 长期堆积和蓝块节拍退化。
2. 建立 24 小时小时级自动观测、最终快照和自动验收报告。
3. 看板难度改为相对 BTC-K 当前网络 `pow_limit` 的倍率；最低难度显示为 `1.0000x`。
4. 当 DAA 位于最低难度且 Stratum 对有效解执行 DAG 波次限流时，不再显示由出块率推导出的虚假物理网络算力。
5. 每次观测增加 `deployment-manifest.json`，保存生产运行文件的逐文件 SHA-256、聚合 SHA-256、Git HEAD/tree 与工作树状态。
6. 增加全节点链数据库与 PPLNS 账本的每日不停机 SQLite 在线备份，并执行 `PRAGMA integrity_check` 与 SHA-256 校验。
7. 修复 systemd daemon reload 导致相对验收定时器重新计算的问题，改用固定日历时间锁定最终报告。
8. 本地完整测试共 141 项，全部通过。

对应本地 Git 记录：

- `67001ce`：RC3 动态 DAG 波次修复，标签 `v3.0.0-rc3`
- `ce98ff1`：24 小时验收自动化
- `261c945`：RC3 真机稳态基线记录
- `454bb92`：部署证据、看板难度语义和在线状态备份

## 4. 自动任务与产物

- `btck-observation.timer`：每小时生成一次观测快照。
- `btck-acceptance-finalize.timer`：2026-10-04 13:42:00 UTC 生成最终快照与报告。
- `btck-state-backup.timer`：每天生成链数据库和 PPLNS 账本在线备份。
- 观测目录：`/home/ubuntu/btck-observation/`
- 状态备份目录：`/home/ubuntu/btck-backups/live-state/`
- 最终报告链接：`/home/ubuntu/btck-observation/acceptance-report-latest.json`

## 5. 本轮等待验证的内容

- 24 小时蓝序平均节拍与 tips 上下界。
- Share 的 Stale、Low Difficulty、Duplicate、Rate Limit 和节点提交错误比例。
- 两台真机连接稳定性及 10 分钟窗口算力。
- 高度 43,200 和 86,400 的真实 Combo 状态转换与开奖结果（若窗口内达到）。
- Combo 奖池、5% 生态基金、矿池费和发行总量的增量守恒。
- 容器运行错误、Traceback、磁盘与资源占用。

## 6. 验收期间不实施的变更

- 不修改 PoW、DAA、GhostDAG、发行或 Combo 共识逻辑。
- 不重启全节点和 Stratum，除非发生必须恢复的故障。
- 不切换真实 PPLNS 生产支付模式。
- 不上线快速共识状态装载；该功能需要在本轮验收后单独开发并执行重启一致性测试。
- 不创建正式发布标签；正式封版以本轮最终报告通过为前提。

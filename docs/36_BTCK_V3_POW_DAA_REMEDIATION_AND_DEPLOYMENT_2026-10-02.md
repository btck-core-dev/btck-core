# BTC-K v3 PoW / DAA 修复与部署记录（2026-10-02）

24 小时真机验收的固定起点、结束时间和 2026-10-03 当日变更，见
[`37_BTCK_RC3_24H_ACCEPTANCE_BASELINE_2026-10-03.md`](37_BTCK_RC3_24H_ACCEPTANCE_BASELINE_2026-10-03.md)。

## 修复范围

- 将 Stratum 与 BlockDAG 统一到同一个 ASIC 兼容 kHeavyHash 计算入口；候选块提交前再次断言矿池 PoW 与节点 PoW 完全一致。
- 引入选定链 DAA：264 块窗口、11 块端点中位时间、1 秒目标、单次 2 倍调整钳位；难度字段成为共识校验项。
- devnet 身份升级为 `btck-devnet-v3`，创世哈希为 `00afe7ba34ab52100a7e086f8bf16cee97dfaa80eaf7bc157a8d933d6ed6fa2a`。旧 v2 数据不得与 v3 混用。
- 模板仅在父块、难度或交易集合改变时清理；旧模板的合格 Share 可记账但不再提交 DAG。
- Duplicate 键改为单客户端 `(job_id, nonce)` 有界缓存；硬 5 ms 限频改为令牌桶。
- 挖矿父集由节点确定且受共识上限约束，不再由矿池任意截断。
- 3333 端口按 IceRiver 真机提交阈值固定为 Share 难度 128；每客户端保留 128 个 Job，迟到 Share 继续记账，只有 16 秒内的候选进入 DAG。Share 难度是记账与采样阈值，与网络出块难度独立。
- Stratum 将并发候选合并为固定 4 秒模板波次（0.25 Hz）；节点始终锚定 selected tip，并优先把低工作量、较旧的侧链 tips 纳入剩余父槽，避免 ASIC 深流水线迟到块长期堆积。
- 提交上限按 DAG 拥塞动态切换：8 个父槽占满时，每模板仅首个达标 Share 进入节点，以每波最多净清理 7 个历史 tips；父槽未满后，每波最多接纳 8 个兄弟块，下一波一次合并。超过上限或旧模板的达标 Share 仍正常计入矿工 accepted 与算力，但不重复污染 DAG。
- PPLNS SQLite 使用独立 Docker 卷 `/app/pool-data`；链数据和分润账本分别持久化。
- 看板由 Compose 管理并公开映射 8088；展示的网络难度和估算算力由当前 DAA target 与实际块间隔计算。

## 验证

- 本地：`PYTHONPATH=src python3 -m unittest discover -s tests -q`
- 结果：141 项测试全部通过（含 DAA 快慢算力窗口、时间戳异常、PPLNS 重启持久化、重复结算幂等、动态 DAG 波次、部署证据清单与 24 小时验收汇总器测试）。
- Docker Compose 配置已在 VPS 使用 Docker Compose 2.40.3 校验并成功构建。
- 公网入口：`http://185.130.45.240:8088/`；Stratum 3333--3336 均已监听。
- 两台 KS0 已自动重连，并产生由 v3 全节点接受的候选块；观察窗口内没有 `Node submitBlock error`。
- 首个 DAA 窗口已在选定链超过 264 块后生效：目标由 `0x1c01ffff` 平滑调整，调整期间持续正常验块。
- 重连瞬间观察到 1 个旧 Job 与 8 个低难度提交；随后连续多个一分钟窗口累计数百个有效 Share，Stale、Low Difficulty、Duplicate、Rate Limit 与节点提交错误均为 0。
- 早期 1 Hz 策略在 53.5 秒窗口内曾达到 1.01 秒/蓝块，但长时间运行后 tips 累积至 389、看板节拍回落至 1.92 秒；因此该短窗口结论已作废，并由 4 秒动态波次与旧 tip 清理策略取代。新的 24 小时验收只能从该策略部署后的新基线开始。
- 看板已按时间排序后的蓝序吞吐计算节拍，不再丢弃并行块的零/负相邻时间差；120 块平滑值与高度墙钟增量一致。
- RC3 真机清障阶段 tips 从 510 连续降至个位数；进入稳态后，5 分钟内 434 个有效 Share、264 个节点提交、160 个超额/旧模板 Share 继续记账，四类 Stratum 拒绝与节点提交错误均为 0。看板稳定到约 1.03 秒/蓝块，tips 为 2，两台矿机 10 分钟估算合计约 764 GH/s。
- 看板难度改为相对当前网络 `pow_limit` 的倍率，最低难度明确显示为 `1.0000x`；当网络位于最低难度且 Stratum 对有效解做 DAG 波次限流时，不再用出块率虚构物理网络算力。
- 每次观测快照增加 `deployment-manifest.json`，记录所有生产运行文件的逐文件 SHA-256、聚合 SHA-256、Git HEAD/tree 与 dirty 状态；即使 VPS 不是干净检出，也可精确追溯实际运行内容。
- 增加不停机 SQLite 在线备份，链 journal 与 PPLNS 账本每日备份并执行 `PRAGMA integrity_check`。快速共识状态装载仍须在本轮验收结束后单独实现和验证。

## 数据迁移与回滚点

v3 改变了 PoW、DAA 和网络身份，因此使用全新 devnet 数据卷启动。旧 v2 代码与链卷已归档在 VPS：

`/home/ubuntu/btck-backups/20261002T133138Z`

目录内 `SHA256SUMS` 可验证归档完整性。回滚必须停止 v3 Compose，恢复该目录中的代码归档与 `btck-data-v2.tar.gz`，不得把 v2 数据直接挂载给 v3 镜像。

## 仍需长期观察

- 24 小时 Share 分类比例与矿机固件重连行为。
- 高度 43,200 与 86,400 的 Combo 实际开奖、15% 奖池与 5% 生态基金守恒。
- 两台矿机 10 分钟 Share 窗口的稳定算力；不得用矿机铭牌值替代矿池实测值。
- 公网看板当前为 HTTP；绑定域名后应在反向代理层启用 TLS、访问日志轮转与速率限制。

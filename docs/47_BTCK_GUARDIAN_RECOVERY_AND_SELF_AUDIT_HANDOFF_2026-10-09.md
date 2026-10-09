# BTC-K Guardian 故障恢复、运维加固与全项目自审交接（2026-10-09）

## 1. 本轮结论

2026-10-09 已完成 Guardian VPS 故障恢复、受控重启验收、资源隔离、备份保留与异机复制、SSH 加固和健康守护部署。当前 MAINNET 私有护网正常运行，两台 KS0 在线，链高度持续增长，系统无 failed unit。

本文件是下一 session 开始全项目自审的唯一交接入口。自审第一阶段应保持只读，先形成证据化问题清单，再把修复与审计发现分开提交，避免边审边改导致证据漂移。

## 2. 已确认的故障原因与风险

1. 同一台 4 GB VPS 曾同时运行旧 DEVNET Docker 栈和正式 MAINNET 服务。旧 DEVNET 节点长期占用约一个 CPU 核和数百 MB 内存，历史峰值达到约 2.7 GB。
2. 2026-10-07 内核 OOM 记录显示 Python 进程曾在无可用 swap 的情况下被杀死；MAINNET 与 DEVNET 同机并行是明确的资源风险。
3. 2026-10-09 重启后，`ssh.socket` 接受 TCP 连接，但 `ssh.service` 未直接启动，表现为端口可连接却长期没有 SSH banner。
4. MAINNET 节点需要回放数据库后才开放 RPC；旧 Stratum 服务未等待 RPC 就绪，启动期间发生连续失败重启。
5. VPS 上原有 4 GB swap 文件有效但未启用，也未写入 `/etc/fstab`。
6. MAINNET 每小时完整备份此前没有保留策略，备份数量和单份体积均持续增长，长期会占满磁盘。
7. 旧 DEVNET 容器停用后，其每日备份 timer 仍会尝试访问已经停止的容器。

当天整机首次失去响应的唯一瞬时触发因素没有足够日志可以百分之百还原；没有证据支持“本机 VPN 导致 VPS 故障”。已确认并关闭的是能够复现或由系统日志证明的资源、启动顺序和 SSH 服务风险。

## 3. 已实施修复

### 3.1 MAINNET 启动与健康恢复

- 启用 `ssh.service` 开机启动，不再只依赖 socket activation。
- Stratum 增加 MAINNET RPC readiness gate：节点完成回放并返回正确 ChainId/DAG 状态后才启动。
- 部署每分钟 Guardian 健康检查，覆盖节点 RPC、Stratum 状态、钱包同步边缘、HTTPS 和 SSH banner。
- 节点连续 5 次失败才恢复，其他组件连续 3 次失败才恢复，防止一次短暂抖动触发误重启。
- 健康状态保存在 `/run/btck-mainnet-health.json`，不写入永久数据库。

相关文件：

- `deploy/wait_for_mainnet_rpc.py`
- `deploy/guardian_health_watch.py`
- `deploy/btck-mainnet-health.service`
- `deploy/btck-mainnet-health.timer`
- `deploy/btck-mainnet-stratum.service`

### 3.2 资源隔离与内存保护

- 旧 `btck-full-node`、`btck-explorer`、`btck-stratum-pool` 三个 DEVNET 容器全部停止。
- 三个容器的 restart policy 均改为 `no`；没有删除容器、镜像、volume 或数据库。
- `/swapfile-btck-rc5` 4 GB swap 已启用并写入 `/etc/fstab`。
- `vm.swappiness=10`，swap 只作为 OOM 最后保护，不替代正常内存。
- 停用旧 `btck-state-backup.timer`，避免访问已停止的 DEVNET 容器。

### 3.3 MAINNET 备份与 Mac 异机副本

- MAINNET VPS 继续每小时生成 SQLite online backup，并逐库执行 `PRAGMA integrity_check`。
- 新增保留策略：最近 24 份小时快照，加最近 7 个 UTC 日期各一份日快照。
- 保留策略只识别严格的 `YYYYMMDDTHHMMSSZ` 目录，并在新备份完全成功、更新 `latest` 后才清理过期快照。
- 首次执行保留策略时删除了两份最旧的重复快照：`20261008T081924Z`、`20261008T081617Z`；其余审计状态和全部链数据库保留。
- 最新完整备份 `20261009T055459Z` 已复制到 Mac：
  `backups/mainnet-guardian/20261009T055459Z/`
- `chain.sqlite3`、`pool_accounting.db`、manifest、node info、pool status 五项 SHA-256 全部通过。
- macOS LaunchAgent `com.bitcoink.guardian-backup-pull` 每 6 小时拉取 VPS 最新快照，Mac 本地保留最近 14 份；首次真实执行退出码为 0。

相关文件：

- `deploy/backup_guardian_state.py`
- `scripts/pull_mainnet_backup.sh`
- `deploy/com.bitcoink.guardian-backup-pull.plist`
- `tests/test_backup_guardian_state.py`

Mac 与 VPS 已形成两个独立设备副本，但这仍不等于离线冷备。长期保存仍应增加独立硬盘或私有云副本。

### 3.4 SSH 加固

- `PasswordAuthentication no`
- `KbdInteractiveAuthentication no`
- `PermitRootLogin no`
- `MaxAuthTries 3`
- `ubuntu` 密钥登录已用全新独立连接验证成功。
- Privex VNC/串口控制台的本地系统密码登录不受 SSH 配置影响。

相关文件：`deploy/00-btck-sshd-hardening.conf`。

## 4. 受控重启验收证据

受控重启前高度为 `75647`，两台矿机在线。重启后的新 boot ID 与旧 boot ID 不同，证明不是服务重载而是真实主机重启。

- SSH：约 20 秒自动恢复；
- swap：随开机自动启用；
- MAINNET 节点：约 2 分 20 秒完成约 7.5 万高度数据库回放；
- Stratum：RPC ready 后自动启动，没有重启风暴；
- 两台 KS0：Stratum 启动后约 5–7 秒自动连接和授权；
- 钱包同步边缘、Nginx、checkpoint timer、backup timer、health timer：全部 active；
- 健康 timer 在开机后自动执行并输出 `all BTC-K guardian health checks passed`；
- systemd failed units：0；
- 高度在验收窗口内从 `75647` 增长到 `75825`。

## 5. 测试与提交

- 全量测试：`236 tests PASS`。
- 新增备份保留测试：小时保留、每日保留、安全路径过滤、忽略 `latest` symlink 和非快照目录。
- 工作区在交接前为 clean。

关联提交：

1. `f78d350 fix: gate guardian stratum on mainnet RPC readiness`
2. `36625e9 ops: harden guardian host recovery`
3. `dceb3dd ops: harden guardian backups and SSH`

## 6. 已知但未在本轮强行实施的事项

这些不是当前私有护网停矿条件，应在全项目自审中重新定级：

1. **公网外部告警**：网站仍受 IP 白名单保护，4070 服务器当前无法访问公网。从未授权地址探测会被设计性拒绝，因此本轮没有制造一个必然误报的“外部监控”。公网切换时再接入独立探测和通知渠道。
2. **高度 271,828 仪式**：checkpoint 候选、两个独立恢复环境重放、公开 manifest 审批和最终制品重建尚未发生；这是阶段 A 尚未到达目标高度的正常状态。
3. **钱包发布签名**：钱包 `3.2.3-guardian1` 可运行，但尚未获得 Apple Developer ID 签名、公证或独立第三方安全审计。
4. **恢复时间**：约 7.5 万高度回放需 2 分 20 秒；到 271,828 高度可能进一步增长，应在公开前评估 checkpoint/snapshot 恢复时间目标。
5. **第二在线节点**：当前不需要立即购买；公开 P2P 时应重新评估 seed 冗余和单 VPS 故障域。
6. **L2**：生产功能保持关闭。MAINNET 小额彩排、正式操作限额和独立安全审计完成前不得开启。

## 7. 下一 session 的全项目自审范围

按以下顺序进行，先读白皮书和 canonical 规范，再核对代码、测试、制品与线上证据：

1. 共识身份、Genesis、PoW、GHOSTDAG、DAA、时间规则、重放一致性与 checkpoint 边界；
2. 四年发行、每日衰减、5% 生态基金、Combo 年度比例和每 12 小时开奖；
3. 矿池 5%/3%/1% 时间表、SOLO/PPLNS 账本、重启幂等和提现路径；
4. 钱包 BIP-39、密钥隔离、隐身地址、增量同步、离线签名、广播与 MAINNET 身份锁定；
5. Explorer、Pool、Wallet API 的隐私泄露、限速、错误处理与公网边界；
6. L2 KCU 定价、链上充值/提现、可验证销毁、GPU 分账、年份判定和所有 fail-closed 路径；
7. systemd、Docker、Nginx、TLS、防火墙、备份、恢复、日志、资源上限和供应链制品；
8. 白皮书、网站、代码、manifest、发布页之间的所有陈述一致性；
9. 形成 P0/P1/P2/非问题四级清单，每项必须包含文件/行号或线上机器证据；
10. 明确区分“公网发射硬阻塞”“发布后优化”“L2 独立里程碑”，避免范围膨胀推迟 L1 发射。

建议新 session 的首条指令：

> 阅读 `docs/47_BTCK_GUARDIAN_RECOVERY_AND_SELF_AUDIT_HANDOFF_2026-10-09.md`，以只读方式开始 BTC-K 全项目自审。先复核白皮书与 canonical tokenomics，再输出证据化 P0/P1/P2 清单；不要边审边改，也不要把 L2 未完成项误判为 L1 公网发射硬阻塞。

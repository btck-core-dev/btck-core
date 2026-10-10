# BTC-K 钱包收口与 100 矿工容量门禁

日期：2026-10-08（Asia/Shanghai）

## 结论

- MAINNET fail-closed 预检：**GO，可进入受控创世搜索仪式**；正式 UTC、nonce、genesis hash 仍为待冻结字段。
- 冻结候选完整回归：**190/190 PASS**，在 VPS 独立目录 `/home/ubuntu/btck-preflight-20261008-0927` 执行。
- 100 矿工容量门禁：**PASS**。测试仅使用隔离 SIMNET、临时数据库和临时钱包，未覆盖或重启线上 DEVNET。
- 当前单台 4 核 VPS 足以作为起步阶段矿池节点；第二服务器仍是收入/规模上升后的可靠性增强项，不是当前创世硬条件。

## 钱包安全收口

联网钱包操作现在统一在读取 UTXO、构造交易或广播前核对：

1. network name；
2. ChainId；
3. genesis hash；
4. consensus fingerprint。

余额扫描、直接转账、离线准备、离线签名包和广播均使用同一 fail-closed 身份规则。MAINNET 身份仍故意不进入钱包批准表；只有真实创世哈希和最终共识指纹产生后，才能生成允许 MAINNET 的正式钱包包。

## Stratum 回连优化

审计发现矿工授权原先通过生成一次性隐身输出验证地址，每次产生三次不必要的 secp256k1 标量运算。在 100 台矿机同时回连时，这会与正式 Job 的隐身 coinbase 生成叠加，导致测试超过 90 秒。

现改为仅执行前缀、Base58/legacy hex、校验和及两个 secp256k1 曲线点验证；正式 Job 仍为每个矿工生成独立一次性 coinbase 输出，没有降低奖励所有权或隐私约束。

## 100 矿工机器证据

测试模型：100 个客户端同时连接、授权并接收独立 Job；在所有连接保持期间，10 个客户端按 VarDiff 现实节奏分散提交 Share；随后执行 5 次 PPLNS 结算守恒检查。

| 指标 | 结果 |
|---|---:|
| 连接成功 | 100 / 100 |
| 失败连接 | 0 |
| Share 接受 | 10 / 10 |
| Share 拒绝/超时 | 0 |
| 持久化 Share/worker | 10 / 10 |
| PPLNS 结算 | 5 |
| 结算守恒错误 | 0 |
| 非预期节点区块提交 | 0 |
| 连接延迟中位数 | 28,347.11 ms |
| 连接延迟 P95 | 28,353.93 ms |
| 归一化 CPU | 19.05% |
| 总测试时间 | 37.411 s |

门禁脚本：`scripts/stratum_capacity_gate.py`。默认门槛为 100 个连接、零连接失败、零 Share 拒绝、P95 小于 30 秒、CPU 小于 70%、PPLNS 落盘一致且 SatK 结算守恒。

## 剩余创世硬工作

1. 负责人冻结正式 UTC，使用两台 KS0 搜索 immutable genesis nonce/hash。
2. 将真实 genesis hash 和 consensus fingerprint 固化进正式 MAINNET 钱包批准身份，重新构建并校验钱包制品。
3. 生成节点、矿池、钱包发布制品的 SHA-256、SBOM、签名和已知限制。
4. 域名到手后配置矿池/浏览器 DNS 与 TLS；域名不参与共识，也不阻塞创世搜索。

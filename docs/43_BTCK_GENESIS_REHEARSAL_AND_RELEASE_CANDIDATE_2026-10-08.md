# BTC-K 创世彩排与预创世发布候选记录（2026-10-08）

## 结论

- 创世彩排：**PASS**。
- 节点、矿池/浏览器、钱包发布材料：已具备 `3.2.0-rc2` 可复现构建入口。
- 这些是**预创世候选包**，不是最终 MAINNET 公网制品。正式创世后必须嵌入最终链身份并重新构建。
- 未清理旧数据库、未修改线上 DEVNET、未修改正式 MAINNET manifest。

## 隔离创世彩排证据

执行环境：VPS `/home/ubuntu/btck-preflight-20261008-0927`；运行数据全部位于自动删除的 `/tmp/btck-mainnet-rehearsal-*`。

彩排使用明确隔离身份 `btck-mainnet-rehearsal-only`、独立 wire magic 和仅供测试的低难度 `0x207fffff`。彩排 hash/nonce 永远不得复制到正式 manifest。

结果：

- 固定临时 UTC：`1791437777`；
- 临时 genesis nonce：`0x1`；
- 临时 genesis hash：`680df92de4b23e6439c1beb4090eb57404c29042c262db6c4c0dd29bcf88180a`；
- 两节点握手成功；
- 节点 A 接受并向节点 B 传播区块 `7efc25e9e843b771588fb0fb81c64835280d8ea9ccda5c4a397599597789813f`；
- 两节点重启后均准确回放 1 个持久化区块；
- 重启前后 selected tip、发行量、Combo 池余额和 state root 全部一致；
- state root：`5bd31a63a1f27fab0580fb246986d001c9b6eef23fc837bda3cce2407bac3b4a`；
- 正式 `params/networks/mainnet.json` 前后 SHA-256 均为 `4232027b842da1daf6018e7fe21e4d56b416c47cf8bfee0b85afa214beeda5f9`。

可重复执行入口：

```bash
python3 scripts/mainnet_genesis_rehearsal.py --output build/rehearsal/report.json
```

受限 Mac 沙箱不能监听回环端口，因此完整网络彩排在隔离 VPS 路径执行；脚本自身会创建和删除临时目录，并对正式 manifest 做前后哈希保护。

## 发布候选材料

版本/标签：`3.2.0-rc2` / `mainnet-pre-genesis-rc2`。

构建产物目录：`dist/release-candidate/`，包含：

- `btck-node-3.2.0-rc2.tar.gz`；
- `btck-pool-stack-3.2.0-rc2.tar.gz`（包括 Stratum、Explorer 和可选 TLS edge）；
- `BTC-K-Wallet-3.2.0-rc2-*.tar.gz`；
- `SHA256SUMS`；
- `SBOM.spdx.json`；
- `RELEASE_MANIFEST.json`；
- `KNOWN_LIMITATIONS.md`。

构建器拒绝脏工作区，并要求 HEAD 正好带有 `mainnet-pre-genesis-rc2` 标签。源码 tar 使用固定元数据和 gzip mtime，便于相同提交的复现核验。

钱包仍然 fail-closed：当前候选只批准 DEVNET/TESTNET，未知 MAINNET 身份会被拒绝。正式 genesis hash 和 consensus fingerprint 冻结后，才把 MAINNET 身份嵌入钱包并生成最终包。

## 域名占位配置

`deploy/public-edge/.env.public.example` 使用保留示例域名：

- `explorer.btck-project.example`；
- `pool.btck-project.example`。

当前 Explorer 页面同时提供链与矿池统计，首次上线只激活一个 HTTPS hostname；第二个 pool hostname 只是待购买域名后的预留值。正式上线只替换 DNS、hostname 和仓库外 TLS 路径，RPC 继续仅绑定本机回环地址。

## 正式点火仍需人工输入的唯一链身份步骤

1. 冻结正式 UTC；
2. 由真实 KS0 搜索正式 nonce；
3. 审核并批准最终 manifest；
4. 将最终 MAINNET 身份嵌入钱包；
5. 从最终标签重建全部制品、SHA-256、SBOM 和签名。

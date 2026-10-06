BTC-K Wallet 3.1.0-rc1（macOS）
================================

适用范围
--------
本包仅用于无真实价值的 BTC-K DEVNET/TESTNET。主网尚未获批，禁止承载真实资产。
这是节点驱动的钱包命令行程序：私钥和签名留在本机，余额查询和广播需要连接受信任的 BTC-K 节点 RPC。
若文件名为 btck-wallet.pyz，需要 Python 3.10 或更高版本；原生 btck-wallet 文件不需要另装 Python。

首次运行
--------
若 macOS 阻止未签名程序，可在“系统设置 → 隐私与安全性”中核验文件 SHA-256 后选择允许。

  ./btck-wallet.pyz --version
  ./btck-wallet.pyz new --name miner -o miner_wallet.json
  ./btck-wallet.pyz info miner_wallet.json

新钱包默认使用 scrypt + ChaCha20-Poly1305 口令加密。交互模式会安全提示输入口令；自动化场景可用仅本人可读的 --password-file，且该参数必须写在子命令之前。

连接节点
--------
推荐先通过 SSH 或受控私网把节点 RPC 映射到本机 127.0.0.1:16110，不要把 RPC token 放在命令历史中：

  export BTCK_RPC_URL=http://127.0.0.1:16110/rpc
  export BTCK_RPC_TOKEN='从节点管理员安全取得的令牌'
  ./btck-wallet.pyz balance miner_wallet.json

转账
----
  ./btck-wallet.pyz transfer miner_wallet.json --to btck1s... --amount 1.25 --fee 0.0001
  ./btck-wallet.pyz status <txid>

真正离线签名
------------
在离线机导出只读扫描钱包（包含私密 view key，但不包含 spend key）：

  ./btck-wallet.pyz export-watch miner_wallet.json --out miner_watch.json

将 miner_watch.json 送到联网机，扫描 UTXO 并生成 unsigned package：

  ./btck-wallet.pyz prepare miner_watch.json --to btck1s... --amount 1.25 --fee 0.0001 --out unsigned.json

只把 unsigned.json 带回离线机；逐项核对收款地址、金额、费用、网络身份和输入后签名：

  ./btck-wallet.pyz sign miner_wallet.json unsigned.json --approve-package-id <已核对的Package-ID> --out signed.json

把 signed.json 带到联网机广播：

  ./btck-wallet.pyz broadcast signed.json

旧版明文钱包必须在离线机迁移，原文件不会被覆盖：

  ./btck-wallet.pyz migrate-encrypt legacy_wallet.json --out encrypted_wallet.json

安全边界
--------
1. 新钱包私钥以 scrypt + ChaCha20-Poly1305 加密；口令丢失无法恢复，必须分别离线备份钱包和口令。
2. watch-only 文件包含私密 view key，会暴露收款可见性；它不能花费资产，但仍应按敏感文件保护。
3. 离线签名前必须逐项核对 package ID、网络、收款地址、金额、费用和输入；命令要求原样输入已批准的 package ID。
4. 不要把完整钱包、口令、RPC token 或未核对的签名材料发送给任何人，也不要放进项目仓库、云盘或 VPS。
5. 本包尚未做 Apple Developer ID 签名/公证，也没有第三方密码学与共识审计。
6. 旧版 transfer --offline 已禁用；必须使用 prepare/sign/broadcast，离线 sign 子命令不会调用节点 RPC。
7. 发布前请用同目录 .sha256 文件核验压缩包。

BTC-K Wallet 3.2.0-rc2（macOS）
================================

适用范围
--------
本包仅用于无真实价值的 BTC-K DEVNET/TESTNET。主网尚未获批，禁止承载真实资产。
这是节点驱动的钱包程序：私钥和签名留在本机，余额查询和广播需要连接受信任的 BTC-K 节点 RPC。
若文件名为 btck-wallet.pyz，需要 Python 3.10 或更高版本；原生 btck-wallet 文件不需要另装 Python。

双击打开
--------
在 Finder 中解压后，双击“打开BTC-K钱包.command”。若 macOS 首次阻止运行，请先核验压缩包
SHA-256，再在“系统设置 → 隐私与安全性”中允许。菜单可以生成三套独立钱包、查看地址和验证助记词。

正式创世前推荐使用菜单第 1 项，一次生成：
  1. 个人/挖矿钱包
  2. 矿池金库钱包
  3. 生态基金钱包

每个钱包使用完全独立的 24 词助记词和独立口令。助记词只在生成时显示一次，不会写入钱包文件；
必须手抄，禁止截图、复制到聊天、邮箱、云盘或 VPS。程序会要求立即重新输入整套助记词，验证成功
后才继续生成下一钱包；全部生成后还应从主菜单逐个再次执行“验证助记词”。
验证时按编号逐个明文输入，能看到自己输入的单词；这些输入发生在钱包程序内部，不进入 Shell 命令历史。
如果输错或关闭窗口，重新打开并选择第 1 项，程序会验证已存在的钱包并从中断处继续，不会覆盖它。

首次运行
--------
若 macOS 阻止未签名程序，可在“系统设置 → 隐私与安全性”中核验文件 SHA-256 后选择允许。

  ./btck-wallet.pyz --version
  ./btck-wallet.pyz new --name miner --words 24 -o miner_wallet.json
  ./btck-wallet.pyz info miner_wallet.json

从助记词恢复：

  ./btck-wallet.pyz restore --name miner -o restored_wallet.json

核对助记词能否恢复同一个地址：

  ./btck-wallet.pyz verify-recovery miner_wallet.json

一次生成三套主网身份钱包与经过所有权验证的生态基金公开输出：

  ./btck-wallet.pyz init-mainnet-wallets --words 24 --output-dir "$HOME/Documents/BTC-K-Mainnet-Wallets"

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
1. 新钱包私钥以 scrypt + ChaCha20-Poly1305 加密；钱包文件或口令丢失时可用对应的 24 词恢复。
2. watch-only 文件包含私密 view key，会暴露收款可见性；它不能花费资产，但仍应按敏感文件保护。
3. 离线签名前必须逐项核对 package ID、网络、收款地址、金额、费用和输入；命令要求原样输入已批准的 package ID。
4. 不要把完整钱包、口令、RPC token 或未核对的签名材料发送给任何人，也不要放进项目仓库、云盘或 VPS。
5. 本包尚未做 Apple Developer ID 签名/公证，也没有第三方密码学与共识审计。
6. 旧版 transfer --offline 已禁用；必须使用 prepare/sign/broadcast，离线 sign 子命令不会调用节点 RPC。
7. 发布前请用同目录 .sha256 文件核验压缩包。
8. 钱包只允许为本版本内置批准的 DEVNET/TESTNET 链身份签名；未知网络或未发布 MAINNET 会被拒绝。
9. 余额会分别显示 tracked 与 spendable；转账只选择已成熟、已解锁的输出。
10. 当前 RC 仍不批准 MAINNET 转账；最终 genesis hash 冻结后必须重新构建主网正式钱包制品。

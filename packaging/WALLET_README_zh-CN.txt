BTC-K Wallet 3.2.1-guardian1（macOS）
=====================================

适用范围
--------
本包已内置并仅批准当前 BTC-K 私有护网 MAINNET（guardian1），同时支持 DEVNET/TESTNET。
它不是高度 271,828 后的公网正式钱包；届时链身份改变，必须换用重新构建的公开版钱包。
这是本地密钥钱包：私钥、查看密钥和签名始终留在本机。余额通过公开 HTTPS 增量接口读取
不含钱包身份的链上输出页，再由本机识别属于自己的输出；服务器不会收到钱包地址或任何私钥。
同步游标和已识别的 UTXO 保存在钱包旁的 .sync.json 文件中，并使用同一钱包口令加密。
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

同步链上余额
------------
正式钱包默认连接公开 HTTPS 只读同步接口，不需要 SSH、VPS 账号或完整节点：

  ./btck-wallet balance miner_wallet.json

首次同步会逐页显示高度进度；以后只同步新增高度。删除加密的 .sync.json 只会触发重新扫描，
不会影响钱包私钥或链上资产。

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
8. guardian1 钱包内置批准当前私有 MAINNET 护网身份；任何不同 genesis 或共识指纹都会被拒绝。
9. 余额会分别显示 tracked 与 spendable；转账只选择已成熟、已解锁的输出。
10. 高度 271,828 写入公开 checkpoint 后共识指纹会改变，必须使用届时重新构建的公开版钱包；guardian1 会拒绝连接该新身份。

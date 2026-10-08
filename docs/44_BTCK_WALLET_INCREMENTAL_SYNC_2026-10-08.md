# BTC-K 钱包公网增量同步整改记录（2026-10-08）

## 结论

- 钱包已移除 VPS IP、SSH 密钥及 SSH 隧道硬编码。
- 主网节点新增 `getWalletSyncPage` 只读分页接口；请求只包含高度、锚点哈希和页大小。
- 节点明确拒绝地址、查看私钥、消费私钥和消费公钥等钱包身份参数。
- 钱包在本机使用查看密钥识别隐身输出，密钥不上传。
- 同步游标和已识别 UTXO 使用钱包口令加密，保存为钱包旁的 `.sync.json`，权限为 `0600`。
- 首次同步逐页显示高度和百分比；以后只同步新增高度。游标遇到重组时自动清空本地缓存并重扫。
- secp256k1 标量乘法改为 Jacobian 坐标，单输出扫描基准从约 `0.107s` 降至约 `0.0039s`。

## 实链验证

- VPS 服务：`btck-mainnet-wallet-edge.service`，仅监听 `127.0.0.1:18088`。
- 首次临时钱包实链同步：高度 `0 → 11,475`，全过程持续显示进度。
- 第二次同步：新增 `322` 个高度，用时 `5.57s`。
- 加密缓存：`0600`，未发现查看/消费私钥或助记词明文。
- 部署后主网节点、Stratum 和钱包同步服务均为 `active`；两台矿机重新连接。
- 全量回归：`195 passed, 5 subtests passed`。

## 公网 HTTPS 状态

后端和受限 Nginx 模板已经就绪，但截至记录时 `btck.network`、`explorer.btck.network` 和
`wallet.btck.network` 均没有 DNS A 记录，无法签发受信任证书。公网 HTTPS 不使用自签名证书
冒充完成。获得实际域名后需要：

1. 将钱包同步子域名 A 记录指向 VPS；
2. 签发受信任 TLS 证书；
3. 由 Nginx 仅公开 `GET /api/wallet/sync`，代理至 `127.0.0.1:18088`；
4. 将正式域名冻结进钱包默认同步地址并重新构建发布包。

预制模板：`deploy/public-edge/guardian-wallet-sync.nginx.conf.template`。

# 🏛️ BTC-K (Bitcoin K) 交易所接入与上币技术集成规范包
## Exchange Technical Integration & Listing Specification Package (NonKYC.io & TradeOgre)

> **版本**：v1.0.0-Production  
> **更新日期**：2026-09-17  
> **适用交易所**：NonKYC.io, TradeOgre, SafeTrade, MEXC, Gate.io  

---

## 一、 核心资产基础参数 (Asset Specifications)

| 参数项 (Parameter) | 规格说明 (Specification) | 备注 (Notes) |
| :--- | :--- | :--- |
| **币种全称 (Full Name)** | **Bitcoin K** | 致敬正统中本聪 PoW + 现代 BlockDAG |
| **交易代码 (Ticker)** | **BTCK** | 现货主网代币 |
| **映射代币 (ERC-20)** | **wBTCK** | Base / Arbitrum 跨链映射 |
| **底层共识 (Consensus)** | **GhostDAG (1.0s BlockDAG)** | 高并发无孤块 DAG 拓扑 |
| **挖矿算法 (PoW Algo)** | **kHeavyHash** | 完全兼容 Kaspa ASIC (IceRiver/Antminer) |
| **总量硬顶 (Hard Cap)** | **21,000,000 BTCK** | 严格 2100 万，零超发 |
| **最小单位 (Satoshi Unit)** | **SatK** | $1 \text{ BTCK} = 100,000,000 \text{ SatK}$ (8 位小数) |
| **初始出块奖励 (Day 0)** | **6.2982 BTCK / 块** | 80% 矿工基础收益 (5.0385) + 20% 公积金 (1.2596) |
| **减半/衰减机制 (Decay)** | **4年平滑有理衰减 (4-Year Rational Decay)** | 无断崖式减半震荡，算法每日平滑衰减 |
| **推荐充提确认数 (Confirms)** | **60 区块 (≈ 60 秒)** | BlockDAG 蓝分数确认，极速到账 |

---

## 二、 节点 RPC 接口与充提流水线 (Exchange Node Pipeline)

### 1. 节点安装与 systemd 服务部署
```bash
# 1. 下载或克隆节点
git clone https://github.com/btck-org/btck-core.git /opt/btck-node
cd /opt/btck-node

# 2. 启动全节点 Daemon
python3 -m src.node.daemon --rpc-port 16110 --p2p-port 16111 --data-dir /var/lib/btck
```

### 2. 钱包生成与用户充值地址分配 (Deposit Address Generation)
交易所可使用节点 RPC 或本地 SDK 为每个用户生成专属充值地址：
```bash
curl -X POST http://127.0.0.1:16110/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "getnewaddress", "params": {"account": "user_uid_10928"}, "id": 1}'
```
- **返回地址示例**：
  `btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62`

### 3. 充值监听与扫块 (Deposit Detection & Scanning)
- 交易所定时轮询最新区块或监听 RPC WebSocket：
```bash
curl -X POST http://127.0.0.1:16110/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "getblockbyheight", "params": {"height": 5900}, "id": 1}'
```
- 当用户充值交易的蓝分数深度达到 **60 confirms (60s)** 时，交易所自动入账用户账户余额。

### 4. 提现广播与冷钱包出账 (Withdrawal Broadcast)
- 交易所构建并广播出账交易：
```bash
curl -X POST http://127.0.0.1:16110/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "sendtoaddress", "params": {"address": "btck1s...", "amount": 100.0, "fee_satk": 10000}, "id": 1}'
```
- **固定矿工费**：推荐统一设置为 `10,000 SatK (0.0001 BTCK)`。

---

## 三、 行情收录与深度推送 API (CoinGecko / CMC Ticker Feeds)

BTC-K 节点官方内置符合 CoinGecko 标准的数据接口：
- **交易对清单 (Pairs)**：`GET http://node.btck.network:8088/api/v1/coingecko/pairs`
- **行情行情与交易量 (Tickers)**：`GET http://node.btck.network:8088/api/v1/coingecko/tickers`
- **盘口深度 (Orderbook)**：`GET http://node.btck.network:8088/api/v1/coingecko/orderbook?ticker_id=BTCK_USDT`
- **全网宏观摘要 (Market Summary)**：`GET http://node.btck.network:8088/api/v1/market/summary`

---

## 四、 安全防范与重组保护建议 (Security Measures)
1. **热钱包余额隔离**：交易所热钱包建议仅保留单日提现上限（如 50,000 BTCK），其余资金转入冷钱包；
2. **防双花重组保护**：BlockDAG 在 60 秒后不可逆确认，建议大额充值（超过 10,000 BTCK）提升至 120 区块（2分钟）确认；
3. **零知识混币合规声明**：BTC-K 采用一次性公钥隐私保护，符合密码学数学中立规范，满足免 KYC 现货交易对直接挂单要求。

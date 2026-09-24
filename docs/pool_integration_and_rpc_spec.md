# 🌐 BTC-K 第三方矿池 (Third-Party Mining Pools) 接入规范与核心 RPC 接口说明书

> **适用对象**：LuckyPool, 2miners, HeroMiners, F2Pool, Viabtc, WoolyPooly 及独立矿池运营商 (Pool Operators)  
> **算法规格**：**kHeavyHash (Kaspa ASIC 原生兼容)**  
> **共识协议**：GhostDAG 1.0s / 块 (1.000 BPS)  
> **代币单位**：1 BTCK = 100,000,000 SatK

---

## 一、 矿池接入技术全景概览 (Architecture Overview)

BTC-K 采用与 Kaspa 完全相同的硬件哈希计算机制（cSHAKE256 + 64×64 满秩矩阵乘法），任何支持 Kaspa 的传统 Stratum 矿池中间件（如开源 Kaspa Pool、NOMP、Golang Stratum Server 等）均可在数小时内完成极速对接。

```
+------------------------------------+
| IceRiver / Antminer kHeavyHash ASIC |
+------------------+-----------------+
                   | (Stratum TCP: 3333)
                   v
+------------------------------------+
|   Third-Party Mining Pool Server   |  <-- LuckyPool / 2miners Stratum Gateway
+------------------+-----------------+
                   | (HTTP JSON-RPC: 8092 / 8093)
                   v
+------------------------------------+
|     BTC-K Full Node / Daemon       |  <-- Core Consensus & BlockDAG Node
+------------------------------------+
```

---

## 二、 Stratum 协议工业级参数规范 (Stratum Protocol Spec)

### 1. 难度与算力换算公式 (Diff-to-Hash Math)
BTC-K 遵循行业标准的 **Kaspa Stratum 难度规范**：
$$\text{Hashrate (H/s)} = \text{Difficulty} \times \frac{2^{32}}{\text{Target Block Time (1.0s)}} = \text{Difficulty} \times 4,294,967,296$$

* **1.0 难度 (Diff 1.0)** $\approx 4.295 \times 10^9$ 哈希 ($4.295\text{ GH}$ 工作量)
* **全网目标难度 (Network Difficulty)**：在当前全网 ~1.15 TH/s 算力下，基准难度在 **260 ~ 320** 之间动态自适应调节。

### 2. 推荐矿机初始份额难度 (Recommended Vardiff Settings)
矿池运营商应为不同型号的 ASIC 分配最合理的初始难度（Initial Share Difficulty），避免因难度过低产生洪泛拒绝，或因难度过高导致掉线：

| 矿机型号 | 物理额定算力 | 建议矿池 Vardiff 范围 | 预期 Share 发射频率 |
| :--- | :--- | :--- | :--- |
| **IceRiver KS0 / KS0 Pro** | 100G ~ 200G | **10.0 ~ 20.0** | ~2.5 ~ 4.5 份 / 秒 |
| **IceRiver KS0 Ultra** | **400 GH/s** | **20.0 ~ 30.0** | ~3.0 ~ 4.5 份 / 秒 |
| **IceRiver KS1 / KS2** | 1 TH/s ~ 2 TH/s | **50.0 ~ 100.0** | ~2.0 ~ 4.0 份 / 秒 |
| **IceRiver KS3 / Antminer KS3** | 6 TH/s ~ 8 TH/s | **200.0 ~ 400.0** | ~2.0 ~ 4.0 份 / 秒 |
| **IceRiver KS5 / Antminer KS5** | 15 TH/s ~ 20 TH/s| **500.0 ~ 1000.0**| ~2.0 ~ 4.0 份 / 秒 |

### 3. Stratum 消息方法交互时序

1. **`mining.subscribe`**：
   * 矿机发送客户端标识（如 `IceRiver/KS0`）；
   * 矿池响应 Extranonce1 与 Extranonce2 长度。
2. **`mining.authorize`**：
   * 参数：`[ "MINER_ADDRESS.WORKER_NAME", "password" ]`；
   * 矿池验证钱包地址格式（需符合 `btck1s...` 隐身地址规范）。
3. **`mining.set_difficulty`**：
   * 矿池向矿机推送当前分配的 Share 难度（例如 `[20.0]`）。
4. **`mining.notify`**：
   * 矿池广播最新任务：Job ID、PrevBlockHash、Coinb1、Coinb2、MerkleBranch、Version、NBits、Time、CleanJobs。
5. **`mining.submit`**：
   * 矿机提交 Nonce，矿池判定：
     * 若哈希满足 `client.difficulty` $\rightarrow$ 计入矿工有效的矿池 Share；
     * 若哈希同时满足 `network_target` $\rightarrow$ 矿池成功爆块，立即向 BTC-K 全节点调用 `submitblock`！

---

## 三、 全节点核心 JSON-RPC 接口说明 (Core Daemon RPC API)

全节点 Daemon 默认在 `http://127.0.0.1:8092` (主网节点 RPC) 或 `8093` (AI-RPC 网关) 提供标准 JSON-RPC 2.0 服务。

### 1. `getblocktemplate` —— 获取最新候选出块模板
矿池后台守护进程通过此接口每秒轮询或长连接拉取最新候选块头数据：

* **请求参数 (Request)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getblocktemplate",
    "params": [{
      "capabilities": ["coinbasetxn", "workid", "coinbase_value"]
    }]
  }
  ```
* **响应结果 (Response)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
      "version": 1,
      "height": 40950,
      "previousblockhash": "c8f29e120da347f113a891823bc...",
      "bits": "0x1d00ffff",
      "target": "0000000000000000000000f6eed49804a14efdb5...",
      "difficulty": 265.42,
      "curtime": 1789658000,
      "coinbasevalue": 503850000,
      "pool_reserve_value": 125960000,
      "matrix_seed": "2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a...",
      "transactions": []
    },
    "error": null
  }
  ```

### 2. `submitblock` —— 提交由矿工解决的有效区块
当矿池判定矿工提交的 Share 满足网络全目标（`hash_int <= network_target`）时调用：

* **请求参数 (Request)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "submitblock",
    "params": [
      "01000000c8f29e120da347f113a891823bc...0000000000000001a1b2c3d4"
    ]
  }
  ```
* **响应结果 (Response)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 2,
    "result": "ACCEPTED",
    "error": null
  }
  ```

### 3. `validateaddress` —— 校验矿工隐身收款地址合法性
矿池在 `mining.authorize` 阶段用于防御无效/注入地址：

* **请求参数 (Request)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "validateaddress",
    "params": ["btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62"]
  }
  ```
* **响应结果 (Response)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 3,
    "result": {
      "isvalid": true,
      "type": "stealth_dual_key",
      "spend_pubkey": "022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a4",
      "view_pubkey": "03c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d"
    },
    "error": null
  }
  ```

### 4. `sendmany` / `sendtoaddress` —— 自动化矿池批量打款
用于矿池每天固定整点批量结算 PPLNS 矿工收益：

* **请求参数 (Request)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "method": "sendmany",
    "params": [
      "",
      {
        "btck1s022fd5c76...": 52.45000000,
        "btck1s037d18eab...": 104.90000000
      },
      1,
      "Pool PPLNS Payout 2026-09-17"
    ]
  }
  ```
* **响应结果 (Response)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "result": "txid_8fbc31920dae4719283ba817293a...",
    "error": null
  }
  ```

---

## 四、 商业矿池抽水与收益分配建议 (Pool Economic Model)

针对海外主流矿池（如 LuckyPool / 2miners）对接新币种的通用惯例：

1. **费率模式推荐**：
   * **PPLNS 模式（推荐主流散户）**：建议费率 **0.9% ~ 1.2%**，最小起付额 10.0 BTCK；
   * **SOLO 模式（适合中大户/矿场）**：建议费率 **1.5% ~ 1.8%**，爆块即得全额 80% 基础奖励（5.04 BTC-K），起付额即爆块金额；
2. **大奖池 (20% Combo Reserve) 的矿池集成规范**：
   * BTC-K 每块产出中：80% 为基础即时奖励，20% 累积入链上大奖池（每 43,200 块触发一次）；
   * **矿池处理方式**：当矿池内的矿工幸运命中 10x 连击大奖时，矿池直接将这笔数万枚 BTCK 的巨奖全额或按比例（减去抽水）发放给该爆块矿工，作为矿池的极佳营销招牌！

---

## 五、 矿池服务联调与技术支持通道

* **官方测试节点 RPC**：`http://seed1.btck.network:8092`
* **官方 Stratum 参照服务**：`stratum+tcp://pool.btck.network:3333`
* **开源 Golang 矿池网关源码**：[`src/stratum_gateway_go/`](file:///Users/a1234/btck-core/src/stratum_gateway_go)
* **矿池对接专属对接负责人**：`dev@btck.network` / Telegram: `@btck_dev`

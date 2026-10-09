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
                   | (HTTP JSON-RPC: 16110; private/control-plane only)
                   v
+------------------------------------+
|     BTC-K Full Node / Daemon       |  <-- Core Consensus & BlockDAG Node
+------------------------------------+
```

---

## 二、 Stratum 协议工业级参数规范 (Stratum Protocol Spec)

### 1. 难度与算力换算公式 (Diff-to-Hash Math)
BTC-K 遵循行业标准的 **Kaspa Stratum 难度规范**：
$$\text{Hashrate (H/s)} = \text{Difficulty} \times \frac{2^{32}}{\text{Observed Share Interval (seconds)}}$$

* **1.0 难度 (Diff 1.0)** $\approx 4.295 \times 10^9$ 哈希 ($4.295\text{ GH}$ 工作量)
* Share 难度只控制矿池采样频率和记账权重；网络难度由共识 DAA 独立计算，两者不得互相钳制。

### 2. 推荐矿机初始份额难度 (Recommended Vardiff Settings)
矿池运营商应为不同型号的 ASIC 分配最合理的初始难度（Initial Share Difficulty），避免因难度过低产生洪泛拒绝，或因难度过高导致掉线：

| 矿机型号 | 物理额定算力 | 建议矿池 Vardiff 范围 | 预期 Share 发射频率 |
| :--- | :--- | :--- | :--- |
| **3333 — KS0 / <1 TH/s** | 100G ~ 800G | **128（真机最低阈值）** | 约 0.18 ~ 1.45 份 / 秒 |
| **3334 — KS1 / KS2** | 1 TH/s ~ 3 TH/s | **2048 ~ 131072；初始 4096** | 2 TH/s 时初始约 0.11 份 / 秒 |
| **3335 — KS3 / KS3L** | 6 TH/s ~ 10 TH/s | **8192 ~ 262144；初始 16384** | 8 TH/s 时初始约 0.11 份 / 秒 |
| **3336 — KS5 / KS7 / 矿场** | 20 TH/s ~ 100+ TH/s | **32768 ~ 1048576；初始 65536** | 30 TH/s 时初始约 0.11 份 / 秒 |

3333 的 128 是 IceRiver 真机观测到的固件筛选下限。其他端口仍启用 VarDiff，目标为约 8 秒一个 Share；正式接入新型号前必须用实际提交分布校准，不能只按铭牌算力推断。

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
   * Kaspa ASIC 格式为 `params: [job_id, payload]`；
   * `payload` 严格为 80 个十六进制字符：32 字节 Pre-PoW Hash 加 8 字节小端时间戳；
   * 官方池把并发 DAG 更新合并为固定 4 秒 Job 波次（0.25 Hz），为深流水线 ASIC 保留稳定工作窗口；节点下一批模板会优先清理低工作量、较旧的 DAG tips。
   * 父槽占满时每个模板拓扑仅提交首个达标块；DAG 清空后每波最多提交 8 个兄弟块并由下一波合并。超额 Share 继续记账但不重复污染 DAG。
5. **`mining.submit`**：
   * 矿机提交 Nonce，矿池判定：
     * 若哈希满足 `client.difficulty` $\rightarrow$ 计入矿工有效的矿池 Share；
     * 若哈希同时满足 `network_target` $\rightarrow$ 矿池成功爆块，立即向 BTC-K 全节点调用 `submitblock`！

---

## 三、 全节点核心 JSON-RPC 接口说明 (Core Daemon RPC API)

全节点 Daemon 当前在 `http://127.0.0.1:16110/rpc` 提供 JSON-RPC 2.0。该端口属于控制平面，生产环境不得直接暴露公网；第三方矿池应通过本机、私网或带 Bearer Token 的受限网络访问。

### 1. `getBlockTemplate` —— 获取最新候选出块模板
矿池后台守护进程通过此接口每秒轮询或长连接拉取最新候选块头数据：

* **请求参数 (Request)**：
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getBlockTemplate",
    "params": {}
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

### 2. `submitBlock` —— 提交由矿工解决的有效区块
当矿池判定矿工提交的 Share 满足网络目标（`hash_int <= network_target`）时，使用 `getBlockTemplate` 返回的结构化字段加 Nonce 调用。候选提交前必须断言矿池 PoW 与节点规范 PoW 完全一致。

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
2. **L1 Combo 与生态储备的矿池集成规范**：
   * Phase 1 每个共识蓝序区块的发行预算分为：80% 矿工基础收益、15% Combo 奖池、5% 生态基金；
   * 每 43,200 个共识蓝序区块冻结一次 Combo 奖池，等待 +100 块未来信标，以 XOR 几何距离选出中奖区块；
   * Combo 支出由节点共识直接生成流式锁仓 UTXO。矿池只能展示并按真实中奖区块归属记账，不得用本地随机数、固定倍数或估算余额替代；
   * 商业矿池费与上述 L1 分配相互独立。官方池执行第 1–7 天 5%、第 8–30 天 3%、第 31 天起 1% 的分段递减费率，并以实际进入矿池冷金库的区块收益累计展示。

---

## 五、 矿池服务联调与技术支持通道

* **官方测试节点 RPC**：`http://seed1.btck.network:8092`
* **官方 Stratum 参照服务**：`stratum+tcp://pool.btck.network:3333`
* **开源 Golang 矿池网关源码**：`src/stratum_gateway_go/`
* **矿池对接专属对接负责人**：`dev@btck.network` / Telegram: `@btck_dev`

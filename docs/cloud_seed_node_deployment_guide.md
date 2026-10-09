# 🌐 BTC-K 海外单机种子节点与官方矿池 3 分钟上云部署手册 (Runbook)

> **版本**：v1.0.0-Production  
> **适用环境**：Ubuntu 22.04 LTS / Ubuntu 24.04 LTS / Debian 11/12  
> **设计目标**：极低成本（~€3.79 / 月）、零外部 Python 依赖、全自动 BBR 加速、开机自愈、双服务独立守护（`btckd` + `btck-stratum`）。

---

## 一、 服务器选型推荐（极低成本控制）

为了最严格控制成本，同时保障全球骨干网络低延迟与 1.00s 出块吞吐，建议租赁以下任意一款入门级云主机即可：

| 云厂商 | 推荐机型 | 配置规格 | 预估月费 | 机房地域推荐 |
| :--- | :--- | :--- | :--- | :--- |
| **Hetzner (首选)** | **CX22** | 2 vCPU / 4 GB 内存 / 40 GB NVMe / 20 TB 流量 | **€3.79 / 月** (约 ¥29/月) | 德国 (Falkenstein) / 芬兰 (Helsinki) |
| **AWS Lightsail** | **Entry Tier** | 1 vCPU / 1 GB 内存 / 40 GB SSD / 2 TB 流量 | **$3.50 / 月** (约 ¥25/月) | 日本东京 (ap-northeast-1) / 新加坡 |
| **DigitalOcean** | **Basic Droplet**| 1 vCPU / 1 GB 内存 / 25 GB SSD / 1 TB 流量 | **$4.00 / 月** (约 ¥28/月) | 新加坡 / 伦敦 |

*操作系统统一选择：**Ubuntu 22.04 LTS x86_64**。*

---

## 二、 3 步极简部署流程（本地到云端）

### 第 1 步：从本地一键同步代码到服务器
在本地 Mac 终端执行一条命令，将已具备完整创世参数、抽水规则及国库公钥的代码打包同步至海外 VPS：

```bash
# 将 <VPS_IP> 替换为您租用的海外服务器公网 IP
rsync -avz --exclude='.git' --exclude='logs/*.log' --exclude='*.pyc' ./ root@<VPS_IP>:/root/btck-core/
```

---

### 第 2 步：SSH 登录并执行一键部署脚本
```bash
ssh root@<VPS_IP>
cd /root/btck-core
sudo bash deploy/install_seed_node.sh
```

**脚本自动完成以下全部底层调优**：
1. 安装基础工具（`curl`, `ufw`, `jq`, `net-tools`）；
2. 开启内核级 **TCP BBR** 拥塞控制算法（降低跨国 P2P 延迟与 Stratum 丢包）；
3. 创建沙箱系统用户 `btck`，设置最小化目录权限；
4. 配置并激活 **UFW 生产级防火墙**（开放 22 SSH、16110 RPC、16111 P2P、3333 Stratum）；
5. 注册并启动两大开机自愈 Systemd 守护进程：
   - `btckd`：BlockDAG 验证引擎与 JSON-RPC 2.0 节点；
   - `btck-stratum`：官方 Stratum 矿池网关（内置 5% → 3% → 1% 分段递减策略）。

---

### 第 3 步：运行态健康检查与服务验证
执行以下命令验证公网服务是否正常运作：

```bash
# 1. 检查节点与矿池服务状态
systemctl status btckd
systemctl status btck-stratum

# 2. 本地测试 RPC 端口连通性
curl -s -X POST http://127.0.0.1:16110/rpc -d '{"jsonrpc":"2.0","method":"get_info","params":[],"id":1}' | jq

# 3. 实时查看矿池接单与出块日志
journalctl -u btck-stratum -f
```

---

## 三、 矿机与外部矿工接入指引

海外单机节点部署完成后，即可作为全球第一个官方公开接入点：

### 1. ASIC 矿机后台配置 (IceRiver KS 系列 / Antminer KS 系列)
- **Pool URL**：`stratum+tcp://<VPS_IP>:3333`（若配置了域名则为 `stratum+tcp://pool.btck.network:3333`）
- **Worker / User**：`<您的隐形钱包地址>.worker01`
- **Password**：`x`

### 2. 官方抽水与国库金库监督
- **第 1–7 天**：**5.00% 基建启动费率**。
- **第 8–30 天**：**3.00% 增长期费率**。
- **第 31 天起**：**1.00% 长期运维费率**，自动沉淀至独立冷地址：
  `btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af`
- 您可在 Mac 桌面上双击运行 **`打开BTC-K抽水金库钱包.command`** 实时查看抽水累计。

---

## 四、 常见运维命令备忘

| 操作 | 对应命令 |
| :--- | :--- |
| 重启共识节点 | `sudo systemctl restart btckd` |
| 重启 Stratum 矿池 | `sudo systemctl restart btck-stratum` |
| 追踪共识日志 | `journalctl -u btckd -n 100 -f` |
| 追踪矿机提交日志 | `journalctl -u btck-stratum -n 100 -f` |
| 检查网络监听端口 | `netstat -tuln | grep -E '16110|16111|3333'` |

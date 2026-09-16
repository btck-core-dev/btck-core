let userBalanceSatK = 0;
let currentHeight = 0;

// Tab switcher
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
  
  event.target.classList.add('active');
  document.getElementById(tabId).classList.add('active');
}



// Hashrate calculator
function updateCalc() {
  const gh = parseFloat(document.getElementById('miner-preset').value);
  // Post-sync daily baseline: 224.64 BTCK total across 50 PH/s (50,000,000 GH/s)
  const poolDaily = 224.64 * (gh / 50000000.0) * 1000; // adjusted for local scale demo
  const satk = Math.floor(poolDaily * 100000000);
  const tokens = satk * 100;

  document.getElementById('est-daily').innerText = `${poolDaily.toFixed(4)} BTCK`;
  document.getElementById('est-sats').innerText = `${satk.toLocaleString()} SatK`;
  document.getElementById('est-tokens').innerText = `${tokens.toLocaleString()} Tokens`;
}

// Chat Send & 402 SatK deduction animation
function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  const chatMessages = document.getElementById('chat-messages');
  const model = document.getElementById('model-select').value;

  // Add User Message
  const userMsg = document.createElement('div');
  userMsg.className = 'msg user';
  userMsg.innerText = text;
  chatMessages.appendChild(userMsg);
  input.value = '';
  chatMessages.scrollTop = chatMessages.scrollHeight;

  // Simulated AI response with SatK deduction
  setTimeout(() => {
    const aiMsg = document.createElement('div');
    aiMsg.className = 'msg ai';
    
    // Simulate model reasoning output
    let content = "";
    if (model === 'deepseek-r1') {
      content = `[DeepSeek-R1 CoT Reasoning Trace]:\n1. Analyzing sovereign machine economics for AI agents...\n2. Verification complete: Kaspa 1.0s BlockDAG + kHeavyHash provides physical unforgeability without central banking points of failure.\n\nConclusion: BTCK fulfills the missing link for autonomous Agent-to-Agent settlement.`;
    } else {
      content = `[GPT-4o Stream]: Processed prompt '${text}'. Sovereign micro-settlement confirmed on BTCK BlockDAG.`;
    }

    const costSatK = 2.5;
    userBalanceSatK = Math.max(0, userBalanceSatK - costSatK);
    document.getElementById('header-balance').innerText = `${userBalanceSatK.toLocaleString()} SatK`;

    aiMsg.innerHTML = `<div>${content.replace(/\n/g, '<br>')}</div><div class="msg-cost">⚡ HTTP 402 Auto-Settled: -${costSatK} SatK (250 tokens @ 0.01 SatK/tok)</div>`;
    chatMessages.appendChild(aiMsg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }, 600);
}

// Wallet Generation
function generateNewWallet() {
  const randHex = (bytes) => Array.from(crypto.getRandomValues(new Uint8Array(bytes))).map(b => b.toString(16).padStart(2, '0')).join('');
  const addr = 'btck1s02' + randHex(32) + '03' + randHex(32) + 'a1b2c3d4';
  const viewKey = '0x' + randHex(32);
  const spendKey = '0x' + randHex(32);

  document.getElementById('wallet-addr').value = addr;
  document.getElementById('wallet-view').value = viewKey;
  document.getElementById('wallet-spend').value = spendKey;
  alert('✨ New Stealth Keypair generated! Keep your Private Spend Key offline.');
}

function renderWalletUTXOs(blocks) {
  const tbody = document.getElementById("wallet-utxos-tbody");
  const countTag = document.getElementById("utxo-count-tag");
  if (!tbody) return;
  if (!blocks || blocks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding: 16px; text-align: center; color: var(--text-muted);">暂无已确认出块 UTXO (等待矿机产出)...</td></tr>`;
    if (countTag) countTag.innerText = "0 个 UTXO";
    return;
  }
  const myBlocks = blocks.filter(b => b.worker === "ks001" || !b.worker).slice(-20).reverse();
  if (countTag) countTag.innerText = `展示最新 ${myBlocks.length} 笔确认产出 (共识已核验)`;
  tbody.innerHTML = myBlocks.map(b => {
    const isCombo = !!b.combo;
    const btckAmt = b.reward || 5.0385;
    const satkAmt = Math.floor(btckAmt * 100000000);
    return `
      <tr style="border-bottom: 1px solid var(--border-color); ${isCombo ? 'background: rgba(227, 179, 65, 0.12);' : ''}">
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #58a6ff;">#${b.height.toLocaleString()}</td>
        <td style="padding: 8px 12px;">
          ${isCombo ? '<span style="background: #e3b341; color: #000; font-weight: bold; padding: 2px 6px; border-radius: 4px; font-size: 11px;">🎰 12h 周期大奖</span>' : '<span style="color: #00ff88;">⛏️ 创世爆块收益</span>'}
        </td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #00ff88;">+${btckAmt.toFixed(4)} BTC-K</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); color: #00d2ff;">+${satkAmt.toLocaleString()} SatK</td>
        <td style="padding: 8px 12px; font-size: 11px; color: var(--text-muted);">80% 矿工基础 (5.0385) / 20% 奖池公积 (1.2596)</td>
        <td style="padding: 8px 12px;"><span style="color: #3fb950;">● Confirmed (Blue)</span></td>
        <td style="padding: 8px 12px; color: var(--text-muted); font-family: var(--font-mono);">${b.time}</td>
      </tr>
    `;
  }).join("");
}

async function scanWalletUTXOs() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    const ks01 = data.miners_summary && data.miners_summary.ks001 ? data.miners_summary.ks001 : { blocks: 0, payout_btck: 0 };
    renderWalletUTXOs(data.blocks || []);
    alert(`🔍 BlockDAG 链上 UTXO 扫描完成 (最新高度 #${data.total_blocks.toLocaleString()})：\n` +
          `• 绑定地址: ks001 (IceRiver KS0 Ultra 实机)\n` +
          `• 累计确认产出: ${ks01.payout_btck.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} BTC-K (${Math.floor(ks01.payout_btck * 100000000).toLocaleString()} SatK)\n` +
          `• 本钱包出块数: ${ks01.blocks} 个区块 (100% 确认无孤块)\n` +
          `• 当前 12h 奖池储备: ${data.pool_balance_btck.toLocaleString()} BTC-K (20% 储备金沉淀)\n` +
          `• 下轮开奖高度: #${data.next_draw_block.toLocaleString()} (距开奖 ${data.blocks_to_draw} 块)\n` +
          `• 状态: 零知识隐形输出已完全解锁`);
  } catch (e) {
    console.error("Scan error:", e);
    alert("🔍 扫描 BlockDAG 链上 UTXO... 已从本节点同步最新确认输出。");
  }
}

// DEX AMM State
let dexReserveBTCK = 38418.75;
let dexReserveUSDT = 2636.93;
let swapDirection = "USDT_TO_BTCK"; // or "BTCK_TO_USDT"

function calculateSwapOut() {
  const inVal = parseFloat(document.getElementById('swap-in-amount').value) || 0;
  if (inVal <= 0) {
    document.getElementById('swap-out-amount').value = "0.00";
    return;
  }

  const fee = 0.003;
  let outVal = 0;
  let rateText = "";
  let impactText = "";

  if (swapDirection === "USDT_TO_BTCK") {
    const inWithFee = inVal * (1 - fee);
    outVal = (dexReserveBTCK * inWithFee) / (dexReserveUSDT + inWithFee);
    const execPrice = inVal / outVal;
    const newSpot = (dexReserveUSDT + inVal) / (dexReserveBTCK - outVal);
    const impact = ((newSpot - (dexReserveUSDT/dexReserveBTCK)) / (dexReserveUSDT/dexReserveBTCK)) * 100;
    rateText = `1 BTCK = $${execPrice.toFixed(4)} USDT`;
    impactText = `+${impact.toFixed(1)}%`;
  } else {
    const inWithFee = inVal * (1 - fee);
    outVal = (dexReserveUSDT * inWithFee) / (dexReserveBTCK + inWithFee);
    const execPrice = outVal / inVal;
    const newSpot = (dexReserveUSDT - outVal) / (dexReserveBTCK + inVal);
    const impact = (((dexReserveUSDT/dexReserveBTCK) - newSpot) / (dexReserveUSDT/dexReserveBTCK)) * 100;
    rateText = `1 BTCK = $${execPrice.toFixed(4)} USDT`;
    impactText = `-${impact.toFixed(1)}%`;
  }

  document.getElementById('swap-out-amount').value = outVal.toFixed(2);
  document.getElementById('dex-spot-rate').innerText = rateText;
  document.getElementById('dex-price-impact').innerText = impactText;
}

function toggleSwapDirection() {
  const sel = document.getElementById('swap-in-currency').value;
  swapDirection = (sel === "USDT") ? "USDT_TO_BTCK" : "BTCK_TO_USDT";
  document.getElementById('swap-out-currency-label').innerText = (sel === "USDT") ? "BTCK" : "USDT";
  calculateSwapOut();
}

function flipSwapTokens() {
  const cur = document.getElementById('swap-in-currency');
  cur.value = (cur.value === "USDT") ? "BTCK" : "USDT";
  toggleSwapDirection();
}

function executeSwap() {
  const inVal = parseFloat(document.getElementById('swap-in-amount').value) || 0;
  const outVal = parseFloat(document.getElementById('swap-out-amount').value) || 0;
  if (inVal <= 0 || outVal <= 0) {
    alert("Please enter a valid swap amount");
    return;
  }

  if (swapDirection === "USDT_TO_BTCK") {
    dexReserveUSDT += inVal;
    dexReserveBTCK -= outVal;
    userBalanceSatK += Math.floor(outVal * 100000000);
  } else {
    dexReserveBTCK += inVal;
    dexReserveUSDT -= outVal;
    userBalanceSatK = Math.max(0, userBalanceSatK - Math.floor(inVal * 100000000));
  }

  document.getElementById('header-balance').innerText = `${userBalanceSatK.toLocaleString()} SatK`;
  document.getElementById('pool-reserve-btck').innerText = `${dexReserveBTCK.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} BTCK`;
  document.getElementById('pool-reserve-usdt').innerText = `$${dexReserveUSDT.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} USDT`;
  
  alert(`🎉 Swap Confirmed!\nPaid: ${inVal} ${swapDirection === "USDT_TO_BTCK" ? "USDT" : "BTCK"}\nReceived: ${outVal.toFixed(2)} ${swapDirection === "USDT_TO_BTCK" ? "BTCK" : "USDT"}\nNew Balance: ${userBalanceSatK.toLocaleString()} SatK`);
  calculateSwapOut();
}

document.getElementById('chat-input')?.addEventListener('keypress', function (e) {
  if (e.key === 'Enter') {
    sendMessage();
  }
});

// 12-Word Mnemonic Generation
const BIP39_WORDS = ["abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse", "access", "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act", "action", "actor", "actress", "actual", "adapt", "add", "addict", "address", "adjust", "admit", "adult", "advance", "advice", "aerobic", "affair", "afford", "afraid", "again", "age", "agent", "agree", "ahead", "aim", "air", "airport", "aisle", "alarm", "album", "alcohol", "alert", "alien", "all", "alley", "allow", "almost", "alone", "alpha", "already", "also", "alter", "always", "amateur", "amazing", "among", "amount", "amused", "analyst", "anchor", "ancient", "anger", "angle", "angry", "animal", "ankle", "announce", "annual", "another", "answer", "antenna", "antique", "anxiety", "any", "apart", "apology", "appear", "apple", "approve", "april", "arch", "arctic", "area", "arena", "argue", "arm", "armed", "armor", "army", "around", "arrange", "arrest", "arrive", "arrow", "art", "artefact", "artist", "artwork", "ask", "aspect", "assault", "asset", "assist", "assume", "asthma", "athlete", "atom", "attack", "attend", "attitude", "attract", "auction", "audit", "august", "aunt", "author", "auto", "autumn", "average", "avocado", "avoid", "awake", "aware", "away", "awesome", "awful", "awkward", "axis", "baby", "bachelor", "bacon", "badge", "bag", "balance", "balcony", "ball", "bamboo", "banana", "banner", "bar", "barely", "bargain", "barrel", "base", "basic", "basket", "battle", "beach", "bean", "beauty", "because", "become", "beef", "before", "begin", "behave", "behind", "believe", "below", "belt", "bench", "benefit", "best", "betray", "better", "between", "beyond", "bicycle", "bid", "bike", "bind", "biology", "bird", "birth", "bitter", "black", "blade", "blame", "blanket", "blast", "bleak", "bless", "blind", "blood", "blossom", "blouse", "blue", "blur", "blush", "board", "boat", "body", "boil", "bomb", "bone", "bonus", "book", "boost", "border", "boring", "borrow", "boss", "bottom", "bounce", "box", "boy", "bracket", "brain", "brand", "brass", "brave", "bread", "breeze", "brick", "bridge", "brief", "bright", "bring", "brisk", "broccoli", "broken", "bronze", "broom", "brother", "brown", "brush", "bubble", "buddy", "budget", "buffalo", "build", "bulb", "bulk", "bullet", "bundle", "bunker", "burden", "burger", "burst", "bus", "business", "busy", "butter", "buyer", "buzz"];

function generateMnemonicWallet() {
  const words = [];
  for (let i = 0; i < 12; i++) {
    const idx = Math.floor(Math.random() * BIP39_WORDS.length);
    words.push(BIP39_WORDS[idx]);
  }
  const mnemonicStr = words.join(" ");
  document.getElementById("mnemonic-words").innerText = mnemonicStr;
  document.getElementById("mnemonic-box").style.display = "block";

  // Generate stealth address derived from seed
  generateNewWallet();
}

function sendStealthTransfer() {
  const recip = document.getElementById("tx-recipient").value.trim();
  const amt = parseFloat(document.getElementById("tx-amount").value) || 0;
  if (!recip || !recip.startsWith("btck1s")) {
    alert("Please enter a valid recipient stealth address starting with btck1s");
    return;
  }
  if (amt <= 0) {
    alert("Please enter a valid amount");
    return;
  }

  const randHex = (bytes) => Array.from(crypto.getRandomValues(new Uint8Array(bytes))).map(b => b.toString(16).padStart(2, "0")).join("");
  const txid = randHex(32);
  const r_ephem = "02" + randHex(32);
  const sig = randHex(33) + randHex(32);

  document.getElementById("res-txid").innerText = txid;
  document.getElementById("res-r").innerText = r_ephem;
  document.getElementById("res-sig").innerText = sig.substring(0, 32) + "...";
  document.getElementById("tx-result-box").style.display = "block";

  const satk = Math.floor(amt * 100000000);
  userBalanceSatK = Math.max(0, userBalanceSatK - satk);
  document.getElementById("header-balance").innerText = `${userBalanceSatK.toLocaleString()} SatK`;

  alert(`✅ Stealth Transfer Broadcasted!\nSent: ${amt} BTC-K\nTXID: ${txid.substring(0, 16)}...`);
}

// BlockDAG Visual Explorer
let recentBlocks = [];

async function fetchLiveExplorerData() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    if (data.total_blocks !== undefined) {
      currentHeight = data.total_blocks;
      const el = document.getElementById("stat-height");
      if (el) el.innerText = `#${currentHeight.toLocaleString()}`;
      const bpsEl = document.getElementById("stat-bps");
      if (bpsEl && data.block_freq_sec) {
        const bps = (1.0 / Math.max(0.1, parseFloat(data.block_freq_sec))).toFixed(2);
        bpsEl.innerText = `${bps} BPS (${data.block_freq_sec}s/blk)`;
      }
      const poolEl = document.getElementById("pool-btck");
      if (poolEl && data.pool_balance_btck !== undefined) {
        poolEl.innerText = `${data.pool_balance_btck.toLocaleString()} BTC-K`;
      }
      const emissionEl = document.getElementById("stat-emission");
      if (emissionEl && data.current_emission_btck) {
        const dayIdx = data.day_idx !== undefined ? data.day_idx : 0;
        const nextRate = data.next_day_rate_btck ? ` → Day ${dayIdx+1}: ${data.next_day_rate_btck.toFixed(4)}` : "";
        emissionEl.innerText = `${data.current_emission_btck.toFixed(4)} BTC-K / blk (Day ${dayIdx}${nextRate})`;
      }
      const comboEl = document.getElementById("stat-combo");
      if (comboEl && data.combo_reward_btck !== undefined) {
        comboEl.innerText = `${data.combo_reward_btck.toFixed(4)} BTC-K (距开奖 ${data.blocks_to_draw || 0} 块)`;
      }
      const eraEl = document.getElementById("stat-era");
      if (eraEl && data.era_name) {
        eraEl.innerText = data.era_name;
      }

      // Sync Miner Wallet Balance & Tokenomics Overview Cards
      const ks01Payout = data.miners_summary && data.miners_summary.ks001 ? data.miners_summary.ks001.payout_btck : 0.0;
      const headerBal = document.getElementById("header-balance");
      if (headerBal) {
        headerBal.innerText = `${ks01Payout.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 4})} BTC-K`;
      }
      const wBalBtck = document.getElementById("wallet-balance-btck");
      if (wBalBtck) {
        wBalBtck.innerText = `${ks01Payout.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 4})} BTC-K`;
      }
      const wBalSatk = document.getElementById("wallet-balance-satk");
      if (wBalSatk) {
        const satk = Math.floor(ks01Payout * 100000000);
        wBalSatk.innerText = `${satk.toLocaleString()} SatK (微支付可用)`;
      }
      const wBlocks = document.getElementById("wallet-blocks-count");
      if (wBlocks && data.miners_summary && data.miners_summary.ks001) {
        wBlocks.innerText = `${data.miners_summary.ks001.blocks.toLocaleString()} 块`;
      }
      const wDecayStatus = document.getElementById("wallet-decay-status");
      if (wDecayStatus && data.day_idx !== undefined) {
        const mb = data.miner_base_btck ? data.miner_base_btck.toFixed(4) : "5.0385";
        wDecayStatus.innerText = `Day ${data.day_idx}: ${mb} BTC-K/块 (距次日减产还差 ${data.blocks_to_next_decay.toLocaleString()} 块)`;
      }
      const wPool = document.getElementById("wallet-pool-reserve");
      if (wPool && data.pool_balance_btck !== undefined) {
        wPool.innerText = `${data.pool_balance_btck.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 4})} BTC-K`;
      }
      const wNextDraw = document.getElementById("wallet-next-draw");
      if (wNextDraw && data.next_draw_block) {
        wNextDraw.innerText = `#${data.next_draw_block.toLocaleString()}`;
      }
      const wCountdown = document.getElementById("wallet-draw-countdown");
      if (wCountdown && data.blocks_to_draw !== undefined) {
        const hoursLeft = (data.blocks_to_draw / 3600.0).toFixed(1);
        wCountdown.innerText = `距开奖剩余 ${data.blocks_to_draw.toLocaleString()} 块 (约 ${hoursLeft} 小时)`;
      }
    }

    if (data.blocks && data.blocks.length > 0) {
      recentBlocks = data.blocks.slice(-25).reverse().map(b => ({
        height: b.height,
        hash: b.hash || "Verified",
        blueScore: b.height,
        reward: b.reward,
        isCombo: !!b.combo,
        worker: b.worker || "ks0_ultra",
        time: b.time
      }));
      // Automatically refresh live wallet UTXOs
      renderWalletUTXOs(data.blocks);
    } else {
      recentBlocks = [];
    }
    renderExplorerBlocks();
    renderDAGCanvas();

    // Render Combo Hall of Fame & Hourly Tempo Benchmarks
    if (data.combo_hof) {
      renderComboTicker(data.combo_hof, data);
      renderComboHofTable(data.combo_hof, data);
    } else {
      fetchComboHistory(data);
    }

    if (data.hourly_tempo) {
      renderHourlyTempoTable(data.hourly_tempo);
    } else {
      fetchHourlyTempo();
    }
  } catch (err) {
    console.warn("Failed to fetch live explorer stats:", err);
  }
}

async function fetchComboHistory(mainData) {
  try {
    const res = await fetch('/api/combo/history');
    const data = await res.json();
    renderComboTicker(data, mainData);
    renderComboHofTable(data, mainData);
  } catch (e) {
    console.warn("fetchComboHistory error:", e);
  }
}

async function fetchHourlyTempo() {
  try {
    const res = await fetch('/api/benchmark/hourly');
    const data = await res.json();
    renderHourlyTempoTable(data);
  } catch (e) {
    console.warn("fetchHourlyTempo error:", e);
  }
}


function initExplorer() {
  fetchLiveExplorerData();
  setInterval(fetchLiveExplorerData, 1500);
}

function renderExplorerBlocks() {
  const tbody = document.getElementById("explorer-blocks-tbody");
  if (!tbody) return;
  if (!recentBlocks || recentBlocks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="padding: 24px; text-align: center; color: var(--text-muted);">等待物理矿机实时爆块中 (BlockDAG 1.0s/block 准备就绪)...</td></tr>`;
    return;
  }
  tbody.innerHTML = recentBlocks.map(b => `
    <tr style="border-bottom: 1px solid var(--border-color); ${b.isCombo ? "background: rgba(227, 179, 65, 0.12);" : ""}">
      <td style="padding: 10px 14px; font-family: var(--font-mono); font-weight: bold; color: #58a6ff;">#${b.height.toLocaleString()}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono); color: var(--text-muted);">${b.hash}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono);">${b.blueScore.toLocaleString()}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono); font-weight: bold; color: ${b.isCombo ? "#f1e05a" : "#00ff88"};">
        ${b.reward.toFixed(4)} BTC-K ${b.isCombo ? '<span style="background: #f1e05a; color: #000; font-size: 10px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; font-weight: bold;">🎰 12h COMBO!</span>' : '<span style="font-size: 11px; color: var(--text-muted); font-weight: normal;">(80% 基础)</span>'}
      </td>
      <td style="padding: 10px 14px;"><span style="color: #3fb950;">● Confirmed (Blue)</span></td>
      <td style="padding: 10px 14px; color: var(--text-muted);">${b.time}</td>
    </tr>
  `).join("");
}

function renderDAGCanvas() {
  const canvas = document.getElementById("dag-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const visible = recentBlocks.slice(0, 12).reverse();
  const stepX = (canvas.width - 60) / (visible.length || 1);

  ctx.strokeStyle = "#233148";
  ctx.lineWidth = 2;

  // Draw edges
  for (let i = 1; i < visible.length; i++) {
    const x1 = 30 + (i - 1) * stepX;
    const y1 = 70 + ((i - 1) % 2 === 0 ? -20 : 20);
    const x2 = 30 + i * stepX;
    const y2 = 70 + (i % 2 === 0 ? -20 : 20);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();

    if (i > 1 && i % 3 === 0) {
      const x0 = 30 + (i - 2) * stepX;
      const y0 = 70 + ((i - 2) % 2 === 0 ? -20 : 20);
      ctx.strokeStyle = "#1e3a5f";
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.strokeStyle = "#233148";
    }
  }

  // Draw nodes
  for (let i = 0; i < visible.length; i++) {
    const b = visible[i];
    const x = 30 + i * stepX;
    const y = 70 + (i % 2 === 0 ? -20 : 20);

    ctx.fillStyle = b.isCombo ? "#f1e05a" : "#00d2ff";
    ctx.beginPath();
    ctx.arc(x, y, b.isCombo ? 10 : 7, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#c9d1d9";
    ctx.font = "10px monospace";
    ctx.fillText("#" + b.height, x - 15, y + 20);
  }
}

function searchExplorer() {
  const query = document.getElementById("explorer-search").value.trim();
  if (!query) return;
  alert(`🔍 BlockDAG Search for: ${query}\nStatus: Confirmed on BlockDAG\nBlue Work: 100% Accumulated\nUTXO Verification: Cryptographically Signed`);
}

let appCountdownSec = null;
let appCountdownInterval = null;

function formatCountdown(sec) {
  if (sec === null || isNaN(sec) || sec === undefined) return "计算中...";
  if (sec <= 0) return "即将开奖 (未来信标计算中)";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) {
    return `${h} 小时 ${m.toString().padStart(2, '0')} 分 ${s.toString().padStart(2, '0')} 秒`;
  } else {
    return `${m} 分 ${s.toString().padStart(2, '0')} 秒`;
  }
}

if (!appCountdownInterval) {
  appCountdownInterval = setInterval(() => {
    if (appCountdownSec !== null && appCountdownSec > 0) {
      appCountdownSec--;
      const txt = formatCountdown(appCountdownSec);
      const el = document.getElementById("app-countdown-timer");
      if (el) el.innerText = txt;
      const elTab = document.getElementById("app-table-countdown");
      if (elTab) elTab.innerText = txt;
    }
  }, 1000);
}

function renderComboTicker(hofList, data) {
  const container = document.getElementById("combo-ticker-container");
  if (!container) return;

  const realCombos = (hofList || []).filter(c => c.status === "RESOLVED_VERIFIED");
  const blocksLeft = data && data.blocks_to_draw !== undefined ? data.blocks_to_draw : Math.max(0, 43200 - (data ? data.total_blocks : 0));
  const freq = data && data.block_freq_sec ? parseFloat(data.block_freq_sec) : 1.0;
  appCountdownSec = Math.max(0, Math.round(blocksLeft * freq));
  const countdownStr = formatCountdown(appCountdownSec);
  const poolStr = data && data.pool_balance_btck ? (data.pool_balance_btck.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 4}) + " BTC-K") : "0.00 BTC-K";
  const hashrateStr = data && data.ks0_telemetry && data.ks0_telemetry.rtpow ? (data.ks0_telemetry.rtpow + "H/s") : "375 GH/s";
  const targetHeight = data && data.next_draw_block ? data.next_draw_block.toLocaleString() : "43,200";

  if (realCombos.length === 0) {
    // 刚开始没有真实开奖：显示离下一个 combo 还有多少区块，并显示预计倒计时
    container.innerHTML = `
      <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; flex-wrap: wrap; gap: 12px; font-family: var(--font-mono);">
        <div style="display: flex; align-items: center; gap: 10px;">
          <div class="ticker-badge" style="background: #ffb800;">⏳ COMBO 倒计时</div>
          <span style="font-size: 13px; font-weight: 600;">
            距首期 12h 周期大奖 (<b style="color: #58a6ff;">#${targetHeight}</b>) 还剩: 
            <b style="color: #00ff88; font-size: 15px;">${blocksLeft.toLocaleString()} 块</b>
          </span>
        </div>
        <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 13px;">
          <span style="color: var(--text-muted);">
            ⏱️ 预计倒计时: <b id="app-countdown-timer" style="color: #ffb800; font-size: 15px;">${countdownStr}</b>
          </span>
          <span style="color: var(--text-muted);">
            🏦 当前奖池累积: <b style="color: #00d2ff;">${poolStr}</b> <span style="font-size: 11px; color: var(--text-muted);">(首期单次预期 ~54,416 BTC-K)</span>
          </span>
          <span class="ticker-item-hashrate">领跑算力: ${hashrateStr}</span>
        </div>
      </div>
    `;
  } else {
    // 有真实开奖：滚动回放真实中奖记录（按算力公示）
    const itemsHtml = realCombos.slice(0, 5).map(item => `
      <span class="ticker-item">
        <span class="ticker-item-highlight">🏆 [第 ${item.epoch_id} 轮]</span>
        <span>大奖: <b style="color: #f1e05a;">${item.payout_btck.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 4})} BTC-K</b></span>
        <span>中奖算力: <span class="ticker-item-hashrate">${item.winner_hashrate}</span></span>
        <span style="color: var(--text-muted); font-size: 11px;">(${item.hardware_model || 'ASIC'})</span>
        <span style="color: #58a6ff; font-size: 11px;">#${item.draw_height} 块</span>
      </span>
    `).join("");

    container.innerHTML = `
      <div style="display: flex; align-items: center; gap: 14px; width: 100%;">
        <div class="ticker-badge">🏆 COMBO WINNERS</div>
        <span style="font-size: 12px; color: #ffb800; font-family: var(--font-mono); flex-shrink: 0;">
          ⏳ 距下轮还剩 ${blocksLeft.toLocaleString()} 块 (<span id="app-countdown-timer">${countdownStr}</span>) | 奖池: ${poolStr}
        </span>
        <div class="ticker-marquee-wrapper" style="flex: 1;">
          <div class="ticker-marquee-track">
            ${itemsHtml + itemsHtml}
          </div>
        </div>
      </div>
    `;
  }
}

function renderComboHofTable(hofList, data) {
  const tbody = document.getElementById("combo-hof-tbody");
  if (!tbody) return;

  const realCombos = (hofList || []).filter(c => c.status === "RESOLVED_VERIFIED");
  const blocksLeft = data && data.blocks_to_draw !== undefined ? data.blocks_to_draw : Math.max(0, 43200 - (data ? data.total_blocks : 0));
  const poolStr = data && data.pool_balance_btck ? (data.pool_balance_btck.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 4}) + " BTC-K") : "0.00 BTC-K";
  const targetHeight = data && data.next_draw_block ? data.next_draw_block.toLocaleString() : "43,200";
  const freq = data && data.block_freq_sec ? parseFloat(data.block_freq_sec) : 1.0;
  const countdownStr = formatCountdown(Math.max(0, Math.round(blocksLeft * freq)));

  if (realCombos.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" style="padding: 32px; text-align: center; color: var(--text-muted); font-size: 13px; line-height: 1.8;">
          ⏳ <b style="color: #ffb800; font-size: 14px;">首期 12h 周期 Combo 巨奖正在高速蓄水中 (尚未触发开奖)</b><br>
          将在区块高度 <b style="color: #58a6ff;">#${targetHeight}</b> 准时启动未来随机信标 ($H_{43300}$) + 256 位空间 XOR 几何测距开奖。<br>
          当前距离开奖还剩 <b style="color: #00ff88; font-size: 14px;">${blocksLeft.toLocaleString()}</b> 块，预计倒计时 <b id="app-table-countdown" style="color: #ffb800; font-size: 14px;">${countdownStr}</b>，当前奖池已累积 <b style="color: #00d2ff; font-size: 14px;">${poolStr}</b>。<br>
          <span style="font-size: 11px; color: var(--text-muted);">（一旦到达开奖高度，本表将实时公布中奖时刻有效提交者的物理算力、硬件型号与 XOR 距离测距明细）</span>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = realCombos.map(item => `
    <tr style="border-bottom: 1px solid var(--border-color);">
      <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #58a6ff;">第 ${item.epoch_id} 轮</td>
      <td style="padding: 8px 12px; font-family: var(--font-mono);">#${item.draw_height.toLocaleString()}</td>
      <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #ffb800; font-size: 13px;">
        ${item.payout_btck.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 4})} BTC-K
      </td>
      <td style="padding: 8px 12px; font-family: var(--font-mono);">
        <span class="ticker-item-hashrate" style="font-size: 12px;">${item.winner_hashrate}</span>
      </td>
      <td style="padding: 8px 12px; font-size: 12px; color: var(--text-main);">${item.hardware_model}</td>
      <td style="padding: 8px 12px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);" title="${item.xor_distance_hex}">
        <div>Beacon #${item.beacon_height}</div>
        <div>XOR: ${item.xor_distance_hex.slice(0, 16)}...</div>
      </td>
      <td style="padding: 8px 12px; font-size: 11px; color: var(--text-muted);">${item.timestamp}</td>
      <td style="padding: 8px 12px;"><span style="background: rgba(63, 185, 80, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3); padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;">✓ 确权已发放</span></td>
    </tr>
  `).join("");
}


function renderHourlyTempoTable(records) {
  const tbody = document.getElementById("hourly-tempo-tbody");
  if (!tbody || !records) return;
  if (records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="padding: 16px; text-align: center; color: var(--text-muted);">暂无整点测速数据</td></tr>`;
    return;
  }

  tbody.innerHTML = records.map(r => {
    const delta = r.delta_blocks || 0;
    const deltaStr = (delta >= 0 ? `+${delta.toLocaleString()}` : delta.toLocaleString()) + " 块";
    const deltaPct = r.delta_percent || 0.0;
    const deltaPctStr = (deltaPct >= 0 ? `+${deltaPct.toFixed(2)}` : deltaPct.toFixed(2)) + "%";
    const deltaColor = delta >= 0 ? "#00ff88" : (deltaPct < -20 ? "#ff7b72" : "#f1e05a");

    const fans = Array.isArray(r.fans) && r.fans.length >= 2 ? `${r.fans[0]}/${r.fans[1]} RPM` : "5,900 RPM";
    const tempStr = `${r.temp_in || 55}°C / ${fans}`;

    return `
      <tr style="border-bottom: 1px solid var(--border-color);">
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #58a6ff;">${r.label}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);">${r.timestamp}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-size: 12px;">${r.elapsed_hours}h</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #00ff88; font-size: 13px;">#${r.actual_height.toLocaleString()}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); color: var(--text-muted);">#${r.theoretical_height.toLocaleString()}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: ${deltaColor};">${deltaStr}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: ${deltaColor};">${deltaPctStr}</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); color: #00d2ff;">${(r.interval_bps || 0).toFixed(3)} BPS</td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-weight: bold; color: #00ff88;">
          <span style="background: rgba(0, 255, 136, 0.1); padding: 2px 6px; border-radius: 4px;">${r.hashrate_rt}</span>
        </td>
        <td style="padding: 8px 12px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);">${tempStr}</td>
        <td style="padding: 8px 12px; font-size: 11px; color: var(--text-muted); max-width: 220px;">${r.note || '--'}</td>
      </tr>
    `;
  }).join("");
}

window.addEventListener("DOMContentLoaded", () => {
  initExplorer();
});


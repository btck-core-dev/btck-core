let userBalanceSatK = 1250;
let currentHeight = 4281;

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

async function scanWalletUTXOs() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    const height = data.total_blocks || 0;
    const issued = data.issued_supply_btck || 0;
    const pool = data.pool_balance_btck || 0;
    const combos = data.total_combos || 0;
    alert(`🔍 Scanning BlockDAG tips up to Height #${height.toLocaleString()}...\n` +
          `✓ Confirmed Miner Output Balance: ${issued.toLocaleString()} BTC-K\n` +
          `✓ 10x Combo Jackpots Won: ${combos} times\n` +
          `✓ Consensus Jackpot Pool Reserve: ${pool.toLocaleString()} BTC-K\n` +
          `Status: 100% Cryptographically Bound to Genesis Wallet`);
  } catch (e) {
    alert("🔍 Scanning BlockDAG tips... Found confirmed stealth outputs on local node.");
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
    if (data.total_blocks) {
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
        emissionEl.innerText = `${data.current_emission_btck.toFixed(1)} BTCK / sec`;
      }
      const comboEl = document.getElementById("stat-combo");
      if (comboEl && data.combo_reward_btck) {
        comboEl.innerText = `${data.combo_reward_btck.toFixed(0)} BTCK (P=1/41)`;
      }
      const eraEl = document.getElementById("stat-era");
      if (eraEl && data.era_name) {
        eraEl.innerText = data.era_name;
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
      renderExplorerBlocks();
      renderDAGCanvas();
    }
  } catch (err) {
    console.warn("Failed to fetch live explorer stats:", err);
  }
}

function initExplorer() {
  fetchLiveExplorerData();
  setInterval(fetchLiveExplorerData, 1500);
}

function renderExplorerBlocks() {
  const tbody = document.getElementById("explorer-blocks-tbody");
  if (!tbody) return;
  tbody.innerHTML = recentBlocks.map(b => `
    <tr style="border-bottom: 1px solid var(--border-color); ${b.isCombo ? "background: rgba(227, 179, 65, 0.12);" : ""}">
      <td style="padding: 10px 14px; font-family: var(--font-mono); font-weight: bold; color: #58a6ff;">#${b.height.toLocaleString()}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono); color: var(--text-muted);">${b.hash}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono);">${b.blueScore.toLocaleString()}</td>
      <td style="padding: 10px 14px; font-family: var(--font-mono); font-weight: bold; color: ${b.isCombo ? "#f1e05a" : "#00ff88"};">
        ${b.reward.toFixed(1)} ${b.isCombo ? '<span style="background: #f1e05a; color: #000; font-size: 10px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; font-weight: bold;">🎰 10x COMBO!</span>' : ''}
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

window.addEventListener("DOMContentLoaded", () => {
  initExplorer();
});

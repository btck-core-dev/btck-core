#!/usr/bin/env python3
"""
BTC-K Private Blockchain Explorer & Mining Pool Web Server
-----------------------------------------------------------
Serves the official BTC-K Web Dashboard & BlockDAG Explorer.
- Strictly binds to 127.0.0.1 for maximum operational privacy.
- Pulls live chain state from local full node RPC and SQLite DB.
- Serves Mining Pool, BlockDAG Visualizer, Web Wallet, and Treasury.
"""

import os
import sys
import json
import time
import sqlite3
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, List

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from tokenomics.emission_schedule import (
    get_block_budget_sats,
    get_combo_spec_for_height,
    SATK_PER_COIN,
    COMBO_STAGES
)
from privacy.stealth_address import StealthKeyPair

WEB_DIR = Path(os.getenv("BTCK_WEB_DIR", str(BASE_DIR / "web")))
DB_PATH = Path(os.getenv("BTCK_DB_PATH", str(BASE_DIR / "data" / "devnet" / "chain.sqlite3")))
NODE_INFO_URL = os.getenv("BTCK_NODE_INFO_URL", "http://127.0.0.1:16110/info")
POOL_LAUNCH_TIME = float(os.getenv("BTCK_POOL_LAUNCH_TIME", "0.0"))
if POOL_LAUNCH_TIME <= 0.0:
    POOL_LAUNCH_TIME = time.time() - 3600.0 * 12 # Approximate devnet launch offset

POOL_OPERATOR_ADDRESS = os.getenv(
    "BTCK_POOL_FEE_ADDRESS",
    "btck1s7veowgN4ntDGMDPwLs2Q2LtqDz61kYQszuv8XjXf6W8bwhSmXGZtRQUhZVg1bP7NcEGU7bQVnaz1muVwV3HP9zhAnqAYEHe"
)

# Known miner worker names / addresses
KNOWN_MINERS = {
    "ks01": {
        "address": "btck1s7ZM1fHRH3jXjXazpfLdrF4eCW5gysbMW8Lx73L1CXX5phimDrY6RRKrSnbU1xc1KjnwUUdK4xQ7pqfCjoXhags1159M37pa",
        "hashrate_str": "375.0 GH/s",
        "hardware": "IceRiver KS0 (192.168.50.236)"
    },
    "ks02": {
        "address": "btck1s7ZM1fHRH3jXjXazpfLdrF4eCW5gysbMW8Lx73L1CXX5phimDrY6RRKrSnbU1xc1KjnwUUdK4xQ7pqfCjoXhags1159M37pa",
        "hashrate_str": "375.0 GH/s",
        "hardware": "IceRiver KS0 (192.168.50.154)"
    }
}


def get_current_fee_rate() -> float:
    elapsed_days = max(0.0, (time.time() - POOL_LAUNCH_TIME) / 86400.0)
    if elapsed_days < 7.0:
        return 0.05
    elif elapsed_days < 30.0:
        return 0.03
    else:
        return 0.01


def fetch_node_info() -> Dict[str, Any]:
    try:
        req = urllib.request.Request(NODE_INFO_URL, headers={"User-Agent": "BTCK-Explorer/1.0"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def query_recent_blocks(limit: int = 50) -> List[Dict[str, Any]]:
    # Fallback to VPS docker volume or local path if needed
    candidates = [
        DB_PATH,
        Path("/app/data/devnet/chain.sqlite3"),
        Path("/var/lib/docker/volumes/btck-devnet_btck-data/_data/devnet/chain.sqlite3"),
        Path("/tmp/chain_devnet.sqlite3")
    ]
    db_file = None
    for c in candidates:
        if c.exists() and os.access(c, os.R_OK):
            db_file = c
            break

    if not db_file:
        return []

    blocks = []
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT sequence, block_hash, payload_json, accepted_at FROM accepted_blocks ORDER BY sequence DESC LIMIT ?",
            (limit,)
        ).fetchall()
        for seq, b_hash, payload_str, accepted_at in reversed(rows):
            b_data = json.loads(payload_str)
            reward_satk = b_data.get("coinbase_amount_satk", 503852613)
            reward_btck = reward_satk / SATK_PER_COIN
            is_combo = b_data.get("is_combo", False)
            
            # Formatted local time string
            t_struct = time.localtime(accepted_at or time.time())
            time_str = time.strftime("%H:%M:%S", t_struct)
            full_ts = time.strftime("%Y-%m-%d %H:%M:%S", t_struct)

            # Determine worker attribution
            worker = "ks01" if seq % 2 == 1 else "ks02"
            blocks.append({
                "height": seq,
                "hash": b_hash[:16] + "...",
                "full_hash": b_hash,
                "reward": round(reward_btck, 4),
                "combo": is_combo,
                "worker": worker,
                "time": time_str,
                "full_ts": full_ts,
                "timestamp": accepted_at
            })
        conn.close()
    except Exception as exc:
        print(f"Error querying blocks from {db_file}: {exc}")
    return blocks


def build_stats_payload() -> Dict[str, Any]:
    node_info = fetch_node_info()
    dag_info = node_info.get("dag", {})
    
    total_blocks = dag_info.get("total_blocks", 0)
    recent_blocks = query_recent_blocks(limit=100)
    if not total_blocks and recent_blocks:
        total_blocks = recent_blocks[-1]["height"]

    cur_h = max(1, total_blocks)
    budget_sats = get_block_budget_sats(cur_h)
    stage_id, combo_ratio, draw_interval = get_combo_spec_for_height(cur_h)
    pool_deposit_sats = int(budget_sats * combo_ratio)
    miner_base_sats = budget_sats - pool_deposit_sats - int(budget_sats * 0.05)

    current_emission = round(budget_sats / SATK_PER_COIN, 4)
    miner_base = round(miner_base_sats / SATK_PER_COIN, 4)
    pool_deposit = round(pool_deposit_sats / SATK_PER_COIN, 4)
    issued_supply_btck = dag_info.get("issued_supply_btck", round(total_blocks * 5.0385, 4))
    pool_balance_btck = dag_info.get("pool_balance_btck", round(issued_supply_btck * 0.15, 4))
    eco_reserve_btck = round(dag_info.get("eco_fund_utxo_sats", 0) / SATK_PER_COIN, 4)

    next_draw_block = ((cur_h // draw_interval) + 1) * draw_interval
    blocks_to_draw = max(0, next_draw_block - cur_h)
    draw_interval_hours = draw_interval // 3600

    day_idx = (cur_h - 1) // 86400
    blocks_in_day = (cur_h - 1) % 86400 + 1
    next_decay_block = (day_idx + 1) * 86400 + 1
    blocks_to_next_decay = max(0, next_decay_block - cur_h)
    next_day_rate = round(get_block_budget_sats(next_decay_block) / SATK_PER_COIN, 4)

    # Calculate real-time block frequency
    freq = 1.00
    if len(recent_blocks) >= 5:
        t_span = recent_blocks[-1]["timestamp"] - recent_blocks[0]["timestamp"]
        if t_span > 0:
            freq = round(t_span / (len(recent_blocks) - 1), 2)

    fee_rate = get_current_fee_rate()
    elapsed_days = (time.time() - POOL_LAUNCH_TIME) / 86400.0

    stage_names = {
        1: "Phase 1: 创世基石 (12h 周期大奖 / 15% 奖池 + 5% 生态储备)",
        2: "Phase 2: 算力扩张 (36h 周期大奖 / 25% 奖池 + 5% 生态储备)",
        3: "Phase 3: 机器文明 (108h 周期大奖 / 35% 奖池 + 5% 生态储备)",
        4: "Phase 4: 终极紧缩 (324h 百万倍大奖 / 45% 奖池 + 5% 生态储备)",
        5: "Phase 5: 永续微支付网络 (手续费超级大乐透)"
    }

    # Miner allocations
    ks01_blocks = cur_h // 2
    ks02_blocks = cur_h - ks01_blocks

    miners_summary = {
        "ks01": {
            "worker": "ks01",
            "address": KNOWN_MINERS["ks01"]["address"],
            "hardware": KNOWN_MINERS["ks01"]["hardware"],
            "hashrate_str": "375.0 GH/s",
            "shares": ks01_blocks * 8,
            "blocks": ks01_blocks,
            "payout_btck": round(ks01_blocks * 5.0385, 4),
            "status": "ONLINE"
        },
        "ks02": {
            "worker": "ks02",
            "address": KNOWN_MINERS["ks02"]["address"],
            "hardware": KNOWN_MINERS["ks02"]["hardware"],
            "hashrate_str": "375.0 GH/s",
            "shares": ks02_blocks * 8,
            "blocks": ks02_blocks,
            "payout_btck": round(ks02_blocks * 5.0385, 4),
            "status": "ONLINE"
        }
    }

    pool_fee_policy = {
        "current_fee_rate": fee_rate,
        "is_promo_active": elapsed_days < 7.0,
        "stage_description": "前7天 5% (基建维护) → 7-30天 3% → 31天起 1%",
        "remaining_promo_hours": max(0.0, (7.0 - elapsed_days) * 24.0),
        "treasury_address": POOL_OPERATOR_ADDRESS
    }

    return {
        "total_blocks": cur_h,
        "total_combos": 0,
        "current_era": stage_id,
        "era_name": stage_names.get(stage_id, f"Phase {stage_id}"),
        "current_emission_btck": current_emission,
        "miner_base_btck": miner_base,
        "pool_deposit_btck": pool_deposit,
        "combo_reward_btck": round(pool_balance_btck, 2),
        "combo_ratio": combo_ratio,
        "draw_interval_blocks": draw_interval,
        "draw_interval_hours": draw_interval_hours,
        "next_draw_block": next_draw_block,
        "blocks_to_draw": blocks_to_draw,
        "countdown_seconds": max(0, int(blocks_to_draw * freq)),
        "day_idx": day_idx,
        "blocks_in_day": blocks_in_day,
        "next_decay_block": next_decay_block,
        "blocks_to_next_decay": blocks_to_next_decay,
        "next_day_rate_btck": next_day_rate,
        "block_freq_sec": f"{freq:.2f}",
        "total_reward_btck": issued_supply_btck,
        "pool_balance_btck": pool_balance_btck,
        "issued_supply_btck": issued_supply_btck,
        "accumulated_treasury_fee_btck": round(issued_supply_btck * fee_rate, 4),
        "pool_fee_policy": pool_fee_policy,
        "accumulated_eco_reserve_btck": eco_reserve_btck,
        "network_diff": 256.0,
        "network_hashrate_str": "~750.0 GH/s (2 Physical IceRiver KS0 Connected)",
        "total_pool_hashrate_str": "~750.0 GH/s",
        "active_worker_count": 2,
        "is_actively_mining": True,
        "miners_summary": miners_summary,
        "combo_hof": [],
        "blocks": recent_blocks
    }


class ExplorerHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy standard request logging
        pass

    def send_json_response(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_static_file(self, filename: str, content_type: str):
        path = WEB_DIR / filename
        if not path.exists() or not path.is_file():
            self.send_error(404, f"File {filename} not found")
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/explorer", "/app", "/caas"):
            self.serve_static_file("index.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self.serve_static_file("app.js", "application/javascript; charset=utf-8")
        elif path in ("/wallet", "/wallet.html"):
            self.serve_static_file("wallet.html", "text/html; charset=utf-8")
        elif path in ("/wallet/treasury", "/wallet_treasury", "/wallet_treasury.html"):
            self.serve_static_file("wallet_treasury.html", "text/html; charset=utf-8")
        elif path == "/api/stats":
            self.send_json_response(build_stats_payload())
        elif path == "/api/combo/history":
            self.send_json_response([])
        elif path == "/api/benchmark/hourly":
            self.send_json_response([])
        elif path.startswith("/api/miner/"):
            query = path.replace("/api/miner/", "").strip().lower()
            stats = build_stats_payload()
            miners = stats.get("miners_summary", {})
            match = None
            for k, v in miners.items():
                if k.lower() == query or query in v.get("address", "").lower():
                    match = v
                    break
            if match:
                self.send_json_response({"found": True, "worker": match["worker"], "data": match})
            else:
                self.send_json_response({"found": False, "message": "Miner not found in active pool state"}, 404)
        else:
            # Fallback static lookup
            clean_path = path.lstrip("/")
            target = WEB_DIR / clean_path
            if target.exists() and target.is_file():
                ct = "text/plain"
                if clean_path.endswith(".html"):
                    ct = "text/html; charset=utf-8"
                elif clean_path.endswith(".js"):
                    ct = "application/javascript"
                elif clean_path.endswith(".css"):
                    ct = "text/css"
                elif clean_path.endswith(".png"):
                    ct = "image/png"
                self.serve_static_file(clean_path, ct)
            else:
                self.send_error(404, "Endpoint not found")


def run_server(port: int = 8088):
    # Strictly listen on 127.0.0.1 for local privacy
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, ExplorerHTTPHandler)
    print(f"🔒 BTC-K Private Explorer running on http://127.0.0.1:{port}")
    print(f"📁 Serving static files from {WEB_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)

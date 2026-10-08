#!/usr/bin/env python3
"""
BTC-K Private Blockchain Explorer & Mining Pool Web Server
-----------------------------------------------------------
Serves the BTC-K network-scoped Web Dashboard & BlockDAG Explorer.
- Binds to BTCK_EXPLORER_HOST (loopback by default; explicit in containers).
- Pulls consensus state from btckd and measured pool state from Stratum APIs.
- Serves Mining Pool, BlockDAG Visualizer, Web Wallet, and Treasury.
"""

import os
import sys
import json
import time
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
from consensus.pow import compact_to_target
from config.network import load_network
from privacy.stealth_address import StealthKeyPair

WEB_DIR = Path(os.getenv("BTCK_WEB_DIR", str(BASE_DIR / "web")))
NODE_INFO_URL = os.getenv("BTCK_NODE_INFO_URL", "http://127.0.0.1:16110/dashboard")
NODE_RPC_URL = os.getenv("BTCK_NODE_RPC_URL", "http://127.0.0.1:16110/rpc")
STRATUM_STATUS_URL = os.getenv("BTCK_STRATUM_STATUS_URL", "http://127.0.0.1:3337/status")
NETWORK = load_network()


import threading

_CACHED_NODE_INFO: Dict[str, Any] = {}
_LAST_CACHE_TIME = 0.0
_CACHED_POOL_STATUS: Dict[str, Any] = {}
_LAST_POOL_CACHE_TIME = 0.0

def _node_info_updater():
    global _CACHED_NODE_INFO, _LAST_CACHE_TIME
    while True:
        try:
            req = urllib.request.Request(NODE_INFO_URL, headers={"User-Agent": "BTCK-Explorer/1.0"})
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data and isinstance(data, dict):
                    _CACHED_NODE_INFO = data
                    _LAST_CACHE_TIME = time.time()
        except Exception:
            pass
        # The mainnet targets one blue block per second. A five-second cache
        # made the explorer visibly trail the node by several ledger heights.
        time.sleep(1.0)


def _pool_status_updater():
    global _CACHED_POOL_STATUS, _LAST_POOL_CACHE_TIME
    while True:
        try:
            req = urllib.request.Request(STRATUM_STATUS_URL, headers={"User-Agent": "BTCK-Explorer/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data and isinstance(data, dict):
                    _CACHED_POOL_STATUS = data
                    _LAST_POOL_CACHE_TIME = time.time()
        except Exception:
            pass
        # Keep the pool page close to the Stratum status window without
        # placing its JSON endpoint on the public request path.
        time.sleep(2.0)

# Start background updater thread
_updater_thread = threading.Thread(target=_node_info_updater, daemon=True)
_updater_thread.start()
_pool_updater_thread = threading.Thread(target=_pool_status_updater, daemon=True)
_pool_updater_thread.start()


def fetch_node_info() -> Dict[str, Any]:
    return _CACHED_NODE_INFO


def fetch_wallet_sync_page(from_height: int, limit: int, anchor_hash: str) -> Dict[str, Any]:
    """Proxy a bounded, secret-free ledger page from the loopback node RPC."""
    params = {"from_height": from_height, "limit": limit, "anchor_hash": anchor_hash}
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getWalletSyncPage", "params": params,
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        NODE_RPC_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "BTCK-Wallet-Edge/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15.0) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("node returned a malformed wallet sync envelope")
    if "error" in envelope:
        raise ValueError(str(envelope["error"].get("message", "wallet sync failed")))
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise ValueError("node returned a malformed wallet sync result")
    return result


def _format_hashrate(rate: float, approximate: bool = True) -> str:
    prefix = "~" if approximate else ""
    if rate >= 1e12:
        return f"{prefix}{rate / 1e12:.2f} TH/s"
    if rate >= 1e9:
        return f"{prefix}{rate / 1e9:.2f} GH/s"
    if rate >= 1e6:
        return f"{prefix}{rate / 1e6:.2f} MH/s"
    if rate >= 1e3:
        return f"{prefix}{rate / 1e3:.2f} kH/s"
    return f"{prefix}{rate:.0f} H/s"


def _relative_network_difficulty(bits: int) -> float:
    """Return difficulty relative to this network's configured PoW limit.

    Bitcoin's historical difficulty-one target is not meaningful for a
    private/dev network with its own pow limit.  Using it made the easiest
    valid BTC-K target appear as ``0.0000`` even though it is, by definition,
    the network's 1.0x floor.
    """
    if not bits:
        return 0.0
    return compact_to_target(NETWORK.pow_limit_bits) / compact_to_target(bits)


def _observed_block_frequency(blocks: List[Dict[str, Any]]) -> float | None:
    """Return blue-ledger throughput from a timestamp window.

    Virtual blue order is consensus order, not chronological order.  Adjacent
    subtraction therefore discards negative and zero intervals and severely
    overstates cadence on a parallel DAG.  Sort timestamps, trim endpoint
    outliers, then divide the elapsed wall time by every block interval,
    including simultaneous blocks.
    """
    timestamps = sorted(
        float(block.get("timestamp", 0.0))
        for block in blocks
        if block.get("timestamp")
    )
    if len(timestamps) < 5:
        return None
    trim = len(timestamps) // 10
    sample = timestamps[trim:len(timestamps) - trim] if trim else timestamps
    elapsed = sample[-1] - sample[0]
    if elapsed <= 0.0 or len(sample) < 2:
        return None
    return elapsed / (len(sample) - 1)


def build_production_stats_payload() -> Dict[str, Any]:
    """Aggregate only node-authoritative and Stratum-measured production data."""
    node_snapshot = _CACHED_NODE_INFO
    pool_snapshot = _CACHED_POOL_STATUS
    dag = node_snapshot.get("dag", {})
    now = time.time()

    ledger_height = int(dag.get("ledger_height", 0) or 0)
    cur_h = max(1, ledger_height)
    budget_sats = get_block_budget_sats(cur_h)
    stage_id, combo_ratio, draw_interval = get_combo_spec_for_height(cur_h)
    eco_deposit_sats = int(budget_sats * 0.05)
    pool_deposit_sats = int(budget_sats * combo_ratio)
    miner_base_sats = budget_sats - pool_deposit_sats - eco_deposit_sats

    combo_state = dag.get("combo", {})
    next_draw_block = int(combo_state.get("next_draw_height", 0) or 0)
    if next_draw_block <= 0:
        next_draw_block = ((cur_h - 1) // draw_interval + 1) * draw_interval
    blocks_to_draw = max(0, next_draw_block - ledger_height)
    target_interval = float(node_snapshot.get("block_time_target_seconds", 1.0) or 1.0)

    raw_blocks = dag.get("recent_blocks", []) or []
    observed_freq = _observed_block_frequency(raw_blocks)
    next_bits = int(dag.get("next_bits", dag.get("selected_bits", 0)) or 0)
    network_diff = 0.0
    network_hashrate_hps = 0.0
    if next_bits:
        network_target = compact_to_target(next_bits)
        network_diff = _relative_network_difficulty(next_bits)
        if observed_freq:
            network_hashrate_hps = (1 << 256) / (network_target + 1) / observed_freq

    daa_at_pow_limit = bool(next_bits and next_bits == NETWORK.pow_limit_bits)
    # At the PoW floor this pool intentionally coalesces many valid ASIC
    # solutions into bounded DAG waves. Block arrival rate therefore measures
    # the submission policy, not physical network hashes. Do not publish a
    # mathematically tiny but operationally misleading hashrate estimate.
    network_hashrate_estimate_valid = bool(
        observed_freq and not (daa_at_pow_limit and pool_snapshot.get("active_workers", 0))
    )

    pool_blocks = {
        str(block.get("block_hash")): block
        for block in pool_snapshot.get("recent_blocks", [])
        if block.get("block_hash")
    }
    display_blocks = []
    for block in raw_blocks:
        block_hash = str(block.get("hash", ""))
        attribution = pool_blocks.get(block_hash, {})
        ts = float(block.get("timestamp", 0.0) or 0.0)
        display_blocks.append({
            "height": int(block.get("height", 0)),
            "blue_score": int(block.get("blue_score", 0) or 0),
            "hash": block_hash[:16] + "...",
            "full_hash": block_hash,
            "reward": round(int(block.get("miner_base_sats", 0) or 0) / SATK_PER_COIN, 8),
            "miner_base_btck": int(block.get("miner_base_sats", 0) or 0) / SATK_PER_COIN,
            "combo_deposit_btck": int(block.get("pool_deposit_sats", 0) or 0) / SATK_PER_COIN,
            "eco_deposit_btck": int(block.get("eco_deposit_sats", 0) or 0) / SATK_PER_COIN,
            "fees_btck": int(block.get("fees_sats", 0) or 0) / SATK_PER_COIN,
            "combo": bool(block.get("is_combo_resolution", False)),
            "worker": attribution.get("finder_worker") or "unattributed",
            "address": attribution.get("finder_wallet") or "",
            "mode": attribution.get("mode") or "",
            "is_pool_fee": bool(attribution.get("is_pool_fee", False)),
            "time": "",
            "full_ts": "",
            "timestamp": ts,
        })

    combo_resolutions = combo_state.get("resolutions", []) or []
    combo_hof = []
    for resolution in reversed(combo_resolutions):
        winner_hash = str(resolution.get("winner_block_hash", ""))
        attribution = pool_blocks.get(winner_hash, {})
        winner_worker = attribution.get("finder_worker") or "unattributed"
        winner_address = attribution.get("finder_wallet") or ""
        worker_match = next(
            (
                worker for worker in pool_snapshot.get("workers", [])
                if worker.get("worker_name") == winner_worker
                and (not winner_address or worker.get("wallet_address") == winner_address)
            ),
            None,
        )
        beacon_block = next(
            (block for block in raw_blocks if block.get("hash") == resolution.get("beacon_hash")),
            {},
        )
        combo_hof.append({
            "epoch_id": int(resolution.get("epoch_id", 0)),
            "draw_height": int(resolution.get("draw_height", 0)),
            "beacon_height": int(resolution.get("beacon_height", 0)),
            "beacon_hash_prefix": str(resolution.get("beacon_hash", ""))[:16],
            "winner_block_hash": winner_hash,
            "winner_miner": winner_worker,
            "winner_address": winner_address,
            "winner_hashrate": _format_hashrate(float(worker_match.get("hashrate_hps", 0.0))) if worker_match else "N/A",
            "hardware_model": "kHeavyHash Stratum worker",
            "xor_distance_hex": str(resolution.get("winner_distance_hex", "")),
            "payout_btck": int(resolution.get("jackpot_amount_satk", 0)) / SATK_PER_COIN,
            "timestamp": float(beacon_block.get("timestamp", 0.0) or 0.0),
            "status": "RESOLVED_VERIFIED",
        })

    combos_by_worker: Dict[tuple[str, str], int] = {}
    for item in combo_hof:
        key = (item["winner_address"], item["winner_miner"])
        combos_by_worker[key] = combos_by_worker.get(key, 0) + 1

    miners_summary: Dict[str, Dict[str, Any]] = {}
    name_counts: Dict[str, int] = {}
    for worker in pool_snapshot.get("workers", []):
        name = str(worker.get("worker_name") or "worker")
        name_counts[name] = name_counts.get(name, 0) + 1
    for worker in pool_snapshot.get("workers", []):
        name = str(worker.get("worker_name") or "worker")
        address = str(worker.get("wallet_address") or "")
        key = name if name_counts.get(name, 0) == 1 else f"{name}@{address[-8:]}"
        miners_summary[key] = {
            "worker": name,
            "address": address,
            "hardware": "kHeavyHash Stratum worker",
            "mode": pool_snapshot.get("pool_mode", "UNKNOWN"),
            "hashrate_str": _format_hashrate(float(worker.get("hashrate_hps", 0.0))),
            "hashrate_hps": float(worker.get("hashrate_hps", 0.0)),
            "shares": int(worker.get("shares_total", 0) or 0),
            "shares_window": int(worker.get("shares_window", 0) or 0),
            "blocks": int(worker.get("miner_blocks", 0) or 0),
            "combos": combos_by_worker.get((address, name), 0),
            "payout_btck": int(worker.get("miner_reward_satk", 0) or 0) / SATK_PER_COIN,
            "status": "ONLINE" if worker.get("online") else "OFFLINE",
            "accepted_since_connect": int(worker.get("accepted_since_connect", 0) or 0),
            "rejected_since_connect": int(worker.get("rejected_since_connect", 0) or 0),
            "ports": worker.get("ports", []),
            "last_share_timestamp": float(worker.get("last_share_timestamp", 0.0) or 0.0),
        }

    fee_policy = dict(pool_snapshot.get("fee_policy", {}))
    next_stage_seconds = float(fee_policy.get("next_stage_seconds", 0.0) or 0.0)
    fee_policy.update({
        "next_stage_hours": next_stage_seconds / 3600.0,
        "stage_description": fee_policy.get("schedule", "5% on days 1-7; 3% on days 8-30; 1% from day 31 onward"),
        "accumulated_fee_btck": int(pool_snapshot.get("fee_satk", 0) or 0) / SATK_PER_COIN,
        "fee_blocks": int(pool_snapshot.get("fee_blocks", 0) or 0),
        "historical_recipients": pool_snapshot.get("fee_recipients", []),
    })

    issued_supply_sats = int(dag.get("issued_supply_sats", 0) or 0)
    pool_balance_sats = int(dag.get("pool_balance_sats", 0) or 0)
    eco_sats = int(dag.get("eco_fund_utxo_sats", 0) or 0)
    day_idx = (cur_h - 1) // 86400
    next_decay_block = (day_idx + 1) * 86400 + 1
    stage_names = {
        1: "Phase 1: Genesis Foundation (12h / 15% Combo + 5% Ecosystem Reserve)",
        2: "Phase 2: Hashrate Expansion (36h / 25% Combo + 5% Ecosystem Reserve)",
        3: "Phase 3: Machine Economy (108h / 35% Combo + 5% Ecosystem Reserve)",
        4: "Phase 4: Final Contraction (324h / 45% Combo + 5% Ecosystem Reserve)",
        5: "Phase 5: Perpetual Fee Era",
    }
    node_age = now - _LAST_CACHE_TIME if _LAST_CACHE_TIME else None
    pool_age = now - _LAST_POOL_CACHE_TIME if _LAST_POOL_CACHE_TIME else None
    return {
        "generated_at": now,
        "ledger_height": ledger_height,
        "total_blocks": ledger_height,
        "total_dag_blocks": int(dag.get("total_dag_blocks", 0) or 0),
        "max_blue_score": int(dag.get("max_blue_score", 0) or 0),
        "tips_count": int(dag.get("tips_count", 0) or 0),
        "selected_tip": str(dag.get("selected_tip", "")),
        "total_combos": int(combo_state.get("resolved_count", 0) or 0),
        "current_era": stage_id,
        "era_name": stage_names.get(stage_id, f"Phase {stage_id}"),
        "current_emission_btck": budget_sats / SATK_PER_COIN,
        "miner_base_btck": miner_base_sats / SATK_PER_COIN,
        "pool_deposit_btck": pool_deposit_sats / SATK_PER_COIN,
        "combo_reward_btck": pool_balance_sats / SATK_PER_COIN,
        "combo_ratio": combo_ratio,
        "draw_interval_blocks": draw_interval,
        "draw_interval_hours": draw_interval * target_interval / 3600.0,
        "next_draw_block": next_draw_block,
        "blocks_to_draw": blocks_to_draw,
        "countdown_seconds": int(blocks_to_draw * target_interval),
        "countdown_basis": "consensus_target",
        "day_idx": day_idx,
        "blocks_in_day": (cur_h - 1) % 86400 + 1,
        "next_decay_block": next_decay_block,
        "blocks_to_next_decay": max(0, next_decay_block - cur_h),
        "next_day_rate_btck": get_block_budget_sats(next_decay_block) / SATK_PER_COIN,
        "block_freq_sec": f"{observed_freq:.2f}" if observed_freq else None,
        "target_block_freq_sec": target_interval,
        "total_reward_btck": issued_supply_sats / SATK_PER_COIN,
        "pool_balance_btck": pool_balance_sats / SATK_PER_COIN,
        "issued_supply_btck": issued_supply_sats / SATK_PER_COIN,
        "accumulated_treasury_fee_btck": int(pool_snapshot.get("fee_satk", 0) or 0) / SATK_PER_COIN,
        "pool_fee_policy": fee_policy,
        "accumulated_eco_reserve_btck": eco_sats / SATK_PER_COIN,
        "network_diff": network_diff,
        "network_bits": f"0x{next_bits:08x}" if next_bits else None,
        "daa_at_pow_limit": daa_at_pow_limit,
        "network_hashrate_estimate_valid": network_hashrate_estimate_valid,
        "network_hashrate_str": (
            _format_hashrate(network_hashrate_hps)
            if network_hashrate_estimate_valid
            else "N/A (pow-limit submission cap)" if daa_at_pow_limit else "N/A"
        ),
        "total_pool_hashrate_str": _format_hashrate(float(pool_snapshot.get("pool_hashrate_hps", 0.0))),
        "pool_hashrate_window_seconds": float(pool_snapshot.get("window_seconds", 600.0) or 600.0),
        "active_worker_count": int(pool_snapshot.get("active_workers", 0) or 0),
        "active_connection_count": int(pool_snapshot.get("active_connections", 0) or 0),
        "is_actively_mining": int(pool_snapshot.get("active_workers", 0) or 0) > 0,
        "miners_summary": miners_summary,
        "stratum_share_counters": pool_snapshot.get("share_counters"),
        "combo_hof": combo_hof,
        "blocks": display_blocks,
        "network": node_snapshot.get("network", {}),
        "telemetry": {
            "node_age_seconds": node_age,
            "pool_age_seconds": pool_age,
            "node_fresh": node_age is not None and node_age < 15.0,
            "pool_fresh": pool_age is not None and pool_age < 25.0,
            "source": "btckd:/dashboard + stratum:/status",
        },
    }


def _search_recent_blocks(query: str, stats: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Search the node-authoritative recent blue-order window.

    The current node dashboard exposes a bounded recent window rather than a
    historical index.  The API states that scope explicitly instead of
    pretending an arbitrary height/hash was found.
    """
    needle = query.strip().lower()
    if not needle:
        return {"found": False, "message": "A block height or hash is required"}
    snapshot = stats if stats is not None else build_stats_payload()
    blocks = snapshot.get("blocks", []) or []
    for block in blocks:
        height_match = needle.lstrip("#").isdigit() and int(needle.lstrip("#")) == int(block.get("height", -1))
        full_hash = str(block.get("full_hash", "")).lower()
        hash_match = len(needle) >= 8 and full_hash.startswith(needle)
        if height_match or hash_match:
            return {"found": True, "scope": "recent_node_window", "block": block}
    return {
        "found": False,
        "scope": "recent_node_window",
        "message": "Block not present in the node's recent explorer window",
    }


# Production endpoint definition supersedes the legacy single-node demo builder above.
build_stats_payload = build_production_stats_payload


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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/pool", "/app", "/caas"):
            self.serve_static_file("index.html", "text/html; charset=utf-8")
        elif path in ("/explorer", "/explorer.html"):
            self.serve_static_file("explorer.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self.send_error(410, "Legacy demo application is not included in the public beta")
        elif path in ("/wallet", "/wallet.html", "/wallet/treasury", "/wallet_treasury", "/wallet_treasury.html"):
            self.send_json_response({
                "available": False,
                "message": "The unaudited browser wallet is not part of the public beta. Use the signed release wallet or offline CLI.",
            }, 410)
        elif path == "/api/stats":
            self.send_json_response(build_stats_payload())
        elif path == "/api/blocks":
            stats = build_stats_payload()
            try:
                limit = max(1, min(int(urllib.parse.parse_qs(parsed.query).get("limit", [50])[0]), 120))
            except (TypeError, ValueError):
                limit = 50
            self.send_json_response({
                "scope": "recent_node_window",
                "blocks": (stats.get("blocks", []) or [])[-limit:],
            })
        elif path == "/api/wallet/sync":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                from_height = int(query.get("from_height", ["0"])[0])
                limit = max(1, min(int(query.get("limit", ["250"])[0]), 1000))
                anchor_hash = str(query.get("anchor_hash", [""])[0])
                if from_height < 0 or (anchor_hash and len(anchor_hash) != 64):
                    raise ValueError("invalid wallet sync cursor")
                self.send_json_response(fetch_wallet_sync_page(from_height, limit, anchor_hash))
            except (TypeError, ValueError) as exc:
                self.send_json_response({"error": str(exc)}, 409)
            except Exception:
                self.send_json_response({"error": "wallet sync backend temporarily unavailable"}, 503)
        elif path == "/api/search":
            query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            result = _search_recent_blocks(query)
            self.send_json_response(result, 200 if result.get("found") else 404)
        elif path.startswith("/api/block/"):
            query = urllib.parse.unquote(path.removeprefix("/api/block/"))
            result = _search_recent_blocks(query)
            self.send_json_response(result, 200 if result.get("found") else 404)
        elif path == "/api/combo/history":
            self.send_json_response(build_stats_payload().get("combo_hof", []))
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
            if clean_path in {"wallet.html", "wallet_treasury.html", "app.js"}:
                self.send_error(410, "Legacy demo asset is not included in the public beta")
                return
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
    host = os.getenv("BTCK_EXPLORER_HOST", "127.0.0.1")
    server_address = (host, port)
    httpd = HTTPServer(server_address, ExplorerHTTPHandler)
    print(f"BTC-K Explorer running on http://{host}:{port}")
    print(f"📁 Serving static files from {WEB_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)

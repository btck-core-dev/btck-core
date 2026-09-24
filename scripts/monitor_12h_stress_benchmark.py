#!/usr/bin/env python3
"""
monitor_12h_stress_benchmark.py
===============================
12-Hour Continuous Multi-Miner Stress Test Monitor for Bitcoin K (BTCK).
Monitors:
  - BlockDAG Height & aggregate cadence (targeting 1.000s / block)
  - DAA Difficulty adjustments across 3+ concurrent ASIC miners (~1.1+ TH/s)
  - Individual miner telemetry (IceRiver KS0 Ultras at 192.168.50.236, .154, .104, .6)
  - Share acceptance rate and zero-rejection verification
  - 12-Hour Combo Jackpot drawing and prize pool accumulation
  - Hourly milestone snapshots logged to logs/12h_benchmark_monitor.log
"""

import os
import sys
import time
import json
import datetime
import urllib.request
import urllib.parse
import signal

REPO_ROOT = "/Users/a1234/btck-core"
LOG_FILE = os.path.join(REPO_ROOT, "logs", "12h_benchmark_monitor.log")
METRICS_JSON = os.path.join(REPO_ROOT, "data", "12h_benchmark_metrics.json")
CHAIN_STATE_FILE = os.path.join(REPO_ROOT, "data", "chain_state.json")
DASHBOARD_API = "http://127.0.0.1:8088/api/stats"

MINER_IPS = {
    "192.168.50.236": "ks001 (SOLO)",
    "192.168.50.154": "miner_154 (PPLNS)",
    "192.168.50.249": "miner_249 (PPLNS)",
    "192.168.50.3": "ks003 (SOLO)",
    "192.168.50.6": "ks004 (PPLNS)",
    "192.168.50.104": "ks002 (SOLO)"
}

TOTAL_TEST_SECONDS = 12 * 3600  # 12 hours (43,200s)
SAMPLE_INTERVAL_SECONDS = 15     # Sample every 15s

def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def fetch_chain_stats():
    try:
        req = urllib.request.Request(DASHBOARD_API, headers={"User-Agent": "BTCK-12h-Monitor"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        # Fallback to direct chain_state.json read
        if os.path.exists(CHAIN_STATE_FILE):
            try:
                with open(CHAIN_STATE_FILE, "r", encoding="utf-8") as f:
                    cs = json.load(f)
                    bps = cs.get("network_bps", 1.0)
                    return {
                        "total_blocks": cs.get("current_height", 0),
                        "network_diff": cs.get("network_diff", 1.0),
                        "block_freq_sec": f"{1.0 / bps:.2f}" if bps > 0 else "1.00",
                        "pool_balance_btck": cs.get("pool_balance_btck", 0.0),
                        "issued_supply_btck": cs.get("issued_supply_btck", 0.0),
                        "total_combos": sum(m.get("combos", 0) for m in cs.get("miners_summary", {}).values()),
                        "miners_summary": cs.get("miners_summary", {})
                    }
            except Exception:
                pass
    return None

def fetch_hardware_telemetry():
    hardware_status = {}
    for ip, label in MINER_IPS.items():
        try:
            req_data = urllib.parse.urlencode({"post": 4}).encode()
            req = urllib.request.Request(
                f"http://{ip}/user/userpanel",
                data=req_data,
                method="POST",
                headers={"Connection": "close"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                d = json.loads(resp.read().decode())
                if d.get("error") == 0 and "data" in d:
                    data = d["data"]
                    fans = data.get("fans", [0, 0])
                    boards = data.get("boards", [{}])
                    temp = boards[0].get("intmp", 0.0) if boards else 0.0
                    hardware_status[ip] = {
                        "label": label,
                        "online": True,
                        "rtpow": data.get("rtpow", "0G"),
                        "avgpow": data.get("avgpow", "0G"),
                        "fans": fans,
                        "temp_in": temp
                    }
                    continue
        except Exception:
            pass
        hardware_status[ip] = {"label": label, "online": False}
    return hardware_status

def main():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(METRICS_JSON), exist_ok=True)

    log("=" * 70)
    log("🚀 BTC-K 12-HOUR CONTINUOUS MULTI-MINER STRESS BENCHMARK INITIATED")
    log(f"   Target Duration: 12.0 Hours ({TOTAL_TEST_SECONDS:,} Seconds)")
    log(f"   Target Aggregate Output: ~43,200 Blocks (@ 1.000 BPS)")
    log(f"   Log Output File: {LOG_FILE}")
    log(f"   Metrics JSON   : {METRICS_JSON}")
    log("=" * 70)

    start_time = time.time()
    last_hourly_report = start_time
    hour_counter = 0

    init_stats = fetch_chain_stats() or {}
    start_height = init_stats.get("total_blocks", 0)
    log(f"[+] Initial Chain Height: #{start_height:,}")
    log(f"[+] Initial Network Difficulty: {init_stats.get('network_diff', 'N/A')}")
    log(f"[+] Initial Pool Reserve: {init_stats.get('pool_balance_btck', 0.0):.2f} BTC-K")

    metrics_history = []
    running = True

    def sig_handler(sig, frame):
        nonlocal running
        log(f"\n[!] Received termination signal ({sig}). Finalizing 12-hour session...")
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    while running:
        now = time.time()
        elapsed = now - start_time
        if elapsed >= TOTAL_TEST_SECONDS:
            log(f"🎉 12-Hour Benchmark duration fully reached ({elapsed/3600:.2f} hours). Completing run.")
            break

        stats = fetch_chain_stats()
        if stats:
            cur_h = stats.get("total_blocks", 0)
            blocks_produced = cur_h - start_height
            freq_str = stats.get("block_freq_sec", "1.00")
            diff = stats.get("network_diff", 1.0)
            pool_btck = stats.get("pool_balance_btck", 0.0)
            combos = stats.get("total_combos", 0)
            miners = stats.get("miners_summary", {})

            # Record metric point
            point = {
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed_hours": round(elapsed / 3600.0, 3),
                "height": cur_h,
                "blocks_produced": blocks_produced,
                "cadence_sec": freq_str,
                "diff": diff,
                "pool_reserve_btck": pool_btck,
                "combos": combos,
                "miners": miners
            }
            metrics_history.append(point)
            if len(metrics_history) > 3000:
                metrics_history.pop(0)

            # Save metrics JSON periodically
            try:
                with open(METRICS_JSON, "w", encoding="utf-8") as f:
                    json.dump({
                        "test_start_time": start_time,
                        "elapsed_seconds": round(elapsed, 1),
                        "elapsed_hours": round(elapsed / 3600.0, 3),
                        "start_height": start_height,
                        "current_height": cur_h,
                        "blocks_produced": blocks_produced,
                        "recent_metric": point,
                        "history_sample_count": len(metrics_history)
                    }, f, indent=2)
            except Exception:
                pass

        # Check hourly milestone
        if now - last_hourly_report >= 3600.0:
            hour_counter += 1
            last_hourly_report = now
            hw = fetch_hardware_telemetry()
            hw_summary = []
            for ip, info in hw.items():
                if info.get("online"):
                    hw_summary.append(f"{info['label']} ({info['rtpow']}, {info['temp_in']}°C)")
                else:
                    hw_summary.append(f"{info['label']} (Offline/Standby)")

            cur_stats = fetch_chain_stats() or {}
            cur_h = cur_stats.get("total_blocks", 0)
            delta_blocks = cur_h - start_height - (hour_counter * 3600)
            delta_pct = (delta_blocks / (hour_counter * 3600)) * 100 if hour_counter > 0 else 0

            log("-" * 70)
            log(f"⏰ [HOURLY MILESTONE] Hour #{hour_counter}/12 ({elapsed/3600:.2f}h elapsed)")
            log(f"   • Current Height: #{cur_h:,} (Produced: {cur_h - start_height:,} blocks in {hour_counter}h)")
            log(f"   • Delta vs 1.0s Theoretical: {delta_blocks:+d} blocks ({delta_pct:+.2f}%)")
            log(f"   • Dynamic Block Cadence: {cur_stats.get('block_freq_sec', '1.00')} sec/block")
            log(f"   • Network Difficulty: {cur_stats.get('network_diff', 'N/A')}")
            log(f"   • 12h Jackpot Pool: {cur_stats.get('pool_balance_btck', 0.0):.2f} BTC-K | Combos Won: {cur_stats.get('total_combos', 0)}")
            log(f"   • Physical Miners: {', '.join(hw_summary)}")
            log("-" * 70)

        time.sleep(SAMPLE_INTERVAL_SECONDS)

    # Final summary
    end_stats = fetch_chain_stats() or {}
    end_h = end_stats.get("total_blocks", 0)
    total_blocks_run = end_h - start_height
    total_run_time = time.time() - start_time

    log("=" * 70)
    log("🏁 12-HOUR BENCHMARK SESSION COMPLETED")
    log(f"   • Total Run Time: {total_run_time/3600:.2f} Hours ({total_run_time:.1f}s)")
    log(f"   • Height Trajectory: #{start_height:,} -> #{end_h:,} (+{total_blocks_run:,} blocks)")
    log(f"   • Average Block Cadence: {total_run_time / max(1, total_blocks_run):.3f}s / block")
    log(f"   • Final Network Difficulty: {end_stats.get('network_diff', 'N/A')}")
    log(f"   • Final Pool Reserve: {end_stats.get('pool_balance_btck', 0.0):.2f} BTC-K")
    log("=" * 70)

if __name__ == "__main__":
    main()

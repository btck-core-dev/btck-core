#!/usr/bin/env bash
set -eu

PROJECT_DIR="${BTCK_PROJECT_DIR:-/home/ubuntu/btck-devnet}"
OBSERVATION_DIR="${BTCK_OBSERVATION_DIR:-/home/ubuntu/btck-observation}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$OBSERVATION_DIR"
work_dir="$(mktemp -d "$OBSERVATION_DIR/.snapshot.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

curl -fsS http://127.0.0.1:16110/info > "$work_dir/node-info.json"
curl -fsS http://127.0.0.1:8088/api/stats > "$work_dir/dashboard-stats.json"

cd "$PROJECT_DIR"
docker compose ps --format json > "$work_dir/compose-ps.jsonl"
docker stats --no-stream --format json \
  btck-full-node btck-stratum-pool btck-explorer > "$work_dir/docker-stats.jsonl"
docker inspect btck-full-node btck-stratum-pool btck-explorer > "$work_dir/docker-inspect.json"
if [ -f "$OBSERVATION_DIR/stratum-rejections-wire.log" ]; then
  tail -n 10000 "$OBSERVATION_DIR/stratum-rejections-wire.log" \
    > "$work_dir/stratum-wire-rejections.log"
else
  : > "$work_dir/stratum-wire-rejections.log"
fi

docker logs --since 65m --timestamps btck-stratum-pool \
  > "$work_dir/stratum-hour.log" 2>&1 || true
docker logs --since 25h --timestamps btck-stratum-pool 2>&1 \
  | grep -E "Miner authorized:|Miner disconnected:" \
  > "$work_dir/stratum-connections.log" || true
docker logs --since 65m --timestamps btck-full-node \
  > "$work_dir/node-hour.log" 2>&1 || true

LOG_FILE="$work_dir/stratum-hour.log" \
NODE_LOG_FILE="$work_dir/node-hour.log" \
OUTPUT_FILE="$work_dir/summary.json" \
STAMP="$STAMP" \
python3 - <<'PY'
import json
import os
import re
from pathlib import Path

log = Path(os.environ["LOG_FILE"]).read_text(errors="replace")
node_log = Path(os.environ["NODE_LOG_FILE"]).read_text(errors="replace")
node = json.loads(Path(os.environ["LOG_FILE"]).with_name("node-info.json").read_text())
dashboard = json.loads(Path(os.environ["LOG_FILE"]).with_name("dashboard-stats.json").read_text())
connections = Path(os.environ["LOG_FILE"]).with_name("stratum-connections.log").read_text(errors="replace")
wire_rejections = Path(os.environ["LOG_FILE"]).with_name("stratum-wire-rejections.log").read_text(errors="replace")
dag = node["dag"]

def count(text, needle):
    return text.count(needle)

client_workers = {}
for line in connections.splitlines():
    match = re.search(r"Miner authorized: (\S+) -> .* \(worker: ([^,]+),", line)
    if match:
        client_workers[match.group(1)] = match.group(2)

worker_stats = {}
def worker_row(worker):
    return worker_stats.setdefault(worker, {
        "valid_shares": 0,
        "stale": 0,
        "low_difficulty": 0,
        "duplicate": 0,
    })

for line in log.splitlines():
    valid = re.search(r"Valid share from (\S+) \(([^)]+)\)", line)
    if valid:
        client_workers[valid.group(1)] = valid.group(2)
        worker_row(valid.group(2))["valid_shares"] += 1
        continue
    reason = None
    if "Stale/unknown" in line:
        reason = "stale"
    elif "Low difficulty" in line:
        reason = "low_difficulty"
    elif "Duplicate share" in line:
        reason = "duplicate"
    if reason:
        rejected = re.search(r" from (\S+?)(?:\s|$)", line)
        client = rejected.group(1).rstrip(":") if rejected else ""
        worker_row(client_workers.get(client, f"unmapped:{client or 'unknown'}"))[reason] += 1

summary = {
    "captured_at_utc": os.environ["STAMP"],
    "network": node["network"],
    "uptime_seconds": node["uptime_seconds"],
    "chain": {
        key: dag[key]
        for key in (
            "total_blocks", "max_blue_score", "virtual_selected_chain_length",
            "virtual_blue_order_length", "tips_count", "selected_bits", "next_bits",
            "issued_supply_sats", "pool_balance_sats", "eco_fund_utxo_sats", "state_root",
        )
    },
    "dashboard": {
        "ledger_height": dashboard.get("total_blocks"),
        "total_dag_blocks": dashboard.get("total_dag_blocks"),
        "max_blue_score": dashboard.get("max_blue_score"),
        "block_freq_sec": dashboard.get("block_freq_sec"),
        "network_diff": dashboard.get("network_diff"),
        "network_hashrate_str": dashboard.get("network_hashrate_str"),
        "pool_hashrate_str": dashboard.get("total_pool_hashrate_str"),
        "active_worker_count": dashboard.get("active_worker_count"),
        "active_connection_count": dashboard.get("active_connection_count"),
        "total_combos": dashboard.get("total_combos"),
        "combo_pool_btck": dashboard.get("combo_reward_btck"),
        "eco_reserve_btck": dashboard.get("accumulated_eco_reserve_btck"),
        "treasury_fee_btck": dashboard.get("accumulated_treasury_fee_btck"),
        "fee_policy": dashboard.get("pool_fee_policy"),
        "miners": dashboard.get("miners_summary"),
    },
    "stratum_last_65m": {
        "valid_shares": count(log, "Valid share"),
        "accepted_blocks": count(log, "BLOCK ACCEPTED"),
        "pool_fee_blocks": count(log, "BLOCK ACCEPTED (Pool Fee"),
        "stale": count(log, "Stale/unknown"),
        "low_difficulty": count(log, "Low difficulty"),
        "duplicate": count(log, "Duplicate share"),
        "rate_limit": count(log, "rate limit"),
        "late_credited_not_submitted": count(log, "Valid late share credited but not submitted"),
        "node_submit_errors": count(log, "Node submitBlock error"),
        "connections": count(log, "Miner connected"),
        "authorizations": count(log, "Miner authorized"),
    },
    "workers_last_65m": dict(sorted(worker_stats.items())),
    "runtime_errors_last_65m": {
        "tracebacks": count(log + node_log, "Traceback"),
        "error_records": count(log + node_log, "[ERROR]"),
    },
    "wire_rejections_since_monitor_start": {
        "stale_21": count(wire_rejections, '"error":[21,'),
        "duplicate_22": count(wire_rejections, '"error":[22,'),
        "low_difficulty_23": count(wire_rejections, '"error":[23,'),
        "rate_limit_25": count(wire_rejections, '"error":[25,'),
    },
}
Path(os.environ["OUTPUT_FILE"]).write_text(
    json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
)
PY

df -h / /var/lib/docker > "$work_dir/disk.txt" 2>&1 || true
(cd "$work_dir" && sha256sum * > SHA256SUMS)
chmod -R go-rwx "$work_dir"
mv "$work_dir" "$OBSERVATION_DIR/$STAMP"
trap - EXIT
ln -sfn "$STAMP" "$OBSERVATION_DIR/latest"
printf '%s\n' "$OBSERVATION_DIR/$STAMP"

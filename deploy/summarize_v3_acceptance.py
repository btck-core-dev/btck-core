#!/usr/bin/env python3
"""Summarize a bounded BTC-K observation window without touching live services."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def load_snapshot(path: Path) -> dict[str, Any]:
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    dashboard = json.loads((path / "dashboard-stats.json").read_text(encoding="utf-8"))
    return {"path": path, "stamp": parse_stamp(path.name), "summary": summary, "dashboard": dashboard}


def resolve_boundary(root: Path, value: str | None, default_name: str) -> str | None:
    if value:
        return value
    marker = root / default_name
    if marker.exists() or marker.is_symlink():
        return marker.resolve().name
    return None


def numeric_delta(first: dict, last: dict, key: str) -> float:
    return float(last.get(key, 0) or 0) - float(first.get(key, 0) or 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observation-dir", type=Path, default=Path("/home/ubuntu/btck-observation"))
    parser.add_argument("--start", help="inclusive snapshot stamp; defaults to acceptance-start marker")
    parser.add_argument("--end", help="inclusive snapshot stamp; defaults to latest marker/latest snapshot")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.observation_dir
    start_name = resolve_boundary(root, args.start, "acceptance-start")
    end_name = resolve_boundary(root, args.end, "latest")
    snapshots = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not (path / "summary.json").is_file():
            continue
        try:
            item = load_snapshot(path)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if start_name and item["stamp"] < parse_stamp(start_name):
            continue
        if end_name and item["stamp"] > parse_stamp(end_name):
            continue
        snapshots.append(item)
    if len(snapshots) < 2:
        raise SystemExit("need at least two valid observation snapshots in the selected window")

    first, last = snapshots[0], snapshots[-1]
    elapsed = (last["stamp"] - first["stamp"]).total_seconds()
    first_dash, last_dash = first["dashboard"], last["dashboard"]
    blue_delta = int(numeric_delta(first_dash, last_dash, "total_blocks"))
    dag_delta = int(numeric_delta(first_dash, last_dash, "total_dag_blocks"))
    wire_first = first["summary"].get("wire_rejections_since_monitor_start", {})
    wire_last = last["summary"].get("wire_rejections_since_monitor_start", {})
    wire_delta = {
        key: max(0, int(wire_last.get(key, 0) or 0) - int(wire_first.get(key, 0) or 0))
        for key in sorted(set(wire_first) | set(wire_last))
    }
    hourly = [item["summary"].get("stratum_last_65m", {}) for item in snapshots]
    runtime = [item["summary"].get("runtime_errors_last_65m", {}) for item in snapshots]

    miner_delta = {}
    first_miners = first_dash.get("miners_summary", {}) or {}
    last_miners = last_dash.get("miners_summary", {}) or {}
    for worker in sorted(set(first_miners) | set(last_miners)):
        before = first_miners.get(worker, {})
        after = last_miners.get(worker, {})
        miner_delta[worker] = {
            "shares": int(numeric_delta(before, after, "shares")),
            "blocks": int(numeric_delta(before, after, "blocks")),
            "payout_btck": round(numeric_delta(before, after, "payout_btck"), 8),
            "ending_hashrate": after.get("hashrate_str"),
            "ending_status": after.get("status"),
        }

    report = {
        "window": {
            "start_utc": first["stamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_utc": last["stamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "elapsed_seconds": elapsed,
            "snapshot_count": len(snapshots),
            "is_24h_complete": elapsed >= 86400,
        },
        "chain": {
            "blue_ledger_delta": blue_delta,
            "dag_block_delta": dag_delta,
            "observed_seconds_per_blue": round(elapsed / blue_delta, 6) if blue_delta > 0 else None,
            "tips_min": min(int(item["summary"]["chain"].get("tips_count", 0)) for item in snapshots),
            "tips_max": max(int(item["summary"]["chain"].get("tips_count", 0)) for item in snapshots),
            "ending_height": last_dash.get("total_blocks"),
            "ending_dag_blocks": last_dash.get("total_dag_blocks"),
        },
        "economics": {
            "combo_draws_delta": int(numeric_delta(first_dash, last_dash, "total_combos")),
            "combo_pool_delta_btck": round(numeric_delta(first_dash, last_dash, "combo_reward_btck"), 8),
            "eco_reserve_delta_btck": round(numeric_delta(first_dash, last_dash, "accumulated_eco_reserve_btck"), 8),
            "treasury_fee_delta_btck": round(numeric_delta(first_dash, last_dash, "accumulated_treasury_fee_btck"), 8),
            "ending_fee_policy": last_dash.get("pool_fee_policy"),
        },
        "pool": {
            "ending_hashrate": last_dash.get("total_pool_hashrate_str"),
            "ending_active_workers": last_dash.get("active_worker_count"),
            "miners": miner_delta,
            "wire_rejection_delta": wire_delta,
            "max_65m_window": {
                key: max(int(row.get(key, 0) or 0) for row in hourly)
                for key in ("stale", "low_difficulty", "duplicate", "rate_limit", "node_submit_errors")
            },
        },
        "runtime": {
            "max_tracebacks_in_65m_window": max(int(row.get("tracebacks", 0) or 0) for row in runtime),
            "max_error_records_in_65m_window": max(int(row.get("error_records", 0) or 0) for row in runtime),
        },
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()

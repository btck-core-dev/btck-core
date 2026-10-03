#!/usr/bin/env python3
"""Record a bounded high-frequency DAG-tip burn-down trace."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def read_dag(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
    return payload.get("dag", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:16110/dashboard")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.output.exists()
    with args.output.open("a", encoding="utf-8", buffering=1) as handle:
        if new_file:
            handle.write("timestamp_utc\tledger_height\ttotal_dag_blocks\ttips_count\tselected_bits\n")
        for index in range(args.samples):
            try:
                dag = read_dag(args.url)
                stamp = datetime.now(timezone.utc).isoformat()
                handle.write(
                    f"{stamp}\t{dag.get('ledger_height')}\t{dag.get('total_dag_blocks')}\t"
                    f"{dag.get('tips_count')}\t{dag.get('selected_bits')}\n"
                )
            except Exception as exc:  # preserve failures in the acceptance evidence
                stamp = datetime.now(timezone.utc).isoformat()
                handle.write(f"{stamp}\tERROR\t{type(exc).__name__}: {exc}\n")
            if index + 1 < args.samples:
                time.sleep(max(1.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

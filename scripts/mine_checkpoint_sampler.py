#!/usr/bin/env python3
"""
BTC-K Milestone & Approaching Checkpoint Sampler (Height >= 270,000)
Polls the private mainnet dashboard and records checksummed forensic evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TARGET_CHECKPOINT_HEIGHT = 271_828
SAMPLING_TRIGGER_HEIGHT = 270_000
KEY_EVIDENCE_HEIGHTS = {270_000, 271_000, 271_500, 271_800, 271_827, 271_828}


def fetch_stats(url: str = "https://bitcoin-k.com/api/stats") -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "BTCK-Evidence-Sampler/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def record_evidence(stats: dict, output_dir: Path, tag: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    utc_now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    height = stats.get("ledger_height", 0)
    
    filename = f"evidence_h{height}_{utc_now}"
    if tag:
        filename += f"_{tag}"
    filename += ".json"
    
    filepath = output_dir / filename
    envelope = {
        "evidence_schema": "btck-checkpoint-sampler-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "private-mainnet-dashboard",
        "stats": stats,
    }
    raw_bytes = (json.dumps(envelope, indent=2, sort_keys=True) + "\n").encode("utf-8")
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    temporary = filepath.with_suffix(".json.tmp")
    with open(temporary, "wb") as f:
        f.write(raw_bytes)
        f.flush()
    temporary.replace(filepath)
    filepath.chmod(0o600)

    sha_file = filepath.with_suffix(".json.sha256")
    with open(sha_file, "w", encoding="utf-8") as f:
        f.write(f"{sha256}  {filepath.name}\n")
    sha_file.chmod(0o600)

    print(f"[{utc_now}] Saved evidence for height {height} -> {filepath.name} (SHA256: {sha256[:16]}...)")
    return filepath


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("evidence/sampler"))
    parser.add_argument("--url", default="https://bitcoin-k.com/api/stats")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval in seconds")
    parser.add_argument("--once", action="store_true", help="Poll once and exit (for systemd timers)")
    parser.add_argument("--force-sample", action="store_true", help="Record current state immediately regardless of height")
    args = parser.parse_args()

    print(f"Starting BTC-K Milestone Evidence Sampler...")
    print(f"Trigger height: {SAMPLING_TRIGGER_HEIGHT}, Target checkpoint: {TARGET_CHECKPOINT_HEIGHT}")
    print(f"Output directory: {args.output_dir.resolve()}")

    while True:
        try:
            stats = fetch_stats(args.url)
            height = stats.get("ledger_height", 0)
            remaining = TARGET_CHECKPOINT_HEIGHT - height
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] Current Ledger Height: {height} | Remaining: {remaining} blocks")

            if args.force_sample:
                record_evidence(stats, args.output_dir, tag="manual_snapshot")
                if not sys.stdin.isatty():
                    break

            if height >= SAMPLING_TRIGGER_HEIGHT:
                record_evidence(stats, args.output_dir, tag="5min_window")
                
                for m_height in KEY_EVIDENCE_HEIGHTS:
                    already_recorded = any(
                        args.output_dir.glob(f"evidence_h*_milestone_{m_height}.json")
                    )
                    if height >= m_height and not already_recorded:
                        record_evidence(stats, args.output_dir, tag=f"milestone_{m_height}")

            if height >= TARGET_CHECKPOINT_HEIGHT:
                print(f"TARGET CHECKPOINT HEIGHT {TARGET_CHECKPOINT_HEIGHT} REACHED! Saving final evidence...")
                record_evidence(stats, args.output_dir, tag="FINAL_271828_CANDIDATE")
                break

            if args.once:
                break

        except Exception as e:
            print(f"Error fetching stats: {e}", file=sys.stderr)

        if args.once or args.force_sample:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

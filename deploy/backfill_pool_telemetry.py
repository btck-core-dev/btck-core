#!/usr/bin/env python3
"""One-time migration of pre-telemetry Stratum logs into pool_accounting.db.

The migration never invents chain rewards: submitted hash prefixes are joined
against a btckd ``/dashboard`` snapshot and rewards are taken from canonical
blue-order fields. Re-running it is safe because block hashes are unique.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from stratum.pplns import PPLNSEngine


VALID_SHARE = re.compile(
    r"^\[(?P<time>[^]]+)\].*Valid share from .* \((?P<worker>[^)]+)\).*Diff: (?P<diff>[0-9.]+)"
)
MINER_BLOCK = re.compile(
    r"^\[(?P<time>[^]]+)\].*BLOCK ACCEPTED ON CHAIN! Hash: (?P<prefix>[0-9a-f]+).*\((?P<worker>[^)]+)\)"
)
FEE_BLOCK = re.compile(
    r"^\[(?P<time>[^]]+)\].*BLOCK ACCEPTED \(Pool Fee [^)]+\)! Hash: (?P<prefix>[0-9a-f]+)"
)


def parse_time(raw: str) -> float:
    return datetime.strptime(raw.split(",", 1)[0], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    ).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--dashboard", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--miner-address", required=True)
    parser.add_argument("--historical-fee-address", required=True)
    parser.add_argument(
        "--launch-time",
        type=float,
        default=0.0,
        help="Original pool launch Unix timestamp persisted into pool_meta",
    )
    args = parser.parse_args()

    dashboard = json.loads(args.dashboard.read_text(encoding="utf-8"))
    canonical_blocks = dashboard.get("dag", dashboard).get("recent_blocks", [])
    by_prefix = {str(block["hash"])[:16]: block for block in canonical_blocks}
    engine = PPLNSEngine(args.db)
    launch_time = engine.get_or_create_launch_time(args.launch_time)

    shares = 0
    blocks = 0
    unmatched = set()
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        share = VALID_SHARE.search(line)
        if share:
            engine.record_share(
                args.miner_address,
                share.group("worker"),
                float(share.group("diff")),
                parse_time(share.group("time")),
            )
            shares += 1
            continue

        fee = FEE_BLOCK.search(line)
        miner = MINER_BLOCK.search(line) if not fee else None
        match = fee or miner
        if not match:
            continue
        prefix = match.group("prefix")[:16]
        canonical = by_prefix.get(prefix)
        if not canonical:
            unmatched.add(prefix)
            continue
        is_fee = fee is not None
        engine.record_solo_block(
            block_hash=str(canonical["hash"]),
            height=int(canonical.get("height", 0)),
            reward_satk=int(canonical.get("miner_base_sats", 0)) + int(canonical.get("fees_sats", 0)),
            finder_wallet=args.miner_address if not is_fee else args.historical_fee_address,
            finder_worker=miner.group("worker") if miner else "pool-fee",
            payout_address=args.historical_fee_address if is_fee else args.miner_address,
            is_pool_fee=is_fee,
            timestamp=parse_time(match.group("time")),
        )
        blocks += 1

    engine.reconcile_solo_block_rewards(canonical_blocks)
    print(json.dumps({
        "shares_imported": shares,
        "blocks_imported": blocks,
        "pool_launch_time": launch_time,
        "unmatched_block_prefixes": sorted(unmatched),
    }, indent=2))


if __name__ == "__main__":
    main()

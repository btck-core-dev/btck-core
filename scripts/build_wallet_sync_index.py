#!/usr/bin/env python3
"""Build a public wallet history index from a verified V2 chain snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from wallet_sync_index import build_wallet_sync_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="immutable chain.snapshot.v2.sqlite3")
    parser.add_argument("output", type=Path, help="new wallet-sync-index.sqlite3")
    args = parser.parse_args()
    print(json.dumps(build_wallet_sync_index(args.snapshot, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

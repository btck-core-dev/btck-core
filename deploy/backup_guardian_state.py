#!/usr/bin/env python3
"""Create an online, integrity-checked backup of private MAINNET guardian state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


SNAPSHOT_NAME = re.compile(r"^\d{8}T\d{6}Z$")


def snapshot_directories(backup_root: Path) -> list[Path]:
    """Return valid snapshot directories from newest to oldest."""
    return sorted(
        (
            path
            for path in backup_root.iterdir()
            if path.is_dir() and not path.is_symlink() and SNAPSHOT_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def retention_plan(snapshots: list[Path], keep_hourly: int, keep_daily: int) -> set[Path]:
    """Keep recent hourly snapshots plus the newest snapshot from recent UTC days."""
    keep = set(snapshots[:keep_hourly])
    retained_days: set[str] = set()
    for snapshot in snapshots:
        day = snapshot.name[:8]
        if day in retained_days:
            continue
        retained_days.add(day)
        keep.add(snapshot)
        if len(retained_days) >= keep_daily:
            break
    return keep


def prune_snapshots(backup_root: Path, keep_hourly: int, keep_daily: int) -> list[str]:
    snapshots = snapshot_directories(backup_root)
    keep = retention_plan(snapshots, keep_hourly, keep_daily)
    removed: list[str] = []
    resolved_root = backup_root.resolve()
    for snapshot in snapshots:
        if snapshot in keep:
            continue
        if snapshot.parent.resolve() != resolved_root or not SNAPSHOT_NAME.fullmatch(snapshot.name):
            raise RuntimeError(f"refusing to prune unsafe backup path: {snapshot}")
        shutil.rmtree(snapshot)
        removed.append(snapshot.name)
    return removed


def backup_sqlite(source: Path, destination: Path) -> None:
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_db = sqlite3.connect(destination, timeout=30)
    try:
        source_db.backup(destination_db)
        if destination_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"integrity check failed: {source}")
    finally:
        destination_db.close()
        source_db.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_json_or_status(url: str) -> dict:
    try:
        return json.load(urlopen(url, timeout=3))
    except (OSError, URLError) as exc:
        return {"status": "unavailable", "url": url, "error": type(exc).__name__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/btck-mainnet-guardian"))
    parser.add_argument("--backup-root", type=Path, default=Path("/home/ubuntu/btck-mainnet-backups"))
    parser.add_argument("--keep-hourly", type=int, default=24)
    parser.add_argument("--keep-daily", type=int, default=7)
    args = parser.parse_args()
    if args.keep_hourly < 1 or args.keep_daily < 1:
        parser.error("backup retention must keep at least one hourly and one daily snapshot")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    args.backup_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".backup.", dir=args.backup_root))
    final = args.backup_root / stamp
    try:
        node_db = args.root / "data-root/mainnet/chain.sqlite3"
        pool_db = args.root / "pool-data/pool_accounting.db"
        backup_sqlite(node_db, temporary / "chain.sqlite3")
        backup_sqlite(pool_db, temporary / "pool_accounting.db")
        manifest = args.root / "params/networks/mainnet.guardian.json"
        (temporary / "mainnet.guardian.json").write_bytes(manifest.read_bytes())
        node_info = json.load(urlopen("http://127.0.0.1:17110/info", timeout=3))
        pool_status = fetch_json_or_status("http://127.0.0.1:3337/status")
        (temporary / "node-info.json").write_text(json.dumps(node_info, indent=2, sort_keys=True) + "\n")
        (temporary / "pool-status.json").write_text(json.dumps(pool_status, indent=2, sort_keys=True) + "\n")
        files = sorted(path for path in temporary.iterdir() if path.is_file())
        (temporary / "SHA256SUMS").write_text(
            "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
        )
        os.chmod(temporary, 0o700)
        for path in temporary.iterdir():
            os.chmod(path, 0o600)
        os.replace(temporary, final)
        latest = args.backup_root / "latest"
        replacement = args.backup_root / ".latest.tmp"
        if replacement.exists() or replacement.is_symlink():
            replacement.unlink()
        replacement.symlink_to(final.name)
        os.replace(replacement, latest)
        print(final)
        removed = prune_snapshots(args.backup_root, args.keep_hourly, args.keep_daily)
        if removed:
            print(f"pruned {len(removed)} expired snapshot(s): {', '.join(removed)}")
    finally:
        if temporary.exists():
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()


if __name__ == "__main__":
    main()

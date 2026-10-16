#!/usr/bin/env python3
"""Create a bounded, integrity-checked online backup of BTC-K MAINNET state."""

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


def retention_plan(snapshots: list[Path], keep_daily: int, keep_weekly: int) -> set[Path]:
    """Keep recent UTC daily points plus one Sunday point per retained week."""
    keep: set[Path] = set()
    retained_days: set[str] = set()
    for snapshot in snapshots:
        day = snapshot.name[:8]
        if day in retained_days:
            continue
        retained_days.add(day)
        keep.add(snapshot)
        if len(retained_days) >= keep_daily:
            break
    if keep_weekly == 0:
        return keep
    retained_weeks: set[str] = set()
    for snapshot in snapshots:
        parsed = time.strptime(snapshot.name, "%Y%m%dT%H%M%SZ")
        if parsed.tm_wday != 6:  # Sunday, UTC
            continue
        week = time.strftime("%G-W%V", parsed)
        if week in retained_weeks:
            continue
        retained_weeks.add(week)
        keep.add(snapshot)
        if len(retained_weeks) >= keep_weekly:
            break
    return keep


def prune_snapshots(backup_root: Path, keep_daily: int, keep_weekly: int) -> list[str]:
    snapshots = snapshot_directories(backup_root)
    keep = retention_plan(snapshots, keep_daily, keep_weekly)
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


def backup_sqlite(source: Path, destination: Path, *, page_sleep: float,
                  timeout_seconds: float = 900, clock=time.monotonic, health_check=None) -> None:
    if not source.is_file():
        raise RuntimeError(f"SQLite source is missing: {source}")
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_db = sqlite3.connect(destination, timeout=30)
    started=clock()
    last_health=started
    try:
        # Without a pinned read snapshot, each concurrent source commit can
        # restart SQLite's incremental backup. A busy mainnet may NEVER finish.
        # Pin ONLY WAL databases: a long reader in DELETE mode blocks writers.
        if source_db.execute('PRAGMA journal_mode').fetchone()[0].lower()=='wal':
            source_db.execute('BEGIN')
            source_db.execute('SELECT rootpage FROM sqlite_master LIMIT 1').fetchone()
        def throttle(_status: int, _remaining: int, _total: int) -> None:
            nonlocal last_health
            if clock()-started>timeout_seconds:
                raise TimeoutError('SQLite backup exceeded its bounded deadline')
            if health_check is not None and clock()-last_health>=10:
                health_check()
                last_health=clock()
            if page_sleep:
                time.sleep(page_sleep)

        source_db.backup(destination_db, pages=256, progress=throttle, sleep=0.1)
        # The destination is now independent. Release the WAL pin BEFORE the
        # potentially slower integrity scan, so checkpointing can resume.
        if source_db.in_transaction:
            source_db.rollback()
        def check_progress():
            try:
                throttle(0,0,0)
                return 0
            except Exception:
                return 1
        destination_db.set_progress_handler(check_progress,10000)
        if destination_db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"integrity check failed: {source}")
    finally:
        destination_db.close()
        source_db.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def required_free_bytes(sources: list[Path], minimum_free_bytes: int) -> int:
    return minimum_free_bytes + sum(path.stat().st_size for path in sources if path.is_file())


def assert_disk_headroom(backup_root: Path, sources: list[Path], minimum_free_bytes: int) -> None:
    free_bytes = shutil.disk_usage(backup_root).free
    required = required_free_bytes(sources, minimum_free_bytes)
    if free_bytes < required:
        raise RuntimeError(
            f"insufficient backup headroom: free={free_bytes}, required={required}"
        )


def fetch_json_or_status(url: str) -> dict:
    try:
        return json.load(urlopen(url, timeout=3))
    except (OSError, URLError) as exc:
        return {"status": "unavailable", "url": url, "error": type(exc).__name__}


def assert_mining_health(url,backup_root,minimum_free_bytes):
    """Cached pool data only; abort this optional workload on source distress."""
    with urlopen(url,timeout=2) as response:
        pool=json.load(response)
    age=pool.get('measurement_age_seconds')
    latest=max((b.get('timestamp',0) for b in pool.get('recent_blocks',[])),default=0)
    if (not isinstance(age,(int,float)) or not 0<=age<30 or
            pool.get('active_connections',0)<1 or not 0<=time.time()-latest<45):
        raise RuntimeError('mining health unavailable/stale; nonproduction backup aborted')
    if shutil.disk_usage(backup_root).free<minimum_free_bytes:
        raise RuntimeError('backup/WAL growth reached free-disk reserve')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-db", type=Path, default=Path("/var/lib/btck/mainnet/chain.sqlite3"))
    parser.add_argument(
        "--solo-db", type=Path, default=Path("/var/lib/btck-pool/solo_accounting.sqlite3")
    )
    parser.add_argument(
        "--pplns-db", type=Path, default=Path("/var/lib/btck-pool/pplns_accounting.sqlite3")
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("/opt/btck/current/params/networks/mainnet.json")
    )
    parser.add_argument("--backup-root", type=Path, default=Path("/var/lib/btck-backups"))
    parser.add_argument("--node-url", default="http://127.0.0.1:16110/info")
    parser.add_argument("--solo-status-url", default="http://127.0.0.1:3337/status")
    parser.add_argument("--keep-daily", type=int, default=3)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--minimum-free-gib", type=float, default=8.0)
    parser.add_argument("--page-sleep", type=float, default=0.002)
    parser.add_argument('--database-timeout-seconds',type=float,default=900)
    args = parser.parse_args()
    if args.keep_daily < 1 or args.keep_weekly < 0:
        parser.error("backup retention must keep at least one daily snapshot")
    if args.minimum_free_gib < 1 or args.page_sleep < 0 or not 1<=args.database_timeout_seconds<=1800:
        parser.error("backup safety values are outside their allowed range")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    args.backup_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".backup.", dir=args.backup_root))
    final = args.backup_root / stamp
    try:
        sources = [args.node_db, args.solo_db, args.pplns_db]
        assert_disk_headroom(
            args.backup_root,
            sources,
            int(args.minimum_free_gib * 1024 * 1024 * 1024),
        )
        def health_check():
            assert_mining_health(args.solo_status_url,args.backup_root,int(args.minimum_free_gib*1024**3))
        health_check()
        backup_sqlite(args.node_db, temporary / "chain.sqlite3", page_sleep=args.page_sleep,
                      timeout_seconds=args.database_timeout_seconds,health_check=health_check)
        backup_sqlite(
            args.solo_db,
            temporary / "solo_accounting.sqlite3",
            page_sleep=args.page_sleep,
            timeout_seconds=args.database_timeout_seconds,
            health_check=health_check,
        )
        backup_sqlite(
            args.pplns_db,
            temporary / "pplns_accounting.sqlite3",
            page_sleep=args.page_sleep,
            timeout_seconds=args.database_timeout_seconds,
            health_check=health_check,
        )
        (temporary / "mainnet.json").write_bytes(args.manifest.read_bytes())
        # A transient optional telemetry failure must not discard sound copies.
        node_info = fetch_json_or_status(args.node_url)
        pool_status = fetch_json_or_status(args.solo_status_url)
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
        removed = prune_snapshots(args.backup_root, args.keep_daily, args.keep_weekly)
        if removed:
            print(f"pruned {len(removed)} expired snapshot(s): {', '.join(removed)}")
    finally:
        if temporary.exists():
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()


if __name__ == "__main__":
    main()

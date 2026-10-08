#!/usr/bin/env python3
"""Create an online, integrity-checked backup of private MAINNET guardian state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/btck-mainnet-guardian"))
    parser.add_argument("--backup-root", type=Path, default=Path("/home/ubuntu/btck-mainnet-backups"))
    args = parser.parse_args()
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
        pool_status = json.load(urlopen("http://127.0.0.1:3337/status", timeout=3))
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
    finally:
        if temporary.exists():
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()


if __name__ == "__main__":
    main()

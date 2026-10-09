#!/usr/bin/env python3
"""Verify one pulled Guardian snapshot without mutating live or backup state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config.network import load_network


FORMAT = "btck-guardian-stage-a-snapshot-v1"
REQUIRED_FILES = {
    "chain.sqlite3",
    "pool_accounting.db",
    "mainnet.guardian.json",
    "node-info.json",
    "pool-status.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("malformed SHA256SUMS line")
        digest, name = parts[0].lower(), parts[1].lstrip(" *")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("malformed SHA-256 digest")
        candidate = Path(name)
        if candidate.name != name or name in checksums:
            raise ValueError("unsafe or duplicate SHA256SUMS filename")
        checksums[name] = digest
    return checksums


def sqlite_integrity(path: Path) -> str:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def snapshot_timestamp(name: str) -> int:
    parsed = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def verify_snapshot(
    snapshot: Path,
    *,
    expected_workers: set[str],
    min_online_workers: int,
    max_age_seconds: int,
    now: int | None = None,
) -> dict:
    failures: list[str] = []
    now = int(time.time()) if now is None else int(now)
    try:
        captured_at = snapshot_timestamp(snapshot.name)
    except ValueError:
        captured_at = 0
        failures.append("snapshot directory name is not a UTC backup timestamp")
    age_seconds = max(0, now - captured_at) if captured_at else None
    if age_seconds is None or age_seconds > max_age_seconds:
        failures.append("snapshot is stale")

    checksum_path = snapshot / "SHA256SUMS"
    try:
        checksums = parse_checksums(checksum_path)
    except (OSError, ValueError) as exc:
        checksums = {}
        failures.append(f"cannot validate SHA256SUMS: {exc}")
    missing = sorted(REQUIRED_FILES - set(checksums))
    if missing:
        failures.append(f"SHA256SUMS is missing required files: {', '.join(missing)}")
    for name, expected in checksums.items():
        path = snapshot / name
        if not path.is_file():
            failures.append(f"checksummed file is missing: {name}")
        elif sha256(path) != expected:
            failures.append(f"checksum mismatch: {name}")

    integrity: dict[str, str] = {}
    for name in ("chain.sqlite3", "pool_accounting.db"):
        try:
            integrity[name] = sqlite_integrity(snapshot / name)
            if integrity[name] != "ok":
                failures.append(f"SQLite integrity failed: {name}")
        except (OSError, sqlite3.Error) as exc:
            integrity[name] = type(exc).__name__
            failures.append(f"cannot check SQLite integrity for {name}: {exc}")

    try:
        network = load_network("mainnet", snapshot / "mainnet.guardian.json")
        node = json.loads((snapshot / "node-info.json").read_text(encoding="utf-8"))
        pool = json.loads((snapshot / "pool-status.json").read_text(encoding="utf-8"))
        node_network = node["network"]
        dag = node["dag"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        network = None
        pool = {}
        dag = {}
        node_network = {}
        failures.append(f"cannot load snapshot identity/status: {exc}")

    height = -1
    online_names: set[str] = set()
    if network is not None:
        expected_identity = {
            "name": network.name,
            "chain_id": network.chain_id,
            "genesis_hash": network.genesis.expected_hash,
            "consensus_fingerprint": network.consensus_fingerprint,
        }
        for field, expected in expected_identity.items():
            if node_network.get(field) != expected:
                failures.append(f"node identity mismatch: {field}")
        try:
            height = int(dag["virtual_blue_order_length"]) - 1
            if height < 0:
                failures.append("invalid virtual-blue height")
            if int(dag["issued_supply_sats"]) != int(dag["utxo_total_sats"]):
                failures.append("asset conservation failed")
        except (KeyError, TypeError, ValueError):
            failures.append("node DAG status is incomplete")
        workers = pool.get("workers", []) if isinstance(pool, dict) else []
        if not isinstance(workers, list):
            workers = []
            failures.append("pool worker status is malformed")
        online_names = {
            str(item.get("worker_name"))
            for item in workers
            if isinstance(item, dict) and item.get("online") is True
        }
        if len(online_names) < min_online_workers:
            failures.append("too few online Guardian workers")
        absent = sorted(expected_workers - online_names)
        if absent:
            failures.append(f"expected Guardian workers are offline or missing: {', '.join(absent)}")

    return {
        "format": FORMAT,
        "verification": "PASS" if not failures else "FAIL",
        "snapshot": snapshot.name,
        "captured_at": captured_at,
        "checked_at": now,
        "age_seconds": age_seconds,
        "ledger_height": height,
        "checkpoint_target": 271_828,
        "online_workers": sorted(online_names),
        "sqlite_integrity": integrity,
        "failures": failures,
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--expected-worker", action="append", default=[])
    parser.add_argument("--min-online-workers", type=int, default=2)
    parser.add_argument("--max-age-seconds", type=int, default=8 * 3600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.min_online_workers < 0 or args.max_age_seconds < 1:
        parser.error("worker and freshness limits must be positive")
    result = verify_snapshot(
        args.snapshot.resolve(),
        expected_workers=set(args.expected_worker),
        min_online_workers=args.min_online_workers,
        max_age_seconds=args.max_age_seconds,
    )
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["verification"] != "PASS":
        raise SystemExit("Guardian Stage-A snapshot verification failed")


if __name__ == "__main__":
    main()

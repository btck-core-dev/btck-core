#!/usr/bin/env bash
set -eu

BACKUP_ROOT="${BTCK_BACKUP_ROOT:-/home/ubuntu/btck-backups/live-state}"
NODE_DB="${BTCK_NODE_DB:-/app/data/devnet/chain.sqlite3}"
POOL_DB="${BTCK_POOL_DB:-/app/pool-data/pool_accounting.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_ROOT"
work_dir="$(mktemp -d "$BACKUP_ROOT/.backup.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

backup_sqlite() {
  container="$1"
  source_path="$2"
  container_copy="/tmp/btck-online-backup.sqlite3"
  destination="$3"

  docker exec "$container" python3 -c '
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
target = sqlite3.connect(sys.argv[2], timeout=30)
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"backup integrity check failed: {result}")
finally:
    target.close()
    source.close()
' "$source_path" "$container_copy"
  # Docker's archive-based `cp` does not expose files living on the
  # container's tmpfs mount, so stream the verified backup to the host.
  docker exec "$container" python3 -c '
import sys
with open(sys.argv[1], "rb") as source:
    while chunk := source.read(1024 * 1024):
        sys.stdout.buffer.write(chunk)
' "$container_copy" > "$destination"
  docker exec "$container" rm -f -- "$container_copy"
}

backup_sqlite btck-full-node "$NODE_DB" "$work_dir/chain.sqlite3"
backup_sqlite btck-stratum-pool "$POOL_DB" "$work_dir/pool_accounting.db"

printf '%s\n' \
  "BTC-K online state backup captured at $STAMP UTC." \
  "" \
  "Restore only while the affected container is stopped. Keep chain.sqlite3 bound" \
  "to the same chain_id/genesis/consensus fingerprint. Never mix devnet, testnet," \
  "or mainnet databases. Verify SHA256SUMS before restoring." \
  > "$work_dir/RESTORE.txt"

(cd "$work_dir" && sha256sum chain.sqlite3 pool_accounting.db > SHA256SUMS)
chmod -R go-rwx "$work_dir"
mv "$work_dir" "$BACKUP_ROOT/$STAMP"
trap - EXIT
ln -sfn "$STAMP" "$BACKUP_ROOT/latest"
printf '%s\n' "$BACKUP_ROOT/$STAMP"

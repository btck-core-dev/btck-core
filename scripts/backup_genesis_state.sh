#!/usr/bin/env bash
set -e

BACKUP_DIR="/Users/a1234/btck-core/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
SNAPSHOT_NAME="btck_genesis_snapshot_${TIMESTAMP}"
TARGET_DIR="${BACKUP_DIR}/${SNAPSHOT_NAME}"

mkdir -p "${TARGET_DIR}"
chmod 700 "${TARGET_DIR}"

echo "=========================================================="
echo "📦 Creating BTC-K Genesis State & Chain Snapshot (Secure Mode)"
echo "   Timestamp: ${TIMESTAMP}"
echo "   Destination: ${TARGET_DIR}"
echo "=========================================================="

# 1. Backup chain state if exists
if [ -f /Users/a1234/btck-core/data/chain_state.json ]; then
  cp /Users/a1234/btck-core/data/chain_state.json "${TARGET_DIR}/chain_state.json"
fi

# 2. SECURITY ENFORCEMENT (P0-07):
# Private keys are NEVER bundled into general backup tarballs.
echo "🔒 Note: Secrets are excluded from general node snapshots."

# 3. Snapshot stratum log tail if exists
if [ -f /Users/a1234/btck-core/logs/stratum.log ]; then
  tail -n 1000 /Users/a1234/btck-core/logs/stratum.log > "${TARGET_DIR}/stratum_last_1000_blocks.log"
fi

# 4. Generate SHA256 checksums
cd "${TARGET_DIR}"
shasum -a 256 * > SHA256SUMS.txt 2>/dev/null || true

# 5. Pack into tarball with strict permissions (chmod 600)
cd "${BACKUP_DIR}"
tar -czf "${SNAPSHOT_NAME}.tar.gz" "${SNAPSHOT_NAME}"
chmod 600 "${SNAPSHOT_NAME}.tar.gz"
shasum -a 256 "${SNAPSHOT_NAME}.tar.gz" > "${SNAPSHOT_NAME}.tar.gz.sha256"

echo "✅ Backup successfully created with strict 0600 permissions:"
echo "   Archive: ${BACKUP_DIR}/${SNAPSHOT_NAME}.tar.gz"
echo "   SHA256:  $(cat ${BACKUP_DIR}/${SNAPSHOT_NAME}.tar.gz.sha256)"
echo "=========================================================="

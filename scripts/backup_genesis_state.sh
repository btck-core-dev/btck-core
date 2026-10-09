#!/usr/bin/env bash
set -euo pipefail

# Compatibility entry point. Guardian state is SQLite-backed; the historical
# JSON state backup must never be used for MAINNET recovery.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT_DIR}/deploy/backup_guardian_state.py" "$@"

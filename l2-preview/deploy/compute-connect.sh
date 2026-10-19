#!/usr/bin/env bash
set -euo pipefail
BTCK_CONNECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$BTCK_CONNECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m worker.connect_app "$@"

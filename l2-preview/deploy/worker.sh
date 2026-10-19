#!/usr/bin/env bash
# Run the resident L2 client against an already-loaded local model.
# Does not install/start/stop a Pearl miner or download models.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$#" -lt 1 ]; then
    echo 'Usage: deploy/worker.sh /path/to/private-worker-config.json [--check]' >&2
    exit 2
fi
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ "$1" == --* ]]; then
    exec "${BTCK_WORKER_PYTHON:-python3}" -m worker.resident_worker "$@"
fi
exec "${BTCK_WORKER_PYTHON:-python3}" -m worker.resident_worker --config "$@"

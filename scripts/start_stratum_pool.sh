#!/usr/bin/env bash
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${PROJECT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
python3 -c 'from config.network import require_feature; require_feature("stratum")'
PORT=${1:-3333}
HOST=${2:-0.0.0.0}
LOG_PATH="${PROJECT_DIR}/logs/stratum.log"
mkdir -p "${PROJECT_DIR}/logs"
echo "=========================================================="
echo "⛏️ Starting BTC-K Stratum Pool Server"
echo "   Protocol: BTC-K v2 node-backed Stratum"
echo "   Listening on: ${HOST}:${PORT}"
echo "   Log destination: ${LOG_PATH}"
echo "   Consensus/accounting authority: btckd JSON-RPC"
echo "=========================================================="
exec python3 -u -c "
import asyncio
import sys
sys.path.insert(0, '${PROJECT_DIR}/src')
from stratum.stratum_server import BTCKStratumServer

async def run():
    server = BTCKStratumServer(host='${HOST}', port=${PORT})
    await server.start()
    while True:
        await asyncio.sleep(3600)

asyncio.run(run())
" 2>&1 | tee -a "${LOG_PATH}"

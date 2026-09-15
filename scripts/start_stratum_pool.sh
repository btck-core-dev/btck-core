#!/usr/bin/env bash
set -e
PORT=${1:-3333}
HOST=${2:-0.0.0.0}
LOG_PATH="/Users/a1234/btck-core/logs/stratum.log"
mkdir -p /Users/a1234/btck-core/logs
echo "=========================================================="
echo "⛏️ Starting BTC-K Stratum Pool Server"
echo "   Target ASIC: IceRiver KS0/KS1/KS2/KS3/KS5, Antminer KS3"
echo "   Listening on: ${HOST}:${PORT}"
echo "   Log destination: ${LOG_PATH}"
echo "   10x Combo Lucky Share Personal Award: ENABLED (P=1/41)"
echo "=========================================================="
exec python3 -u -c "
import asyncio
import sys
sys.path.insert(0, '/Users/a1234/btck-core/src')
from stratum.stratum_server import BTCKStratumServer

async def run():
    server = BTCKStratumServer(host='${HOST}', port=${PORT})
    await server.start()
    while True:
        await asyncio.sleep(3600)

asyncio.run(run())
" 2>&1 | tee -a "${LOG_PATH}"

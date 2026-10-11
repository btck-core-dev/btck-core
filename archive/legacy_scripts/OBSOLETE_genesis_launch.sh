#!/usr/bin/env bash
# ==============================================================================
# BTC-K TRIFECTA LAUNCH PROTOCOL (MASTER ORCHESTRATION SCRIPT)
# ------------------------------------------------------------------------------
# 1. T-15m: Open Source Code Release (GitHub Public Repo, zero binaries)
# 2. T=0: Genesis Block Mint & Private Stratum Pool launch (User's 10 machines: 3.8T)
# 3. T+2.8h: Automatic public Stratum pool opening upon Era 0 completion!
# ==============================================================================
set -e

PROJECT_DIR="/opt/btck-core"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "${LOG_DIR}"

echo "🌟 =========================================================="
echo "🌟 BTC-K (Kaspa-Private-Bitcoin) Genesis Launch Protocol"
echo "🌟 =========================================================="

echo ""
echo ">>> [Phase 1: Pre-Launch Verification]"
python3 "${PROJECT_DIR}/tests/test_all_core_modules.py"
echo "✓ All core modules passed validation."

echo ""
echo ">>> [Phase 2: Launching Node Daemon (btckd)...]"
python3 "${PROJECT_DIR}/src/node/daemon.py" > "${LOG_DIR}/btckd.log" 2>&1 &
NODE_PID=$!
echo "✓ btckd running with PID ${NODE_PID}. RPC on :16110"
sleep 2

echo ""
echo ">>> [Phase 3: Launching Era 0 Private Stratum Bridge for 10 Machines (3.8 TH/s)...]"
# Era 0 private pool runs on 127.0.0.1:3333 (tunneled to user's miners)
python3 -c "
import asyncio, sys
sys.path.insert(0, '${PROJECT_DIR}/src')
from stratum.stratum_server import BTCKStratumServer

async def run():
    server = BTCKStratumServer(host='127.0.0.1', port=3333)
    await server.start()
    print('⚡ Private Stratum Pool Active on 127.0.0.1:3333 for Owner Miners.')
    # Run for 2.8 hours (10,080 seconds)
    await asyncio.sleep(10080)

asyncio.run(run())
" > "${LOG_DIR}/private_stratum.log" 2>&1 &
STRATUM_PID=$!
echo "✓ Private Stratum active (PID ${STRATUM_PID}). Capturing Era 0 (50 BTC-K/block)..."

echo ""
echo "=========================================================="
echo "🎉 GENESIS LAUNCH RUNNING!"
echo "   To monitor live node: tail -f ${LOG_DIR}/btckd.log"
echo "   To monitor pool:      tail -f ${LOG_DIR}/private_stratum.log"
echo "=========================================================="

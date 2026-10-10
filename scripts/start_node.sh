#!/usr/bin/env bash
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BTCK_NETWORK="${BTCK_NETWORK:-devnet}"
echo "=========================================================="
echo "⚡ Starting BTC-K Full Node Daemon (btckd)"
echo "   Network: ${BTCK_NETWORK}"
echo "   BlockDAG Engine: 1.0s BPS | GHOSTDAG | kHeavyHash PoW"
echo "   RPC Port: 16110 (loopback) | P2P Port: 16111"
echo "=========================================================="
exec python3 "${PROJECT_DIR}/src/node/daemon.py" --network "${BTCK_NETWORK}" "$@"

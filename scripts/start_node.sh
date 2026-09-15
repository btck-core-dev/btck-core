#!/usr/bin/env bash
set -e
echo "=========================================================="
echo "⚡ Starting BTC-K Full Node Daemon (btckd)"
echo "   BlockDAG Engine: 1.0s BPS | GHOSTDAG | kHeavyHash PoW"
echo "   RPC Port: 16110 | P2P Port: 16112"
echo "=========================================================="
python3 /Users/a1234/btck-core/src/node/daemon.py

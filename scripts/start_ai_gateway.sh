#!/usr/bin/env bash
set -e
PORT=${1:-8080}
HOST=${2:-0.0.0.0}
echo "=========================================================="
echo "🤖 Starting BTCK AI-RPC Gateway Server (RFC 7231 HTTP 402)"
echo "   Listening on: http://${HOST}:${PORT}"
echo "   Supported Models: DeepSeek-R1, DeepSeek-V3, GPT-4o, Llama-3.3"
echo "   Settlement Currency: SatK (0.01 SatK / token benchmark)"
echo "=========================================================="
python3 /Users/a1234/btck-core/src/gateway/ai_rpc_gateway.py

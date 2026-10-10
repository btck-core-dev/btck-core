#!/usr/bin/env bash
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${PROJECT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
python3 -c 'from config.network import require_feature; require_feature("ai_payments")'
PORT=${1:-8080}
HOST=${2:-0.0.0.0}
echo "=========================================================="
echo "🤖 Starting BTCK AI-RPC Gateway Server (RFC 7231 HTTP 402)"
echo "   Listening on: http://${HOST}:${PORT}"
echo "   Supported Models: DeepSeek-R1, DeepSeek-V3, GPT-4o, Llama-3.3"
echo "   Settlement Currency: SatK (0.01 SatK / token benchmark)"
echo "=========================================================="
exec python3 "${PROJECT_DIR}/src/gateway/ai_rpc_gateway.py"

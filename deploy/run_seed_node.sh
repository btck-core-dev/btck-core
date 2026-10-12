#!/usr/bin/env bash
set -euo pipefail

: "${BTCK_NETWORK:?BTCK_NETWORK is required}"
: "${BTCK_NETWORK_MANIFEST:?BTCK_NETWORK_MANIFEST is required}"
: "${BTCK_DATA_ROOT:?BTCK_DATA_ROOT is required}"
: "${BTCK_RPC_HOST:?BTCK_RPC_HOST is required}"
: "${BTCK_RPC_PORT:?BTCK_RPC_PORT is required}"
: "${BTCK_P2P_HOST:?BTCK_P2P_HOST is required}"
: "${BTCK_P2P_PORT:?BTCK_P2P_PORT is required}"

[[ "$BTCK_NETWORK" == "mainnet" ]] || { echo "Seed service only permits mainnet." >&2; exit 1; }
[[ "$BTCK_NETWORK_MANIFEST" == "/opt/btck/current/params/networks/mainnet.json" ]] || {
  echo "Seed service requires the release-bound mainnet manifest." >&2
  exit 1
}
[[ "$BTCK_RPC_HOST" == "127.0.0.1" || "$BTCK_RPC_HOST" == "::1" ]] || {
  echo "Refusing to expose JSON-RPC beyond loopback." >&2
  exit 1
}
[[ "$BTCK_P2P_HOST" == "0.0.0.0" || "$BTCK_P2P_HOST" == "::" ]] || {
  echo "Unexpected P2P bind address." >&2
  exit 1
}

args=(
  /opt/btck/current/src/node/daemon.py
  --network mainnet
  --network-manifest "$BTCK_NETWORK_MANIFEST"
  --data-root "$BTCK_DATA_ROOT"
  --host "$BTCK_RPC_HOST"
  --port "$BTCK_RPC_PORT"
  --p2p-host "$BTCK_P2P_HOST"
  --p2p-port "$BTCK_P2P_PORT"
)

if [[ -n "${BTCK_SEEDS:-}" ]]; then
  IFS=',' read -r -a seeds <<< "$BTCK_SEEDS"
  for seed in "${seeds[@]}"; do
    [[ "$seed" =~ ^[A-Za-z0-9._-]+:[0-9]{1,5}$ ]] || { echo "Invalid seed endpoint: $seed" >&2; exit 1; }
    seed_port="${seed##*:}"
    ((seed_port >= 1 && seed_port <= 65535)) || { echo "Invalid seed port: $seed" >&2; exit 1; }
    args+=(--seed "$seed")
  done
fi

exec /usr/bin/python3 "${args[@]}"

#!/bin/sh
set -eu

ENV_FILE="${1:-/home/ubuntu/btck-mainnet-guardian/evidence/guardian.env}"
KEY_NAME="BTCK_PUBLIC_MINER_ID_KEY"

if [ ! -f "$ENV_FILE" ]; then
    echo "Environment file not found: $ENV_FILE" >&2
    exit 1
fi

if grep -q "^${KEY_NAME}=" "$ENV_FILE"; then
    echo "Public miner ID key is already installed."
    exit 0
fi

secret="$(openssl rand -hex 32)"
printf '\n%s=%s\n' "$KEY_NAME" "$secret" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "Installed a persistent public miner ID key without printing it."

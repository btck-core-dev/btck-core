#!/usr/bin/env bash
set -euo pipefail

# One-shot Stage-A guardian launch. This does not expose MAINNET P2P/RPC or
# delete the preserved DEVNET volumes. Run only from the isolated guardian tree.
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_EPOCH=1791442800
CUTOVER_EPOCH=$((LAUNCH_EPOCH - 15))
GENESIS_BITS=0x1c017668
MINER_PUBLIC_IP=183.12.45.145
APPROVAL_ID=BTCK-GUARDIAN-20261008-1500-CST
LOCKED_MANIFEST="$PROJECT_DIR/params/networks/mainnet.json"
GUARDIAN_MANIFEST="$PROJECT_DIR/params/networks/mainnet.guardian.json"
EVIDENCE_DIR="$PROJECT_DIR/evidence"
RESULT="$EVIDENCE_DIR/mainnet-genesis-result.json"
RPC_TOKEN_FILE="$EVIDENCE_DIR/mainnet-rpc-token"
NODE_LOG="$PROJECT_DIR/logs/mainnet-node.log"
POOL_LOG="$PROJECT_DIR/logs/mainnet-stratum.log"
DATA_ROOT="$PROJECT_DIR/data-root"
POOL_DB="$PROJECT_DIR/pool-data/pool_accounting.db"
PERSONAL_ADDRESS=btck1s6LLVmAk5s1PN5VRRh2h4AqmGnASiPe4reShbabFbEFSp7zPRm92MrprgtkgqQTg9C9vZgFmfdfYLkm4NhXWmbZLujFbXkA1
POOL_ADDRESS=btck1sAPmQArRKd4jw8xVrVrvzUKz3BXCY7QYuk27wS9nUYMApuF8u8QfQV5KNFfejoPEf8m1r5qsYRSz8S44wG3ZiWTzy8guRRuK

mkdir -p "$EVIDENCE_DIR" "$PROJECT_DIR/logs" "$DATA_ROOT" "$PROJECT_DIR/pool-data"
chmod 700 "$EVIDENCE_DIR" "$DATA_ROOT" "$PROJECT_DIR/pool-data"
if [ -e "$RESULT" ] || [ -e "$GUARDIAN_MANIFEST" ]; then
    echo "Refusing to overwrite an existing genesis result or guardian manifest" >&2
    exit 1
fi

while [ "$(date +%s)" -lt "$CUTOVER_EPOCH" ]; do
    sleep 1
done

printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ) DEVNET Stratum cutover started" >> "$EVIDENCE_DIR/ceremony.log"
docker stop btck-stratum-pool >> "$EVIDENCE_DIR/ceremony.log"

# The old Stratum ports were Docker-forwarded. The fixed genesis server runs on
# the host and accepts only the already-observed home miner public IP.
if ! sudo -n iptables -C INPUT -s "$MINER_PUBLIC_IP" -p tcp --dport 3333 -m comment --comment btck-mainnet-guardian -j ACCEPT 2>/dev/null; then
    sudo -n iptables -I INPUT 3 -s "$MINER_PUBLIC_IP" -p tcp --dport 3333 -m comment --comment btck-mainnet-guardian -j ACCEPT
fi

while [ "$(date +%s)" -lt "$LAUNCH_EPOCH" ]; do
    sleep 1
done
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ) fixed genesis search started" >> "$EVIDENCE_DIR/ceremony.log"

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR/src" python3 scripts/mine_mainnet_genesis.py \
    --manifest "$LOCKED_MANIFEST" \
    --timestamp "$LAUNCH_EPOCH" \
    --bits "$GENESIS_BITS" \
    --host 0.0.0.0 \
    --port 3333 \
    --difficulty 128 \
    --result "$RESULT" \
    --serve >> "$EVIDENCE_DIR/genesis-stratum.log" 2>&1

PYTHONPATH="$PROJECT_DIR/src" python3 scripts/finalize_mainnet_genesis.py \
    --manifest "$LOCKED_MANIFEST" \
    --result "$RESULT" \
    --output "$GUARDIAN_MANIFEST" \
    --timestamp "$LAUNCH_EPOCH" \
    --approval-id "$APPROVAL_ID" > "$EVIDENCE_DIR/finalize-report.json"

if [ ! -e "$RPC_TOKEN_FILE" ]; then
    umask 077
    openssl rand -hex 32 > "$RPC_TOKEN_FILE"
fi
RPC_TOKEN="$(tr -d '\r\n' < "$RPC_TOKEN_FILE")"

nohup env PYTHONPATH="$PROJECT_DIR/src" BTCK_NETWORK=mainnet \
    BTCK_NETWORK_MANIFEST="$GUARDIAN_MANIFEST" BTCK_DATA_ROOT="$DATA_ROOT" \
    BTCK_RPC_TOKEN="$RPC_TOKEN" \
    python3 "$PROJECT_DIR/src/node/daemon.py" \
    --network mainnet --network-manifest "$GUARDIAN_MANIFEST" \
    --data-root "$DATA_ROOT" --host 127.0.0.1 --port 17110 \
    --p2p-host 127.0.0.1 --p2p-port 17111 > "$NODE_LOG" 2>&1 &
echo "$!" > "$EVIDENCE_DIR/mainnet-node.pid"

for _attempt in $(seq 1 60); do
    if curl -fsS --max-time 1 http://127.0.0.1:17110/info > "$EVIDENCE_DIR/mainnet-node-info.json"; then
        break
    fi
    sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:17110/info >/dev/null

nohup env PYTHONPATH="$PROJECT_DIR/src" BTCK_NETWORK=mainnet \
    BTCK_NETWORK_MANIFEST="$GUARDIAN_MANIFEST" BTCK_RPC_TOKEN="$RPC_TOKEN" \
    BTCK_NODE_RPC_URL=http://127.0.0.1:17110/rpc \
    BTCK_STRATUM_HOST=0.0.0.0 BTCK_STRATUM_PORTS=3333 \
    BTCK_STRATUM_STATUS_HOST=127.0.0.1 BTCK_STRATUM_STATUS_PORT=3337 \
    BTCK_POOL_MODE=solo BTCK_POOL_LAUNCH_TIME="$LAUNCH_EPOCH" \
    BTCK_PPLNS_DB_PATH="$POOL_DB" \
    BTCK_POOL_FEE_ADDRESS="$POOL_ADDRESS" BTCK_POOL_TREASURY_ADDRESS="$POOL_ADDRESS" \
    BTCK_GUARDIAN_PAYOUT_ADDRESS="$PERSONAL_ADDRESS" \
    python3 "$PROJECT_DIR/src/stratum/stratum_server.py" > "$POOL_LOG" 2>&1 &
echo "$!" > "$EVIDENCE_DIR/mainnet-stratum.pid"

for _attempt in $(seq 1 60); do
    if curl -fsS --max-time 1 http://127.0.0.1:3337/status > "$EVIDENCE_DIR/mainnet-pool-status.json"; then
        break
    fi
    sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:3337/status >/dev/null
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ) guardian node and Stratum active" >> "$EVIDENCE_DIR/ceremony.log"

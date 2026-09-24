#!/usr/bin/env bash
# HiveOS execution wrapper for BTC-K Symbiotic Worker

CUSTOM_DIR=$(dirname "$0")
cd "$CUSTOM_DIR" || exit 1

. /hive/custom/btck-symbiotic/h-manifest.conf
CONF_FILE="/hive/miners/custom/btck-symbiotic/worker_config.json"

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

LOG_FILE="/var/log/miner/custom/btck-symbiotic/btck-symbiotic.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Execute symbiotic worker master orchestrator
exec python3 -u "$CUSTOM_DIR/symbiotic_worker.py" --config "$CONF_FILE" 2>&1 | tee -a "$LOG_FILE"

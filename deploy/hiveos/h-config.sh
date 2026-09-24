#!/usr/bin/env bash
# Generates BTC-K Symbiotic Worker configuration from HiveOS Flightsheet variables

CONF_FILE="/hive/miners/custom/btck-symbiotic/worker_config.json"

BTCK_WAL="${CUSTOM_TEMPLATE}"
PEARL_POOL="${CUSTOM_URL:-pearl-eu1.lproute.com:3360}"
PRL_WAL="${CUSTOM_USER_CONFIG:-prl1p5tmmse5204pjpylfe6ghcmchmx5rm93jyvns2x78ltfn9c5rsgsqw8n3d5}"
GATEWAY_URL="https://api.btck.network/v1"

cat << JSON > "$CONF_FILE"
{
  "payout_address": "$BTCK_WAL",
  "gateway_url": "$GATEWAY_URL",
  "pearl_pool": "$PEARL_POOL",
  "prl_wallet": "$PRL_WAL",
  "auto_preempt": true,
  "safety_timeout_sec": 30.0
}
JSON

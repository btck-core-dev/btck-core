#!/usr/bin/env bash
# HiveOS Stats Reporting (reports hashrate, temperatures, and fan speeds to HiveOS dashboard)

stats=""
khs=0

# Query local SRBMiner API for Pearl Hashrate
if curl -s -m 2 http://127.0.0.1:21550/api > /tmp/srb_stats.json; then
    khs=$(jq -r '.hashrate_total_now // 0' /tmp/srb_stats.json 2>/dev/null | awk '{print $1 / 1000}')
fi

# Query NVIDIA GPU stats
gpu_temps=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | tr '\n' ' ' | sed 's/ $//')
gpu_fans=$(nvidia-smi --query-gpu=fan.speed --format=csv,noheader,nounits | tr '\n' ' ' | sed 's/ $//')

stats=$(jq -nc \
    --arg khs "$khs" \
    --arg hs "$khs" \
    --arg temp "$gpu_temps" \
    --arg fan "$gpu_fans" \
    '{"khs": ($khs|tonumber), "hs": [($hs|tonumber)], "temp": [($temp|split(" ")|map(tonumber))[]], "fan": [($fan|split(" ")|map(tonumber))[]], "algo": "pearlhash+ai"}')

echo "$stats"

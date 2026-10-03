#!/usr/bin/env bash
set -eu

OBSERVATION_DIR="${BTCK_OBSERVATION_DIR:-/home/ubuntu/btck-observation}"
mkdir -p "$OBSERVATION_DIR"
touch "$OBSERVATION_DIR/stratum-rejections-wire.log"
chown ubuntu:ubuntu "$OBSERVATION_DIR/stratum-rejections-wire.log"
chmod 600 "$OBSERVATION_DIR/stratum-rejections-wire.log"

exec /usr/bin/tcpdump -l -nn -s 0 -A -i any \
  '(tcp src port 3333 or tcp src port 3334 or tcp src port 3335 or tcp src port 3336)' 2>/dev/null \
  | /usr/bin/awk '
      / IP / { header=$0 }
      /"error":\[(21|22|23|25),/ {
          print strftime("%Y-%m-%dT%H:%M:%SZ", systime()) "\t" header "\t" $0
          fflush()
      }
    ' >> "$OBSERVATION_DIR/stratum-rejections-wire.log"

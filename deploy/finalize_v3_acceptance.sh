#!/usr/bin/env bash
set -eu

PROJECT_DIR="${BTCK_PROJECT_DIR:-/home/ubuntu/btck-devnet}"
OBSERVATION_DIR="${BTCK_OBSERVATION_DIR:-/home/ubuntu/btck-observation}"

snapshot="$($PROJECT_DIR/deploy/capture_v3_observation.sh)"
stamp="$(basename "$snapshot")"
report="$OBSERVATION_DIR/acceptance-report-$stamp.json"

python3 "$PROJECT_DIR/deploy/summarize_v3_acceptance.py" \
  --observation-dir "$OBSERVATION_DIR" \
  --end "$stamp" \
  --output "$report" \
  >/dev/null

ln -sfn "$(basename "$report")" "$OBSERVATION_DIR/acceptance-report-latest.json"
printf '%s\n' "$report"

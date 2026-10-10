#!/usr/bin/env bash
# Package HiveOS custom miner archive
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="/tmp/btck-symbiotic-hiveos"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

cp "$DIR/h-manifest.conf" "$PACKAGE_DIR/"
cp "$DIR/h-config.sh" "$PACKAGE_DIR/"
cp "$DIR/h-run.sh" "$PACKAGE_DIR/"
cp "$DIR/h-stats.sh" "$PACKAGE_DIR/"
cp "$DIR/../../src/worker/symbiotic_worker.py" "$PACKAGE_DIR/"
cp "$DIR/../../src/worker/process_preemptor.py" "$PACKAGE_DIR/"
cp "$DIR/../../src/worker/hardware_probe.py" "$PACKAGE_DIR/"
cp "$DIR/../../src/worker/pricing_engine.py" "$PACKAGE_DIR/"
cp "$DIR/../../src/worker/tui_console.py" "$PACKAGE_DIR/"

OUT_TAR="${DIR}/btck-symbiotic-1.0.0.tar.gz"
tar -czf "$OUT_TAR" -C "/tmp" "btck-symbiotic-hiveos"
echo "HiveOS Custom Miner Package Built: $OUT_TAR"

#!/usr/bin/env bash
# One-click Seed Node Setup for AWS / Hetzner / DigitalOcean (Ubuntu/Debian)
set -e

echo "=========================================================="
echo "🌐 Installing BTC-K Independent Seed Node"
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

apt-get update -y && apt-get install -y python3 python3-pip git ufw curl

# Allow P2P and RPC ports
ufw allow 16110/tcp comment "BTC-K RPC"
ufw allow 16111/tcp comment "BTC-K P2P Gossip"
ufw allow 3333/tcp comment "BTC-K Stratum Pool"

# Setup btck service
mkdir -p /opt/btck-core /opt/btck-core/data /opt/btck-core/logs
cp -r /Users/a1234/btck-core/src /opt/btck-core/
cp /Users/a1234/btck-core/deploy/btckd.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable btckd
systemctl restart btckd

echo "✅ BTC-K Seed Node successfully installed and started!"
echo "   Status: systemctl status btckd"
echo "   RPC Endpoint: http://127.0.0.1:16110/info"
echo "=========================================================="

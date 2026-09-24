#!/usr/bin/env bash
# ==============================================================================
# BTC-K Sovereign Cloud Node Bootstrap Script (AWS / Hetzner / DigitalOcean)
# Ubuntu 22.04 / 24.04 / Debian 11 / 12
# ==============================================================================
set -e

echo "=========================================================="
echo "🌐 Installing BTC-K Production Cloud Node & Stratum Pool"
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root (sudo)"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "📦 Step 1/5: Updating system packages and dependencies..."
apt-get update -y
apt-get install -y python3 python3-pip git ufw curl jq net-tools

# Enable TCP BBR for high-speed cross-border P2P and Stratum low latency
if ! sysctl net.ipv4.tcp_congestion_control | grep -q bbr; then
  echo "⚡ Enabling TCP BBR congestion control..."
  modprobe tcp_bbr 2>/dev/null || true
  echo "net.core.default_qdisc=fq" >> /etc/sysctl.d/99-btck.conf
  echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.d/99-btck.conf
  sysctl -p /etc/sysctl.d/99-btck.conf 2>/dev/null || true
fi

echo "👤 Step 2/5: Configuring dedicated 'btck' system user..."
if ! id -u btck >/dev/null 2>&1; then
  useradd -r -s /bin/false -d /opt/btck-core btck
  echo "✓ Created isolated system user 'btck'"
fi

echo "📁 Step 3/5: Deploying BTC-K codebase to /opt/btck-core..."
mkdir -p /opt/btck-core /opt/btck-core/data /opt/btck-core/logs /opt/btck-core/secrets

if [ -d "$REPO_ROOT/src" ]; then
  cp -r "$REPO_ROOT/src" /opt/btck-core/
  [ -d "$REPO_ROOT/secrets" ] && cp -r "$REPO_ROOT/secrets/"* /opt/btck-core/secrets/ 2>/dev/null || true
else
  echo "Cloning repository from source..."
  git clone https://github.com/btck-network/btck-core.git /opt/btck-core/repo_tmp
  cp -r /opt/btck-core/repo_tmp/src /opt/btck-core/
  rm -rf /opt/btck-core/repo_tmp
fi

chown -R btck:btck /opt/btck-core
chmod 700 /opt/btck-core/secrets 2>/dev/null || true

echo "🛡️ Step 4/5: Configuring UFW firewall rules..."
ufw allow 22/tcp comment "SSH Remote Access" || true
ufw allow 16110/tcp comment "BTC-K RPC Gateway"
ufw allow 16111/tcp comment "BTC-K P2P Gossip Mesh"
ufw allow 3333/tcp comment "BTC-K Stratum Mining Pool"
ufw --force enable || true

echo "⚙️ Step 5/5: Installing and starting Systemd services (btckd & btck-stratum)..."
cat << 'EOF' > /etc/systemd/system/btckd.service
[Unit]
Description=BTC-K Sovereign BlockDAG Node & RPC Daemon
After=network.target

[Service]
Type=simple
User=btck
WorkingDirectory=/opt/btck-core
ExecStart=/usr/bin/python3 /opt/btck-core/src/node/daemon.py --port 16110 --p2p-port 16111
Restart=always
RestartSec=5s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' > /etc/systemd/system/btck-stratum.service
[Unit]
Description=BTC-K High-Performance Stratum Mining Pool Server
After=network.target btckd.service

[Service]
Type=simple
User=btck
WorkingDirectory=/opt/btck-core
ExecStart=/usr/bin/python3 /opt/btck-core/src/stratum/stratum_server.py
Restart=always
RestartSec=5s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable btckd btck-stratum
systemctl restart btckd btck-stratum

echo "⏳ Verifying services startup (waiting 2s)..."
sleep 2

PUBLIC_IP=$(curl -s -m 3 https://api.ipify.org || hostname -I | awk '{print $1}')
echo "=========================================================="
echo "✅ BTC-K Cloud Node & Stratum Pool successfully launched!"
echo "   • P2P Gossip Mesh: ${PUBLIC_IP}:16111"
echo "   • JSON-RPC Gateway: http://${PUBLIC_IP}:16110"
echo "   • Official Stratum Pool: stratum+tcp://${PUBLIC_IP}:3333"
echo "   • Node Status: systemctl status btckd"
echo "   • Pool Status: systemctl status btck-stratum"
echo "   • Pool Logs: journalctl -u btck-stratum -f"
echo "=========================================================="

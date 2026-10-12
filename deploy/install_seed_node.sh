#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo deploy/install_seed_node.sh \
  --release-dir /path/to/signed-release \
  --trusted-public-key /path/to/pre-distributed-release-key.pub \
  [--seed host:port]... [--start]

Verifies the complete signed release before installing its node archive.
The installer never changes the host firewall and never exposes JSON-RPC.
Without --start it installs and enables the service but leaves it stopped.
EOF
}

RELEASE_DIR=""
TRUSTED_PUBLIC_KEY=""
START_SERVICE=0
SEEDS=()
while (($#)); do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:?missing --release-dir value}"; shift 2 ;;
    --trusted-public-key) TRUSTED_PUBLIC_KEY="${2:?missing --trusted-public-key value}"; shift 2 ;;
    --seed) SEEDS+=("${2:?missing --seed value}"); shift 2 ;;
    --start) START_SERVICE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root (sudo)." >&2
  exit 1
fi
[[ -n "$RELEASE_DIR" && -d "$RELEASE_DIR" ]] || { echo "A valid --release-dir is required." >&2; exit 1; }
[[ -n "$TRUSTED_PUBLIC_KEY" && -f "$TRUSTED_PUBLIC_KEY" ]] || { echo "A valid --trusted-public-key is required." >&2; exit 1; }

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd)"
TRUSTED_PUBLIC_KEY="$(cd "$(dirname "$TRUSTED_PUBLIC_KEY")" && pwd)/$(basename "$TRUSTED_PUBLIC_KEY")"

python3 "$SOURCE_ROOT/scripts/verify_mainnet_release.py" "$RELEASE_DIR" \
  --trusted-public-key "$TRUSTED_PUBLIC_KEY"

readarray -t RELEASE_FIELDS < <(python3 - "$RELEASE_DIR/RELEASE_MANIFEST.json" <<'PY'
import json
import sys
from pathlib import Path

release = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
version = release.get("version", "")
if release.get("status") != "PUBLIC-MAINNET-CHECKPOINT-BOUND" or not version:
    raise SystemExit("release manifest is not an approved public MAINNET release")
expected = f"btck-node-{version}.tar.gz"
if expected not in release.get("artifacts", {}):
    raise SystemExit("release manifest does not contain the expected node archive")
print(version)
print(expected)
print(release["artifacts"][expected])
PY
)
VERSION="${RELEASE_FIELDS[0]}"
ARCHIVE_NAME="${RELEASE_FIELDS[1]}"
ARCHIVE_SHA256="${RELEASE_FIELDS[2]}"
ARCHIVE="$RELEASE_DIR/$ARCHIVE_NAME"

for seed in "${SEEDS[@]}"; do
  [[ "$seed" =~ ^[A-Za-z0-9._-]+:[0-9]{1,5}$ ]] || {
    echo "Invalid seed endpoint: $seed" >&2
    exit 1
  }
  seed_port="${seed##*:}"
  ((seed_port >= 1 && seed_port <= 65535)) || { echo "Invalid seed port: $seed" >&2; exit 1; }
done

install -d -o root -g root -m 0755 /opt/btck /opt/btck/releases
install -d -o root -g root -m 0755 /etc/btck
if ! getent group btck >/dev/null; then
  groupadd --system btck
fi
if ! id -u btck >/dev/null 2>&1; then
  useradd --system --gid btck --home-dir /var/lib/btck --shell /usr/sbin/nologin btck
fi
install -d -o btck -g btck -m 0750 /var/lib/btck

TARGET="/opt/btck/releases/$VERSION"
if [[ -e "$TARGET" ]]; then
  [[ -f "$TARGET/.release-archive-sha256" ]] || { echo "Existing release lacks provenance marker: $TARGET" >&2; exit 1; }
  [[ "$(cat "$TARGET/.release-archive-sha256")" == "$ARCHIVE_SHA256" ]] || {
    echo "Existing release differs from the signed archive: $TARGET" >&2
    exit 1
  }
else
  STAGING="$(mktemp -d /opt/btck/releases/.install.XXXXXX)"
  cleanup() { rm -rf -- "$STAGING"; }
  trap cleanup EXIT
  python3 - "$ARCHIVE" "$STAGING" "$VERSION" <<'PY'
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path, staging, version = map(Path, sys.argv[1:])
expected_root = f"btck-node-{version}"
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("node archive is empty")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
            raise SystemExit(f"unsafe node archive path: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported node archive entry: {member.name}")
    archive.extractall(staging)
PY
  EXTRACTED="$STAGING/btck-node-$VERSION"
  [[ -d "$EXTRACTED" ]] || { echo "Expected node archive root is missing." >&2; exit 1; }
  printf '%s\n' "$ARCHIVE_SHA256" > "$EXTRACTED/.release-archive-sha256"
  chown -R root:root "$EXTRACTED"
  chmod -R go-w "$EXTRACTED"
  mv "$EXTRACTED" "$TARGET"
  trap - EXIT
  rm -rf -- "$STAGING"
fi

ln -sfn "$TARGET" /opt/btck/current.new
mv -Tf /opt/btck/current.new /opt/btck/current

seed_csv=""
if ((${#SEEDS[@]})); then
  seed_csv="$(IFS=,; printf '%s' "${SEEDS[*]}")"
fi
ENV_TMP="$(mktemp /etc/btck/.btckd.env.XXXXXX)"
trap 'rm -f -- "$ENV_TMP"' EXIT
cat > "$ENV_TMP" <<EOF
BTCK_NETWORK=mainnet
BTCK_NETWORK_MANIFEST=/opt/btck/current/params/networks/mainnet.json
BTCK_DATA_ROOT=/var/lib/btck
BTCK_RPC_HOST=127.0.0.1
BTCK_RPC_PORT=16110
BTCK_P2P_HOST=0.0.0.0
BTCK_P2P_PORT=16111
BTCK_SEEDS=$seed_csv
EOF
chmod 0640 "$ENV_TMP"
chown root:btck "$ENV_TMP"
mv -f "$ENV_TMP" /etc/btck/btckd.env
trap - EXIT

install -o root -g root -m 0644 "$TARGET/deploy/btckd.service" /etc/systemd/system/btckd.service
systemctl daemon-reload
systemctl enable btckd.service >/dev/null
if ((START_SERVICE)); then
  systemctl restart btckd.service
  systemctl --no-pager --full status btckd.service
else
  echo "Installed BTC-K $VERSION and enabled btckd.service; service remains stopped."
  echo "Review /etc/btck/btckd.env and the host firewall, then start it explicitly."
fi

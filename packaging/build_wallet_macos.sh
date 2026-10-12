#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${BTCK_WALLET_BUILD_ROOT:-$PROJECT_DIR/build/wallet-macos}"
RELEASE_DIR="${BTCK_WALLET_RELEASE_DIR:-$PROJECT_DIR/dist/wallet}"
VENV_DIR="$BUILD_ROOT/venv"
ARCH="$(uname -m)"
VERSION="${BTCK_WALLET_VERSION:-3.2.4}"
NETWORK_MANIFEST="${BTCK_WALLET_NETWORK_MANIFEST:-$PROJECT_DIR/params/networks/mainnet.json}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$PROJECT_DIR" show -s --format=%ct HEAD)}"
export SOURCE_DATE_EPOCH
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

case "$ARCH" in
  arm64|x86_64) ;;
  *)
    printf 'Unsupported macOS architecture: %s\n' "$ARCH" >&2
    exit 1
    ;;
esac

if [ "$(uname -s)" != "Darwin" ]; then
  printf 'This builder must run on macOS.\n' >&2
  exit 1
fi

mkdir -p "$BUILD_ROOT" "$RELEASE_DIR"
rm -rf "$BUILD_ROOT/pyinstaller" "$BUILD_ROOT/package" "$BUILD_ROOT/zipapp"

if xcrun -f lipo >/dev/null 2>&1; then
  PACKAGE_NAME="BTC-K-Wallet-${VERSION}-macOS-${ARCH}"
  mkdir -p "$BUILD_ROOT/package/$PACKAGE_NAME"
  PYINSTALLER_BIN="${BTCK_PYINSTALLER_BIN:-$(command -v pyinstaller || true)}"
  if [ -z "$PYINSTALLER_BIN" ] || [ "$($PYINSTALLER_BIN --version)" != "6.16.0" ]; then
    printf 'Install pinned pyinstaller 6.16.0 offline and set BTCK_PYINSTALLER_BIN.\n' >&2
    exit 1
  fi
  cd "$PROJECT_DIR"
  "$PYINSTALLER_BIN" \
    --noconfirm \
    --clean \
    --onefile \
    --name btck-wallet \
    --paths "$PROJECT_DIR/src" \
    --distpath "$BUILD_ROOT/pyinstaller/dist" \
    --workpath "$BUILD_ROOT/pyinstaller/work" \
    --specpath "$BUILD_ROOT/pyinstaller" \
    tools/btck_cli.py
  install -m 0755 "$BUILD_ROOT/pyinstaller/dist/btck-wallet" \
    "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet"
  WALLET_COMMAND="$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet"
else
  PACKAGE_NAME="BTC-K-Wallet-${VERSION}-portable-python3"
  ZIPAPP_ROOT="$BUILD_ROOT/zipapp"
  mkdir -p "$BUILD_ROOT/package/$PACKAGE_NAME" "$ZIPAPP_ROOT/privacy" "$ZIPAPP_ROOT/tokenomics"
  install -m 0644 "$PROJECT_DIR/tools/btck_cli.py" "$ZIPAPP_ROOT/__main__.py"
  install -m 0644 "$PROJECT_DIR/src/privacy/__init__.py" "$ZIPAPP_ROOT/privacy/__init__.py"
  install -m 0644 "$PROJECT_DIR/src/privacy/wallet_engine.py" "$ZIPAPP_ROOT/privacy/wallet_engine.py"
  install -m 0644 "$PROJECT_DIR/src/privacy/wallet_crypto.py" "$ZIPAPP_ROOT/privacy/wallet_crypto.py"
  install -m 0644 "$PROJECT_DIR/src/privacy/stealth_address.py" "$ZIPAPP_ROOT/privacy/stealth_address.py"
  install -m 0644 "$PROJECT_DIR/src/privacy/bip39.py" "$ZIPAPP_ROOT/privacy/bip39.py"
  install -m 0644 "$PROJECT_DIR/src/tokenomics/__init__.py" "$ZIPAPP_ROOT/tokenomics/__init__.py"
  install -m 0644 "$PROJECT_DIR/src/tokenomics/emission_schedule.py" "$ZIPAPP_ROOT/tokenomics/emission_schedule.py"
  install -m 0644 "$PROJECT_DIR/src/tokenomics/canonical_daily_quotas.py" "$ZIPAPP_ROOT/tokenomics/canonical_daily_quotas.py"
  find "$ZIPAPP_ROOT" -exec touch -t 198001010000 {} +
  python3 -m zipapp "$ZIPAPP_ROOT" \
    --python '/usr/bin/env python3' \
    --output "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz" \
    --compress
  chmod 0755 "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz"
  WALLET_COMMAND="$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz"
  printf 'Xcode Command Line Tools not found; built portable Python 3 package.\n' >&2
fi

BTCK_PROJECT_DIR="$PROJECT_DIR" BTCK_IDENTITY_OUT="$BUILD_ROOT/package/$PACKAGE_NAME/network_identity.json" \
BTCK_IDENTITY_MANIFEST="$NETWORK_MANIFEST" BTCK_IDENTITY_VERSION="$VERSION" python3 - <<'PY'
import json, os, sys
from pathlib import Path
root = Path(os.environ["BTCK_PROJECT_DIR"])
sys.path.insert(0, str(root / "src"))
from config.network import load_network
network = load_network("mainnet", Path(os.environ["BTCK_IDENTITY_MANIFEST"]))
payload = {
    "version": os.environ["BTCK_IDENTITY_VERSION"],
    "name": network.name,
    "chain_id": network.chain_id,
    "genesis_hash": network.genesis.expected_hash,
    "consensus_fingerprint": network.consensus_fingerprint,
    "release_approval_id": network.release_approval_id,
}
Path(os.environ["BTCK_IDENTITY_OUT"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

install -m 0644 "$PROJECT_DIR/packaging/WALLET_README.txt" \
  "$BUILD_ROOT/package/$PACKAGE_NAME/README.txt"
sed "s/__BTCK_WALLET_VERSION__/$VERSION/g" \
  "$PROJECT_DIR/packaging/btck_wallet_launcher.command" \
  > "$BUILD_ROOT/package/$PACKAGE_NAME/Open BTC-K Wallet.command"
chmod 0755 "$BUILD_ROOT/package/$PACKAGE_NAME/Open BTC-K Wallet.command"

"$WALLET_COMMAND" --version
tmp_wallet="$BUILD_ROOT/package/smoke-wallet.json"
tmp_password="$BUILD_ROOT/package/smoke-password.txt"
install -m 0600 /dev/null "$tmp_password"
printf '%s\n' 'package-smoke-password-only' > "$tmp_password"
"$WALLET_COMMAND" --password-file "$tmp_password" new --name package-smoke \
  --skip-immediate-recovery-check -o "$tmp_wallet" >/dev/null
"$WALLET_COMMAND" --password-file "$tmp_password" info "$tmp_wallet" >/dev/null
test "$(stat -f '%Lp' "$tmp_wallet")" = "600"
rm -f "$tmp_wallet" "$tmp_password"

if [ "${BTCK_WALLET_REQUIRE_SIGNING:-0}" = "1" ]; then
  if [ -z "${BTCK_APPLE_SIGN_IDENTITY:-}" ] || [ ! -x "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet" ]; then
    printf 'Signed build requires BTCK_APPLE_SIGN_IDENTITY and a native wallet binary.\n' >&2
    exit 1
  fi
  codesign --force --options runtime --timestamp --sign "$BTCK_APPLE_SIGN_IDENTITY" \
    "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet"
  codesign --verify --strict --verbose=2 "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet"
fi

archive="$RELEASE_DIR/$PACKAGE_NAME.tar.gz"
python3 "$PROJECT_DIR/packaging/reproducible_tar.py" "$BUILD_ROOT/package/$PACKAGE_NAME" "$archive"
(
  cd "$RELEASE_DIR"
  shasum -a 256 "$(basename "$archive")" > "$(basename "$archive").sha256"
)
BTCK_PROJECT_DIR="$PROJECT_DIR" BTCK_ARCHIVE="$archive" BTCK_IDENTITY="$BUILD_ROOT/package/$PACKAGE_NAME/network_identity.json" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
archive = Path(os.environ["BTCK_ARCHIVE"])
identity = json.loads(Path(os.environ["BTCK_IDENTITY"]).read_text())
evidence = {
    "format": "btck-wallet-build-evidence-v1",
    "artifact": archive.name,
    "artifact_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "network": identity["name"],
    "chain_id": identity["chain_id"],
    "genesis_hash": identity["genesis_hash"],
    "consensus_fingerprint": identity["consensus_fingerprint"],
    "version": identity["version"],
}
archive.with_suffix(archive.suffix + ".evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
PY
printf '%s\n' "$archive"
printf '%s\n' "$archive.sha256"

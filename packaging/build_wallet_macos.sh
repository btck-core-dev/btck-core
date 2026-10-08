#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${BTCK_WALLET_BUILD_ROOT:-$PROJECT_DIR/build/wallet-macos}"
RELEASE_DIR="${BTCK_WALLET_RELEASE_DIR:-$PROJECT_DIR/dist/wallet}"
VENV_DIR="$BUILD_ROOT/venv"
ARCH="$(uname -m)"
VERSION="3.2.0-rc2"

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
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet 'pyinstaller==6.16.0'
  cd "$PROJECT_DIR"
  "$VENV_DIR/bin/pyinstaller" \
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
  python3 -m zipapp "$ZIPAPP_ROOT" \
    --python '/usr/bin/env python3' \
    --output "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz" \
    --compress
  chmod 0755 "$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz"
  WALLET_COMMAND="$BUILD_ROOT/package/$PACKAGE_NAME/btck-wallet.pyz"
  printf 'Xcode Command Line Tools not found; built portable Python 3 package.\n' >&2
fi

install -m 0644 "$PROJECT_DIR/packaging/WALLET_README_zh-CN.txt" \
  "$BUILD_ROOT/package/$PACKAGE_NAME/使用说明.txt"
install -m 0755 "$PROJECT_DIR/packaging/btck_wallet_launcher.command" \
  "$BUILD_ROOT/package/$PACKAGE_NAME/打开BTC-K钱包.command"

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

archive="$RELEASE_DIR/$PACKAGE_NAME.tar.gz"
tar -C "$BUILD_ROOT/package" -czf "$archive" "$PACKAGE_NAME"
shasum -a 256 "$archive" > "$archive.sha256"
printf '%s\n' "$archive"
printf '%s\n' "$archive.sha256"

#!/bin/bash
set -uo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
display_unit="btck"
display_label="BTC-K"
if [ -d "$PACKAGE_DIR/BTC-K Wallet.app" ]; then
  open "$PACKAGE_DIR/BTC-K Wallet.app"
  exit $?
fi
if [ -x "$PACKAGE_DIR/btck-wallet" ]; then
  WALLET_BIN="$PACKAGE_DIR/btck-wallet"
elif [ -x "$PACKAGE_DIR/btck-wallet.pyz" ]; then
  WALLET_BIN="$PACKAGE_DIR/btck-wallet.pyz"
else
  echo "Cannot find btck-wallet or btck-wallet.pyz."
  read -r -p "Press Return to exit..."
  exit 1
fi

while true; do
  clear
  echo "=============================================="
  echo " BTC-K Wallet __BTCK_WALLET_VERSION__"
  echo "=============================================="
  echo "1. Create the three mainnet wallets"
  echo "2. View an encrypted wallet address"
  echo "3. View personal wallet balance (display: $display_label)"
  echo "4. Verify a recovery phrase"
  echo "5. Show command-line help"
  echo "6. Toggle balance display BTC-K / SatK"
  echo "0. Exit"
  echo
  read -r -p "Select an option: " choice
  case "$choice" in
    1)
      default_dir="$HOME/Documents/BTC-K-Mainnet-Wallets"
      read -r -p "Wallet directory [$default_dir]: " target_dir
      target_dir="${target_dir:-$default_dir}"
      if ! "$WALLET_BIN" init-mainnet-wallets --resume --words 24 --output-dir "$target_dir"; then
        echo
        echo "Wallet setup did not finish. Existing files were preserved; you may resume from this menu."
      fi
      ;;
    2)
      read -r -p "Full path to the wallet JSON file: " wallet_path
      "$WALLET_BIN" info "$wallet_path" || echo "Unable to read the wallet. Check the path and password."
      ;;
    3)
      wallet_path="$HOME/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json"
      if [ ! -f "$wallet_path" ]; then
        echo "Personal wallet not found: $wallet_path"
      else
        echo "Syncing through the public HTTPS endpoint. Keys always remain on this Mac..."
        "$WALLET_BIN" balance "$wallet_path" --unit "$display_unit" || \
          echo "Balance lookup failed. The wallet file was not modified."
      fi
      ;;
    4)
      read -r -p "Full path to the wallet JSON file: " wallet_path
      "$WALLET_BIN" verify-recovery "$wallet_path" || echo "Verification failed. The wallet file remains unchanged."
      ;;
    5)
      "$WALLET_BIN" --help
      ;;
    6)
      if [ "$display_unit" = "btck" ]; then
        display_unit="satk"
        display_label="SatK"
      else
        display_unit="btck"
        display_label="BTC-K"
      fi
      echo "Balance display changed to $display_label."
      ;;
    0)
      exit 0
      ;;
    *)
      echo "Invalid selection."
      ;;
  esac
  echo
  read -r -p "Press Return to return to the main menu..."
done

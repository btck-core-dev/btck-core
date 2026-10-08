#!/bin/bash
set -uo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$PACKAGE_DIR/btck-wallet" ]; then
  WALLET_BIN="$PACKAGE_DIR/btck-wallet"
elif [ -x "$PACKAGE_DIR/btck-wallet.pyz" ]; then
  WALLET_BIN="$PACKAGE_DIR/btck-wallet.pyz"
else
  echo "找不到 btck-wallet 或 btck-wallet.pyz。"
  read -r -p "按回车键退出..."
  exit 1
fi

while true; do
  clear
  echo "=============================================="
  echo " BTC-K 钱包 3.2.0-rc2"
  echo "=============================================="
  echo "1. 首次生成三个主网钱包（个人/矿池/生态基金）"
  echo "2. 查看加密钱包地址"
  echo "3. 验证助记词能恢复钱包"
  echo "4. 显示命令行帮助"
  echo "0. 退出"
  echo
  read -r -p "请选择：" choice
  case "$choice" in
    1)
      default_dir="$HOME/Documents/BTC-K-Mainnet-Wallets"
      read -r -p "保存目录 [$default_dir]：" target_dir
      target_dir="${target_dir:-$default_dir}"
      if ! "$WALLET_BIN" init-mainnet-wallets --resume --words 24 --output-dir "$target_dir"; then
        echo
        echo "钱包操作未完成。现有文件已保留，可返回菜单后继续。"
      fi
      ;;
    2)
      read -r -p "请输入钱包 JSON 文件完整路径：" wallet_path
      "$WALLET_BIN" info "$wallet_path" || echo "读取失败，请检查路径或口令。"
      ;;
    3)
      read -r -p "请输入钱包 JSON 文件完整路径：" wallet_path
      "$WALLET_BIN" verify-recovery "$wallet_path" || echo "验证未通过，钱包文件保持不变。"
      ;;
    4)
      "$WALLET_BIN" --help
      ;;
    0)
      exit 0
      ;;
    *)
      echo "无效选择。"
      ;;
  esac
  echo
  read -r -p "按回车键返回主菜单..."
done

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
  echo " BTC-K 钱包 3.2.1-guardian1"
  echo "=============================================="
  echo "1. 首次生成三个主网钱包（个人/矿池/生态基金）"
  echo "2. 查看加密钱包地址"
  echo "3. 查看当前护网主网个人钱包余额"
  echo "4. 验证助记词能恢复钱包"
  echo "5. 显示命令行帮助"
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
      wallet_path="$HOME/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json"
      if [ ! -f "$wallet_path" ]; then
        echo "找不到个人钱包：$wallet_path"
      else
        echo "正在通过公开 HTTPS 接口同步链上数据；密钥始终留在本机..."
        "$WALLET_BIN" balance "$wallet_path" || \
          echo "余额读取失败；钱包文件没有被修改。"
      fi
      ;;
    4)
      read -r -p "请输入钱包 JSON 文件完整路径：" wallet_path
      "$WALLET_BIN" verify-recovery "$wallet_path" || echo "验证未通过，钱包文件保持不变。"
      ;;
    5)
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

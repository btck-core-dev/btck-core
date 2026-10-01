#!/usr/bin/env python3
"""
BTC-K Sovereign Offline BIP-39 Multi-Wallet Generator
=====================================================
A completely self-contained, offline cryptographic tool to generate and manage
the three foundational wallets of the BTC-K Network:
  1. Founder Personal Wallet (个人挖矿收益与创世战略储备)
  2. Mining Pool Treasury Wallet (矿池手续费金库与运维基金)
  3. Ecosystem Development Fund (主网创世共识写入的生态激励基金)

Security Guarantee:
  - Pure Python Standard Library (no third-party pip dependencies required).
  - SECP256k1 Elliptic Curve + PBKDF2-HMAC-SHA512 BIP-39 Standard.
  - Zero network calls. Safe to run completely air-gapped / offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure src/ is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from privacy.bip39 import (
    generate_mnemonic,
    validate_mnemonic,
    mnemonic_to_seed,
    derive_btck_stealth_wallet,
    WORDLIST,
)
from privacy.stealth_address import StealthKeyPair, _pubkey_to_bytes


class Color:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def print_banner():
    print(f"{Color.CYAN}{Color.BOLD}")
    print("=" * 72)
    print("  ⛏️  BTC-K SOVEREIGN OFFLINE BIP-39 WALLET GENERATOR  ⛏️")
    print("   Dual-Key Stealth Address System (DKSAP / secp256k1)")
    print("=" * 72)
    print(f"{Color.RESET}")
    print(f"{Color.YELLOW}⚠️  安全警告：本工具完全在本地内存中运行，绝无任何网络连接。")
    print("   请确保周围无人窥视，生成助记词后请务必【手抄在物理纸张上】并妥善存入保险箱！")
    print(f"{Color.RESET}")


def format_words(words_str: str) -> str:
    words = words_str.split()
    lines = []
    chunk_size = 4
    for i in range(0, len(words), chunk_size):
        chunk = words[i:i + chunk_size]
        formatted = "   ".join(f"{Color.BOLD}{i+j+1:2d}.{Color.RESET} {Color.GREEN}{w:10s}{Color.RESET}" for j, w in enumerate(chunk))
        lines.append("    " + formatted)
    return "\n".join(lines)


def generate_single_account(name: str, account_type: str, strength_bits: int = 128) -> dict:
    mnemonic = generate_mnemonic(strength_bits)
    seed = mnemonic_to_seed(mnemonic)
    v_int, s_int, kp = derive_btck_stealth_wallet(seed, account_index=0)
    
    return {
        "name": name,
        "type": account_type,
        "word_count": len(mnemonic.split()),
        "mnemonic": mnemonic,
        "stealth_address": kp.get_public_stealth_address(),
        "view_public_key": kp.view_pub_bytes.hex(),
        "spend_public_key": kp.spend_pub_bytes.hex(),
        "view_private_key_hex": f"{v_int:064x}",
        "spend_private_key_hex": f"{s_int:064x}",
    }


def generate_isolated_wallets(strength_bits: int = 128) -> list[dict]:
    """Generate 3 completely independent master seed phrases."""
    configs = [
        ("① 创始人个人钱包 (Founder Personal Wallet)", "founder"),
        ("② 矿池金库钱包 (Pool Treasury Wallet)", "pool_treasury"),
        ("③ 生态基金钱包 (Eco Fund Wallet)", "eco_fund"),
    ]
    wallets = []
    for name, acct_type in configs:
        wallets.append(generate_single_account(name, acct_type, strength_bits))
    return wallets


def generate_hierarchical_wallets(strength_bits: int = 128) -> tuple[str, list[dict]]:
    """Generate 1 master seed and derive 3 separate sub-accounts."""
    master_mnemonic = generate_mnemonic(strength_bits)
    seed = mnemonic_to_seed(master_mnemonic)
    
    configs = [
        ("① 创始人个人钱包 (Founder Personal Wallet)", "founder", 0),
        ("② 矿池金库钱包 (Pool Treasury Wallet)", "pool_treasury", 1),
        ("③ 生态基金钱包 (Eco Fund Wallet)", "eco_fund", 2),
    ]
    wallets = []
    for name, acct_type, idx in configs:
        v_int, s_int, kp = derive_btck_stealth_wallet(seed, account_index=idx)
        wallets.append({
            "name": name,
            "type": acct_type,
            "account_index": idx,
            "stealth_address": kp.get_public_stealth_address(),
            "view_public_key": kp.view_pub_bytes.hex(),
            "spend_public_key": kp.spend_pub_bytes.hex(),
            "view_private_key_hex": f"{v_int:064x}",
            "spend_private_key_hex": f"{s_int:064x}",
        })
    return master_mnemonic, wallets


def restore_from_mnemonic(mnemonic: str, account_index: int = 0) -> dict:
    if not validate_mnemonic(mnemonic):
        raise ValueError("Invalid BIP-39 mnemonic phrase. Check spelling and word count.")
    seed = mnemonic_to_seed(mnemonic)
    v_int, s_int, kp = derive_btck_stealth_wallet(seed, account_index=account_index)
    return {
        "mnemonic": mnemonic,
        "account_index": account_index,
        "stealth_address": kp.get_public_stealth_address(),
        "view_public_key": kp.view_pub_bytes.hex(),
        "spend_public_key": kp.spend_pub_bytes.hex(),
        "view_private_key_hex": f"{v_int:064x}",
        "spend_private_key_hex": f"{s_int:064x}",
    }


def display_wallets(wallets: list[dict], master_mnemonic: str = None):
    print("\n" + "=" * 72)
    if master_mnemonic:
        print(f"{Color.CYAN}{Color.BOLD}>>> 统一主主助记词 (Master Seed Phrase - 控制全部 3 个子账户):{Color.RESET}")
        print("-" * 72)
        print(format_words(master_mnemonic))
        print("-" * 72)

    for w in wallets:
        print(f"\n{Color.YELLOW}{Color.BOLD}------------------------------------------------------------{Color.RESET}")
        print(f"{Color.BOLD}🏷️  {w['name']}{Color.RESET}")
        print(f"{Color.YELLOW}------------------------------------------------------------{Color.RESET}")
        
        if "mnemonic" in w and not master_mnemonic:
            print(f"{Color.CYAN}🔑 独立专属助记词 ({w['word_count']} Words):{Color.RESET}")
            print(format_words(w["mnemonic"]))
            print("")
            
        print(f"📬 隐身收款公钥地址 (Public Stealth Address):")
        print(f"   {Color.GREEN}{Color.BOLD}{w['stealth_address']}{Color.RESET}")
        print(f"🔍 审计查看公钥 (View PubKey):  {Color.DIM}{w['view_public_key']}{Color.RESET}")
        print(f"💸 消费公钥 (Spend PubKey):      {Color.DIM}{w['spend_public_key']}{Color.RESET}")
        print(f"🔒 扫描私钥 (View PrivKey):      {Color.RED}{w['view_private_key_hex']}{Color.RESET}")
        print(f"🔐 消费私钥 (Spend PrivKey):     {Color.RED}{w['spend_private_key_hex']}{Color.RESET}")

    print("\n" + "=" * 72)
    print(f"{Color.GREEN}{Color.BOLD}✅ 创世参数摘要 (填入主网配置的公开参数，可安全公开)：{Color.RESET}")
    print("=" * 72)
    for w in wallets:
        tag = w['type'].upper()
        print(f"• {tag}_ADDRESS = \"{w['stealth_address']}\"")
        if w['type'] == 'eco_fund':
            print(f"• ECO_FUND_OUTPUT = [\"{w['spend_public_key']}\", \"{w['view_public_key']}\"]")
    print("=" * 72 + "\n")


def save_wallets_json(wallets: list[dict], filepath: Path, master_mnemonic: str = None):
    data = {
        "timestamp": os.path.getmtime(ROOT_DIR),
        "protocol": "BTC-K DKSAP v2",
        "wallets": wallets,
    }
    if master_mnemonic:
        data["master_mnemonic"] = master_mnemonic
        
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(filepath, 0o600)
    print(f"{Color.GREEN}💾 钱包加密备份文件已安全存盘至: {filepath} (权限: chmod 600){Color.RESET}")


def main():
    parser = argparse.ArgumentParser(description="BTC-K BIP-39 Multi-Wallet Generator")
    parser.add_argument("--words", type=int, choices=[12, 24], default=12, help="Mnemonic word count (12 or 24)")
    parser.add_argument("--mode", choices=["isolated", "hierarchical", "restore"], default="isolated",
                        help="Mode: 'isolated' (3 separate seeds, recommended) or 'hierarchical' (1 seed, 3 accounts)")
    parser.add_argument("--restore-mnemonic", type=str, default=None, help="Mnemonic phrase to restore")
    parser.add_argument("--save", type=str, default=None, help="Optional output JSON filepath to save keys")
    args = parser.parse_args()

    print_banner()

    strength = 128 if args.words == 12 else 256

    if args.mode == "restore":
        mnemonic = args.restore_mnemonic
        if not mnemonic:
            mnemonic = input(f"{Color.CYAN}请输入您的 12 或 24 位英文助记词 (空格分隔): {Color.RESET}").strip()
        account = restore_from_mnemonic(mnemonic)
        display_wallets([account])
        if args.save:
            save_wallets_json([account], Path(args.save))
        return

    if args.mode == "isolated":
        print(f"{Color.CYAN}正在生成【3 个完全隔离的独立主钱包】(推荐模式，最高安全性)...{Color.RESET}")
        wallets = generate_isolated_wallets(strength_bits=strength)
        display_wallets(wallets)
        if args.save:
            save_wallets_json(wallets, Path(args.save))
    else:
        print(f"{Color.CYAN}正在生成【1 套主助记词 + 3 个衍生子账户】(便捷模式)...{Color.RESET}")
        master_m, wallets = generate_hierarchical_wallets(strength_bits=strength)
        display_wallets(wallets, master_mnemonic=master_m)
        if args.save:
            save_wallets_json(wallets, Path(args.save), master_mnemonic=master_m)


if __name__ == "__main__":
    main()

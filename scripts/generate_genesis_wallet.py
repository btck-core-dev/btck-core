#!/usr/bin/env python3
"""
BTC-K Genesis Mining Wallet Generator
-------------------------------------
Generates the official Stealth Mining Wallet for Genesis Launch.
Saves private keys securely to secrets/genesis_wallet.json (chmod 600).
"""

import os
import sys
import json
import stat

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BTCK_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, os.path.join(BTCK_ROOT, "src"))

from privacy.stealth_address import StealthKeyPair

def generate_wallet():
    secrets_dir = os.path.join(BTCK_ROOT, "secrets")
    os.makedirs(secrets_dir, exist_ok=True)
    wallet_file = os.path.join(secrets_dir, "genesis_wallet.json")

    if os.path.exists(wallet_file):
        print(f"⚠️ Existing Genesis wallet found at: {wallet_file}")
        with open(wallet_file, "r") as f:
            data = json.load(f)
        print("==================================================")
        print("🔑 EXISTING BTC-K GENESIS MINING WALLET")
        print("==================================================")
        print(f"Address : {data['address']}")
        print(f"Worker  : {data['address']}.ks0_ultra")
        print("==================================================")
        return data

    keypair = StealthKeyPair()
    address = keypair.get_public_stealth_address()
    
    wallet_data = {
        "address": address,
        "spend_priv_scalar": str(keypair.spend_priv),
        "view_priv_scalar": str(keypair.view_priv),
        "spend_pub_hex": keypair.spend_pub_bytes.hex(),
        "view_pub_hex": keypair.view_pub_bytes.hex(),
        "recommended_worker": f"{address}.ks0_ultra",
        "stratum_pool": "stratum+tcp://192.168.50.189:3333"
    }

    with open(wallet_file, "w") as f:
        json.dump(wallet_data, f, indent=2)

    os.chmod(wallet_file, stat.S_IRUSR | stat.S_IWUSR)

    print("==================================================")
    print("🎉 NEW BTC-K GENESIS MINING WALLET CREATED")
    print("==================================================")
    print(f"📁 Key File : {wallet_file} (chmod 600)")
    print(f"📫 Address  : {address}")
    print(f"⛏️ Worker   : {wallet_data['recommended_worker']}")
    print("==================================================")
    print("⚠️ Please backup genesis_wallet.json to an offline drive!")
    
    return wallet_data

if __name__ == "__main__":
    generate_wallet()

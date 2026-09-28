"""
BTC-K Lightweight Stealth Wallet SDK
------------------------------------
Provides dual-key stealth address generation, balance scanning,
and Sats micropayment signing for autonomous AI agents.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

import os
import sys

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
src_dir = os.path.join(base_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet
from tokenomics.emission_schedule import SATS_PER_COIN

class BTCKWallet:
    def __init__(self, spend_priv_scalar: Optional[int] = None, view_priv_scalar: Optional[int] = None, rpc_url: str = "http://127.0.0.1:16110/rpc", rpc_token: Optional[str] = None):
        self.keypair = StealthKeyPair(spend_priv_scalar, view_priv_scalar)
        self.rpc_url = rpc_url
        self.rpc_token = rpc_token or os.getenv("BTCK_RPC_TOKEN")
        self.address = self.keypair.get_public_stealth_address()

    @classmethod
    def generate(cls, rpc_url: str = "http://127.0.0.1:16110/rpc") -> "BTCKWallet":
        """Generate a brand new random Stealth Wallet"""
        return cls(rpc_url=rpc_url)

    def export_keys(self) -> Dict[str, str]:
        """Export private and public keys for backup"""
        return {
            "address": self.address,
            "spend_priv_scalar": str(self.keypair.spend_priv),
            "view_priv_scalar": str(self.keypair.view_priv),
            "spend_pub_hex": self.keypair.spend_pub_bytes.hex(),
            "view_pub_hex": self.keypair.view_pub_bytes.hex()
        }

    def _rpc_call(self, method: str, params: dict) -> dict:
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode('utf-8')
        headers = {"Content-Type": "application/json"}
        if self.rpc_token:
            headers["Authorization"] = f"Bearer {self.rpc_token}"
        req = urllib.request.Request(self.rpc_url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if "error" in data:
                    raise RuntimeError(f"RPC Error: {data['error']}")
                return data.get("result", {})
        except urllib.error.URLError as e:
            raise ConnectionError(f"Failed to connect to BTC-K node at {self.rpc_url}: {e}")

    def get_balance(self) -> Dict[str, Any]:
        """Fetch public outputs and scan locally; no private key leaves this process."""
        snapshot = self._rpc_call("getUTXOSet", {})
        wallet = StealthWallet(self.keypair, "sdk")
        wallet.scan_chain_outputs(snapshot.get("outputs", []))
        return {
            "address": self.address,
            "total_sats": wallet.get_balance_satk(),
            "total_btck": wallet.get_balance_btck(),
            "utxo_count": len(wallet.utxos),
            "utxos": [vars(utxo) for utxo in wallet.utxos],
        }

    def pay_invoice(self, invoice: dict) -> dict:
        """
        Legacy self-signed receipts are not payments and are intentionally disabled.
        """
        raise RuntimeError(
            "HTTP 402 settlement is disabled: use a real UTXO transaction and node confirmation"
        )

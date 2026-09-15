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
import time

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
src_dir = os.path.join(base_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from privacy.stealth_address import StealthKeyPair, sha256
from privacy.wallet_engine import schnorr_sign
from tokenomics.emission_schedule import SATS_PER_COIN

class BTCKWallet:
    def __init__(self, spend_priv_scalar: Optional[int] = None, view_priv_scalar: Optional[int] = None, rpc_url: str = "http://127.0.0.1:16110/rpc"):
        self.keypair = StealthKeyPair(spend_priv_scalar, view_priv_scalar)
        self.rpc_url = rpc_url
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
        req = urllib.request.Request(self.rpc_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if "error" in data:
                    raise RuntimeError(f"RPC Error: {data['error']}")
                return data.get("result", {})
        except urllib.error.URLError as e:
            raise ConnectionError(f"Failed to connect to BTC-K node at {self.rpc_url}: {e}")

    def get_balance(self) -> Dict[str, Any]:
        """Scan node DAG for stealth UTXOs belonging to this wallet"""
        res = self._rpc_call("scanStealthBalance", {
            "view_priv_scalar": str(self.keypair.view_priv),
            "spend_priv_scalar": str(self.keypair.spend_priv)
        })
        return {
            "address": self.address,
            "total_sats": res.get("total_sats", 0),
            "total_btck": res.get("total_btck", 0.0),
            "utxo_count": res.get("utxo_count", 0),
            "utxos": res.get("utxos", [])
        }

    def pay_invoice(self, invoice: dict) -> dict:
        """
        Pay an HTTP 402 AI-RPC invoice using Sats with Schnorr signature verification
        """
        raw_amt = invoice.get("amount_satk", invoice.get("amount_sats", 0))
        amount_satk = int(raw_amt)
        recipient_addr = invoice.get("payment_address")
        invoice_id = invoice.get("invoice_id")
        nonce = invoice.get("nonce", "")
        timestamp = int(time.time())
        
        # Derive one-time stealth output for recipient
        R_ephem, P_onetime = StealthKeyPair.generate_one_time_stealth_output(recipient_addr)
        
        # Digest matching gateway canonical digest
        digest = sha256(f"{invoice_id}_{amount_satk}_{recipient_addr}_{nonce}_{timestamp}".encode())
        sig = schnorr_sign(self.keypair.spend_priv, digest)
        
        tx_receipt = {
            "invoice_id": invoice_id,
            "amount_satk": amount_satk,
            "amount_sats": amount_satk,
            "payer_stealth_address": self.address,
            "payer_spend_pub_hex": self.keypair.spend_pub_bytes.hex(),
            "recipient_one_time_output": P_onetime.hex(),
            "ephemeral_pubkey": R_ephem.hex(),
            "timestamp": timestamp,
            "payment_signature": sig.hex(),
            "status": "PAID"
        }
        return tx_receipt

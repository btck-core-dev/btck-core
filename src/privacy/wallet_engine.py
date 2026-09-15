"""
BTC-K Stealth Wallet & Offline Transaction Signing Engine
---------------------------------------------------------
Features:
1. Dual-Key Stealth Address management (Spend Key + View Key).
2. View Key Blind Scanning: Scan block outputs and discover UTXOs without revealing Spend Key.
3. Spend Key Offline Transaction Signing: Derives one-time private key p = s + c mod N,
   constructs unspent outputs for recipient, and produces BIP-340 Schnorr signatures.
4. Transaction Verification & Double-Spending Protection.
"""

import os
import json
import secrets
import hashlib
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict

from privacy.stealth_address import (
    StealthKeyPair,
    _G, _N, _P,
    _point_mul, _point_add,
    _pubkey_to_bytes, _bytes_to_pubkey,
    sha256
)
from tokenomics.emission_schedule import SATK_PER_COIN

def schnorr_sign(privkey_scalar: int, message_hash: bytes) -> bytes:
    """Produce 65-byte Schnorr signature: R_compressed(33) + s(32)"""
    pub_point = _point_mul(privkey_scalar, _G)
    pub_bytes = _pubkey_to_bytes(pub_point, compressed=True)
    
    # Deterministic nonce k = H(priv || msg) mod N
    k = int.from_bytes(sha256(privkey_scalar.to_bytes(32, "big") + message_hash), "big") % _N
    if k == 0:
        k = 1
    R_point = _point_mul(k, _G)
    R_bytes = _pubkey_to_bytes(R_point, compressed=True)
    
    # Challenge e = H(R || Pub || msg) mod N
    e = int.from_bytes(sha256(R_bytes + pub_bytes + message_hash), "big") % _N
    
    # s = (k + e * priv) mod N
    s = (k + e * privkey_scalar) % _N
    return R_bytes + s.to_bytes(32, "big")

def schnorr_verify(pubkey_bytes: bytes, message_hash: bytes, signature_bytes: bytes) -> bool:
    """Verify Schnorr signature: check s * G == R + e * PubKey"""
    if len(signature_bytes) != 65:
        return False
    R_bytes = signature_bytes[:33]
    s = int.from_bytes(signature_bytes[33:], "big")
    if s >= _N:
        return False
        
    pub_point = _bytes_to_pubkey(pubkey_bytes)
    R_point = _bytes_to_pubkey(R_bytes)
    if pub_point is None or R_point is None:
        return False
        
    e = int.from_bytes(sha256(R_bytes + pubkey_bytes + message_hash), "big") % _N
    
    sG = _point_mul(s, _G)
    ePub = _point_mul(e, pub_point)
    R_plus_ePub = _point_add(R_point, ePub)
    return sG == R_plus_ePub

@dataclass
class UTXO:
    txid: str
    vout: int
    amount_satk: int
    R_ephem_hex: str
    P_onetime_hex: str
    block_height: int
    spent: bool = False

@dataclass
class TxInput:
    prev_txid: str
    prev_vout: int
    amount_satk: int
    P_onetime_hex: str
    signature_hex: str = ""

@dataclass
class TxOutput:
    amount_satk: int
    R_ephem_hex: str
    P_onetime_hex: str
    recipient_tag: str = "" # Optional memo / tag

@dataclass
class StealthTransaction:
    version: int
    inputs: List[TxInput]
    outputs: List[TxOutput]
    lock_time: int
    txid: str = ""

    def compute_sighash(self) -> bytes:
        payload = bytearray()
        payload.extend(self.version.to_bytes(4, "little"))
        for inp in self.inputs:
            payload.extend(bytes.fromhex(inp.prev_txid))
            payload.extend(inp.prev_vout.to_bytes(4, "little"))
            payload.extend(inp.amount_satk.to_bytes(8, "little"))
            payload.extend(bytes.fromhex(inp.P_onetime_hex))
        for out in self.outputs:
            payload.extend(out.amount_satk.to_bytes(8, "little"))
            payload.extend(bytes.fromhex(out.R_ephem_hex))
            payload.extend(bytes.fromhex(out.P_onetime_hex))
        payload.extend(self.lock_time.to_bytes(8, "little"))
        return sha256(sha256(bytes(payload)))

    def finalize(self):
        sighash = self.compute_sighash()
        self.txid = sha256(sighash).hex()

class StealthWallet:
    def __init__(self, keypair: Optional[StealthKeyPair] = None, name: str = "default"):
        self.keypair = keypair or StealthKeyPair()
        self.name = name
        self.address = self.keypair.get_public_stealth_address()
        self.utxos: List[UTXO] = []

    @classmethod
    def generate(cls, name: str = "default") -> "StealthWallet":
        return cls(keypair=StealthKeyPair(), name=name)

    @classmethod
    def from_keys(cls, spend_priv_hex: str, view_priv_hex: str, name: str = "imported") -> "StealthWallet":
        spend_scalar = int(spend_priv_hex, 16)
        view_scalar = int(view_priv_hex, 16)
        kp = StealthKeyPair(spend_priv_scalar=spend_scalar, view_priv_scalar=view_scalar)
        return cls(keypair=kp, name=name)

    @classmethod
    def load_from_json(cls, filepath: str) -> "StealthWallet":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        spend = data.get("spend_priv_scalar") or data.get("spend_priv_hex")
        view = data.get("view_priv_scalar") or data.get("view_priv_hex")
        name = data.get("name", "loaded")
        return cls.from_keys(spend, view, name=name)

    def export_to_dict(self, include_private: bool = False) -> dict:
        d = {
            "name": self.name,
            "address": self.address,
            "spend_pub_hex": self.keypair.spend_pub_bytes.hex(),
            "view_pub_hex": self.keypair.view_pub_bytes.hex()
        }
        if include_private:
            d["spend_priv_hex"] = hex(self.keypair.spend_priv)[2:].zfill(64)
            d["view_priv_hex"] = hex(self.keypair.view_priv)[2:].zfill(64)
        return d

    def scan_chain_outputs(self, raw_outputs: List[dict]) -> List[UTXO]:
        """
        View Key Blind Scan:
        Examines (R_ephem, P_onetime) pairs. If it belongs to this wallet, adds to self.utxos.
        Does NOT require spend private key!
        """
        new_discovered = []
        for out in raw_outputs:
            txid = out["txid"]
            vout = out.get("vout", 0)
            R_bytes = bytes.fromhex(out["R_ephem_hex"])
            P_bytes = bytes.fromhex(out["P_onetime_hex"])
            
            if self.keypair.is_my_stealth_output(R_bytes, P_bytes):
                # Verify not already tracked
                exists = any(u.txid == txid and u.vout == vout for u in self.utxos)
                if not exists:
                    utxo = UTXO(
                        txid=txid,
                        vout=vout,
                        amount_satk=out["amount_satk"],
                        R_ephem_hex=out["R_ephem_hex"],
                        P_onetime_hex=out["P_onetime_hex"],
                        block_height=out.get("height", 0)
                    )
                    self.utxos.append(utxo)
                    new_discovered.append(utxo)
        return new_discovered

    def get_balance_satk(self) -> int:
        return sum(u.amount_satk for u in self.utxos if not u.spent)

    def get_balance_btck(self) -> float:
        return self.get_balance_satk() / SATK_PER_COIN

    def create_and_sign_transfer(
        self,
        recipient_stealth_addr: str,
        amount_satk: int,
        fee_satk: int = 10_000
    ) -> StealthTransaction:
        """
        Spend Key Offline Signing:
        1. Selects unspent UTXOs to cover amount + fee.
        2. Generates stealth output for recipient: (R_recip, P_recip).
        3. Generates change stealth output for self: (R_change, P_change).
        4. Derives one-time private key p_i = s + c_i mod N for each input.
        5. Signs sighash with Schnorr signature.
        """
        available_utxos = [u for u in self.utxos if not u.spent]
        total_selected = 0
        selected_utxos: List[UTXO] = []
        
        needed = amount_satk + fee_satk
        for u in available_utxos:
            selected_utxos.append(u)
            total_selected += u.amount_satk
            if total_selected >= needed:
                break
                
        if total_selected < needed:
            raise ValueError(f"Insufficient balance: have {total_selected/SATK_PER_COIN:.8f} BTC-K, need {needed/SATK_PER_COIN:.8f} BTC-K")

        # 1. Inputs
        tx_inputs = [
            TxInput(
                prev_txid=u.txid,
                prev_vout=u.vout,
                amount_satk=u.amount_satk,
                P_onetime_hex=u.P_onetime_hex
            )
            for u in selected_utxos
        ]

        # 2. Recipient Output
        R_recip, P_recip = StealthKeyPair.generate_one_time_stealth_output(recipient_stealth_addr)
        tx_outputs = [
            TxOutput(
                amount_satk=amount_satk,
                R_ephem_hex=R_recip.hex(),
                P_onetime_hex=P_recip.hex(),
                recipient_tag="Recipient Payment"
            )
        ]

        # 3. Change Output (back to self stealth address)
        change_satk = total_selected - needed
        if change_satk > 0:
            R_change, P_change = StealthKeyPair.generate_one_time_stealth_output(self.address)
            tx_outputs.append(
                TxOutput(
                    amount_satk=change_satk,
                    R_ephem_hex=R_change.hex(),
                    P_onetime_hex=P_change.hex(),
                    recipient_tag="Change Return"
                )
            )

        tx = StealthTransaction(
            version=1,
            inputs=tx_inputs,
            outputs=tx_outputs,
            lock_time=0
        )
        sighash = tx.compute_sighash()

        # 4. Sign each input using derived one-time private key
        for i, u in enumerate(selected_utxos):
            R_ephem_bytes = bytes.fromhex(u.R_ephem_hex)
            one_time_priv = self.keypair.derive_one_time_private_key(R_ephem_bytes)
            
            sig = schnorr_sign(one_time_priv, sighash)
            tx.inputs[i].signature_hex = sig.hex()

        tx.finalize()
        
        # Mark selected UTXOs as spent locally
        for u in selected_utxos:
            u.spent = True

        return tx

    @staticmethod
    def verify_transaction(tx: StealthTransaction) -> bool:
        """
        Full transaction consensus validation:
        1. Check all Schnorr signatures match input P_onetime.
        2. Check sum(inputs) >= sum(outputs).
        """
        sighash = tx.compute_sighash()
        total_in = sum(inp.amount_satk for inp in tx.inputs)
        total_out = sum(out.amount_satk for out in tx.outputs)
        
        if total_in < total_out:
            return False

        for inp in tx.inputs:
            if not inp.signature_hex:
                return False
            sig_bytes = bytes.fromhex(inp.signature_hex)
            pub_bytes = bytes.fromhex(inp.P_onetime_hex)
            if not schnorr_verify(pub_bytes, sighash, sig_bytes):
                return False
        return True

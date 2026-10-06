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
from privacy.wallet_crypto import WALLET_FORMAT, decrypt_wallet_payload, encrypt_wallet_payload

def _tagged_hash(tag: str, payload: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode("ascii")).digest()
    return hashlib.sha256(tag_hash + tag_hash + payload).digest()


def _lift_x_even(x_coord: int):
    """BIP-340 lift_x: return the curve point with the even Y coordinate."""
    if not 0 <= x_coord < _P:
        return None
    y_squared = (pow(x_coord, 3, _P) + 7) % _P
    y_coord = pow(y_squared, (_P + 1) // 4, _P)
    if pow(y_coord, 2, _P) != y_squared:
        return None
    return (x_coord, y_coord if y_coord % 2 == 0 else _P - y_coord)


def bip340_pubkey(privkey_scalar: int) -> bytes:
    """Return the canonical 32-byte x-only BIP-340 public key."""
    if not isinstance(privkey_scalar, int) or not 1 <= privkey_scalar < _N:
        raise ValueError("BIP-340 private key must be in the secp256k1 scalar range")
    point = _point_mul(privkey_scalar, _G)
    return point[0].to_bytes(32, "big")


def schnorr_sign(privkey_scalar: int, message_hash: bytes, aux_rand: Optional[bytes] = None) -> bytes:
    """Create a standard 64-byte BIP-340 Schnorr signature."""
    if not isinstance(privkey_scalar, int) or not 1 <= privkey_scalar < _N:
        raise ValueError("BIP-340 private key must be in the secp256k1 scalar range")
    if not isinstance(message_hash, bytes):
        raise TypeError("BIP-340 message must be bytes")
    if aux_rand is None:
        aux_rand = secrets.token_bytes(32)
    if not isinstance(aux_rand, bytes) or len(aux_rand) != 32:
        raise ValueError("BIP-340 auxiliary randomness must be exactly 32 bytes")

    public_point = _point_mul(privkey_scalar, _G)
    normalized_secret = privkey_scalar if public_point[1] % 2 == 0 else _N - privkey_scalar
    public_x = public_point[0].to_bytes(32, "big")
    aux_hash = _tagged_hash("BIP0340/aux", aux_rand)
    masked_secret = bytes(
        left ^ right for left, right in zip(normalized_secret.to_bytes(32, "big"), aux_hash)
    )
    nonce_seed = _tagged_hash("BIP0340/nonce", masked_secret + public_x + message_hash)
    nonce = int.from_bytes(nonce_seed, "big") % _N
    if nonce == 0:
        raise RuntimeError("BIP-340 nonce generation produced zero")
    nonce_point = _point_mul(nonce, _G)
    if nonce_point[1] % 2:
        nonce = _N - nonce
        nonce_point = _point_mul(nonce, _G)
    r_bytes = nonce_point[0].to_bytes(32, "big")
    challenge = int.from_bytes(
        _tagged_hash("BIP0340/challenge", r_bytes + public_x + message_hash), "big"
    ) % _N
    signature = r_bytes + ((nonce + challenge * normalized_secret) % _N).to_bytes(32, "big")
    if not schnorr_verify(public_x, message_hash, signature):
        raise RuntimeError("Created BIP-340 signature did not verify")
    return signature


def schnorr_verify(pubkey_bytes: bytes, message_hash: bytes, signature_bytes: bytes) -> bool:
    """Verify BIP-340 using a 32-byte x-only or 33-byte compressed public key."""
    if not all(isinstance(value, bytes) for value in (pubkey_bytes, message_hash, signature_bytes)):
        return False
    if len(signature_bytes) != 64 or len(pubkey_bytes) not in (32, 33):
        return False
    if len(pubkey_bytes) == 33:
        if _bytes_to_pubkey(pubkey_bytes) is None:
            return False
        public_x = pubkey_bytes[1:]
    else:
        public_x = pubkey_bytes
    public_point = _lift_x_even(int.from_bytes(public_x, "big"))
    r_coord = int.from_bytes(signature_bytes[:32], "big")
    s_scalar = int.from_bytes(signature_bytes[32:], "big")
    if public_point is None or r_coord >= _P or s_scalar >= _N:
        return False
    challenge = int.from_bytes(
        _tagged_hash("BIP0340/challenge", signature_bytes[:32] + public_x + message_hash), "big"
    ) % _N
    verification_point = _point_add(
        _point_mul(s_scalar, _G),
        _point_mul((_N - challenge) % _N, public_point),
    )
    return (
        verification_point is not None
        and verification_point[1] % 2 == 0
        and verification_point[0] == r_coord
    )

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


def transaction_to_dict(tx: StealthTransaction) -> dict:
    """Canonical JSON-safe transaction representation used on disk and RPC."""
    return {
        "version": tx.version,
        "lock_time": tx.lock_time,
        "txid": tx.txid,
        "inputs": [asdict(tx_input) for tx_input in tx.inputs],
        "outputs": [asdict(tx_output) for tx_output in tx.outputs],
    }


def transaction_from_dict(payload: dict) -> StealthTransaction:
    """Parse an untrusted transaction and verify its content-derived ID."""
    if not isinstance(payload, dict):
        raise ValueError("Transaction payload must be an object")
    tx = StealthTransaction(
        version=int(payload["version"]),
        inputs=[TxInput(**item) for item in payload["inputs"]],
        outputs=[TxOutput(**item) for item in payload["outputs"]],
        lock_time=int(payload["lock_time"]),
        txid=str(payload.get("txid", "")),
    )
    if tx.version != 1 or tx.lock_time < 0:
        raise ValueError("Unsupported transaction version or lock_time")
    claimed_txid = tx.txid
    tx.finalize()
    if not claimed_txid or claimed_txid != tx.txid:
        raise ValueError("Transaction ID does not match canonical transaction contents")
    return tx

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
        if not (1 <= spend_scalar < _N and 1 <= view_scalar < _N):
            raise ValueError("Wallet private scalar is outside the secp256k1 range")
        kp = StealthKeyPair(spend_priv_scalar=spend_scalar, view_priv_scalar=view_scalar)
        return cls(keypair=kp, name=name)

    @classmethod
    def load_from_json(cls, filepath: str, password: Optional[str] = None) -> "StealthWallet":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("format") == WALLET_FORMAT:
            if password is None:
                raise ValueError("Encrypted wallet requires a password")
            private, public = decrypt_wallet_payload(data, password)
            data = {**public, **private}
        if data.get("spend_priv_hex") and data.get("view_priv_hex"):
            spend = str(data["spend_priv_hex"])
            view = str(data["view_priv_hex"])
        elif data.get("spend_priv_scalar") and data.get("view_priv_scalar"):
            spend = f"{int(data['spend_priv_scalar']):064x}"
            view = f"{int(data['view_priv_scalar']):064x}"
        else:
            raise ValueError("Wallet file does not contain a complete private key pair")
        name = data.get("name", "loaded")
        wallet = cls.from_keys(spend, view, name=name)
        if data.get("address"):
            expected_address = data["address"]
            if expected_address != wallet.address and expected_address != wallet.keypair.get_legacy_hex_address():
                raise ValueError("Wallet address does not match its private keys")
        return wallet

    def export_encrypted(self, password: str) -> dict:
        public = self.export_to_dict(include_private=False)
        private = {
            "spend_priv_hex": f"{self.keypair.spend_priv:064x}",
            "view_priv_hex": f"{self.keypair.view_priv:064x}",
        }
        return encrypt_wallet_payload(private, public, password)

    def export_to_dict(self, include_private: bool = False) -> dict:
        d = {
            "name": self.name,
            "address": self.address,
            "legacy_address": self.keypair.get_legacy_hex_address(),
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

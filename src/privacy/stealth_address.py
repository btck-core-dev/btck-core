"""
BTC-K Stealth Address Engine
----------------------------
Implements Dual-Key Stealth Address Scheme (Monero / BIP47 / Vitalik EIP-5564 standard).
Hides sender, receiver, and transaction balances with cryptographic one-time public keys.
Uses pure Python secp256k1 elliptic curve arithmetic for zero external dependency portability.
"""

import hashlib
import secrets
from typing import Tuple, Optional

# secp256k1 Curve Constants
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G = (_Gx, _Gy)

def _inv(k: int, p: int = _P) -> int:
    """Modular inverse using extended Euclidean algorithm"""
    return pow(k, p - 2, p)

def _point_add(p1: Optional[Tuple[int, int]], p2: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """Elliptic curve point addition on secp256k1"""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 != y2:
        return None
    if x1 == x2:
        m = (3 * x1 * x1) * _inv(2 * y1, _P) % _P
    else:
        m = (y2 - y1) * _inv(x2 - x1, _P) % _P
    x3 = (m * m - x1 - x2) % _P
    y3 = (m * (x1 - x3) - y1) % _P
    return (x3, y3)

def _point_mul(k: int, point: Tuple[int, int] = _G) -> Optional[Tuple[int, int]]:
    """Scalar multiplication k * Point"""
    k = k % _N
    if k == 0:
        return None
    res = None
    addend = point
    while k > 0:
        if k & 1:
            res = _point_add(res, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return res

def _pubkey_to_bytes(point: Tuple[int, int], compressed: bool = True) -> bytes:
    """Convert EC point to compressed (33-byte) or uncompressed (65-byte) representation"""
    x, y = point
    if compressed:
        prefix = b'\x02' if (y % 2 == 0) else b'\x03'
        return prefix + x.to_bytes(32, 'big')
    return b'\x04' + x.to_bytes(32, 'big') + y.to_bytes(32, 'big')

def _bytes_to_pubkey(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse compressed 33-byte public key to EC point. Returns None if invalid point."""
    try:
        if len(data) == 33:
            prefix = data[0]
            if prefix not in (2, 3):
                return None
            x = int.from_bytes(data[1:33], 'big')
            if x >= _P:
                return None
            # y^2 = x^3 + 7 mod P
            y_sq = (pow(x, 3, _P) + 7) % _P
            y = pow(y_sq, (_P + 1) // 4, _P)
            if pow(y, 2, _P) != y_sq:
                return None
            if (prefix == 2 and y % 2 != 0) or (prefix == 3 and y % 2 == 0):
                y = _P - y
            return (x, y)
        elif len(data) == 65 and data[0] == 4:
            x = int.from_bytes(data[1:33], 'big')
            y = int.from_bytes(data[33:65], 'big')
            if (pow(y, 2, _P) - (pow(x, 3, _P) + 7)) % _P != 0:
                return None
            return (x, y)
        return None
    except Exception:
        return None

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, 'big')
    chars = []
    while n > 0:
        n, rem = divmod(n, 58)
        chars.append(B58_ALPHABET[rem])
    res = ''.join(reversed(chars))
    pad = 0
    for byte in b:
        if byte == 0:
            pad += 1
        else:
            break
    return (B58_ALPHABET[0] * pad) + res

def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = B58_ALPHABET.find(ch)
        if idx == -1:
            raise ValueError(f"Invalid character in Base58 string: {ch}")
        n = n * 58 + idx
    b = []
    while n > 0:
        n, rem = divmod(n, 256)
        b.append(rem)
    res = bytes(reversed(b))
    pad = 0
    for ch in s:
        if ch == B58_ALPHABET[0]:
            pad += 1
        else:
            break
    return (b'\x00' * pad) + res

class StealthKeyPair:
    """
    Dual-Key Stealth Master Keypair:
    - View Key (Scan Key): (v, V = v * G) - Used to scan the blockchain for incoming stealth payments without ability to spend.
    - Spend Key: (s, S = s * G) - Used to sign and spend funds.
    - Supports watch-only scan mode (spend_priv is None, only view_priv and spend_pub_bytes).
    """
    def __init__(
        self,
        spend_priv_scalar: Optional[int] = None,
        view_priv_scalar: Optional[int] = None,
        spend_pub_bytes: Optional[bytes] = None
    ):
        if view_priv_scalar is None:
            view_priv_scalar = secrets.randbelow(_N - 1) + 1
            
        self.view_priv = view_priv_scalar
        self.view_pub_point = _point_mul(self.view_priv, _G)
        self.view_pub_bytes = _pubkey_to_bytes(self.view_pub_point, compressed=True)

        if spend_priv_scalar is not None:
            self.spend_priv = spend_priv_scalar
            self.spend_pub_point = _point_mul(self.spend_priv, _G)
            self.spend_pub_bytes = _pubkey_to_bytes(self.spend_pub_point, compressed=True)
        elif spend_pub_bytes is not None:
            self.spend_priv = None  # Watch-only / View-Key-only scanning mode!
            self.spend_pub_bytes = spend_pub_bytes
            self.spend_pub_point = _bytes_to_pubkey(spend_pub_bytes)
        else:
            self.spend_priv = secrets.randbelow(_N - 1) + 1
            self.spend_pub_point = _point_mul(self.spend_priv, _G)
            self.spend_pub_bytes = _pubkey_to_bytes(self.spend_pub_point, compressed=True)

    def get_public_stealth_address(self, compressed: bool = True) -> str:
        """
        Public stealth address string: (Spend_Pub_33B || View_Pub_33B)
        If compressed=True (default): Base58 encoded with 'btck1s' prefix (~101 chars, fits ASIC 128B limit).
        If compressed=False: Legacy hex encoded with 'btck1s' prefix (146 chars).
        """
        raw = self.spend_pub_bytes + self.view_pub_bytes
        checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        if compressed:
            return "btck1s" + b58encode(raw + checksum)
        return "btck1s" + (raw + checksum).hex()

    def get_legacy_hex_address(self) -> str:
        return self.get_public_stealth_address(compressed=False)

    @staticmethod
    def parse_stealth_address(address_str: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """Parse btck1s address (supporting both Base58 and legacy hex) to (Spend_Pub_Point, View_Pub_Point)"""
        if not address_str.startswith("btck1s"):
            raise ValueError("Invalid address prefix, expected btck1s")
        data_str = address_str[6:]
        raw = None
        if len(address_str) == 146:
            try:
                raw = bytes.fromhex(data_str)
            except ValueError:
                pass
        if raw is None:
            raw = b58decode(data_str)
        if len(raw) != 70:
            raise ValueError(f"Invalid stealth address payload length: {len(raw)} bytes (expected 70)")
        payload = raw[:-4]
        checksum = raw[-4:]
        expected_cs = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        if checksum != expected_cs:
            raise ValueError("Invalid checksum in stealth address")
        spend_bytes = payload[0:33]
        view_bytes = payload[33:66]
        return _bytes_to_pubkey(spend_bytes), _bytes_to_pubkey(view_bytes)

    @staticmethod
    def generate_one_time_stealth_output(recipient_public_stealth_addr: str) -> Tuple[bytes, bytes]:
        """
        Sender generates an ephemeral keypair (r, R = r * G) and derives a one-time public key P:
        1. Shared Secret Point: S_dh = r * View_Pub = r * (v * G)
        2. Shared Secret Scalar: c = Hash(S_dh_compressed)
        3. One-Time Output Public Key: P = Spend_Pub + c * G
        Returns: (R_ephemeral_pubkey_bytes, P_one_time_output_pubkey_bytes)
        """
        spend_pub_point, view_pub_point = StealthKeyPair.parse_stealth_address(recipient_public_stealth_addr)
        
        # 1. Generate Ephemeral secret scalar r in [1, N-1]
        r = secrets.randbelow(_N - 1) + 1
        R_ephem_point = _point_mul(r, _G)
        R_ephem_bytes = _pubkey_to_bytes(R_ephem_point, compressed=True)
        
        # 2. Compute Diffie-Hellman Shared Secret Point: r * View_Pub
        S_dh_point = _point_mul(r, view_pub_point)
        S_dh_bytes = _pubkey_to_bytes(S_dh_point, compressed=True)
        
        # 3. Scalar hash c = H(S_dh) mod N
        c = int.from_bytes(sha256(b"BTCK_STEALTH_DH:" + S_dh_bytes), 'big') % _N
        
        # 4. Derive One-Time Destination PubKey P = Spend_Pub + c * G
        cG_point = _point_mul(c, _G)
        P_point = _point_add(spend_pub_point, cG_point)
        P_bytes = _pubkey_to_bytes(P_point, compressed=True)
        
        return R_ephem_bytes, P_bytes

    def is_my_stealth_output(self, R_ephem_bytes: bytes, P_one_time_bytes: bytes) -> bool:
        """
        Recipient wallet scans every block using its private View Key (v):
        1. Shared Secret Point: S_dh = v * R_ephem = v * (r * G) = r * (v * G)
        2. Shared Secret Scalar: c = Hash(S_dh_compressed)
        3. Expected P = Spend_Pub + c * G
        If Expected P == P_one_time_bytes, this output belongs to this wallet!
        """
        R_ephem_point = _bytes_to_pubkey(R_ephem_bytes)
        if R_ephem_point is None:
            return False
            
        # S_dh = v * R
        S_dh_point = _point_mul(self.view_priv, R_ephem_point)
        if S_dh_point is None:
            return False
            
        S_dh_bytes = _pubkey_to_bytes(S_dh_point, compressed=True)
        
        # c = H(S_dh) mod N
        c = int.from_bytes(sha256(b"BTCK_STEALTH_DH:" + S_dh_bytes), 'big') % _N
        
        # Expected P = Spend_Pub + c * G
        cG_point = _point_mul(c, _G)
        expected_P_point = _point_add(self.spend_pub_point, cG_point)
        if expected_P_point is None:
            return False
            
        expected_P_bytes = _pubkey_to_bytes(expected_P_point, compressed=True)
        return expected_P_bytes == P_one_time_bytes

    def derive_one_time_private_key(self, R_ephem_bytes: bytes) -> int:
        """
        Derive one-time private key p to spend the UTXO:
        p = (s + c) mod N where P = p * G
        """
        R_ephem_point = _bytes_to_pubkey(R_ephem_bytes)
        S_dh_point = _point_mul(self.view_priv, R_ephem_point)
        S_dh_bytes = _pubkey_to_bytes(S_dh_point, compressed=True)
        c = int.from_bytes(sha256(b"BTCK_STEALTH_DH:" + S_dh_bytes), 'big') % _N
        
        one_time_priv = (self.spend_priv + c) % _N
        return one_time_priv

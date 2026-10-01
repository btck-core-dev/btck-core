"""
BIP-39 Mnemonic Generator and BIP-44 Key Derivation for BTC-K Stealth Wallets.
Standard 2048-word English list with PBKDF2-HMAC-SHA512 seed derivation.
"""

import base64
import hashlib
import hmac
import secrets
import zlib
from typing import Tuple, List

from privacy.stealth_address import StealthKeyPair, _N

# Compressed official BIP-39 English wordlist (2048 words)
_COMPRESSED_WORDS = "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"
WORDLIST = zlib.decompress(base64.b64decode(_COMPRESSED_WORDS)).decode('utf-8').splitlines()
_WORD_INDEX = {w: i for i, w in enumerate(WORDLIST)}


def generate_mnemonic(strength_bits: int = 128) -> str:
    """
    Generate standard BIP-39 mnemonic.
    strength_bits: 128 for 12 words, 256 for 24 words.
    """
    if strength_bits not in (128, 160, 192, 224, 256):
        raise ValueError("strength_bits must be 128, 160, 192, 224, or 256")
    entropy = secrets.token_bytes(strength_bits // 8)
    checksum_len = strength_bits // 32
    hash_bytes = hashlib.sha256(entropy).digest()
    
    # Binary representation of entropy + checksum
    ent_int = int.from_bytes(entropy, 'big')
    ent_bin = f'{ent_int:0{strength_bits}b}'
    chk_int = int.from_bytes(hash_bytes, 'big')
    chk_bin = f'{chk_int:0256b}'[:checksum_len]
    full_bits = ent_bin + chk_bin
    
    words = []
    for i in range(0, len(full_bits), 11):
        idx = int(full_bits[i:i+11], 2)
        words.append(WORDLIST[idx])
    return ' '.join(words)


def validate_mnemonic(mnemonic: str) -> bool:
    """Verify mnemonic words and checksum according to BIP-39 standard."""
    words = mnemonic.strip().lower().split()
    if len(words) not in (12, 15, 18, 21, 24):
        return False
    if any(w not in _WORD_INDEX for w in words):
        return False
        
    full_bits = ''.join(f'{_WORD_INDEX[w]:011b}' for w in words)
    checksum_len = len(words) // 3
    strength_bits = len(full_bits) - checksum_len
    
    ent_bin = full_bits[:strength_bits]
    chk_bin = full_bits[strength_bits:]
    
    ent_bytes = int(ent_bin, 2).to_bytes(strength_bits // 8, 'big')
    hash_bytes = hashlib.sha256(ent_bytes).digest()
    expected_chk = f'{int.from_bytes(hash_bytes, "big"):0256b}'[:checksum_len]
    return chk_bin == expected_chk


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """Convert mnemonic phrase to 512-bit (64-byte) binary seed via PBKDF2-HMAC-SHA512."""
    mnemonic_bytes = mnemonic.strip().encode('utf-8')
    salt = ('mnemonic' + passphrase).encode('utf-8')
    return hashlib.pbkdf2_hmac('sha512', mnemonic_bytes, salt, iterations=2048)


def derive_btck_stealth_wallet(seed: bytes, account_index: int = 0) -> Tuple[int, int, StealthKeyPair]:
    """
    Derive deterministic DKSAP keys (View Key & Spend Key) from master seed.
    account_index:
        0 = Founder Personal Wallet
        1 = Pool Treasury Wallet
        2 = Eco Fund Wallet
    """
    # Domain-separated HMAC key derivation
    v_bytes = hmac.new(f'BTCK_DKSAP_VIEW_KEY_ACCOUNT_{account_index}'.encode(), seed, hashlib.sha256).digest()
    s_bytes = hmac.new(f'BTCK_DKSAP_SPEND_KEY_ACCOUNT_{account_index}'.encode(), seed, hashlib.sha256).digest()
    
    v_int = int.from_bytes(v_bytes, 'big') % (_N - 1) + 1
    s_int = int.from_bytes(s_bytes, 'big') % (_N - 1) + 1
    
    kp = StealthKeyPair(spend_priv_scalar=s_int, view_priv_scalar=v_int)
    return v_int, s_int, kp

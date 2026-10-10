"""Password-based authenticated encryption for portable BTC-K wallet files.

The implementation follows RFC 8439 ChaCha20-Poly1305 and uses hashlib.scrypt
for password stretching so the portable wallet keeps its zero-third-party
runtime dependency.  It is intentionally small and covered by the RFC AEAD
test vector in the wallet security tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct


WALLET_FORMAT = "btck-wallet-encrypted"
WALLET_FORMAT_VERSION = 1
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024


def _rotate_left(value: int, count: int) -> int:
    return ((value << count) & 0xFFFFFFFF) | (value >> (32 - count))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left(state[b] ^ state[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    if len(key) != 32 or len(nonce) != 12 or not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("ChaCha20 requires a 32-byte key, 12-byte nonce and uint32 counter")
    initial = list(struct.unpack("<4I", b"expand 32-byte k"))
    initial += list(struct.unpack("<8I", key))
    initial += [counter]
    initial += list(struct.unpack("<3I", nonce))
    working = initial.copy()
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    return struct.pack("<16I", *(((working[i] + initial[i]) & 0xFFFFFFFF) for i in range(16)))


def _chacha20_xor(key: bytes, nonce: bytes, counter: int, data: bytes) -> bytes:
    output = bytearray()
    for offset in range(0, len(data), 64):
        if counter > 0xFFFFFFFF:
            raise ValueError("ChaCha20 counter exhausted")
        block = data[offset:offset + 64]
        stream = _chacha20_block(key, counter, nonce)
        output.extend(left ^ right for left, right in zip(block, stream))
        counter += 1
    return bytes(output)


def _poly1305_mac(message: bytes, one_time_key: bytes) -> bytes:
    if len(one_time_key) != 32:
        raise ValueError("Poly1305 requires a 32-byte one-time key")
    r = int.from_bytes(one_time_key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(one_time_key[16:], "little")
    accumulator = 0
    modulus = (1 << 130) - 5
    for offset in range(0, len(message), 16):
        chunk = message[offset:offset + 16]
        accumulator = (accumulator + int.from_bytes(chunk + b"\x01", "little")) % modulus
        accumulator = (accumulator * r) % modulus
    return ((accumulator + s) % (1 << 128)).to_bytes(16, "little")


def _pad16(value: bytes) -> bytes:
    remainder = len(value) % 16
    return b"" if remainder == 0 else b"\x00" * (16 - remainder)


def aead_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20_xor(key, nonce, 1, plaintext)
    mac_data = (
        aad + _pad16(aad) + ciphertext + _pad16(ciphertext)
        + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ciphertext))
    )
    return ciphertext, _poly1305_mac(mac_data, one_time_key)


def aead_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    mac_data = (
        aad + _pad16(aad) + ciphertext + _pad16(ciphertext)
        + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ciphertext))
    )
    expected = _poly1305_mac(mac_data, one_time_key)
    if len(tag) != 16 or not hmac.compare_digest(tag, expected):
        raise ValueError("Wallet password is incorrect or the encrypted file was modified")
    return _chacha20_xor(key, nonce, 1, ciphertext)


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_wallet_payload(private_payload: dict, public_metadata: dict, password: str) -> dict:
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError("Wallet password must contain at least 10 characters")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    kdf = {"name": "scrypt", "salt_hex": salt.hex(), "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
    aead = {"name": "chacha20-poly1305", "nonce_hex": nonce.hex()}
    header = {
        "format": WALLET_FORMAT,
        "version": WALLET_FORMAT_VERSION,
        "public": public_metadata,
        "kdf": kdf,
        "aead": aead,
    }
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32, maxmem=SCRYPT_MAXMEM,
    )
    ciphertext, tag = aead_encrypt(key, nonce, _canonical_json(private_payload), _canonical_json(header))
    header["aead"] = {**aead, "ciphertext_hex": ciphertext.hex(), "tag_hex": tag.hex()}
    return header


def decrypt_wallet_payload(envelope: dict, password: str) -> tuple[dict, dict]:
    try:
        if envelope["format"] != WALLET_FORMAT or envelope["version"] != WALLET_FORMAT_VERSION:
            raise ValueError("Unsupported encrypted wallet format")
        public = envelope["public"]
        kdf = envelope["kdf"]
        stored_aead = envelope["aead"]
        if kdf["name"] != "scrypt" or stored_aead["name"] != "chacha20-poly1305":
            raise ValueError("Unsupported wallet encryption parameters")
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            raise ValueError("Unsupported wallet scrypt parameters")
        salt = bytes.fromhex(kdf["salt_hex"])
        nonce = bytes.fromhex(stored_aead["nonce_hex"])
        ciphertext = bytes.fromhex(stored_aead["ciphertext_hex"])
        tag = bytes.fromhex(stored_aead["tag_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Unsupported"):
            raise
        raise ValueError("Encrypted wallet file is malformed") from exc
    if len(salt) != 16 or len(nonce) != 12:
        raise ValueError("Encrypted wallet salt or nonce has an invalid length")
    header = {
        "format": WALLET_FORMAT,
        "version": WALLET_FORMAT_VERSION,
        "public": public,
        "kdf": kdf,
        "aead": {"name": stored_aead["name"], "nonce_hex": stored_aead["nonce_hex"]},
    }
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        dklen=32, maxmem=SCRYPT_MAXMEM,
    )
    plaintext = aead_decrypt(key, nonce, ciphertext, tag, _canonical_json(header))
    try:
        private = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Encrypted wallet private payload is malformed") from exc
    if not isinstance(private, dict) or not isinstance(public, dict):
        raise ValueError("Encrypted wallet payload must contain objects")
    return private, public

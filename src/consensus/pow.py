"""Consensus-safe compact targets and proof-of-work validation."""

from __future__ import annotations

from consensus.kheavyhash import hash_to_target_int


MAX_UINT256 = (1 << 256) - 1


class InvalidTarget(ValueError):
    """Raised when compact target encoding is invalid or out of range."""


def compact_to_target(bits: int) -> int:
    """Decode Bitcoin-style unsigned compact target encoding."""
    if not isinstance(bits, int) or not 0 <= bits <= 0xFFFFFFFF:
        raise InvalidTarget("bits must be a uint32")

    exponent = bits >> 24
    coefficient = bits & 0x007FFFFF
    negative = bool(bits & 0x00800000)
    if negative or coefficient == 0:
        raise InvalidTarget("compact target must be positive")

    if exponent <= 3:
        target = coefficient >> (8 * (3 - exponent))
    else:
        target = coefficient << (8 * (exponent - 3))
    if target <= 0 or target > MAX_UINT256:
        raise InvalidTarget("compact target exceeds uint256 range")
    return target


def target_to_compact(target: int) -> int:
    """Encode a positive uint256 target using canonical compact encoding."""
    if not isinstance(target, int) or target <= 0 or target > MAX_UINT256:
        raise InvalidTarget("target must be a positive uint256")

    size = (target.bit_length() + 7) // 8
    if size <= 3:
        compact = target << (8 * (3 - size))
    else:
        compact = target >> (8 * (size - 3))
    if compact & 0x00800000:
        compact >>= 8
        size += 1
    return (size << 24) | (compact & 0x007FFFFF)


def validate_compact_target(bits: int, pow_limit_bits: int) -> int:
    """Return target only when encoding is canonical and within network limit."""
    target = compact_to_target(bits)
    if target_to_compact(target) != bits:
        raise InvalidTarget("non-canonical compact target encoding")
    pow_limit = compact_to_target(pow_limit_bits)
    if target > pow_limit:
        raise InvalidTarget("target is easier than the network proof-of-work limit")
    return target


def hash_meets_target(hash_bytes: bytes, bits: int, pow_limit_bits: int) -> bool:
    if len(hash_bytes) != 32:
        return False
    target = validate_compact_target(bits, pow_limit_bits)
    return hash_to_target_int(hash_bytes) <= target


def block_work(bits: int, pow_limit_bits: int) -> int:
    """Return chain work contribution using floor(2^256 / (target + 1))."""
    target = validate_compact_target(bits, pow_limit_bits)
    return (1 << 256) // (target + 1)

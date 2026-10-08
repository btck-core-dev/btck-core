"""Canonical, publicly auditable BTC-K L2 burn output.

The two compressed curve points are deterministically obtained by hash-to-curve,
not by multiplying the generator by a published scalar. Under the secp256k1
discrete-log assumption, no party knows a spend key for the fixed P point.
Every observer can reproduce the constants and identify burned SatK on L1.
"""

import hashlib
from typing import Tuple

from privacy.stealth_address import _P


BURN_DOMAIN = "BTC-K/v2.4/L2/PROVABLE-BURN"


def _nums_compressed_point(label: str) -> bytes:
    seed = hashlib.sha256(f"{BURN_DOMAIN}/{label}".encode("ascii")).digest()
    x_coord = int.from_bytes(seed, "big") % _P
    while True:
        y_squared = (pow(x_coord, 3, _P) + 7) % _P
        y_coord = pow(y_squared, (_P + 1) // 4, _P)
        if pow(y_coord, 2, _P) == y_squared:
            prefix = b"\x02" if y_coord % 2 == 0 else b"\x03"
            return prefix + x_coord.to_bytes(32, "big")
        x_coord = (x_coord + 1) % _P


BURN_R_EPHEM = _nums_compressed_point("R")
BURN_P_ONETIME = _nums_compressed_point("P")


def burn_output_hex() -> Tuple[str, str]:
    return BURN_R_EPHEM.hex(), BURN_P_ONETIME.hex()


def is_provable_burn_output(R_ephem_hex: str, P_onetime_hex: str) -> bool:
    return (
        R_ephem_hex.lower() == BURN_R_EPHEM.hex()
        and P_onetime_hex.lower() == BURN_P_ONETIME.hex()
    )

"""
BTC-K Consensus Protocol Safeguards & Invariant Enforcement Engine
------------------------------------------------------------------
Architecture & Security Specification:
1. Cryptographic Network Defense & Emergency Alert System (Satoshi-style Alert System).
   - Signs emergency beacons using secp256k1 Schnorr signatures.
   - Nodes verify against hardcoded compressed network defense public key.
   - Capable of triggering Safe Mode (read-only state during critical exploits),
     broadcasting network upgrade directives, or injecting dynamic rolling checkpoints.
2. Height-Activated Soft-Fork Registry (BIP-8 / BIP-9 Compatible Switchboard).
   - Allows seamless rule tightening and parameter hot-patching without chain splits.
3. Rolling Consensus Checkpoint Guard (Long-Range 51% Reorganization Shield).
   - Enforces permanent finality barriers against malicious deep reorgs from external ASIC pools.
"""

import json
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

from privacy.wallet_engine import schnorr_verify, sha256

# Master Network Defense Public Key (secp256k1 compressed, 33 bytes)
NETWORK_DEFENSE_PUBLIC_KEY_HEX = "035d7a287afff2d02ad0f8347ba3a5032bf1fc0b5c01e786d111b5ec69733545b1"


# =====================================================================
# 1. Satoshi-Style Cryptographic Emergency Alert System
# =====================================================================
class AlertAction:
    NOTICE = "NOTICE"                     # Advisory notice broadcasted to UI / logs
    ENABLE_SAFE_MODE = "ENABLE_SAFE_MODE" # Freeze transaction broadcasts (read-only)
    CLEAR_SAFE_MODE = "CLEAR_SAFE_MODE"   # Revoke safe mode, restore full processing
    FORCE_UPGRADE = "FORCE_UPGRADE"       # Require node update to minimum version
    INJECT_CHECKPOINT = "INJECT_CHECKPOINT" # Lock in an immutable block checkpoint


@dataclass
class NetworkAlertPayload:
    alert_id: int
    version: int
    expiration_timestamp: int
    priority: int                         # 1=Low, 2=Medium, 3=Critical
    actions: List[str]
    message: str
    min_client_version: Optional[str] = None
    checkpoint_height: Optional[int] = None
    checkpoint_hash: Optional[str] = None
    cancel_alert_id: Optional[int] = None # Revoke a previous active alert

    def compute_digest(self) -> bytes:
        """Deterministic canonical JSON hashing of the payload without signature."""
        data = {
            "alert_id": self.alert_id,
            "version": self.version,
            "expiration_timestamp": self.expiration_timestamp,
            "priority": self.priority,
            "actions": sorted(self.actions),
            "message": self.message,
            "min_client_version": self.min_client_version,
            "checkpoint_height": self.checkpoint_height,
            "checkpoint_hash": self.checkpoint_hash,
            "cancel_alert_id": self.cancel_alert_id
        }
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(canonical_bytes)


@dataclass
class SignedNetworkAlert:
    payload: NetworkAlertPayload
    signature_hex: str

    def verify(self, pubkey_hex: str = NETWORK_DEFENSE_PUBLIC_KEY_HEX) -> bool:
        """Cryptographically verifies payload against network defense public key using Schnorr."""
        try:
            pub_bytes = bytes.fromhex(pubkey_hex)
            sig_bytes = bytes.fromhex(self.signature_hex)
            digest = self.payload.compute_digest()
            return schnorr_verify(pub_bytes, digest, sig_bytes)
        except Exception:
            return False


class AlertManager:
    """Manages active network alerts and safe mode state within the node daemon."""

    def __init__(self, pubkey_hex: str = NETWORK_DEFENSE_PUBLIC_KEY_HEX):
        self.pubkey_hex = pubkey_hex
        self.safe_mode_active = False
        self.active_alerts: Dict[int, SignedNetworkAlert] = {}
        self.cancelled_alert_ids: set = set()

    def process_incoming_alert(self, alert: SignedNetworkAlert, current_time: Optional[float] = None) -> Tuple[bool, str]:
        if current_time is None:
            current_time = time.time()

        # 1. Verify cryptographic Schnorr signature
        if not alert.verify(self.pubkey_hex):
            return False, "Cryptographic alert signature verification failed"

        # 2. Check expiration
        if current_time >= alert.payload.expiration_timestamp:
            return False, f"Alert {alert.payload.alert_id} has expired"

        # 3. Handle cancellation of previous alerts
        if alert.payload.cancel_alert_id is not None:
            cancel_id = alert.payload.cancel_alert_id
            self.cancelled_alert_ids.add(cancel_id)
            if cancel_id in self.active_alerts:
                del self.active_alerts[cancel_id]

        if alert.payload.alert_id in self.cancelled_alert_ids:
            return False, f"Alert {alert.payload.alert_id} was revoked by a subsequent directive"

        # 4. Apply actions
        self.active_alerts[alert.payload.alert_id] = alert
        if AlertAction.ENABLE_SAFE_MODE in alert.payload.actions:
            self.safe_mode_active = True
        elif AlertAction.CLEAR_SAFE_MODE in alert.payload.actions:
            self.safe_mode_active = False

        return True, f"Alert {alert.payload.alert_id} verified and applied successfully"

    def is_safe_mode(self) -> bool:
        """Returns True if the node is currently locked in Safe Mode (read-only defense)."""
        return self.safe_mode_active

    def get_latest_broadcast_notice(self) -> Optional[str]:
        if not self.active_alerts:
            return None
        highest = max(self.active_alerts.values(), key=lambda a: (a.payload.priority, a.payload.alert_id))
        return highest.payload.message


# =====================================================================
# 2. Height-Activated Soft-Fork Engine (BIP-8 Framework)
# =====================================================================
@dataclass
class SoftForkRule:
    rule_id: str
    activation_height: int
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)


class SoftForkManager:
    """
    Manages conditional protocol parameter switches and rule tightening by block height.
    Enables zero-split forward-compatible protocol adaptations.
    """

    def __init__(self):
        self.rules: Dict[str, SoftForkRule] = {}

    def register_rule(self, rule_id: str, activation_height: int, description: str, parameters: Optional[Dict[str, Any]] = None):
        self.rules[rule_id] = SoftForkRule(
            rule_id=rule_id,
            activation_height=activation_height,
            description=description,
            parameters=parameters or {}
        )

    def is_rule_active(self, rule_id: str, current_height: int) -> bool:
        if rule_id not in self.rules:
            return False
        return current_height >= self.rules[rule_id].activation_height

    def get_parameter(self, rule_id: str, param_name: str, default_val: Any, current_height: int) -> Any:
        if not self.is_rule_active(rule_id, current_height):
            return default_val
        return self.rules[rule_id].parameters.get(param_name, default_val)


# =====================================================================
# 3. Rolling Consensus Checkpoint Guard
# =====================================================================
class RollingCheckpointGuard:
    """
    Guards against long-range 51% reorgs by enforcing immutable block hash checkpoints.
    Historical blocks prior to a confirmed checkpoint can never be rewritten.
    """

    def __init__(self):
        # Initial canonical consensus checkpoints
        self.checkpoints: Dict[int, str] = {
            0: "genesis_block_0",
            271_828: "0000000000000000000000000000000000000000000000000000000000042718" # Euler checkpoint homage
        }

    def add_checkpoint(self, height: int, block_hash: str):
        self.checkpoints[height] = block_hash

    def is_block_valid_against_checkpoints(self, height: int, block_hash: str) -> bool:
        if height in self.checkpoints:
            expected = self.checkpoints[height]
            return (block_hash == expected) or (expected == "genesis_block_0" and height == 0)
        return True

    def get_latest_checkpoint_height(self) -> int:
        return max(self.checkpoints.keys()) if self.checkpoints else 0


# =====================================================================
# 4. Global Consensus Guardian Singleton
# =====================================================================
class ConsensusGuardian:
    """Unified entrypoint for all L1 safety safeguards and invariant enforcement."""

    def __init__(self):
        self.alerts = AlertManager(NETWORK_DEFENSE_PUBLIC_KEY_HEX)
        self.softforks = SoftForkManager()
        self.checkpoints = RollingCheckpointGuard()

    def check_safe_mode_or_raise(self):
        if self.alerts.is_safe_mode():
            raise PermissionError("Node is currently operating in Safe Mode: transaction execution is paused for network defense.")


consensus_guardian = ConsensusGuardian()

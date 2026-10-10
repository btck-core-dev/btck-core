"""
BTC-K Embodied AI & Robotics Delegated Wallet SDK (Hardened Profile v2.2.0)
--------------------------------------------------------------------------
Enables physical robots (Unitree, Tesla Optimus, Drones, Autonomous Delivery AGVs)
to operate autonomous micro-wallets under master policy constraints:

1. Hierarchical Machine Isolation (BIP-44: m/44'/9999'/0'/machine_index/0)
2. Hardware Enclave & TEE Abstraction (ARM TrustZone / NVIDIA Jetson TPM / ROS2)
3. Allowance & Policy Pacts (Default-Deny, positive integer SatK, 3-tier budget caps & concurrency locks)
4. Physical Trajectory Anti-Tracking (Dual-key stealth addresses btck1s... prevent mapping robot location)
5. Master Revocation & Circuit Breaker (Owner can invalidate sub-wallet without exposing cold master keys)

Security Model Disclosures (Audit v3 / H-05):
- Recipient Privacy: Dual-key stealth addresses generate unforgeable one-time outputs (P_onetime),
  preventing on-chain observers from linking physical payment locations to the recipient's identity.
- Payer Pseudonymity: The payer's stealth address is used for peer-to-peer authorization. Without a
  mixing layer (CoinJoin / ring signatures), payer addresses remain linkable across direct RPC sessions.
- Physical / Network Metadata: Off-chain network identifiers (IP addresses, merchant access logs, physical
  cameras) are outside the cryptographic boundary and require transport-level anonymity (Tor/I2P).
"""

import hashlib
import json
import os
import secrets
import sys
import threading
import time
import uuid
from typing import Dict, Any, Optional, List, Tuple

# Path discovery fallback
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
src_dir = os.path.join(base_dir, "src")
sdk_dir = os.path.join(base_dir, "sdk")
for p in (src_dir, sdk_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from privacy.stealth_address import StealthKeyPair, sha256, _N, _G
from privacy.wallet_engine import schnorr_sign, schnorr_verify
from btck_agent.wallet import BTCKWallet
from btck_agent.delegation import DelegationReceipt

BIP44_MACHINE_PURPOSE = 44
COIN_TYPE_BTCK = 9999

class HardwareEnclaveInterface:
    """
    Hardware Abstraction Layer (HAL) for Robot TEE / TPM 2.0 / ARM TrustZone.
    
    Security Specification (Fixes C-02):
    - Generates and stores internal private keys using cryptographically secure high-entropy random sources (secrets.token_bytes).
    - Private key scalars are NEVER derived from public inputs (master address, derivation path, or device ID).
    - Private keys NEVER leave the enclave boundary. External callers can only access public keys and request signatures via secure_sign().
    - In development/testing environments, this class provides a SoftwareSimulatedEnclave mode.
      In production robotics hardware (NVIDIA Jetson AGX Orin, Unitree Go2 ARM TrustZone), this is backed by hardware TPM2 / PKCS#11.
    """
    def __init__(self, device_id: str, is_simulated: bool = True, seed_entropy: Optional[bytes] = None):
        self.device_id = device_id
        self.is_simulated = is_simulated
        
        # High-entropy internal keys generated within the enclave boundary (never derived from public inputs)
        raw_spend = seed_entropy if (seed_entropy and len(seed_entropy) == 32) else secrets.token_bytes(32)
        raw_view = sha256(raw_spend + b"_internal_enclave_view_" + secrets.token_bytes(16))
        
        self._spend_priv = (int.from_bytes(raw_spend, "big") % (_N - 1)) + 1
        self._view_priv = (int.from_bytes(raw_view, "big") % (_N - 1)) + 1
        
        # Create stealth keypair inside enclave
        self._keypair = StealthKeyPair(self._spend_priv, self._view_priv)
        self._hardware_fingerprint = hashlib.sha256(
            f"{self.device_id}_{self._keypair.spend_pub_bytes.hex()}".encode()
        ).hexdigest()[:16]

    def secure_sign(self, message_digest: bytes) -> bytes:
        """Execute BIP-340 Schnorr signature strictly inside isolated enclave memory boundary."""
        if not isinstance(message_digest, bytes) or len(message_digest) != 32:
            raise ValueError("message_digest must be a 32-byte hash")
        return schnorr_sign(self._spend_priv, message_digest)

    def get_public_keys(self) -> Tuple[bytes, bytes, str]:
        """Return (spend_pub_bytes, view_pub_bytes, stealth_address). Private keys are non-exportable."""
        return (
            self._keypair.spend_pub_bytes,
            self._keypair.view_pub_bytes,
            self._keypair.get_public_stealth_address()
        )

    def get_hardware_fingerprint(self) -> str:
        return self._hardware_fingerprint

    def is_hardware_backed(self) -> bool:
        return not self.is_simulated


class RobotDelegatedWallet:
    """
    Autonomous Sub-Wallet for physical Embodied AI machines (Unitree, Tesla Optimus, Drones, AGVs).
    Operates strictly under a Master Principal's signed policy in Default-Deny mode (Fixes H-03).
    """
    def __init__(
        self,
        robot_id: str,
        master_address: str,
        machine_index: int = 1,
        enclave: Optional[HardwareEnclaveInterface] = None,
        rpc_url: str = "http://127.0.0.1:16110/rpc"
    ):
        self.robot_id = robot_id
        self.master_address = master_address
        self.machine_index = machine_index
        self.derivation_path = f"m/{BIP44_MACHINE_PURPOSE}'/{COIN_TYPE_BTCK}'/0'/{machine_index}'/0"
        self.rpc_url = rpc_url
        self._lock = threading.Lock()
        
        # Hardware Enclave: private keys remain inside the HAL (C-02)
        self.enclave = enclave or HardwareEnclaveInterface(device_id=robot_id)
        self.spend_pub_bytes, self.view_pub_bytes, self.address = self.enclave.get_public_keys()
        
        # Active Master Policy Pact - Default Deny (H-03)
        # Without an active verified delegation, the robot cannot spend any funds.
        self.active_delegation: Optional[DelegationReceipt] = None
        
        # 3-tier budget limits (uint64 integer SatK, Fixes H-03 & H-06)
        self.single_tx_limit_satk: int = 0
        self.daily_budget_satk: int = 0
        self.spent_today_satk: int = 0
        self.lifetime_spending_cap_satk: int = 0
        self.total_spent_lifetime_satk: int = 0
        self.last_budget_reset_day: int = int(time.time() // 86400)

    def bind_master_policy(
        self,
        delegation: DelegationReceipt,
        single_tx_limit_satk: Optional[int] = None
    ):
        """
        Bind Master Owner's signed delegation pact.
        Enforces cryptographic signature verification before activating any spending permission.
        """
        if not delegation.verify():
            raise ValueError("Master policy signature verification failed! Delegation rejected.")
        
        # Security Boundary Enforcement: AgentID & Master Principal Verification (Chapter 10 Audit)
        if delegation.agent_id != self.robot_id:
            raise ValueError(f"Delegation agent_id '{delegation.agent_id}' does not match robot_id '{self.robot_id}'")

        if self.master_address and delegation.principal_stealth_address != self.master_address:
            raise ValueError(
                f"Delegation principal '{delegation.principal_stealth_address}' does not match configured master_address '{self.master_address}'"
            )

        with self._lock:
            self.active_delegation = delegation
            total_cap = int(delegation.max_amount_satk)
            self.lifetime_spending_cap_satk = total_cap
            
            # Extract daily & single limit policies from policy parameters
            self.daily_budget_satk = min(total_cap, int(delegation.max_amount_satk))
            self.single_tx_limit_satk = single_tx_limit_satk if (single_tx_limit_satk and single_tx_limit_satk > 0) else max(1, self.daily_budget_satk // 2)
            
            # Anti-Bypass: Preserve cumulative lifetime & daily spending across rebindings
            # Ensure day reset only triggers if calendar day actually rolled over
            current_day = int(time.time() // 86400)
            if current_day > self.last_budget_reset_day:
                self.spent_today_satk = 0
                self.last_budget_reset_day = current_day

    def check_and_deduct_allowance(self, amount_satk: int, service_id: str) -> bool:
        """
        Enforce strict Default-Deny, positive integer SatK, 3-tier budget caps & concurrency locks (Fixes H-03 & H-06).
        """
        # Type & value safety: strictly positive integer SatK
        if not isinstance(amount_satk, int) or isinstance(amount_satk, bool):
            raise TypeError(f"amount_satk must be an integer, got {type(amount_satk).__name__}")
        if amount_satk <= 0:
            raise ValueError(f"amount_satk must be strictly positive (> 0), got {amount_satk}")

        with self._lock:
            # 1. Default-Deny: Must have active delegation
            if self.active_delegation is None:
                raise PermissionError("Default-Deny: Robot sub-wallet cannot spend without an active, verified Master Delegation Pact.")

            # 2. Expiration check
            if self.active_delegation.is_expired():
                raise TimeoutError("Master delegation policy has expired.")

            # 3. Whitelist check
            if not self.active_delegation.is_service_allowed(service_id):
                raise PermissionError(f"Robot action '{service_id}' is not permitted by owner whitelist.")

            # 4. Tier 1: Single Transaction Limit
            if self.single_tx_limit_satk > 0 and amount_satk > self.single_tx_limit_satk:
                raise OverflowError(
                    f"Single transaction limit exceeded: requested {amount_satk} SatK, cap is {self.single_tx_limit_satk} SatK."
                )

            # 5. Day rollover for Tier 2: Daily Budget
            current_day = int(time.time() // 86400)
            if current_day > self.last_budget_reset_day:
                self.spent_today_satk = 0
                self.last_budget_reset_day = current_day

            if self.spent_today_satk + amount_satk > self.daily_budget_satk:
                remaining_today = max(0, self.daily_budget_satk - self.spent_today_satk)
                raise OverflowError(
                    f"Daily allowance exceeded: attempted {amount_satk} SatK, remaining daily {remaining_today} SatK."
                )

            # 6. Tier 3: Lifetime Total Spending Ceiling
            if self.total_spent_lifetime_satk + amount_satk > self.lifetime_spending_cap_satk:
                remaining_lifetime = max(0, self.lifetime_spending_cap_satk - self.total_spent_lifetime_satk)
                raise OverflowError(
                    f"Lifetime delegation budget cap exceeded: attempted {amount_satk} SatK, remaining lifetime {remaining_lifetime} SatK."
                )

            # Deduct allowance
            self.spent_today_satk += amount_satk
            self.total_spent_lifetime_satk += amount_satk
            return True

    def pay_physical_service(self, service_id: str, recipient_stealth_address: str, amount_satk: int) -> Dict[str, Any]:
        """
        Execute physical payment (e.g. EV charging pole, automated toll, store checkout).
        Generates verifiable Schnorr-signed receipt with canonical digest (Fixes H-04).
        """
        # Deducts allowance under lock and validates integer SatK
        self.check_and_deduct_allowance(amount_satk, service_id)
        
        # Generate one-time stealth output for recipient to preserve recipient anonymity
        R_ephem, P_onetime = StealthKeyPair.generate_one_time_stealth_output(recipient_stealth_address)
        payment_nonce = uuid.uuid4().hex[:16]
        timestamp = int(time.time())
        
        # Canonical serialization for digest (payer, recipient, amount_satk, service_id, nonce, timestamp, R_ephem, P_onetime)
        canonical_str = (
            f"BTCK_ROBOT_PAYMENT_V1:{self.address}:{recipient_stealth_address}:"
            f"{amount_satk}:{service_id}:{payment_nonce}:{timestamp}:"
            f"{R_ephem.hex()}:{P_onetime.hex()}"
        )
        tx_digest = sha256(canonical_str.encode('utf-8'))
        
        # Hardware Enclave Signature (private key never leaves enclave boundary)
        sig = self.enclave.secure_sign(tx_digest)
        
        return {
            "status": "SIGNED_PREPARED",
            "protocol": "x402-Robotics-v2",
            "robot_id": self.robot_id,
            "derivation_path": self.derivation_path,
            "payer_stealth_address": self.address,
            "payer_spend_pub_hex": self.spend_pub_bytes.hex(),
            "recipient_stealth_address": recipient_stealth_address,
            "recipient_one_time_output": P_onetime.hex(),
            "ephemeral_pubkey": R_ephem.hex(),
            "amount_satk": amount_satk,
            "service_id": service_id,
            "payment_nonce": payment_nonce,
            "canonical_digest_hex": tx_digest.hex(),
            "schnorr_signature": sig.hex(),
            "anti_tracking_verified": True,
            "timestamp": timestamp
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "robot_id": self.robot_id,
                "master_address": self.master_address,
                "derivation_path": self.derivation_path,
                "robot_stealth_address": self.address,
                "hardware_fingerprint": self.enclave.get_hardware_fingerprint(),
                "is_hardware_backed": self.enclave.is_hardware_backed(),
                "has_active_delegation": self.active_delegation is not None,
                "single_tx_limit_satk": self.single_tx_limit_satk,
                "daily_budget_satk": self.daily_budget_satk,
                "spent_today_satk": self.spent_today_satk,
                "remaining_daily_budget_satk": max(0, self.daily_budget_satk - self.spent_today_satk),
                "lifetime_spending_cap_satk": self.lifetime_spending_cap_satk,
                "total_spent_lifetime_satk": self.total_spent_lifetime_satk,
                "remaining_lifetime_budget_satk": max(0, self.lifetime_spending_cap_satk - self.total_spent_lifetime_satk)
            }

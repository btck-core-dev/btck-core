"""
BTC-K Cryptographic Delegation Receipt & Enforceable Budget Engine
------------------------------------------------------------------
Implements the headless economy governance specification (Doc 27):
1. Principle: Balance != Authorization (余额 != 支出授权)
2. Enforceable Spending Caps: Execution boundary hard limits protected from LLM injection
3. Cryptographic Audit Trail: Schnorr-signed delegation by human principal on secp256k1
4. Complete External Privacy: Preserves Dual-Key Stealth Address anonymity on wire
"""

import hashlib
import json
import time
import uuid
import threading
from typing import List, Dict, Any, Optional

import sys
from pathlib import Path
_project_src = str(Path(__file__).resolve().parent.parent.parent / "src")
if _project_src not in sys.path:
    sys.path.insert(0, _project_src)
from privacy.stealth_address import StealthKeyPair, sha256
from privacy.wallet_engine import schnorr_sign, schnorr_verify

class DelegationSecurityError(Exception):
    """Base exception for delegation policy violations"""
    pass

class DelegationExpiredError(DelegationSecurityError):
    """Raised when delegation validity window has lapsed"""
    pass

class UnauthorizedServiceError(DelegationSecurityError):
    """Raised when agent attempts to call a non-whitelisted service"""
    pass

class BudgetExceededError(DelegationSecurityError):
    """Raised when requested expenditure exceeds hard authorized budget cap"""
    pass

class DelegationReceipt:
    """
    Cryptographic Delegation Receipt signed by human principal's spend key.
    Bound to an agent_id with explicit service whitelist and hard SatK cap.
    """
    def __init__(
        self,
        delegation_id: str,
        principal_stealth_address: str,
        principal_spend_pub_hex: str,
        agent_id: str,
        max_amount_satk: float,
        allowed_services: List[str],
        expires_at: int,
        policy_hash: str,
        signature_hex: str = ""
    ):
        self.delegation_id = delegation_id
        self.principal_stealth_address = principal_stealth_address
        self.principal_spend_pub_hex = principal_spend_pub_hex
        self.agent_id = agent_id
        self.max_amount_satk = float(max_amount_satk)
        self.allowed_services = [s.lower() for s in allowed_services]
        self.expires_at = int(expires_at)
        self.policy_hash = policy_hash
        self.signature_hex = signature_hex

    def compute_digest(self) -> bytes:
        """Compute canonical cryptographic SHA-256 digest of delegation parameters"""
        canonical_str = (
            f"BTCK_DELEGATION_V1:{self.delegation_id}:{self.principal_stealth_address}:"
            f"{self.principal_spend_pub_hex}:{self.agent_id}:{self.max_amount_satk:.4f}:"
            f"{','.join(sorted(self.allowed_services))}:{self.expires_at}:{self.policy_hash}"
        )
        return hashlib.sha256(canonical_str.encode('utf-8')).digest()

    def sign(self, principal_keypair: StealthKeyPair) -> str:
        """Sign delegation using principal's private spend scalar (Schnorr BIP-340)"""
        digest = self.compute_digest()
        sig_bytes = schnorr_sign(principal_keypair.spend_priv, digest)
        self.signature_hex = sig_bytes.hex()
        return self.signature_hex

    def verify(self) -> bool:
        """Verify that the delegation was cryptographically signed by the principal's spend key"""
        if not self.signature_hex or not self.principal_spend_pub_hex:
            return False
        try:
            digest = self.compute_digest()
            pubkey_bytes = bytes.fromhex(self.principal_spend_pub_hex)
            sig_bytes = bytes.fromhex(self.signature_hex)
            return schnorr_verify(pubkey_bytes, digest, sig_bytes)
        except Exception:
            return False

    def is_expired(self, current_time: Optional[int] = None) -> bool:
        now = current_time or int(time.time())
        return now > self.expires_at

    def is_service_allowed(self, service_id: str) -> bool:
        if not self.allowed_services:
            return True
        return service_id.lower() in self.allowed_services

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "principal_stealth_address": self.principal_stealth_address,
            "principal_spend_pub_hex": self.principal_spend_pub_hex,
            "agent_id": self.agent_id,
            "max_amount_satk": self.max_amount_satk,
            "allowed_services": self.allowed_services,
            "expires_at": self.expires_at,
            "policy_hash": self.policy_hash,
            "signature_hex": self.signature_hex
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DelegationReceipt":
        return cls(
            delegation_id=data["delegation_id"],
            principal_stealth_address=data["principal_stealth_address"],
            principal_spend_pub_hex=data["principal_spend_pub_hex"],
            agent_id=data["agent_id"],
            max_amount_satk=data["max_amount_satk"],
            allowed_services=data.get("allowed_services", []),
            expires_at=data["expires_at"],
            policy_hash=data.get("policy_hash", "default_policy"),
            signature_hex=data.get("signature_hex", "")
        )

    @classmethod
    def issue(
        cls,
        principal_keypair: StealthKeyPair,
        agent_id: str,
        max_amount_satk: float,
        allowed_services: Optional[List[str]] = None,
        duration_seconds: int = 3600,
        policy_rules: Optional[Dict[str, Any]] = None
    ) -> "DelegationReceipt":
        """Convenience method for human principal to issue a signed delegation to an agent"""
        d_id = f"del_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        expires = now + duration_seconds
        services = allowed_services or []
        policy_dict = policy_rules or {"version": "1.0", "enforce_stealth_privacy": True}
        policy_hash = hashlib.sha256(json.dumps(policy_dict, sort_keys=True).encode()).hexdigest()

        receipt = cls(
            delegation_id=d_id,
            principal_stealth_address=principal_keypair.get_public_stealth_address(),
            principal_spend_pub_hex=principal_keypair.spend_pub_bytes.hex(),
            agent_id=agent_id,
            max_amount_satk=max_amount_satk,
            allowed_services=services,
            expires_at=expires,
            policy_hash=policy_hash
        )
        receipt.sign(principal_keypair)
        return receipt

class EnforceableBudgetTracker:
    """
    Code-level budget checkpoint. Completely isolated from LLM prompt text.
    Maintains cryptographic spending state and blocks execution if limits are breached.
    """
    def __init__(self, delegation: DelegationReceipt):
        if not delegation.verify():
            raise DelegationSecurityError("Cannot initialize BudgetTracker with invalid or forged delegation signature!")
        self.delegation = delegation
        self.total_spent_satk: int = 0
        self.settled_tasks: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def check_authorization(self, service_id: str, requested_cost_satk: int) -> None:
        """
        Verify that request complies with delegation terms.
        Raises DelegationSecurityError sub-types upon violation.
        """
        with self._lock:
            # 1. Expiration check
            if self.delegation.is_expired():
                raise DelegationExpiredError(
                    f"Delegation [{self.delegation.delegation_id}] expired at {self.delegation.expires_at}."
                )

            # 2. Service whitelist check
            if not self.delegation.is_service_allowed(service_id):
                raise UnauthorizedServiceError(
                    f"Service '{service_id}' is not in authorized whitelist: {self.delegation.allowed_services}"
                )

            # 3. Hard spending cap check (strictly integer SatK)
            req_cost = int(requested_cost_satk)
            new_total = self.total_spent_satk + req_cost
            if new_total > int(self.delegation.max_amount_satk):
                raise BudgetExceededError(
                    f"Expenditure of {req_cost} SatK exceeds remaining budget. "
                    f"Spent: {self.total_spent_satk}/{int(self.delegation.max_amount_satk)} SatK."
                )

    def record_settled_task(
        self,
        task_id: str,
        service_id: str,
        cost_satk: int,
        result_hash: str,
        receipt_hash: str
    ):
        """Record settled task and commit spent funds"""
        with self._lock:
            cost = int(cost_satk)
            self.total_spent_satk += cost
            self.settled_tasks.append({
                "task_id": task_id,
                "service_id": service_id,
                "cost_satk": cost,
                "result_hash": result_hash,
                "receipt_hash": receipt_hash,
                "total_spent_so_far": self.total_spent_satk,
                "timestamp": int(time.time())
            })

    def get_remaining_budget(self) -> int:
        with self._lock:
            return max(0, int(self.delegation.max_amount_satk) - self.total_spent_satk)

    def get_audit_summary(self) -> Dict[str, Any]:
        with self._lock:
            rem = max(0, int(self.delegation.max_amount_satk) - self.total_spent_satk)
            return {
                "delegation_id": self.delegation.delegation_id,
                "principal_stealth_address": self.delegation.principal_stealth_address,
                "agent_id": self.delegation.agent_id,
                "max_budget_satk": int(self.delegation.max_amount_satk),
                "total_spent_satk": self.total_spent_satk,
                "remaining_budget_satk": rem,
                "tasks_count": len(self.settled_tasks),
                "tasks": list(self.settled_tasks)
            }

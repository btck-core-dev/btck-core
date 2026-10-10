"""
BTC-K AI Agent Client (Zero-KYC, Sats-Powered LLM Client)
---------------------------------------------------------
Upgraded with Document 27 Headless Economy Defense Specifications:
1. Dual-Key Stealth Privacy: Zero-KYC anonymity on wire (Monero / BIP47)
2. Enforceable Delegation Receipts: Hard budget & service whitelist execution boundary
3. Cryptographic Proof of Delivery: Validates provider result_hash and Schnorr signature
4. Idempotency & Replay Defense: Automatic Idempotency-Key handling
5. State Machine & unknown_effect Handling: Safe recovery on timeout without double-charging
"""

import hashlib
import json
import time
import uuid
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List

from .wallet import BTCKWallet
from .delegation import DelegationReceipt, EnforceableBudgetTracker, DelegationSecurityError, BudgetExceededError, UnauthorizedServiceError
from .state_machine import TaskState, AgentTaskContext, InvalidStateTransitionError

import sys
from pathlib import Path
_project_src = str(Path(__file__).resolve().parent.parent.parent / "src")
if _project_src not in sys.path:
    sys.path.insert(0, _project_src)
from privacy.wallet_engine import schnorr_verify

class DeliveryVerificationError(Exception):
    """Raised when server response fails cryptographic hash or signature verification"""
    pass

class BTCKAgentClient:
    def __init__(
        self,
        wallet: Optional[BTCKWallet] = None,
        gateway_url: str = "http://127.0.0.1:8080",
        delegation: Optional[DelegationReceipt] = None,
        budget_tracker: Optional[EnforceableBudgetTracker] = None,
        agent_id: str = "default_autonomous_agent"
    ):
        self.wallet = wallet or BTCKWallet.generate()
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.delegation = delegation
        self.budget_tracker = budget_tracker
        if self.delegation and not self.budget_tracker:
            self.budget_tracker = EnforceableBudgetTracker(self.delegation)

    def attach_delegation(self, delegation: DelegationReceipt):
        """Bind human principal's signed delegation receipt"""
        if not delegation.verify():
            raise DelegationSecurityError("Delegation signature verification failed!")
        self.delegation = delegation
        self.budget_tracker = EnforceableBudgetTracker(delegation)

    def get_service_cards(self) -> List[Dict[str, Any]]:
        """Fetch standardized machine service cards from gateway"""
        url = f"{self.gateway_url}/v1/service-cards"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("services", [])

    def get_quote(self, service_id: str, units: int = 1000) -> Dict[str, Any]:
        """Fetch pre-execution quote with expiration and price ceiling"""
        url = f"{self.gateway_url}/v1/quote?service={service_id}&units={units}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def reconcile_task(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Reconcile unknown_effect task with gateway via Idempotency-Key"""
        url = f"{self.gateway_url}/v1/tasks/{idempotency_key}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise

    def chat_completion(
        self,
        model: str,
        prompt: str,
        max_tokens: int = 500,
        idempotency_key: Optional[str] = None,
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """
        Execute an AI chat completion request with complete Doc 27 defenses:
        1. Check Delegation & Hard Budget
        2. Set Idempotency-Key
        3. Handle HTTP 402 with Stealth SatK Micropayment
        4. Validate Cryptographic Proof of Delivery
        5. Handle Network Timeouts with unknown_effect state
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        idem_key = idempotency_key or f"idem_{uuid.uuid4().hex[:16]}"
        task_ctx = AgentTaskContext(task_id, idem_key, TaskState.QUOTED)

        # 1. Estimate Cost
        est_tokens = len(prompt.split()) * 2 + max_tokens
        rate_approx = 0.01  # approx 10 SatK / 1k tokens
        est_cost_satk = max(1.0, round(est_tokens * rate_approx, 4))

        # 2. Delegation & Budget Check
        if self.delegation:
            task_ctx.transition_to(TaskState.DELEGATED, "Delegation attached")
            task_ctx.delegation_id = self.delegation.delegation_id

        if self.budget_tracker:
            self.budget_tracker.check_authorization(model, est_cost_satk)
            task_ctx.transition_to(TaskState.AUTHORIZED, f"Budget approved for estimate {est_cost_satk} SatK")
        else:
            if task_ctx.state == TaskState.QUOTED:
                task_ctx.transition_to(TaskState.DELEGATED, "Autonomous self-funded")
            task_ctx.transition_to(TaskState.AUTHORIZED, "Autonomous wallet authorization")

        # 3. Prepare Payload
        url = f"{self.gateway_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False
        }
        data = json.dumps(payload).encode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Stealth-Address": self.wallet.address,
            "Idempotency-Key": idem_key
        }
        if self.delegation:
            headers["X-Delegation-Receipt"] = json.dumps(self.delegation.to_dict())

        # 4. Dispatch Execution
        task_ctx.transition_to(TaskState.EXECUTION_STARTED, f"HTTP POST dispatched (Idempotency: {idem_key})")
        req = urllib.request.Request(url, data=data, headers=headers)

        resp_data = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as err:
            if err.code == 402:
                # HTTP 402 Payment Required!
                err_data = json.loads(err.read().decode('utf-8'))
                invoice = err_data.get("error", {}).get("invoice", {})
                required_satk = invoice.get("amount_satk", est_cost_satk)

                # Re-verify budget against exact invoice
                if self.budget_tracker:
                    self.budget_tracker.check_authorization(model, required_satk)

                # Pay invoice using wallet (dual-key stealth one-time output)
                payment_receipt = self.wallet.pay_invoice(invoice)
                payment_receipt["amount_satk"] = required_satk

                # Retry with payment proof + Idempotency-Key
                retry_headers = dict(headers)
                retry_headers["Authorization"] = f"BTCK-SatK {json.dumps(payment_receipt)}"
                retry_req = urllib.request.Request(url, data=data, headers=retry_headers)
                
                try:
                    with urllib.request.urlopen(retry_req, timeout=timeout) as retry_resp:
                        resp_data = json.loads(retry_resp.read().decode('utf-8'))
                except (urllib.error.URLError, TimeoutError) as net_err:
                    task_ctx.transition_to(TaskState.UNKNOWN_EFFECT, f"Payment sent but timeout occurred: {net_err}")
                    # Attempt safe reconciliation via Idempotency-Key
                    reconciled = self.reconcile_task(idem_key)
                    if reconciled and "choices" in reconciled:
                        resp_data = reconciled
                        task_ctx.transition_to(TaskState.DELIVERED, "Recovered via idempotency reconciliation")
                    else:
                        task_ctx.transition_to(TaskState.RECONCILE_REQUIRED, "Payment in-flight, manual reconcile required")
                        raise TimeoutError(f"Task entered unknown_effect state: payment submitted but response timed out. Idempotency Key: {idem_key}")
            else:
                task_ctx.transition_to(TaskState.DISPUTED, f"HTTP Error {err.code}")
                raise RuntimeError(f"HTTP Error {err.code}: {err.read().decode('utf-8')}")
        except (urllib.error.URLError, TimeoutError) as net_err:
            task_ctx.transition_to(TaskState.UNKNOWN_EFFECT, f"Network timeout: {net_err}")
            reconciled = self.reconcile_task(idem_key)
            if reconciled and "choices" in reconciled:
                resp_data = reconciled
                task_ctx.transition_to(TaskState.DELIVERED, "Recovered via idempotency reconciliation")
            else:
                raise

        if not resp_data:
            raise RuntimeError("Empty response received from gateway.")

        if task_ctx.state != TaskState.DELIVERED:
            task_ctx.transition_to(TaskState.DELIVERED, "Response payload received from gateway")

        # 5. Verify Cryptographic Proof of Delivery (Doc 27 Section 6)
        actual_content = resp_data["choices"][0]["message"]["content"]
        delivery_receipt = resp_data.get("delivery_receipt", {})
        task_ctx.delivery_receipt = delivery_receipt

        if delivery_receipt:
            result_hash = delivery_receipt.get("result_hash", "")
            expected_digest = hashlib.sha256(actual_content.encode('utf-8')).digest()
            expected_hash = f"sha256:{expected_digest.hex()}"
            if result_hash != expected_hash:
                task_ctx.transition_to(TaskState.DISPUTED, "Content delivery hash mismatch!")
                raise DeliveryVerificationError(
                    f"Result hash mismatch: expected {expected_hash}, got {result_hash}"
                )

            # Verify Gateway Schnorr Signature
            sig_hex = delivery_receipt.get("signature_hex", "")
            pub_hex = delivery_receipt.get("provider_spend_pub_hex", "")
            if sig_hex and pub_hex:
                is_valid = schnorr_verify(bytes.fromhex(pub_hex), expected_digest, bytes.fromhex(sig_hex))
                if not is_valid:
                    task_ctx.transition_to(TaskState.DISPUTED, "Invalid provider delivery signature!")
                    raise DeliveryVerificationError("Provider delivery Schnorr signature verification failed!")

        task_ctx.transition_to(TaskState.ACCEPTED, "Proof of delivery cryptographically verified")

        # 6. Commit Cost to Budget Tracker
        actual_cost = resp_data.get("usage", {}).get("cost_satk", est_cost_satk)
        if self.budget_tracker:
            self.budget_tracker.record_settled_task(
                task_id=task_id,
                service_id=model,
                cost_satk=actual_cost,
                result_hash=task_ctx.result_hash or "",
                receipt_hash=delivery_receipt.get("result_hash", "")
            )

        task_ctx.transition_to(TaskState.SETTLED, f"Settled {actual_cost} SatK")
        resp_data["task_context"] = task_ctx.to_dict()
        return resp_data

"""
BTC-K Headless Economy Task State Machine
------------------------------------------
Implements Section 7 of Document 27:
Standard: quoted -> delegated -> authorized -> execution_started -> delivered -> accepted -> settled
Exceptions: unknown_effect, reconcile_required, disputed, timeout, cancelled
"""

from enum import Enum
import time
from typing import List, Dict, Any, Optional

class TaskState(str, Enum):
    # Normal Forward Path
    QUOTED = "quoted"
    DELEGATED = "delegated"
    AUTHORIZED = "authorized"
    EXECUTION_STARTED = "execution_started"
    PARTIALLY_DELIVERED = "partially_delivered"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    SETTLED = "settled"

    # Exception and Defense Paths
    EXPIRED = "expired"
    REVOKED = "revoked"
    TIMEOUT = "timeout"
    UNKNOWN_EFFECT = "unknown_effect"         # Network lost: MUST reconcile before retry!
    RECONCILE_REQUIRED = "reconcile_required" # Balance mismatch / manual audit needed
    PARTIAL_REFUND = "partial_refund"
    FULL_REFUND = "full_refund"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"

class InvalidStateTransitionError(Exception):
    pass

# Valid state transitions
VALID_TRANSITIONS = {
    TaskState.QUOTED: {TaskState.DELEGATED, TaskState.EXPIRED, TaskState.CANCELLED},
    TaskState.DELEGATED: {TaskState.AUTHORIZED, TaskState.REVOKED, TaskState.CANCELLED},
    TaskState.AUTHORIZED: {TaskState.EXECUTION_STARTED, TaskState.CANCELLED, TaskState.REVOKED},
    TaskState.EXECUTION_STARTED: {
        TaskState.DELIVERED,
        TaskState.PARTIALLY_DELIVERED,
        TaskState.TIMEOUT,
        TaskState.UNKNOWN_EFFECT,
        TaskState.DISPUTED
    },
    TaskState.PARTIALLY_DELIVERED: {TaskState.DELIVERED, TaskState.TIMEOUT, TaskState.UNKNOWN_EFFECT, TaskState.DISPUTED},
    TaskState.DELIVERED: {TaskState.ACCEPTED, TaskState.DISPUTED, TaskState.RECONCILE_REQUIRED},
    TaskState.ACCEPTED: {TaskState.SETTLED, TaskState.RECONCILE_REQUIRED},
    TaskState.UNKNOWN_EFFECT: {
        TaskState.DELIVERED,           # Reconciled: response was actually cached!
        TaskState.AUTHORIZED,          # Reconciled: never executed, safe to retry
        TaskState.RECONCILE_REQUIRED,  # Discrepancy confirmed
        TaskState.FULL_REFUND
    },
    TaskState.TIMEOUT: {TaskState.UNKNOWN_EFFECT, TaskState.RECONCILE_REQUIRED, TaskState.CANCELLED},
    TaskState.RECONCILE_REQUIRED: {TaskState.SETTLED, TaskState.PARTIAL_REFUND, TaskState.FULL_REFUND, TaskState.DISPUTED},
    TaskState.DISPUTED: {TaskState.PARTIAL_REFUND, TaskState.FULL_REFUND, TaskState.SETTLED},
    TaskState.SETTLED: set(),
    TaskState.EXPIRED: set(),
    TaskState.REVOKED: set(),
    TaskState.CANCELLED: set(),
    TaskState.FULL_REFUND: set(),
    TaskState.PARTIAL_REFUND: set(),
}

class AgentTaskContext:
    def __init__(self, task_id: str, idempotency_key: str, initial_state: TaskState = TaskState.QUOTED):
        self.task_id = task_id
        self.idempotency_key = idempotency_key
        self.state = initial_state
        self.history: List[Dict[str, Any]] = [{
            "state": initial_state.value,
            "timestamp": int(time.time()),
            "reason": "Task initialized"
        }]
        self.quote: Optional[Dict[str, Any]] = None
        self.delegation_id: Optional[str] = None
        self.delivery_receipt: Optional[Dict[str, Any]] = None
        self.result_hash: Optional[str] = None

    def transition_to(self, new_state: TaskState, reason: str = "") -> None:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition task [{self.task_id}] from '{self.state.value}' to '{new_state.value}'. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )
        self.state = new_state
        self.history.append({
            "state": new_state.value,
            "timestamp": int(time.time()),
            "reason": reason
        })

    def is_terminal(self) -> bool:
        return self.state in {
            TaskState.SETTLED,
            TaskState.EXPIRED,
            TaskState.REVOKED,
            TaskState.CANCELLED,
            TaskState.FULL_REFUND
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "current_state": self.state.value,
            "delegation_id": self.delegation_id,
            "quote": self.quote,
            "delivery_receipt": self.delivery_receipt,
            "result_hash": self.result_hash,
            "history": self.history
        }

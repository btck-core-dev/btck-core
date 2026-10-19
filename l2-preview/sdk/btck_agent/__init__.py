"""
BTC-K Python SDK (btck-agent)
-----------------------------
The official Python library for autonomous AI Agents to make and accept
Sats micropayments, manage stealth privacy wallets, enforce cryptographic
delegation budgets, and consume AI-RPC gateways with proof of delivery.
"""

from .wallet import BTCKWallet
from .ai_client import BTCKAgentClient
from .delegation import DelegationReceipt, EnforceableBudgetTracker, DelegationSecurityError, BudgetExceededError, UnauthorizedServiceError, DelegationExpiredError
from .state_machine import TaskState, AgentTaskContext
from .robot_wallet import RobotDelegatedWallet, HardwareEnclaveInterface

__version__ = "2.2.0"
__all__ = [
    "BTCKWallet",
    "BTCKAgentClient",
    "DelegationReceipt",
    "EnforceableBudgetTracker",
    "DelegationSecurityError",
    "BudgetExceededError",
    "UnauthorizedServiceError",
    "DelegationExpiredError",
    "TaskState",
    "AgentTaskContext",
    "RobotDelegatedWallet",
    "HardwareEnclaveInterface"
]

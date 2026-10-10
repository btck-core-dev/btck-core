"""
BTC-K Headless Economy Defensive Architecture Test Suite (Document 27)
-----------------------------------------------------------------------
Verifies:
1. Complete Zero-KYC Dual-Key Stealth Privacy (Monero / BIP-47)
2. Principle: Balance != Authorization (Signed DelegationReceipt + Hard Budget Caps)
3. Machine Service Cards & Structured Pre-execution Quoting
4. Idempotency & Replay Defense (Prevents double payment)
5. Cryptographic Proof of Delivery (result_hash + Gateway Schnorr Signature)
6. Task-Level Cost Ledger Auditability
7. Finite State Machine & unknown_effect Safe Reconciliation
8. Adversarial C-01 Defense (Forged Receipt Rejection)
9. Adversarial C-03 Defense (Production /v1/deposit 403 Forbidden)
10. Adversarial C-02 Defense (Hardware Enclave Key Non-Derivability)
11. Adversarial H-03 Defense (Robot Default-Deny & 3-Tier Budget Limits)
12. Adversarial H-04 Defense (Third-Party Cryptographic Receipt Verification)
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sdk")))

from gateway.ai_rpc_gateway import AIRPCGateway
from btck_agent import (
    BTCKWallet,
    BTCKAgentClient,
    DelegationReceipt,
    EnforceableBudgetTracker,
    BudgetExceededError,
    UnauthorizedServiceError,
    DelegationExpiredError,
    TaskState,
    RobotDelegatedWallet,
    HardwareEnclaveInterface
)
from privacy.stealth_address import StealthKeyPair, sha256
from privacy.wallet_engine import schnorr_verify

TEST_PORT = 8092
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def sync_get(url: str, headers: dict = None):
    h = {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8'))


def sync_post(url: str, data: dict, headers: dict = None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode('utf-8'))


class TestHeadlessEconomySafeguards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.gw = AIRPCGateway(
            host="127.0.0.1",
            port=TEST_PORT,
            allow_test_faucet=True,
            data_dir=self.temp_dir
        )
        await self.gw.start()
        await asyncio.sleep(0.3)

    async def asyncTearDown(self):
        await self.gw.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_all_safeguards(self):
        gw = self.gw

        # -------------------------------------------------------------
        # 1. Dual-Key Stealth Privacy Verification (Zero-KYC)
        # -------------------------------------------------------------
        human_principal_wallet = BTCKWallet.generate()
        agent_wallet = BTCKWallet.generate()

        self.assertTrue(human_principal_wallet.address.startswith("btck1s"))
        self.assertTrue(agent_wallet.address.startswith("btck1s"))
        
        # Verify one-time ephemeral output generation
        R_ephem, P_onetime = StealthKeyPair.generate_one_time_stealth_output(agent_wallet.address)
        self.assertEqual(len(R_ephem), 33)
        self.assertEqual(len(P_onetime), 33)
        # Recipient wallet recognizes this output
        self.assertTrue(agent_wallet.keypair.is_my_stealth_output(R_ephem, P_onetime))
        # Random third-party does NOT recognize this output
        stranger_wallet = BTCKWallet.generate()
        self.assertFalse(stranger_wallet.keypair.is_my_stealth_output(R_ephem, P_onetime))

        # -------------------------------------------------------------
        # 2. Machine Service Cards & Pre-execution Quoting (Doc 27 Section 5)
        # -------------------------------------------------------------
        client = BTCKAgentClient(wallet=agent_wallet, gateway_url=BASE_URL)
        
        cards = await asyncio.to_thread(client.get_service_cards)
        self.assertGreaterEqual(len(cards), 4)
        service_ids = [c["service_id"] for c in cards]
        self.assertIn("deepseek-r1.v1", service_ids)
        self.assertIn("code-audit.v1", service_ids)

        quote = await asyncio.to_thread(client.get_quote, "deepseek-r1", 800)
        self.assertEqual(quote["service_id"], "deepseek-r1")
        self.assertGreater(quote["amount_satk"], 0)
        self.assertGreaterEqual(quote["max_total_satk"], quote["amount_satk"])

        # -------------------------------------------------------------
        # 3. Principle: Balance != Authorization (Doc 27 Section 3)
        # -------------------------------------------------------------
        delegation = DelegationReceipt.issue(
            principal_keypair=human_principal_wallet.keypair,
            agent_id="agent_worker_77",
            max_amount_satk=2_000_000,
            allowed_services=["deepseek-r1", "deepseek-r1.v1"],
            duration_seconds=3600
        )
        self.assertTrue(delegation.verify())
        self.assertEqual(delegation.agent_id, "agent_worker_77")
        self.assertEqual(delegation.max_amount_satk, 2_000_000)

        # Anti-Tampering test
        tampered_delegation = DelegationReceipt.from_dict(delegation.to_dict())
        tampered_delegation.max_amount_satk = 9_999_999.0
        self.assertFalse(tampered_delegation.verify())

        # Bind delegation to agent client
        client.attach_delegation(delegation)
        with self.assertRaises(UnauthorizedServiceError):
            client.chat_completion(model="code-audit.v1", prompt="Audit this contract")

        # -------------------------------------------------------------
        # 4. HTTP 402 SatK Settlement & Cryptographic Proof of Delivery
        # -------------------------------------------------------------
        # Fund agent's on-chain balance via test faucet
        status, dep_res = await asyncio.to_thread(
            sync_post,
            f"{BASE_URL}/v1/deposit",
            {"agent_stealth_address": agent_wallet.address, "amount_satk": 1_000_000}
        )
        self.assertEqual(status, 200)
        self.assertEqual(dep_res["new_balance_satk"], 1_000_000)

        # Execute completion request
        idem_1 = f"idem_test_task_{int(time.time()*1000)}"
        res1 = await asyncio.to_thread(
            client.chat_completion,
            model="deepseek-r1",
            prompt="Compute the next prime number.",
            idempotency_key=idem_1
        )
        self.assertIn("choices", res1)
        charged_satk = res1["usage"]["cost_satk"]
        self.assertGreater(charged_satk, 0)
        self.assertEqual(res1["usage"]["price_source"], "bootstrap-fixed-v1")
        self.assertGreater(res1["usage"]["task_kcu"], 0)

        # Verify Proof of Delivery
        pod = res1["delivery_receipt"]
        self.assertTrue(pod["result_hash"].startswith("sha256:"))
        self.assertTrue(len(pod["signature_hex"]) > 0)

        # Check EnforceableBudgetTracker state
        budget = client.budget_tracker
        self.assertEqual(budget.total_spent_satk, charged_satk)
        self.assertEqual(budget.get_remaining_budget(), 2_000_000 - charged_satk)

        # -------------------------------------------------------------
        # 5. Idempotency & Replay Defense (Doc 27 Section 6)
        # -------------------------------------------------------------
        res1_replay = await asyncio.to_thread(
            client.chat_completion,
            model="deepseek-r1",
            prompt="Compute the next prime number.",
            idempotency_key=idem_1
        )
        self.assertEqual(res1_replay["choices"][0]["message"]["content"], res1["choices"][0]["message"]["content"])
        self.assertEqual(gw.ledger.get_balance(agent_wallet.address), 1_000_000 - charged_satk)

        # -------------------------------------------------------------
        # 6. Hard Spending Cap Enforcement (Budget Exhaustion)
        # -------------------------------------------------------------
        with self.assertRaises(BudgetExceededError):
            budget.check_authorization("deepseek-r1", 5_000_000)

        # -------------------------------------------------------------
        # 7. Task-Level Cost Ledger & Audit Query
        # -------------------------------------------------------------
        status_audit, audit_data = await asyncio.to_thread(
            sync_get,
            f"{BASE_URL}/v1/audit/tasks?agent_stealth_address={agent_wallet.address}"
        )
        self.assertEqual(status_audit, 200)
        self.assertGreaterEqual(audit_data["count"], 1)
        record = audit_data["records"][0]
        self.assertEqual(record["agent_stealth_address"], agent_wallet.address)
        self.assertEqual(record["status"], "settled")
        self.assertIn("result_hash", record)
        self.assertIn("receipt_hash", record)

        # -------------------------------------------------------------
        # 8. unknown_effect State & Recovery (Doc 27 Section 7)
        # -------------------------------------------------------------
        idem_recovery = f"idem_recovery_test_{int(time.time()*1000)}"
        reconciled = await asyncio.to_thread(client.reconcile_task, idem_recovery)
        self.assertIsNone(reconciled)

        # Execute once
        await asyncio.to_thread(
            client.chat_completion,
            model="deepseek-r1",
            prompt="Test network timeout resilience.",
            idempotency_key=idem_recovery
        )

        reconciled_after = await asyncio.to_thread(client.reconcile_task, idem_recovery)
        self.assertIsNotNone(reconciled_after)
        self.assertIn("choices", reconciled_after)

        # -------------------------------------------------------------
        # 9. Adversarial C-01 Remediation: Rejection of Forged x402 Payment Receipts
        # -------------------------------------------------------------
        attacker_wallet = BTCKWallet.generate()
        fake_receipt = {
            "invoice_id": "inv_fake_nonexistent_999",
            "amount_satk": 1000,
            "payer_stealth_address": attacker_wallet.address,
            "status": "PAID"
        }
        status_fake, err_fake = await asyncio.to_thread(
            sync_post,
            f"{BASE_URL}/v1/chat/completions",
            {"model": "deepseek-r1", "messages": [{"role": "user", "content": "Free service exploit attempt"}]},
            {
                "X-Agent-Stealth-Address": attacker_wallet.address,
                "Authorization": f"BTCK-SatK {json.dumps(fake_receipt)}"
            }
        )
        self.assertEqual(status_fake, 402)
        self.assertIn("payment_verification_failed", json.dumps(err_fake))
        self.assertEqual(gw.ledger.get_balance(attacker_wallet.address), 0)

        # -------------------------------------------------------------
        # 10. Adversarial C-03 Remediation: Production Direct /v1/deposit Blocking
        # -------------------------------------------------------------
        prod_port = TEST_PORT + 1
        prod_temp = tempfile.mkdtemp()
        try:
            gw_prod = AIRPCGateway(host="127.0.0.1", port=prod_port, allow_test_faucet=False, data_dir=prod_temp)
            await gw_prod.start()
            await asyncio.sleep(0.2)
            try:
                status_dep, err_dep = await asyncio.to_thread(
                    sync_post,
                    f"http://127.0.0.1:{prod_port}/v1/deposit",
                    {"agent_stealth_address": attacker_wallet.address, "amount_satk": 50000}
                )
                self.assertEqual(status_dep, 403)
                self.assertIn("Direct deposit is disabled in production", json.dumps(err_dep))
                self.assertEqual(gw_prod.ledger.get_balance(attacker_wallet.address), 0)
            finally:
                await gw_prod.stop()
        finally:
            shutil.rmtree(prod_temp, ignore_errors=True)

        # -------------------------------------------------------------
        # 11. Adversarial C-02 Remediation: Hardware Enclave Key Non-Derivability
        # -------------------------------------------------------------
        robot1_id = "unitree_b2_industrial_001"
        robot_wallet1 = RobotDelegatedWallet(
            robot_id=robot1_id,
            master_address=human_principal_wallet.address,
            machine_index=1
        )
        robot_wallet2 = RobotDelegatedWallet(
            robot_id=robot1_id,
            master_address=human_principal_wallet.address,
            machine_index=1
        )
        self.assertNotEqual(robot_wallet1.address, robot_wallet2.address)
        self.assertNotEqual(robot_wallet1.spend_pub_bytes, robot_wallet2.spend_pub_bytes)
        self.assertFalse(hasattr(robot_wallet1, "spend_priv"))

        # -------------------------------------------------------------
        # 12. Adversarial H-03 Remediation: Robot Wallet Default-Deny & 3-Tier Budgets
        # -------------------------------------------------------------
        robot_wallet = RobotDelegatedWallet(
            robot_id="optimus_factory_bot_042",
            master_address=human_principal_wallet.address,
            machine_index=5
        )
        merchant_wallet = BTCKWallet.generate()

        with self.assertRaises(PermissionError):
            robot_wallet.pay_physical_service("ev_charging", merchant_wallet.address, 10)

        robot_delegation = DelegationReceipt.issue(
            principal_keypair=human_principal_wallet.keypair,
            agent_id="optimus_factory_bot_042",
            max_amount_satk=100,
            allowed_services=["ev_charging", "automated_toll"],
            duration_seconds=86400
        )
        robot_wallet.bind_master_policy(robot_delegation, single_tx_limit_satk=20)
        self.assertTrue(robot_wallet.get_status()["has_active_delegation"])

        with self.assertRaises(ValueError):
            robot_wallet.pay_physical_service("ev_charging", merchant_wallet.address, -50)

        with self.assertRaises(TypeError):
            robot_wallet.pay_physical_service("ev_charging", merchant_wallet.address, 12.5)

        with self.assertRaises(OverflowError):
            robot_wallet.pay_physical_service("ev_charging", merchant_wallet.address, 25)

        # -------------------------------------------------------------
        # 13. Third-Party Verifiable Receipt (H-04 Remediation)
        # -------------------------------------------------------------
        receipt = robot_wallet.pay_physical_service("ev_charging", merchant_wallet.address, 15)
        self.assertEqual(receipt["status"], "SIGNED_PREPARED")
        self.assertEqual(receipt["amount_satk"], 15)
        self.assertIn("schnorr_signature", receipt)
        self.assertIn("payer_spend_pub_hex", receipt)

        # Independent 3rd-party verification by merchant
        pubkey_bytes = bytes.fromhex(receipt["payer_spend_pub_hex"])
        sig_bytes = bytes.fromhex(receipt["schnorr_signature"])
        canonical_str = (
            f"BTCK_ROBOT_PAYMENT_V1:{receipt['payer_stealth_address']}:{receipt['recipient_stealth_address']}:"
            f"{receipt['amount_satk']}:{receipt['service_id']}:{receipt['payment_nonce']}:{receipt['timestamp']}:"
            f"{receipt['ephemeral_pubkey']}:{receipt['recipient_one_time_output']}"
        )
        tx_digest = sha256(canonical_str.encode('utf-8'))
        is_valid_receipt = schnorr_verify(pubkey_bytes, tx_digest, sig_bytes)
        self.assertTrue(is_valid_receipt)


if __name__ == "__main__":
    unittest.main()

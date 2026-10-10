"""
Unit & Integration Tests: Sandbox Faucet Voucher Safeguards
-----------------------------------------------------------
Verifies all anti-sybil and economic protection rules for the AI-RPC Gateway:
1. Claim grant quota (exactly 2,500 SatK) and 14-day expiry TTL.
2. 24-hour cooldown enforcement per agent and per public IP.
3. Strict asset isolation (non-transferable and non-withdrawable).
4. Priority deduction: vouchers are consumed prior to paid balances.
5. Automatic expiration and burning of expired vouchers.
6. Anti-sybil trial rate limiting (5 requests per minute) returning HTTP 429.
7. Anti-sybil trial concurrency limiting (max 1 concurrent request) returning HTTP 429.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import urllib.request
import urllib.error
import unittest
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_TEST_DIR)
sys.path.insert(0, os.path.join(_ROOT_DIR, "src"))
sys.path.insert(0, os.path.join(_ROOT_DIR, "sdk"))

from gateway.ai_rpc_gateway import AIRPCGateway, AgentLedger, FAUCET_VOUCHER_GRANT_SATK, FAUCET_VOUCHER_TTL_SECONDS
from btck_agent import BTCKWallet


def sync_http_get(url: str, headers: dict = None):
    h = {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def sync_http_post(url: str, data: dict, headers: dict = None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


class TestAgentLedgerVouchers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_file = os.path.join(self.temp_dir, "test_ledger.json")
        self.ledger = AgentLedger(persist_path=self.ledger_file)
        self.wallet_a = BTCKWallet.generate()
        self.wallet_b = BTCKWallet.generate()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_claim_voucher_success(self):
        """Test valid voucher claim receives 2,500 SatK with 14-day expiry."""
        ok, msg, voucher = self.ledger.claim_faucet_voucher(
            self.wallet_a.address,
            client_ip="198.51.100.1",
            github_user="dev_alice"
        )
        self.assertTrue(ok)
        self.assertEqual(voucher["grant_satk"], 2500)
        self.assertEqual(voucher["remaining_satk"], 2500)
        self.assertEqual(voucher["expires_in_days"], 14)
        self.assertFalse(voucher["is_transferable"])
        self.assertFalse(voucher["is_withdrawable"])
        self.assertEqual(voucher["status"], "ACTIVE")

        # Balance check
        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 2500)
        self.assertEqual(self.ledger.get_balance(self.wallet_a.address), 2500)
        self.assertTrue(self.ledger.is_voucher_only(self.wallet_a.address))

    def test_claim_voucher_invalid_address(self):
        """Test claim rejection on invalid address format."""
        ok, msg, _ = self.ledger.claim_faucet_voucher("0xNonBtckAddress12345")
        self.assertFalse(ok)
        self.assertIn("Invalid agent address", msg)

    def test_claim_cooldown_agent(self):
        """Test 24h cooldown prevents same agent from claiming repeatedly."""
        ok1, _, _ = self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="127.0.0.1")
        self.assertTrue(ok1)

        ok2, msg2, _ = self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="127.0.0.1")
        self.assertFalse(ok2)
        self.assertIn("Agent cooldown active", msg2)

    def test_claim_cooldown_ip(self):
        """Test 24h cooldown prevents different agents on the same public IP from sybil claiming."""
        ok1, _, _ = self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="203.0.113.50")
        self.assertTrue(ok1)

        # Different wallet, identical IP
        ok2, msg2, _ = self.ledger.claim_faucet_voucher(self.wallet_b.address, client_ip="203.0.113.50")
        self.assertFalse(ok2)
        self.assertIn("IP cooldown active", msg2)

    def test_voucher_priority_deduction(self):
        """Test that deductions consume voucher credits first before touching paid balance."""
        # Deposit 1,000 paid SatK
        self.ledger.deposit(self.wallet_a.address, 1000)
        # Claim 2,500 voucher SatK
        self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="127.0.0.1")

        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 2500)
        self.assertEqual(self.ledger.balances[self.wallet_a.address], 1000)
        self.assertEqual(self.ledger.get_balance(self.wallet_a.address), 3500)
        self.assertFalse(self.ledger.is_voucher_only(self.wallet_a.address))

        # First deduction: 500 SatK -> voucher should absorb all 500
        ok = self.ledger.deduct(self.wallet_a.address, 500)
        self.assertTrue(ok)
        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 2000)
        self.assertEqual(self.ledger.balances[self.wallet_a.address], 1000)

        # Second deduction: 2,200 SatK -> exhausts remaining 2,000 voucher, 200 from paid
        ok = self.ledger.deduct(self.wallet_a.address, 2200)
        self.assertTrue(ok)
        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 0)
        self.assertEqual(self.ledger.balances[self.wallet_a.address], 800)
        self.assertEqual(self.ledger.get_balance(self.wallet_a.address), 800)

    def test_voucher_non_transferable(self):
        """Test that voucher trial credits can never be transferred or withdrawn."""
        self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="127.0.0.1")
        self.assertEqual(self.ledger.get_balance(self.wallet_a.address), 2500)

        # Attempt to transfer voucher credits
        ok, msg = self.ledger.transfer(self.wallet_a.address, self.wallet_b.address, 500)
        self.assertFalse(ok)
        self.assertIn("non-transferable", msg)
        self.assertEqual(self.ledger.get_balance(self.wallet_b.address), 0)

        # Legitimate paid balance transfer works normally
        self.ledger.deposit(self.wallet_a.address, 300)
        ok2, msg2 = self.ledger.transfer(self.wallet_a.address, self.wallet_b.address, 300)
        self.assertTrue(ok2)
        self.assertEqual(self.ledger.get_balance(self.wallet_b.address), 300)

        # Attempt to transfer more than paid balance fails
        ok3, _ = self.ledger.transfer(self.wallet_a.address, self.wallet_b.address, 1)
        self.assertFalse(ok3)

    def test_voucher_auto_expiry_and_burning(self):
        """Test that vouchers automatically expire and burn after 14 days."""
        self.ledger.claim_faucet_voucher(self.wallet_a.address, client_ip="127.0.0.1")
        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 2500)

        # Simulate 14 days passing by rewinding expires_at
        self.ledger.vouchers[self.wallet_a.address]["expires_at"] = time.time() - 10

        # Query balance triggers burn
        self.assertEqual(self.ledger.get_voucher_balance(self.wallet_a.address), 0)
        self.assertEqual(self.ledger.get_balance(self.wallet_a.address), 0)
        self.assertEqual(self.ledger.vouchers[self.wallet_a.address]["status"], "EXPIRED")

        # Deduct must fail
        ok = self.ledger.deduct(self.wallet_a.address, 10)
        self.assertFalse(ok)


class TestAIRPCGatewayFaucetIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.port = 8098
        self.gw = AIRPCGateway(
            host="127.0.0.1",
            port=self.port,
            allow_test_faucet=False,
            data_dir=self.temp_dir,
            # A deliberately high sandbox reference keeps the fixed 2,500-SatK
            # voucher usable for repeated rate-limit tests. It is not a market
            # price and this gateway never performs real-value settlement.
            btck_reference_price_usd=1000.0,
            price_source="sandbox-faucet-test",
        )
        await self.gw.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.wallet = BTCKWallet.generate()

    async def asyncTearDown(self):
        await self.gw.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_faucet_claim_status_and_transfer_rejection(self):
        """Test /v1/faucet/claim, /v1/faucet/status, and transfer restriction."""
        # 1. Status before claim
        code, status_data = await asyncio.to_thread(
            sync_http_get,
            f"{self.base_url}/v1/faucet/status?agent={self.wallet.address}"
        )
        self.assertEqual(code, 200)
        self.assertFalse(status_data["has_voucher"])

        # 2. Claim voucher
        code, claim_data = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/faucet/claim",
            {"agent_stealth_address": self.wallet.address, "github_user": "tester"}
        )
        self.assertEqual(code, 200)
        self.assertTrue(claim_data["success"])
        self.assertEqual(claim_data["available_balance_satk"], 2500)

        # 3. Status after claim
        code, status_data = await asyncio.to_thread(
            sync_http_get,
            f"{self.base_url}/v1/faucet/status?agent={self.wallet.address}"
        )
        self.assertEqual(code, 200)
        self.assertTrue(status_data["has_voucher"])
        self.assertEqual(status_data["voucher_balance_satk"], 2500)
        self.assertGreater(status_data["expires_in_hours"], 330)

        # 4. Attempt transfer -> must return 403 Forbidden
        other_wallet = BTCKWallet.generate()
        code, xfer_data = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/transfer",
            {"from_agent": self.wallet.address, "to_agent": other_wallet.address, "amount_satk": 500}
        )
        self.assertEqual(code, 403)
        self.assertFalse(xfer_data["success"])

    async def test_trial_rate_limiting_and_concurrency(self):
        """Test that trial voucher enforces 5 RPM rate limit and concurrency=1."""
        # Claim voucher
        code, _ = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/faucet/claim",
            {"agent_stealth_address": self.wallet.address}
        )
        self.assertEqual(code, 200)

        headers = {"X-Agent-Stealth-Address": self.wallet.address}
        req_body = {
            "model": "deepseek-r1",
            "messages": [{"role": "user", "content": "Ping"}]
        }

        # 1. Execute 5 rapid requests (within 5 RPM limit)
        for i in range(5):
            code, resp = await asyncio.to_thread(
                sync_http_post,
                f"{self.base_url}/v1/chat/completions",
                req_body,
                headers
            )
            self.assertEqual(code, 200, f"Request {i+1} failed with code {code}")

        # 2. 6th request within 1 minute must trigger HTTP 429 trial_rate_limit_exceeded
        code, resp = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/chat/completions",
            req_body,
            headers
        )
        self.assertEqual(code, 429)
        self.assertEqual(resp["error"]["type"], "trial_rate_limit_exceeded")

        # 3. Concurrency check: simulate an active in-flight request
        other_wallet = BTCKWallet.generate()
        code, _ = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/faucet/claim",
            {"agent_stealth_address": other_wallet.address}
        )
        self.assertEqual(code, 200)

        # Artificially set active concurrency to 1
        self.gw.trial_active_concurrency[other_wallet.address] = 1

        code, resp = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/chat/completions",
            req_body,
            {"X-Agent-Stealth-Address": other_wallet.address}
        )
        self.assertEqual(code, 429)
        self.assertEqual(resp["error"]["type"], "trial_concurrency_exceeded")

        # Clear concurrency and verify next call succeeds
        self.gw.trial_active_concurrency[other_wallet.address] = 0
        code, resp = await asyncio.to_thread(
            sync_http_post,
            f"{self.base_url}/v1/chat/completions",
            req_body,
            {"X-Agent-Stealth-Address": other_wallet.address}
        )
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()

"""
Integration Test: Standalone AI-RPC Gateway Server (HTTP 402)
-------------------------------------------------------------
Verifies:
1. /health and /v1/models endpoints
2. HTTP 402 Payment Required response when unfunded
3. SatK micro-settlement and token deduction
4. Integration with btck-agent Python SDK client
"""

import asyncio
import json
import os
import shutil
import tempfile
import urllib.request
import urllib.error
import unittest
import sys

sys.path.insert(0, '/Users/a1234/btck-core/src')
sys.path.insert(0, '/Users/a1234/btck-core/sdk')

from gateway.ai_rpc_gateway import AIRPCGateway
from btck_agent import BTCKWallet, BTCKAgentClient


def sync_http_get(url: str):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode())


def sync_http_post(url: str, data: dict, headers: dict = None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=h)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestLiveAIRPCServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_live_gateway(self):
        test_port = 8094
        gw = AIRPCGateway(host="127.0.0.1", port=test_port, allow_test_faucet=True, data_dir=self.temp_dir)
        await gw.start()
        base_url = f"http://127.0.0.1:{test_port}"

        try:
            # 1. /health and /v1/models
            h = await asyncio.to_thread(sync_http_get, f"{base_url}/health")
            self.assertEqual(h["status"], "ok")

            m = await asyncio.to_thread(sync_http_get, f"{base_url}/v1/models")
            self.assertGreaterEqual(len(m["data"]), 3)

            # 2. HTTP 402 Payment Required
            wallet = BTCKWallet.generate()
            
            def try_unfunded():
                req = urllib.request.Request(
                    f"{base_url}/v1/chat/completions",
                    data=json.dumps({"model": "deepseek-r1", "messages": [{"role": "user", "content": "Explain quantum computing."}]}).encode(),
                    headers={"Content-Type": "application/json", "X-Agent-Stealth-Address": wallet.address}
                )
                try:
                    urllib.request.urlopen(req)
                    return 200, {}
                except urllib.error.HTTPError as err:
                    return err.code, json.loads(err.read().decode())

            code, err_body = await asyncio.to_thread(try_unfunded)
            self.assertEqual(code, 402)
            invoice = err_body["error"]["invoice"]
            self.assertIn("invoice_id", invoice)

            # 3. SatK Deposit & Model Invocation
            status, d = await asyncio.to_thread(
                sync_http_post,
                f"{base_url}/v1/deposit",
                {"agent_stealth_address": wallet.address, "amount_satk": 100}
            )
            self.assertEqual(status, 200)
            self.assertEqual(d["new_balance_satk"], 100)

            # Make completion request with funded balance
            status, comp = await asyncio.to_thread(
                sync_http_post,
                f"{base_url}/v1/chat/completions",
                {"model": "deepseek-r1", "messages": [{"role": "user", "content": "Hello AI!"}]},
                {"X-Agent-Stealth-Address": wallet.address}
            )
            self.assertEqual(status, 200)
            self.assertGreater(comp["usage"]["cost_satk"], 0)
            self.assertLess(comp["usage"]["remaining_balance_satk"], 100)

            # 4. End-to-End Client with btck-agent SDK
            client = BTCKAgentClient(wallet=wallet, gateway_url=base_url)
            res_sdk = await asyncio.to_thread(
                client.chat_completion,
                "deepseek-r1",
                "What is the mission of BTCK?"
            )
            self.assertIn("choices", res_sdk)
            self.assertGreater(len(res_sdk["choices"]), 0)
        finally:
            await gw.stop()


if __name__ == "__main__":
    unittest.main()

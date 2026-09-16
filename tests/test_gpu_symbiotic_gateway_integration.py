"""
BTC-K End-to-End Symbiotic GPU Grid & AI-RPC Gateway Integration Test
Validates the complete symbiotic lifecycle:
1. Symbiotic worker registers hardware profile and stealth payout address with gateway.
2. Worker performs baseline Pearl mining loop.
3. User / Agent submits x402 chat completion.
4. Gateway routes task to worker; worker preempts mining, executes inference, and resumes.
5. SatK payment settles: 85% paid to worker stealth address, proof-of-delivery signed.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request
import urllib.error

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../sdk")))

from gateway.ai_rpc_gateway import AIRPCGateway
from privacy.stealth_address import StealthKeyPair
from worker.process_preemptor import ProcessPreemptor, MinerState
from worker.tui_console import TUIConsole


def sync_http_get(url: str):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def sync_http_post(url: str, payload: dict, headers: dict = None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestGPUSymbioticGatewayIntegration(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.port = 8199
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.gw = AIRPCGateway(
            host=self.host,
            port=self.port,
            allow_test_faucet=True,
            data_dir=self.temp_dir
        )
        await self.gw.start()

    async def asyncTearDown(self):
        await self.gw.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_worker_registration_and_cluster_status(self):
        """Test worker registration endpoint and cluster aggregation."""
        # 1. Initial check: 0 workers
        status, res = await asyncio.to_thread(sync_http_get, f"{self.base_url}/v1/workers")
        self.assertEqual(status, 200)
        self.assertEqual(res["total_registered"], 0)

        # 2. Register a 4070 Ti Super Symbiotic Worker
        worker_payload = {
            "worker_id": "worker-4070ti-rig-alpha",
            "worker_address": "btck1sqqsymbioticmineraddressforpayouts1234567890",
            "tier_level": 1,
            "model_name": "deepseek-r1",
            "supported_models": ["deepseek-r1", "deepseek-r1-distill-qwen-8b"],
            "vram_gb": 16.0,
            "pearl_hashrate": "549.3 TH/s"
        }
        status, reg_res = await asyncio.to_thread(sync_http_post, f"{self.base_url}/v1/workers/register", worker_payload)
        self.assertEqual(status, 200)
        self.assertTrue(reg_res["success"])
        self.assertEqual(reg_res["worker"]["tier_level"], 1)

        # 3. Check cluster status reflects the new worker
        status, res2 = await asyncio.to_thread(sync_http_get, f"{self.base_url}/v1/workers")
        self.assertEqual(status, 200)
        self.assertEqual(res2["total_registered"], 1)
        self.assertEqual(res2["active_online"], 1)
        self.assertEqual(res2["cluster_vram_gb"], 16.0)

    async def test_end_to_end_inference_and_worker_payout(self):
        """
        Test end-to-end flow:
        User deposits SatK -> asks question -> Worker polls & executes -> Worker receives 85% SatK!
        """
        worker_addr = "btck1sqqsymbioticworkeraddress998877665544332211"
        worker_id = "worker-symbiotic-node-42"

        # 1. Register Worker
        reg_payload = {
            "worker_id": worker_id,
            "worker_address": worker_addr,
            "tier_level": 1,
            "model_name": "deepseek-r1",
            "supported_models": ["deepseek-r1"],
            "vram_gb": 16.0,
            "pearl_hashrate": "549.3 TH/s"
        }
        status, _ = await asyncio.to_thread(sync_http_post, f"{self.base_url}/v1/workers/register", reg_payload)
        self.assertEqual(status, 200)

        # 2. Setup Client Agent with 100 SatK
        client_addr = "btck1sqqclientagentstealthaddress1122334455"
        await asyncio.to_thread(sync_http_post, f"{self.base_url}/v1/deposit", {"agent_stealth_address": client_addr, "amount_satk": 100})
        self.assertEqual(self.gw.ledger.get_balance(client_addr), 100)
        self.assertEqual(self.gw.ledger.get_balance(worker_addr), 0)

        # 3. Spawn background worker polling daemon
        worker_stop_event = asyncio.Event()

        async def worker_loop():
            while not worker_stop_event.is_set():
                try:
                    # Non-blocking poll
                    status, hb_res = await asyncio.to_thread(
                        sync_http_post,
                        f"{self.base_url}/v1/workers/heartbeat",
                        {
                            "worker_id": worker_id,
                            "pearl_hashrate": "549.5 TH/s",
                            "status": "MINING_PEARL"
                        }
                    )
                    pending = hb_res.get("pending_task")
                    if pending:
                        # Simulated Preemption & Inference
                        preemptor = ProcessPreemptor()
                        preemptor.pause()
                        
                        output_text = f"[BTC-K Symbiotic AI Result on RTX 4070 Ti] Answer to prompt: '{pending.get('prompt')}'"
                        await asyncio.sleep(0.05)
                        
                        preemptor.resume()

                        # Submit completion
                        await asyncio.to_thread(
                            sync_http_post,
                            f"{self.base_url}/v1/workers/complete",
                            {
                                "worker_id": worker_id,
                                "task_id": pending["task_id"],
                                "output_text": output_text
                            }
                        )
                except Exception:
                    pass
                await asyncio.sleep(0.05)

        poller_task = asyncio.create_task(worker_loop())

        try:
            # 4. Client Agent requests completion via /v1/chat/completions
            chat_payload = {
                "model": "deepseek-r1",
                "messages": [
                    {"role": "user", "content": "How does BTC-K achieve zero-cost Pearl mining symbiosis?"}
                ]
            }
            chat_headers = {
                "x-agent-stealth-address": client_addr,
                "idempotency-key": "test_idem_symbiotic_001"
            }

            status, chat_res = await asyncio.to_thread(
                sync_http_post,
                f"{self.base_url}/v1/chat/completions",
                chat_payload,
                chat_headers
            )
            self.assertEqual(status, 200)
            self.assertIn("choices", chat_res)
            content = chat_res["choices"][0]["message"]["content"]
            self.assertIn("BTC-K Symbiotic AI Result on RTX 4070 Ti", content)

            # 5. Verify Cryptographic Proof of Delivery
            self.assertIn("delivery_receipt", chat_res)
            self.assertTrue(chat_res["delivery_receipt"]["signature_hex"])

            # 6. Verify Economic SatK Ledger Settlement
            client_bal = self.gw.ledger.get_balance(client_addr)
            worker_bal = self.gw.ledger.get_balance(worker_addr)

            # Client balance should have decreased
            self.assertLess(client_bal, 100)
            spent = 100 - client_bal

            # Worker stealth address must have received exactly 85% of spent SatK!
            expected_payout = max(1, int(spent * 0.85))
            self.assertEqual(worker_bal, expected_payout)
            self.assertGreater(worker_bal, 0)

            # Worker registered stats
            worker_info = self.gw.registered_workers[worker_id]
            self.assertEqual(worker_info["tasks_completed"], 1)
            self.assertEqual(worker_info["satk_earned"], expected_payout)

        finally:
            worker_stop_event.set()
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    unittest.main()

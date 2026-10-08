"""
BTC-K End-to-End Symbiotic GPU Grid & AI-RPC Gateway Integration Test
Validates the complete symbiotic lifecycle:
1. Symbiotic worker registers hardware profile and stealth payout address with gateway.
2. Worker performs baseline Pearl mining loop.
3. User / Agent submits x402 chat completion.
4. Gateway routes task to worker; worker preempts mining, executes inference, and resumes.
5. SatK payment settles under the v2.4 five-way schedule with a signed receipt.
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
from privacy.wallet_engine import schnorr_sign
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


def sync_http_post_with_error(url: str, payload: dict, headers: dict = None):
    try:
        return sync_http_post(url, payload, headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


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
            "worker_address": StealthKeyPair().get_public_stealth_address(),
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

    async def test_only_assigned_worker_can_complete_task(self):
        for worker_id in ("worker-owner", "worker-attacker"):
            worker_address = StealthKeyPair().get_public_stealth_address()
            status, _ = await asyncio.to_thread(sync_http_post, f"{self.base_url}/v1/workers/register", {
                "worker_id": worker_id,
                "worker_address": worker_address,
                "model_name": "deepseek-r1",
            })
            self.assertEqual(status, 200)

        dispatch = asyncio.create_task(self.gw._dispatch_task_to_worker(
            "worker-owner", {"task_id": "bound-task", "prompt": "x", "model": "deepseek-r1"}, timeout_sec=1
        ))
        await asyncio.sleep(0)
        status, _ = await asyncio.to_thread(sync_http_post_with_error, f"{self.base_url}/v1/workers/complete", {
            "worker_id": "worker-attacker",
            "task_id": "bound-task",
            "output_text": "forged",
        })
        self.assertEqual(status, 403)
        status, _ = await asyncio.to_thread(sync_http_post, f"{self.base_url}/v1/workers/complete", {
            "worker_id": "worker-owner",
            "task_id": "bound-task",
            "output_text": "authentic",
        })
        self.assertEqual(status, 200)
        self.assertEqual((await dispatch)["output_text"], "authentic")

    async def test_production_does_not_charge_without_real_gpu_result(self):
        prod_dir = tempfile.mkdtemp()
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, prod_dir, True)
        prod = AIRPCGateway(host=self.host, port=8200, allow_test_faucet=False, data_dir=prod_dir)
        await prod.start()
        try:
            payer = "btck1s-production-agent"
            prod.ledger.deposit(payer, 100)
            status, body = await asyncio.to_thread(sync_http_post_with_error, "http://127.0.0.1:8200/v1/chat/completions", {
                "model": "deepseek-r1",
                "messages": [{"role": "user", "content": "must use a real provider"}],
            }, {"x-agent-stealth-address": payer})
            self.assertEqual(status, 503)
            self.assertIn("gpu_provider_unavailable", json.dumps(body))
            self.assertEqual(prod.ledger.get_balance(payer), 100)
        finally:
            await prod.stop()

    async def test_production_worker_registration_and_result_are_signed(self):
        prod_dir = tempfile.mkdtemp()
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, prod_dir, True)
        prod = AIRPCGateway(host=self.host, port=8201, allow_test_faucet=False, data_dir=prod_dir)
        await prod.start()
        worker_keys = StealthKeyPair()
        worker_id = "signed-worker"
        registration = {
            "worker_id": worker_id,
            "worker_address": worker_keys.get_public_stealth_address(),
            "worker_spend_pub_hex": worker_keys.spend_pub_bytes.hex(),
            "tier_level": 1,
            "model_name": "deepseek-r1",
            "supported_models": ["deepseek-r1"],
            "vram_gb": 16.0,
            "registration_timestamp": int(time.time()),
        }
        try:
            rejected, _, _ = prod.register_worker(dict(registration))
            self.assertFalse(rejected)
            registration["registration_signature"] = schnorr_sign(
                worker_keys.spend_priv,
                prod.worker_registration_digest(registration),
            ).hex()
            accepted, _, _ = prod.register_worker(registration)
            self.assertTrue(accepted)

            heartbeat = {
                "worker_id": worker_id,
                "pearl_hashrate": "549.5 TH/s",
                "status": "MINING_PEARL",
                "heartbeat_timestamp": int(time.time()),
            }
            rejected_status, _ = await asyncio.to_thread(
                sync_http_post_with_error,
                "http://127.0.0.1:8201/v1/workers/heartbeat",
                heartbeat,
            )
            self.assertEqual(rejected_status, 403)
            heartbeat["heartbeat_signature"] = schnorr_sign(
                worker_keys.spend_priv,
                prod.worker_heartbeat_digest(heartbeat),
            ).hex()
            accepted_status, _ = await asyncio.to_thread(
                sync_http_post,
                "http://127.0.0.1:8201/v1/workers/heartbeat",
                heartbeat,
            )
            self.assertEqual(accepted_status, 200)

            dispatch = asyncio.create_task(prod._dispatch_task_to_worker(
                worker_id,
                {"task_id": "signed-task", "prompt": "x", "model": "deepseek-r1"},
                timeout_sec=1,
            ))
            task = await prod.worker_queues[worker_id].get()
            output = "signed provider result"
            signature = schnorr_sign(
                worker_keys.spend_priv,
                prod.worker_result_digest("signed-task", task["challenge_nonce"], output),
            ).hex()
            status, _ = await asyncio.to_thread(sync_http_post, "http://127.0.0.1:8201/v1/workers/complete", {
                "worker_id": worker_id,
                "task_id": "signed-task",
                "output_text": output,
                "provider_signature": signature,
            })
            self.assertEqual(status, 200)
            result = await dispatch
            self.assertEqual(result["output_text"], output)
            self.assertEqual(result["provider_signature"], signature)
        finally:
            await prod.stop()

    async def test_end_to_end_inference_and_worker_payout(self):
        """
        Test end-to-end flow:
        User deposits SatK -> asks question -> Worker polls & executes -> v2.4 settlement.
        """
        worker_addr = StealthKeyPair().get_public_stealth_address()
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

            settlement = chat_res["usage"]["l2_settlement"]
            self.assertEqual(settlement["network_year"], 1)
            self.assertEqual(settlement["charged_satk"], spent)
            self.assertEqual(settlement["paid_satk"], spent)
            self.assertEqual(settlement["sandbox_voucher_satk"], 0)
            self.assertEqual(settlement["conserved_satk"], spent)
            self.assertEqual(
                settlement["provider_satk"]
                + settlement["standby_satk"]
                + settlement["combo_satk"]
                + settlement["burn_satk"]
                + settlement["ecosystem_satk"],
                spent,
            )
            self.assertEqual(worker_bal, settlement["provider_satk"])
            self.assertEqual(
                self.gw.ledger.get_balance(self.gw.l2_standby_account),
                settlement["standby_satk"],
            )
            self.assertEqual(
                self.gw.ledger.get_balance(self.gw.l2_combo_account),
                settlement["combo_satk"],
            )
            self.assertEqual(
                self.gw.ledger.get_balance(self.gw.l2_ecosystem_account),
                settlement["ecosystem_satk"],
            )
            self.assertEqual(self.gw.ledger.l2_burned_satk, settlement["burn_satk"])
            self.assertGreater(worker_bal, 0)

            # Worker registered stats
            worker_info = self.gw.registered_workers[worker_id]
            self.assertEqual(worker_info["tasks_completed"], 1)
            self.assertEqual(worker_info["satk_earned"], settlement["provider_satk"])

        finally:
            worker_stop_event.set()
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    unittest.main()

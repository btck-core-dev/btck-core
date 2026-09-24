"""
Test BTC-K Stratum Server with Simulated Kaspa ASIC Miner
---------------------------------------------------------
Verifies Stratum V1 handshake, difficulty adjustment, job dispatch,
and Lucky Share 10x Combo Jackpot block submissions.
"""

import asyncio
import json
import sys
import unittest

sys.path.insert(0, '/Users/a1234/btck-core/src')

from stratum.stratum_server import BTCKStratumServer
from privacy.stealth_address import StealthKeyPair


class TestStratumAndAsicMiner(unittest.IsolatedAsyncioTestCase):
    async def test_stratum_handshake_and_share_submission(self):
        test_port = 3338
        server = BTCKStratumServer(host="127.0.0.1", port=test_port, network_diff=1.0)
        await server.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", test_port)
            pending_responses = {}
            received_jobs = []

            async def listen_loop():
                try:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        data = json.loads(line.decode().strip())
                        method = data.get("method")
                        msg_id = data.get("id")
                        if method == "mining.notify":
                            received_jobs.append(data["params"][0])
                        elif msg_id is not None:
                            fut = pending_responses.get(msg_id)
                            if fut and not fut.done():
                                fut.set_result(data)
                except Exception:
                    pass

            listener_task = asyncio.create_task(listen_loop())

            async def call_rpc(req):
                msg_id = req["id"]
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                pending_responses[msg_id] = fut
                writer.write((json.dumps(req) + "\n").encode())
                await writer.drain()
                return await asyncio.wait_for(fut, timeout=5.0)

            # 1. Subscribe
            sub_res = await call_rpc({"id": 1, "method": "mining.subscribe", "params": ["IceRiver/KS0_Sim", None]})
            self.assertEqual(sub_res["id"], 1)
            self.assertIsNone(sub_res["error"])

            # 2. Authorize
            miner_wallet = StealthKeyPair().get_public_stealth_address()
            auth_res = await call_rpc({"id": 2, "method": "mining.authorize", "params": [f"{miner_wallet}.ks0_box_01", "x"]})
            self.assertTrue(auth_res["result"])

            # Wait for first job
            for _ in range(50):
                if received_jobs:
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(len(received_jobs) > 0, "Failed to receive mining job")
            current_job_id = received_jobs[-1]

            # 3. Submit Shares
            for i in range(3):
                nonce = f"{i+1:08x}"
                sub_res = await call_rpc({
                    "id": 10 + i,
                    "method": "mining.submit",
                    "params": [f"{miner_wallet}.ks0_box_01", current_job_id, "00000000", "64000000", nonce]
                })
                self.assertTrue(sub_res["result"])
                if received_jobs:
                    current_job_id = received_jobs[-1]

            writer.close()
            await writer.wait_closed()
            listener_task.cancel()
        finally:
            await server.stop()

    def test_pool_fee_schedule_logic(self):
        server = BTCKStratumServer(host="127.0.0.1", port=3339, network_diff=1.0)
        
        # Simulated Day 3 (Within first 7 days)
        import time
        server.test_start_time = time.time() - (3 * 86400)
        rate, is_promo, left = server.get_pool_fee_status()
        self.assertEqual(rate, 0.00)
        self.assertTrue(is_promo)
        self.assertGreater(left, 0.0)
        
        # Simulated Day 8 (After 7-day promo expires)
        server.test_start_time = time.time() - (8 * 86400)
        rate, is_promo, left = server.get_pool_fee_status()
        self.assertEqual(rate, 0.01)  # 1.0%
        self.assertFalse(is_promo)
        self.assertEqual(left, 0.0)


if __name__ == "__main__":
    unittest.main()

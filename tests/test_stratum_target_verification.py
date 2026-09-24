"""
Unit test for Stratum Server PoW target verification (P0-02)
-----------------------------------------------------------
Tests:
1. Low-difficulty share rejection (Error 23)
2. Stale / unknown job rejection (Error 21)
3. Duplicate share rejection (Error 22)
4. Valid share acceptance
"""

import unittest
import asyncio
import json
import sys
sys.path.insert(0, '/Users/a1234/btck-core/src')

from stratum.stratum_server import BTCKStratumServer, StratumClient, MiningJob
from consensus.kheavyhash import difficulty_to_target, kheavyhash, hash_to_target_int

class MockWriter:
    def __init__(self):
        self.sent_messages = []
        self.closed = False

    def write(self, data: bytes):
        self.sent_messages.append(data.decode())

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

class TestStratumTargetVerification(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.server = BTCKStratumServer(host="127.0.0.1", port=19999, network_diff=1000.0)
        self.mock_writer = MockWriter()
        self.client = StratumClient(None, self.mock_writer, "test_client_1")
        self.client.authorized = True
        self.client.worker_name = "test_miner"
        self.client.difficulty = 1000.0  # High difficulty so arbitrary nonces fail share target
        self.server.clients[self.client.client_id] = self.client

    async def test_stale_or_unknown_job(self):
        # Submit for a job that does not exist
        await self.server.process_message(self.client, {
            "id": 1,
            "method": "mining.submit",
            "params": ["test_miner", "non_existent_job_99999", "12345678"]
        })
        self.assertEqual(len(self.mock_writer.sent_messages), 1)
        resp = json.loads(self.mock_writer.sent_messages[0])
        self.assertFalse(resp["result"])
        self.assertEqual(resp["error"][0], 21)
        self.assertEqual(self.client.shares_rejected, 1)

    async def test_low_difficulty_share_rejected(self):
        job = self.server.create_new_block_job()
        # Set difficulty very high (e.g. 10,000,000) so random nonce fails
        self.client.difficulty = 10000000.0
        await self.server.process_message(self.client, {
            "id": 2,
            "method": "mining.submit",
            "params": ["test_miner", str(job.job_id), "0000000000000001"]
        })
        resp = json.loads(self.mock_writer.sent_messages[-1])
        self.assertFalse(resp["result"])
        self.assertEqual(resp["error"][0], 23)
        self.assertIn("Low difficulty share", resp["error"][1])
        self.assertEqual(self.client.shares_rejected, 1)

    async def test_duplicate_share_rejected(self):
        job = self.server.create_new_block_job()
        # Set difficulty to 0.000001 so any share is accepted
        self.client.difficulty = 0.000001
        
        # First submission should be accepted
        await self.server.process_message(self.client, {
            "id": 3,
            "method": "mining.submit",
            "params": ["test_miner", str(job.job_id), "aabbccdd"]
        })
        resp1 = json.loads(self.mock_writer.sent_messages[-1])
        self.assertTrue(resp1["result"])
        self.assertEqual(self.client.shares_accepted, 1)

        # Second submission with the exact same nonce and job must be rejected as duplicate!
        await self.server.process_message(self.client, {
            "id": 4,
            "method": "mining.submit",
            "params": ["test_miner", str(job.job_id), "aabbccdd"]
        })
        resp2 = json.loads(self.mock_writer.sent_messages[-1])
        self.assertFalse(resp2["result"])
        self.assertEqual(resp2["error"][0], 22)
        self.assertIn("Duplicate share", resp2["error"][1])

    async def test_strict_pow_rejects_timeout_fallback_in_production(self):
        # Ensure that in strict_pow_only=True mode, elapsed time >= 2.5s does NOT award a block if hash > network_target
        job = self.server.create_new_block_job()
        self.client.difficulty = 0.000001 # Accept share
        self.server.strict_pow_only = True
        self.server.last_global_block_time = 0 # Force dt > 2.5s
        initial_height = self.server.current_height

        await self.server.process_message(self.client, {
            "id": 5,
            "method": "mining.submit",
            "params": ["test_miner", str(job.job_id), "deadbeef"]
        })
        # Share is accepted as valid share
        resp = json.loads(self.mock_writer.sent_messages[-1])
        self.assertTrue(resp["result"])
        # But block height MUST NOT increase because network_diff=1000.0 (hash is not <= network_target)
        self.assertEqual(self.server.current_height, initial_height)

if __name__ == "__main__":
    unittest.main()

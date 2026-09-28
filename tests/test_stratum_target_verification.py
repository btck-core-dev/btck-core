import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.kheavyhash import hash_to_target_int
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from privacy.stealth_address import StealthKeyPair
from stratum.stratum_server import BTCKStratumServer, StratumClient


class MockWriter:
    def __init__(self):
        self.messages = []

    def write(self, data):
        self.messages.append(json.loads(data.decode()))

    async def drain(self):
        pass


class TestStratumTargetVerification(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.daemon = BTCKDaemon(host="127.0.0.1", network="devnet")
        self.writer = MockWriter()
        self.client = StratumClient(None, self.writer, "client-1")
        self.client.authorized = True
        self.client.worker_name = "worker"
        self.client.wallet_address = StealthKeyPair().get_public_stealth_address()
        self.server = BTCKStratumServer(
            network="devnet", node_rpc=InProcessNodeRPCClient(self.daemon)
        )
        self.server.clients[self.client.client_id] = self.client

    async def _job(self):
        return await self.server.create_new_block_job(self.client)

    async def test_unknown_and_expired_jobs_are_rejected(self):
        await self.server.process_message(self.client, {"id": 1, "method": "mining.submit", "params": ["worker", "missing", "1"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 21)
        job = await self._job()
        job.created_at = time.time() - self.server.job_ttl_seconds - 1
        await self.server.process_message(self.client, {"id": 2, "method": "mining.submit", "params": ["worker", job.job_id, "1"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 21)

    async def test_low_difficulty_and_duplicate_shares(self):
        job = await self._job()
        values = [(n, hash_to_target_int(bytes.fromhex(job.build_block(n).hash))) for n in range(20)]
        nonce, value = max(values, key=lambda item: item[1])
        self.server.share_target = max(1, value - 1)
        await self.server.process_message(self.client, {"id": 3, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 23)
        await self.server.process_message(self.client, {"id": 4, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 22)

    async def test_client_cannot_lower_difficulty(self):
        original = self.server.share_target
        await self.server.process_message(self.client, {"id": 5, "method": "mining.suggest_difficulty", "params": [0.000001]})
        self.assertFalse(self.writer.messages[-1]["result"])
        self.assertEqual(self.server.share_target, original)


if __name__ == "__main__":
    unittest.main()

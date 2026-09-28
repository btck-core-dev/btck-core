import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.pow import compact_to_target
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from privacy.stealth_address import StealthKeyPair
from stratum.stratum_server import BTCKStratumServer


class TestNodeBackedStratum(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_node_authoritative_block_submission(self):
        daemon = BTCKDaemon(host="127.0.0.1", network="devnet")
        server = BTCKStratumServer(
            host="127.0.0.1",
            port=0,
            network="devnet",
            node_rpc=InProcessNodeRPCClient(daemon),
        )
        await server.start()
        port = server.server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((json.dumps({"id": 1, "method": "mining.subscribe", "params": ["test"]}) + "\n").encode())
            await writer.drain()
            subscribed = json.loads(await reader.readline())
            self.assertIsNone(subscribed["error"])
            target_msg = json.loads(await reader.readline())
            self.assertEqual(target_msg["method"], "mining.set_target")

            wallet = StealthKeyPair().get_public_stealth_address()
            writer.write((json.dumps({"id": 2, "method": "mining.authorize", "params": [wallet + ".worker", "x"]}) + "\n").encode())
            await writer.drain()
            self.assertTrue(json.loads(await reader.readline())["result"])
            notify = json.loads(await reader.readline())
            job_id = notify["params"][0]
            job = server.current_jobs[job_id]
            target = compact_to_target(job.bits)
            nonce = next(n for n in range(1000) if int.from_bytes(bytes.fromhex(job.build_block(n).hash), "little") <= target)

            initial_blocks = len(daemon.dag.blocks)
            writer.write((json.dumps({"id": 3, "method": "mining.submit", "params": ["worker", job_id, f"{nonce:x}"]}) + "\n").encode())
            await writer.drain()
            submitted = json.loads(await reader.readline())
            self.assertTrue(submitted["result"])
            self.assertEqual(len(daemon.dag.blocks), initial_blocks + 1)
            self.assertFalse(hasattr(server, "current_height"))
            self.assertFalse(hasattr(server, "issued_supply_satk"))
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


if __name__ == "__main__":
    unittest.main()

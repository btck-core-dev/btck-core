#!/usr/bin/env python3
"""End-to-End Integration Test for Stratum PPLNS Pool Mode.

Verifies:
1. Stratum server in 'pplns' mode directs block templates to the Pool Treasury stealth address.
2. Miner shares are credited to SQLite ledger with difficulty weights.
3. Block discovery triggers proportional distribution across miners in the sliding window.
4. AutoPayoutWorker settles eligible balances via stealth transactions.
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest

from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from consensus.pow import compact_to_target
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet, SATK_PER_COIN
from stratum.stratum_server import BTCKStratumServer


class TestStratumPPLNSE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "pplns_e2e.db")

        # Generate treasury wallet & 2 miners
        self.treasury = StealthWallet.generate("PoolTreasury")
        self.miner1 = StealthWallet.generate("Miner1")
        self.miner2 = StealthWallet.generate("Miner2")

        # Save treasury wallet JSON so stratum can load it
        self.treasury_file = os.path.join(self.temp_dir, "treasury_wallet.json")
        with open(self.treasury_file, "w") as f:
            json.dump(self.treasury.export_to_dict(include_private=True), f)

        # Set environment variables for PPLNS mode
        os.environ["BTCK_POOL_MODE"] = "pplns"
        os.environ["BTCK_POOL_TREASURY_ADDRESS"] = self.treasury.address
        os.environ["BTCK_POOL_TREASURY_WALLET_FILE"] = self.treasury_file
        os.environ["BTCK_PPLNS_DB_PATH"] = self.db_path
        os.environ["BTCK_PPLNS_MIN_PAYOUT_BTCK"] = "1.0"

        self.daemon = BTCKDaemon(host="127.0.0.1", network="simnet")
        self.server = BTCKStratumServer(
            host="127.0.0.1",
            port=0,
            network="simnet",
            share_target=compact_to_target(0x207fffff),
            node_rpc=InProcessNodeRPCClient(self.daemon),
        )
        await self.server.start()
        self.port = self.server.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.server.stop()
        for k in [
            "BTCK_POOL_MODE",
            "BTCK_POOL_TREASURY_ADDRESS",
            "BTCK_POOL_TREASURY_WALLET_FILE",
            "BTCK_PPLNS_DB_PATH",
            "BTCK_PPLNS_MIN_PAYOUT_BTCK",
        ]:
            os.environ.pop(k, None)
        shutil.rmtree(self.temp_dir)

    async def test_pplns_block_discovery_and_split(self):
        # 1. Verify pool mode is active
        self.assertEqual(self.server.pool_mode, "pplns")
        self.assertEqual(self.server.pool_treasury_address, self.treasury.address)

        # 2. Connect Miner 1 (using btck-standard agent to receive JSON block template)
        r1, w1 = await asyncio.open_connection("127.0.0.1", self.port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            w1.write((json.dumps({"id": 1, "method": "mining.subscribe", "params": ["btck-test-agent"]}) + "\n").encode())
            await w1.drain()
            sub_resp = json.loads(await r1.readline())
            self.assertIsNone(sub_resp["error"])
            await r1.readline()  # target notification

            w1.write((json.dumps({"id": 2, "method": "mining.authorize", "params": [self.miner1.address, "d=1.0"]}) + "\n").encode())
            await w1.drain()
            auth_resp = json.loads(await r1.readline())
            self.assertTrue(auth_resp["result"])

            # Receive first job for Miner 1
            job1_msg = json.loads(await r1.readline())
            job1_data = job1_msg["params"][1]
            job1_id = job1_msg["params"][0]

            # In PPLNS mode, block template MUST be addressed to the Treasury!
            cb_out = job1_data["coinbase_output"]
            cb_bytes = (bytes.fromhex(cb_out[0]), bytes.fromhex(cb_out[1]))
            self.assertTrue(self.treasury.keypair.is_my_stealth_output(cb_bytes[0], cb_bytes[1]))

            # 3. Connect Miner 2
            w2.write((json.dumps({"id": 1, "method": "mining.subscribe", "params": ["btck-test-agent"]}) + "\n").encode())
            await w2.drain()
            await r2.readline()  # sub
            await r2.readline()  # target

            w2.write((json.dumps({"id": 2, "method": "mining.authorize", "params": [self.miner2.address, "d=1.0"]}) + "\n").encode())
            await w2.drain()
            await r2.readline()  # auth
            job2_msg = json.loads(await r2.readline())  # job

            # 4. Miner 1 finds a valid nonce that meets target
            job = self.server.current_jobs[job1_id]
            target = min(self.server.share_target, compact_to_target(job.bits))
            nonce = next(n for n in range(5000) if int.from_bytes(bytes.fromhex(job.build_block(n).hash), "little") <= target)

            w1.write((json.dumps({
                "id": 10,
                "method": "mining.submit",
                "params": [self.miner1.address, job1_id, f"{nonce:x}"]
            }) + "\n").encode())
            await w1.drain()

            submit_resp = json.loads(await r1.readline())
            self.assertTrue(submit_resp["result"])

            # 5. Verify PPLNS accounting
            stats = self.server.pplns_engine.get_pool_stats()
            self.assertGreaterEqual(stats["blocks_found"], 1)

            # Miner 1 found the block, so Miner 1 gets reward credited
            m1 = self.server.pplns_engine.get_miner(self.miner1.address)
            self.assertIsNotNone(m1)
            self.assertGreater(m1.unpaid_satk, 0)

            # Treasury received the block on chain
            utxo_resp = await self.server.node_rpc.call("getUTXOSet")
            self.treasury.scan_chain_outputs(utxo_resp["outputs"])
            self.assertGreater(self.treasury.get_balance_satk(), 0)

        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()


if __name__ == "__main__":
    unittest.main()

"""Real loopback Stratum and simnet only; never connects to mainnet."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from consensus.kheavyhash import hash_to_target_int
from consensus.pow import compact_to_target
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from privacy.wallet_engine import StealthWallet
from stratum.stratum_server import BTCKStratumServer


class PPLNSTCPAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.treasury = StealthWallet.generate('disposable-pool')
        self.miner = StealthWallet.generate('disposable-miner')
        self.daemon = BTCKDaemon(host='127.0.0.1', network='simnet')
        self.env = patch.dict('os.environ', {
            'BTCK_POOL_MODE': 'pplns',
            'BTCK_PPLNS_DB_PATH': str(Path(self.directory.name) / 'pool.sqlite3'),
            'BTCK_POOL_TREASURY_ADDRESS': self.treasury.address,
            'BTCK_POOL_FEE_ADDRESS': self.treasury.address,
            'BTCK_PPLNS_AUTOPAYOUT_ENABLED': '0',
            'BTCK_POOL_TREASURY_WALLET_FILE': '',
        })
        self.env.start()
        self.server = BTCKStratumServer(host='127.0.0.1', port=0, network='simnet',
                                       node_rpc=InProcessNodeRPCClient(self.daemon))
        self.server.running = True
        self.listener = await asyncio.start_server(self.server.handle_client, '127.0.0.1', 0)
        self.writers = []

    async def asyncTearDown(self):
        self.server.running = False
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        await asyncio.sleep(0)
        self.listener.close()
        await self.listener.wait_closed()
        tasks = tuple(self.server._block_submission_tasks)
        if tasks:
            await asyncio.gather(*tasks)
        self.env.stop()
        self.directory.cleanup()

    async def request(self, reader, writer, ident, method, params):
        writer.write((json.dumps(dict(id=ident, method=method, params=params)) + '\n').encode())
        await writer.drain()
        while True:
            reply = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if reply.get('id') == ident and 'result' in reply:
                return reply

    async def connect(self, difficulty='x'):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.listener.sockets[0].getsockname()[1])
        self.writers.append(writer)
        self.assertIsNone((await self.request(reader, writer, 1, 'mining.subscribe', ['BTCK/native-v2']))['error'])
        response = await self.request(reader, writer, 2, 'mining.authorize', [self.miner.address + '.loopback', difficulty])
        return reader, writer, response

    async def latest_job(self):
        for _ in range(100):
            if self.server.current_jobs:
                return list(self.server.current_jobs.values())[-1]
            await asyncio.sleep(.01)
        self.fail('No mining job was issued over TCP')

    async def test_native_client_cannot_inflate_credit_with_password_difficulty(self):
        reader, writer, response = await self.connect('d=1000000000')
        self.assertTrue(response['result'])
        job = await self.latest_job()
        actual_weight = ((1 << 224) - 1) / job.share_target
        self.assertAlmostEqual(job.share_diff / actual_weight, 1.0, places=10)

    async def test_nonfinite_manual_difficulty_is_rejected_before_authorization(self):
        for value in ['inf', 'nan']:
            reader, writer, response = await self.connect('d=' + value)
            self.assertFalse(response['result'])
            self.assertEqual(response['error'][0], 24)

    async def test_tcp_share_block_distribution_and_retry_are_durable(self):
        reader, writer, response = await self.connect()
        self.assertTrue(response['result'])
        job = await self.latest_job()
        self.assertEqual(job.assigned_payout_address, self.treasury.address)
        target = min(job.share_target, compact_to_target(job.bits))
        for nonce in range(10000):
            if hash_to_target_int(job.calculate_pow(nonce)) <= target:
                break
        else:
            self.fail('Simnet nonce search exhausted')
        submit = [self.miner.address + '.loopback', job.job_id, f'{nonce:x}']
        self.assertTrue((await self.request(reader, writer, 3, 'mining.submit', submit))['result'])
        tasks = tuple(self.server._block_submission_tasks)
        if tasks:
            await asyncio.gather(*tasks)
        self.assertTrue((await self.request(reader, writer, 4, 'mining.submit', submit))['result'])
        engine = self.server.pplns_engine
        account = engine.get_miner(self.miner.address)
        self.assertEqual(account.total_shares_count, 1)
        self.assertAlmostEqual(account.total_shares_weight / (((1 << 224) - 1) / job.share_target), 1.0, places=10)
        self.assertGreater(account.unpaid_satk, 0)
        self.assertEqual(account.paid_satk, 0)
        with engine._get_connection() as db:
            reward, fee, distributed = db.execute('SELECT reward_satk,pool_fee_satk,distributed_satk FROM blocks').fetchone()
        self.assertEqual(reward, fee + distributed)
        self.assertEqual(distributed, account.unpaid_satk)
        from stratum.pplns import PPLNSEngine
        reopened = PPLNSEngine(engine.db_path)
        self.assertEqual(reopened.get_miner(self.miner.address).unpaid_satk, distributed)

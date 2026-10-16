import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock

from node.blockdag import BlockDAG
from p2p.p2p_network import PeerConnection
from p2p.standby_sync import StandbySyncNetwork
from privacy.stealth_address import StealthKeyPair
from tests.pow_helpers import mine_block


class StandbySyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_joiner_reaches_identical_consensus_over_loopback(self):
        from p2p.p2p_network import P2PNetwork
        source_dag = BlockDAG(network='simnet')
        destination_dag = BlockDAG(network='simnet')
        address = StealthKeyPair().get_public_stealth_address()
        for i in range(20):
            output = StealthKeyPair.generate_one_time_stealth_output(address)
            block = mine_block(source_dag, parents=source_dag.get_tips(),
                               timestamp=1788912100.0+i, coinbase_output=output)
            self.assertTrue(source_dag.add_block(block))
        source = P2PNetwork(source_dag, p2p_host='127.0.0.1', p2p_port=0)
        await source.start()
        port = source.server.sockets[0].getsockname()[1]
        destination = StandbySyncNetwork(
            destination_dag, p2p_host='127.0.0.1', p2p_port=0,
            seed_peers=[f'127.0.0.1:{port}'], requests_per_second=20)
        try:
            started = time.monotonic()
            await destination.start()
            async with asyncio.timeout(15):
                while len(destination_dag.blocks) < len(source_dag.blocks):
                    await asyncio.sleep(.02)
            self.assertGreaterEqual(time.monotonic()-started, .90)
            self.assertEqual(source_dag.selected_tip, destination_dag.selected_tip)
            self.assertEqual(source_dag.state_root(), destination_dag.state_root())
            self.assertEqual(destination._outstanding, {})
        finally:
            await destination.stop()
            await source.stop()

    def peer(self):
        writer = Mock()
        writer.is_closing.return_value = False
        peer = PeerConnection(None, writer, '127.0.0.1:1', 'test')
        peer.send_msg = AsyncMock()
        return peer

    async def test_concurrent_requests_share_global_pacing_and_dedup(self):
        network = StandbySyncNetwork(BlockDAG(network='simnet'), requests_per_second=20)
        peers = [self.peer(), self.peer()]
        sent = []
        for peer in peers:
            async def record(*args):
                sent.append(time.monotonic())
            peer.send_msg.side_effect = record
        await asyncio.gather(*[
            network._request_block(peers[i % 2], f'{i:064x}') for i in range(4)
        ])
        self.assertEqual(len(sent), 4)
        self.assertTrue(all(b-a >= .045 for a, b in zip(sent, sent[1:])))
        self.assertFalse(await network._request_block(peers[1], f'{0:064x}'))
        self.assertEqual(len(sent), 4)

    async def test_timeout_disconnects_without_retry(self):
        network = StandbySyncNetwork(BlockDAG(network='simnet'), request_timeout=.02)
        peer = self.peer()
        await network._request_block(peer, 'a'*64)
        await asyncio.sleep(.03)
        network._expire_requests()
        peer.writer.close.assert_called_once()
        self.assertEqual(peer.send_msg.await_count, 1)
        self.assertEqual(network._outstanding, {})

    async def test_outstanding_cap_fails_closed(self):
        network = StandbySyncNetwork(BlockDAG(network='simnet'), max_outstanding=16)
        peer = self.peer()
        for i in range(16):
            block_hash = f'{i:064x}'
            peer.requested_blocks.add(block_hash)
            network._outstanding[(id(peer), block_hash)] = (peer, time.monotonic()+30)
        self.assertFalse(await network._request_block(peer, 'f'*64))
        peer.writer.close.assert_called_once()
        peer.send_msg.assert_not_awaited()

    async def test_completion_releases_global_slot(self):
        network = StandbySyncNetwork(BlockDAG(network='simnet'))
        peer = self.peer()
        await network._request_block(peer, 'a'*64)
        await network._complete_block_request(peer, 'a'*64)
        self.assertEqual(network._outstanding, {})
        self.assertNotIn('a'*64, peer.requested_blocks)

    async def test_suppressed_duplicate_survives_owner_disconnect(self):
        network = StandbySyncNetwork(BlockDAG(network='simnet'), requests_per_second=1000)
        owner, waiting = self.peer(), self.peer()
        block_hash = 'a'*64
        await network._request_block(owner, block_hash)
        waiting.ibd_pending.add(block_hash)
        waiting.ibd_queue.append(block_hash)
        await network._pump_ibd_requests(waiting)
        self.assertIn(block_hash, waiting.ibd_queue)
        waiting.send_msg.assert_not_awaited()
        waiting.peer_addr = '127.0.0.1:2'
        network.peers = {p.peer_addr:p for p in (owner, waiting)}
        network.running = True
        owner.reader = Mock()
        owner.reader.readline = AsyncMock(return_value=b'')
        owner.writer.wait_closed = AsyncMock()
        def mark_closed():
            owner.writer.is_closing.return_value = True
        owner.writer.close.side_effect = mark_closed
        await network._peer_listen_loop(owner)
        waiting.send_msg.assert_awaited_once_with('GET_BLOCK', {'hash':block_hash})
        self.assertIn(block_hash, waiting.requested_blocks)

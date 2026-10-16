"""Run against the candidate, or an exact captured deployment with the env override."""
import importlib.util
import asyncio
import hashlib
import os
from pathlib import Path
import time
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from node.blockdag import BlockDAG
from privacy.stealth_address import StealthKeyPair
from tests.pow_helpers import mine_block
from privacy.wallet_engine import transaction_to_dict


def implementation():
    path = os.environ.get('BTCK_P2P_REGRESSION_IMPL')
    if path:
        spec = importlib.util.spec_from_file_location('captured_p2p_regression', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from p2p import p2p_network
    return p2p_network


p2p = implementation()


def wire(block):
    return {'hash': block.hash, 'version': block.version, 'chain_id': block.chain_id,
            'parents': block.parents, 'timestamp': block.timestamp, 'bits': block.bits,
            'nonce': block.nonce, 'payload_hash': block.payload_hash.hex(),
            'coinbase_output': [v.hex() for v in block.coinbase_output],
            'transactions': [transaction_to_dict(tx) for tx in block.transactions]}


class SyncRegressions(unittest.IsolatedAsyncioTestCase):
    def setup_pair(self):
        self.source = BlockDAG(network='simnet')
        self.target = BlockDAG(network='simnet')
        self.net = p2p.P2PNetwork(self.target)
        self.peer = p2p.PeerConnection(None, Mock(), '127.0.0.1:9', self.net.network_magic)
        self.peer.handshake_complete = True
        self.peer.send_msg = AsyncMock()
        self.net.peers[self.peer.peer_addr] = self.peer
        self.address = StealthKeyPair().get_public_stealth_address()

    def block(self, parents=None):
        block = mine_block(self.source, parents=parents or self.source.get_tips(),
                           timestamp=1788912100.0+len(self.source.blocks),
                           coinbase_output=StealthKeyPair.generate_one_time_stealth_output(self.address))
        self.assertTrue(self.source.add_block(block))
        return block

    async def msg(self, kind, payload):
        await self.net._process_msg(self.peer, {'type': kind, 'payload': payload})

    async def test_gossip_does_not_replace_history_batch(self):
        self.setup_pair()
        hashes = [f'{i:064x}' for i in range(1, 4)]
        await self.msg('INV', {'blocks': hashes, 'more': True})
        await self.msg('INV', {'blocks': ['f'*64]})
        self.assertTrue(self.peer.ibd_more)
        self.assertEqual(self.peer.ibd_cursor, hashes[-1])
        self.assertTrue(set(hashes).issubset(self.peer.ibd_pending))

    async def test_received_orphan_is_not_committed_batch_progress(self):
        self.setup_pair()
        parent = self.block()
        child = self.block()
        await self.msg('INV', {'blocks': [child.hash], 'more': True})
        await self.msg('BLOCK', wire(child))
        self.assertIn(child.hash, self.peer.ibd_pending)
        self.assertFalse(any(c.args[0] == 'GET_BLOCKS' for c in self.peer.send_msg.await_args_list))
        await self.msg('BLOCK', wire(parent))
        self.assertIn(child.hash, self.target.blocks)
        self.assertTrue(any(c.args[0] == 'GET_BLOCKS' for c in self.peer.send_msg.await_args_list))

    async def test_duplicate_parent_request_suppressed(self):
        self.setup_pair()
        parent = self.block()
        children = [self.block([parent.hash]) for _ in range(2)]
        for child in children:
            await self.msg('BLOCK', wire(child))
        requests = [c for c in self.peer.send_msg.await_args_list
                    if c.args == ('GET_BLOCK', {'hash': parent.hash})]
        self.assertEqual(len(requests), 1)

    async def test_parents_and_history_share_bounded_window(self):
        self.setup_pair()
        parents = [self.block([self.source.genesis_hash]) for _ in range(24)]
        children = [self.block([b.hash]) for b in parents]
        await self.msg('INV', {'blocks': [b.hash for b in children], 'more': False})
        for child in children:
            await self.msg('BLOCK', wire(child))
        requests = [c for c in self.peer.send_msg.await_args_list if c.args[0] == 'GET_BLOCK']
        # Returned children release slots; their ancestors use the same window.
        self.assertLessEqual(len(requests)-len(children), 16)

    async def test_unrelated_gossip_orphan_cannot_block_next_history_page(self):
        self.setup_pair()
        history = self.block()
        missing = self.block()
        gossip = self.block()
        await self.msg('BLOCK', wire(gossip))
        self.assertIn(gossip.hash, self.net.orphan_pool)
        await self.msg('INV', {'blocks': [history.hash], 'more': True})
        await self.msg('BLOCK', wire(history))
        self.assertNotIn(missing.hash, self.target.blocks)
        self.assertTrue(any(c.args[0] == 'GET_BLOCKS' for c in self.peer.send_msg.await_args_list))

    async def test_orphan_admission_failure_is_visible_and_retained(self):
        self.setup_pair()
        parent, child = self.block(), self.block()
        await self.msg('BLOCK', wire(child))
        normal_accept = self.net.block_acceptor
        def failing(block):
            if block.hash == child.hash:
                raise OSError('injected persistence failure')
            return normal_accept(block)
        self.net.block_acceptor = failing
        with self.assertRaisesRegex(RuntimeError, 'orphan admission failed'):
            await self.msg('BLOCK', wire(parent))
        self.assertIn(child.hash, self.net.orphan_pool)
        self.assertNotIn(child.hash, self.target.blocks)

    async def test_reverse_delivery_accepts_exact_merge_dag(self):
        self.setup_pair()
        blocks = []
        for _ in range(20):
            parents = self.source.get_tips()
            blocks.extend([self.block(parents), self.block(parents)])
        # A final merger includes both tips, so all forty ancestors are relevant.
        blocks.append(self.block())
        await self.msg('INV', {'blocks': [blocks[-1].hash], 'more': False})
        for block in reversed(blocks):
            await self.msg('BLOCK', wire(block))
        self.assertEqual(set(self.source.blocks), set(self.target.blocks))
        self.assertEqual(self.source.state_root(), self.target.state_root())
        self.assertFalse(self.net.orphan_pool)

    async def test_reconnect_resumes_cached_orphan_dependencies(self):
        self.setup_pair()
        parent, child = self.block(), self.block()
        await self.msg('BLOCK', wire(child))
        self.net.peers.clear()
        new_peer = p2p.PeerConnection(None, Mock(), '127.0.0.1:10', self.net.network_magic)
        new_peer.handshake_complete = True
        new_peer.send_msg = AsyncMock()
        self.net.peers[new_peer.peer_addr] = new_peer
        self.peer = new_peer
        await self.net._resume_orphan_dependencies(new_peer)
        self.assertIn(parent.hash, new_peer.requested_blocks)
        await self.msg('BLOCK', wire(parent))
        self.assertEqual(self.source.state_root(), self.target.state_root())
        self.assertFalse(self.net.orphan_pool)

    async def test_orphan_failure_retry_preserves_other_children_indexes(self):
        self.setup_pair()
        parent = self.block()
        children = [self.block([parent.hash]) for _ in range(3)]
        for child in children:
            await self.msg('BLOCK', wire(child))
        normal_accept = self.net.block_acceptor
        def failing(block):
            if block.hash != parent.hash:
                raise OSError('injected unavailable storage')
            return normal_accept(block)
        self.net.block_acceptor = failing
        with self.assertRaises(RuntimeError):
            await self.msg('BLOCK', wire(parent))
        self.net.block_acceptor = normal_accept
        await self.net._resume_orphan_dependencies(self.peer)
        self.assertEqual(set(self.source.blocks), set(self.target.blocks))
        self.assertFalse(self.net.orphan_pool)
        self.assertFalse(self.net.orphans_by_parent)

    async def test_capacity_fails_explicitly_without_silent_eviction(self):
        self.setup_pair()
        parent = self.block()
        first, second = self.block([parent.hash]), self.block([parent.hash])
        with patch.object(p2p, 'MAX_ORPHANS', 1):
            await self.msg('BLOCK', wire(first))
            with self.assertRaisesRegex(RuntimeError, 'orphan capacity exceeded'):
                await self.msg('BLOCK', wire(second))
        self.assertEqual(set(self.net.orphan_pool), {first.hash})

    async def test_orphan_byte_budget_is_enforced_and_released(self):
        self.setup_pair()
        parent, child = self.block(), self.block()
        with patch.object(p2p, 'MAX_ORPHAN_BYTES', 1):
            with self.assertRaisesRegex(RuntimeError, 'orphan byte capacity'):
                await self.msg('BLOCK', wire(child))
        self.assertFalse(self.net.orphan_pool)
        self.assertEqual(self.net.orphan_bytes, 0)
        await self.msg('BLOCK', wire(child))
        self.assertGreater(self.net.orphan_bytes, 0)
        await self.msg('BLOCK', wire(parent))
        self.assertEqual(self.net.orphan_bytes, 0)
        self.assertFalse(self.net.orphan_sizes)

    async def test_timeout_does_not_silently_keep_stalled_peer(self):
        self.setup_pair()
        self.peer.request_deadlines['a'*64] = time.monotonic()-1
        with self.assertRaises(TimeoutError):
            self.net._check_sync_timeouts(self.peer)
        self.assertEqual(self.net.sync_counters['request_timeout'], 1)

    async def test_lost_inventory_response_also_times_out(self):
        self.setup_pair()
        self.peer.ibd_waiting_page = True
        self.peer.ibd_page_deadline = time.monotonic()-1
        with self.assertRaises(TimeoutError):
            self.net._check_sync_timeouts(self.peer)

    async def test_empty_continuation_cannot_spin_history_requests(self):
        self.setup_pair()
        with self.assertRaisesRegex(ValueError, 'continuation marker'):
            await self.msg('INV', {'blocks': [], 'more': True})
        self.peer.send_msg.assert_not_awaited()

    async def test_consensus_rejection_never_commits_history_progress(self):
        self.setup_pair()
        block = self.block()
        original = self.net.block_acceptor
        def reject(block):
            raise ValueError('injected invalid PoW')
        self.net.block_acceptor = reject
        await self.msg('INV', {'blocks': [block.hash], 'more': True})
        with self.assertRaisesRegex(RuntimeError, 'block admission failed'):
            await self.msg('BLOCK', wire(block))
        self.assertIn(block.hash, self.peer.ibd_pending)
        self.assertNotIn(block.hash, self.target.blocks)
        self.assertFalse(any(c.args[0] == 'GET_BLOCKS' for c in self.peer.send_msg.await_args_list))
        self.net.block_acceptor = original

    async def test_far_ahead_gossip_is_deferred_until_history_admitted(self):
        self.setup_pair()
        history, live = self.block(), self.block()
        await self.msg('INV', {'blocks': [history.hash], 'more': False})
        await self.msg('INV', {'blocks': [live.hash]})
        self.assertNotIn(live.hash, self.peer.requested_blocks)
        self.assertIn(live.hash, self.peer.deferred_gossip)
        await self.msg('BLOCK', wire(history))
        self.assertIn(live.hash, self.peer.requested_blocks)
        await self.msg('BLOCK', wire(live))
        self.assertEqual(self.source.state_root(), self.target.state_root())

    async def test_wire_hash_mismatch_cannot_advance_page(self):
        self.setup_pair()
        block = self.block()
        await self.msg('INV', {'blocks': [block.hash], 'more': True})
        payload = wire(block)
        payload['nonce'] += 1
        with self.assertRaisesRegex(ValueError, 'hash does not match'):
            await self.msg('BLOCK', payload)
        self.assertNotIn(block.hash, self.target.blocks)
        self.assertIn(block.hash, self.peer.ibd_pending)

    async def test_empty_history_page_completes_request(self):
        self.setup_pair()
        await self.msg('GET_BLOCKS', {'locator': [self.target.genesis_hash]})
        self.peer.send_msg.assert_awaited_once_with('INV', {'blocks': [], 'more': False})

    async def test_shared_ancestor_from_other_peer_unblocks_history_page(self):
        self.setup_pair()
        parent, child = self.block(), self.block()
        await self.msg('INV', {'blocks': [child.hash], 'more': True})
        await self.msg('BLOCK', wire(child))
        other = p2p.PeerConnection(None, Mock(), '127.0.0.1:11', self.net.network_magic)
        other.handshake_complete = True
        other.send_msg = AsyncMock()
        self.net.peers[other.peer_addr] = other
        await self.net._process_msg(other, {'type': 'BLOCK', 'payload': wire(parent)})
        self.assertFalse(self.peer.ibd_pending)
        self.assertNotIn(child.hash, self.peer.requested_blocks)
        self.assertNotIn(child.hash, self.peer.request_deadlines)
        self.assertTrue(any(c.args[0] == 'GET_BLOCKS' for c in self.peer.send_msg.await_args_list))


class FrozenDeploymentTests(unittest.TestCase):
    def test_capture_is_byte_identical(self):
        capture = Path(__file__).parent / 'fixtures/p2p_network_seedb_20261015.py'
        self.assertEqual(hashlib.sha256(capture.read_bytes()).hexdigest(),
                         '240bebe37c91964c3027743d3a3cf707baf9008978ea106708de0b0b47fabae0')


@unittest.skipIf(os.environ.get('BTCK_P2P_REGRESSION_IMPL'), 'candidate-only integration')
class NativeTCPRegression(unittest.IsolatedAsyncioTestCase):
    async def test_real_tcp_disconnect_and_explicit_reconnect_resume(self):
        path = Path(__file__).parent / 'fixtures/p2p_network_seedb_20261015.py'
        spec = importlib.util.spec_from_file_location('frozen_disconnect_source', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        source_dag, target_dag = BlockDAG(network='simnet'), BlockDAG(network='simnet')
        output = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        for i in range(24):
            block = mine_block(source_dag, parents=source_dag.get_tips(),
                               timestamp=1788912100+i, coinbase_output=output)
            self.assertTrue(source_dag.add_block(block))
        class InterruptedSource(old.P2PNetwork):
            replies = 0
            interrupted = False
            async def _process_msg(self, peer, msg):
                if msg['type'] == 'GET_BLOCK' and not self.interrupted:
                    self.replies += 1
                    if self.replies == 3:
                        self.interrupted = True
                        peer.writer.close()
                        return
                await super()._process_msg(peer, msg)
        source = InterruptedSource(source_dag, p2p_host='127.0.0.1', p2p_port=0)
        await source.start()
        endpoint = f'127.0.0.1:{source.server.sockets[0].getsockname()[1]}'
        target = p2p.P2PNetwork(target_dag, p2p_host='127.0.0.1', p2p_port=0,
                                seed_peers=[endpoint])
        try:
            await target.start()
            async with asyncio.timeout(10):
                while not source.interrupted or target.peers:
                    await asyncio.sleep(.02)
            self.assertLess(len(target_dag.blocks), len(source_dag.blocks))
            await target.connect_to_peer(endpoint)
            async with asyncio.timeout(10):
                while set(target_dag.blocks) != set(source_dag.blocks):
                    await asyncio.sleep(.02)
            self.assertEqual(source_dag.state_root(), target_dag.state_root())
        finally:
            await target.stop()
            await source.stop()

    async def test_full_500_block_page_with_durable_native_admission(self):
        from node.daemon import BTCKDaemon
        path = Path(__file__).parent / 'fixtures/p2p_network_seedb_20261015.py'
        spec = importlib.util.spec_from_file_location('frozen_fullpage_source', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        source_dag = BlockDAG(network='simnet')
        output = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        for i in range(520):
            block = mine_block(source_dag, parents=source_dag.get_tips(),
                               timestamp=1788912100+i, coinbase_output=output)
            self.assertTrue(source_dag.add_block(block))
        source = old.P2PNetwork(source_dag, p2p_host='127.0.0.1', p2p_port=0)
        await source.start()
        port = source.server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as root:
                destination = BTCKDaemon(network='simnet', data_root=root, persistent=True,
                                          p2p_host='127.0.0.1', p2p_port=0,
                                          seed_peers=[f'127.0.0.1:{port}'])
                target = destination.p2p
                await target.start()
                try:
                    async with asyncio.timeout(60):
                        while len(destination.dag.blocks) < len(source_dag.blocks):
                            await asyncio.sleep(.02)
                    self.assertEqual(source_dag.state_root(), destination.dag.state_root())
                    self.assertEqual(target.sync_counters['history_page'], 2)
                    with destination.chain_store._connect() as conn:
                        persisted = {r[0] for r in conn.execute('SELECT block_hash FROM accepted_blocks')}
                    self.assertEqual(persisted, set(source_dag.blocks)-{source_dag.genesis_hash})
                    self.assertFalse(target.orphan_pool)
                    self.assertEqual(destination.dag.utxo_set.total_value_sats(),
                                     destination.dag.issued_supply_sats)
                finally:
                    await target.stop()
        finally:
            await source.stop()

    async def test_multiple_pages_and_live_gossip_with_frozen_old_source(self):
        """Old serving code, candidate receiver, real PoW/consensus and TCP only."""
        path = Path(__file__).parent / 'fixtures/p2p_network_seedb_20261015.py'
        spec = importlib.util.spec_from_file_location('frozen_source_p2p', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        old.MAX_INV_BLOCKS = 7  # Force multiple pages without a long chain replay.
        source_dag, target_dag = BlockDAG(network='simnet'), BlockDAG(network='simnet')
        address = StealthKeyPair().get_public_stealth_address()
        for i in range(32):
            parents = source_dag.get_tips()
            for _ in range(2):
                block = mine_block(source_dag, parents=parents, timestamp=1788912100+len(source_dag.blocks),
                                   coinbase_output=StealthKeyPair.generate_one_time_stealth_output(address))
                self.assertTrue(source_dag.add_block(block))
        merger = mine_block(source_dag, parents=source_dag.get_tips(), timestamp=1788912300,
                           coinbase_output=StealthKeyPair.generate_one_time_stealth_output(address))
        self.assertTrue(source_dag.add_block(merger))
        class InterleavedSource(old.P2PNetwork):
            async def _process_msg(self, peer, msg):
                await super()._process_msg(peer, msg)
                if msg['type'] == 'GET_BLOCKS':
                    # Deliberately put a new-tip announcement immediately
                    # after every historical page, before its BLOCK responses.
                    await peer.send_msg('INV', {'blocks': [merger.hash]})
        source = InterleavedSource(source_dag, p2p_host='127.0.0.1', p2p_port=0)
        await source.start()
        port = source.server.sockets[0].getsockname()[1]
        target = p2p.P2PNetwork(target_dag, p2p_host='127.0.0.1', p2p_port=0,
                                seed_peers=[f'127.0.0.1:{port}'])
        try:
            await target.start()
            async with asyncio.timeout(20):
                while set(target_dag.blocks) != set(source_dag.blocks):
                    await asyncio.sleep(.02)
            self.assertEqual(source_dag.state_root(), target_dag.state_root())
            self.assertGreater(target.sync_counters['history_page'], 1)
            self.assertFalse(target.orphan_pool)
        finally:
            await target.stop()
            await source.stop()


if __name__ == '__main__':
    unittest.main()

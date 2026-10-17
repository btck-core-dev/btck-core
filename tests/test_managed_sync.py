"""Isolated acceptance of opt-in standby connection management; no mainnet I/O."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from node.blockdag import BlockDAG
from p2p.managed_sync import BoundedPeerConnection, ManagedSyncNetwork, block_locator
from privacy.stealth_address import StealthKeyPair
from tests.pow_helpers import mine_block


class ManagedSyncTests(unittest.IsolatedAsyncioTestCase):
    def network(self, **kwargs):
        return ManagedSyncNetwork(BlockDAG(network='simnet'),
                                  p2p_host='127.0.0.1', p2p_port=0, **kwargs)

    def peer(self, net, key='127.0.0.1:9', handshaken=True):
        writer = Mock()
        writer.is_closing.return_value = False
        writer.wait_closed = AsyncMock()
        peer = BoundedPeerConnection(None, writer, key, net.network_magic, send_timeout=.05)
        peer.handshake_complete = handshaken
        peer.send_msg = AsyncMock()
        net.peers[key] = peer
        return peer

    async def wait_until(self, predicate, timeout=3):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(.01)

    def test_locator_is_bounded_ordered_and_includes_genesis_and_tip(self):
        chain = [str(i) for i in range(500000)]
        locator = block_locator(chain)
        self.assertLessEqual(len(locator), 64)
        self.assertEqual(locator[0], chain[0])
        self.assertEqual(locator[-1], chain[-1])
        self.assertEqual(locator, sorted(set(locator), key=int))
        self.assertGreater(len(locator), 10)
        self.assertEqual(block_locator([]), [])
        self.assertEqual(block_locator(['g']), ['g'])

    def test_invalid_and_implicit_peers_rejected(self):
        with self.assertRaises(ValueError):
            self.network(seed_peers=['127.0.0.1:9'])
        with self.assertRaises(ValueError):
            self.network(managed_peers=['127.0.0.1:0'])
        with self.assertRaises(ValueError):
            self.network(managed_peers=[f'127.0.0.1:{i}' for i in range(1, 10)])

    async def test_default_dials_nothing(self):
        net = self.network()
        with patch('p2p.managed_sync.asyncio.open_connection', new_callable=AsyncMock) as dial:
            await net.start()
            await asyncio.sleep(.02)
            dial.assert_not_awaited()
            await net.stop()

    async def test_reconnect_finishes_retained_dependencies_before_new_history(self):
        net=self.network()
        net.running=True
        peer=self.peer(net,handshaken=False)
        net.orphan_pool['a'*64]=Mock(parents=['b'*64])
        async def retained_roots(_peer):
            _peer.parent_queue.extend(f'{n:064x}' for n in range(1100))
            _peer.requested_blocks.add('b'*64)
        net._resume_orphan_dependencies=retained_roots
        identity={'network':net.network.name,'chain_id':net.network.chain_id,
            'consensus_fingerprint':net.network.consensus_fingerprint,
            'genesis_hash':net.dag.genesis_hash,'version':'1.2.0','blue_score':2000}
        await net._process_msg(peer,{'type':'HANDSHAKE_ACK','payload':identity})
        peer.send_msg.assert_not_awaited()
        # Live announcements must not open a second dependency front while
        # the retained parents occupy the bounded request queue.
        await net._process_msg(peer,{'type':'INV','payload':{'blocks':['c'*64]}})
        self.assertIn('c'*64,peer.deferred_gossip)
        self.assertEqual(len(peer.parent_queue),1100)
        net.orphan_pool.clear();peer.parent_queue.clear();peer.requested_blocks.clear()
        await net._maintenance_once()
        await asyncio.sleep(0)
        peer.send_msg.assert_awaited_once()
        self.assertEqual(peer.send_msg.await_args.args[0],'GET_BLOCKS')
        self.assertTrue(peer.ibd_waiting_page)

    async def test_retained_valid_orphans_recover_over_native_loopback(self):
        from node.blockdag import DAGBlock
        from p2p.p2p_network import P2PNetwork
        source_dag=BlockDAG(network='simnet')
        address=StealthKeyPair().get_public_stealth_address()
        blocks=[]
        for i in range(24):
            block=mine_block(source_dag,parents=source_dag.get_tips(),
                timestamp=1788912100.0+i,
                coinbase_output=StealthKeyPair.generate_one_time_stealth_output(address))
            self.assertTrue(source_dag.add_block(block));blocks.append(block)
        source=P2PNetwork(source_dag,p2p_host='127.0.0.1',p2p_port=0)
        await source.start()
        port=source.server.sockets[0].getsockname()[1]
        net=self.network(managed_peers=[f'127.0.0.1:{port}'],requests_per_second=1000,
                         maintenance_interval=.01)
        for block in blocks[4:]:
            clone=DAGBlock(parents=block.parents,timestamp=block.timestamp,bits=block.bits,
                nonce=block.nonce,coinbase_output=block.coinbase_output,
                transactions=block.transactions,version=block.version,
                payload_hash=block.payload_hash,chain_id=block.chain_id)
            net.orphan_pool[clone.hash]=clone
            missing=set(clone.parents)-set(net.dag.blocks)
            net.orphan_missing[clone.hash]=missing
            for parent in missing:net.orphans_by_parent.setdefault(parent,set()).add(clone.hash)
        try:
            await net.start()
            await self.wait_until(lambda:len(net.dag.blocks)==len(source_dag.blocks),timeout=8)
            self.assertFalse(net.orphan_pool)
            self.assertEqual(net.dag.state_root(),source_dag.state_root())
            self.assertEqual(net.sync_counters['history_waiting_for_dependencies'],1)
            self.assertEqual(net.sync_counters['peer_loop_failed'],0)
            self.assertGreater(net.sync_counters['orphan_accepted'],0)
        finally:
            await net.stop();await source.stop()

    async def test_failure_schedules_exponential_capped_retry(self):
        net = self.network(managed_peers=['127.0.0.1:9'], retry_jitter=0)
        net.running = True
        net.connect_to_peer = AsyncMock(return_value=False)
        with patch('p2p.managed_sync.time.monotonic', return_value=1000):
            for expected in [30, 60, 120, 240, 480, 480]:
                await net._connect_managed('127.0.0.1:9')
                self.assertEqual(net.connection_requests['127.0.0.1:9'].next_attempt,
                                 1000 + expected)
            await net._maintenance_once()
            self.assertFalse(net._connect_tasks)

    async def test_failed_health_gate_and_ban_prevent_dialing(self):
        net = self.network(managed_peers=['127.0.0.1:9'], connection_allowed=lambda: False)
        net.running = True
        await net._maintenance_once()
        self.assertFalse(net._connect_tasks)
        def failing_gate():
            raise OSError('health unavailable')
        net.connection_allowed = failing_gate
        await net._maintenance_once()
        self.assertFalse(net._connect_tasks)
        net.connection_allowed = lambda: True
        net.banned_addrs.add('127.0.0.1')
        await net._maintenance_once()
        self.assertFalse(net._connect_tasks)

    async def test_handshake_timeout_closes_silent_peer(self):
        net = self.network(handshake_timeout=.01)
        peer = self.peer(net, handshaken=False)
        peer.opened_monotonic = time.monotonic() - 1
        await net._maintenance_once()
        peer.writer.close.assert_called_once()
        self.assertEqual(net.sync_counters['handshake_timeout'], 1)

    async def test_idle_reconciliation_uses_existing_wire_messages(self):
        net = self.network()
        net.running = True
        peer = self.peer(net)
        peer.handshaken_monotonic = time.monotonic()
        peer.next_reconcile = 0
        await net._maintenance_once()
        await asyncio.sleep(.01)
        peer.send_msg.assert_awaited_once_with('GET_BLOCKS', {
            'locator': block_locator(net.dag.virtual_selected_parent_chain)})
        self.assertTrue(peer.ibd_waiting_page)
        self.assertIsNotNone(peer.ibd_page_deadline)
        await net._maintenance_once()
        self.assertEqual(peer.send_msg.await_count, 1)
        await net.stop()

    async def test_only_new_admission_postpones_idle_probe_not_duplicate_traffic(self):
        from tests.test_p2p_sync_regressions import wire
        source=BlockDAG(network='simnet')
        address=StealthKeyPair().get_public_stealth_address()
        block=mine_block(source,parents=source.get_tips(),timestamp=1788912100,
                         coinbase_output=StealthKeyPair.generate_one_time_stealth_output(address))
        net=self.network()
        peer=self.peer(net)
        peer.handshaken_monotonic=time.monotonic()
        peer.next_reconcile=0
        await net._process_msg(peer,{'type':'BLOCK','payload':wire(block)})
        deadline=peer.next_reconcile
        self.assertGreater(deadline,time.monotonic()+50)
        await net._maintenance_once()
        self.assertFalse(any(call.args[0]=='GET_BLOCKS' for call in peer.send_msg.await_args_list))
        peer.next_reconcile=0
        await net._process_msg(peer,{'type':'BLOCK','payload':wire(block)})
        self.assertEqual(peer.next_reconcile,0)
        await net.stop()

    async def test_initial_negotiation_uses_exponential_locator(self):
        from p2p.p2p_network import P2P_PROTOCOL_VERSION
        net = self.network()
        peer = self.peer(net, handshaken=False)
        net.dag.virtual_selected_parent_chain = [f'{i:064x}' for i in range(1024)]
        await net._process_msg(peer, {'type': 'HANDSHAKE_ACK', 'payload': {
            'network': net.network.name, 'chain_id': net.network.chain_id,
            'consensus_fingerprint': net.network.consensus_fingerprint,
            'genesis_hash': net.dag.genesis_hash, 'blue_score': 1,
            'version': P2P_PROTOCOL_VERSION}})
        sent = peer.send_msg.await_args_list[0]
        self.assertEqual(sent.args[0], 'GET_BLOCKS')
        locator = sent.args[1]['locator']
        self.assertGreater(len(locator), 10)
        self.assertEqual(locator[0], net.dag.virtual_selected_parent_chain[0])

    async def test_active_history_and_pending_dependencies_are_not_overwritten(self):
        for flag, value in [('ibd_pending', {'f'*64}), ('ibd_waiting_page', True),
                            ('ibd_more', True), ('requested_blocks', {'f'*64}),
                            ('parent_queue', ['f'*64]), ('ibd_queue', ['f'*64]),
                            ('deferred_gossip', {'f'*64: None})]:
            net = self.network()
            peer = self.peer(net)
            peer.handshaken_monotonic, peer.next_reconcile = time.monotonic(), 0
            setattr(peer, flag, value)
            await net._maintenance_once()
            self.assertFalse(net._probe_tasks, flag)
            peer.send_msg.assert_not_awaited()

    async def test_stable_connection_resets_failures_not_tcp_connect_alone(self):
        net = self.network(managed_peers=['127.0.0.1:9'], stable_connection_seconds=10)
        request = net.connection_requests['127.0.0.1:9']
        request.failures = 5
        peer = self.peer(net)
        await net._maintenance_once()
        self.assertEqual(request.failures, 5)
        peer.handshaken_monotonic = time.monotonic() - 11
        await net._maintenance_once()
        self.assertEqual(request.failures, 0)

    async def test_dial_timeout_is_bounded_and_duplicate_dial_suppressed(self):
        net = self.network(connect_timeout=.02)
        net.running = True
        async def hung(*args, **kwargs):
            await asyncio.Event().wait()
        with patch('p2p.managed_sync.asyncio.open_connection', side_effect=hung) as dial:
            task = asyncio.create_task(net.connect_to_peer('127.0.0.1:9'))
            await asyncio.sleep(.005)
            self.assertFalse(await net.connect_to_peer('127.0.0.1:9'))
            self.assertFalse(await task)
            self.assertEqual(dial.call_count, 1)
        self.assertFalse(net.peers)
        self.assertFalse(net._dialing)

    async def test_handshake_write_failure_does_not_leave_phantom_peer(self):
        net = self.network(send_timeout=.01)
        net.running = True
        writer = Mock()
        writer.drain = AsyncMock(side_effect=OSError('broken transport'))
        with patch('p2p.managed_sync.asyncio.open_connection',
                   new=AsyncMock(return_value=(Mock(), writer))):
            self.assertFalse(await net.connect_to_peer('127.0.0.1:9'))
        self.assertFalse(net.peers)
        writer.close.assert_called()

    async def test_slow_relay_does_not_delay_rpc_or_healthy_peer(self):
        net = self.network(send_timeout=.03)
        net.running = True
        slow, healthy = self.peer(net, '127.0.0.1:8'), self.peer(net)
        async def blocked(*args):
            await asyncio.Event().wait()
        slow.send_msg.side_effect = blocked
        await asyncio.wait_for(net.broadcast_block('f'*64), .01)
        await self.wait_until(lambda: healthy.send_msg.await_count == 1)
        await self.wait_until(lambda: slow.writer.close.called)
        self.assertEqual(net.sync_counters['relay_failed'], 1)
        await net.stop()

    async def test_relay_backlog_is_bounded(self):
        net = self.network(relay_queue_limit=2)
        net.running = True
        peer = self.peer(net)
        # No yield: simulate a burst before the worker gets a scheduling slot.
        for i in range(3):
            await net.broadcast_block(f'{i:064x}')
        self.assertEqual(net._relay_queues[id(peer)].qsize(), 2)
        peer.writer.close.assert_called_once()
        self.assertEqual(net.sync_counters['relay_capacity'], 1)
        await net.stop()

    async def test_loopback_reconnects_without_node_restart(self):
        source = self.network(maintenance_interval=.01)
        await source.start()
        port = source.server.sockets[0].getsockname()[1]
        target = f'127.0.0.1:{port}'
        follower = self.network(managed_peers=[target], maintenance_interval=.01,
                                reconnect_initial=.03, reconnect_max=.1, retry_jitter=0)
        await follower.start()
        try:
            await self.wait_until(lambda: target in follower.peers
                                  and follower.peers[target].handshake_complete)
            old = follower.peers[target]
            old.writer.close()
            await self.wait_until(lambda: target in follower.peers
                                  and follower.peers[target] is not old
                                  and follower.peers[target].handshake_complete)
            self.assertGreaterEqual(follower.sync_counters['reconnect_scheduled'], 1)
            self.assertEqual(len(follower.peers), 1)
        finally:
            await follower.stop()
            await source.stop()

    async def test_old_source_interrupted_download_auto_recovers_and_persists(self):
        from node.daemon import BTCKDaemon
        path = Path(__file__).parent / 'fixtures/p2p_network_seedb_20261015.py'
        spec = importlib.util.spec_from_file_location('managed_follow_old_source', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        dag = BlockDAG(network='simnet')
        output = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        for i in range(24):
            block = mine_block(dag, parents=dag.get_tips(), timestamp=1788912100+i,
                               coinbase_output=output)
            self.assertTrue(dag.add_block(block))
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
        source = InterruptedSource(dag, p2p_host='127.0.0.1', p2p_port=0)
        await source.start()
        endpoint = f'127.0.0.1:{source.server.sockets[0].getsockname()[1]}'
        try:
            with tempfile.TemporaryDirectory() as root:
                node = BTCKDaemon(network='simnet', data_root=root, persistent=True)
                receiver = ManagedSyncNetwork(
                    node.dag, p2p_host='127.0.0.1', p2p_port=0,
                    managed_peers=[endpoint], block_acceptor=node.accept_block,
                    requests_per_second=100, reconnect_initial=.03,
                    reconnect_max=.1, retry_jitter=0, maintenance_interval=.01)
                await receiver.start()
                try:
                    await self.wait_until(lambda: set(node.dag.blocks) == set(dag.blocks), 8)
                    self.assertTrue(source.interrupted)
                    self.assertGreaterEqual(receiver.sync_counters['reconnect_scheduled'], 1)
                    self.assertEqual(node.dag.state_root(), dag.state_root())
                    self.assertEqual(node.dag.utxo_set.total_value_sats(), node.dag.issued_supply_sats)
                    with node.chain_store._connect() as conn:
                        persisted = {r[0] for r in conn.execute('SELECT block_hash FROM accepted_blocks')}
                    self.assertEqual(persisted, set(dag.blocks)-{dag.genesis_hash})
                finally:
                    await receiver.stop()
        finally:
            await source.stop()

    async def test_loopback_reconciles_unannounced_block_and_keeps_consensus(self):
        source = self.network(maintenance_interval=.01)
        await source.start()
        target = f'127.0.0.1:{source.server.sockets[0].getsockname()[1]}'
        follower = self.network(managed_peers=[target], maintenance_interval=.01,
                                reconcile_interval=.06, requests_per_second=100)
        await follower.start()
        try:
            await self.wait_until(lambda: target in follower.peers
                                  and follower.peers[target].handshake_complete)
            address = StealthKeyPair().get_public_stealth_address()
            block = mine_block(source.dag, parents=source.dag.get_tips(),
                               timestamp=1788912100.0,
                               coinbase_output=StealthKeyPair.generate_one_time_stealth_output(address))
            self.assertTrue(source.dag.add_block(block))
            # Deliberately omit broadcast: no transport-level re-announcement.
            await self.wait_until(lambda: block.hash in follower.dag.blocks)
            self.assertEqual(source.dag.state_root(), follower.dag.state_root())
            self.assertEqual(follower.dag.utxo_set.total_value_sats(), follower.dag.issued_supply_sats)
            self.assertGreaterEqual(follower.sync_counters['history_reconcile'], 1)
        finally:
            await follower.stop()
            await source.stop()


if __name__ == '__main__':
    unittest.main()

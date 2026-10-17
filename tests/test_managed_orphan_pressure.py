"""Bounded native dependency traversal deeper than the resident orphan cache."""
import collections
import unittest
import tempfile
from unittest.mock import AsyncMock, Mock, patch

from node.blockdag import BlockDAG
from p2p.managed_sync import ManagedSyncNetwork, BoundedPeerConnection
from tests.pow_helpers import mine_block
from tests.test_p2p_sync_regressions import wire


class ManagedOrphanPressureTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, depth, cap, branches=1, disconnect=False, byte_cap=None, persistent=False):
        source = BlockDAG(network='simnet')
        blocks = {}
        heads = []
        cb = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        for branch in range(branches):
            parent = source.genesis_hash
            for index in range(depth):
                b = mine_block(source, parents=[parent],
                    timestamp=1788912100+index+branch/10, coinbase_output=cb)
                self.assertTrue(source.add_block(b))
                blocks[b.hash] = wire(b)
                parent = b.hash
            heads.append(parent)
        if branches > 1:
            b = mine_block(source, parents=heads, timestamp=1788912100+depth+1, coinbase_output=cb)
            self.assertTrue(source.add_block(b)); blocks[b.hash] = wire(b)
            heads = [b.hash]
        acceptor = None
        if persistent:
            from node.daemon import BTCKDaemon
            root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
            daemon = BTCKDaemon(network='simnet', data_root=root.name, persistent=True)
            target, acceptor = daemon.dag, daemon.accept_block
        else:
            target = BlockDAG(network='simnet')
        net = ManagedSyncNetwork(target, block_acceptor=acceptor, requests_per_second=100000)
        pending = collections.deque()
        pages = []
        def peer():
            writer = Mock(); writer.is_closing.return_value = False
            p = BoundedPeerConnection(None, writer, '127.0.0.1:9', net.network_magic, send_timeout=1)
            p.handshake_complete = True
            async def send(kind, payload):
                if kind == 'GET_BLOCK': pending.append(payload['hash'])
                if kind == 'GET_BLOCKS':
                    # A new cursor is legal only after every page member commits.
                    self.assertTrue(all(h in target.blocks for h in heads))
                    self.assertFalse(net._orphan_recovery_roots)
                    pages.append(payload)
            p.send_msg = AsyncMock(side_effect=send)
            net.peers[p.peer_addr] = p
            return p
        p = peer(); reconnected = False
        with patch('p2p.managed_sync.MAX_ORPHANS', cap), patch('p2p.p2p_network.MAX_ORPHANS', cap), \
             patch('p2p.managed_sync.MAX_ORPHAN_BYTES', byte_cap or 32*1024*1024):
            await net._process_msg(p, {'type':'INV','payload':{'blocks':heads,'more':True}})
            for _ in range(depth*branches*30):
                if all(h in target.blocks for h in heads): break
                self.assertTrue(pending, 'dependency traversal stalled without a request')
                h = pending.popleft()
                await net._process_msg(p, {'type':'BLOCK','payload':blocks[h]})
                self.assertLessEqual(len(net.orphan_pool), cap)
                self.assertLessEqual(net.orphan_bytes, byte_cap or 32*1024*1024)
                self.assertLessEqual(len(net._orphan_recovery_roots), 3000)
                self.assertEqual(sum(net.orphan_sizes.values()), net.orphan_bytes)
                if disconnect and net.sync_counters['orphan_cache_rebase'] and not reconnected:
                    pending.clear(); p.writer.is_closing.return_value = True
                    net._outstanding.clear(); net.peers.clear()
                    p = peer()
                    await net._begin_history_sync(p)
                    self.assertTrue(p.history_after_dependencies)
                    await net._resume_orphan_dependencies(p)
                    reconnected = True
            else: self.fail('bounded traversal did not converge')
        self.assertEqual(source.state_root(), target.state_root())
        self.assertEqual(set(source.blocks), set(target.blocks))
        self.assertFalse(net.orphan_pool)
        self.assertFalse(net.orphans_by_parent)
        self.assertFalse(net.orphan_missing)
        self.assertEqual(net.orphan_bytes, 0)
        self.assertFalse(net._orphan_recovery_roots)
        self.assertGreater(net.sync_counters['orphan_cache_rebase'], 0)
        self.assertEqual(net.sync_counters['peer_loop_failed'], 0)
        if not disconnect: self.assertTrue(pages)
        if persistent:
            with daemon.chain_store._connect() as db:
                hashes = {row[0] for row in db.execute('SELECT block_hash FROM accepted_blocks')}
            self.assertEqual(hashes, set(source.blocks)-{source.genesis_hash})
        return net

    async def test_real_pow_chain_beyond_production_1000_limit(self):
        await self.exercise(1105, 1000)

    async def test_multiple_rebases_and_merge_dependencies(self):
        await self.exercise(30, 8, branches=2)

    async def test_reconnect_retains_retry_roots_after_rebase(self):
        await self.exercise(30, 8, branches=2, disconnect=True)

    async def test_byte_pressure_also_recovers_with_bounded_payloads(self):
        await self.exercise(30, 1000, byte_cap=3000)

    async def test_pressure_recovery_commits_every_block_to_real_journal(self):
        await self.exercise(30, 8, branches=2, persistent=True)

    async def test_native_tcp_pressure_disconnect_and_durable_recovery(self):
        import asyncio
        from node.daemon import BTCKDaemon
        from node.blockdag import DAGBlock
        from p2p.p2p_network import P2PNetwork
        source_dag = BlockDAG(network='simnet')
        cb = (bytes.fromhex('02'+'77'*32), bytes.fromhex('03'+'88'*32))
        blocks = []
        for i in range(40):
            b = mine_block(source_dag, parents=source_dag.get_tips(),
                           timestamp=1788912100+i, coinbase_output=cb)
            self.assertTrue(source_dag.add_block(b)); blocks.append(b)
        source = P2PNetwork(source_dag, p2p_host='127.0.0.1', p2p_port=0)
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        destination = BTCKDaemon(network='simnet', data_root=root.name, persistent=True)
        await source.start()
        port = source.server.sockets[0].getsockname()[1]
        net = ManagedSyncNetwork(destination.dag, block_acceptor=destination.accept_block,
            p2p_host='127.0.0.1', p2p_port=0, managed_peers=[f'127.0.0.1:{port}'],
            requests_per_second=200, maintenance_interval=.005,
            reconnect_initial=.02, reconnect_max=.04, retry_jitter=0)
        for b in blocks[-8:]:
            clone = DAGBlock(parents=b.parents, timestamp=b.timestamp, bits=b.bits,
                nonce=b.nonce, coinbase_output=b.coinbase_output, transactions=b.transactions,
                version=b.version, payload_hash=b.payload_hash, chain_id=b.chain_id)
            net.orphan_pool[b.hash] = clone
            missing = set(b.parents)-set(destination.dag.blocks)
            net.orphan_missing[b.hash] = missing
            for h in missing: net.orphans_by_parent.setdefault(h,set()).add(b.hash)
        disconnected = False
        try:
            with patch('p2p.managed_sync.MAX_ORPHANS',8), patch('p2p.p2p_network.MAX_ORPHANS',8):
                await net.start()
                async with asyncio.timeout(15):
                    while len(destination.dag.blocks) != len(source_dag.blocks):
                        self.assertLessEqual(len(net.orphan_pool),8)
                        if net._orphan_recovery_roots and not disconnected:
                            for p in list(net.peers.values()): p.writer.close()
                            disconnected = True
                        await asyncio.sleep(.005)
            self.assertTrue(disconnected)
            self.assertGreater(net.sync_counters['orphan_cache_rebase'],0)
            self.assertGreaterEqual(net.sync_counters['history_waiting_for_dependencies'],2)
            self.assertEqual(source_dag.state_root(),destination.dag.state_root())
            with destination.chain_store._connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM accepted_blocks').fetchone()[0],40)
            self.assertFalse(net._orphan_recovery_roots)
        finally:
            await net.stop(); await source.stop()

    def gossip_pair(self):
        net = ManagedSyncNetwork(BlockDAG(network='simnet'), requests_per_second=100000)
        writer = Mock(); writer.is_closing.return_value = False
        peer = BoundedPeerConnection(None, writer, '127.0.0.1:9', net.network_magic, send_timeout=1)
        peer.handshake_complete = True; peer.send_msg = AsyncMock()
        net.peers[peer.peer_addr] = peer
        return net,peer

    async def test_gossip_waits_for_existing_live_parent_dependencies(self):
        net,peer = self.gossip_pair()
        peer.requested_blocks.add('a'*64)
        await net._process_msg(peer, {'type':'INV','payload':{'blocks':['b'*64]}})
        self.assertIn('b'*64,peer.deferred_gossip)
        self.assertNotIn('b'*64,peer.requested_blocks)

    async def test_deferred_live_gossip_opens_one_dependency_front(self):
        net,peer = self.gossip_pair()
        peer.deferred_gossip = dict.fromkeys(['a'*64,'b'*64,'c'*64])
        await net._release_deferred_gossip(peer)
        self.assertEqual(peer.requested_blocks,{'a'*64})
        self.assertEqual(set(peer.deferred_gossip),{'b'*64,'c'*64})

    async def test_recovery_gossip_cannot_bypass_handshake(self):
        net,peer = self.gossip_pair(); peer.handshake_complete = False
        net._orphan_recovery_roots['a'*64] = None
        await net._process_msg(peer, {'type':'INV','payload':{'blocks':['b'*64]}})
        peer.writer.close.assert_called_once()
        self.assertFalse(peer.deferred_gossip)

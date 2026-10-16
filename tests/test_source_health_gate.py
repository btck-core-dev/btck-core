import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from p2p.source_health_gate import SourceHealthGate
from tests import test_managed_sync as helpers


class HealthGateTests(unittest.TestCase):
    def test_missing_invalid_expired_and_revoked_leases_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'health.json'
            gate = SourceHealthGate(path, clock=lambda: 100)
            self.assertFalse(gate())
            for value in ('invalid', '{}', '[]', json.dumps({'allowed': True,
                    'network': 'mainnet', 'issued_at': 99, 'expires_at': 200})):
                path.write_text(value)
                self.assertFalse(gate())
            value = {'allowed': True, 'network': 'mainnet', 'issued_at': 90, 'expires_at': 150}
            path.write_text(json.dumps(value))
            self.assertTrue(gate())
            for key, bad in (('allowed', False), ('network', 'simnet'),
                             ('issued_at', 101), ('expires_at', 99)):
                path.write_text(json.dumps(dict(value, **{key: bad})))
                self.assertFalse(gate())


class ActiveTransferGuardTests(unittest.IsolatedAsyncioTestCase):
    network = helpers.ManagedSyncTests.network
    peer = helpers.ManagedSyncTests.peer
    wait_until = helpers.ManagedSyncTests.wait_until

    async def test_real_tcp_gate_revocation_closes_and_suppresses_reconnect(self):
        allowed=True
        source=self.network(maintenance_interval=.01)
        await source.start()
        target=f'127.0.0.1:{source.server.sockets[0].getsockname()[1]}'
        follower=self.network(managed_peers=[target],connection_allowed=lambda: allowed,
                              reconnect_initial=.03,reconnect_max=.1,retry_jitter=0,
                              maintenance_interval=.01)
        await follower.start()
        try:
            await self.wait_until(lambda: target in follower.peers and follower.peers[target].handshake_complete)
            allowed=False
            await self.wait_until(lambda: target not in follower.peers)
            await asyncio.sleep(.12)
            self.assertFalse(follower.peers)
            self.assertFalse(follower._dialing)
            self.assertGreaterEqual(follower.sync_counters['health_gate_disconnect'],1)
            allowed=True
            await self.wait_until(lambda: target in follower.peers and follower.peers[target].handshake_complete)
        finally:
            await follower.stop()
            await source.stop()

    async def test_revocation_closes_existing_transfer_and_blocks_requests(self):
        allowed = True
        net = self.network(connection_allowed=lambda: allowed)
        peer = self.peer(net)
        allowed = False
        await net._maintenance_once()
        peer.writer.close.assert_called_once()
        self.assertFalse(await net._request_block(peer, 'f'*64))
        peer.send_msg.assert_not_awaited()
        self.assertEqual(net.sync_counters['health_gate_disconnect'], 1)

    async def test_failed_guard_cancels_a_pending_dial(self):
        net = self.network(connection_allowed=lambda: False)
        task = asyncio.create_task(asyncio.Event().wait())
        net._connect_tasks['127.0.0.1:9'] = task
        await net._maintenance_once()
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled())

    async def test_invalid_peer_file_removes_retry_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'peers.json'
            net = self.network(managed_peers=['127.0.0.1:9'], managed_peer_file=path)
            peer = self.peer(net)
            path.write_text('{"network":"mainnet","peers":[]}')
            net._refresh_managed_peers()
            self.assertFalse(net.connection_requests)
            peer.writer.close.assert_called_once()

    async def test_explicit_valid_peer_file_registers_bounded_requests(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'peers.json'
            path.write_text('{"network":"simnet","peers":["127.0.0.1:9"]}')
            net = self.network(managed_peer_file=path)
            net._refresh_managed_peers()
            self.assertEqual(set(net.connection_requests), {'127.0.0.1:9'})
            path.write_text('invalid')
            net._refresh_managed_peers()
            self.assertFalse(net.connection_requests)

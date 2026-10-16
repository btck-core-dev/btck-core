"""Opt-in, paced standby following with an explicit connection manager.

Borrows scheduling patterns, not wire/consensus rules, from Kaspa's connection
manager and separate IBD/relay flows. No peers are discovered or dialed unless
explicitly configured. This module is NOT wired into either deployment.
"""
import asyncio
import random
import time
from dataclasses import dataclass

from p2p.p2p_network import (
    BLOCK_REQUEST_TIMEOUT, MAX_LOCATOR_HASHES, MAX_MESSAGE_BYTES, MAX_PEERS,
    P2P_PROTOCOL_VERSION, PeerConnection,
)
from p2p.standby_sync import StandbySyncNetwork


@dataclass
class ConnectionRequest:
    next_attempt: float = 0.0
    failures: int = 0


def block_locator(chain):
    """Oldest-to-newest locator, compatible with existing GET_BLOCKS handlers.

    Keep the nearest ten ancestors, then exponentially spaced ancestors and
    genesis. A fork deeper than ten blocks must not force a genesis rescan.
    """
    if not chain:
        return []
    indexes = []
    index, step = len(chain) - 1, 1
    while index > 0 and len(indexes) < MAX_LOCATOR_HASHES - 1:
        indexes.append(index)
        if len(indexes) >= 10:
            step *= 2
        index = max(0, index - step)
    indexes.append(0)
    return [chain[i] for i in reversed(indexes)]


class BoundedPeerConnection(PeerConnection):
    def __init__(self, *args, send_timeout, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_timeout = send_timeout
        self.opened_monotonic = time.monotonic()
        self.handshaken_monotonic = None
        self.next_reconcile = None

    async def send_msg(self, msg_type, payload):
        try:
            await asyncio.wait_for(super().send_msg(msg_type, payload), self.send_timeout)
        except (Exception, asyncio.CancelledError):
            self.writer.close()
            raise


class ManagedSyncNetwork(StandbySyncNetwork):
    """A disabled-by-default connection policy for an isolated standby.

    Requests stay globally paced/deduplicated by StandbySyncNetwork. A single
    supervisor owns explicit permanent peers, with exponential retry, jitter,
    handshake deadlines and periodic legacy-protocol history reconciliation.
    A failed health gate closes active transfers and cancels pending dialing.
    It is not a replacement for source monitoring or deployment approval.
    """
    def __init__(self, *args, managed_peers=(), reconnect_initial=30.0,
                 reconnect_max=480.0, retry_jitter=0.2, connect_timeout=10.0,
                 handshake_timeout=15.0, reconcile_interval=60.0,
                 send_timeout=5.0, stable_connection_seconds=60.0,
                 maintenance_interval=1.0, relay_queue_limit=128,
                 connection_allowed=None, managed_peer_file=None,
                 inbound_allowed_hosts=None, **kwargs):
        if kwargs.pop('seed_peers', None):
            raise ValueError('use explicit managed_peers, not one-shot seed_peers')
        if (min(reconnect_initial, reconnect_max, connect_timeout, handshake_timeout,
                reconcile_interval, send_timeout, stable_connection_seconds,
                maintenance_interval) <= 0 or reconnect_max < reconnect_initial
                or not 0 <= retry_jitter <= 1 or relay_queue_limit < 1):
            raise ValueError('invalid managed following limits')
        targets = list(dict.fromkeys(managed_peers))
        if len(targets) > 8:
            raise ValueError('at most eight explicit standby peers are allowed')
        for target in targets:
            self._split_address(target)
        super().__init__(*args, seed_peers=[], **kwargs)
        self.connection_requests = {target: ConnectionRequest() for target in targets}
        self.reconnect_initial, self.reconnect_max = reconnect_initial, reconnect_max
        self.retry_jitter = retry_jitter
        self.connect_timeout, self.handshake_timeout = connect_timeout, handshake_timeout
        self.reconcile_interval, self.send_timeout = reconcile_interval, send_timeout
        self.stable_connection_seconds = stable_connection_seconds
        self.maintenance_interval, self.relay_queue_limit = maintenance_interval, relay_queue_limit
        self.connection_allowed = connection_allowed or (lambda: True)
        self.managed_peer_file = managed_peer_file
        self.inbound_allowed_hosts = inbound_allowed_hosts
        self._dialing = set()
        self._connect_tasks = {}
        self._probe_tasks = {}
        self._relay_queues = {}
        self._relay_tasks = {}

    @staticmethod
    def _split_address(address):
        host, port = address.rsplit(':', 1)
        if not host or any(c.isspace() for c in host) or not 1 <= int(port) <= 65535:
            raise ValueError('invalid peer address')
        return host.strip('[]'), int(port)

    def _retry_delay(self, failures):
        ceiling = min(self.reconnect_max, self.reconnect_initial * 2 ** min(failures, 16))
        # Positive jitter avoids retrying earlier than the configured minimum.
        return min(self.reconnect_max, ceiling * (1 + random.uniform(0, self.retry_jitter)))

    def _initial_block_locator(self):
        return block_locator(self.dag.virtual_selected_parent_chain)

    def _schedule_retry(self, target):
        request = self.connection_requests[target]
        delay = self._retry_delay(request.failures)
        request.failures = min(request.failures + 1, 16)
        request.next_attempt = time.monotonic() + delay
        self._sync_event('reconnect_scheduled', delay_seconds=delay,
                         attempts=request.failures, target=target)

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.append(task)
        return task

    async def start(self):
        await super().start()
        self._spawn(self._connection_manager())

    async def _connection_manager(self):
        while self.running:
            await self._maintenance_once()
            await asyncio.sleep(self.maintenance_interval)

    def _health_allowed(self):
        try:
            return self.connection_allowed() is True
        except Exception:
            return False

    async def _maintenance_once(self):
        now = time.monotonic()
        if self.managed_peer_file is not None:
            self._refresh_managed_peers()
        self._tasks[:] = [task for task in self._tasks if not task.done()]
        for target, task in list(self._connect_tasks.items()):
            if task.done():
                del self._connect_tasks[target]
        if not self._health_allowed():
            for task in self._connect_tasks.values():
                task.cancel()
            for peer in list(self.peers.values()):
                if not peer.writer.is_closing():
                    self._sync_event('health_gate_disconnect', peer)
                    peer.writer.close()
            return
        for peer in list(self.peers.values()):
            if peer.writer.is_closing():
                continue
            if not peer.handshake_complete:
                if now - peer.opened_monotonic >= self.handshake_timeout:
                    self._sync_event('handshake_timeout', peer)
                    peer.writer.close()
                continue
            if peer.handshaken_monotonic is None:
                peer.handshaken_monotonic = now
                peer.next_reconcile = now + self.reconcile_interval
            request = self.connection_requests.get(peer.peer_addr)
            if request and now - peer.handshaken_monotonic >= self.stable_connection_seconds:
                request.failures = 0
            busy = (peer.ibd_pending or peer.ibd_waiting_page or peer.ibd_more
                    or peer.requested_blocks or peer.parent_queue or peer.ibd_queue
                    or peer.deferred_gossip)
            if not busy and now >= peer.next_reconcile and id(peer) not in self._probe_tasks:
                peer.next_reconcile = now + self.reconcile_interval
                self._probe_tasks[id(peer)] = self._spawn(self._reconcile(peer))
        for target, request in self.connection_requests.items():
            host, _ = self._split_address(target)
            if (target not in self.peers and target not in self._connect_tasks
                    and target not in self._dialing and host not in self.banned_addrs
                    and now >= request.next_attempt and len(self.peers) < MAX_PEERS):
                self._connect_tasks[target] = self._spawn(self._connect_managed(target))

    def _refresh_managed_peers(self):
        import json
        from pathlib import Path
        try:
            path = Path(self.managed_peer_file)
            if path.stat().st_size > 4096:
                raise ValueError('oversized peer policy')
            value = json.loads(path.read_text())
            targets = value['peers']
            if (value.get('network') != self.network.name or not isinstance(targets, list)
                    or len(targets) > 8 or any(not isinstance(t, str) for t in targets)):
                raise ValueError('invalid managed peer policy')
            for target in targets:
                self._split_address(target)
        except (OSError, ValueError, KeyError, TypeError):
            targets = []  # Missing/invalid configuration must never retain dialing.
        for target in set(self.connection_requests)-set(targets):
            self.connection_requests.pop(target)
            task = self._connect_tasks.get(target)
            if task:
                task.cancel()
            if target in self.peers:
                self.peers[target].writer.close()
        for target in targets:
            self.connection_requests.setdefault(target, ConnectionRequest())

    async def _connect_managed(self, target):
        if not await self.connect_to_peer(target) and self.running:
            self._schedule_retry(target)

    async def connect_to_peer(self, peer_str):
        """One bounded attempt; only the manager may schedule another attempt."""
        host, port = self._split_address(peer_str)
        if (not self.running or not self._health_allowed()
                or peer_str in self.peers or peer_str in self._dialing
                or host in self.banned_addrs
                or len(self.peers) + len(self._dialing) >= MAX_PEERS):
            return False
        self._dialing.add(peer_str)
        writer = peer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, limit=MAX_MESSAGE_BYTES + 1),
                self.connect_timeout)
            if not self.running or not self._health_allowed() or len(self.peers) >= MAX_PEERS:
                return False
            peer = BoundedPeerConnection(reader, writer, peer_str, self.network_magic,
                                         send_timeout=self.send_timeout)
            self.peers[peer_str] = peer
            tip = self.dag.blocks[self.dag.selected_tip]
            await peer.send_msg('HANDSHAKE', {
                'network': self.network.name, 'chain_id': self.network.chain_id,
                'consensus_fingerprint': self.network.consensus_fingerprint,
                'genesis_hash': self.dag.genesis_hash, 'blue_score': tip.blue_score,
                'version': P2P_PROTOCOL_VERSION,
            })
            self._spawn(self._peer_listen_loop(peer))
            return True
        except (Exception, asyncio.CancelledError) as exc:
            if peer is not None and self.peers.get(peer_str) is peer:
                del self.peers[peer_str]
            if writer is not None:
                writer.close()
            self._sync_event('connect_failed', error=type(exc).__name__, target=peer_str)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return False
        finally:
            if writer is not None and (peer is None or self.peers.get(peer_str) is not peer):
                writer.close()
            self._dialing.discard(peer_str)

    async def _handle_incoming(self, reader, writer):
        address = writer.get_extra_info('peername')
        if (not address or not self._health_allowed()
                or (self.inbound_allowed_hosts is not None
                    and address[0] not in self.inbound_allowed_hosts)
                or address[0] in self.banned_addrs or len(self.peers) >= MAX_PEERS):
            writer.close()
            return
        key = f'{address[0]}:{address[1]}'
        peer = BoundedPeerConnection(reader, writer, key, self.network_magic,
                                     send_timeout=self.send_timeout)
        self.peers[key] = peer
        self._spawn(self._peer_listen_loop(peer))

    async def _peer_listen_loop(self, peer):
        try:
            await super()._peer_listen_loop(peer)
        finally:
            for tasks in (self._probe_tasks, self._relay_tasks):
                task = tasks.pop(id(peer), None)
                if task:
                    task.cancel()
            self._relay_queues.pop(id(peer), None)
            if self.running and peer.peer_addr in self.connection_requests:
                self._schedule_retry(peer.peer_addr)

    async def _reconcile(self, peer):
        try:
            if not self._health_allowed():
                peer.writer.close()
                return
            # This is the existing wire protocol, not an unnegotiated heartbeat.
            peer.ibd_waiting_page = True
            peer.ibd_page_deadline = time.monotonic() + BLOCK_REQUEST_TIMEOUT
            await peer.send_msg('GET_BLOCKS', {
                'locator': block_locator(self.dag.virtual_selected_parent_chain)})
            self._sync_event('history_reconcile', peer)
        except Exception as exc:
            self._sync_event('reconcile_failed', peer, error=type(exc).__name__)
            peer.writer.close()
        finally:
            self._probe_tasks.pop(id(peer), None)

    async def _process_msg(self, peer, message):
        before = len(self.dag.blocks)
        await super()._process_msg(peer, message)
        if len(self.dag.blocks) > before:
            # Reconcile stalled progress, not a healthy live stream. Legacy
            # peers omit an empty terminal INV; probing them at the live tip
            # would otherwise defer good gossip then force a false timeout.
            # Mere traffic/duplicate hints MUST NOT postpone this deadline.
            peer.next_reconcile = time.monotonic() + self.reconcile_interval

    async def broadcast_block(self, block_hash):
        """Queue bounded relay hints; a slow peer must not hold submitBlock RPC."""
        if not self._health_allowed():
            return
        for peer in list(self.peers.values()):
            if not peer.handshake_complete or peer.writer.is_closing():
                continue
            queue = self._relay_queues.setdefault(id(peer), asyncio.Queue(self.relay_queue_limit))
            try:
                queue.put_nowait(block_hash)
            except asyncio.QueueFull:
                self._sync_event('relay_capacity', peer)
                peer.writer.close()
                continue
            if id(peer) not in self._relay_tasks:
                self._relay_tasks[id(peer)] = self._spawn(self._relay(peer, queue))

    async def _relay(self, peer, queue):
        try:
            while (not queue.empty() and not peer.writer.is_closing()
                   and self._health_allowed()):
                block_hash = queue.get_nowait()
                await asyncio.wait_for(peer.send_msg('INV', {'blocks': [block_hash]}),
                                       self.send_timeout)
        except Exception as exc:
            self._sync_event('relay_failed', peer, error=type(exc).__name__)
            peer.writer.close()
        finally:
            self._relay_tasks.pop(id(peer), None)

    async def _request_block(self, peer, block_hash):
        if not self._health_allowed():
            peer.writer.close()
            return False
        return await super()._request_block(peer, block_hash)

    async def stop(self):
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        peers = list(self.peers.values())
        for peer in peers:
            peer.writer.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        async def close(peer):
            try:
                await asyncio.wait_for(peer.writer.wait_closed(), self.send_timeout)
            except Exception:
                pass
        await asyncio.gather(*(close(peer) for peer in peers))
        self._tasks.clear()
        self._connect_tasks.clear()
        self._probe_tasks.clear()
        self._relay_tasks.clear()
        self._relay_queues.clear()
        self.peers.clear()

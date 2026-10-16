"""Opt-in standby synchronization policy; not enabled on production nodes.

Pace all block requests (including missing parents), bound outstanding requests
globally, and disconnect on timeout. Reconnection is an operator decision.
"""
import asyncio
import time

from p2p.p2p_network import P2PNetwork


class StandbySyncNetwork(P2PNetwork):
    def __init__(self, *args, requests_per_second=2.0, request_timeout=30.0,
                 max_outstanding=32, **kwargs):
        if requests_per_second <= 0 or request_timeout <= 0 or max_outstanding < 16:
            raise ValueError("invalid standby synchronization limits")
        super().__init__(*args, **kwargs)
        self.requests_per_second = requests_per_second
        self.request_timeout = request_timeout
        self.max_outstanding = max_outstanding
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._outstanding = {}

    def _expire_requests(self):
        now = time.monotonic()
        for key, (peer, deadline) in list(self._outstanding.items()):
            if peer.writer.is_closing() or key[1] not in peer.requested_blocks:
                self._outstanding.pop(key, None)
            elif now >= deadline:
                peer.writer.close()
                self._outstanding.pop(key, None)

    async def _watch_requests(self):
        while self.running:
            self._expire_requests()
            await asyncio.sleep(min(1.0, self.request_timeout / 2))

    async def start(self):
        await super().start()
        self._tasks.append(asyncio.create_task(self._watch_requests()))

    async def _request_block(self, peer, block_hash):
        async with self._request_lock:
            self._expire_requests()
            if peer.writer.is_closing():
                return False
            if block_hash in self.dag.blocks or block_hash in peer.requested_blocks:
                return False
            # Cross-peer duplicate requests are suppressed as well.
            if any(key[1] == block_hash for key in self._outstanding):
                return False
            if len(self._outstanding) >= self.max_outstanding:
                # Fail closed rather than accumulating an unbounded parent queue.
                peer.writer.close()
                return False
            await asyncio.sleep(max(0.0, self._next_request_at - time.monotonic()))
            if peer.writer.is_closing():
                return False
            self._next_request_at = time.monotonic() + 1.0 / self.requests_per_second
            requested = await asyncio.wait_for(
                super()._request_block(peer, block_hash), timeout=self.request_timeout
            )
            if requested:
                self._outstanding[(id(peer), block_hash)] = (
                    peer, time.monotonic() + self.request_timeout
                )
            return requested

    async def _complete_block_request(self, peer, block_hash):
        self._outstanding.pop((id(peer), block_hash), None)
        await super()._complete_block_request(peer, block_hash)

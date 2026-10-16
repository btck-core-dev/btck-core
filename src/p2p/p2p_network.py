"""
BTC-K High-Speed P2P Gossip Network Engine (P1-04 / Chapter 7)
--------------------------------------------------------------
Features:
1. Per-network Wire Protocol Envelope and max size limit (2 MB).
2. Genesis Block Hash validation and Peer Handshakes.
3. Peer Scoring & Automated Ban List for misbehaving/corrupt peers.
4. Initial Block Download (IBD) with locator hashes and INV sync.
5. Orphan Block Pool & Recursive Missing Parent Resolution.
6. Full Block Propagation with Transaction Decoupling & StealthTransaction support.
7. Bi-directional Gossip & Relay across active peer mesh.
"""

import asyncio
import json
import logging
import time
from collections import Counter, deque
from typing import Callable, Dict, List, Set, Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from node.blockdag import BlockDAG, DAGBlock, transaction_commitment
from consensus.pow import hash_meets_target
from privacy.wallet_engine import transaction_from_dict, transaction_to_dict

logger = logging.getLogger("btck-p2p")

# Legacy export for callers that imported it directly. Runtime connections use
# dag.network.p2p_magic and never this compatibility value.
NETWORK_MAGIC = "4244434b"
MAX_MESSAGE_BYTES = 2 * 1024 * 1024  # 2MB anti-DoS limit
INITIAL_PEER_SCORE = 100
MIN_VALID_PEER_SCORE = 20
MAX_PEERS = 128
MAX_ORPHANS = 1000
MAX_ORPHAN_BYTES = 32 * 1024 * 1024
MAX_INV_BLOCKS = 500
MAX_LOCATOR_HASHES = 64
MAX_PEER_SCORE = 200
IBD_REQUEST_WINDOW = 16
BLOCK_REQUEST_TIMEOUT = 30.0
MAX_QUEUED_REQUESTS = MAX_INV_BLOCKS + MAX_ORPHANS
P2P_PROTOCOL_VERSION = "1.2.0"

class PeerConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_addr: str, network_magic: str):
        self.reader = reader
        self.writer = writer
        self.peer_addr = peer_addr
        self.network_magic = network_magic
        self.remote_blue_score = 0
        self.handshake_complete = False
        self.connected_at = time.time()
        self.score = INITIAL_PEER_SCORE
        self.banned = False
        self.peer_host = peer_addr.rsplit(":", 1)[0]
        self.ibd_pending: Set[str] = set()
        self.ibd_queue: List[str] = []
        self.ibd_inflight: Set[str] = set()
        self.requested_blocks: Set[str] = set()
        self.request_deadlines: Dict[str, float] = {}
        self.parent_queue: List[str] = []
        self.deferred_gossip: Dict[str, None] = {}
        self.ibd_waiting_page = False
        self.ibd_page_deadline: Optional[float] = None
        self.ibd_more = False
        self.ibd_cursor: Optional[str] = None

    async def send_msg(self, msg_type: str, payload: dict):
        envelope = {
            "magic": self.network_magic,
            "type": msg_type,
            "payload": payload,
            "ts": time.time()
        }
        data = json.dumps(envelope) + "\n"
        self.writer.write(data.encode('utf-8'))
        await self.writer.drain()

    def adjust_score(self, delta: int) -> bool:
        self.score = min(MAX_PEER_SCORE, self.score + delta)
        if self.score <= MIN_VALID_PEER_SCORE:
            self.banned = True
            return False
        return True

class P2PNetwork:
    def __init__(
        self,
        dag: BlockDAG,
        p2p_host: str = "0.0.0.0",
        p2p_port: int = 16111,
        seed_peers: Optional[List[str]] = None,
        block_acceptor: Optional[Callable[[DAGBlock], bool]] = None,
    ):
        self.dag = dag
        self.network = dag.network
        self.network_magic = self.network.p2p_magic
        self.p2p_host = p2p_host
        self.p2p_port = p2p_port
        self.seed_peers = seed_peers or []
        self.block_acceptor = block_acceptor or self.dag.add_block
        self.peers: Dict[str, PeerConnection] = {}
        # Ban by host, not ephemeral source port.  Otherwise reconnecting from
        # a new port bypasses the penalty immediately.
        self.banned_addrs: Set[str] = set()
        self.orphan_pool: Dict[str, DAGBlock] = {}  # block_hash -> DAGBlock awaiting missing parents
        # Reverse dependency index for orphan resolution.  IBD can receive a
        # full page of children before their merge parents; scanning every
        # orphan after every accepted block makes catch-up quadratic and can
        # leave a small seed permanently behind a busy DAG.
        self.orphan_missing: Dict[str, Set[str]] = {}
        self.orphans_by_parent: Dict[str, Set[str]] = {}
        self.orphan_sizes: Dict[str, int] = {}
        self.orphan_bytes = 0
        self._sync_log_count = 0
        self._sync_log_started = time.monotonic()
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self._tasks: List[asyncio.Task] = []
        # Bounded diagnostic evidence, never block payloads or wallet data.
        self.sync_events = deque(maxlen=128)
        self.sync_counters = Counter()

    def _sync_event(self, event, peer=None, block_hash=None, **details):
        self.sync_counters[event] += 1
        record = {"event": event, "time": time.time(), **details}
        if peer is not None:
            record["peer"] = peer.peer_addr
        if block_hash is not None:
            record["hash"] = block_hash
        self.sync_events.append(record)
        logger.debug("P2P sync event: %s", record)

    def _check_sync_timeouts(self, peer):
        now = time.monotonic()
        expired = next((h for h, deadline in peer.request_deadlines.items() if deadline <= now), None)
        if expired is not None or (peer.ibd_page_deadline is not None and peer.ibd_page_deadline <= now):
            self._sync_event("request_timeout", peer, expired)
            logger.warning("P2P synchronization timed out for %s (block %s)", peer.peer_addr, expired)
            raise TimeoutError("P2P synchronization request timed out")

    def _initial_block_locator(self):
        # Standby policies may override locator construction without changing
        # the existing wire format or the production default behavior.
        return list(self.dag.virtual_selected_parent_chain[-10:])

    async def start(self):
        self.running = True
        self.server = await asyncio.start_server(
            self._handle_incoming, self.p2p_host, self.p2p_port, limit=MAX_MESSAGE_BYTES + 1
        )
        logger.info(f"🌐 P2P Gossip Engine listening on {self.p2p_host}:{self.p2p_port}")
        
        # Connect to seed peers
        for seed in self.seed_peers:
            t = asyncio.create_task(self.connect_to_peer(seed))
            self._tasks.append(t)

    async def connect_to_peer(self, peer_str: str):
        """Outbound peer connection"""
        peer_host = peer_str.rsplit(":", 1)[0]
        if peer_str in self.peers or peer_host in self.banned_addrs:
            return
        try:
            host, port = peer_str.split(":")
            reader, writer = await asyncio.open_connection(host, int(port), limit=MAX_MESSAGE_BYTES + 1)
            peer = PeerConnection(reader, writer, peer_str, self.network_magic)
            self.peers[peer_str] = peer
            logger.info(f"🔗 Outbound connected to peer: {peer_str}")
            
            # Send Handshake
            local_tip = self.dag.blocks[self.dag.selected_tip]
            await peer.send_msg("HANDSHAKE", {
                "network": self.network.name,
                "chain_id": self.network.chain_id,
                "consensus_fingerprint": self.network.consensus_fingerprint,
                "genesis_hash": self.dag.genesis_hash,
                "blue_score": local_tip.blue_score,
                "version": P2P_PROTOCOL_VERSION,
            })
            
            t = asyncio.create_task(self._peer_listen_loop(peer))
            self._tasks.append(t)
        except Exception as e:
            logger.debug(f"Could not connect to peer {peer_str}: {e}")

    async def _handle_incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Inbound peer connection"""
        addr = writer.get_extra_info('peername')
        peer_addr = f"{addr[0]}:{addr[1]}"
        if addr[0] in self.banned_addrs or len(self.peers) >= MAX_PEERS:
            writer.close()
            return

        peer = PeerConnection(reader, writer, peer_addr, self.network_magic)
        self.peers[peer_addr] = peer
        logger.info(f"📥 Incoming peer connection from {peer_addr}")
        
        t = asyncio.create_task(self._peer_listen_loop(peer))
        self._tasks.append(t)

    async def _peer_listen_loop(self, peer: PeerConnection):
        try:
            while self.running:
                try:
                    line = await asyncio.wait_for(peer.reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    self._check_sync_timeouts(peer)
                    continue
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES:
                    logger.warning(f"Peer {peer.peer_addr} exceeded max message size. Disconnecting.")
                    peer.adjust_score(-50)
                    break

                try:
                    msg = json.loads(line.decode('utf-8').strip())
                except Exception:
                    peer.adjust_score(-20)
                    continue

                if msg.get("magic") != self.network_magic:
                    logger.warning(f"Peer {peer.peer_addr} sent invalid network magic: {msg.get('magic')}")
                    peer.adjust_score(-100)
                    break

                await self._process_msg(peer, msg)
                self._check_sync_timeouts(peer)
                if peer.banned:
                    self.banned_addrs.add(peer.peer_host)
                    break
        except Exception as e:
            logger.debug(f"Peer {peer.peer_addr} loop terminated: {e}")
        finally:
            if peer.peer_addr in self.peers:
                del self.peers[peer.peer_addr]
            try:
                peer.writer.close()
                await peer.writer.wait_closed()
            except Exception:
                pass
            logger.info(f"❌ Peer disconnected: {peer.peer_addr}")
            self._sync_event("disconnected", peer, pending=len(peer.ibd_pending),
                             outstanding=len(peer.requested_blocks))
            # An opt-in global request policy may have suppressed another
            # connection's duplicate while this connection owned the request.
            # Retry its retained queue after the owner closes, without dialing
            # new peers or creating a reconnection loop.
            for remaining in (list(self.peers.values()) if self.running else ()):
                try:
                    await self._pump_ibd_requests(remaining)
                except Exception as exc:
                    logger.warning("Cannot resume queued P2P requests for %s: %s",
                                   remaining.peer_addr, exc)
                    remaining.writer.close()

    async def _process_msg(self, peer: PeerConnection, msg: dict):
        m_type = msg.get("type")
        payload = msg.get("payload", {})

        if not peer.handshake_complete and m_type not in {"HANDSHAKE", "HANDSHAKE_ACK"}:
            peer.adjust_score(-100)
            peer.writer.close()
            return

        if m_type == "HANDSHAKE":
            if (
                payload.get("network") != self.network.name
                or payload.get("chain_id") != self.network.chain_id
                or payload.get("consensus_fingerprint") != self.network.consensus_fingerprint
                or payload.get("genesis_hash") != self.dag.genesis_hash
                or payload.get("version") != P2P_PROTOCOL_VERSION
            ):
                logger.warning(f"Peer {peer.peer_addr} network identity mismatch! Banning.")
                peer.adjust_score(-100)
                peer.writer.close()
                return

            peer.remote_blue_score = payload.get("blue_score", 0)
            peer.handshake_complete = True
            logger.info(f"✓ Peer {peer.peer_addr} handshake verified. Remote blue score: {peer.remote_blue_score}")
            
            # Reciprocate handshake ack
            local_tip = self.dag.blocks[self.dag.selected_tip]
            await peer.send_msg("HANDSHAKE_ACK", {
                "network": self.network.name,
                "chain_id": self.network.chain_id,
                "consensus_fingerprint": self.network.consensus_fingerprint,
                "genesis_hash": self.dag.genesis_hash,
                "blue_score": local_tip.blue_score,
                "version": P2P_PROTOCOL_VERSION,
            })

            # Check if remote peer is ahead: trigger Initial Block Download (IBD)
            if peer.remote_blue_score > local_tip.blue_score:
                locator = self._initial_block_locator()
                peer.ibd_waiting_page = True
                peer.ibd_page_deadline = time.monotonic() + BLOCK_REQUEST_TIMEOUT
                await peer.send_msg("GET_BLOCKS", {"locator": locator})
            await self._resume_orphan_dependencies(peer)

        elif m_type == "HANDSHAKE_ACK":
            if (
                payload.get("network") != self.network.name
                or payload.get("chain_id") != self.network.chain_id
                or payload.get("consensus_fingerprint") != self.network.consensus_fingerprint
                or payload.get("genesis_hash") != self.dag.genesis_hash
                or payload.get("version") != P2P_PROTOCOL_VERSION
            ):
                peer.adjust_score(-100)
                peer.writer.close()
                return
            peer.remote_blue_score = payload.get("blue_score", 0)
            peer.handshake_complete = True
            logger.info(f"✓ Peer {peer.peer_addr} acknowledged handshake.")

            # Trigger IBD if remote peer has higher blue score
            local_tip = self.dag.blocks[self.dag.selected_tip]
            if peer.remote_blue_score > local_tip.blue_score:
                locator = self._initial_block_locator()
                peer.ibd_waiting_page = True
                peer.ibd_page_deadline = time.monotonic() + BLOCK_REQUEST_TIMEOUT
                await peer.send_msg("GET_BLOCKS", {"locator": locator})
            await self._resume_orphan_dependencies(peer)

        elif m_type == "GET_BLOCKS":
            # Initial block download locator resolution
            # Search newest-to-oldest to find the most recent common ancestor
            locator = payload.get("locator", [])
            if (
                not isinstance(locator, list)
                or len(locator) > MAX_LOCATOR_HASHES
                or any(not isinstance(value, str) or len(value) != 64 for value in locator)
            ):
                peer.adjust_score(-25)
                return
            split_idx = 0
            chain = self.dag.virtual_selected_parent_chain
            positions = {block_hash: index for index, block_hash in enumerate(chain)}
            for h in reversed(locator):
                if h in positions:
                    split_idx = positions[h] + 1
                    break
            # Send up to 500 block hashes to catch up
            send_hashes = chain[split_idx:split_idx + MAX_INV_BLOCKS]
            # Empty pages must terminate a request too, otherwise a peer at
            # our tip cannot distinguish completion from a lost response.
            await peer.send_msg("INV", {
                "blocks": send_hashes,
                "more": split_idx + len(send_hashes) < len(chain),
            })

        elif m_type == "INV":
            hashes = payload.get("blocks", [])
            if (
                not isinstance(hashes, list)
                or len(hashes) > MAX_INV_BLOCKS
                or any(not isinstance(value, str) or len(value) != 64 for value in hashes)
            ):
                peer.adjust_score(-25)
                return
            # GET_BLOCKS responses explicitly carry `more`; one-block gossip
            # announcements do not. Keep far-ahead gossip out of historical
            # dependency resolution: otherwise a tip announcement can pull in
            # tens of thousands of descendants before the first page finishes.
            if "more" not in payload and (peer.ibd_pending or peer.ibd_waiting_page or peer.ibd_more):
                for h in hashes:
                    if h not in self.dag.blocks:
                        if len(peer.deferred_gossip) >= MAX_INV_BLOCKS and h not in peer.deferred_gossip:
                            peer.deferred_gossip.pop(next(iter(peer.deferred_gossip)))
                            self._sync_event("gossip_hint_evicted", peer)
                        peer.deferred_gossip[h] = None
                self._sync_event("gossip_deferred", peer, count=len(hashes))
                return
            if "more" in payload:
                if not isinstance(payload["more"], bool) or (payload["more"] and not hashes):
                    raise ValueError("invalid history continuation marker")
                if payload["more"] and hashes[-1] == peer.ibd_cursor:
                    raise ValueError("history page cursor did not advance")
                if peer.ibd_pending:
                    raise ValueError("overlapping history inventory page")
                peer.ibd_waiting_page = False
                peer.ibd_page_deadline = None
                peer.ibd_more = payload.get("more") is True
                peer.ibd_cursor = hashes[-1] if hashes else peer.ibd_cursor
                peer.ibd_pending = {h for h in hashes if h not in self.dag.blocks}
                self._sync_event("history_page", peer, count=len(hashes), more=peer.ibd_more)
            for h in hashes:
                if (
                    h not in self.dag.blocks
                    and h not in self.orphan_pool
                    and h not in peer.requested_blocks
                ):
                    queue = peer.ibd_queue if "more" in payload else peer.parent_queue
                    if h not in queue:
                        if len(peer.ibd_queue) + len(peer.parent_queue) >= MAX_QUEUED_REQUESTS:
                            raise RuntimeError("P2P request queue capacity exceeded")
                        queue.append(h)
                elif h in self.orphan_pool:
                    self._queue_missing_roots(peer, h)
            await self._pump_ibd_requests(peer)
            if not peer.ibd_pending and peer.ibd_more:
                await self._request_next_ibd_batch(peer)
            await self._release_deferred_gossip(peer)

        elif m_type == "GET_BLOCK":
            h = payload.get("hash")
            if h in self.dag.blocks:
                b = self.dag.blocks[h]
                await peer.send_msg("BLOCK", {
                    "hash": b.hash,
                    "version": b.version,
                    "chain_id": b.chain_id,
                    "parents": b.parents,
                    "timestamp": b.timestamp,
                    "bits": b.bits,
                    "nonce": b.nonce,
                    "payload_hash": b.payload_hash.hex(),
                    "coinbase_output": [b.coinbase_output[0].hex(), b.coinbase_output[1].hex()],
                    "transactions": [transaction_to_dict(tx) for tx in b.transactions]
                })

        elif m_type == "BLOCK":
            h = payload.get("hash")
            if not isinstance(h, str) or len(h) != 64:
                raise ValueError("Peer block hash is not 32-byte hex")
            try:
                bytes.fromhex(h)
            except ValueError as exc:
                raise ValueError("Peer block hash is not 32-byte hex") from exc
            # The transport slot is free on receipt, but history progress is
            # committed ONLY once the block is admitted by normal consensus.
            peer.requested_blocks.discard(h)
            peer.request_deadlines.pop(h, None)
            peer.ibd_inflight.discard(h)
            self._sync_event("received", peer, h)
            if h not in self.dag.blocks:
                cb = payload.get("coinbase_output", ["00"*33, "00"*33])
                cb_tuple = (bytes.fromhex(cb[0]), bytes.fromhex(cb[1]))
                
                if payload.get("chain_id") != self.network.chain_id:
                    raise ValueError("Peer block chain_id mismatch")
                deserialized_txs = [transaction_from_dict(raw) for raw in payload.get("transactions", [])]

                blk = DAGBlock(
                    parents=payload["parents"],
                    timestamp=payload["timestamp"],
                    bits=payload["bits"],
                    nonce=payload["nonce"],
                    coinbase_output=cb_tuple,
                    transactions=deserialized_txs,
                    version=payload.get("version"),
                    payload_hash=bytes.fromhex(payload.get("payload_hash", "")),
                    chain_id=self.network.chain_id,
                )
                if blk.hash != h:
                    raise ValueError("Peer block hash does not match the serialized header")

                # Check if parents exist locally
                missing_parents = [p for p in blk.parents if p not in self.dag.blocks]
                if missing_parents:
                    # Cheap context-free rules must pass before untrusted data
                    # can occupy the cache or trigger ancestor downloads. DAA,
                    # parent timestamps and ledger rules still run at admission.
                    self._validate_orphan_header(blk)
                    wire_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
                    if self.orphan_bytes - self.orphan_sizes.get(h, 0) + wire_size > MAX_ORPHAN_BYTES:
                        self._sync_event("orphan_byte_capacity", peer, h)
                        raise RuntimeError("P2P orphan byte capacity exceeded")
                    # Stash in orphan pool and request missing parents
                    if blk.hash not in self.orphan_pool and len(self.orphan_pool) >= MAX_ORPHANS:
                        self._sync_event("orphan_capacity", peer, h)
                        # Never silently evict a dependency and then advance
                        # the cursor beyond it. Disconnect with explicit cause.
                        raise RuntimeError("P2P orphan capacity exceeded")
                    # A peer can advertise the same unresolved child again.
                    # Remove its old reverse-index entries before replacing it.
                    self._drop_orphan(blk.hash)
                    self.orphan_pool[blk.hash] = blk
                    self.orphan_sizes[blk.hash] = wire_size
                    self.orphan_bytes += wire_size
                    missing = set(missing_parents)
                    self.orphan_missing[blk.hash] = missing
                    for parent_hash in missing:
                        self.orphans_by_parent.setdefault(parent_hash, set()).add(blk.hash)
                    self._sync_event("orphan_wait", peer, h, missing=len(missing))
                    self._queue_missing_roots(peer, h)
                    await self._complete_block_request(peer, h)
                    return

                try:
                    added = self.block_acceptor(blk)
                    if not added and blk.hash not in self.dag.blocks:
                        raise RuntimeError("block admission returned false without known block")
                    if added:
                        self._sync_event("accepted", peer, h)
                        self._sync_log_count += 1
                        # Per-block INFO logging is surprisingly expensive at
                        # IBD rates.  Keep detailed records at DEBUG and emit a
                        # periodic operational progress line instead.
                        logger.debug(
                            "Synchronized BlockDAG block #%s (%s...) from %s",
                            blk.blue_score,
                            blk.hash[:16],
                            peer.peer_addr,
                        )
                        now = time.monotonic()
                        if now - self._sync_log_started >= 10.0:
                            elapsed = max(now - self._sync_log_started, 0.001)
                            logger.info(
                                "IBD progress: tip #%s, accepted %s blocks in %.1fs (%.1f blocks/s), %s orphans pending",
                                self.dag.blocks[self.dag.selected_tip].blue_score,
                                self._sync_log_count,
                                elapsed,
                                self._sync_log_count / elapsed,
                                len(self.orphan_pool),
                            )
                            self._sync_log_count = 0
                            self._sync_log_started = now
                        peer.adjust_score(1)  # Reward valid block propagation
                        await self.broadcast_block(blk.hash)
                except Exception as e:
                    self._sync_event("admission_failed", peer, h, error=type(e).__name__)
                    logger.warning("Block admission failed for %s: %s", h, e)
                    peer.adjust_score(-15)
                    raise RuntimeError(f"block admission failed: {h}") from e
                # Keep orphan failures out of the catch-all invalid-block
                # handler: storage failures must not disappear at DEBUG level.
                self._drop_orphan(blk.hash)
                await self._process_orphans(blk.hash)
            else:
                self._drop_orphan(h)
                await self._process_orphans(h)
            await self._complete_block_request(peer, h)

    def _validate_orphan_header(self, block):
        if block.version != self.network.block_version:
            raise ValueError("Unsupported orphan block version")
        if not block.parents or len(block.parents) > self.network.max_parents or len(set(block.parents)) != len(block.parents):
            raise ValueError("Invalid orphan parent count")
        for parent in block.parents:
            if not isinstance(parent, str) or len(parent) != 64 or len(bytes.fromhex(parent)) != 32:
                raise ValueError("Invalid orphan parent hash")
        if any(len(point) != 33 or point[0] not in (2, 3) for point in block.coinbase_output):
            raise ValueError("Invalid orphan coinbase output")
        if len(block.payload_hash) != 32 or block.payload_hash != transaction_commitment(block.transactions):
            raise ValueError("Invalid orphan payload commitment")
        if block.timestamp > time.time() + self.network.max_future_seconds:
            raise ValueError("Orphan block timestamp is too far in the future")
        if not hash_meets_target(bytes.fromhex(block.hash), block.bits, self.network.pow_limit_bits):
            raise ValueError("Orphan block proof of work does not meet target")

    def _queue_missing_roots(self, peer, orphan_hash):
        """Walk cached ancestry iteratively; only request genuinely absent roots."""
        pending = [orphan_hash]
        seen = set()
        while pending:
            h = pending.pop()
            if h in seen or h in self.dag.blocks:
                continue
            seen.add(h)
            if h in self.orphan_pool:
                pending.extend(self.orphan_pool[h].parents)
            elif h not in peer.requested_blocks and h not in peer.parent_queue:
                if len(peer.parent_queue) + len(peer.ibd_queue) >= MAX_QUEUED_REQUESTS:
                    raise RuntimeError("P2P dependency queue capacity exceeded")
                peer.parent_queue.append(h)

    async def _resume_orphan_dependencies(self, peer):
        # A new connection must recover dependencies cached before disconnect.
        # Admission via another path may also have made them ready meanwhile.
        for h in list(self.orphan_pool):
            block = self.orphan_pool.get(h)
            if block is None:
                continue
            for parent in block.parents:
                if parent in self.dag.blocks:
                    await self._process_orphans(parent)
            if h in self.orphan_pool:
                self._queue_missing_roots(peer, h)
        await self._pump_ibd_requests(peer)

    async def _release_deferred_gossip(self, peer):
        if peer.ibd_pending or peer.ibd_waiting_page or peer.ibd_more:
            return
        for h in list(peer.deferred_gossip):
            if h not in self.dag.blocks and h not in peer.parent_queue and h not in peer.requested_blocks:
                if len(peer.parent_queue) + len(peer.ibd_queue) >= MAX_QUEUED_REQUESTS:
                    break
                if h in self.orphan_pool:
                    self._queue_missing_roots(peer, h)
                else:
                    peer.parent_queue.append(h)
            peer.deferred_gossip.pop(h, None)
        await self._pump_ibd_requests(peer)

    async def _request_block(self, peer: PeerConnection, block_hash: str) -> bool:
        """Request a block at most once until its response is received."""
        if block_hash in self.dag.blocks or block_hash in peer.requested_blocks:
            return False
        peer.requested_blocks.add(block_hash)
        peer.request_deadlines[block_hash] = time.monotonic() + BLOCK_REQUEST_TIMEOUT
        self._sync_event("requested", peer, block_hash)
        await peer.send_msg("GET_BLOCK", {"hash": block_hash})
        return True

    async def _pump_ibd_requests(self, peer: PeerConnection) -> None:
        """Keep a bounded selected-chain request window in flight.

        A full 500-request burst places missing merge-parent responses behind
        hundreds of children and can saturate the serving node's event loop.
        The small window lets parent requests interleave promptly.
        """
        while (peer.parent_queue or peer.ibd_queue) and len(peer.requested_blocks) < IBD_REQUEST_WINDOW:
            queue = peer.parent_queue if peer.parent_queue else peer.ibd_queue
            block_hash = queue.pop(0)
            if block_hash in self.dag.blocks:
                peer.ibd_pending.discard(block_hash)
                continue
            if block_hash in self.orphan_pool:
                self._queue_missing_roots(peer, block_hash)
                continue
            if await self._request_block(peer, block_hash):
                peer.ibd_inflight.add(block_hash)
            elif block_hash not in self.dag.blocks and block_hash not in peer.requested_blocks:
                # A shared limiter/deduplicator can defer, rather than send, a
                # request. Do not lose its only scheduling entry or spin here.
                queue.append(block_hash)
                break

    async def _complete_block_request(self, peer: PeerConnection, block_hash: str) -> None:
        peer.requested_blocks.discard(block_hash)
        peer.request_deadlines.pop(block_hash, None)
        peer.ibd_inflight.discard(block_hash)
        # Another peer may have supplied a shared ancestor; wake every affected
        # page, not just the connection that happened to deliver the parent.
        for candidate in {id(p): p for p in [peer, *self.peers.values()]}.values():
            # Iterate the bounded page, never the hundreds-of-thousands-entry DAG.
            candidate.ibd_pending = {h for h in candidate.ibd_pending if h not in self.dag.blocks}
            for known in list(candidate.requested_blocks):
                if known in self.dag.blocks:
                    candidate.requested_blocks.discard(known)
                    candidate.request_deadlines.pop(known, None)
                    candidate.ibd_inflight.discard(known)
            await self._pump_ibd_requests(candidate)
            if not candidate.ibd_pending and candidate.ibd_more:
                await self._request_next_ibd_batch(candidate)
            await self._release_deferred_gossip(candidate)

    async def _request_next_ibd_batch(self, peer: PeerConnection) -> None:
        """Continue IBD only after every advertised block was admitted."""
        peer.ibd_more = False
        peer.ibd_waiting_page = True
        peer.ibd_page_deadline = time.monotonic() + BLOCK_REQUEST_TIMEOUT
        self._sync_event("next_page", peer, peer.ibd_cursor)
        peer.ibd_queue.clear()
        peer.ibd_inflight.clear()
        # Cursor advances only after every advertised block is admitted.
        locator = [peer.ibd_cursor] if peer.ibd_cursor else list(self.dag.virtual_selected_parent_chain[-10:])
        await peer.send_msg("GET_BLOCKS", {"locator": locator})

    def _drop_orphan(self, orphan_hash: str) -> Optional[DAGBlock]:
        """Remove one orphan and every reverse-index reference to it."""
        orphan = self.orphan_pool.pop(orphan_hash, None)
        self.orphan_bytes -= self.orphan_sizes.pop(orphan_hash, 0)
        for parent_hash in self.orphan_missing.pop(orphan_hash, set()):
            waiting = self.orphans_by_parent.get(parent_hash)
            if waiting is None:
                continue
            waiting.discard(orphan_hash)
            if not waiting:
                self.orphans_by_parent.pop(parent_hash, None)
        return orphan

    async def _process_orphans(self, newly_added_hash: str):
        """Resolve only orphans affected by a newly accepted parent.

        Accepted orphans are queued as parents themselves, so an arbitrarily
        deep out-of-order subtree is drained without repeatedly walking the
        unrelated orphan pool.
        """
        ready_parents = [newly_added_hash]
        while ready_parents:
            parent_hash = ready_parents.pop()
            dependent_hashes = list(self.orphans_by_parent.get(parent_hash, set()))
            for orphan_hash in dependent_hashes:
                missing = self.orphan_missing.get(orphan_hash)
                if missing is None:
                    continue
                waiting = self.orphans_by_parent.get(parent_hash)
                if waiting is not None:
                    waiting.discard(orphan_hash)
                    if not waiting:
                        self.orphans_by_parent.pop(parent_hash, None)
                missing.discard(parent_hash)
                if missing:
                    continue
                orphan_blk = self.orphan_pool.get(orphan_hash)
                if orphan_blk is None:
                    continue
                try:
                    added = self.block_acceptor(orphan_blk)
                    if not added and orphan_hash not in self.dag.blocks:
                        raise RuntimeError("orphan admission returned false")
                    self._drop_orphan(orphan_hash)
                    self._sync_event("orphan_accepted", block_hash=orphan_hash)
                    if added:
                        logger.debug(
                            "Resolved orphan block #%s (%s...)",
                            orphan_blk.blue_score,
                            orphan_hash[:16],
                        )
                        await self.broadcast_block(orphan_blk.hash)
                    ready_parents.append(orphan_blk.hash)
                except Exception as e:
                    # Preserve data and reverse indexes for an explicit retry
                    # after reconnect. Do not misreport receipt as acceptance.
                    self.orphan_missing[orphan_hash] = set(orphan_blk.parents)
                    for p in orphan_blk.parents:
                        self.orphans_by_parent.setdefault(p, set()).add(orphan_hash)
                    self._sync_event("orphan_admission_failed", block_hash=orphan_hash,
                                     error=type(e).__name__)
                    logger.warning("Orphan admission failed for %s: %s", orphan_hash, e)
                    raise RuntimeError(f"orphan admission failed: {orphan_hash}") from e

    def get_peers_info(self) -> List[dict]:
        """Return operational snapshot of connected P2P peers"""
        info = []
        for addr, peer in list(self.peers.items()):
            info.append({
                "address": addr,
                "remote_blue_score": peer.remote_blue_score,
                "score": peer.score,
                "connected_seconds": round(time.time() - peer.connected_at, 1),
                "handshake_complete": peer.handshake_complete,
                "sync": {"pending_admission": len(peer.ibd_pending),
                          "outstanding": len(peer.requested_blocks),
                          "queued": len(peer.parent_queue) + len(peer.ibd_queue),
                          "deferred_gossip": len(peer.deferred_gossip),
                          "waiting_page": peer.ibd_waiting_page}
            })
        return info

    async def broadcast_block(self, block_hash: str):
        for peer in list(self.peers.values()):
            if peer.handshake_complete:
                try:
                    await peer.send_msg("INV", {"blocks": [block_hash]})
                except Exception:
                    pass

    async def stop(self):
        self.running = False
        for t in self._tasks:
            t.cancel()
        for p in list(self.peers.values()):
            try:
                p.writer.close()
                await p.writer.wait_closed()
            except Exception:
                pass
        if self.server:
            self.server.close()
            await self.server.wait_closed()

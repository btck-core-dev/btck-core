"""
BTC-K High-Speed P2P Gossip Network Engine (P1-04 / Chapter 7)
--------------------------------------------------------------
Features:
1. Wire Protocol Envelope with Network Magic ("BTCK") and max size limit (2 MB).
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
from typing import Dict, List, Set, Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from node.blockdag import BlockDAG, DAGBlock
from privacy.wallet_engine import StealthTransaction, TxInput, TxOutput

logger = logging.getLogger("btck-p2p")

NETWORK_MAGIC = "BTCK"
MAX_MESSAGE_BYTES = 2 * 1024 * 1024  # 2MB anti-DoS limit
INITIAL_PEER_SCORE = 100
MIN_VALID_PEER_SCORE = 20

class PeerConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_addr: str):
        self.reader = reader
        self.writer = writer
        self.peer_addr = peer_addr
        self.remote_blue_score = 0
        self.handshake_complete = False
        self.connected_at = time.time()
        self.score = INITIAL_PEER_SCORE
        self.banned = False

    async def send_msg(self, msg_type: str, payload: dict):
        envelope = {
            "magic": NETWORK_MAGIC,
            "type": msg_type,
            "payload": payload,
            "ts": time.time()
        }
        data = json.dumps(envelope) + "\n"
        self.writer.write(data.encode('utf-8'))
        await self.writer.drain()

    def adjust_score(self, delta: int) -> bool:
        self.score += delta
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
        seed_peers: Optional[List[str]] = None
    ):
        self.dag = dag
        self.p2p_host = p2p_host
        self.p2p_port = p2p_port
        self.seed_peers = seed_peers or []
        self.peers: Dict[str, PeerConnection] = {}
        self.banned_addrs: Set[str] = set()
        self.orphan_pool: Dict[str, DAGBlock] = {}  # block_hash -> DAGBlock awaiting missing parents
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self._tasks: List[asyncio.Task] = []

    async def start(self):
        self.running = True
        self.server = await asyncio.start_server(self._handle_incoming, self.p2p_host, self.p2p_port)
        logger.info(f"🌐 P2P Gossip Engine listening on {self.p2p_host}:{self.p2p_port}")
        
        # Connect to seed peers
        for seed in self.seed_peers:
            t = asyncio.create_task(self.connect_to_peer(seed))
            self._tasks.append(t)

    async def connect_to_peer(self, peer_str: str):
        """Outbound peer connection"""
        if peer_str in self.peers or peer_str in self.banned_addrs:
            return
        try:
            host, port = peer_str.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
            peer = PeerConnection(reader, writer, peer_str)
            self.peers[peer_str] = peer
            logger.info(f"🔗 Outbound connected to peer: {peer_str}")
            
            # Send Handshake
            local_tip = self.dag.blocks[self.dag.selected_tip]
            await peer.send_msg("HANDSHAKE", {
                "genesis_hash": self.dag.genesis_hash,
                "blue_score": local_tip.blue_score,
                "version": "1.1.0"
            })
            
            t = asyncio.create_task(self._peer_listen_loop(peer))
            self._tasks.append(t)
        except Exception as e:
            logger.debug(f"Could not connect to peer {peer_str}: {e}")

    async def _handle_incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Inbound peer connection"""
        addr = writer.get_extra_info('peername')
        peer_addr = f"{addr[0]}:{addr[1]}"
        if peer_addr in self.banned_addrs:
            writer.close()
            return

        peer = PeerConnection(reader, writer, peer_addr)
        self.peers[peer_addr] = peer
        logger.info(f"📥 Incoming peer connection from {peer_addr}")
        
        t = asyncio.create_task(self._peer_listen_loop(peer))
        self._tasks.append(t)

    async def _peer_listen_loop(self, peer: PeerConnection):
        try:
            while self.running:
                line = await peer.reader.readline()
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

                if msg.get("magic") != NETWORK_MAGIC:
                    logger.warning(f"Peer {peer.peer_addr} sent invalid network magic: {msg.get('magic')}")
                    peer.adjust_score(-30)
                    continue

                await self._process_msg(peer, msg)
                if peer.banned:
                    self.banned_addrs.add(peer.peer_addr)
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

    async def _process_msg(self, peer: PeerConnection, msg: dict):
        m_type = msg.get("type")
        payload = msg.get("payload", {})

        if m_type == "HANDSHAKE":
            if payload.get("genesis_hash") != self.dag.genesis_hash:
                logger.warning(f"Peer {peer.peer_addr} genesis mismatch! Banning.")
                peer.adjust_score(-100)
                peer.writer.close()
                return

            peer.remote_blue_score = payload.get("blue_score", 0)
            peer.handshake_complete = True
            logger.info(f"✓ Peer {peer.peer_addr} handshake verified. Remote blue score: {peer.remote_blue_score}")
            
            # Reciprocate handshake ack
            local_tip = self.dag.blocks[self.dag.selected_tip]
            await peer.send_msg("HANDSHAKE_ACK", {
                "genesis_hash": self.dag.genesis_hash,
                "blue_score": local_tip.blue_score,
                "version": "1.1.0"
            })

            # Check if remote peer is ahead: trigger Initial Block Download (IBD)
            if peer.remote_blue_score > local_tip.blue_score:
                locator = list(self.dag.virtual_selected_parent_chain[-10:])
                await peer.send_msg("GET_BLOCKS", {"locator": locator})

        elif m_type == "HANDSHAKE_ACK":
            if payload.get("genesis_hash") != self.dag.genesis_hash:
                peer.adjust_score(-100)
                peer.writer.close()
                return
            peer.remote_blue_score = payload.get("blue_score", 0)
            peer.handshake_complete = True
            logger.info(f"✓ Peer {peer.peer_addr} acknowledged handshake.")

            # Trigger IBD if remote peer has higher blue score
            local_tip = self.dag.blocks[self.dag.selected_tip]
            if peer.remote_blue_score > local_tip.blue_score:
                locator = list(self.dag.virtual_selected_parent_chain[-10:])
                await peer.send_msg("GET_BLOCKS", {"locator": locator})

        elif m_type == "GET_BLOCKS":
            # Initial block download locator resolution
            # Search newest-to-oldest to find the most recent common ancestor
            locator = payload.get("locator", [])
            split_idx = 0
            chain = self.dag.virtual_selected_parent_chain
            for h in reversed(locator):
                if h in chain:
                    split_idx = chain.index(h) + 1
                    break
            # Send up to 500 block hashes to catch up
            send_hashes = chain[split_idx:split_idx + 500]
            if send_hashes:
                await peer.send_msg("INV", {"blocks": send_hashes})

        elif m_type == "INV":
            hashes = payload.get("blocks", [])
            for h in hashes:
                if h not in self.dag.blocks and h not in self.orphan_pool:
                    await peer.send_msg("GET_BLOCK", {"hash": h})

        elif m_type == "GET_BLOCK":
            h = payload.get("hash")
            if h in self.dag.blocks:
                b = self.dag.blocks[h]
                # Serialize transactions
                txs_payload = []
                for tx in b.transactions:
                    txs_payload.append({
                        "version": tx.version,
                        "lock_time": tx.lock_time,
                        "inputs": [{
                            "prev_txid": inp.prev_txid,
                            "prev_vout": inp.prev_vout,
                            "amount_satk": inp.amount_satk,
                            "P_onetime_hex": inp.P_onetime_hex,
                            "signature_hex": inp.signature_hex
                        } for inp in tx.inputs],
                        "outputs": [{
                            "amount_satk": out.amount_satk,
                            "P_onetime_hex": out.P_onetime_hex,
                            "R_ephem_hex": out.R_ephem_hex
                        } for out in tx.outputs]
                    })

                await peer.send_msg("BLOCK", {
                    "hash": b.hash,
                    "parents": b.parents,
                    "timestamp": b.timestamp,
                    "bits": b.bits,
                    "nonce": b.nonce,
                    "coinbase_output": [b.coinbase_output[0].hex(), b.coinbase_output[1].hex()],
                    "transactions": txs_payload
                })

        elif m_type == "BLOCK":
            h = payload.get("hash")
            if h not in self.dag.blocks:
                cb = payload.get("coinbase_output", ["00"*33, "00"*33])
                cb_tuple = (bytes.fromhex(cb[0]), bytes.fromhex(cb[1]))
                
                # Reconstruct transactions
                deserialized_txs = []
                for tx_dict in payload.get("transactions", []):
                    try:
                        inputs = [TxInput(
                            prev_txid=inp["prev_txid"],
                            prev_vout=inp["prev_vout"],
                            amount_satk=inp["amount_satk"],
                            P_onetime_hex=inp["P_onetime_hex"],
                            signature_hex=inp["signature_hex"]
                        ) for inp in tx_dict["inputs"]]
                        outputs = [TxOutput(
                            amount_satk=out["amount_satk"],
                            P_onetime_hex=out["P_onetime_hex"],
                            R_ephem_hex=out["R_ephem_hex"]
                        ) for out in tx_dict["outputs"]]
                        deserialized_txs.append(StealthTransaction(
                            version=tx_dict.get("version", 1),
                            inputs=inputs,
                            outputs=outputs,
                            lock_time=tx_dict.get("lock_time", 0)
                        ))
                    except Exception as te:
                        logger.warning(f"Malformed transaction in block from {peer.peer_addr}: {te}")
                        peer.adjust_score(-10)

                blk = DAGBlock(
                    parents=payload["parents"],
                    timestamp=payload["timestamp"],
                    bits=payload["bits"],
                    nonce=payload["nonce"],
                    coinbase_output=cb_tuple,
                    transactions=deserialized_txs
                )

                # Check if parents exist locally
                missing_parents = [p for p in blk.parents if p not in self.dag.blocks]
                if missing_parents:
                    # Stash in orphan pool and request missing parents
                    self.orphan_pool[blk.hash] = blk
                    for mp in missing_parents:
                        await peer.send_msg("GET_BLOCK", {"hash": mp})
                    return

                try:
                    added = self.dag.add_block(blk)
                    if added:
                        logger.info(f"📦 Synchronized new BlockDAG block #{blk.blue_score} ({blk.hash[:16]}...) from {peer.peer_addr}")
                        peer.adjust_score(1)  # Reward valid block propagation
                        await self.broadcast_block(blk.hash)
                        # Process any orphans that were waiting for this block
                        await self._process_orphans(blk.hash)
                except Exception as e:
                    logger.debug(f"Invalid block from peer {peer.peer_addr}: {e}")
                    peer.adjust_score(-15)

    async def _process_orphans(self, newly_added_hash: str):
        """Recursively resolves orphan blocks once their parents are added"""
        while True:
            progress = False
            for orphan_hash, orphan_blk in list(self.orphan_pool.items()):
                if all(p in self.dag.blocks for p in orphan_blk.parents):
                    try:
                        if self.dag.add_block(orphan_blk):
                            logger.info(f"🧩 Resolved orphan block #{orphan_blk.blue_score} ({orphan_hash[:16]}...)")
                            await self.broadcast_block(orphan_blk.hash)
                    except Exception as e:
                        logger.debug(f"Failed to add resolved orphan: {e}")
                    self.orphan_pool.pop(orphan_hash, None)
                    progress = True
            if not progress:
                break

    def get_peers_info(self) -> List[dict]:
        """Return operational snapshot of connected P2P peers"""
        info = []
        for addr, peer in list(self.peers.items()):
            info.append({
                "address": addr,
                "remote_blue_score": peer.remote_blue_score,
                "score": peer.score,
                "connected_seconds": round(time.time() - peer.connected_at, 1),
                "handshake_complete": peer.handshake_complete
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

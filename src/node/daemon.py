"""
BTC-K Node RPC Daemon (btckd)
-----------------------------
Autonomous full node daemon hosting the BlockDAG consensus engine,
serving JSON-RPC 2.0 for Stratum bridges, wallets, and AI Agent clients.
Zero external dependencies (uses standard library asyncio).
"""

import asyncio
import copy
import hmac
import json
import logging
import time
from typing import Dict, Any, Optional

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from node.blockdag import BlockDAG, DAGBlock
from privacy.stealth_address import StealthKeyPair
from tokenomics.emission_schedule import SATS_PER_COIN
from p2p.p2p_network import P2PNetwork
from consensus.safeguards import consensus_guardian, SignedNetworkAlert, NetworkAlertPayload
from config.network import NetworkConfig, load_network
from node.chain_store import ChainStore
from node.utxo_set import UTXOEntry
from privacy.wallet_engine import transaction_from_dict, transaction_to_dict

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [btckd] %(message)s'
)
logger = logging.getLogger("btckd")

class BTCKDaemon:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 16110,
        p2p_host: Optional[str] = None,
        p2p_port: int = 16111,
        seed_peers: Optional[list] = None,
        network: Optional[NetworkConfig | str] = None,
        data_root: Optional[str] = None,
        persistent: bool = False,
        rpc_token: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.p2p_host = p2p_host or host
        self.p2p_port = p2p_port
        self.rpc_token = rpc_token or os.getenv("BTCK_RPC_TOKEN")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            if not self.rpc_token or len(self.rpc_token) < 32:
                raise ValueError("Non-loopback RPC binding requires BTCK_RPC_TOKEN with at least 32 characters")
        self.network = network if isinstance(network, NetworkConfig) else load_network(network)
        self.data_dir = self.network.data_dir(data_root)
        self.dag = BlockDAG(network=self.network)
        self.mempool: Dict[str, Any] = {}
        self.chain_store: Optional[ChainStore] = None
        self.replayed_blocks = 0
        if persistent:
            self.chain_store = ChainStore(self.data_dir / "chain.sqlite3", self.network)
            self.replayed_blocks = self.chain_store.replay_into(self.dag)
        self.p2p = P2PNetwork(
            dag=self.dag,
            p2p_host=self.p2p_host,
            p2p_port=p2p_port,
            seed_peers=seed_peers,
            block_acceptor=self.accept_block,
        )
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self.start_time = time.time()
        logger.info(
            f"Node initialized on {self.network.name} ({self.network.chain_id}) with "
            f"Genesis Block: {self.dag.genesis_hash[:16]}... "
            f"(data: {self.data_dir}, P2P port: {self.p2p_port})"
        )

    def accept_block(self, block: DAGBlock) -> bool:
        commit_hook = self.chain_store.append_block if self.chain_store else None
        accepted = self.dag.add_block(block, commit_hook=commit_hook)
        if accepted:
            selected_hashes = set(self.dag.virtual_selected_parent_chain)
            if block.hash in selected_hashes:
                for tx in block.transactions:
                    self.mempool.pop(tx.txid, None)
            self._revalidate_mempool()
        return accepted

    def _mempool_overlay(self):
        overlay = copy.deepcopy(self.dag.utxo_set.utxos)
        height = len(self.dag.virtual_selected_parent_chain)
        for tx in self.mempool.values():
            valid, error, _ = self.dag.utxo_set.validate_transaction(tx, height, overlay)
            if not valid:
                raise ValueError(f"Mempool invariant failed for {tx.txid}: {error}")
            for tx_input in tx.inputs:
                overlay.pop((tx_input.prev_txid, tx_input.prev_vout))
            for index, output in enumerate(tx.outputs):
                overlay[(tx.txid, index)] = UTXOEntry(
                    amount_satk=output.amount_satk,
                    P_onetime_hex=output.P_onetime_hex,
                    R_ephem_hex=output.R_ephem_hex,
                    block_height=height,
                    kind="standard",
                )
        return overlay

    def _revalidate_mempool(self) -> None:
        pending = list(self.mempool.values())
        self.mempool = {}
        for tx in pending:
            try:
                self.submit_transaction(tx)
            except ValueError:
                logger.info("Evicted invalidated mempool transaction %s", tx.txid)

    def submit_transaction(self, tx) -> dict:
        if tx.txid in self.mempool:
            return {"accepted": True, "txid": tx.txid, "status": "already-in-mempool"}
        if len(self.mempool) >= 1000:
            raise ValueError("Mempool capacity reached")
        if len(tx.inputs) > 100 or len(tx.outputs) > 100:
            raise ValueError("Transaction exceeds input/output limit")
        for block in self.dag.blocks.values():
            if any(existing.txid == tx.txid for existing in block.transactions):
                return {"accepted": True, "txid": tx.txid, "status": "confirmed"}
        overlay = self._mempool_overlay()
        height = len(self.dag.virtual_selected_parent_chain)
        valid, error, fee_sats = self.dag.utxo_set.validate_transaction(tx, height, overlay)
        if not valid:
            raise ValueError(error)
        self.mempool[tx.txid] = tx
        return {"accepted": True, "txid": tx.txid, "status": "mempool", "fee_satk": fee_sats}

    async def handle_http_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # Read HTTP request line
            line = await reader.readline()
            if not line:
                return
            
            parts = line.decode('utf-8').strip().split()
            if len(parts) < 2:
                return
            
            method, path = parts[0], parts[1]
            headers = {}
            content_length = 0

            # Read headers
            while True:
                header_line = await reader.readline()
                if not header_line or header_line == b'\r\n' or header_line == b'\n':
                    break
                header_str = header_line.decode('utf-8').strip()
                if ':' in header_str:
                    k, v = header_str.split(':', 1)
                    headers[k.strip().lower()] = v.strip()
                    if k.strip().lower() == 'content-length':
                        content_length = int(v.strip())

            if content_length < 0 or content_length > 1_000_000:
                response_body = b'{"error":"request body too large"}'
                writer.write(
                    b"HTTP/1.1 413 Payload Too Large\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(response_body)}\r\nConnection: close\r\n\r\n".encode()
                    + response_body
                )
                await writer.drain()
                return

            # Read body
            body_bytes = b""
            if content_length > 0:
                body_bytes = await reader.readexactly(content_length)

            # Route request
            if method == "POST" and (path == "/" or path == "/rpc"):
                try:
                    rpc_req = json.loads(body_bytes.decode('utf-8'))
                    rpc_method = rpc_req.get("method", "") if isinstance(rpc_req, dict) else ""
                    if self._method_requires_auth(rpc_method) and not self._authorized(headers):
                        response_body = json.dumps({"jsonrpc": "2.0", "error": {"code": -32001, "message": "RPC authentication required"}, "id": rpc_req.get("id")}).encode()
                        status_line = "HTTP/1.1 401 Unauthorized\r\n"
                    else:
                        rpc_res = await self.process_rpc(rpc_req)
                        response_body = json.dumps(rpc_res).encode('utf-8')
                        status_line = "HTTP/1.1 200 OK\r\n"
                except Exception as e:
                    response_body = json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": None}).encode('utf-8')
                    status_line = "HTTP/1.1 500 Internal Server Error\r\n"
            elif method == "GET" and path == "/info":
                info = self.get_node_info()
                response_body = json.dumps(info, indent=2).encode('utf-8')
                status_line = "HTTP/1.1 200 OK\r\n"
            else:
                response_body = b'{"status": "BTC-K node running"}'
                status_line = "HTTP/1.1 200 OK\r\n"

            # Send HTTP response
            resp_headers = (
                f"{status_line}"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                f"Connection: close\r\n"
                f"X-Content-Type-Options: nosniff\r\n\r\n"
            ).encode('utf-8')

            writer.write(resp_headers + response_body)
            await writer.drain()

        except Exception as e:
            logger.debug(f"HTTP handler error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _method_requires_auth(method: str) -> bool:
        return method in {"submitAlert", "addPeer", "submitBlock", "submitTransaction", "scanStealthBalance"}

    def _authorized(self, headers: dict) -> bool:
        if not self.rpc_token:
            return self.host in {"127.0.0.1", "::1", "localhost"}
        supplied = headers.get("authorization", "")
        expected = f"Bearer {self.rpc_token}"
        return hmac.compare_digest(supplied, expected)

    def get_node_info(self) -> dict:
        dag_info = self.dag.get_dag_info()
        return {
            "version": "1.0.0-cypherpunk",
            "protocol_version": 1,
            "network": {
                "name": self.network.name,
                "chain_id": self.network.chain_id,
                "p2p_magic": self.network.p2p_magic,
                "genesis_hash": self.dag.genesis_hash,
                "consensus_fingerprint": self.network.consensus_fingerprint,
                "data_dir": str(self.data_dir),
                "manifest": str(self.network.manifest_path),
                "persistence_enabled": self.chain_store is not None,
                "replayed_blocks": self.replayed_blocks,
            },
            "uptime_seconds": int(time.time() - self.start_time),
            "block_time_target": "1.0s",
            "safeguards": {
                "safe_mode": consensus_guardian.alerts.is_safe_mode(),
                "latest_notice": consensus_guardian.alerts.get_latest_broadcast_notice()
            },
            "dag": dag_info,
            "p2p": {
                "port": self.p2p_port,
                "connected_peers": len(self.p2p.peers),
                "seed_peers": self.p2p.seed_peers,
                "peers": self.p2p.get_peers_info()
            }
        }

    async def process_rpc(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "getInfo":
            return {"jsonrpc": "2.0", "result": self.get_node_info(), "id": req_id}

        elif method == "getAlertInfo":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "safe_mode": consensus_guardian.alerts.is_safe_mode(),
                    "latest_notice": consensus_guardian.alerts.get_latest_broadcast_notice(),
                    "active_alert_count": len(consensus_guardian.alerts.active_alerts)
                },
                "id": req_id
            }

        elif method == "submitAlert":
            try:
                payload_dict = params.get("payload", {})
                sig_hex = params.get("signature_hex", "")
                payload = NetworkAlertPayload(**payload_dict)
                signed_alert = SignedNetworkAlert(payload=payload, signature_hex=sig_hex)
                success, msg = consensus_guardian.alerts.process_incoming_alert(signed_alert)
                if success and payload.checkpoint_height and payload.checkpoint_hash:
                    consensus_guardian.checkpoints.add_checkpoint(payload.checkpoint_height, payload.checkpoint_hash)
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "accepted": success,
                        "message": msg,
                        "safe_mode_active": consensus_guardian.alerts.is_safe_mode()
                    },
                    "id": req_id
                }
            except Exception as e:
                return {"jsonrpc": "2.0", "error": {"code": -32602, "message": str(e)}, "id": req_id}

        elif method == "getPeerInfo":
            return {"jsonrpc": "2.0", "result": self.p2p.get_peers_info(), "id": req_id}

        elif method == "addPeer":
            peer_addr = params.get("peer")
            if not peer_addr:
                return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing 'peer' parameter (expected 'host:port')"}, "id": req_id}
            asyncio.create_task(self.p2p.connect_to_peer(peer_addr))
            return {"jsonrpc": "2.0", "result": {"initiated": True, "peer": peer_addr}, "id": req_id}

        elif method == "getBlockDagInfo":
            return {"jsonrpc": "2.0", "result": self.dag.get_dag_info(), "id": req_id}

        elif method == "getBlockTemplate":
            tips = self.dag.get_tips()
            selected = self.dag.blocks[self.dag.selected_tip]
            return {
                "jsonrpc": "2.0",
                "result": {
                    "parents": tips,
                    "version": self.network.block_version,
                    "chain_id": self.network.chain_id,
                    "target_bits": self.network.pow_limit_bits,
                    "blue_score": selected.blue_score + 1,
                    "timestamp": max(time.time(), max(self.dag.blocks[parent].timestamp for parent in tips) + 0.000001),
                    "transactions": [transaction_to_dict(tx) for tx in self.mempool.values()],
                    "mempool_size": len(self.mempool),
                },
                "id": req_id
            }

        elif method == "submitBlock":
            # params: {parents, timestamp, bits, nonce, coinbase_output: [R_hex, P_hex]}
            try:
                if params.get("chain_id", self.network.chain_id) != self.network.chain_id:
                    raise ValueError("Submitted block chain_id does not match this node")
                parents = params.get("parents", self.dag.get_tips())
                ts = params.get("timestamp", time.time())
                bits = params.get("bits", self.network.pow_limit_bits)
                nonce = params.get("nonce", 0)
                cb = params.get("coinbase_output", ["00" * 33, "00" * 33])
                cb_tuple = (bytes.fromhex(cb[0]), bytes.fromhex(cb[1]))
                transactions = [transaction_from_dict(raw) for raw in params.get("transactions", [])]
                
                block = DAGBlock(
                    parents,
                    ts,
                    bits,
                    nonce,
                    cb_tuple,
                    transactions=transactions,
                    version=params.get("version", self.network.block_version),
                    chain_id=self.network.chain_id,
                )
                success = self.accept_block(block)
                if success:
                    await self.p2p.broadcast_block(block.hash)
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "accepted": success,
                        "hash": block.hash,
                        "blue_score": block.blue_score,
                        "reward_btck": block.reward_sats / SATS_PER_COIN,
                        "is_combo_10x": block.is_combo
                    },
                    "id": req_id
                }
            except Exception as e:
                return {"jsonrpc": "2.0", "error": {"code": -32600, "message": str(e)}, "id": req_id}

        elif method == "submitTransaction":
            try:
                tx = transaction_from_dict(params.get("transaction"))
                result = self.submit_transaction(tx)
                return {"jsonrpc": "2.0", "result": result, "id": req_id}
            except Exception as e:
                return {"jsonrpc": "2.0", "error": {"code": -32602, "message": str(e)}, "id": req_id}

        elif method == "getTransaction":
            txid = str(params.get("txid", ""))
            if txid in self.mempool:
                return {"jsonrpc": "2.0", "result": {"txid": txid, "status": "mempool"}, "id": req_id}
            for block_hash in self.dag.virtual_selected_parent_chain:
                block = self.dag.blocks[block_hash]
                if any(tx.txid == txid for tx in block.transactions):
                    return {
                        "jsonrpc": "2.0",
                        "result": {"txid": txid, "status": "confirmed", "block_hash": block_hash, "blue_score": block.blue_score},
                        "id": req_id,
                    }
            return {"jsonrpc": "2.0", "result": {"txid": txid, "status": "unknown"}, "id": req_id}

        elif method == "getUTXOSet":
            outputs = []
            for (txid, vout), entry in sorted(self.dag.utxo_set.utxos.items()):
                if not entry.R_ephem_hex or not entry.P_onetime_hex:
                    continue
                outputs.append({
                    "txid": txid,
                    "vout": vout,
                    "amount_satk": entry.amount_satk,
                    "R_ephem_hex": entry.R_ephem_hex,
                    "P_onetime_hex": entry.P_onetime_hex,
                    "height": entry.block_height,
                    "is_coinbase": entry.is_coinbase,
                    "timelock_until_height": entry.timelock_until_height,
                    "kind": entry.kind,
                })
            return {
                "jsonrpc": "2.0",
                "result": {"selected_height": len(self.dag.virtual_selected_parent_chain) - 1, "outputs": outputs},
                "id": req_id,
            }

        elif method == "scanStealthBalance":
            # View-Key-only scanning (Chapter 8 Audit: Spend key NEVER sent to node)
            # params: {view_priv_scalar, spend_pub_hex}
            if "spend_priv_scalar" in params:
                return {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": "Security policy violation: Spend private key must NEVER be transmitted to node. Provide only 'view_priv_scalar' and 'spend_pub_hex'."
                    },
                    "id": req_id
                }
            try:
                view_priv = int(params["view_priv_scalar"])
                spend_pub_hex = params.get("spend_pub_hex")
                spend_pub_bytes = bytes.fromhex(spend_pub_hex) if spend_pub_hex else None
                wallet = StealthKeyPair(spend_priv_scalar=None, view_priv_scalar=view_priv, spend_pub_bytes=spend_pub_bytes)
                
                my_utxos = []
                total_sats = 0
                for h, b in self.dag.blocks.items():
                    R, P = b.coinbase_output
                    if wallet.is_my_stealth_output(R, P):
                        my_utxos.append({
                            "block_hash": h,
                            "blue_score": b.blue_score,
                            "sats": b.reward_sats,
                            "btck": b.reward_sats / SATS_PER_COIN,
                            "is_combo_10x": b.is_combo
                        })
                        total_sats += b.reward_sats

                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "stealth_address": wallet.get_public_stealth_address(),
                        "utxo_count": len(my_utxos),
                        "total_sats": total_sats,
                        "total_btck": total_sats / SATS_PER_COIN,
                        "utxos": my_utxos
                    },
                    "id": req_id
                }
            except Exception as e:
                return {"jsonrpc": "2.0", "error": {"code": -32602, "message": str(e)}, "id": req_id}

        else:
            return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method '{method}' not found"}, "id": req_id}

    async def start(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.running = True
        await self.p2p.start()
        self.server = await asyncio.start_server(self.handle_http_request, self.host, self.port)
        addrs = ', '.join(str(sock.getsockname()) for sock in self.server.sockets)
        logger.info(f"🌐 btckd Node Daemon listening on {addrs} (RPC on port {self.port}, P2P on port {self.p2p_port})")

    async def stop(self):
        self.running = False
        await self.p2p.stop()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("btckd Node Daemon stopped.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BTC-K High-Speed BlockDAG Node Daemon (btckd)")
    parser.add_argument("--host", default="127.0.0.1", help="RPC listen host (default: loopback only)")
    parser.add_argument("--port", type=int, default=16110, help="RPC listen port (default: 16110)")
    parser.add_argument("--p2p-host", default="0.0.0.0", help="P2P listen host (default: 0.0.0.0)")
    parser.add_argument("--p2p-port", type=int, default=16111, help="P2P listen port (default: 16111)")
    parser.add_argument("--seed", action="append", default=[], help="Seed peer in host:port format (can specify multiple)")
    parser.add_argument("--network", choices=("devnet", "testnet", "mainnet"), default=os.getenv("BTCK_NETWORK", "devnet"), help="Network to join (default: devnet)")
    parser.add_argument("--network-manifest", default=os.getenv("BTCK_NETWORK_MANIFEST"), help="Explicit network manifest path")
    parser.add_argument("--data-root", default=os.getenv("BTCK_DATA_ROOT"), help="Storage root; the network name is always appended")
    parser.add_argument("--ephemeral", action="store_true", help="Disable the accepted-block journal (tests only)")
    parser.add_argument("--rpc-token", default=os.getenv("BTCK_RPC_TOKEN"), help="RPC bearer token (prefer BTCK_RPC_TOKEN)")
    args = parser.parse_args()

    selected_network = load_network(args.network, args.network_manifest)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    daemon = BTCKDaemon(
        host=args.host,
        port=args.port,
        p2p_host=args.p2p_host,
        p2p_port=args.p2p_port,
        seed_peers=args.seed,
        network=selected_network,
        data_root=args.data_root,
        persistent=not args.ephemeral,
        rpc_token=args.rpc_token,
    )
    try:
        loop.run_until_complete(daemon.start())
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(daemon.stop())

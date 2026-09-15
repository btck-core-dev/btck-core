"""
BTC-K Node RPC Daemon (btckd)
-----------------------------
Autonomous full node daemon hosting the BlockDAG consensus engine,
serving JSON-RPC 2.0 for Stratum bridges, wallets, and AI Agent clients.
Zero external dependencies (uses standard library asyncio).
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional

import sys
sys.path.insert(0, '/Users/a1234/btck-core/src')

from node.blockdag import BlockDAG, DAGBlock
from privacy.stealth_address import StealthKeyPair
from tokenomics.emission_schedule import SATS_PER_COIN
from p2p.p2p_network import P2PNetwork

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [btckd] %(message)s'
)
logger = logging.getLogger("btckd")

class BTCKDaemon:
    def __init__(self, host: str = "0.0.0.0", port: int = 16110, p2p_port: int = 16111, seed_peers: Optional[list] = None):
        self.host = host
        self.port = port
        self.p2p_port = p2p_port
        self.dag = BlockDAG()
        self.p2p = P2PNetwork(dag=self.dag, p2p_host=host, p2p_port=p2p_port, seed_peers=seed_peers)
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self.start_time = time.time()
        logger.info(f"Node initialized with Genesis Block: {self.dag.genesis_hash[:16]}... (P2P on port {self.p2p_port})")

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

            # Read body
            body_bytes = b""
            if content_length > 0:
                body_bytes = await reader.readexactly(content_length)

            # Route request
            if method == "POST" and (path == "/" or path == "/rpc"):
                try:
                    rpc_req = json.loads(body_bytes.decode('utf-8'))
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
                f"Access-Control-Allow-Origin: *\r\n\r\n"
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

    def get_node_info(self) -> dict:
        dag_info = self.dag.get_dag_info()
        return {
            "version": "1.0.0-cypherpunk",
            "protocol_version": 1,
            "uptime_seconds": int(time.time() - self.start_time),
            "block_time_target": "1.0s",
            "dag": dag_info
        }

    async def process_rpc(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "getInfo":
            return {"jsonrpc": "2.0", "result": self.get_node_info(), "id": req_id}

        elif method == "getBlockDagInfo":
            return {"jsonrpc": "2.0", "result": self.dag.get_dag_info(), "id": req_id}

        elif method == "getBlockTemplate":
            tips = self.dag.get_tips()
            selected = self.dag.blocks[self.dag.selected_tip]
            return {
                "jsonrpc": "2.0",
                "result": {
                    "parents": tips,
                    "target_bits": 1,
                    "blue_score": selected.blue_score + 1,
                    "timestamp": time.time()
                },
                "id": req_id
            }

        elif method == "submitBlock":
            # params: {parents, timestamp, bits, nonce, coinbase_output: [R_hex, P_hex]}
            try:
                parents = params.get("parents", self.dag.get_tips())
                ts = params.get("timestamp", time.time())
                bits = params.get("bits", 1)
                nonce = params.get("nonce", 0)
                cb = params.get("coinbase_output", ["00" * 33, "00" * 33])
                cb_tuple = (bytes.fromhex(cb[0]), bytes.fromhex(cb[1]))
                
                block = DAGBlock(parents, ts, bits, nonce, cb_tuple)
                success = self.dag.add_block(block)
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    daemon = BTCKDaemon()
    try:
        loop.run_until_complete(daemon.start())
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(daemon.stop())

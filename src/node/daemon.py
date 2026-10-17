"""
BTC-K Node RPC Daemon (btckd)
-----------------------------
Autonomous full node daemon hosting the BlockDAG consensus engine,
serving JSON-RPC 2.0 for Stratum bridges, wallets, and AI Agent clients.
Zero external dependencies (uses standard library asyncio).
"""

import asyncio
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


class UTXOOverlay:
    """Copy-on-write view over the consensus UTXO map for mempool checks."""

    def __init__(self, base):
        self.base = base
        self.updates = {}
        self.spent = set()

    def get(self, outpoint, default=None):
        if outpoint in self.spent:
            return default
        if outpoint in self.updates:
            return self.updates[outpoint]
        return self.base.get(outpoint, default)

    def pop(self, outpoint, default=None):
        value = self.get(outpoint, default)
        self.updates.pop(outpoint, None)
        self.spent.add(outpoint)
        return value

    def __setitem__(self, outpoint, entry):
        self.spent.discard(outpoint)
        self.updates[outpoint] = entry

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
        self.latest_checkpoint_sequence = 0
        self.latest_checkpoint_state_root = ""
        if persistent:
            self.chain_store = ChainStore(self.data_dir / "chain.sqlite3", self.network)
            self.replayed_blocks = self.chain_store.replay_into(self.dag)
            checkpoint = self.chain_store.latest_checkpoint()
            if checkpoint:
                self.latest_checkpoint_sequence, self.latest_checkpoint_state_root = checkpoint
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
        commit_hook = self._persist_accepted_block if self.chain_store else None
        accepted = self.dag.add_block(block, commit_hook=commit_hook)
        if accepted:
            # With no transactions there is nothing to remove from mempool;
            # constructing a lifetime selected-chain set is wasted O(height).
            if block.transactions:
                selected_hashes = set(self.dag.virtual_selected_parent_chain)
                if block.hash in selected_hashes:
                    for tx in block.transactions:
                        self.mempool.pop(tx.txid, None)
            self._revalidate_mempool()
        return accepted

    def _persist_accepted_block(self, block: DAGBlock) -> None:
        """Persist every block without putting lifetime work on the hot path."""
        if not self.chain_store:
            return
        if not self.chain_store.should_checkpoint_next_block():
            self.chain_store.append_block(block)
            return

        # Inline checkpoints are retained only as an explicit development/test
        # option. Production checkpoints and snapshots are built from a stopped,
        # immutable database copy by deploy/verify_chain_replay.py.
        state_root = self.dag.state_root() if self.chain_store.should_checkpoint_next_block() else ""
        sequence = self.chain_store.append_block(block, state_root)
        if state_root:
            self.latest_checkpoint_sequence = sequence
            self.latest_checkpoint_state_root = state_root
            if self.chain_store.should_snapshot(sequence):
                try:
                    self.chain_store.write_snapshot(self.dag, sequence, block.hash, state_root)
                except Exception:
                    # The accepted-block journal is already durable. A snapshot
                    # is a recovery accelerator, never a reason to roll back a
                    # consensus-valid block or diverge from peers.
                    logger.exception("Failed to write consensus snapshot at sequence %d", sequence)

    def _mempool_overlay(self):
        # A full deepcopy is proportional to lifetime UTXO count and blocked
        # the single-threaded RPC/mining loop for more than ten seconds on
        # mainnet. Mempool validation needs only point lookups plus its own
        # spends/outputs, so keep a small copy-on-write delta over consensus.
        overlay = UTXOOverlay(self.dag.utxo_set.utxos)
        # UTXO heights, coinbase maturity and timelocks are consensus-defined
        # in the canonical virtual blue order.  Selected-parent steps can be
        # dramatically shorter on a busy DAG and must never be used here.
        height = len(self.dag.virtual_blue_order) - 1
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
        # Bound duplicate-confirmation lookup. Scanning the lifetime DAG on
        # every public wallet submission grows linearly and can stall mining.
        # Mempool duplicates remain fully idempotent; recently confirmed
        # resubmissions are recognized across a generous 2,048-blue window.
        for block_hash in reversed(self.dag.virtual_blue_order[-2048:]):
            if tx.txid in self.dag.utxo_set.get_block_accepted_txids(block_hash):
                return {"accepted": True, "txid": tx.txid, "status": "confirmed"}
        overlay = self._mempool_overlay()
        height = len(self.dag.virtual_blue_order) - 1
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
            elif method == "GET" and path == "/dashboard":
                response_body = json.dumps({
                    "network": {
                        "name": self.network.name,
                        "chain_id": self.network.chain_id,
                        "genesis_hash": self.dag.genesis_hash,
                        "consensus_fingerprint": self.network.consensus_fingerprint,
                    },
                    "block_time_target_seconds": self.network.daa_target_interval_ms / 1000.0,
                    "dag": self.dag.get_dashboard_info(),
                    "generated_at": time.time(),
                }, separators=(",", ":")).encode("utf-8")
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
        # Full state-root construction serializes the lifetime DAG and can take
        # minutes on a long chain.  Online status endpoints must stay bounded;
        # expose the newest already-verified durable checkpoint instead.
        dag_info = self.dag.get_dag_info(include_state_root=False)
        dag_info["state_root"] = self.latest_checkpoint_state_root
        dag_info["state_root_is_checkpoint"] = bool(self.latest_checkpoint_state_root)
        dag_info["state_root_checkpoint_sequence"] = self.latest_checkpoint_sequence
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
                "loaded_snapshot_sequence": (
                    self.chain_store.loaded_snapshot_sequence if self.chain_store else 0
                ),
                "tail_replayed_blocks": (
                    self.chain_store.tail_replayed_blocks if self.chain_store else 0
                ),
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
                "peers": self.p2p.get_peers_info(),
                "orphan_count": len(self.p2p.orphan_pool),
                "orphan_bytes": self.p2p.orphan_bytes,
                "recovery_roots": len(getattr(self.p2p, "_orphan_recovery_roots", ())),
                "sync_counters": dict(self.p2p.sync_counters),
                "sync_events": list(self.p2p.sync_events)[-24:],
            }
        }

    def get_wallet_sync_page(self, params: dict) -> dict:
        """Return a bounded, secret-free canonical ledger delta for local wallets.

        Clients identify their own stealth outputs locally.  The request contains
        only a public cursor; it must never contain an address or view/spend key.
        """
        if not isinstance(params, dict):
            raise ValueError("wallet sync parameters must be an object")
        forbidden = {
            "address", "wallet_address", "view_priv", "view_priv_scalar",
            "spend_priv", "spend_priv_scalar", "spend_pub_hex",
        }
        leaked = sorted(forbidden.intersection(params))
        if leaked:
            raise ValueError("wallet sync refuses secret or identity parameters")

        order = self.dag.virtual_blue_order
        tip_height = len(order) - 1
        from_height = int(params.get("from_height", 0))
        limit = max(1, min(int(params.get("limit", 250)), 1000))
        unspent_only = bool(params.get("unspent_only", False))
        if from_height < 0 or from_height > tip_height + 1:
            raise ValueError("from_height is outside the canonical ledger")
        if from_height > 0:
            anchor_hash = str(params.get("anchor_hash", ""))
            expected_anchor = order[from_height - 1]
            if anchor_hash != expected_anchor:
                raise ValueError("wallet sync cursor is stale after a canonical reorganization")

        end_height = min(tip_height + 1, from_height + limit)
        resolved_entries = {}
        unresolved = set()
        for height in range(from_height, end_height):
            block_hash = order[height]
            for outpoint in self.dag.utxo_set._block_created_undo.get(block_hash, []):
                entry = self.dag.utxo_set.utxos.get(outpoint)
                if entry is not None:
                    resolved_entries[outpoint] = entry
                elif not unspent_only and not str(outpoint[0]).startswith("pool_"):
                    unresolved.add(outpoint)

        # Most mainnet outputs are still present in the O(1) UTXO map.  Only
        # recover page outputs that were actually spent, walking the canonical
        # suffix newest-first and stopping as soon as those few entries are
        # found.  The previous implementation rebuilt a lifetime-wide mapping
        # for every page, which made wallet sync O(chain length * page count)
        # and caused large transient allocations on the production node.
        if unresolved:
            for spending_hash in reversed(order[from_height:]):
                for outpoint, entry in self.dag.utxo_set._block_spent_undo.get(spending_hash, []):
                    if outpoint in unresolved:
                        resolved_entries[outpoint] = entry
                        unresolved.remove(outpoint)
                if not unresolved:
                    break

        blocks = []
        for height in range(from_height, end_height):
            block_hash = order[height]
            created = []
            for txid, vout in self.dag.utxo_set._block_created_undo.get(block_hash, []):
                outpoint = (txid, vout)
                entry = resolved_entries.get(outpoint)
                if entry is None or not entry.R_ephem_hex or not entry.P_onetime_hex:
                    continue
                created.append({
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
            spent = [] if unspent_only else [
                {"txid": outpoint[0], "vout": outpoint[1]}
                for outpoint, _entry in self.dag.utxo_set._block_spent_undo.get(block_hash, [])
            ]
            blocks.append({
                "height": height,
                "hash": block_hash,
                "created": created,
                "spent": spent,
            })

        next_height = end_height
        anchor_hash = order[next_height - 1] if next_height > 0 else ""
        return {
            "format": "btck-wallet-sync-v1",
            "network": {
                "name": self.network.name,
                "chain_id": self.network.chain_id,
                "genesis_hash": self.dag.genesis_hash,
                "consensus_fingerprint": self.network.consensus_fingerprint,
            },
            "from_height": from_height,
            "next_height": next_height,
            "anchor_hash": anchor_hash,
            "tip_height": tip_height,
            "tip_hash": order[tip_height] if tip_height >= 0 else "",
            "done": next_height > tip_height,
            "unspent_only": unspent_only,
            "blocks": blocks,
        }

    async def process_rpc(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "getInfo":
            return {"jsonrpc": "2.0", "result": self.get_node_info(), "id": req_id}

        elif method == "getWalletSyncPage":
            try:
                return {
                    "jsonrpc": "2.0",
                    "result": self.get_wallet_sync_page(params),
                    "id": req_id,
                }
            except (TypeError, ValueError) as exc:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": str(exc)},
                    "id": req_id,
                }

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
            if self.network.is_mainnet:
                return {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32601,
                        "message": "MAINNET operator alerts are disabled; consensus checkpoints require a reviewed manifest release",
                    },
                    "id": req_id,
                }
            try:
                payload_dict = params.get("payload", {})
                sig_hex = params.get("signature_hex", "")
                payload = NetworkAlertPayload(**payload_dict)
                signed_alert = SignedNetworkAlert(payload=payload, signature_hex=sig_hex)
                success, msg = consensus_guardian.alerts.process_incoming_alert(signed_alert)
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
            dag_info = self.dag.get_dag_info(include_state_root=False)
            dag_info["state_root"] = self.latest_checkpoint_state_root
            dag_info["state_root_is_checkpoint"] = bool(self.latest_checkpoint_state_root)
            dag_info["state_root_checkpoint_sequence"] = self.latest_checkpoint_sequence
            return {"jsonrpc": "2.0", "result": dag_info, "id": req_id}

        elif method == "getDashboardInfo":
            limit = int(params.get("recent_limit", 120)) if isinstance(params, dict) else 120
            return {"jsonrpc": "2.0", "result": self.dag.get_dashboard_info(limit), "id": req_id}

        elif method == "getBlockTemplate":
            tips = self.dag.get_mining_parents()
            selected = self.dag.blocks[self.dag.selected_tip]
            target_bits = self.dag.expected_bits(tips)
            return {
                "jsonrpc": "2.0",
                "result": {
                    "parents": tips,
                    "version": self.network.block_version,
                    "chain_id": self.network.chain_id,
                    "target_bits": target_bits,
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
                        "is_combo": block.is_combo
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
            order = self.dag.virtual_blue_order
            tip_height = len(order) - 1
            # Never walk lifetime block bodies in the single RPC/mining loop.
            # Consensus already journals exactly the transactions it accepted
            # for each canonical block, including through snapshot recovery.
            start_height = max(0, len(order) - 2048)
            hinted_height = params.get('height')
            hinted_hash = params.get('block_hash')
            if hinted_height is not None or hinted_hash is not None:
                if (type(hinted_height) is not int or not 0 <= hinted_height < len(order)
                        or not isinstance(hinted_hash, str) or order[hinted_height] != hinted_hash):
                    return {"jsonrpc": "2.0", "error": {"code": -32602,
                            "message": "canonical transaction locator mismatch"}, "id": req_id}
                heights = (hinted_height,)
            else:
                heights = range(tip_height, start_height - 1, -1)
            for height in heights:
                block_hash = order[height]
                if txid in self.dag.utxo_set.get_block_accepted_txids(block_hash):
                    block = self.dag.blocks[block_hash]
                    return {
                        "jsonrpc": "2.0",
                        "result": {
                            "txid": txid,
                            "status": "confirmed",
                            "block_hash": block_hash,
                            "blue_score": block.blue_score,
                            "height": height,
                            "confirmations": tip_height - height + 1,
                        },
                        "id": req_id,
                    }
            return {"jsonrpc": "2.0", "result": {"txid": txid, "status": "unknown",
                    "lookup_scope": "canonical_locator" if hinted_height is not None else "recent_2048",
                    "tip_height": tip_height}, "id": req_id}

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
                # UTXO block heights and coinbase maturity are defined by the
                # canonical virtual blue ledger order, not selected-parent
                # steps. Keep the legacy field name for wallet compatibility.
                "result": {"selected_height": len(self.dag.virtual_blue_order) - 1, "outputs": outputs},
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
                            "is_combo": b.is_combo
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

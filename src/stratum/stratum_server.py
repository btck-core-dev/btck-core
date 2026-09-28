#!/usr/bin/env python3
"""Node-backed BTC-K Stratum bridge.

This process is deliberately stateless with respect to consensus. It obtains
templates from btckd and submits complete candidates back to btckd; it never
mints rewards, advances height, or writes an independent chain ledger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from config.network import NetworkConfig, load_network, require_feature
from consensus.kheavyhash import hash_to_target_int
from consensus.pow import MAX_UINT256, compact_to_target
from node.blockdag import DAGBlock
from node.rpc_client import HTTPNodeRPCClient, RPCError
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthTransaction, transaction_from_dict, transaction_to_dict


logger = logging.getLogger("btck-stratum")


@dataclass
class MiningJob:
    job_id: str
    owner_id: str
    worker_name: str
    parents: list[str]
    version: int
    chain_id: str
    timestamp: float
    bits: int
    coinbase_output: tuple[bytes, bytes]
    transactions: list[StealthTransaction]
    created_at: float
    is_clean: bool = True

    def build_block(self, nonce: int) -> DAGBlock:
        return DAGBlock(
            parents=self.parents,
            timestamp=self.timestamp,
            bits=self.bits,
            nonce=nonce,
            coinbase_output=self.coinbase_output,
            transactions=self.transactions,
            version=self.version,
            chain_id=self.chain_id,
        )

    def notify_params(self) -> list:
        """BTC-K v2 job payload; nonce is the only mutable field."""
        return [
            self.job_id,
            {
                "version": self.version,
                "chain_id": self.chain_id,
                "parents": self.parents,
                "timestamp": self.timestamp,
                "bits": self.bits,
                "coinbase_output": [self.coinbase_output[0].hex(), self.coinbase_output[1].hex()],
                "transactions": [transaction_to_dict(tx) for tx in self.transactions],
                "nonce_encoding": "uint64-hex",
            },
            self.is_clean,
        ]


class StratumClient:
    def __init__(self, reader, writer, client_id: str):
        self.reader = reader
        self.writer = writer
        self.client_id = client_id
        self.wallet_address = ""
        self.worker_name = ""
        self.authorized = False
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.blocks_found = 0
        self.last_submit_at = 0.0

    async def send_json(self, data: dict) -> None:
        self.writer.write((json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8"))
        await self.writer.drain()


class BTCKStratumServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3333,
        node_rpc=None,
        node_rpc_url: str = "http://127.0.0.1:16110/rpc",
        rpc_token: Optional[str] = None,
        network: Optional[NetworkConfig | str] = None,
        share_target: Optional[int] = None,
        job_ttl_seconds: int = 30,
        max_clients: int = 256,
    ):
        self.host = host
        self.port = port
        self.network = network if isinstance(network, NetworkConfig) else load_network(network)
        self.node_rpc = node_rpc or HTTPNodeRPCClient(node_rpc_url, rpc_token or os.getenv("BTCK_RPC_TOKEN"))
        share_target = compact_to_target(self.network.pow_limit_bits) if share_target is None else share_target
        if not 0 < share_target <= MAX_UINT256:
            raise ValueError("share_target must be a positive uint256")
        self.share_target = share_target
        self.job_ttl_seconds = job_ttl_seconds
        self.max_clients = max_clients
        self.clients: Dict[str, StratumClient] = {}
        self.current_jobs: Dict[str, MiningJob] = {}
        self.submitted_shares: set[tuple[str, int]] = set()
        self._job_counter = 0
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self._refresh_task: Optional[asyncio.Task] = None

    async def _verify_node_identity(self) -> None:
        info = await self.node_rpc.call("getInfo")
        identity = info["network"]
        expected = self.network
        if (
            identity.get("name") != expected.name
            or identity.get("chain_id") != expected.chain_id
            or identity.get("genesis_hash") != expected.genesis.expected_hash
            or identity.get("consensus_fingerprint") != expected.consensus_fingerprint
        ):
            raise RuntimeError("Stratum node identity does not match the selected network manifest")

    async def create_new_block_job(self, client: StratumClient, is_clean: bool = True) -> MiningJob:
        if not client.authorized:
            raise ValueError("Cannot create a mining job for an unauthorized client")
        template = await self.node_rpc.call("getBlockTemplate")
        if template["chain_id"] != self.network.chain_id:
            raise RuntimeError("Node returned a template for another chain")
        r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(client.wallet_address)
        self._job_counter += 1
        job = MiningJob(
            job_id=str(self._job_counter),
            owner_id=client.client_id,
            worker_name=client.worker_name,
            parents=list(template["parents"]),
            version=int(template["version"]),
            chain_id=str(template["chain_id"]),
            timestamp=float(template["timestamp"]),
            bits=int(template["target_bits"]),
            coinbase_output=(r_ephem, p_onetime),
            transactions=[transaction_from_dict(raw) for raw in template.get("transactions", [])],
            created_at=time.time(),
            is_clean=is_clean,
        )
        self.current_jobs[job.job_id] = job
        self._prune_jobs()
        return job

    def _prune_jobs(self) -> None:
        cutoff = time.time() - self.job_ttl_seconds
        stale = [job_id for job_id, job in self.current_jobs.items() if job.created_at < cutoff]
        for job_id in stale:
            self.current_jobs.pop(job_id, None)
        if len(self.current_jobs) > 1000:
            ordered = sorted(self.current_jobs.values(), key=lambda job: job.created_at)
            for job in ordered[:-1000]:
                self.current_jobs.pop(job.job_id, None)
        live_ids = set(self.current_jobs)
        self.submitted_shares = {key for key in self.submitted_shares if key[0] in live_ids}

    async def send_job_to_client(self, client: StratumClient, job: MiningJob) -> None:
        await client.send_json({"id": None, "method": "mining.notify", "params": job.notify_params()})

    async def _issue_job(self, client: StratumClient, is_clean: bool = True) -> MiningJob:
        job = await self.create_new_block_job(client, is_clean=is_clean)
        await self.send_job_to_client(client, job)
        return job

    @staticmethod
    def _parse_nonce(raw_nonce) -> int:
        if isinstance(raw_nonce, int):
            nonce = raw_nonce
        else:
            text = str(raw_nonce).strip().lower()
            if text.startswith("0x"):
                text = text[2:]
            if not text or len(text) > 16:
                raise ValueError("nonce must be 1-16 hexadecimal characters")
            nonce = int(text, 16)
        if not 0 <= nonce < 2**64:
            raise ValueError("nonce is outside uint64")
        return nonce

    async def process_message(self, client: StratumClient, msg: dict) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", [])

        if method == "mining.subscribe":
            await client.send_json(
                {"id": msg_id, "result": [[["mining.notify", "btck-v2"]], "", 0], "error": None}
            )
            await client.send_json(
                {"id": None, "method": "mining.set_target", "params": [f"{self.share_target:064x}"]}
            )
            return

        if method == "mining.authorize":
            full_user = str(params[0]) if params else ""
            address, _, worker = full_user.partition(".")
            try:
                StealthKeyPair.generate_one_time_stealth_output(address)
            except Exception:
                await client.send_json({"id": msg_id, "result": False, "error": [24, "Invalid BTC-K payout address", None]})
                return
            client.wallet_address = address
            client.worker_name = worker[:64] or "worker"
            client.authorized = True
            await client.send_json({"id": msg_id, "result": True, "error": None})
            await self._issue_job(client)
            return

        if method == "mining.submit":
            if not client.authorized:
                await client.send_json({"id": msg_id, "result": False, "error": [24, "Unauthorized worker", None]})
                return
            if len(params) < 3:
                await client.send_json({"id": msg_id, "result": False, "error": [20, "Expected worker, job id and nonce", None]})
                return
            job_id = str(params[1])
            raw_nonce = params[4] if len(params) >= 5 else params[2]
            job = self.current_jobs.get(job_id)
            if job is None or job.owner_id != client.client_id or time.time() - job.created_at > self.job_ttl_seconds:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [21, "Job not found / stale share", None]})
                return
            try:
                nonce = self._parse_nonce(raw_nonce)
            except (TypeError, ValueError) as exc:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [20, str(exc), None]})
                return
            share_key = (job_id, nonce)
            if share_key in self.submitted_shares:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [22, "Duplicate share", None]})
                return
            now = time.monotonic()
            if now - client.last_submit_at < 0.01:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [25, "Share rate limit exceeded", None]})
                return
            client.last_submit_at = now
            self.submitted_shares.add(share_key)

            candidate = job.build_block(nonce)
            hash_value = hash_to_target_int(bytes.fromhex(candidate.hash))
            if hash_value > self.share_target:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [23, "Low difficulty share", None]})
                return

            network_target = compact_to_target(job.bits)
            if hash_value <= network_target:
                try:
                    result = await self.node_rpc.call(
                        "submitBlock",
                        {
                            "parents": job.parents,
                            "version": job.version,
                            "chain_id": job.chain_id,
                            "timestamp": job.timestamp,
                            "bits": job.bits,
                            "nonce": nonce,
                            "coinbase_output": [job.coinbase_output[0].hex(), job.coinbase_output[1].hex()],
                            "transactions": [transaction_to_dict(tx) for tx in job.transactions],
                        },
                    )
                except RPCError as exc:
                    client.shares_rejected += 1
                    await client.send_json({"id": msg_id, "result": False, "error": [21, f"Node rejected candidate: {exc}", None]})
                    return
                if not result.get("accepted"):
                    client.shares_rejected += 1
                    await client.send_json({"id": msg_id, "result": False, "error": [21, "Node rejected stale candidate", None]})
                    return
                client.blocks_found += 1
                self.current_jobs.clear()
                self.submitted_shares.clear()

            client.shares_accepted += 1
            await client.send_json({"id": msg_id, "result": True, "error": None})
            if hash_value <= network_target:
                for connected in list(self.clients.values()):
                    if connected.authorized:
                        try:
                            await self._issue_job(connected)
                        except Exception as exc:
                            logger.warning("Could not refresh job for %s: %s", connected.client_id, exc)
            return

        if method == "mining.suggest_difficulty":
            await client.send_json({"id": msg_id, "result": False, "error": [20, "Client difficulty changes are not allowed", None]})
            return

        await client.send_json({"id": msg_id, "result": None, "error": [20, "Method not supported", None]})

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("unknown", 0)
        client_id = f"{peer[0]}:{peer[1]}"
        if len(self.clients) >= self.max_clients:
            writer.close()
            await writer.wait_closed()
            return
        client = StratumClient(reader, writer, client_id)
        self.clients[client_id] = client
        try:
            while self.running:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        raise ValueError("request must be an object")
                    await self.process_message(client, message)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    await client.send_json({"id": None, "result": None, "error": [20, "Malformed request", None]})
        finally:
            self.clients.pop(client_id, None)
            writer.close()
            await writer.wait_closed()

    async def _refresh_loop(self) -> None:
        while self.running:
            await asyncio.sleep(min(10, max(1, self.job_ttl_seconds // 2)))
            for client in list(self.clients.values()):
                if client.authorized:
                    try:
                        await self._issue_job(client, is_clean=False)
                    except Exception as exc:
                        logger.warning("Template refresh failed: %s", exc)

    async def start(self) -> None:
        await self._verify_node_identity()
        self.running = True
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port, limit=65_536)
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("BTC-K Stratum bridge listening on %s:%s for %s", self.host, self.port, self.network.name)

    async def stop(self) -> None:
        self.running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def _main() -> None:
    network = require_feature("stratum")
    server = BTCKStratumServer(
        host=os.getenv("BTCK_STRATUM_HOST", "127.0.0.1"),
        port=int(os.getenv("BTCK_STRATUM_PORT", "3333")),
        node_rpc_url=os.getenv("BTCK_NODE_RPC_URL", "http://127.0.0.1:16110/rpc"),
        network=network,
    )
    await server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [stratum] %(message)s")
    asyncio.run(_main())

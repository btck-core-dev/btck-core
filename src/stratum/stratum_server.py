#!/usr/bin/env python3
"""Node-backed BTC-K Stratum bridge.

This process is deliberately stateless with respect to consensus. It obtains
templates from btckd and submits complete candidates back to btckd; it never
mints rewards, advances height, or writes an independent chain ledger.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from config.network import NetworkConfig, load_network, require_feature
from consensus.genesis import build_pre_pow_hash
from consensus.kheavyhash import calculate_pow_value, generate_matrix, hash_to_target_int
from consensus.pow import MAX_UINT256, compact_to_target
from node.blockdag import DAGBlock, transaction_commitment
from node.rpc_client import HTTPNodeRPCClient, RPCError
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthTransaction, transaction_from_dict, transaction_to_dict
from tokenomics.emission_schedule import SATK_PER_COIN
from stratum.pplns import PPLNSEngine, AutoPayoutWorker

import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [stratum] %(message)s")
logger = logging.getLogger("btck-stratum")

CANONICAL_POOL_TREASURY_ADDRESS = (
    "btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23"
    "ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af"
)

# IceRiver firmware can submit work tens of seconds after it was announced.
# Keep enough per-client history to cover the late-block window and retain
# older valid shares for accounting, while preserving a strict memory bound.
CLIENT_JOB_HISTORY_LIMIT = 128


def diff_to_kaspa_target(diff: float) -> int:
    """Convert Kaspa/IceRiver difficulty to target integer (Kaspa MaxTarget = 2^224 - 1)."""
    kaspa_max_target = (1 << 224) - 1
    if diff <= 0:
        return kaspa_max_target
    return int(kaspa_max_target / diff)


PORT_PROFILES = {
    # IceRiver KS0 firmware enforces an effective difficulty floor of 128 even
    # when a smaller value is announced.  Account those shares at the actual
    # hardware threshold so reported hashrate is not understated.
    3333: {"name": "Entry / KS0 / GPU (<1 TH/s)", "base_diff": 128.0, "min_diff": 128.0, "max_diff": 128.0},
    3334: {"name": "Mid / KS1 / KS2 (1-3 TH/s)", "base_diff": 4096.0, "min_diff": 2048.0, "max_diff": 131072.0},
    3335: {"name": "High / KS3 / KS3L (6-10 TH/s)", "base_diff": 16384.0, "min_diff": 8192.0, "max_diff": 262144.0},
    3336: {"name": "Ultra / KS5 / KS7 / Large (20-100+ TH/s)", "base_diff": 65536.0, "min_diff": 32768.0, "max_diff": 1048576.0},
}


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
    share_target: int
    share_diff: float
    is_clean: bool = True
    header_hash: bytes = b""
    timestamp_ms: int = 0
    matrix: list = None
    is_pool_fee_job: bool = False
    assigned_payout_address: str = ""
    template_key: tuple = ()

    def __post_init__(self):
        if not self.header_hash:
            self.header_hash = build_pre_pow_hash(
                version=self.version,
                chain_id=self.chain_id,
                parents=self.parents,
                timestamp=self.timestamp,
                bits=self.bits,
                coinbase_output=self.coinbase_output,
                payload_hash=transaction_commitment(self.transactions),
            )
        if not self.timestamp_ms:
            self.timestamp_ms = int(self.timestamp * 1000)
        if self.matrix is None:
            self.matrix = generate_matrix(self.header_hash)

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

    def calculate_pow(self, nonce: int) -> bytes:
        return calculate_pow_value(
            self.header_hash, self.timestamp_ms, nonce, self.matrix
        )

    def notify_params(self, is_kaspa: bool = False) -> list:
        if is_kaspa:
            # Official Kaspa Stratum specification for IceRiver / BzMiner (GenerateLargeJobParams):
            # Strictly 80 hex characters:
            # 4 uint64 header hashes (64 hex characters) + 1 uint64 timestamp (16 hex characters)
            ids = [
                int.from_bytes(self.header_hash[0:8], "big"),
                int.from_bytes(self.header_hash[8:16], "big"),
                int.from_bytes(self.header_hash[16:24], "big"),
                int.from_bytes(self.header_hash[24:32], "big"),
                int.from_bytes(self.timestamp_ms.to_bytes(8, "big"), "little"),
            ]
            payload = "".join(f"{v:016x}" for v in ids)
            return [str(self.job_id), payload]
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
    def __init__(self, reader, writer, client_id: str, client_num: int = 1, server_port: int = 3333, default_share_target: int = 0):
        self.reader = reader
        self.writer = writer
        self.client_id = client_id
        self.client_num = client_num
        self.server_port = server_port
        self.wallet_address = ""
        self.worker_name = ""
        self.authorized = False
        self.is_kaspa = False
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.blocks_found = 0
        self.job_count = 0
        self.last_submit_at = 0.0

        # Profile difficulty based on connected port
        profile = PORT_PROFILES.get(server_port, PORT_PROFILES[3333])
        self.share_diff = profile["base_diff"]
        self.min_diff = profile["min_diff"]
        self.max_diff = profile["max_diff"]
        self.share_target = diff_to_kaspa_target(self.share_diff) if default_share_target == 0 else default_share_target
        self.vardiff_window_start = time.monotonic()
        self.vardiff_shares = 0
        self.last_diff_change_at = time.monotonic()
        self.recent_shares: OrderedDict[tuple[str, int], None] = OrderedDict()
        self.job_ids = deque(maxlen=CLIENT_JOB_HISTORY_LIMIT)
        self.template_key: Optional[tuple] = None
        self.submit_tokens = 50.0
        self.submit_token_updated_at = time.monotonic()

    async def send_json(self, data: dict) -> None:
        self.writer.write((json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8"))
        await self.writer.drain()


class BTCKStratumServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3333,
        ports: Optional[list[int]] = None,
        node_rpc=None,
        node_rpc_url: str = "http://127.0.0.1:16110/rpc",
        rpc_token: Optional[str] = None,
        network: Optional[NetworkConfig | str] = None,
        share_target: Optional[int] = None,
        job_ttl_seconds: int = 120,
        late_block_grace_seconds: float = 16.0,
        max_clients: int = 256,
    ):
        self.host = host
        self.port = port
        if ports is not None:
            self.ports = ports
        elif port == 0:
            self.ports = [0]
        else:
            self.ports = [3333, 3334, 3335, 3336]
        self.servers: list[asyncio.Server] = []
        self.network = network if isinstance(network, NetworkConfig) else load_network(network)
        self.node_rpc = node_rpc or HTTPNodeRPCClient(node_rpc_url, rpc_token or os.getenv("BTCK_RPC_TOKEN"))
        self.default_payout_address = os.getenv("BTCK_DEFAULT_PAYOUT_ADDRESS")
        self.pool_fee_address = os.getenv(
            "BTCK_POOL_FEE_ADDRESS",
            CANONICAL_POOL_TREASURY_ADDRESS,
        )
        share_target = compact_to_target(self.network.pow_limit_bits) if share_target is None else share_target
        if not 0 < share_target <= MAX_UINT256:
            raise ValueError("share_target must be a positive uint256")
        self.share_target = share_target
        self.job_ttl_seconds = int(os.getenv("BTCK_STRATUM_JOB_TTL", job_ttl_seconds))
        self.late_block_grace_seconds = max(
            0.0,
            float(os.getenv("BTCK_STRATUM_LATE_BLOCK_GRACE", late_block_grace_seconds)),
        )
        self.max_clients = max_clients
        self.clients: Dict[str, StratumClient] = {}
        self.current_jobs: Dict[str, MiningJob] = {}
        self.submitted_shares: set[tuple[str, int]] = set()
        self._job_counter = 0
        self._client_counter = 0
        self.active_template_key: Optional[tuple] = None
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self._refresh_task: Optional[asyncio.Task] = None
        self._vardiff_task: Optional[asyncio.Task] = None
        self._status_server: Optional[asyncio.Server] = None
        self.status_host = os.getenv("BTCK_STRATUM_STATUS_HOST", "127.0.0.1")
        self.status_port = int(os.getenv("BTCK_STRATUM_STATUS_PORT", "0"))

        # PPLNS Accounting & Settlement Engine
        self.pool_mode = os.getenv("BTCK_POOL_MODE", "solo").strip().lower()
        self.pool_treasury_address = os.getenv("BTCK_POOL_TREASURY_ADDRESS", self.pool_fee_address)
        self.pplns_window_size = int(os.getenv("BTCK_PPLNS_WINDOW_SIZE", "10000"))
        default_db = "data/pool_accounting.db"
        if not os.path.exists("data") and not os.access(".", os.W_OK):
            default_db = "/tmp/pool_accounting.db"
        self.pplns_db_path = os.getenv("BTCK_PPLNS_DB_PATH", default_db)
        self.pplns_payout_threshold_btck = float(os.getenv("BTCK_PPLNS_MIN_PAYOUT_BTCK", "5.0"))
        self.pplns_engine: Optional[PPLNSEngine] = None
        telemetry_enabled = self.pool_mode == "pplns" or bool(os.getenv("BTCK_PPLNS_DB_PATH"))
        if telemetry_enabled:
            try:
                self.pplns_engine = PPLNSEngine(
                    db_path=self.pplns_db_path,
                    window_size=self.pplns_window_size,
                )
            except Exception as e:
                logger.warning("Pool accounting database initialization deferred: %s", e)
        requested_launch_time = float(os.getenv("BTCK_POOL_LAUNCH_TIME", "0.0"))
        self.pool_launch_time = (
            self.pplns_engine.get_or_create_launch_time(requested_launch_time)
            if self.pplns_engine
            else (requested_launch_time if requested_launch_time > 0.0 else time.time())
        )
        self.payout_worker: Optional[AutoPayoutWorker] = None
        treasury_wallet_path = os.getenv("BTCK_POOL_TREASURY_WALLET_FILE")
        if self.pplns_engine and treasury_wallet_path and os.path.exists(treasury_wallet_path):
            try:
                from privacy.wallet_engine import StealthWallet
                t_wallet = StealthWallet.load_from_json(treasury_wallet_path)
                self.payout_worker = AutoPayoutWorker(
                    engine=self.pplns_engine,
                    treasury_wallet=t_wallet,
                    node_rpc=self.node_rpc,
                    default_threshold_satk=int(self.pplns_payout_threshold_btck * SATK_PER_COIN),
                )
                logger.info("🔑 Loaded Pool Treasury wallet: %s", t_wallet.address[:18] + "...")
            except Exception as e:
                logger.warning("Failed to load Pool Treasury wallet: %s", e)

    def get_pool_fee_rate(self) -> float:
        """
        Canonical official pool fee schedule:
        - Days 1-7: 5.0% infrastructure bootstrap fee.
        - Days 8-30: 3.0% growth-stage fee.
        - Day 31 onward: 1.0% steady-state operational fee.
        """
        if not self.pool_fee_address:
            return 0.0
        elapsed_days = max(0.0, (time.time() - self.pool_launch_time) / 86400.0)
        if elapsed_days < 7.0:
            return 0.05
        if elapsed_days < 30.0:
            return 0.03
        return 0.01

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

    @staticmethod
    def _template_key(template: dict) -> tuple:
        return (
            tuple(template.get("parents", [])),
            int(template["target_bits"]),
            tuple(str(tx.get("txid", "")) for tx in template.get("transactions", [])),
        )

    async def create_new_block_job(
        self,
        client: StratumClient,
        is_clean: bool = True,
        template: Optional[dict] = None,
    ) -> MiningJob:
        if not client.authorized:
            raise ValueError("Cannot create a mining job for an unauthorized client")
        template = template or await self.node_rpc.call("getBlockTemplate")
        if template["chain_id"] != self.network.chain_id:
            raise RuntimeError("Node returned a template for another chain")
        template_key = self._template_key(template)

        client.job_count += 1
        fee_rate = self.get_pool_fee_rate()
        is_pool_fee_job = False

        if self.pool_mode == "pplns":
            # In PPLNS mode, all block rewards are routed to the central Pool Treasury
            target_address = self.pool_treasury_address or self.pool_fee_address or client.wallet_address
        else:
            # SOLO mode: block rewards go directly to miner unless fee block
            if self.pool_fee_address and fee_rate > 0.0:
                step = int(round(1.0 / fee_rate))
                if step > 0 and (client.job_count % step) == 0:
                    is_pool_fee_job = True
            target_address = self.pool_fee_address if is_pool_fee_job else client.wallet_address

        r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(target_address)
        self._job_counter += 1
        parents = list(template.get("parents", []))
        if not parents or len(parents) > self.network.max_parents:
            raise RuntimeError("Node returned an invalid mining parent set")
        job = MiningJob(
            job_id=str(self._job_counter),
            owner_id=client.client_id,
            worker_name=client.worker_name,
            parents=parents,
            version=int(template["version"]),
            chain_id=str(template["chain_id"]),
            timestamp=float(template["timestamp"]),
            bits=int(template["target_bits"]),
            coinbase_output=(r_ephem, p_onetime),
            transactions=[transaction_from_dict(raw) for raw in template.get("transactions", [])],
            created_at=time.time(),
            share_target=client.share_target,
            share_diff=client.share_diff,
            is_clean=is_clean,
            is_pool_fee_job=is_pool_fee_job,
            assigned_payout_address=target_address,
            template_key=template_key,
        )
        self.current_jobs[job.job_id] = job
        if len(client.job_ids) == client.job_ids.maxlen:
            expired_id = client.job_ids[0]
            self.current_jobs.pop(expired_id, None)
        client.job_ids.append(job.job_id)
        client.template_key = template_key
        self.active_template_key = template_key
        self._prune_jobs()
        return job

    def _prune_jobs(self) -> None:
        cutoff = time.time() - self.job_ttl_seconds
        stale = [job_id for job_id, job in self.current_jobs.items() if job.created_at < cutoff]
        for job_id in stale:
            self.current_jobs.pop(job_id, None)
        live_ids = set(self.current_jobs)
        self.submitted_shares = {key for key in self.submitted_shares if key[0] in live_ids}

    async def send_job_to_client(self, client: StratumClient, job: MiningJob) -> None:
        msg_id = int(job.job_id) if client.is_kaspa else None
        await client.send_json({"id": msg_id, "method": "mining.notify", "params": job.notify_params(is_kaspa=client.is_kaspa)})

    async def update_client_difficulty(self, client: StratumClient, new_diff: float) -> None:
        if abs(client.share_diff - new_diff) < 1e-6:
            return
        old_diff = client.share_diff
        client.share_diff = new_diff
        client.share_target = diff_to_kaspa_target(new_diff) if client.is_kaspa else self.share_target
        client.last_diff_change_at = time.monotonic()
        logger.info("⚡ VarDiff: adjusting difficulty for %s (%s) [%.1f -> %.1f] (Bounds: [%.1f, %.1f])", client.client_id, client.worker_name, old_diff, new_diff, client.min_diff, client.max_diff)
        await client.send_json({
            "id": None,
            "method": "mining.set_difficulty",
            "params": [new_diff]
        })

    async def _check_client_vardiff(self, client: StratumClient) -> None:
        now = time.monotonic()
        elapsed = now - client.vardiff_window_start
        if elapsed < 15.0:
            return

        rate = (client.vardiff_shares / elapsed) * 60.0  # shares per minute
        # Target: ~15-20 shares/min. Power-of-2 clamping bounded by [client.min_diff, client.max_diff]
        if rate > 25.0 and client.share_diff < client.max_diff:
            # If rate is massive (>60/min), scale up faster (4x)
            mult = 4.0 if rate > 60.0 else 2.0
            new_diff = min(client.max_diff, client.share_diff * mult)
            await self.update_client_difficulty(client, new_diff)
            client.vardiff_window_start = now
            client.vardiff_shares = 0
        elif rate < 10.0 and elapsed >= 30.0 and client.share_diff > client.min_diff:
            new_diff = max(client.min_diff, client.share_diff / 2.0)
            await self.update_client_difficulty(client, new_diff)
            client.vardiff_window_start = now
            client.vardiff_shares = 0
        elif elapsed >= 60.0:
            client.vardiff_window_start = now
            client.vardiff_shares = 0

    async def _vardiff_monitor_loop(self) -> None:
        while self.running:
            await asyncio.sleep(10)
            now = time.monotonic()
            for client in list(self.clients.values()):
                if not client.authorized or not client.is_kaspa:
                    continue
                elapsed = now - client.vardiff_window_start
                # If a client hasn't submitted a single share in 45s, difficulty is likely too high
                if client.vardiff_shares == 0 and elapsed >= 45.0 and client.share_diff > client.min_diff:
                    new_diff = max(client.min_diff, client.share_diff / 2.0)
                    await self.update_client_difficulty(client, new_diff)
                    client.vardiff_window_start = now
                    client.vardiff_shares = 0

    async def _issue_job(
        self,
        client: StratumClient,
        is_clean: bool = True,
        template: Optional[dict] = None,
    ) -> MiningJob:
        job = await self.create_new_block_job(client, is_clean=is_clean, template=template)
        # Share difficulty is an accounting/sampling threshold and is independent
        # of the easier network block target. A share target may be harder than
        # the network target; every such share is simply also a block candidate.
        # Capping VarDiff to network difficulty destroys share weight accounting
        # on low-difficulty bootstrap networks and can under-report ASIC hashrate
        # by orders of magnitude.
        await self.send_job_to_client(client, job)
        return job

    async def _broadcast_template(self, *, force: bool = False) -> None:
        template = await self.node_rpc.call("getBlockTemplate")
        template_key = self._template_key(template)
        if not force and template_key == self.active_template_key:
            return
        self.active_template_key = template_key
        for connected in list(self.clients.values()):
            if connected.authorized:
                await self._issue_job(connected, is_clean=True, template=template)

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
            agent = str(params[0]) if params else "IceRiver/KS0"
            agent_lower = agent.lower()
            if any(k in agent_lower for k in ["iceriver", "kaspa", "godminer", "bzminer", "antminer", "miner"]):
                client.is_kaspa = True
                profile = PORT_PROFILES.get(client.server_port, PORT_PROFILES[3333])
                client.share_diff = profile["base_diff"]
                client.min_diff = profile["min_diff"]
                client.max_diff = profile["max_diff"]
                client.share_target = diff_to_kaspa_target(client.share_diff)
                client.vardiff_window_start = time.monotonic()
                client.vardiff_shares = 0
                res = [True, "EthereumStratum/1.0.0"]
                await client.send_json({"id": msg_id, "result": res, "error": None})
                await client.send_json({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [client.share_diff]
                })
            else:
                client.is_kaspa = False
                await client.send_json(
                    {"id": msg_id, "result": [[["mining.notify", "btck-v2"]], "", 0], "error": None}
                )
                await client.send_json(
                    {"id": None, "method": "mining.set_target", "params": [f"{self.share_target:064x}"]}
                )
            return

        if method in ("mining.extranonce.subscribe", "mining.configure"):
            await client.send_json({"id": msg_id, "result": True, "error": None})
            return

        if method == "mining.authorize":
            full_user = str(params[0]) if params else ""
            pwd = str(params[1]) if len(params) > 1 else ""
            address, _, worker = full_user.partition(".")
            if not worker:
                worker = full_user

            # Support d=XXXX or diff=XXXX manual difficulty override in password or username
            manual_diff = None
            for candidate_str in (pwd, full_user):
                for part in candidate_str.replace(";", ",").split(","):
                    part = part.strip().lower()
                    if part.startswith("d=") or part.startswith("diff="):
                        try:
                            d_val = float(part.split("=")[1])
                            if d_val >= 1.0:
                                manual_diff = d_val
                        except Exception:
                            pass
            if manual_diff:
                client.share_diff = manual_diff
                client.min_diff = max(16.0, manual_diff / 2.0)
                client.max_diff = max(client.max_diff, manual_diff * 4.0)
                client.share_target = diff_to_kaspa_target(manual_diff) if client.is_kaspa else self.share_target
                logger.info("⚙️ Miner %s requested manual difficulty via password/param: %.1f", client.client_id, manual_diff)

            payout_address = None
            for candidate in (address, full_user):
                try:
                    StealthKeyPair.generate_one_time_stealth_output(candidate)
                    payout_address = candidate
                    break
                except Exception:
                    pass

            if payout_address is None and self.default_payout_address:
                try:
                    StealthKeyPair.generate_one_time_stealth_output(self.default_payout_address)
                    payout_address = self.default_payout_address
                except Exception:
                    pass

            if payout_address is None:
                await client.send_json({"id": msg_id, "result": False, "error": [24, "Invalid BTC-K payout address and no default configured", None]})
                return

            client.wallet_address = payout_address
            client.worker_name = worker[:64] or "worker"
            client.authorized = True
            logger.info("🔑 Miner authorized: %s -> %s (worker: %s, port: %d, is_kaspa: %s, diff: %.1f)", client.client_id, payout_address[:18] + "...", client.worker_name, client.server_port, client.is_kaspa, client.share_diff)
            await client.send_json({"id": msg_id, "result": True, "error": None})
            if client.is_kaspa:
                extranonce_hex = f"{client.client_num % 65536:04x}"
                await client.send_json({
                    "id": None,
                    "method": "set_extranonce",
                    "params": [extranonce_hex]
                })
                await client.send_json({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [client.share_diff]
                })
            await self._issue_job(client)
            return

        if method == "mining.submit":
            if not client.authorized:
                if self.default_payout_address:
                    client.wallet_address = self.default_payout_address
                    client.worker_name = (str(params[0])[:64] if params else "worker")
                    client.authorized = True
                    logger.info("🔑 Auto-authorizing reconnected miner %s with default payout address", client.client_id)
                else:
                    await client.send_json({"id": msg_id, "result": False, "error": [24, "Unauthorized worker", None]})
                    return
            if len(params) < 3:
                await client.send_json({"id": msg_id, "result": False, "error": [20, "Expected worker, job id and nonce", None]})
                return
            job_id = str(params[1])
            raw_nonce = params[4] if len(params) >= 5 else params[2]
            job = self.current_jobs.get(job_id)
            if (
                job is None
                or job.owner_id != client.client_id
                or time.time() - job.created_at > self.job_ttl_seconds
            ):
                client.shares_rejected += 1
                logger.warning("❌ Stale/unknown job %s from %s (live jobs: %d)", job_id, client.client_id, len(self.current_jobs))
                await client.send_json({"id": msg_id, "result": False, "error": [21, "Job not found / stale share", None]})
                return
            try:
                nonce = self._parse_nonce(raw_nonce)
            except (TypeError, ValueError) as exc:
                client.shares_rejected += 1
                logger.warning("❌ Invalid nonce %s from %s: %s", raw_nonce, client.client_id, exc)
                await client.send_json({"id": msg_id, "result": False, "error": [20, str(exc), None]})
                return
            share_key = (job_id, nonce)
            is_duplicate = (
                share_key in client.recent_shares
                if client.is_kaspa
                else share_key in self.submitted_shares
            )

            if is_duplicate:
                client.shares_rejected += 1
                logger.warning("❌ Duplicate share %s from %s", share_key, client.client_id)
                await client.send_json({"id": msg_id, "result": False, "error": [22, "Duplicate share", None]})
                return

            now = time.monotonic()
            elapsed = max(0.0, now - client.submit_token_updated_at)
            client.submit_tokens = min(50.0, client.submit_tokens + elapsed * 50.0)
            client.submit_token_updated_at = now
            if client.submit_tokens < 1.0:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [25, "Share rate limit exceeded", None]})
                return
            client.submit_tokens -= 1.0
            client.last_submit_at = now

            if client.is_kaspa:
                client.recent_shares[share_key] = None
                client.recent_shares.move_to_end(share_key)
                while len(client.recent_shares) > 256:
                    client.recent_shares.popitem(last=False)
            else:
                self.submitted_shares.add(share_key)

            pow_value = job.calculate_pow(nonce)
            hash_value = hash_to_target_int(pow_value)
            # Stratum difficulty changes apply to subsequently issued jobs. ASICs
            # legitimately finish queued work for earlier jobs, so validate and
            # account each submission with the target attached to that job.
            target_to_compare = job.share_target
            if hash_value > target_to_compare:
                client.shares_rejected += 1
                logger.warning("❌ Low difficulty share from %s: hash=%x > target=%x (job_diff=%.1f)", client.client_id, hash_value, target_to_compare, job.share_diff)
                await client.send_json({"id": msg_id, "result": False, "error": [23, "Low difficulty share", None]})
                return

            # Share is valid! Immediately accept and credit miner.
            client.shares_accepted += 1
            client.vardiff_shares += 1
            if self.pplns_engine:
                self.pplns_engine.record_share(client.wallet_address, client.worker_name, job.share_diff)
            await client.send_json({"id": msg_id, "result": True, "error": None})

            network_target = compact_to_target(job.bits)
            is_current_template = job.template_key == self.active_template_key
            job_age = max(0.0, time.time() - job.created_at)
            within_late_block_grace = job_age <= self.late_block_grace_seconds
            is_block = hash_value <= network_target and (
                is_current_template or within_late_block_grace
            )
            logger.info(
                "⛏️ Valid share from %s (%s) | Nonce: %016x | JobDiff: %.1f | Block: %s | Template: %s (age %.2fs)",
                client.client_id,
                client.worker_name,
                nonce,
                job.share_diff,
                is_block,
                "current" if is_current_template else "late-grace",
                job_age,
            )

            # VarDiff adaptive adjustment check
            if client.is_kaspa:
                await self._check_client_vardiff(client)

            # If it qualifies as a block candidate, submit to node
            if is_block:
                try:
                    candidate = job.build_block(nonce)
                    if candidate.hash != pow_value.hex():
                        raise RuntimeError("Stratum/node canonical PoW mismatch")
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
                    if result.get("accepted"):
                        client.blocks_found += 1
                        reward_btck = float(result.get("reward_btck", 5.0))
                        reward_satk = int(reward_btck * SATK_PER_COIN)

                        if self.pool_mode == "pplns":
                            pplns_summary = self.pplns_engine.process_block(
                                block_hash=str(result.get("hash")),
                                height=int(result.get("blue_score", 0)),
                                reward_satk=reward_satk,
                                pool_fee_rate=self.get_pool_fee_rate(),
                                finder_wallet=client.wallet_address,
                                finder_worker=client.worker_name,
                            )
                            logger.info(
                                "💎 [PPLNS] BLOCK ACCEPTED ON CHAIN! Hash: %s, Score: %s, Reward: %.4f BTC-K | Dist: %.4f BTC-K to %d miners | Finder: %s (%s)",
                                str(result.get("hash"))[:16],
                                result.get("blue_score"),
                                reward_btck,
                                pplns_summary.get("distributed_satk", 0) / SATK_PER_COIN,
                                pplns_summary.get("miners_paid_count", 0),
                                client.wallet_address[:16] + "...",
                                client.worker_name,
                            )
                        else:
                            if self.pplns_engine:
                                self.pplns_engine.record_solo_block(
                                    block_hash=str(result.get("hash")),
                                    height=int(result.get("blue_score", 0)),
                                    reward_satk=reward_satk,
                                    finder_wallet=client.wallet_address,
                                    finder_worker=client.worker_name,
                                    payout_address=job.assigned_payout_address,
                                    is_pool_fee=bool(getattr(job, "is_pool_fee_job", False)),
                                )
                            if getattr(job, "is_pool_fee_job", False):
                                logger.info(
                                    "💎 BLOCK ACCEPTED (Pool Fee %d%%)! Hash: %s, Score: %s, Reward: %s BTC-K -> Pool Treasury %s",
                                    int(self.get_pool_fee_rate() * 100),
                                    str(result.get("hash"))[:16],
                                    result.get("blue_score"),
                                    result.get("reward_btck"),
                                    self.pool_fee_address[:16] + "...",
                                )
                            else:
                                logger.info(
                                    "🏆 BLOCK ACCEPTED ON CHAIN! Hash: %s, Score: %s, Reward: %s BTC-K -> Miner %s (%s)",
                                    str(result.get("hash"))[:16],
                                    result.get("blue_score"),
                                    result.get("reward_btck"),
                                    client.wallet_address[:16] + "...",
                                    client.worker_name,
                                )
                        # The 1 Hz refresh loop publishes the newest DAG view.
                        # Broadcasting here as well creates a positive feedback
                        # loop: parallel candidates generate duplicate jobs,
                        # which IceRiver firmware queues and later returns as
                        # increasingly stale work.
                    else:
                        logger.info("ℹ️ Share was block candidate but DAG tips advanced (stale block, share credited to miner)")
                except Exception as exc:
                    logger.warning("Node submitBlock error: %s (share still credited to miner)", exc)
            elif hash_value <= network_target:
                logger.info(
                    "ℹ️ Valid late share credited but not submitted: template age %.2fs exceeds %.2fs grace",
                    job_age,
                    self.late_block_grace_seconds,
                )

            return

        if method == "mining.suggest_difficulty":
            await client.send_json({"id": msg_id, "result": False, "error": [20, "Client difficulty changes are not allowed", None]})
            return

        await client.send_json({"id": msg_id, "result": None, "error": [20, "Method not supported", None]})

    def get_status_payload(self) -> dict:
        measured = self.pplns_engine.get_dashboard_stats() if self.pplns_engine else {
            "window_seconds": 600.0,
            "workers": [],
            "accepted_blocks": 0,
            "fee_blocks": 0,
            "fee_satk": 0,
            "distributed_satk": 0,
            "fee_recipients": [],
            "pool_hashrate_hps": 0.0,
            "recent_blocks": [],
        }
        live_by_key: Dict[tuple[str, str], dict] = {}
        for client in self.clients.values():
            if not client.authorized:
                continue
            key = (client.wallet_address, client.worker_name)
            item = live_by_key.setdefault(key, {
                "connections": 0,
                "accepted_since_connect": 0,
                "rejected_since_connect": 0,
                "blocks_since_connect": 0,
                "ports": set(),
                "difficulty": client.share_diff,
            })
            item["connections"] += 1
            item["accepted_since_connect"] += client.shares_accepted
            item["rejected_since_connect"] += client.shares_rejected
            item["blocks_since_connect"] += client.blocks_found
            item["ports"].add(client.server_port)
            item["difficulty"] = client.share_diff

        measured_by_key = {
            (worker["wallet_address"], worker["worker_name"]): worker
            for worker in measured["workers"]
        }
        all_keys = sorted(set(measured_by_key) | set(live_by_key), key=lambda key: key[1])
        workers = []
        now = time.time()
        for wallet_address, worker_name in all_keys:
            worker = dict(measured_by_key.get((wallet_address, worker_name), {
                "wallet_address": wallet_address,
                "worker_name": worker_name,
                "shares_total": 0,
                "shares_window": 0,
                "hashrate_hps": 0.0,
                "last_share_timestamp": 0.0,
                "accepted_blocks": 0,
                "miner_blocks": 0,
                "fee_blocks": 0,
                "miner_reward_satk": 0,
            }))
            live = live_by_key.get((wallet_address, worker_name))
            worker.update({
                "online": bool(live),
                "connections": live["connections"] if live else 0,
                "accepted_since_connect": live["accepted_since_connect"] if live else 0,
                "rejected_since_connect": live["rejected_since_connect"] if live else 0,
                "blocks_since_connect": live["blocks_since_connect"] if live else 0,
                "ports": sorted(live["ports"]) if live else [],
                "difficulty": live["difficulty"] if live else None,
                "last_seen_seconds_ago": (
                    max(0.0, now - worker["last_share_timestamp"])
                    if worker["last_share_timestamp"] else None
                ),
            })
            workers.append(worker)

        elapsed = max(0.0, now - self.pool_launch_time)
        measured.update({
            "pool_mode": self.pool_mode.upper(),
            "workers": workers,
            "active_workers": sum(1 for worker in workers if worker["online"]),
            "active_connections": sum(worker["connections"] for worker in workers),
            "fee_policy": {
                "current_fee_rate": self.get_pool_fee_rate(),
                "stage": "bootstrap" if elapsed < 7 * 86400 else ("growth" if elapsed < 30 * 86400 else "steady_state"),
                "next_stage_seconds": (
                    max(0.0, 7 * 86400 - elapsed)
                    if elapsed < 7 * 86400
                    else (max(0.0, 30 * 86400 - elapsed) if elapsed < 30 * 86400 else 0.0)
                ),
                "launch_time": self.pool_launch_time,
                "schedule": "5% on days 1-7; 3% on days 8-30; 1% from day 31 onward",
                "treasury_address": self.pool_fee_address,
            },
            "generated_at": now,
        })
        return measured

    async def handle_status_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            parts = request_line.decode("ascii", "replace").strip().split()
            path = parts[1] if len(parts) >= 2 else "/"
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                if line in (b"\r\n", b"\n", b""):
                    break
            if path not in ("/", "/status"):
                status = "HTTP/1.1 404 Not Found\r\n"
                body = b'{"error":"not found"}'
            else:
                status = "HTTP/1.1 200 OK\r\n"
                body = json.dumps(self.get_status_payload(), separators=(",", ":")).encode("utf-8")
            headers = (
                status
                + "Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n"
                + "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(headers + body)
            await writer.drain()
        except Exception as exc:
            logger.debug("Status HTTP handler error: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("unknown", 0)
        sock = writer.get_extra_info("sockname")
        server_port = sock[1] if (sock and len(sock) >= 2) else self.port
        client_id = f"{peer[0]}:{peer[1]}"
        if len(self.clients) >= self.max_clients:
            writer.close()
            await writer.wait_closed()
            return
        self._client_counter += 1
        client = StratumClient(
            reader,
            writer,
            client_id,
            client_num=self._client_counter,
            server_port=server_port,
            default_share_target=self.share_target,
        )
        self.clients[client_id] = client
        prof = PORT_PROFILES.get(server_port, PORT_PROFILES[3333])
        logger.info(
            "🔌 Miner connected: %s (Worker #%d, Port: %d [%s], Initial Diff: %.1f, Min Floor: %.1f)",
            client_id,
            self._client_counter,
            server_port,
            prof.get("name", "Custom"),
            client.share_diff,
            client.min_diff,
        )
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
            logger.info("🔌 Miner disconnected: %s", client_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _refresh_loop(self) -> None:
        refresh_count = 0
        while self.running:
            await asyncio.sleep(1)
            try:
                # Kaspa ASIC firmware is designed around an approximately 1 Hz
                # job stream.  Force one coalesced snapshot per tick so miners
                # receive fresh work even when several DAG candidates arrive
                # between refreshes.
                await self._broadcast_template(force=True)
                refresh_count += 1
                if self.pplns_engine and refresh_count % 10 == 0:
                    dashboard = await self.node_rpc.call("getDashboardInfo", {"recent_limit": 500})
                    self.pplns_engine.reconcile_solo_block_rewards(dashboard.get("recent_blocks", []))
            except Exception as exc:
                logger.warning("Template refresh failed: %s", exc)

    async def start(self) -> None:
        await self._verify_node_identity()
        self.running = True
        self.servers = []
        for p in self.ports:
            srv = await asyncio.start_server(self.handle_client, self.host, p, limit=65_536)
            self.servers.append(srv)
            prof = PORT_PROFILES.get(p, {})
            logger.info(
                "BTC-K Stratum bridge listening on %s:%d [%s] (BaseDiff=%.1f, MinDiff=%.1f)",
                self.host,
                p,
                prof.get("name", "Custom"),
                prof.get("base_diff", 512.0),
                prof.get("min_diff", 256.0),
            )
        self.server = self.servers[0] if self.servers else None
        if self.status_port > 0:
            self._status_server = await asyncio.start_server(
                self.handle_status_client,
                self.status_host,
                self.status_port,
                limit=65_536,
            )
            logger.info("Pool status API listening on %s:%d", self.status_host, self.status_port)
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._vardiff_task = asyncio.create_task(self._vardiff_monitor_loop())
        if self.payout_worker:
            self.payout_worker.start()

    async def stop(self) -> None:
        self.running = False
        if self.payout_worker:
            await self.payout_worker.stop()
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._vardiff_task:
            self._vardiff_task.cancel()
            try:
                await self._vardiff_task
            except asyncio.CancelledError:
                pass
        for srv in self.servers:
            srv.close()
            await srv.wait_closed()
        if self._status_server:
            self._status_server.close()
            await self._status_server.wait_closed()
            self._status_server = None
        self.servers.clear()
        self.server = None


async def _main() -> None:
    network = require_feature("stratum")
    ports_str = os.getenv("BTCK_STRATUM_PORTS", "3333,3334,3335,3336")
    ports = [int(p.strip()) for p in ports_str.split(",") if p.strip().isdigit()]
    if not ports:
        ports = [int(os.getenv("BTCK_STRATUM_PORT", "3333"))]
    server = BTCKStratumServer(
        host=os.getenv("BTCK_STRATUM_HOST", "127.0.0.1"),
        port=ports[0],
        ports=ports,
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

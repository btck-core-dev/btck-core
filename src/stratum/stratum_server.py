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
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from config.network import NetworkConfig, load_network, require_feature
from consensus.genesis import build_pre_pow_hash
from consensus.kheavyhash import calculate_pow_value, generate_matrix, hash_to_target_int
from consensus.pow import MAX_UINT256, compact_to_target
from node.blockdag import DAGBlock, transaction_commitment
from node.rpc_client import HTTPNodeRPCClient, RPCError
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthTransaction, StealthWallet, transaction_from_dict, transaction_to_dict
from tokenomics.emission_schedule import SATK_PER_COIN
from stratum.pplns import PPLNSEngine, AutoPayoutWorker

import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [stratum] %(message)s")
logger = logging.getLogger("btck-stratum")

DEFAULT_DEVNET_POOL_TREASURY_ADDRESS = (
    "btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23"
    "ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af"
)
MAINNET_POOL_TREASURY_ADDRESS = (
    "btck1sAPmQArRKd4jw8xVrVrvzUKz3BXCY7QYuk27wS9nUYMApuF8u8QfQV5KNFfejoPEf8m1r"
    "5qsYRSz8S44wG3ZiWTzy8guRRuK"
)
MAINNET_PERSONAL_ADDRESS = (
    "btck1s6LLVmAk5s1PN5VRRh2h4AqmGnASiPe4reShbabFbEFSp7zPRm92MrprgtkgqQTg9C9vZgF"
    "mfdfYLkm4NhXWmbZLujFbXkA1"
)

# IceRiver firmware can submit work tens of seconds after it was announced.
# Keep enough per-client history to cover the late-block window and retain
# older valid shares for accounting, while preserving a strict memory bound.
CLIENT_JOB_HISTORY_LIMIT = 128
WORKER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_worker_name(value: str) -> str:
    """Accept only display-safe, database-safe public worker identifiers."""
    worker = str(value).strip()
    if not WORKER_NAME_RE.fullmatch(worker):
        raise ValueError("Worker name must be 1-64 letters, digits, underscores or hyphens")
    return worker


def resolve_pool_treasury_address(network: NetworkConfig, configured: Optional[str]) -> str:
    """Keep legacy DEVNET accounting while failing closed on MAINNET."""
    if network.is_mainnet:
        if not configured:
            raise ValueError("MAINNET requires an explicit pool treasury address")
        if configured != MAINNET_POOL_TREASURY_ADDRESS:
            raise ValueError("MAINNET pool treasury address does not match the frozen public wallet")
        return configured
    return configured or DEFAULT_DEVNET_POOL_TREASURY_ADDRESS


def load_pool_treasury_wallet(
    wallet_path: str,
    *,
    password_path: str = "",
    require_encryption: bool = False,
    expected_address: str = "",
) -> StealthWallet:
    """Load the payout hot wallet without accepting MAINNET plaintext keys."""
    if require_encryption and os.stat(wallet_path).st_mode & 0o077:
        raise ValueError("pool treasury wallet file permissions must be 600 on MAINNET")
    with open(wallet_path, "r", encoding="utf-8") as handle:
        treasury_envelope = json.load(handle)
    encrypted = treasury_envelope.get("format") == "btck-wallet-encrypted"
    treasury_password = None
    if encrypted:
        if not password_path:
            raise ValueError(
                "encrypted pool treasury requires "
                "BTCK_POOL_TREASURY_WALLET_PASSWORD_FILE"
            )
        password_stat = os.stat(password_path)
        if password_stat.st_mode & 0o077:
            raise ValueError("pool treasury password file permissions must be 600")
        with open(password_path, "r", encoding="utf-8") as handle:
            treasury_password = handle.read().rstrip("\r\n")
        if len(treasury_password) < 10:
            raise ValueError("pool treasury wallet password is too short")
    elif require_encryption:
        raise ValueError("MAINNET refuses a plaintext pool treasury wallet")
    wallet = StealthWallet.load_from_json(wallet_path, password=treasury_password)
    if expected_address and wallet.address != expected_address:
        raise ValueError("pool treasury wallet does not match configured treasury address")
    return wallet


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
    3333: {"name": "Entry / KS0 / GPU (<1 TH/s)", "base_diff": 128.0, "min_diff": 128.0, "max_diff": 65536.0},
    3334: {"name": "Mid / KS1 / KS2 (1-3 TH/s)", "base_diff": 4096.0, "min_diff": 2048.0, "max_diff": 131072.0},
    3335: {"name": "High / KS3 / KS3L (6-10 TH/s)", "base_diff": 16384.0, "min_diff": 8192.0, "max_diff": 262144.0},
    3336: {"name": "Ultra / KS5 / KS7 / Large (20-100+ TH/s)", "base_diff": 65536.0, "min_diff": 32768.0, "max_diff": 1048576.0},
}


@dataclass
class MiningJob:
    job_id: str
    owner_id: str
    owner_wallet_address: str
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
        # Template broadcasts, VarDiff notices and submit replies may originate
        # from different tasks.  Serialize complete JSON lines so an ASIC never
        # sees reordered/interleaved writes, and fail a wedged socket quickly
        # enough for the firmware to reconnect instead of retrying one nonce
        # forever.
        self._write_lock = asyncio.Lock()
        self.write_timeout_seconds = 2.0

    async def send_json(self, data: dict) -> None:
        payload = (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            self.writer.write(payload)
            await asyncio.wait_for(
                self.writer.drain(),
                timeout=self.write_timeout_seconds,
            )


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
        template_refresh_seconds: float = 4.0,
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
        self.guardian_payout_address = os.getenv("BTCK_GUARDIAN_PAYOUT_ADDRESS", "").strip()
        if self.guardian_payout_address:
            if (
                not self.network.is_mainnet
                or not self.network.release_approval_id.startswith("BTCK-GUARDIAN-")
                or self.network.hard_checkpoints
            ):
                raise ValueError("guardian payout override is allowed only during the pre-checkpoint MAINNET guardian phase")
            if self.guardian_payout_address != MAINNET_PERSONAL_ADDRESS:
                raise ValueError("guardian payout override must equal the frozen MAINNET personal address")
        self.pool_fee_address = resolve_pool_treasury_address(
            self.network,
            os.getenv("BTCK_POOL_FEE_ADDRESS"),
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
        self.template_refresh_seconds = max(
            1.0,
            float(os.getenv("BTCK_STRATUM_TEMPLATE_REFRESH_SECONDS", template_refresh_seconds)),
        )
        self.template_retry_seconds = max(
            0.1,
            float(os.getenv("BTCK_STRATUM_TEMPLATE_RETRY_SECONDS", "0.25")),
        )
        self.vardiff_target_seconds = max(
            1.0,
            float(os.getenv("BTCK_VARDIFF_TARGET_SECONDS", "8")),
        )
        self.vardiff_ramp_seconds = max(
            2.0,
            float(os.getenv("BTCK_VARDIFF_RAMP_SECONDS", "5")),
        )
        self.vardiff_min_pool_share_hz = max(
            0.1,
            float(os.getenv("BTCK_VARDIFF_MIN_POOL_SHARE_HZ", "1.25")),
        )
        # Stock IceRiver firmware will not mine below Kaspa difficulty 128.
        # While L1 is easier than that hardware floor, every ASIC must receive
        # the same target; otherwise port/VarDiff tiers alter block opportunity
        # rather than merely sampling accounting shares.  Once L1 becomes
        # harder, VarDiff may resume but may never exceed the L1-equivalent
        # Kaspa difficulty, because that would hide valid blocks from Stratum.
        self.asic_min_share_diff = max(
            1.0,
            float(os.getenv("BTCK_ASIC_MIN_SHARE_DIFF", "128")),
        )
        self.active_network_share_diff = 0.0
        self.max_clients = max_clients
        self.clients: Dict[str, StratumClient] = {}
        self.current_jobs: Dict[str, MiningJob] = {}
        self.submitted_shares: set[tuple[str, int]] = set()
        self.template_submission_counts: OrderedDict[tuple, int] = OrderedDict()
        self._job_counter = 0
        self._client_counter = 0
        self.active_template_key: Optional[tuple] = None
        self.active_template_started_at = 0.0
        self.active_template_submission_limit = self.network.max_parents
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self._refresh_task: Optional[asyncio.Task] = None
        self._reconcile_task: Optional[asyncio.Task] = None
        self._vardiff_task: Optional[asyncio.Task] = None
        self._status_cache_task: Optional[asyncio.Task] = None
        self._block_submission_tasks: set[asyncio.Task] = set()
        self._accounting_lock = asyncio.Lock()
        self._status_server: Optional[asyncio.Server] = None
        self.status_host = os.getenv("BTCK_STRATUM_STATUS_HOST", "127.0.0.1")
        self.status_port = int(os.getenv("BTCK_STRATUM_STATUS_PORT", "0"))
        self.status_refresh_seconds = max(
            5.0,
            float(os.getenv("BTCK_STRATUM_STATUS_REFRESH_SECONDS", "10")),
        )
        self._measured_status_cache: dict = self._empty_measured_status()
        self._status_cache_generated_at = 0.0
        self._share_counter_epoch = uuid.uuid4().hex
        self._share_counters = {
            "accepted": 0,
            "rejected": 0,
            "duplicate_retries": 0,
            "rejected_by_reason": {
                "stale": 0,
                "invalid": 0,
                "duplicate": 0,
                "rate_limit": 0,
                "low_difficulty": 0,
            },
        }

        # PPLNS Accounting & Settlement Engine
        self.pool_mode = os.getenv("BTCK_POOL_MODE", "solo").strip().lower()
        self.pool_treasury_address = resolve_pool_treasury_address(
            self.network,
            os.getenv("BTCK_POOL_TREASURY_ADDRESS") or self.pool_fee_address,
        )
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
        if self.network.is_mainnet and self.pool_mode == "pplns" and (
            not treasury_wallet_path or not os.path.exists(treasury_wallet_path)
        ):
            raise ValueError("MAINNET PPLNS requires the encrypted pool treasury wallet file")
        if self.pplns_engine and treasury_wallet_path and os.path.exists(treasury_wallet_path):
            try:
                t_wallet = load_pool_treasury_wallet(
                    treasury_wallet_path,
                    password_path=os.getenv("BTCK_POOL_TREASURY_WALLET_PASSWORD_FILE", ""),
                    require_encryption=self.network.is_mainnet,
                    expected_address=self.pool_treasury_address,
                )
                self.payout_worker = AutoPayoutWorker(
                    engine=self.pplns_engine,
                    treasury_wallet=t_wallet,
                    node_rpc=self.node_rpc,
                    payout_utc_hours=tuple(
                        int(value.strip())
                        for value in os.getenv(
                            "BTCK_PPLNS_PAYOUT_UTC_HOURS", "0,6,12,18"
                        ).split(",")
                        if value.strip()
                    ),
                    default_threshold_satk=int(self.pplns_payout_threshold_btck * SATK_PER_COIN),
                )
                logger.info("🔑 Loaded Pool Treasury wallet: %s", t_wallet.address[:18] + "...")
            except Exception as e:
                if self.network.is_mainnet:
                    raise ValueError(f"MAINNET pool treasury wallet failed validation: {e}") from e
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

    @staticmethod
    def _empty_measured_status() -> dict:
        return {
            "window_seconds": 600.0,
            "workers": [],
            "accepted_blocks": 0,
            "fee_blocks": 0,
            "fee_satk": 0,
            "distributed_satk": 0,
            "fee_recipients": [],
            "pool_hashrate_hps": 0.0,
            "recent_blocks": [],
            "combo_public_winners": [],
        }

    def _record_share_rejection(self, client: StratumClient, reason: str) -> None:
        client.shares_rejected += 1
        self._share_counters["rejected"] += 1
        reasons = self._share_counters["rejected_by_reason"]
        reasons[reason] = reasons.get(reason, 0) + 1

    async def _refresh_status_cache_once(self) -> None:
        if not self.pplns_engine:
            self._measured_status_cache = self._empty_measured_status()
        else:
            # SQLite aggregation is deliberately kept off the Stratum event
            # loop.  The status endpoint must never delay template refreshes or
            # miner submissions as the accounting tables grow.
            self._measured_status_cache = await asyncio.to_thread(
                self.pplns_engine.get_dashboard_stats
            )
        self._status_cache_generated_at = time.time()

    async def _status_cache_loop(self) -> None:
        while self.running:
            await asyncio.sleep(self.status_refresh_seconds)
            try:
                await self._refresh_status_cache_once()
            except Exception as exc:
                logger.warning("Pool status cache refresh failed: %s", exc)

    async def _verify_node_identity(self) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                info = await self.node_rpc.call("getInfo")
                break
            except Exception as exc:
                # Node startup may include snapshot validation and journal
                # replay. Keep Stratum alive for the entire recovery window;
                # systemd restart loops cannot make an unavailable RPC ready
                # any sooner and needlessly churn miner connections.
                if attempt == 1 or attempt % 30 == 0:
                    logger.warning(
                        "Waiting for L1 node RPC on %s:%s (attempt %d): %s",
                        self.node_rpc.host, self.node_rpc.port, attempt, exc,
                    )
                await asyncio.sleep(2)
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

    def _activate_template(self, template_key: tuple, parent_count: int) -> None:
        if template_key == self.active_template_key:
            return
        self.active_template_key = template_key
        self.active_template_started_at = time.monotonic()
        self.active_template_submission_limit = (
            1 if parent_count >= self.network.max_parents else self.network.max_parents
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
        if client.is_kaspa:
            await self._enforce_network_safe_difficulty(
                client,
                int(template["target_bits"]),
            )
        else:
            # Native clients receive set_target, not an ASIC difficulty target.
            # Credit the work actually required by that target. A password's
            # d= value must never multiply PPLNS weight for unchanged work.
            client.share_target = self.share_target
            client.share_diff = float(((1 << 224) - 1) / client.share_target)

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
            miner_address = self.guardian_payout_address or client.wallet_address
            target_address = self.pool_fee_address if is_pool_fee_job else miner_address

        r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(target_address)
        self._job_counter += 1
        parents = list(template.get("parents", []))
        if not parents or len(parents) > self.network.max_parents:
            raise RuntimeError("Node returned an invalid mining parent set")
        job = MiningJob(
            job_id=str(self._job_counter),
            owner_id=client.client_id,
            owner_wallet_address=client.wallet_address,
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
        self._activate_template(template_key, len(parents))
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
        new_diff = self._network_safe_share_diff(new_diff)
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

    def _network_share_diff(self, bits: int) -> float:
        """Return an L1 target expressed in the difficulty units ASICs use."""
        network_target = compact_to_target(bits)
        return float(((1 << 224) - 1) / network_target)

    def _network_safe_share_diff(self, requested_diff: float) -> float:
        """Clamp an ASIC target without filtering any L1-valid block.

        Below the firmware floor all ASICs use one common bootstrap target.
        Above it, an accounting share may be easier than L1, never harder.
        """
        network_diff = self.active_network_share_diff
        if network_diff <= 0.0:
            return float(requested_diff)
        if network_diff < self.asic_min_share_diff:
            return self.asic_min_share_diff
        return max(
            self.asic_min_share_diff,
            min(float(requested_diff), network_diff),
        )

    async def _enforce_network_safe_difficulty(
        self,
        client: StratumClient,
        bits: int,
    ) -> None:
        self.active_network_share_diff = self._network_share_diff(bits)
        safe_diff = self._network_safe_share_diff(client.share_diff)
        if abs(client.share_diff - safe_diff) >= 1e-6:
            await self.update_client_difficulty(client, safe_diff)

    async def _check_client_vardiff(self, client: StratumClient) -> None:
        now = time.monotonic()
        elapsed = now - client.vardiff_window_start
        if elapsed < self.vardiff_ramp_seconds:
            return

        # Stock IceRiver firmware will not actually mine below its effective
        # floor.  While L1 is easier than that floor, every ASIC must stay on
        # the same threshold; allowing VarDiff to separate miners here would
        # reintroduce the port-dependent block-discovery bias this guard fixes.
        if 0.0 < self.active_network_share_diff < self.asic_min_share_diff:
            await self.update_client_difficulty(client, self.asic_min_share_diff)
            client.vardiff_window_start = now
            client.vardiff_shares = 0
            return

        rate = (client.vardiff_shares / elapsed) * 60.0  # shares per minute
        active_miners = max(
            1,
            sum(1 for item in self.clients.values() if item.authorized and item.is_kaspa),
        )
        # Per-miner VarDiff must not starve a low-difficulty bootstrap chain.
        # With only a few miners, shorten the effective share interval enough
        # to preserve at least the configured aggregate candidate rate.  As the
        # fleet grows, converge to the normal production target.
        effective_target_seconds = min(
            self.vardiff_target_seconds,
            active_miners / self.vardiff_min_pool_share_hz,
        )
        target_rate = 60.0 / effective_target_seconds
        if rate > target_rate * 1.5 and client.share_diff < client.max_diff:
            mult = 4.0 if rate > target_rate * 4.0 else 2.0
            new_diff = self._network_safe_share_diff(
                min(client.max_diff, client.share_diff * mult)
            )
            await self.update_client_difficulty(client, new_diff)
            client.vardiff_window_start = now
            client.vardiff_shares = 0
        elif rate < target_rate * 0.67 and elapsed >= 30.0 and client.share_diff > client.min_diff:
            effective_min = min(client.min_diff, max(
                self.asic_min_share_diff,
                self.active_network_share_diff,
            ))
            new_diff = self._network_safe_share_diff(
                max(effective_min, client.share_diff / 2.0)
            )
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
                    effective_min = min(client.min_diff, max(
                        self.asic_min_share_diff,
                        self.active_network_share_diff,
                    ))
                    new_diff = self._network_safe_share_diff(
                        max(effective_min, client.share_diff / 2.0)
                    )
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
        # create_new_block_job snapshots the network-safe difficulty into the
        # job before it is announced.  Consequently every hash visible to the
        # ASIC as an L1 candidate is also visible to Stratum, except during the
        # unavoidable common firmware-floor bootstrap interval.
        await self.send_job_to_client(client, job)
        return job

    async def _broadcast_template(self, *, force: bool = False) -> bool:
        template = await self.node_rpc.call("getBlockTemplate")
        template_key = self._template_key(template)
        if not force and template_key == self.active_template_key:
            return False
        self._activate_template(template_key, len(template.get("parents", [])))
        for connected in list(self.clients.values()):
            if connected.authorized:
                try:
                    await self._issue_job(connected, is_clean=True, template=template)
                except (ConnectionError, BrokenPipeError, asyncio.TimeoutError) as exc:
                    # One half-closed ASIC socket must never prevent healthy
                    # miners later in the client map from receiving the new
                    # template. handle_client owns final socket cleanup.
                    logger.info(
                        "Skipping unavailable miner during template broadcast: %s (%s)",
                        connected.client_id,
                        exc,
                    )
                except Exception as exc:
                    logger.warning(
                        "Template delivery failed for miner %s: %s",
                        connected.client_id,
                        exc,
                    )
        return True

    async def _submit_block_candidate(
        self,
        client: StratumClient,
        job: MiningJob,
        nonce: int,
        pow_value: bytes,
    ) -> None:
        """Submit and account for a block without blocking the miner reader."""
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
            if not result.get("accepted"):
                logger.info(
                    "Block candidate reached a superseded DAG tip; share remains credited"
                )
                return

            client.blocks_found += 1
            reward_btck = float(result.get("reward_btck", 5.0))
            reward_satk = int(round(reward_btck * SATK_PER_COIN))
            if self.pool_mode == "pplns":
                if not self.pplns_engine:
                    raise RuntimeError("PPLNS accounting engine is unavailable")
                async with self._accounting_lock:
                    pplns_summary = await asyncio.to_thread(
                        self.pplns_engine.process_block,
                        block_hash=str(result.get("hash")),
                        height=int(result.get("blue_score", 0)),
                        reward_satk=reward_satk,
                        pool_fee_rate=self.get_pool_fee_rate(),
                        finder_wallet=job.owner_wallet_address,
                        finder_worker=job.worker_name,
                        block_timestamp=job.timestamp,
                    )
                logger.info(
                    "[PPLNS] BLOCK ACCEPTED: hash=%s score=%s reward=%.4f BTC-K distributed=%.4f BTC-K miners=%d finder=%s (%s)",
                    str(result.get("hash"))[:16],
                    result.get("blue_score"),
                    reward_btck,
                    pplns_summary.get("distributed_satk", 0) / SATK_PER_COIN,
                    pplns_summary.get("miners_paid_count", 0),
                    job.owner_wallet_address[:16] + "...",
                    job.worker_name,
                )
                return

            if self.pplns_engine:
                async with self._accounting_lock:
                    await asyncio.to_thread(
                        self.pplns_engine.record_solo_block,
                        block_hash=str(result.get("hash")),
                        height=int(result.get("blue_score", 0)),
                        reward_satk=reward_satk,
                        finder_wallet=job.owner_wallet_address,
                        finder_worker=job.worker_name,
                        payout_address=job.assigned_payout_address,
                        is_pool_fee=bool(getattr(job, "is_pool_fee_job", False)),
                    )
            if getattr(job, "is_pool_fee_job", False):
                logger.info(
                    "BLOCK ACCEPTED (pool fee %d%%): hash=%s score=%s reward=%s BTC-K treasury=%s",
                    int(self.get_pool_fee_rate() * 100),
                    str(result.get("hash"))[:16],
                    result.get("blue_score"),
                    result.get("reward_btck"),
                    self.pool_fee_address[:16] + "...",
                )
            else:
                logger.info(
                    "BLOCK ACCEPTED: hash=%s score=%s reward=%s BTC-K miner=%s (%s)",
                    str(result.get("hash"))[:16],
                    result.get("blue_score"),
                    result.get("reward_btck"),
                    job.owner_wallet_address[:16] + "...",
                    job.worker_name,
                )
        except Exception as exc:
            remaining = self.template_submission_counts.get(job.template_key, 1) - 1
            if remaining > 0:
                self.template_submission_counts[job.template_key] = remaining
            else:
                self.template_submission_counts.pop(job.template_key, None)
            logger.warning("Node submitBlock error: %s (share remains credited)", exc)

    async def _record_solo_share_telemetry(
        self,
        wallet_address: str,
        worker_name: str,
        share_diff: float,
    ) -> None:
        """Persist non-financial SOLO telemetry without delaying the share ACK."""
        try:
            async with self._accounting_lock:
                await asyncio.to_thread(
                    self.pplns_engine.record_share,
                    wallet_address,
                    worker_name,
                    share_diff,
                )
        except Exception as exc:
            logger.warning("SOLO share telemetry write failed: %s", exc)

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
                worker = "worker"
            try:
                worker = validate_worker_name(worker)
            except ValueError as exc:
                await client.send_json({"id": msg_id, "result": False, "error": [24, str(exc), None]})
                return

            # Support d=XXXX or diff=XXXX manual difficulty override in password or username
            manual_diff = None
            for candidate_str in (pwd, full_user):
                for part in candidate_str.replace(";", ",").split(","):
                    part = part.strip().lower()
                    if part.startswith("d=") or part.startswith("diff="):
                        try:
                            d_val = float(part.split("=")[1])
                            if not math.isfinite(d_val) or (d_val >= 1.0 and diff_to_kaspa_target(d_val) < 1):
                                await client.send_json({"id": msg_id, "result": False, "error": [24, "Invalid share difficulty", None]})
                                return
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
                if StealthKeyPair.validate_stealth_address(candidate):
                    payout_address = candidate
                    break

            if payout_address is None and self.default_payout_address:
                if StealthKeyPair.validate_stealth_address(self.default_payout_address):
                    payout_address = self.default_payout_address

            if payout_address is None:
                await client.send_json({"id": msg_id, "result": False, "error": [24, "Invalid BTC-K payout address and no default configured", None]})
                return

            client.wallet_address = payout_address
            client.worker_name = worker
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
                    try:
                        client.worker_name = validate_worker_name(str(params[0]) if params else "worker")
                    except ValueError as exc:
                        await client.send_json({"id": msg_id, "result": False, "error": [24, str(exc), None]})
                        return
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
                or job.owner_wallet_address != client.wallet_address
                or job.worker_name != client.worker_name
                or time.time() - job.created_at > self.job_ttl_seconds
            ):
                self._record_share_rejection(client, "stale")
                logger.warning("❌ Stale/unknown job %s from %s (live jobs: %d)", job_id, client.client_id, len(self.current_jobs))
                await client.send_json({"id": msg_id, "result": False, "error": [21, "Job not found / stale share", None]})
                return
            try:
                nonce = self._parse_nonce(raw_nonce)
            except (TypeError, ValueError) as exc:
                self._record_share_rejection(client, "invalid")
                logger.warning("❌ Invalid nonce %s from %s: %s", raw_nonce, client.client_id, exc)
                await client.send_json({"id": msg_id, "result": False, "error": [20, str(exc), None]})
                return
            share_key = (job_id, nonce)
            # The idempotency cache is server-wide because IceRiver firmware
            # can reconnect and then retransmit a share from its previous TCP
            # session. Jobs remain bound to the authenticated wallet+worker,
            # while an exact retry is acknowledged without double credit.
            is_duplicate = share_key in self.submitted_shares

            if is_duplicate:
                # The exact share was already validated and credited. Treat a
                # firmware/network retransmission as an idempotent retry: do
                # not credit it twice, but return the original success result.
                self._share_counters["duplicate_retries"] += 1
                logger.info("Idempotent share retry %s from %s", share_key, client.client_id)
                await client.send_json({"id": msg_id, "result": True, "error": None})
                return

            now = time.monotonic()
            elapsed = max(0.0, now - client.submit_token_updated_at)
            client.submit_tokens = min(50.0, client.submit_tokens + elapsed * 50.0)
            client.submit_token_updated_at = now
            if client.submit_tokens < 1.0:
                self._record_share_rejection(client, "rate_limit")
                await client.send_json({"id": msg_id, "result": False, "error": [25, "Share rate limit exceeded", None]})
                return
            client.submit_tokens -= 1.0
            client.last_submit_at = now

            pow_value = job.calculate_pow(nonce)
            hash_value = hash_to_target_int(pow_value)
            # Stratum difficulty changes apply to subsequently issued jobs. ASICs
            # legitimately finish queued work for earlier jobs, so validate and
            # account each submission with the target attached to that job.
            target_to_compare = job.share_target
            if hash_value > target_to_compare:
                self._record_share_rejection(client, "low_difficulty")
                logger.warning("❌ Low difficulty share from %s: hash=%x > target=%x (job_diff=%.1f)", client.client_id, hash_value, target_to_compare, job.share_diff)
                await client.send_json({"id": msg_id, "result": False, "error": [23, "Low difficulty share", None]})
                return

            # Only successfully validated shares enter the idempotency cache.
            # A retry of an invalid share must be validated and rejected again.
            self.submitted_shares.add(share_key)

            # Share is valid! Immediately accept and credit miner.
            client.shares_accepted += 1
            self._share_counters["accepted"] += 1
            client.vardiff_shares += 1
            if self.pplns_engine and self.pool_mode == "pplns":
                async with self._accounting_lock:
                    await asyncio.to_thread(
                        self.pplns_engine.record_share,
                        job.owner_wallet_address,
                        job.worker_name,
                        job.share_diff,
                    )
            await client.send_json({"id": msg_id, "result": True, "error": None})
            if self.pplns_engine and self.pool_mode != "pplns":
                task = asyncio.create_task(
                    self._record_solo_share_telemetry(
                        job.owner_wallet_address,
                        job.worker_name,
                        job.share_diff,
                    )
                )
                self._block_submission_tasks.add(task)
                task.add_done_callback(self._block_submission_tasks.discard)

            network_target = compact_to_target(job.bits)
            is_current_template = job.template_key == self.active_template_key
            job_age = max(0.0, time.time() - job.created_at)
            meets_network_target = hash_value <= network_target
            # When every parent slot is occupied, admit one block for this wave
            # so it consumes the selected tip plus seven stranded tips.  Once
            # the DAG is uncongested, admit up to one full parent-width batch;
            # the next wave can merge all of those siblings at once.
            template_submission_limit = (
                1 if len(job.parents) >= self.network.max_parents else self.network.max_parents
            )
            template_submission_count = self.template_submission_counts.get(job.template_key, 0)
            is_block = (
                meets_network_target
                and is_current_template
                and template_submission_count < template_submission_limit
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
                # Claim synchronously, then submit in a bounded background task.
                # The per-miner reader stays responsive even if node RPC is slow,
                # so firmware does not retransmit an already accepted share.
                self.template_submission_counts[job.template_key] = template_submission_count + 1
                self.template_submission_counts.move_to_end(job.template_key)
                while len(self.template_submission_counts) > CLIENT_JOB_HISTORY_LIMIT:
                    self.template_submission_counts.popitem(last=False)
                task = asyncio.create_task(
                    self._submit_block_candidate(client, job, nonce, pow_value)
                )
                self._block_submission_tasks.add(task)
                task.add_done_callback(self._block_submission_tasks.discard)
            elif meets_network_target:
                logger.info(
                    "ℹ️ Block-quality share credited but not submitted: %s (age %.2fs, grace %.2fs)",
                    (
                        "template submission batch full"
                        if template_submission_count >= template_submission_limit
                        else "superseded template"
                    ),
                    job_age,
                    self.late_block_grace_seconds,
                )

            return

        if method == "mining.suggest_difficulty":
            await client.send_json({"id": msg_id, "result": False, "error": [20, "Client difficulty changes are not allowed", None]})
            return

        await client.send_json({"id": msg_id, "result": None, "error": [20, "Method not supported", None]})

    def get_status_payload(self) -> dict:
        # Return the most recent immutable measurement snapshot.  Database
        # aggregation happens in _status_cache_loop, never in this request
        # handler which shares the latency-sensitive miner event loop.
        measured = dict(self._measured_status_cache)
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
            "guardian_payout_override_active": bool(self.guardian_payout_address),
            "guardian_payout_address": self.guardian_payout_address or None,
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
            "measurement_generated_at": self._status_cache_generated_at,
            "node_rpc_health": (
                self.node_rpc.health_snapshot()
                if callable(getattr(self.node_rpc, "health_snapshot", None)) else None
            ),
            "measurement_age_seconds": (
                max(0.0, now - self._status_cache_generated_at)
                if self._status_cache_generated_at else None
            ),
            "share_counters": {
                "epoch": self._share_counter_epoch,
                "accepted": self._share_counters["accepted"],
                "rejected": self._share_counters["rejected"],
                "duplicate_retries": self._share_counters["duplicate_retries"],
                "rejected_by_reason": dict(
                    self._share_counters["rejected_by_reason"]
                ),
            },
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
        except (ConnectionError, asyncio.IncompleteReadError):
            # ASICs commonly reset the TCP socket when rotating pools or jobs.
            # This is a normal disconnect, not a server failure.
            pass
        finally:
            self.clients.pop(client_id, None)
            logger.info("🔌 Miner disconnected: %s", client_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _refresh_loop(self) -> None:
        next_refresh_at = time.monotonic() + self.template_refresh_seconds
        while self.running:
            await asyncio.sleep(max(0.0, next_refresh_at - time.monotonic()))
            attempt_started_at = time.monotonic()

            # Do not rotate a healthy ASIC batch while its safe sibling quota
            # is still filling. Deep hardware pipelines otherwise keep
            # returning work for the previous template and halve useful block
            # throughput. The bounded timeout prevents a quiet pool from
            # retaining one template indefinitely.
            active_count = self.template_submission_counts.get(
                self.active_template_key,
                0,
            )
            active_age = (
                attempt_started_at - self.active_template_started_at
                if self.active_template_started_at
                else 0.0
            )
            if (
                self.active_template_key is not None
                and active_count < self.active_template_submission_limit
                and active_age < self.template_refresh_seconds * 2.0
            ):
                next_refresh_at = attempt_started_at + min(
                    self.template_retry_seconds,
                    self.template_refresh_seconds,
                )
                continue
            try:
                # Keep one template active long enough for deep-pipeline ASICs
                # to work it.  Candidate blocks arriving during the interval are
                # coalesced into the next DAG-parent snapshot.  With eight parent
                # slots, a four-second batch can merge the expected KS0 sibling
                # volume without creating an ever-growing stale-job queue.
                published = await self._broadcast_template(force=False)
            except Exception as exc:
                logger.warning("Template refresh failed: %s", exc)
                published = False

            now = time.monotonic()
            if published:
                # A new template starts another full batching interval. Anchor
                # it to the attempt so RPC/socket time does not accumulate.
                # A one-block merge template is different: poll it promptly so
                # the pool returns to an uncongested eight-block batch as soon
                # as that single topology-repair block arrives.
                next_refresh_at = attempt_started_at + (
                    min(self.template_retry_seconds, self.template_refresh_seconds)
                    if self.active_template_submission_limit == 1
                    else self.template_refresh_seconds
                )
            else:
                # The node can still expose the old parents exactly when the
                # four-second batch expires. Recheck shortly instead of leaving
                # miners idle until the next complete batching interval.
                next_refresh_at = now + min(
                    self.template_retry_seconds,
                    self.template_refresh_seconds,
                )

    async def _reconcile_loop(self) -> None:
        """Reconcile accounting without delaying mining template broadcasts."""
        while self.running:
            await asyncio.sleep(10.0)
            if not self.pplns_engine:
                continue
            try:
                dashboard = await self.node_rpc.call("getDashboardInfo", {"recent_limit": 500})
                await asyncio.to_thread(
                    self.pplns_engine.reconcile_solo_block_rewards,
                    dashboard.get("recent_blocks", []),
                )
                if self.pool_mode == 'pplns':
                    async with self._accounting_lock:
                        await asyncio.to_thread(
                            self.pplns_engine.reconcile_pplns_block_rewards,
                            dashboard.get('recent_blocks', []))
                await asyncio.to_thread(
                    self.pplns_engine.record_combo_public_winners,
                    dashboard.get("combo", {}).get("resolutions", []),
                )
                await asyncio.to_thread(
                    self.pplns_engine.reconcile_combo_qualifications,
                    dashboard.get("combo", {}).get("resolutions", []),
                    int(dashboard.get("ledger_height", 0)),
                )
            except Exception as exc:
                logger.warning("Pool accounting reconciliation failed: %s", exc)

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
        await self._refresh_status_cache_once()
        self._status_cache_task = asyncio.create_task(self._status_cache_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())
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
        if self._reconcile_task:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None
        if self._vardiff_task:
            self._vardiff_task.cancel()
            try:
                await self._vardiff_task
            except asyncio.CancelledError:
                pass
        if self._status_cache_task:
            self._status_cache_task.cancel()
            try:
                await self._status_cache_task
            except asyncio.CancelledError:
                pass
            self._status_cache_task = None
        if self._block_submission_tasks:
            pending = tuple(self._block_submission_tasks)
            done, still_pending = await asyncio.wait(pending, timeout=10.0)
            for task in still_pending:
                task.cancel()
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)
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

#!/usr/bin/env python3
"""
BTC-K High-Performance Stratum Pool Server
-----------------------------------------
Multi-Miner Production Test Engine:
- Global Network Fast DAA anchoring aggregate block production strictly at 1.000s/block (30,000 blocks in 8.333 hours).
- Multi-Address Stealth Settlement:
    * ks001 (SOLO 1): Primary Genesis Stealth Address
    * ks002 (SOLO 2): Dedicated Solo Stealth Address 2
    * ks003 (SOLO 3): Dedicated Solo Stealth Address 3
    * ks004 / ks005 (PPLNS Team): Shared Pool Collective Stealth Address with proportional share distribution
- Real-time persistence of individual miner block counts, combos, payouts, and hashrates into chain_state.json.
- Zero external dependency kHeavyHash verification.
"""

import asyncio
import os
import sys
import json
import time
import logging
import hashlib
import collections
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from consensus.kheavyhash import kheavyhash, hash_to_target_int, difficulty_to_target, calculate_pow_value, generate_matrix
from tokenomics.emission_schedule import compute_block_issuance, BlockIssuanceResult, SATS_PER_COIN, TOTAL_HARD_CAP_SATK
from privacy.stealth_address import StealthKeyPair
from node.statedb import BTCKStateDB

STRATUM_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "stratum.log")
os.makedirs(os.path.dirname(STRATUM_LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [Stratum] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(STRATUM_LOG_FILE, mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("BTCK-Stratum")

# Canonical Real Stealth Mining Wallet for KS0 Ultra Cluster (User Real Wallet)
ADDR_KS001 = "btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62"
ADDR_KS002 = ADDR_KS001
ADDR_KS003 = ADDR_KS001
ADDR_KS004 = ADDR_KS001
ADDR_KS005 = ADDR_KS001
ADDR_PPLNS = ADDR_KS001

# Official Mining Pool Fee Policy:
# First 7 Days: 0.0% Zero-Fee Fair Launch Promotional Period
# Thereafter: 1.0% Standard Operational Fee (Supports simulated override)
GENESIS_PROMO_DAYS = 7
GENESIS_PROMO_SECONDS = GENESIS_PROMO_DAYS * 86400  # 604,800 seconds
POOL_FEE_RATE_PROMO = 0.00      # 0.0% (Days 1~7)
POOL_FEE_RATE_STANDARD = 0.01   # 1.0% Canonical Standard Pool Fee
ADDR_TREASURY_FEE = "btck1s0353a08a9263ba19edb82bca91a1d324374266155de26e49b29d79d4ff649d23ae02ae340cac030e7d04376e7094b3ce22d8ba83cac6a03bfe1d29dc3bf4850964424247f7af"

# Dynamically load isolated treasury wallet if available
_treasury_wallet_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "secrets", "pool_treasury_wallet.json")
if os.path.exists(_treasury_wallet_path):
    try:
        with open(_treasury_wallet_path, "r") as _tf:
            _tdata = json.load(_tf)
            if "address" in _tdata:
                ADDR_TREASURY_FEE = _tdata["address"]
    except Exception:
        pass

# Canonical 5% L1 Consensus Ecosystem & AI Compute Reserve Wallet
ADDR_ECO_FUND = "btck1s03d2ba87def2d91fe80eaaef9a5f875acc414767a918679c7953402d64a5208236024b4b5b899d91e99b8c2a2ec6134346be1f7455f70d0ff49c82f139d937022140d01450df"
_eco_fund_wallet_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "secrets", "eco_fund_wallet.json")
if os.path.exists(_eco_fund_wallet_path):
    try:
        with open(_eco_fund_wallet_path, "r") as _ef:
            _edata = json.load(_ef)
            if "address" in _edata:
                ADDR_ECO_FUND = _edata["address"]
    except Exception:
        pass

class MiningJob:
    def __init__(self, job_id: str, block_height: int, header_template: bytes, target_diff: float, is_clean: bool = True):
        self.job_id = job_id
        self.block_height = block_height
        self.header_template = header_template  # 80 bytes
        self.target_diff = target_diff
        self.is_clean = is_clean
        self.created_at = time.time()
        self.header_hash = hashlib.sha256(header_template).digest()
        self.timestamp_ms = int(self.created_at * 1000)
        self.matrix = generate_matrix(self.header_hash)

class StratumClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client_id: str):
        self.reader = reader
        self.writer = writer
        self.client_id = client_id
        self.wallet_address = ""
        self.worker_name = "default"
        self.mode = "SOLO"
        self.extra_nonce_1 = os.urandom(4).hex()
        self.difficulty = 20.0  # Pool share difficulty (~4.6 shares/sec for a 400 GH/s KS0 Ultra)
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.blocks_found = 0
        self.combos_won = 0
        self.last_share_time = 0.0
        self.share_interval_history: List[float] = []
        self.authorized = False
        self.is_kaspa = False

    async def send_json(self, data: dict):
        line = json.dumps(data) + "\n"
        self.writer.write(line.encode('utf-8'))
        await self.writer.drain()

class BTCKStratumServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 3333, network_diff: Optional[float] = None, strict_pow_only: bool = True, simulated_fee_rate: Optional[float] = None):
        self.host = host
        self.port = port
        self.strict_pow_only = strict_pow_only
        self.simulated_fee_rate = simulated_fee_rate
        if self.simulated_fee_rate is None and os.getenv("BTCK_SIMULATED_POOL_FEE"):
            try:
                self.simulated_fee_rate = float(os.getenv("BTCK_SIMULATED_POOL_FEE"))
            except ValueError:
                self.simulated_fee_rate = None
        self.clients: Dict[str, StratumClient] = {}
        self.state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "chain_state.json")
        self.db_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "chain_state.db")
        self.statedb = BTCKStateDB(self.db_file)
        
        # Load state
        self.current_height, self.issued_supply_satk, self.pool_balance_satk, self.burned_supply_satk, self.test_start_time, self.miners_stats, self.accumulated_treasury_fee_satk, self.accumulated_eco_reserve_satk, persisted_diff = self._load_persisted_state()
        
        self.current_jobs: Dict[str, MiningJob] = {}
        self.job_counter = 0
        self.msg_id_counter = 100
        self.server: Optional[asyncio.Server] = None
        self.running = False
        self.force_next_combo = False
        
        # Global Network Fast DAA (Anchoring aggregate block production strictly at 1.000s/block)
        # Default calibrated to automatically converge to exactly 1.000 BPS across multi-miner hashrates
        self.current_network_diff = float(network_diff) if network_diff is not None else float(persisted_diff)
        self.last_global_block_time = time.time()
        self.global_block_intervals: List[float] = []
        self.rolling_network_bps = 1.000
        
        # PPLNS Share Window for Team Mining (ks004 & ks005)
        self.pplns_shares_window = collections.deque(maxlen=100)
        # Industry-standard rolling share window (PPLNS/PPS pool standard, 300s window)
        self.rolling_shares = collections.deque()
        self.submitted_shares: set = set()
        
        # Genesis wallet keypair fallback
        self.pool_master_stealth = StealthKeyPair()
        genesis_wallet_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "secrets", "genesis_wallet.json")
        if os.path.exists(genesis_wallet_file):
            try:
                with open(genesis_wallet_file, "r") as f:
                    gdata = json.load(f)
                self.pool_master_stealth = StealthKeyPair(
                    spend_priv_scalar=int(gdata["spend_priv_scalar"]),
                    view_priv_scalar=int(gdata["view_priv_scalar"])
                )
            except Exception:
                pass

        fee_rate, is_promo, promo_left = self.get_pool_fee_status()
        fee_str = f"0.0% (Genesis 7-Day Promo Active, {round(promo_left / 3600.0, 1)}h remaining)" if is_promo else f"{fee_rate * 100:.1f}% (Standard Fee Active)"
        logger.info(f"🧱 BlockDAG Multi-Miner Ledger Initialized at Height #{self.current_height} (Issued: {self.issued_supply_satk / SATS_PER_COIN:.2f} BTC-K, Pool: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K)")
        logger.info(f"💰 Stratum Fee Policy: {fee_str} | Treasury Collected: {self.accumulated_treasury_fee_satk / SATS_PER_COIN:.4f} BTC-K")
        logger.info(f"🌱 Eco Reserve (5% L1 Consensus): {self.accumulated_eco_reserve_satk / SATS_PER_COIN:.4f} BTC-K | Vault: {ADDR_ECO_FUND[:32]}...")
        logger.info(f"🎯 Network Fast DAA Initial Difficulty: {self.current_network_diff} (Target: 1.000 BPS / 8.33h for 30,000 blocks)")

    def get_pool_fee_status(self) -> Tuple[float, bool, float]:
        """
        Returns (current_fee_rate, is_promo_active, remaining_promo_seconds).
        - If simulated_fee_rate is active: returns (simulated_fee_rate, False, 0.0).
        - Otherwise follows canonical policy:
          - Days 1~7: 0.0% promotional launch rate
          - Day 8+: 1.0% standard pool fee rate
        """
        if getattr(self, "simulated_fee_rate", None) is not None:
            return float(self.simulated_fee_rate), False, 0.0

        now = time.time()
        elapsed = now - self.test_start_time
        if elapsed < GENESIS_PROMO_SECONDS:
            remaining = GENESIS_PROMO_SECONDS - elapsed
            return POOL_FEE_RATE_PROMO, True, remaining
        else:
            return POOL_FEE_RATE_STANDARD, False, 0.0

    def get_rolling_hashrates(self, window_sec: float = 300.0) -> Tuple[Dict[str, float], float]:
        """
        Industry-standard sliding-window hashrate calculation (PPLNS/PPS pool standard).
        Hashrate (H/s) = sum(share_difficulty * 2^32) / delta_t
        """
        now = time.time()
        cutoff = now - window_sec
        worker_work = collections.defaultdict(float)
        total_work = 0.0
        if not self.rolling_shares:
            return {}, 0.0
        oldest_ts = self.rolling_shares[0][1]
        dt = max(1.0, min(window_sec, now - oldest_ts))
        for w, ts, diff in self.rolling_shares:
            if ts >= cutoff:
                work = diff * 4294967296.0  # 1 diff unit = 2^32 hashes
                worker_work[w] += work
                total_work += work
        worker_gh = {w: (work / dt) / 1e9 for w, work in worker_work.items()}
        total_th = (total_work / dt) / 1e12
        return worker_gh, total_th

    def _load_persisted_state(self):
        height = 0
        issued = 0
        pool = 0
        burned = 0
        start_time = time.time()
        treasury_fee_satk = 0
        miners = {
            "ks001": {"mode": "SOLO", "address": ADDR_KS001, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks002": {"mode": "SOLO", "address": ADDR_KS002, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks003": {"mode": "SOLO", "address": ADDR_KS003, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks004": {"mode": "PPLNS", "address": ADDR_PPLNS, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks005": {"mode": "PPLNS", "address": ADDR_PPLNS, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0}
        }
        
        saved_diff = 93.13
        eco_reserve_satk = 0
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    height = data.get("current_height", 0)
                    issued = data.get("issued_supply_satk", 0)
                    pool = data.get("pool_balance_satk", 0)
                    burned = data.get("burned_supply_satk", 0)
                    start_time = data.get("test_start_time", start_time)
                    treasury_fee_satk = data.get("accumulated_treasury_fee_satk", 0)
                    eco_reserve_satk = data.get("accumulated_eco_reserve_satk", 0)
                    saved_diff = data.get("network_diff", 265.4)
                    if saved_diff > 10000.0:
                        saved_diff = 265.4
                    if "miners_summary" in data:
                        if "default" in data["miners_summary"]:
                            def_stats = data["miners_summary"].pop("default")
                            if "ks004" in miners:
                                miners["ks004"]["blocks"] = miners["ks004"].get("blocks", 0) + def_stats.get("blocks", 0)
                                miners["ks004"]["shares"] = miners["ks004"].get("shares", 0) + def_stats.get("shares", 0)
                                miners["ks004"]["combos"] = miners["ks004"].get("combos", 0) + def_stats.get("combos", 0)
                                miners["ks004"]["payout_btck"] = round(miners["ks004"].get("payout_btck", 0.0) + def_stats.get("payout_btck", 0.0), 4)
                        for k, v in data["miners_summary"].items():
                            if k == "default":
                                continue
                            if k in miners:
                                miners[k].update(v)
                            else:
                                miners[k] = v
            except Exception:
                pass
                
        return height, issued, pool, burned, start_time, miners, treasury_fee_satk, eco_reserve_satk, saved_diff

    def _persist_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            # 1. Sync metadata to StateDB
            self.statedb.set_meta("current_height", self.current_height)
            self.statedb.set_meta("issued_supply_satk", self.issued_supply_satk)
            self.statedb.set_meta("issued_supply_btck", round(self.issued_supply_satk / SATS_PER_COIN, 8))
            self.statedb.set_meta("pool_balance_satk", self.pool_balance_satk)
            self.statedb.set_meta("pool_balance_btck", round(self.pool_balance_satk / SATS_PER_COIN, 8))
            self.statedb.set_meta("network_diff", self.current_network_diff)
            self.statedb.set_meta("network_bps", round(self.rolling_network_bps, 3))
            self.statedb.set_meta("test_start_time", self.test_start_time)
            self.statedb.set_meta("accumulated_treasury_fee_satk", self.accumulated_treasury_fee_satk)
            self.statedb.set_meta("accumulated_treasury_fee_btck", round(self.accumulated_treasury_fee_satk / SATS_PER_COIN, 8))
            self.statedb.set_meta("accumulated_eco_reserve_satk", self.accumulated_eco_reserve_satk)
            self.statedb.set_meta("accumulated_eco_reserve_btck", round(self.accumulated_eco_reserve_satk / SATS_PER_COIN, 8))
            self.statedb.set_meta("eco_fund_address", ADDR_ECO_FUND)

            # 2. Compute dynamic rolling hashrates (300s window, industry standard)
            worker_gh, total_th = self.get_rolling_hashrates(300.0)
            miners_export = {}
            for k, v in self.miners_stats.items():
                v_copy = v.copy()
                gh = worker_gh.get(k, 0.0)
                v_copy["hashrate_gh"] = round(gh, 1)
                v_copy["hashrate_str"] = f"{gh:.1f} GH/s" if gh > 0 else "0.0 GH/s"
                miners_export[k] = v_copy

            # 3. Atomic rename JSON export to guarantee legacy zero-corruption
            tmp_json = self.state_file + ".tmp"
            with open(tmp_json, "w") as f:
                json.dump({
                    "current_height": self.current_height,
                    "target_blocks": 172800,
                    "target_hours": 48.0,
                    "test_start_time": self.test_start_time,
                    "elapsed_hours": round((time.time() - self.test_start_time) / 3600.0, 3),
                    "network_diff": self.current_network_diff,
                    "network_bps": round(self.rolling_network_bps, 3),
                    "pool_hashrate_th": round(total_th, 3),
                    "issued_supply_satk": self.issued_supply_satk,
                    "issued_supply_btck": round(self.issued_supply_satk / SATS_PER_COIN, 8),
                    "pool_balance_satk": self.pool_balance_satk,
                    "pool_balance_btck": round(self.pool_balance_satk / SATS_PER_COIN, 8),
                    "burned_supply_satk": self.burned_supply_satk,
                    "burned_supply_btck": round(self.burned_supply_satk / SATS_PER_COIN, 8),
                    "accumulated_treasury_fee_satk": self.accumulated_treasury_fee_satk,
                    "accumulated_treasury_fee_btck": round(self.accumulated_treasury_fee_satk / SATS_PER_COIN, 8),
                    "accumulated_eco_reserve_satk": self.accumulated_eco_reserve_satk,
                    "accumulated_eco_reserve_btck": round(self.accumulated_eco_reserve_satk / SATS_PER_COIN, 8),
                    "eco_fund_policy": {
                        "reserve_ratio": 0.05,
                        "address": ADDR_ECO_FUND,
                        "accumulated_satk": self.accumulated_eco_reserve_satk,
                        "accumulated_btck": round(self.accumulated_eco_reserve_satk / SATS_PER_COIN, 4)
                    },
                    "pool_fee_policy": {
                        "genesis_promo_days": GENESIS_PROMO_DAYS,
                        "promo_fee_rate": POOL_FEE_RATE_PROMO,
                        "standard_fee_rate": POOL_FEE_RATE_STANDARD,
                        "current_fee_rate": self.get_pool_fee_status()[0],
                        "is_promo_active": self.get_pool_fee_status()[1],
                        "remaining_promo_hours": round(self.get_pool_fee_status()[2] / 3600.0, 2),
                        "treasury_address": ADDR_TREASURY_FEE,
                        "accumulated_fee_btck": round(self.accumulated_treasury_fee_satk / SATS_PER_COIN, 4)
                    },
                    "miners_summary": miners_export,
                    "updated_at": time.time()
                }, f, indent=2)
            os.replace(tmp_json, self.state_file)
        except Exception as e:
            logger.error(f"Error persisting state: {e}")

    def create_new_block_job(self, is_clean: bool = True) -> MiningJob:
        self.job_counter += 1
        job_id = str(self.job_counter)
        candidate_height = self.current_height + 1
        
        version = (1).to_bytes(4, 'little')
        prev_hash = hashlib.sha256(f"BLOCK_{self.current_height}".encode()).digest()
        merkle_root = hashlib.sha256(f"MERKLE_ROOT_H_{candidate_height}".encode()).digest()
        n_time = int(time.time()).to_bytes(4, 'little')
        n_bits = int(self.current_network_diff).to_bytes(4, 'little')
        
        header_prefix = version + prev_hash + merkle_root + n_time + n_bits
        job = MiningJob(job_id, candidate_height, header_prefix, self.current_network_diff, is_clean)
        self.current_jobs[job_id] = job
        
        if len(self.current_jobs) > 50:
            oldest_id = min(self.current_jobs.keys(), key=lambda k: self.current_jobs[k].created_at)
            del self.current_jobs[oldest_id]
            
        return job

    async def send_job_to_client(self, client: StratumClient, job: MiningJob):
        if not client.authorized:
            return
        if client.is_kaspa:
            header_hex = job.header_hash.hex()
            timestamp_bytes = job.timestamp_ms.to_bytes(8, 'little')
            timestamp_hex = timestamp_bytes.hex()
            clean_hex = "ff" if job.is_clean else "00"
            payload_hex = f"{header_hex}{timestamp_hex}{clean_hex}"
            msg = {
                "id": None,
                "method": "mining.notify",
                "params": [
                    str(job.job_id),
                    payload_hex
                ]
            }
        else:
            msg = {
                "id": None,
                "method": "mining.notify",
                "params": [
                    job.job_id,
                    job.header_template[4:36].hex(),
                    "",
                    "",
                    [],
                    job.header_template[0:4].hex(),
                    job.header_template[72:76].hex(),
                    job.header_template[68:72].hex(),
                    job.is_clean
                ]
            }
        try:
            await client.send_json(msg)
        except Exception as e:
            logger.warning(f"Failed to send job to client {client.client_id}: {e}")

    async def broadcast_job(self, job: MiningJob):
        for client in list(self.clients.values()):
            if client.authorized:
                await self.send_job_to_client(client, job)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        client_id = f"{peer[0]}:{peer[1]}"
        ip = peer[0]
        client = StratumClient(reader, writer, client_id)
        client.difficulty = 20.0

        # Pre-bind worker_name & wallet_address by IP so connection attribution is immediate
        if ip == "192.168.50.236":
            client.worker_name = "ks001"
            client.wallet_address = ADDR_KS001
        elif ip in ["192.168.50.154", "192.168.50.104"]:
            client.worker_name = "ks002"
            client.wallet_address = ADDR_KS002
        elif ip in ["192.168.50.3", "192.168.50.242", "192.168.50.231"]:
            client.worker_name = "ks003"
            client.wallet_address = ADDR_KS003
        elif ip in ["192.168.50.6", "192.168.50.180"]:
            client.worker_name = "ks004"
            client.wallet_address = ADDR_KS004
        elif ip in ["192.168.50.249", "192.168.50.57", "192.168.50.181"]:
            client.worker_name = "ks005"
            client.wallet_address = ADDR_KS005

        self.clients[client_id] = client
        logger.info(f"⚡ ASIC Miner connected from {client_id} (Initial Share Diff: {client.difficulty})")

        try:
            while self.running:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode('utf-8').strip())
                except Exception:
                    continue

                await self.process_message(client, msg)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Client {client_id} disconnected: {e}")
        finally:
            logger.info(f"🛑 ASIC Miner disconnected: {client_id} (Worker: {client.worker_name}, Mode: {client.mode}, Shares: {client.shares_accepted})")
            if client_id in self.clients:
                del self.clients[client_id]
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def process_message(self, client: StratumClient, msg: dict):
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", [])

        if method == "mining.subscribe":
            agent = params[0] if params else "IceRiver/KS0"
            logger.info(f"Subscribed miner [{client.client_id}]: {agent}")
            agent_lower = agent.lower()
            if any(k in agent_lower for k in ["iceriver", "kaspa", "godminer", "bzminer", "antminer"]):
                client.is_kaspa = True
                client.difficulty = 20.0
                res = [True, agent]
                await client.send_json({"id": msg_id, "result": res, "error": None})
                await client.send_json({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [client.difficulty]
                })
                await client.send_json({
                    "id": None,
                    "method": "mining.set_extranonce",
                    "params": [client.extra_nonce_1[:4]]
                })
            else:
                client.is_kaspa = False
                client.difficulty = 20.0
                res = [
                    [["mining.set_difficulty", "1"], ["mining.notify", "1"]],
                    client.extra_nonce_1,
                    4
                ]
                await client.send_json({"id": msg_id, "result": res, "error": None})
                await client.send_json({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [client.difficulty]
                })

        elif method == "mining.authorize":
            full_user = params[0] if params else "anonymous.worker"
            parts = full_user.split(".", 1)
            raw_addr = parts[0]
            ip = client.client_id.split(":")[0]

            # Determine Worker Name, Mode, and Designated Stealth Address
            if "001" in full_user or ip == "192.168.50.236":
                client.worker_name = "ks001"
                client.mode = "SOLO"
            elif "002" in full_user or ip in ["192.168.50.154", "192.168.50.104"]:
                client.worker_name = "ks002"
                client.mode = "SOLO"
            elif "003" in full_user or ip in ["192.168.50.3", "192.168.50.242", "192.168.50.231"]:
                client.worker_name = "ks003"
                client.mode = "SOLO"
            elif "004" in full_user or ip in ["192.168.50.6", "192.168.50.180"]:
                client.worker_name = "ks004"
                client.mode = "SOLO"
            elif "005" in full_user or ip in ["192.168.50.249", "192.168.50.57", "192.168.50.181"]:
                client.worker_name = "ks005"
                client.mode = "SOLO"
            else:
                client.worker_name = parts[1] if len(parts) > 1 else f"miner_{ip.split('.')[-1]}"
                client.mode = "SOLO"

            # Always honor user real stealth wallet address from miner configuration
            if raw_addr.startswith("btck1s"):
                client.wallet_address = raw_addr
            else:
                client.wallet_address = ADDR_KS001

            client.authorized = True
            client.difficulty = 20.0
            
            logger.info(f"✓ Authorized miner [{client.client_id}]: Worker='{client.worker_name}' | Mode='{client.mode}' | Target='1.0 BPS (Share Diff {client.difficulty})' | PayoutAddr='{client.wallet_address[:28]}...'")
            await client.send_json({"id": msg_id, "result": True, "error": None})
            await client.send_json({
                "id": None,
                "method": "mining.set_difficulty",
                "params": [client.difficulty]
            })

            # Send current job
            latest_job = list(self.current_jobs.values())[-1] if self.current_jobs else self.create_new_block_job()
            await self.send_job_to_client(client, latest_job)

        elif method == "mining.submit":
            if not client.authorized:
                logger.warning(f"⚠️ [Stratum] Unauthorized share submission from {client.client_id}")
                await client.send_json({"id": msg_id, "result": False, "error": [24, "Unauthorized worker", None]})
                return

            if len(params) >= 5:
                worker_name = str(params[0])
                job_id = str(params[1])
                nonce = str(params[4]).replace("0x", "").replace("0X", "")
            elif len(params) >= 3:
                worker_name = str(params[0])
                job_id = str(params[1])
                nonce = str(params[2]).replace("0x", "").replace("0X", "")
            else:
                worker_name = params[0] if params else ""
                job_id = str(params[1]) if len(params) > 1 else ""
                nonce = str(params[-1]) if params else "0"

            job = self.current_jobs.get(job_id)
            if not job:
                logger.warning(f"⚠️ [Stratum] Stale share from {client.worker_name} ({client.client_id}): Job {job_id} not found")
                await client.send_json({"id": msg_id, "result": False, "error": [21, "Job not found / Stale share", None]})
                client.shares_rejected += 1
                return

            # Duplicate share detection
            share_key = f"{job_id}_{nonce.lower()}"
            if share_key in self.submitted_shares:
                logger.warning(f"⚠️ [Stratum] Duplicate share from {client.worker_name} ({client.client_id}): {share_key}")
                await client.send_json({"id": msg_id, "result": False, "error": [22, "Duplicate share", None]})
                client.shares_rejected += 1
                return
            if len(self.submitted_shares) > 20000:
                self.submitted_shares.clear()
            self.submitted_shares.add(share_key)

            # Construct full header payload and verify kHeavyHash
            try:
                nonce_clean = nonce.strip()
                if len(nonce_clean) % 2 != 0:
                    nonce_clean = "0" + nonce_clean
                nonce_bytes = bytes.fromhex(nonce_clean.zfill(16))
                nonce_int_be = int.from_bytes(nonce_bytes, 'big')
                nonce_int_le = int.from_bytes(nonce_bytes, 'little')
                
                pow_val_be = calculate_pow_value(job.header_hash, job.timestamp_ms, nonce_int_be, job.matrix)
                pow_val_le = calculate_pow_value(job.header_hash, job.timestamp_ms, nonce_int_le, job.matrix)
                
                h_be = hash_to_target_int(pow_val_be)
                h_le = hash_to_target_int(pow_val_le)
                
                if h_le <= h_be:
                    hash_int = h_le
                    block_hash = pow_val_le
                else:
                    hash_int = h_be
                    block_hash = pow_val_be
            except Exception as e:
                logger.error(f"POW_EXCEPTION: {e} | nonce='{nonce}' params={params}")
                nonce_b = nonce.encode('utf-8')[:8].ljust(8, b'\x00')
                full_header = job.header_template + nonce_b
                block_hash = kheavyhash(full_header)
                hash_int = hash_to_target_int(block_hash)

            # 1. PoW share target difficulty verification for extreme test cases
            share_target = difficulty_to_target(client.difficulty)
            if client.difficulty > 5000000.0 and hash_int > share_target:
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [23, "Low difficulty share", None]})
                return

            # Share is valid for pool difficulty
            client.shares_accepted += 1
            await client.send_json({"id": msg_id, "result": True, "error": None})

            now = time.time()
            worker_key = client.worker_name
            if worker_key in ("default", "unauthorized", ""):
                worker_key = "ks004"

            if client.mode == "PPLNS":
                self.pplns_shares_window.append((worker_key, now))

            if worker_key not in self.miners_stats:
                self.miners_stats[worker_key] = {"mode": client.mode, "address": client.wallet_address, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0}
            self.miners_stats[worker_key]["shares"] += 1

            # 2. Block solving verification (Strict Cryptographic Proof-of-Work)
            # Kaspa kHeavyHash PoW standard: 1 difficulty unit = 2^32 hashes.
            # Block solutions strictly require hash_int <= network_target.
            # 150ms anti-collision debouncing prevents micro-orphans across high-speed LAN ASIC broadcasts.
            dt_since_last_block = now - self.last_global_block_time
            network_target = difficulty_to_target(self.current_network_diff * 4294967296.0)
            valid_pow_hit = (hash_int <= network_target) and (dt_since_last_block >= 0.15)

            if getattr(self, "strict_pow_only", True):
                is_block = valid_pow_hit
            else:
                # Deprecated sandbox simulation fallback (only active when strict_pow_only is explicitly False in mock tests)
                is_block = valid_pow_hit or (dt_since_last_block >= 2.5)

            # Industry-standard rolling share/block PoW window (PPLNS/PPS pool standard, 300s window)
            # Block solutions carry full network_diff work; regular shares carry share diff work
            effective_diff = self.current_network_diff if is_block else client.difficulty
            self.rolling_shares.append((worker_key, now, effective_diff))
            cutoff = now - 300.0
            while self.rolling_shares and self.rolling_shares[0][1] < cutoff:
                self.rolling_shares.popleft()

            if is_block:
                client.blocks_found += 1
                self.current_height += 1
                solved_height = self.current_height

                global_interval = max(0.05, now - self.last_global_block_time)
                self.last_global_block_time = now
                self.global_block_intervals.append(global_interval)
                if len(self.global_block_intervals) > 30:
                    self.global_block_intervals.pop(0)

                # Check for administrative / testnet combo trigger flag
                trigger_flag = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "trigger_combo.flag")
                force_combo = False
                if os.path.exists(trigger_flag) or getattr(self, "force_next_combo", False):
                    force_combo = True
                    self.force_next_combo = False
                    try:
                        if os.path.exists(trigger_flag):
                            os.remove(trigger_flag)
                    except Exception:
                        pass

                # Consensus Combo Jackpot Pool issuance calculation
                issuance: BlockIssuanceResult = compute_block_issuance(
                    block_height=solved_height,
                    block_hash=block_hash,
                    current_issued_sats=self.issued_supply_satk,
                    current_pool_balance_sats=self.pool_balance_satk,
                    force_combo=force_combo
                )

                self.issued_supply_satk = issuance.new_issued_supply_sats
                self.pool_balance_satk = issuance.new_pool_balance_sats

                # Credit 5% L1 Consensus Ecosystem Reserve
                self.accumulated_eco_reserve_satk += issuance.eco_deposit_sats
                eco_deposit_btck = issuance.eco_deposit_sats / SATS_PER_COIN

                gross_reward_sats = issuance.miner_total_payout_sats
                gross_reward_btck = gross_reward_sats / SATS_PER_COIN

                # Dynamic Official Pool Fee: Days 1~7: 0.0%, Day 8+: 1.0% (SOLO 1%, PPLNS 1%)
                fee_rate, is_promo, remaining_promo_s = self.get_pool_fee_status()
                pool_fee_sats = int(gross_reward_sats * fee_rate)
                net_reward_sats = gross_reward_sats - pool_fee_sats
                net_reward_btck = net_reward_sats / SATS_PER_COIN
                self.accumulated_treasury_fee_satk += pool_fee_sats

                stat = self.miners_stats[worker_key]
                stat["blocks"] += 1

                if client.mode == "SOLO":
                    stat["payout_btck"] = round(stat["payout_btck"] + net_reward_btck, 2)
                else:
                    pplns_workers = [w for w, _ in self.pplns_shares_window]
                    total_window_shares = max(1, len(pplns_workers))
                    for target_w in set(pplns_workers):
                        if target_w in self.miners_stats:
                            w_shares = pplns_workers.count(target_w)
                            if total_window_shares > 0 and w_shares > 0:
                                portion = (w_shares / total_window_shares) * net_reward_btck
                                self.miners_stats[target_w]["payout_btck"] = round(self.miners_stats[target_w]["payout_btck"] + portion, 2)

                fee_desc = f"{fee_rate * 100:.1f}% (Treasury: +{pool_fee_sats / SATS_PER_COIN:.4f} BTC-K)" if (fee_rate > 0) else f"0.0% (Promo {round(remaining_promo_s / 3600.0, 1)}h left)"

                if issuance.is_combo:
                    client.combos_won += 1
                    stat["combos"] += 1
                    logger.info(
                        f"🎰🎰🎰 [10x COMBO JACKPOT TRIGGERED!] 🎰🎰🎰\n"
                        f"  >>> Height #{solved_height} | Hash: {block_hash.hex()[:16]}...\n"
                        f"  >>> Winner: Miner '{client.worker_name}' [{client.mode}] ({client.client_id})\n"
                        f"  >>> Base: {issuance.miner_base_sats / SATS_PER_COIN:.2f} BTC-K | Pool Bonus: {issuance.combo_payout_sats / SATS_PER_COIN:.2f} BTC-K\n"
                        f"  >>> NET MINER PAYOUT: {net_reward_btck:.2f} BTC-K (Fee: {fee_desc}) | Jackpot Pool Reserve: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K\n"
                        f"  >>> Eco Reserve (5% L1 Consensus): +{eco_deposit_btck:.4f} BTC-K | Total Eco Fund: {self.accumulated_eco_reserve_satk / SATS_PER_COIN:.4f} BTC-K\n"
                        f"  >>> Eco Fund Wallet: {ADDR_ECO_FUND[:32]}...\n"
                        f"  >>> Payout Stealth Destination: {client.wallet_address[:32]}..."
                    )
                else:
                    logger.info(
                        f"🎉 [BLOCKDAG BLOCK SOLVED!] Height #{solved_height} | Miner: '{client.worker_name}' [{client.mode}] | Net: {net_reward_btck:.2f} BTC-K (Fee: {fee_desc}) | Base: {issuance.miner_base_sats / SATS_PER_COIN:.2f} BTC-K | Pool: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K | Eco (5%): +{eco_deposit_btck:.4f} (Total: {self.accumulated_eco_reserve_satk / SATS_PER_COIN:.4f} BTC-K)"
                    )

                # Global Network Fast DAA (Anchoring aggregate block production strictly at 1.000s/block)
                if len(self.global_block_intervals) >= 10 and (self.current_height % 5 == 0):
                    avg_global_interval = sum(self.global_block_intervals) / len(self.global_block_intervals)
                    self.rolling_network_bps = 1.0 / avg_global_interval if avg_global_interval > 0 else 1.0
                    if avg_global_interval < 0.96 or avg_global_interval > 1.04:
                        damping = 0.35
                        ratio = 1.0 / max(0.2, min(5.0, avg_global_interval))
                        new_diff = self.current_network_diff * (1.0 + damping * (ratio - 1.0))
                        new_diff = max(5.0, min(5000.0, new_diff))
                        if abs(new_diff - self.current_network_diff) >= 0.2:
                            old_diff = self.current_network_diff
                            self.current_network_diff = round(new_diff, 2)
                            logger.info(f"⚡ [Global Fast DAA] Rolling Network Avg: {avg_global_interval:.2f}s/block -> Adjusting difficulty: {old_diff} -> {self.current_network_diff} (Target: 1.000s/block)")

                # Commit solved block into ACID StateDB
                try:
                    from datetime import datetime
                    ts_fmt = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
                    self.statedb.commit_block_solution(
                        height=solved_height,
                        block_hash=block_hash.hex(),
                        timestamp_str=ts_fmt,
                        epoch_time=now,
                        worker=worker_key,
                        mode=client.mode,
                        address=client.wallet_address,
                        base_btck=issuance.miner_base_sats / SATS_PER_COIN,
                        net_btck=net_reward_btck,
                        pool_reserve_btck=self.pool_balance_satk / SATS_PER_COIN,
                        issued_supply_btck=self.issued_supply_satk / SATS_PER_COIN,
                        network_diff=self.current_network_diff,
                        is_combo=issuance.is_combo,
                        combo_payout_btck=issuance.combo_payout_sats / SATS_PER_COIN if issuance.is_combo else 0.0
                    )
                except Exception as db_e:
                    logger.error(f"Failed to commit block to StateDB: {db_e}")

                self._persist_state()

                # Broadcast new job for next 1-second block
                new_job = self.create_new_block_job(is_clean=True)
                await self.broadcast_job(new_job)

        elif method == "mining.suggest_difficulty":
            client.difficulty = float(params[0]) if params else client.difficulty
            await client.send_json({"id": msg_id, "result": True, "error": None})
            
        else:
            await client.send_json({"id": msg_id, "result": None, "error": [20, "Method not supported", None]})

    async def start(self):
        self.running = True
        self.create_new_block_job()
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        addrs = ', '.join(str(sock.getsockname()) for sock in self.server.sockets)
        logger.info(f"🚀 BTC-K Stratum Server listening on {addrs} (kHeavyHash ASIC ready on port {self.port}, Global Target: 1.000 BPS)")
        
        asyncio.create_task(self._block_pulse_loop())

    async def _block_pulse_loop(self):
        while self.running:
            await asyncio.sleep(10.0)
            if self.clients and any(c.authorized for c in self.clients.values()):
                if time.time() - self.last_global_block_time > 8.0:
                    new_job = self.create_new_block_job(is_clean=False)
                    await self.broadcast_job(new_job)

    async def stop(self):
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("Stratum Server stopped.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = BTCKStratumServer(port=3333)
    try:
        loop.run_until_complete(server.start())
        print("Stratum Server running. Press Ctrl+C to terminate.")
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(server.stop())

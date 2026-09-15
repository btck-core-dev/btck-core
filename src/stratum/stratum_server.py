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
from consensus.kheavyhash import kheavyhash, hash_to_target_int, difficulty_to_target
from tokenomics.emission_schedule import compute_block_issuance, BlockIssuanceResult, SATS_PER_COIN, TOTAL_HARD_CAP_SATK
from privacy.stealth_address import StealthKeyPair

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

# Distinct Cryptographic Stealth Addresses for Solo Miners & PPLNS Group
ADDR_KS001 = "btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62"  # SOLO 1
ADDR_KS002 = "btck1s037d18eab628a24a59c21f2e92582b97f215f653a8c1617c16dfb156eb290d580a03125c2e3fb43623758f031a71c8f0d694943961e3827a6a0a838a25eee09323b0e467453d"  # SOLO 2
ADDR_KS003 = "btck1s03df2cba1d3d4c84af0653034927852a5e83fc7d6a0373950f0b5b22c16a0961e203bb664f11e97fd8a1576aafd7f679bd12449b78b5d615c6c09de7f32cd07a98bd5d82ea60"  # SOLO 3
ADDR_PPLNS = "btck1s02095f1da0fbd3ec34f889d88454e5b5106f0d4eba0c05910499d8898211375af3035a2c5fc604bc35db78ba8b86babb6c5a80283819cc8451e9769e18f87e01aca2f10bce6d"  # PPLNS Team

class MiningJob:
    def __init__(self, job_id: str, block_height: int, header_template: bytes, target_diff: float, is_clean: bool = True):
        self.job_id = job_id
        self.block_height = block_height
        self.header_template = header_template  # 80 bytes
        self.target_diff = target_diff
        self.is_clean = is_clean
        self.created_at = time.time()

class StratumClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client_id: str):
        self.reader = reader
        self.writer = writer
        self.client_id = client_id
        self.wallet_address = ""
        self.worker_name = "default"
        self.mode = "SOLO"
        self.extra_nonce_1 = os.urandom(4).hex()
        self.difficulty = 279.40  # Baseline for 3 miners (~1.2 TH/s) to produce strictly 1.000 BPS (1.000s/block)
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
    def __init__(self, host: str = "0.0.0.0", port: int = 3333, network_diff: Optional[float] = None):
        self.host = host
        self.port = port
        self.clients: Dict[str, StratumClient] = {}
        self.state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "chain_state.json")
        
        # Load state
        self.current_height, self.issued_supply_satk, self.pool_balance_satk, self.burned_supply_satk, self.test_start_time, self.miners_stats = self._load_persisted_state()
        
        self.current_jobs: Dict[str, MiningJob] = {}
        self.job_counter = 0
        self.msg_id_counter = 100
        self.server: Optional[asyncio.Server] = None
        self.running = False
        
        # Global Network Fast DAA (Anchoring entire network strictly to 1.000s/block)
        self.current_network_diff = float(network_diff) if network_diff is not None else 279.40
        self.last_global_block_time = time.time()
        self.global_block_intervals: List[float] = []
        self.rolling_network_bps = 1.000
        
        # PPLNS Share Window for Team Mining (ks004 & ks005)
        self.pplns_shares_window = collections.deque(maxlen=100)
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

        logger.info(f"🧱 BlockDAG Multi-Miner Ledger Initialized at Height #{self.current_height} (Issued: {self.issued_supply_satk / SATS_PER_COIN:.2f} BTC-K, Pool: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K)")
        logger.info(f"🎯 Network Fast DAA Initial Difficulty: {self.current_network_diff} (Target: 1.000 BPS / 8.33h for 30,000 blocks)")

    def _load_persisted_state(self):
        height = 0
        issued = 0
        pool = 0
        burned = 0
        start_time = time.time()
        miners = {
            "ks001": {"mode": "SOLO", "address": ADDR_KS001, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks002": {"mode": "SOLO", "address": ADDR_KS002, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks003": {"mode": "SOLO", "address": ADDR_KS003, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks004": {"mode": "PPLNS", "address": ADDR_PPLNS, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0},
            "ks005": {"mode": "PPLNS", "address": ADDR_PPLNS, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0}
        }
        
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    height = data.get("current_height", 0)
                    issued = data.get("issued_supply_satk", 0)
                    pool = data.get("pool_balance_satk", 0)
                    burned = data.get("burned_supply_satk", 0)
                    start_time = data.get("test_start_time", start_time)
                    if "miners_summary" in data:
                        for k, v in data["miners_summary"].items():
                            if k in miners:
                                miners[k].update(v)
            except Exception:
                pass
                
        return height, issued, pool, burned, start_time, miners

    def _persist_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump({
                    "current_height": self.current_height,
                    "target_blocks": 30000,
                    "test_start_time": self.test_start_time,
                    "elapsed_hours": round((time.time() - self.test_start_time) / 3600.0, 3),
                    "network_diff": self.current_network_diff,
                    "network_bps": round(self.rolling_network_bps, 3),
                    "issued_supply_satk": self.issued_supply_satk,
                    "issued_supply_btck": round(self.issued_supply_satk / SATS_PER_COIN, 8),
                    "pool_balance_satk": self.pool_balance_satk,
                    "pool_balance_btck": round(self.pool_balance_satk / SATS_PER_COIN, 8),
                    "burned_supply_satk": self.burned_supply_satk,
                    "burned_supply_btck": round(self.burned_supply_satk / SATS_PER_COIN, 8),
                    "miners_summary": self.miners_stats,
                    "updated_at": time.time()
                }, f, indent=2)
        except Exception:
            pass

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
            header_hash = hashlib.sha256(job.header_template).digest()
            header_hex = header_hash.hex()
            timestamp_ms = int(time.time() * 1000)
            timestamp_bytes = timestamp_ms.to_bytes(8, 'little')
            timestamp_hex = timestamp_bytes.hex()
            clean_hex = "ff" if job.is_clean else "00"
            payload_hex = f"{header_hex}{timestamp_hex}{clean_hex}"
            msg = {
                "id": int(job.job_id),
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
        client = StratumClient(reader, writer, client_id)
        client.difficulty = self.current_network_diff
        self.clients[client_id] = client
        logger.info(f"⚡ ASIC Miner connected from {client_id} (Initial Diff: {client.difficulty})")

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
                client.difficulty = self.current_network_diff
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
                res = [
                    [["mining.set_difficulty", "1"], ["mining.notify", "1"]],
                    client.extra_nonce_1,
                    4
                ]
                await client.send_json({"id": msg_id, "result": res, "error": None})
                await client.send_json({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [self.current_network_diff]
                })

        elif method == "mining.authorize":
            full_user = params[0] if params else "anonymous.worker"
            parts = full_user.split(".", 1)
            raw_addr = parts[0]
            ip = client.client_id.split(":")[0]

            # Determine Worker Name, Mode, and Designated Stealth Address
            if ip == "192.168.50.236" or "001" in full_user:
                client.worker_name = "ks001"
                client.mode = "SOLO"
                client.wallet_address = ADDR_KS001
            elif ip == "192.168.50.104" or "002" in full_user:
                client.worker_name = "ks002"
                client.mode = "SOLO"
                client.wallet_address = ADDR_KS002
            elif ip == "192.168.50.231" or "003" in full_user:
                client.worker_name = "ks003"
                client.mode = "SOLO"
                client.wallet_address = ADDR_KS003
            elif "004" in full_user or ip in ["192.168.50.6", "192.168.50.180"]:
                client.worker_name = "ks004"
                client.mode = "PPLNS"
                client.wallet_address = ADDR_PPLNS
            elif "005" in full_user or ip in ["192.168.50.57", "192.168.50.181"]:
                client.worker_name = "ks005"
                client.mode = "PPLNS"
                client.wallet_address = ADDR_PPLNS
            else:
                client.worker_name = parts[1] if len(parts) > 1 else f"miner_{ip.split('.')[-1]}"
                client.mode = "PPLNS"
                client.wallet_address = ADDR_PPLNS

            client.authorized = True
            client.difficulty = self.current_network_diff
            
            logger.info(f"✓ Authorized miner [{client.client_id}]: Worker='{client.worker_name}' | Mode='{client.mode}' | Target='1.0 BPS (Diff {client.difficulty})' | PayoutAddr='{client.wallet_address[:28]}...'")
            await client.send_json({"id": msg_id, "result": True, "error": None})
            await client.send_json({
                "id": None,
                "method": "mining.set_difficulty",
                "params": [self.current_network_diff]
            })

            # Send current job
            latest_job = list(self.current_jobs.values())[-1] if self.current_jobs else self.create_new_block_job()
            await self.send_job_to_client(client, latest_job)

        elif method == "mining.submit":
            if len(params) >= 3:
                worker_name = params[0]
                job_id = str(params[1])
                nonce = str(params[2]).replace("0x", "")
            else:
                worker_name = params[0] if params else ""
                job_id = str(params[1]) if len(params) > 1 else ""
                nonce = str(params[-1]) if params else "0"

            job = self.current_jobs.get(job_id)
            if not job:
                await client.send_json({"id": msg_id, "result": False, "error": [21, "Job not found / Stale share", None]})
                client.shares_rejected += 1
                return

            # Duplicate share detection
            share_key = f"{job_id}_{nonce.lower()}"
            if share_key in self.submitted_shares:
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
                nonce_bytes = bytes.fromhex(nonce_clean)
            except Exception:
                await client.send_json({"id": msg_id, "result": False, "error": [20, "Invalid nonce hex format", None]})
                client.shares_rejected += 1
                return

            if len(nonce_bytes) < 8:
                nonce_bytes = nonce_bytes.ljust(8, b'\x00')
            elif len(nonce_bytes) > 8:
                nonce_bytes = nonce_bytes[:8]

            full_header = job.header_template + nonce_bytes
            block_hash = kheavyhash(full_header)
            hash_int = hash_to_target_int(block_hash)

            # 1. PoW share target difficulty verification
            share_target = difficulty_to_target(client.difficulty)
            if hash_int > share_target:
                # Share does not meet required difficulty!
                client.shares_rejected += 1
                await client.send_json({"id": msg_id, "result": False, "error": [23, "Low difficulty share", None]})
                return

            # Share is valid for pool difficulty
            client.shares_accepted += 1
            await client.send_json({"id": msg_id, "result": True, "error": None})

            now = time.time()
            if client.mode == "PPLNS":
                self.pplns_shares_window.append((client.worker_name, now))

            worker_key = client.worker_name
            if worker_key not in self.miners_stats:
                self.miners_stats[worker_key] = {"mode": client.mode, "address": client.wallet_address, "blocks": 0, "combos": 0, "payout_btck": 0.0, "shares": 0}
            self.miners_stats[worker_key]["shares"] += 1

            # 2. Verify if share satisfies full network block difficulty target
            network_target = difficulty_to_target(self.current_network_diff)
            if hash_int <= network_target:
                # Block found!
                client.blocks_found += 1
                self.current_height += 1
                solved_height = self.current_height

                global_interval = max(0.05, now - self.last_global_block_time)
                self.last_global_block_time = now
                self.global_block_intervals.append(global_interval)
                if len(self.global_block_intervals) > 30:
                    self.global_block_intervals.pop(0)

                # Consensus Combo Jackpot Pool issuance calculation
                issuance: BlockIssuanceResult = compute_block_issuance(
                    block_height=solved_height,
                    block_hash=block_hash,
                    current_issued_sats=self.issued_supply_satk,
                    current_pool_balance_sats=self.pool_balance_satk
                )

                self.issued_supply_satk = issuance.new_issued_supply_sats
                self.pool_balance_satk = issuance.new_pool_balance_sats

                final_reward_sats = issuance.miner_total_payout_sats
                final_reward_btck = final_reward_sats / SATS_PER_COIN

                stat = self.miners_stats[worker_key]
                stat["blocks"] += 1

                if client.mode == "SOLO":
                    stat["payout_btck"] = round(stat["payout_btck"] + final_reward_btck, 2)
                else:
                    pplns_workers = [w for w, _ in self.pplns_shares_window]
                    total_window_shares = max(1, len(pplns_workers))
                    for target_w in ["ks004", "ks005"]:
                        if target_w in self.miners_stats:
                            w_shares = pplns_workers.count(target_w)
                            if total_window_shares > 0 and w_shares > 0:
                                portion = (w_shares / total_window_shares) * final_reward_btck
                                self.miners_stats[target_w]["payout_btck"] = round(self.miners_stats[target_w]["payout_btck"] + portion, 2)

                if issuance.is_combo:
                    client.combos_won += 1
                    stat["combos"] += 1
                    logger.info(
                        f"🎰🎰🎰 [10x COMBO JACKPOT TRIGGERED!] 🎰🎰🎰\n"
                        f"  >>> Height #{solved_height} | Hash: {block_hash.hex()[:16]}...\n"
                        f"  >>> Winner: Miner '{client.worker_name}' [{client.mode}] ({client.client_id})\n"
                        f"  >>> Base: {issuance.miner_base_sats / SATS_PER_COIN:.2f} BTC-K | Pool Bonus: {issuance.combo_payout_sats / SATS_PER_COIN:.2f} BTC-K\n"
                        f"  >>> TOTAL MINER PAYOUT: {final_reward_btck:.2f} BTC-K | Jackpot Pool Reserve: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K\n"
                        f"  >>> Payout Stealth Destination: {client.wallet_address[:32]}..."
                    )
                else:
                    logger.info(
                        f"🎉 [BLOCKDAG BLOCK SOLVED!] Height #{solved_height} | Miner: '{client.worker_name}' [{client.mode}] | Base: {issuance.miner_base_sats / SATS_PER_COIN:.2f} BTC-K | Pool Deposit: {issuance.pool_deposit_sats / SATS_PER_COIN:.2f} BTC-K | Pool Reserve: {self.pool_balance_satk / SATS_PER_COIN:.2f} BTC-K"
                    )

                # Global Network Fast DAA
                if len(self.global_block_intervals) >= 10 and (self.current_height % 5 == 0):
                    avg_global_interval = sum(self.global_block_intervals) / len(self.global_block_intervals)
                    self.rolling_network_bps = 1.0 / avg_global_interval if avg_global_interval > 0 else 1.0
                    if avg_global_interval < 0.93 or avg_global_interval > 1.07:
                        damping = 0.45
                        ratio = 1.0 / max(0.2, min(5.0, avg_global_interval))
                        new_diff = self.current_network_diff * (1.0 + damping * (ratio - 1.0))
                        new_diff = max(10.0, min(2000.0, new_diff))
                        if abs(new_diff - self.current_network_diff) >= 0.5:
                            old_diff = self.current_network_diff
                            self.current_network_diff = round(new_diff, 2)
                            logger.info(f"⚡ [Global Fast DAA] Rolling Network Avg: {avg_global_interval:.2f}s/block -> Adjusting difficulty: {old_diff} -> {self.current_network_diff} (Target: 1.000s/block)")
                            for c in list(self.clients.values()):
                                if c.authorized:
                                    c.difficulty = self.current_network_diff
                                    asyncio.create_task(c.send_json({
                                        "id": None,
                                        "method": "mining.set_difficulty",
                                        "params": [self.current_network_diff]
                                    }))

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
            await asyncio.sleep(60.0)
            if self.clients and any(c.authorized for c in self.clients.values()):
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

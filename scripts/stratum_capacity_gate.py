#!/usr/bin/env python3
"""Run the BTC-K public-pool 100-client capacity gate on isolated SIMNET.

The harness never connects to, modifies, or reuses a DEVNET/MAINNET database.
Each client completes subscribe, authorize, job receipt and one accepted Share.
PPLNS writes are checked directly for durability and every settled block is
checked for SatK conservation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import resource
import sqlite3
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from consensus.pow import MAX_UINT256
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from privacy.wallet_engine import StealthWallet
from stratum.stratum_server import BTCKStratumServer


class CapacityNodeRPC:
    """Delegate node RPC while making accidental full blocks vanishingly rare.

    SIMNET normally has an intentionally trivial network target.  Using that
    target would turn a connection/accounting benchmark into 100 synchronous
    block validations.  The production MAINNET starting bits preserve real
    server-side Share verification cost without charging the pool for work a
    miner performs outside the server.
    """

    def __init__(self, delegate):
        self.delegate = delegate
        self.block_submissions = 0

    async def call(self, method: str, params: dict | None = None):
        result = await self.delegate.call(method, params)
        if method == "getBlockTemplate":
            result = dict(result)
            result["target_bits"] = 0x1C017668
        elif method == "submitBlock":
            self.block_submissions += 1
        return result


@contextmanager
def temporary_environment(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def read_until(reader: asyncio.StreamReader, predicate, timeout: float) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Stratum response deadline exceeded")
        line = await asyncio.wait_for(reader.readline(), remaining)
        if not line:
            raise ConnectionError("Stratum connection closed before expected response")
        message = json.loads(line.decode("utf-8"))
        if predicate(message):
            return message


async def exercise_client(
    host: str,
    port: int,
    payout_address: str,
    index: int,
    timeout: float,
    submit_share: bool,
    share_interval: float,
) -> tuple[float, asyncio.StreamWriter, bool]:
    started = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout,
    )
    worker = f"load{index:03d}"
    writer.write(
        (json.dumps({
            "id": 1,
            "method": "mining.subscribe",
            "params": ["btck-capacity-gate"],
        }) + "\n").encode("utf-8")
    )
    writer.write(
        (json.dumps({
            "id": 2,
            "method": "mining.authorize",
            "params": [f"{payout_address}.{worker}", "x"],
        }) + "\n").encode("utf-8")
    )
    await writer.drain()
    authorization = await read_until(reader, lambda item: item.get("id") == 2, timeout)
    if authorization.get("result") is not True:
        raise RuntimeError(f"authorization rejected: {authorization}")
    notification = await read_until(
        reader,
        lambda item: item.get("method") == "mining.notify",
        timeout,
    )
    handshake_latency = time.perf_counter() - started
    if not submit_share:
        return handshake_latency, writer, False
    job_id = notification["params"][0]
    await asyncio.sleep(index * share_interval)
    writer.write(
        (json.dumps({
            "id": 3,
            "method": "mining.submit",
            "params": [worker, job_id, f"{index:x}"],
        }) + "\n").encode("utf-8")
    )
    await writer.drain()
    submission = await read_until(reader, lambda item: item.get("id") == 3, timeout)
    if submission.get("result") is not True:
        raise RuntimeError(f"Share rejected: {submission}")
    return handshake_latency, writer, True


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def inspect_accounting(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as connection:
        shares = int(connection.execute("SELECT COUNT(*) FROM shares").fetchone()[0])
        workers = int(connection.execute(
            "SELECT COUNT(DISTINCT worker_name) FROM shares"
        ).fetchone()[0])
        blocks = connection.execute(
            """
            SELECT reward_satk, pool_fee_satk, finder_bonus_satk, distributed_satk
            FROM blocks
            """
        ).fetchall()
    conservation_errors = sum(
        1
        for reward, fee, finder, distributed in blocks
        # distributed_satk already includes the finder bonus.  The dedicated
        # finder column is an audit breakdown, not an additional debit.
        if int(reward) != int(fee) + int(distributed)
    )
    return {
        "durable_shares": shares,
        "durable_workers": workers,
        "settled_blocks": len(blocks),
        "settlement_conservation_errors": conservation_errors,
    }


async def run_gate(
    clients: int,
    shares: int,
    share_interval: float,
    timeout: float,
    cpu_limit_percent: float,
) -> dict:
    logging.getLogger().setLevel(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="btck-capacity-") as temporary:
        root = Path(temporary)
        db_path = root / "pplns.sqlite3"
        payout_address = StealthWallet.generate("capacity-gate").address
        environment = {
            "BTCK_DATA_ROOT": str(root / "chain-data"),
            "BTCK_POOL_MODE": "pplns",
            "BTCK_POOL_TREASURY_ADDRESS": payout_address,
            "BTCK_POOL_FEE_ADDRESS": payout_address,
            "BTCK_PPLNS_DB_PATH": str(db_path),
            "BTCK_STRATUM_STATUS_PORT": "0",
            "BTCK_STRATUM_TEMPLATE_REFRESH_SECONDS": "60",
        }
        with temporary_environment(environment):
            daemon = BTCKDaemon(host="127.0.0.1", network="simnet")
            capacity_rpc = CapacityNodeRPC(InProcessNodeRPCClient(daemon))
            server = BTCKStratumServer(
                host="127.0.0.1",
                port=0,
                network="simnet",
                node_rpc=capacity_rpc,
                share_target=MAX_UINT256,
                template_refresh_seconds=60.0,
                max_clients=max(256, clients + 16),
            )
            await server.start()
            port = server.server.sockets[0].getsockname()[1]
            writers: list[asyncio.StreamWriter] = []
            failures: list[str] = []
            handshake_latencies: list[float] = []
            submitted = 0
            usage_start = resource.getrusage(resource.RUSAGE_SELF)
            wall_start = time.perf_counter()
            try:
                results = await asyncio.gather(
                    *(
                        exercise_client(
                            "127.0.0.1",
                            port,
                            payout_address,
                            index,
                            timeout,
                            index < shares,
                            share_interval,
                        )
                        for index in range(clients)
                    ),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        failures.append(f"{type(result).__name__}: {result}")
                    else:
                        latency, writer, did_submit = result
                        handshake_latencies.append(latency)
                        writers.append(writer)
                        submitted += int(did_submit)
                # Share responses precede block submission/settlement by design.
                # Give all connection handlers a bounded interval to finish.
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    accounting = inspect_accounting(db_path)
                    if accounting["durable_shares"] >= submitted:
                        break
                    await asyncio.sleep(0.05)
                if server.pplns_engine and submitted:
                    # Exercise the exact production settlement transaction after
                    # all concurrent Share writes have committed.  This avoids
                    # relying on SIMNET's deliberately easy full-block target.
                    for height in range(1, 6):
                        await asyncio.to_thread(
                            server.pplns_engine.process_block,
                            block_hash=f"{height:064x}",
                            height=height,
                            reward_satk=100_000_000,
                            pool_fee_rate=server.get_pool_fee_rate(),
                            finder_wallet=payout_address,
                            finder_worker=f"load{height - 1:03d}",
                        )
                accounting = inspect_accounting(db_path)
            finally:
                for writer in writers:
                    writer.close()
                await asyncio.gather(
                    *(writer.wait_closed() for writer in writers),
                    return_exceptions=True,
                )
                await server.stop()
            wall_seconds = max(0.000001, time.perf_counter() - wall_start)
            usage_end = resource.getrusage(resource.RUSAGE_SELF)
            cpu_seconds = (
                usage_end.ru_utime + usage_end.ru_stime
                - usage_start.ru_utime - usage_start.ru_stime
            )
            logical_cpus = max(1, os.cpu_count() or 1)
            normalized_cpu_percent = cpu_seconds / wall_seconds / logical_cpus * 100.0
            counters = server._share_counters
            report = {
                "gate": "btck-stratum-100-client-v1",
                "network": "isolated-simnet",
                "requested_clients": clients,
                "successful_clients": len(handshake_latencies),
                "failed_clients": len(failures),
                "requested_shares": shares,
                "successful_share_clients": submitted,
                "failure_samples": failures[:5],
                "accepted_shares": int(counters["accepted"]),
                "rejected_shares": int(counters["rejected"]),
                "rejected_by_reason": dict(counters["rejected_by_reason"]),
                "unexpected_node_block_submissions": capacity_rpc.block_submissions,
                "wall_seconds": round(wall_seconds, 4),
                "handshake_median_ms": round(statistics.median(handshake_latencies) * 1000, 2) if handshake_latencies else 0.0,
                "handshake_p95_ms": round(percentile(handshake_latencies, 0.95) * 1000, 2),
                "process_cpu_seconds": round(cpu_seconds, 4),
                "normalized_cpu_percent": round(normalized_cpu_percent, 2),
                "cpu_limit_percent": cpu_limit_percent,
                **accounting,
            }
            report["passed"] = all((
                report["successful_clients"] == clients,
                report["failed_clients"] == 0,
                report["handshake_p95_ms"] < timeout * 1000.0,
                report["successful_share_clients"] == shares,
                report["accepted_shares"] == shares,
                report["rejected_shares"] == 0,
                report["durable_shares"] == shares,
                report["durable_workers"] == shares,
                report["settled_blocks"] >= 1,
                report["settlement_conservation_errors"] == 0,
                report["unexpected_node_block_submissions"] == 0,
                report["normalized_cpu_percent"] < cpu_limit_percent,
            ))
            return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=100)
    parser.add_argument(
        "--shares",
        type=int,
        default=10,
        help="Shares submitted while all clients stay connected; VarDiff limits real aggregate rate",
    )
    parser.add_argument("--share-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--cpu-limit-percent", type=float, default=70.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.clients <= 256:
        parser.error("--clients must be between 1 and 256")
    if not 1 <= args.shares <= args.clients:
        parser.error("--shares must be between 1 and --clients")
    if args.share_interval < 0:
        parser.error("--share-interval must not be negative")
    report = asyncio.run(run_gate(
        args.clients,
        args.shares,
        args.share_interval,
        args.timeout,
        args.cpu_limit_percent,
    ))
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

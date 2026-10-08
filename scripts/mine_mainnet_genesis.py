#!/usr/bin/env python3
"""Serve one immutable MAINNET genesis job to Kaspa-family ASICs.

This tool never starts a node, edits the checked-in manifest, or reads a chain
database.  It converts a locked MAINNET manifest plus an explicit UTC timestamp
and genesis bits into the same kHeavyHash job used by consensus.  A successful
nonce is written as a small review artifact which can later be copied into the
manifest through the normal release review.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from consensus.genesis import build_pre_pow_hash, calculate_block_pow
from consensus.kheavyhash import generate_matrix, hash_to_target_int
from consensus.pow import compact_to_target, validate_compact_target


DEFAULT_MANIFEST = PROJECT_ROOT / "params" / "networks" / "mainnet.json"
KASPA_MAX_TARGET = (1 << 224) - 1


@dataclass(frozen=True)
class GenesisJob:
    chain_id: str
    version: int
    timestamp: int
    bits: int
    headline: str
    coinbase_output: tuple[bytes, bytes]
    pre_pow_hash: bytes

    @property
    def timestamp_ms(self) -> int:
        return self.timestamp * 1000

    @property
    def kaspa_payload(self) -> str:
        ids = [
            int.from_bytes(self.pre_pow_hash[offset : offset + 8], "big")
            for offset in range(0, 32, 8)
        ]
        ids.append(int.from_bytes(self.timestamp_ms.to_bytes(8, "big"), "little"))
        return "".join(f"{value:016x}" for value in ids)


def parse_bits(value: str | int) -> int:
    return int(str(value), 0)


def load_job(manifest_path: Path, timestamp: int, bits: int | None) -> GenesisJob:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("network") != "mainnet":
        raise ValueError("genesis miner only accepts a MAINNET manifest")
    if raw.get("release", {}).get("approved") is not False:
        raise ValueError("manifest must remain release-locked while searching genesis")

    consensus = raw["consensus"]
    genesis = raw["genesis"]
    version = int(genesis["version"])
    if version != int(consensus["block_version"]):
        raise ValueError("genesis version must match the frozen block header version")
    selected_bits = parse_bits(bits if bits is not None else consensus["daa"]["initial_bits"])
    pow_limit_bits = parse_bits(consensus["pow_limit_bits"])
    validate_compact_target(selected_bits, pow_limit_bits)
    if timestamp <= 0:
        raise ValueError("timestamp must be a positive whole UTC second")

    outputs = tuple(bytes.fromhex(value) for value in genesis["coinbase_output"])
    if len(outputs) != 2 or any(len(value) != 33 for value in outputs):
        raise ValueError("genesis coinbase output must contain two compressed points")
    headline = str(genesis["headline"])
    payload_hash = hashlib.sha256(headline.encode("utf-8")).digest()
    pre_pow_hash = build_pre_pow_hash(
        version=version,
        chain_id=str(raw["chain_id"]),
        parents=[],
        timestamp=float(timestamp),
        bits=selected_bits,
        coinbase_output=(outputs[0], outputs[1]),
        payload_hash=payload_hash,
    )
    return GenesisJob(
        chain_id=str(raw["chain_id"]),
        version=version,
        timestamp=timestamp,
        bits=selected_bits,
        headline=headline,
        coinbase_output=(outputs[0], outputs[1]),
        pre_pow_hash=pre_pow_hash,
    )


def parse_nonce(raw_nonce: object) -> int:
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


def result_artifact(job: GenesisJob, nonce: int, pow_hash: bytes) -> dict:
    return {
        "format": "btck-mainnet-genesis-result-v1",
        "network": "mainnet",
        "chain_id": job.chain_id,
        "version": job.version,
        "timestamp": job.timestamp,
        "bits": f"0x{job.bits:08x}",
        "nonce": f"0x{nonce:x}",
        "expected_hash": pow_hash.hex(),
        "headline": job.headline,
        "headline_sha256": hashlib.sha256(job.headline.encode("utf-8")).hexdigest(),
        "pre_pow_hash": job.pre_pow_hash.hex(),
        "coinbase_output": [value.hex() for value in job.coinbase_output],
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FixedGenesisStratum:
    def __init__(self, job: GenesisJob, result_path: Path, difficulty: float = 128.0):
        self.job = job
        self.result_path = result_path
        self.difficulty = difficulty
        self.matrix = generate_matrix(job.pre_pow_hash)
        self.network_target = compact_to_target(job.bits)
        self.share_target = int(KASPA_MAX_TARGET / difficulty)
        self.found = asyncio.Event()
        self.client_counter = 0

    async def send(self, writer: asyncio.StreamWriter, payload: dict) -> None:
        writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.client_counter += 1
        client_num = self.client_counter
        peer = writer.get_extra_info("peername")
        authorized = False
        print(f"miner connected: {peer}", flush=True)
        try:
            while not self.found.is_set():
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                    message_id = message.get("id")
                    method = message.get("method")
                    params = message.get("params", [])
                    if method == "mining.subscribe":
                        await self.send(writer, {"id": message_id, "result": [True, "EthereumStratum/1.0.0"], "error": None})
                        await self.send(writer, {"id": None, "method": "mining.set_difficulty", "params": [self.difficulty]})
                    elif method in ("mining.extranonce.subscribe", "mining.configure"):
                        await self.send(writer, {"id": message_id, "result": True, "error": None})
                    elif method == "mining.authorize":
                        authorized = True
                        await self.send(writer, {"id": message_id, "result": True, "error": None})
                        await self.send(writer, {"id": None, "method": "set_extranonce", "params": [f"{client_num % 65536:04x}"]})
                        await self.send(writer, {"id": None, "method": "mining.set_difficulty", "params": [self.difficulty]})
                        await self.send(writer, {"id": 1, "method": "mining.notify", "params": ["genesis", self.job.kaspa_payload]})
                    elif method == "mining.submit":
                        if not authorized or len(params) < 3:
                            await self.send(writer, {"id": message_id, "result": False, "error": [24, "Unauthorized or malformed submit", None]})
                            continue
                        if str(params[1]) != "genesis":
                            await self.send(writer, {"id": message_id, "result": False, "error": [21, "Unknown genesis job", None]})
                            continue
                        raw_nonce = params[4] if len(params) >= 5 else params[2]
                        nonce = parse_nonce(raw_nonce)
                        pow_hash = calculate_block_pow(
                            version=self.job.version,
                            chain_id=self.job.chain_id,
                            parents=[],
                            timestamp=float(self.job.timestamp),
                            bits=self.job.bits,
                            nonce=nonce,
                            coinbase_output=self.job.coinbase_output,
                            payload_hash=hashlib.sha256(self.job.headline.encode("utf-8")).digest(),
                            pre_pow_hash=self.job.pre_pow_hash,
                            matrix=self.matrix,
                        )
                        value = hash_to_target_int(pow_hash)
                        if value > self.share_target:
                            await self.send(writer, {"id": message_id, "result": False, "error": [23, "Low difficulty share", None]})
                            continue
                        await self.send(writer, {"id": message_id, "result": True, "error": None})
                        if value <= self.network_target and not self.found.is_set():
                            artifact = result_artifact(self.job, nonce, pow_hash)
                            atomic_write_json(self.result_path, artifact)
                            print(json.dumps(artifact, indent=2), flush=True)
                            self.found.set()
                    else:
                        await self.send(writer, {"id": message_id, "result": None, "error": [20, "Unsupported method", None]})
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    await self.send(writer, {"id": None, "result": False, "error": [20, str(exc), None]})
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        server = await asyncio.start_server(self.handle, host, port, limit=65_536)
        print(f"fixed genesis Stratum listening on {host}:{port}", flush=True)
        print(f"pre_pow_hash={self.job.pre_pow_hash.hex()}", flush=True)
        print(f"kaspa_payload={self.job.kaspa_payload}", flush=True)
        async with server:
            await self.found.wait()
            server.close()
            await server.wait_closed()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--timestamp", required=True, type=int, help="Frozen genesis UTC Unix timestamp")
    parser.add_argument("--bits", help="Genesis compact target; defaults to manifest DAA initial_bits")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3340)
    parser.add_argument("--difficulty", type=float, default=128.0)
    parser.add_argument("--result", type=Path, default=Path("mainnet-genesis-result.json"))
    parser.add_argument("--serve", action="store_true", help="Serve the fixed job; otherwise print a dry-run description")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bits = parse_bits(args.bits) if args.bits else None
    job = load_job(args.manifest.resolve(), args.timestamp, bits)
    description = {
        "network": "mainnet",
        "chain_id": job.chain_id,
        "version": job.version,
        "timestamp": job.timestamp,
        "bits": f"0x{job.bits:08x}",
        "headline": job.headline,
        "pre_pow_hash": job.pre_pow_hash.hex(),
        "kaspa_payload": job.kaspa_payload,
        "result_path": str(args.result.resolve()),
        "manifest_will_be_modified": False,
    }
    print(json.dumps(description, indent=2), flush=True)
    if args.serve:
        if not 0 < args.difficulty:
            raise SystemExit("difficulty must be positive")
        asyncio.run(FixedGenesisStratum(job, args.result.resolve(), args.difficulty).serve(args.host, args.port))


if __name__ == "__main__":
    main()

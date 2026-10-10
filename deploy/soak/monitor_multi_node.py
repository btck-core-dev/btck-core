#!/usr/bin/env python3
"""Collect and machine-grade BTC-K multi-node soak evidence.

This tool never changes node state. It polls each node's read-only ``/info``
endpoint, appends fsync'd JSONL evidence, and writes an atomic rolling report.
Seven-day PASS requires continuous identity/accounting/safety checks, bounded
node lag, forward progress on every node, and matching checkpoint roots when
nodes report the same checkpoint sequence.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import ssl
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_DURATION_SECONDS = 7 * 24 * 60 * 60
ZERO_HASH = "0" * 64


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def is_hex_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def fetch_info(url: str, timeout: float, allow_http: bool = False) -> dict:
    if not url.startswith("https://") and not (allow_http and url.startswith("http://")):
        raise ValueError("Node URL must use HTTPS (or explicitly allow HTTP for isolated DEVNET tests)")
    request = Request(url.rstrip("/") + "/info", headers={"User-Agent": "btck-soak-monitor/1"})
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read {url}/info: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url}/info did not return an object")
    return payload


def normalize_node(name: str, url: str, info: dict) -> dict:
    network = info.get("network", {})
    dag = info.get("dag", {})
    safeguards = info.get("safeguards", {})
    return {
        "name": name,
        "url": url,
        "identity": {
            "network": network.get("name"),
            "chain_id": network.get("chain_id"),
            "genesis_hash": network.get("genesis_hash"),
            "consensus_fingerprint": network.get("consensus_fingerprint"),
        },
        "blue_score": int(dag.get("max_blue_score", -1)),
        "total_blocks": int(dag.get("total_blocks", -1)),
        "tips_count": int(dag.get("tips_count", -1)),
        "selected_tip": dag.get("selected_tip"),
        "issued_supply_sats": int(dag.get("issued_supply_sats", -1)),
        "utxo_total_sats": int(dag.get("utxo_total_sats", -2)),
        "checkpoint_sequence": int(dag.get("state_root_checkpoint_sequence", 0) or 0),
        "checkpoint_root": str(dag.get("state_root", "")),
        "safe_mode": bool(safeguards.get("safe_mode", True)),
        "connected_peers": int(info.get("p2p", {}).get("connected_peers", 0)),
    }


def evaluate_sample(nodes: list[dict], max_blue_lag: int, max_tips: int) -> list[str]:
    failures: list[str] = []
    if len(nodes) < 2:
        return ["at least two nodes are required"]
    identities = {json.dumps(node["identity"], sort_keys=True) for node in nodes}
    if len(identities) != 1:
        failures.append("network identity mismatch")
    scores = [node["blue_score"] for node in nodes]
    if min(scores) < 0 or max(scores) - min(scores) > max_blue_lag:
        failures.append(f"blue score lag exceeds {max_blue_lag}")
    for node in nodes:
        prefix = node["name"]
        identity = node["identity"]
        if not identity.get("network") or not identity.get("chain_id"):
            failures.append(f"{prefix}: network identity incomplete")
        if not is_hex_digest(identity.get("genesis_hash")):
            failures.append(f"{prefix}: genesis hash invalid")
        if not is_hex_digest(identity.get("consensus_fingerprint")):
            failures.append(f"{prefix}: consensus fingerprint invalid")
        if node["safe_mode"]:
            failures.append(f"{prefix}: safe mode active or missing")
        if node["issued_supply_sats"] < 0 or node["issued_supply_sats"] != node["utxo_total_sats"]:
            failures.append(f"{prefix}: asset conservation failed")
        if not 1 <= node["tips_count"] <= max_tips:
            failures.append(f"{prefix}: tips outside 1..{max_tips}")
        if not node["selected_tip"]:
            failures.append(f"{prefix}: selected tip missing")
        if node["connected_peers"] < 1:
            failures.append(f"{prefix}: no connected P2P peer")
        if node["checkpoint_sequence"] <= 0 or not is_hex_digest(node["checkpoint_root"]):
            failures.append(f"{prefix}: durable checkpoint missing")
    roots_by_sequence: dict[int, set[str]] = {}
    for node in nodes:
        roots_by_sequence.setdefault(node["checkpoint_sequence"], set()).add(node["checkpoint_root"])
    for sequence, roots in roots_by_sequence.items():
        peers_at_sequence = [node for node in nodes if node["checkpoint_sequence"] == sequence]
        if len(peers_at_sequence) > 1 and len(roots) != 1:
            failures.append(f"checkpoint root mismatch at sequence {sequence}")
    return failures


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def seal_evidence_record(payload: dict, previous_hash: str) -> dict:
    if not is_hex_digest(previous_hash):
        raise ValueError("Invalid previous evidence hash")
    record = dict(payload)
    record.pop("record_hash", None)
    record["previous_hash"] = previous_hash
    record["record_hash"] = hashlib.sha256(canonical_json(record)).hexdigest()
    return record


def append_evidence(path: Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_samples(path)
    previous_hash = existing[-1]["record_hash"] if existing else ZERO_HASH
    record = seal_evidence_record(payload, previous_hash)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record).decode("utf-8") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def load_samples(path: Path) -> list[dict]:
    if not path.exists():
        return []
    samples = []
    expected_previous = ZERO_HASH
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt evidence JSONL at line {number}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Invalid evidence record at line {number}")
        record_hash = item.get("record_hash")
        previous_hash = item.get("previous_hash")
        if previous_hash != expected_previous:
            raise ValueError(f"Broken evidence hash chain at line {number}")
        unsigned = dict(item)
        unsigned.pop("record_hash", None)
        calculated = hashlib.sha256(canonical_json(unsigned)).hexdigest()
        if not isinstance(record_hash, str) or not hmac.compare_digest(record_hash, calculated):
            raise ValueError(f"Evidence record hash mismatch at line {number}")
        expected_previous = record_hash
        samples.append(item)
    return samples


def summarize(samples: list[dict], duration_seconds: int, max_gap_seconds: int) -> dict:
    if not samples:
        return {"verdict": "INCOMPLETE", "reasons": ["no samples"], "sample_count": 0}
    timestamps = [float(item["timestamp"]) for item in samples]
    elapsed = max(timestamps) - min(timestamps)
    reasons = []
    failed_samples = [item for item in samples if item.get("failures")]
    if failed_samples:
        reasons.append(f"{len(failed_samples)} samples failed machine gates")
    if elapsed < duration_seconds:
        reasons.append(f"elapsed {elapsed:.0f}s is below required {duration_seconds}s")
    gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
    if any(gap <= 0 for gap in gaps):
        reasons.append("sample timestamps are not strictly increasing")
    if gaps and max(gaps) > max_gap_seconds:
        reasons.append(f"maximum sample gap {max(gaps):.0f}s exceeds {max_gap_seconds}s")
    first_nodes = {node["name"]: node for node in samples[0].get("nodes", [])}
    last_nodes = {node["name"]: node for node in samples[-1].get("nodes", [])}
    baseline_topology = {
        name: {"url": node.get("url"), "identity": node.get("identity")}
        for name, node in first_nodes.items()
    }
    for index, sample in enumerate(samples[1:], 2):
        topology = {
            node["name"]: {"url": node.get("url"), "identity": node.get("identity")}
            for node in sample.get("nodes", [])
        }
        if topology != baseline_topology:
            reasons.append(f"node topology or identity changed at sample {index}")
            break
    for name in sorted(set(first_nodes) | set(last_nodes)):
        if name not in first_nodes or name not in last_nodes:
            reasons.append(f"{name}: missing first or last sample")
        elif last_nodes[name]["blue_score"] <= first_nodes[name]["blue_score"]:
            reasons.append(f"{name}: no forward blue-score progress")
    return {
        "verdict": "PASS" if not reasons else "INCOMPLETE" if elapsed < duration_seconds else "FAIL",
        "reasons": reasons,
        "sample_count": len(samples),
        "failed_sample_count": len(failed_samples),
        "started_at": min(timestamps),
        "ended_at": max(timestamps),
        "elapsed_seconds": elapsed,
        "max_sample_gap_seconds": max(gaps) if gaps else 0,
        "nodes": sorted(last_nodes),
    }


def parse_nodes(values: list[str]) -> list[tuple[str, str]]:
    nodes = []
    for value in values:
        if "=" not in value:
            raise ValueError("--node must use NAME=URL")
        name, url = value.split("=", 1)
        if not name or not url or any(existing == name for existing, _ in nodes):
            raise ValueError("Node names and URLs must be non-empty and names unique")
        nodes.append((name, url.rstrip("/")))
    if len(nodes) < 2:
        raise ValueError("At least two --node arguments are required")
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", action="append", required=True, help="NAME=https://host")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--max-blue-lag", type=int, default=600)
    parser.add_argument("--max-tips", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-http", action="store_true", help="isolated DEVNET testing only")
    args = parser.parse_args()
    nodes = parse_nodes(args.node)
    if args.interval < 10 or args.duration <= 0:
        raise SystemExit("interval must be >=10s and duration must be positive")

    while True:
        started = time.time()
        normalized = []
        fetch_failures = []
        for name, url in nodes:
            try:
                normalized.append(normalize_node(name, url, fetch_info(url, args.timeout, args.allow_http)))
            except Exception as exc:
                fetch_failures.append(f"{name}: {type(exc).__name__}: {exc}")
        failures = fetch_failures + evaluate_sample(normalized, args.max_blue_lag, args.max_tips)
        sample = {"timestamp": started, "nodes": normalized, "failures": failures}
        append_evidence(args.evidence, sample)
        samples = load_samples(args.evidence)
        report = summarize(samples, args.duration, args.interval * 2)
        report["latest_failures"] = failures
        atomic_write(args.report, report)
        print(json.dumps(report, sort_keys=True))
        if args.once:
            break
        time.sleep(max(0.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    main()

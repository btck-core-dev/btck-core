#!/usr/bin/env python3
"""Off-site HTTPS probe with durable evidence and optional webhook alerting."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def send_alert(webhook: str, payload: dict) -> None:
    request = Request(
        webhook,
        data=json.dumps(payload, sort_keys=True).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"alert webhook returned HTTP {response.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--expect", action="append", default=[])
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--webhook", default=os.getenv("BTCK_ALERT_WEBHOOK_URL", ""))
    args = parser.parse_args()
    if any(not url.startswith("https://") for url in args.url):
        raise SystemExit("public probes require HTTPS URLs")
    expected_by_url = {url: [] for url in args.url}
    for rule in args.expect:
        try:
            url, marker = rule.rsplit("=", 1)
        except ValueError as exc:
            raise SystemExit("--expect must be URL=MARKER") from exc
        if url not in expected_by_url or not marker:
            raise SystemExit("--expect URL must exactly match a probed URL")
        expected_by_url[url].append(marker)
    results = []
    failures = []
    for url in args.url:
        started = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": "btck-external-health/1"})
            with urlopen(request, timeout=args.timeout) as response:
                body = response.read(1_000_000).decode("utf-8", errors="replace")
                status = int(response.status)
            missing = [value for value in expected_by_url[url] if value not in body]
            if status != 200 or missing:
                raise RuntimeError(f"HTTP {status}; missing markers={missing}")
            results.append({
                "url": url,
                "status": status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "verification": "PASS",
            })
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            results.append({"url": url, "verification": "FAIL", "error": str(exc)})
    evidence = {
        "format": "btck-external-public-probe-v1",
        "checked_at": int(time.time()),
        "verification": "PASS" if not failures else "FAIL",
        "results": results,
    }
    atomic_json(args.evidence, evidence)
    if failures and args.webhook:
        send_alert(args.webhook, {
            "service": "BTC-K public endpoints",
            "status": "FAIL",
            "failures": failures,
            "checked_at": evidence["checked_at"],
        })
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if failures:
        raise SystemExit("external public probe failed")


if __name__ == "__main__":
    main()

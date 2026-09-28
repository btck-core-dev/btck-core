#!/usr/bin/env python3
"""Fail-closed preflight for the no-real-value DEVNET ignition path."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config.network import MainnetReleaseError, load_network


def fail(message: str) -> None:
    raise SystemExit(f"PREFLIGHT NO-GO: {message}")


def main() -> None:
    selected = os.getenv("BTCK_NETWORK", "devnet")
    if selected != "devnet":
        fail("this preflight only approves the no-real-value devnet")
    network = load_network(selected)
    if not network.release_approved or network.release_approval_id != "local-development-only":
        fail("devnet manifest is not explicitly marked local-development-only")
    required_features = {"stratum": True, "ai_payments": False, "web_wallet_send": False, "dex": False, "bridge": False, "exchange_adapter": False}
    for feature, expected in required_features.items():
        if network.feature_enabled(feature) is not expected:
            fail(f"unsafe feature policy mismatch: {feature}")
    try:
        load_network("mainnet")
    except MainnetReleaseError:
        pass
    else:
        fail("mainnet release lock is not active")

    token = os.getenv("BTCK_RPC_TOKEN", "")
    if len(token) < 32:
        fail("BTCK_RPC_TOKEN must contain at least 32 characters")

    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in ("secrets", "data", "docs_internal_confidential", "*wallet*.json"):
        if required not in dockerignore.splitlines():
            fail(f".dockerignore does not exclude {required}")

    tracked = subprocess.run(
        ["git", "ls-files", "secrets", "*.key", "*.pem", "*wallet*.json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    forbidden_tracked = [path for path in tracked if path not in {"web/wallet.html"}]
    if forbidden_tracked:
        fail("tracked secret/wallet artifacts: " + ", ".join(forbidden_tracked))

    decimal_secret = re.compile(r'(?i)(spendKey|viewKey|private[^\n]{0,30}key)[^\n]{0,80}["=:]\s*["\']?[0-9]{60,}')
    for relative in ("web/wallet.html", "web/wallet_treasury.html", "web/app.js"):
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        if decimal_secret.search(text):
            fail(f"private scalar appears in {relative}")
        if "转账广播成功" in text or "Stealth Transfer Broadcasted" in text:
            fail(f"fake broadcast success remains in {relative}")

    print("PREFLIGHT GO: isolated no-real-value DEVNET code path")
    print(f"chain_id={network.chain_id}")
    print(f"genesis={network.genesis.expected_hash}")
    print("mainnet=LOCKED; unsafe application features=DISABLED")


if __name__ == "__main__":
    main()

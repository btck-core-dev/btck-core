#!/usr/bin/env python3
"""Fail-closed checks for the BTC-K MAINNET genesis-search handoff.

This does not approve MAINNET, alter its manifest, touch chain data, or start
services.  It verifies that the locked candidate is internally consistent and
that known public-beta misrepresentations are absent before genesis search.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "params/networks/mainnet.json"
PUBLIC_WALLETS = ROOT / "params/mainnet_wallets_public.json"
sys.path.insert(0, str(ROOT / "src"))


def fail(message: str) -> None:
    raise SystemExit(f"MAINNET PREFLIGHT NO-GO: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wallets = json.loads(PUBLIC_WALLETS.read_text(encoding="utf-8"))
    consensus = manifest["consensus"]
    genesis = manifest["genesis"]

    require(manifest.get("network") == "mainnet", "wrong network manifest")
    require(manifest.get("chain_id") == "btck-mainnet-v1", "unexpected chain_id")
    require(manifest.get("p2p_magic") == "4254434d", "unexpected P2P magic")
    require(manifest.get("release", {}).get("approved") is False, "candidate must remain locked before genesis search")
    require(not manifest.get("release", {}).get("approval_id"), "approval id must remain empty before review")
    require(consensus.get("block_version") == 2, "unexpected block header version")
    require(consensus.get("pow_limit_bits") == "0x1c176680", "PoW safety floor changed")
    require(consensus.get("daa", {}).get("initial_bits") == "0x1c017668", "DAA initial bits changed")
    require(consensus.get("daa", {}).get("target_interval_ms") == 1000, "target cadence is not 1 second")
    require(genesis.get("bits") == int("0x1c017668", 0), "genesis bits differ from calibrated initial bits")
    require(genesis.get("timestamp") == 1.0, "candidate timestamp was changed outside the genesis ceremony")
    require(genesis.get("nonce") == "0x0", "candidate nonce was changed outside the genesis ceremony")
    require(genesis.get("expected_hash") == "0" * 64, "candidate hash was changed outside the genesis ceremony")
    require(genesis.get("coinbase_output") == wallets.get("genesis_coinbase_output"), "genesis public output mismatch")
    require(consensus.get("eco_fund_output") == wallets.get("eco_fund_output"), "ecosystem public output mismatch")
    require(all(value is False for value in manifest.get("features", {}).values()), "unfinished MAINNET feature enabled")

    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    server = (ROOT / "src/web_explorer_server.py").read_text(encoding="utf-8")
    stratum = (ROOT / "src/stratum/stratum_server.py").read_text(encoding="utf-8")
    daemon = (ROOT / "src/node/daemon.py").read_text(encoding="utf-8")
    from stratum.stratum_server import MAINNET_POOL_TREASURY_ADDRESS
    for stale in ("0% 创世", "5台 KS0", "ks001", "get.btck.network/worker.sh", "discord.gg/btck-network"):
        require(stale not in index, f"stale or unverified public claim remains: {stale}")
    require("public beta" in server and "410" in server, "unaudited browser wallet is not fail-closed")
    require("/api/search" in server and "/api/blocks" in server, "minimal explorer query API is missing")
    require(wallets["pool_treasury_address"] == MAINNET_POOL_TREASURY_ADDRESS, "MAINNET pool treasury address is not frozen in Stratum")
    require("MAINNET requires an explicit pool treasury address" in stratum, "MAINNET pool address is not fail-closed")
    require("MAINNET operator alerts are disabled" in daemon, "MAINNET still exposes the signed operator alert path")
    require("consensus_guardian.checkpoints.add_checkpoint" not in daemon, "runtime checkpoint injection remains enabled")

    if (ROOT / ".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files", "secrets", "*.key", "*.pem", "*privkey*", "*mnemonic*"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        require(not tracked, "tracked secret material: " + ", ".join(tracked))
    else:
        require(not (ROOT / "secrets").exists(), "exported source contains a secrets directory")
        ignored_roots = {"build", "dist", ".venv", "node_modules", "__pycache__"}
        forbidden = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(part in ignored_roots for part in path.relative_to(ROOT).parts)
            and (path.suffix.lower() in {".key", ".pem"} or "privkey" in path.name.lower() or "mnemonic" in path.name.lower())
        ]
        require(not forbidden, "exported source contains secret-like files: " + ", ".join(forbidden))

    print("MAINNET PREFLIGHT GO: ready for a controlled genesis search ceremony")
    print("mainnet=LOCKED; genesis timestamp/nonce/hash=PENDING")
    print("browser wallet/DEX/GPU ecosystem=EXCLUDED FROM PUBLIC BETA")
    print("next=freeze source commit and UTC, then mine the immutable genesis job")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the inert MAINNET-LIVE website bundle without deploying it.

The checked-in ``web`` tree must remain fail-closed during prelaunch. This
builder copies that tree and applies the small, auditable state transition that
is allowed only after ``public_launch_gate.py`` returns PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


RELEASE_BASE = "https://github.com/btck-core-dev/btck-core/releases/download/v3.2.5"
RELEASE_PAGE = "https://github.com/btck-core-dev/btck-core/releases/tag/v3.2.5"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match in {path} but found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def replace_all(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"expected {expected} matches in {path} but found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("web"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    shutil.copytree(args.source, args.output)

    network_path = args.output / "network.json"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    if network["availability"]["public_mining"] or network["availability"]["public_node_release"]:
        raise SystemExit("source website is not in fail-closed prelaunch state")
    if network["release"]["downloads_enabled"]:
        raise SystemExit("source website already enables downloads")
    network["updated_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    network["network"]["status"] = "mainnet_live"
    network["release"]["downloads_enabled"] = True
    network["release"]["release_url"] = RELEASE_PAGE
    network["availability"]["public_mining"] = True
    network["pool_modes"] = {
        "solo": {"ports": [3333, 3334, 3335, 3336], "status": "operating"},
        "pplns": {"ports": [3332], "automatic_payouts_enabled": False,
                  "status": "production_payment_acceptance_incomplete"},
        "access_guide": "/mining.html",
    }
    network["availability"]["public_node_release"] = True
    network_path.write_text(json.dumps(network, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    releases = args.output / "releases.html"
    replace_once(
        releases,
        "<div class=\"notice\"><b>CHECKPOINT-BOUND RELEASE VERIFIED — DOWNLOADS NOT YET OPEN.</b> Version 3.2.5 has reproducible wallet builds and a valid detached release signature. Public downloads remain disabled until Seed-B Fresh IBD, recovery-RTO, external-probe and final public-launch gates pass.</div>",
        f"<div class=\"notice\"><b>MAINNET LIVE — SIGNED v3.2.5 DOWNLOADS OPEN.</b> Download only from the <a href=\"{RELEASE_PAGE}\">official GitHub Release</a>, verify SHA-256 and verify the detached release signature before installation.</div>",
    )
    for artifact in (
        "BTC-K-Wallet-3.2.5-macOS-arm64.tar.gz",
        "btck-node-3.2.5.tar.gz",
        "btck-pool-stack-3.2.5.tar.gz",
        "mainnet.json",
    ):
        replace_once(
            releases,
            f"<th>{artifact}</th>",
            f"<th><a href=\"{RELEASE_BASE}/{artifact}\">{artifact}</a></th>",
        )
    replace_once(
        releases,
        "BITCOIN K · CHECKPOINT-BOUND RELEASE · DOWNLOADS OPEN ONLY AFTER FINAL GATE PASS",
        "BITCOIN K · CHECKPOINT-BOUND v3.2.5 · SIGNED DOWNLOADS OPEN",
    )

    genesis = args.output / "genesis.html"
    replace_once(
        genesis,
        "<div class=\"notice\">CHECKPOINT VERIFIED — PUBLIC LAUNCH PENDING. The signed release exists, but downloads and public mining remain closed until the second public seed completes Fresh IBD and the recovery, external-probe and final launch gates pass.</div>",
        f"<div class=\"notice\"><b>MAINNET LIVE.</b> The checkpoint-bound signed release is public at <a href=\"{RELEASE_PAGE}\">GitHub v3.2.5</a>. Verify the identifiers on this page before connecting a node, wallet or miner.</div>",
    )

    participate = args.output / "participate.html"
    replace_once(
        participate,
        "Checkpoint #271,828 and the signed release are complete; L1 public access opens only after the second-seed and final launch gates pass.",
        "Checkpoint #271,828 and the signed v3.2.5 release are complete; L1 public node access and mining are open.",
    )
    replace_once(
        participate,
        "<span class=\"badge\">CHECKPOINT-BOUND PRELAUNCH — PUBLIC MINING CLOSED</span>",
        "<span class=\"badge\">MAINNET LIVE — PUBLIC MINING OPEN</span>",
    )
    replace_once(
        participate,
        "When public mining is marked LIVE, add <code>stratum+tcp://pool.bitcoin-k.com:3333</code> as the pool URL.",
        "Choose SOLO ports 3333-3336 by initial difficulty tier. PPLNS port 3332 has automatic payouts disabled. See the <a href=\"/mining.html\">complete endpoints and payout status</a> before connecting.",
    )
    replace_once(
        participate,
        "<div class=\"notice\">Port 3333 is not yet announced for public mining. This badge changes to LIVE only after Seed-B Fresh IBD, recovery RTO, external probing and the final launch gate pass.</div>",
        "<div class=\"notice\">SOLO is operating on ports 3333-3336. PPLNS 3332: automatic payouts disabled; production payment acceptance incomplete. Direct-IP and domain endpoints, hashrate tiers and fees: <a href=\"/mining.html\">mining access</a>.</div>",
    )
    replace_once(
        participate,
        "<span class=\"badge\">SIGNED PACKAGE VERIFIED — DOWNLOAD PENDING</span>",
        "<span class=\"badge\">SIGNED v3.2.5 PACKAGE AVAILABLE</span>",
    )
    replace_once(
        participate,
        "The signed checkpoint-bound package exists, but downloads remain closed until the final public launch gate passes.",
        f"The signed checkpoint-bound package is available from the <a href=\"{RELEASE_PAGE}\">official GitHub Release</a>.",
    )

    home = args.output / "home.html"
    replace_once(home, "checkpoint-bound prelaunch", "mainnet live")
    replace_once(
        home,
        "The checkpoint is locked and the signed release is ready. Public downloads and mining remain closed until the second-seed and final launch gates pass.",
        "The checkpoint is locked, the signed release is public, and verified node and mining access are open.",
    )
    replace_once(
        home,
        '<article class="mile"><span class="status">FINAL GATE</span><h3>Public release</h3><p>Complete second-seed Fresh IBD, recovery RTO and external probing before downloads and mining open.</p></article>',
        '<article class="mile done"><span class="status">● LIVE</span><h3>Public release</h3><p>Two public seeds, Fresh IBD, recovery RTO, external probing and signed downloads.</p></article>',
    )
    replace_once(home, "Checkpoint-bound prelaunch · Experimental software", "Mainnet live · Experimental software")
    replace_once(
        home,
        "The L1 ledger and mining pool are live in the guardian phase.",
        "The public L1 ledger and official mining pool are live.",
    )
    replace_once(home, "L1 · READY AT PUBLIC RELEASE", "L1 · MAINNET LIVE")
    replace_once(
        home,
        'Pool: <span class="mono">pool.bitcoin-k.com:3333</span>',
        'PPLNS: <span class="mono">3332 — payouts disabled</span><br>SOLO: <span class="mono">3333-3336</span><br><a href="/mining.html">Endpoints and hashrate guide</a>',
    )

    pool_page = args.output / "index.html"
    pool_text = pool_page.read_text(encoding="utf-8")
    if "pool.bitcoin-k.com:3333" not in pool_text:
        raise SystemExit("pool page lacks the expected prelaunch Stratum endpoint")
    pool_text = pool_text.replace("pool.bitcoin-k.com:3333", "pool.bitcoin-k.com:3332")
    pool_text = pool_text.replace("Stratum Port 3333", "PPLNS Port 3332")
    pool_text = pool_text.replace(
        "SOLO &amp; PPLNS Supported",
        "PPLNS 3332 · SOLO 3333-3336",
    ).replace(
        "SOLO & PPLNS Supported",
        "PPLNS 3332 · SOLO 3333-3336",
    )
    pool_notice = ('<aside style="padding:1rem;border:1px solid #d4a72c;margin:1rem">'
                   '<strong>PPLNS automatic payouts are disabled.</strong> '
                   'Production payment acceptance is incomplete. SOLO uses ports 3333-3336; '
                   'PPLNS uses 3332. <a href="/mining.html">Full endpoints, hashrate tiers and payment status</a>.</aside>')
    pool_text = pool_text.replace('<main class="container">', '<main class="container">' + pool_notice, 1)
    pool_page.write_text(pool_text, encoding="utf-8")

    whitepaper = args.output / "whitepaper.html"
    replace_once(
        whitepaper,
        "STATUS — L1 mainnet is operating in a disclosed guardian phase through checkpoint #271,828.",
        "STATUS — L1 mainnet completed its disclosed Guardian phase at checkpoint #271,828 and is now publicly accessible.",
    )
    replace_all(whitepaper, "Guardian mainnet live", "Public mainnet live", expected=2)
    replace_once(whitepaper, "Access pending checkpoint", "Public access live")
    replace_once(whitepaper, "Guardian release only", "Signed v3.2.5 release")

    explorer = args.output / "explorer.html"
    replace_once(
        explorer,
        "Consensus data comes directly from the private guardian node.",
        "Consensus data comes directly from the public mainnet node.",
    )

    llms = args.output / "llms.txt"
    replace_once(
        llms,
        "- The checkpoint-bound v3.2.5 release is signed and reproducible; public downloads and mining remain closed until the second-seed and final launch gates pass.",
        f"- The checkpoint-bound v3.2.5 release is signed, reproducible and public: {RELEASE_PAGE}",
    )

    forbidden = (
        "DOWNLOADS NOT YET OPEN",
        "PUBLIC LAUNCH PENDING",
        "PUBLIC MINING CLOSED",
        "DOWNLOAD PENDING",
        "checkpoint-bound prelaunch",
    )
    for path in args.output.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".txt", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                raise SystemExit(f"live bundle retains prelaunch marker {marker!r}: {path}")

    files = {
        str(path.relative_to(args.output)): sha256(path)
        for path in sorted(args.output.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "format": "btck-public-live-site-bundle-v1",
        "release": "v3.2.5",
        "release_url": RELEASE_PAGE,
        "generated_at": network["updated_utc"],
        "deployment_authorized": False,
        "required_gate": "scripts/public_launch_gate.py PASS",
        "files": files,
    }
    (args.output.parent / "LIVE_SITE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "files": len(files), "status": "STAGED_NOT_DEPLOYED"}, indent=2))


if __name__ == "__main__":
    main()

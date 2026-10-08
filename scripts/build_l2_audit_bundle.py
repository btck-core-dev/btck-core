#!/usr/bin/env python3
"""Build a deterministic, secret-free BTC-K L2 external-audit source bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXACT_FILES = {
    "docs/45_BTCK_L2_CHAIN_SETTLEMENT_AND_GPU_HARDENING_2026-10-09.md",
    "docs/46_BTCK_L2_PRODUCTION_CEREMONY_AND_AUDIT_HANDOFF_2026-10-09.md",
    "src/gateway/ai_rpc_gateway.py",
    "src/gateway/l2_chain_bridge.py",
    "src/node/daemon.py",
    "src/node/rpc_client.py",
    "src/privacy/stealth_address.py",
    "src/privacy/wallet_engine.py",
    "src/tokenomics/emission_schedule.py",
    "src/tokenomics/provable_burn.py",
    "src/worker/pricing_engine.py",
}
TEST_PREFIXES = (
    "tests/test_l2_",
    "tests/test_x402_",
    "tests/test_gpu_symbiotic_",
    "tests/test_stealth_wallet_transfer.py",
    "tests/test_node_transaction_rpc.py",
    "tests/test_bip340_official_vectors.py",
)
FORBIDDEN_PARTS = {"secrets", "data", "logs", "backups", "docs_internal_confidential"}


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes, mode: int = 0o644) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = len(payload)
    entry.mode = mode
    entry.mtime = 0
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = "root"
    archive.addfile(entry, io.BytesIO(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "l2-audit")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    if git("status", "--porcelain") and not args.allow_dirty:
        raise SystemExit("refusing to build an audit bundle from a dirty worktree")
    commit = git("rev-parse", "HEAD")
    tracked = [item for item in git("ls-files").splitlines() if item]
    selected = sorted(
        path for path in tracked
        if path in EXACT_FILES or any(path.startswith(prefix) for prefix in TEST_PREFIXES)
    )
    missing = sorted(EXACT_FILES - set(selected))
    if missing:
        raise SystemExit("audit scope is missing required files: " + ", ".join(missing))
    for relative in selected:
        path = Path(relative)
        if FORBIDDEN_PARTS.intersection(path.parts) or "wallet.json" in path.name or "privkey" in path.name:
            raise SystemExit(f"refusing to package sensitive path: {relative}")

    files = {relative: (ROOT / relative).read_bytes() for relative in selected}
    manifest = {
        "format": "btck-l2-external-audit-v1",
        "status": "PRODUCTION-DISABLED-PENDING-INDEPENDENT-AUDIT",
        "source_commit": commit,
        "files": {relative: digest(payload) for relative, payload in files.items()},
        "excluded": "wallets, private keys, runtime ledgers, chain data, logs and backups",
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    prefix = f"btck-l2-audit-{commit[:12]}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{prefix}.tar.gz"
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                add_bytes(archive, f"{prefix}/AUDIT_MANIFEST.json", manifest_bytes)
                for relative, payload in files.items():
                    mode = 0o755 if (ROOT / relative).stat().st_mode & 0o111 else 0o644
                    add_bytes(archive, f"{prefix}/{relative}", payload, mode)
    checksum = digest(output.read_bytes())
    checksum_file = output.with_suffix(output.suffix + ".sha256")
    checksum_file.write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(json.dumps({
        "archive": str(output),
        "sha256": checksum,
        "source_commit": commit,
        "file_count": len(files),
    }, indent=2))


if __name__ == "__main__":
    main()

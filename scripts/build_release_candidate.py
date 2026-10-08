#!/usr/bin/env python3
"""Build reproducible BTC-K pre-genesis node/pool release bundles and metadata."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.2.0-rc2"

NODE_PREFIXES = (
    "src/config/", "src/consensus/", "src/node/", "src/p2p/", "src/privacy/", "src/tokenomics/",
    "src/stratum/", "web/", "params/",
)
NODE_FILES = {
    "Dockerfile", "README.md", "requirements.txt", "docker-compose.yml", "bin/btck-cli",
    "src/web_explorer_server.py",
    "deploy/btckd.service", "deploy/install_seed_node.sh", "deploy/verify_chain_replay.py",
    "scripts/backup_genesis_state.sh", "scripts/mainnet_preflight.py",
    "scripts/mainnet_genesis_rehearsal.py", "scripts/mine_mainnet_genesis.py", "scripts/start_node.sh",
}
POOL_PREFIXES = NODE_PREFIXES + ("src/stratum/", "web/", "deploy/public-edge/")
POOL_FILES = NODE_FILES | {
    "src/web_explorer_server.py", "scripts/start_stratum_pool.sh",
}


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def tracked_files() -> list[str]:
    return [item for item in run("git", "ls-files").splitlines() if item]


def select(files: list[str], exact: set[str], prefixes: tuple[str, ...]) -> list[str]:
    return sorted(path for path in files if path in exact or path.startswith(prefixes))


def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    archive.addfile(info, io.BytesIO(payload))


def build_archive(output: Path, prefix: str, files: list[str], install_text: str) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                add_bytes(archive, f"{prefix}/INSTALL.md", install_text.encode("utf-8"))
                for relative in files:
                    source = ROOT / relative
                    data = source.read_bytes()
                    mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                    add_bytes(archive, f"{prefix}/{relative}", data, mode)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "release-candidate")
    parser.add_argument("--wallet-artifact", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    status = run("git", "status", "--porcelain")
    if status and not args.allow_dirty:
        raise SystemExit("refusing to build from a dirty worktree")
    commit = run("git", "rev-parse", "HEAD")
    tag = run("git", "describe", "--tags", "--exact-match", "HEAD")
    if tag != "mainnet-pre-genesis-rc2":
        raise SystemExit("HEAD must carry the mainnet-pre-genesis-rc2 tag")
    wallet_source = args.wallet_artifact.resolve()
    if not wallet_source.is_file():
        raise SystemExit(f"wallet artifact not found: {wallet_source}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files = tracked_files()
    node_files = select(files, NODE_FILES, NODE_PREFIXES)
    pool_files = select(files, POOL_FILES, POOL_PREFIXES)
    preflight_runtime = {
        "scripts/mainnet_preflight.py", "src/web_explorer_server.py",
        "src/stratum/stratum_server.py", "src/node/daemon.py", "web/index.html",
    }
    missing = sorted(preflight_runtime - set(node_files))
    if missing:
        raise SystemExit("node bundle cannot run MAINNET preflight; missing: " + ", ".join(missing))
    node_name = f"btck-node-{VERSION}"
    pool_name = f"btck-pool-stack-{VERSION}"
    node_archive = output_dir / f"{node_name}.tar.gz"
    pool_archive = output_dir / f"{pool_name}.tar.gz"
    build_archive(node_archive, node_name, node_files, """# BTC-K node pre-genesis candidate

This is a source release for Python 3.10+ or Docker Compose. It is deliberately
unable to start formal MAINNET while the checked-in manifest remains locked.
Run `python3 scripts/mainnet_preflight.py`, create a 32+ character RPC token,
then review `docker compose config` before any start. Do not expose RPC publicly.
""")
    build_archive(pool_archive, pool_name, pool_files, """# BTC-K pool/explorer pre-genesis candidate

This source bundle includes the node dependency, Stratum, Explorer and the
opt-in TLS edge. Copy `deploy/public-edge/.env.public.example` outside the
bundle, replace only the placeholder host/certificate paths, validate it, and
review `docker compose config`. Formal MAINNET remains release-locked.
""")
    wallet_target = output_dir / wallet_source.name
    shutil.copy2(wallet_source, wallet_target)

    artifacts = [node_archive, pool_archive, wallet_target]
    checksums = {path.name: sha256(path) for path in artifacts}
    (output_dir / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())), encoding="utf-8"
    )
    formal_manifest = ROOT / "params" / "networks" / "mainnet.json"
    known = """# Known limitations — BTC-K 3.2.0-rc2 pre-genesis candidate

- This is not the final public MAINNET release. The formal MAINNET manifest is still locked.
- The wallet intentionally rejects MAINNET until the final genesis hash and consensus fingerprint are embedded.
- The macOS wallet is not Apple Developer ID signed or notarized.
- No independent third-party cryptography/consensus audit has been completed.
- Domain names, DNS and production TLS certificates are placeholders/pending.
- Node/pool bundles are source/Docker packages, not signed native Linux binaries.
- After formal genesis, all artifacts, hashes, SBOM and signatures must be rebuilt from the final tag.
"""
    (output_dir / "KNOWN_LIMITATIONS.md").write_text(known, encoding="utf-8")

    packages = []
    relationships = []
    for index, path in enumerate(artifacts, 1):
        spdx_id = f"SPDXRef-Package-{index}"
        packages.append({
            "SPDXID": spdx_id, "name": path.name, "versionInfo": VERSION,
            "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION", "licenseDeclared": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": checksums[path.name]}],
        })
        relationships.append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": spdx_id})
    sbom = {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"BTC-K-{VERSION}-pre-genesis", "documentNamespace": f"https://btck.invalid/spdx/{commit}",
        "creationInfo": {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "creators": ["Tool: btck-build-release-candidate"]},
        "packages": packages, "relationships": relationships,
    }
    (output_dir / "SBOM.spdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "format": "btck-pre-genesis-release-v1", "status": "PRE-GENESIS-NOT-FOR-PUBLIC-MAINNET",
        "version": VERSION, "tag": tag, "source_commit": commit,
        "formal_mainnet_manifest_sha256": sha256(formal_manifest), "artifacts": checksums,
        "required_finalization": ["freeze genesis UTC", "mine formal nonce", "approve formal manifest", "embed wallet MAINNET identity", "rebuild and sign"],
    }
    (output_dir / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

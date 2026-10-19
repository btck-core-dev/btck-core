#!/usr/bin/env python3
"""Build a self-contained Linux x86_64 wallet using native Linux Python."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="3.2.13")
    parser.add_argument("--build-root", type=Path, default=ROOT / "build" / "wallet-linux")
    parser.add_argument("--release-dir", type=Path, default=ROOT / "dist" / "wallet-linux")
    args = parser.parse_args()
    if not sys.platform.startswith("linux") or platform.machine().lower() != "x86_64":
        parser.error("Build on Linux x86_64 with Python 3.10+; cross-compilation is unsupported")
    if importlib.metadata.version("pyinstaller") != "6.16.0":
        parser.error("Install the pinned build dependencies from requirements-linux-build.txt")
    if not all(c.isalnum() or c in ".-" for c in args.version):
        parser.error("Invalid package version")
    import tkinter
    probe = tkinter.Tk()
    probe.withdraw()
    probe.destroy()
    build = args.build_root.resolve()
    release = args.release_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    release.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "src"))
    from config.network import load_network
    network = load_network("mainnet", ROOT / "params" / "networks" / "mainnet.json")
    identity = dict(version=args.version, name=network.name, chain_id=network.chain_id,
                    genesis_hash=network.genesis.expected_hash,
                    consensus_fingerprint=network.consensus_fingerprint,
                    release_approval_id=network.release_approval_id)
    identity_file = build / "network_identity.json"
    identity_file.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Each run owns a fresh staging directory. Never delete a user-selected path.
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=build))
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx",
              "--paths", str(ROOT / "src"), "--paths", str(ROOT / "tools"),
              "--add-data", f"{identity_file}:.", "--specpath", str(stage),
              "--distpath", str(stage / "dist"), "--workpath", str(stage / "work")]
    env = dict(os.environ, PYTHONHASHSEED="0")
    subprocess.run(common + ["--onedir", "--console", "--name", "BTC-K Wallet",
                            str(ROOT / "tools" / "btck_wallet_gui.py")],
                   cwd=ROOT, env=env, check=True)
    subprocess.run(common + ["--onefile", "--console", "--name", "btck-wallet",
                            str(ROOT / "tools" / "btck_cli.py")],
                   cwd=ROOT, env=env, check=True)
    package_name = f"BTC-K-Wallet-{args.version}-Linux-x86_64"
    package = stage / package_name
    shutil.copytree(stage / "dist" / "BTC-K Wallet", package)
    shutil.copy2(stage / "dist" / "btck-wallet", package)
    shutil.copy2(identity_file, package)
    shutil.copy2(ROOT / "packaging" / "LINUX_WALLET_README.txt", package / "README.txt")
    shutil.copy2(ROOT / "docs" / "mining_pool_access.md", package / "MINING_ACCESS.md")
    shutil.copy2(ROOT / "LICENSE", package / "LICENSE.txt")
    cli = package / "btck-wallet"
    subprocess.run([str(cli), "--version"], check=True)
    with tempfile.TemporaryDirectory(prefix="btck-package-smoke-") as temporary:
        scratch = Path(temporary)
        password = scratch / "password.txt"
        password.write_text("disposable-package-smoke-only\n", encoding="utf-8")
        from privacy.private_files import restrict_private_path
        restrict_private_path(password)
        wallet = scratch / "wallet.json"
        subprocess.run([str(cli), "--password-file", str(password), "new", "--name", "smoke",
                        "--skip-immediate-recovery-check", "-o", str(wallet)],
                       stdout=subprocess.DEVNULL, check=True)
        subprocess.run([str(cli), "--password-file", str(password), "info", str(wallet)],
                       stdout=subprocess.DEVNULL, check=True)
    launcher = package / "launch-wallet.sh"
    launcher.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec "./BTC-K Wallet" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    installer = package / "install-desktop.sh"
    installer.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec "./BTC-K Wallet" --install-desktop\n', encoding="utf-8")
    installer.chmod(0o755)
    archive = release / (package_name + ".tar.gz")
    subprocess.run([sys.executable, str(ROOT / "packaging" / "reproducible_tar.py"),
                    str(package), str(archive)], check=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    evidence = dict(format="btck-linux-wallet-build-v1", artifact=archive.name,
                    artifact_sha256=digest, identity=identity, python=sys.version,
                    pyinstaller="6.16.0", glibc=platform.libc_ver(),
                    native_cli_create_unlock=True, package_directory=str(package),
                    source_commit=subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                        if (ROOT / ".git").exists() else "see-source-manifest")
    archive.with_name(archive.name + ".evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a self-contained Windows x64 wallet using native Windows Python."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="3.2.13")
    parser.add_argument("--build-root", type=Path, default=ROOT / "build" / "wallet-windows")
    parser.add_argument("--release-dir", type=Path, default=ROOT / "dist" / "wallet-windows")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        parser.error("Build on Windows x64 with 64-bit Python; cross-compilation is unsupported")
    if importlib.metadata.version("pyinstaller") != "6.16.0":
        parser.error("Install the pinned build dependencies from requirements-windows-build.txt")
    if not all(c.isalnum() or c in ".-" for c in args.version):
        parser.error("Invalid package version")
    # CPython 3.13 venvs may not discover their base Tcl library automatically.
    for key, folder in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        candidate = Path(sys.base_prefix) / "tcl" / folder
        if candidate.is_dir():
            os.environ.setdefault(key, str(candidate))
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
    subprocess.run(common + ["--onedir", "--windowed", "--name", "BTC-K Wallet",
                            str(ROOT / "tools" / "btck_wallet_gui.py")],
                   cwd=ROOT, env=env, check=True)
    subprocess.run(common + ["--onefile", "--console", "--name", "btck-wallet",
                            str(ROOT / "tools" / "btck_cli.py")],
                   cwd=ROOT, env=env, check=True)
    package_name = f"BTC-K-Wallet-{args.version}-Windows-x64"
    package = stage / package_name
    shutil.copytree(stage / "dist" / "BTC-K Wallet", package)
    shutil.copy2(stage / "dist" / "btck-wallet.exe", package)
    shutil.copy2(identity_file, package)
    shutil.copy2(ROOT / "packaging" / "WINDOWS_WALLET_README.txt", package / "README.txt")
    shutil.copy2(ROOT / "docs" / "mining_pool_access.md", package / "MINING_ACCESS.md")
    shutil.copy2(ROOT / "LICENSE", package / "LICENSE.txt")
    cli = package / "btck-wallet.exe"
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
    # ZIP order/timestamps are fixed; PE build reproducibility is not claimed.
    archive = release / (package_name + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    evidence = dict(format="btck-windows-wallet-build-v1", artifact=archive.name,
                    artifact_sha256=digest, identity=identity, python=sys.version,
                    pyinstaller="6.16.0", authenticode_signed=False,
                    native_cli_create_unlock=True, package_directory=str(package),
                    source_commit=subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                        if (ROOT / ".git").exists() else "see-source-manifest")
    archive.with_suffix(".zip.evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()

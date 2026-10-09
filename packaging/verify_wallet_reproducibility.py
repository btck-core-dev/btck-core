#!/usr/bin/env python3
"""Build the wallet twice in isolated roots and export only identical output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_once(root: Path, args) -> tuple[Path, dict]:
    build_root = root / "build"
    release_root = root / "release"
    env = dict(os.environ)
    env.update({
        "BTCK_WALLET_BUILD_ROOT": str(build_root),
        "BTCK_WALLET_RELEASE_DIR": str(release_root),
        "BTCK_WALLET_VERSION": args.version,
        "BTCK_WALLET_NETWORK_MANIFEST": str(args.manifest.resolve()),
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": args.source_date_epoch,
    })
    if args.pyinstaller:
        env["BTCK_PYINSTALLER_BIN"] = str(args.pyinstaller.resolve())
    subprocess.run([str(ROOT / "packaging/build_wallet_macos.sh")], cwd=ROOT, env=env, check=True)
    archives = list(release_root.glob("*.tar.gz"))
    if len(archives) != 1:
        raise SystemExit("wallet builder did not produce exactly one archive")
    evidence_path = archives[0].with_suffix(archives[0].suffix + ".evidence.json")
    return archives[0], json.loads(evidence_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pyinstaller", type=Path)
    parser.add_argument("--source-date-epoch", default="0")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="btck-wallet-repro-a-") as first_root:
        with tempfile.TemporaryDirectory(prefix="btck-wallet-repro-b-") as second_root:
            first, first_evidence = build_once(Path(first_root), args)
            second, second_evidence = build_once(Path(second_root), args)
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()
            if first_bytes != second_bytes:
                raise SystemExit("wallet builds are not byte-for-byte reproducible")
            digest = hashlib.sha256(first_bytes).hexdigest()
            if first_evidence != second_evidence or first_evidence.get("artifact_sha256") != digest:
                raise SystemExit("wallet build evidence differs between isolated builds")
            output = args.output_dir.resolve()
            output.mkdir(parents=True, exist_ok=True)
            target = output / first.name
            shutil.copyfile(first, target)
            evidence = {
                **first_evidence,
                "reproducible_build_verified": True,
                "isolated_builds": 2,
                "source_date_epoch": args.source_date_epoch,
            }
            evidence_target = target.with_suffix(target.suffix + ".evidence.json")
            evidence_target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            (target.with_suffix(target.suffix + ".sha256")).write_text(f"{digest}  {target.name}\n")
            print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

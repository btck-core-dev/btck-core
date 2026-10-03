#!/usr/bin/env python3
"""Generate a secret-free, content-addressed manifest of deployed runtime files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_PATTERNS = (
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    "params/**/*.json",
    "src/**/*.py",
    "web/*.html",
    "web/*.css",
    "web/*.js",
    "deploy/*.py",
    "deploy/*.sh",
    "deploy/*.service",
    "deploy/*.timer",
)


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    # Preserve porcelain status' leading column; only remove line endings.
    return result.stdout.rstrip("\r\n")


def _runtime_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in RUNTIME_PATTERNS:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    hashes: dict[str, str] = {}
    for path in _runtime_files(root):
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "schema": "btck-deployment-manifest-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": {
            "head": _git(root, "rev-parse", "HEAD"),
            "tree": _git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(status),
            "status": status.splitlines() if status else [],
        },
        "runtime_file_count": len(hashes),
        "runtime_files_sha256": hashes,
        "runtime_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(build_manifest(args.project_dir), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()

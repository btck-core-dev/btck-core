#!/usr/bin/env python3
"""Create and verify a detached OpenSSH Ed25519 signature for a release."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DOMAIN = b"BTC-K PUBLIC MAINNET RELEASE SIGNATURE V1\0"
ROOT = Path(__file__).resolve().parents[1]


def signed_payload(release_dir: Path) -> bytes:
    manifest = (release_dir / "RELEASE_MANIFEST.json").read_bytes()
    sums = (release_dir / "SHA256SUMS").read_bytes()
    return DOMAIN + hashlib.sha256(manifest).digest() + hashlib.sha256(sums).digest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    release_dir = args.release_dir.resolve()
    private_key = args.private_key.resolve()
    public_key = args.public_key.resolve()
    if ROOT == private_key or ROOT in private_key.parents:
        raise SystemExit("release signing private key must be stored outside the repository")
    if not private_key.is_file() or not public_key.is_file():
        raise SystemExit("signing key files are missing")

    payload = signed_payload(release_dir)
    with tempfile.TemporaryDirectory(prefix="btck-release-sign-") as temporary:
        payload_path = Path(temporary) / "payload.bin"
        payload_path.write_bytes(payload)
        subprocess.run([
            "ssh-keygen", "-Y", "sign", "-f", str(private_key),
            "-n", "btck-mainnet-release", str(payload_path),
        ], check=True, stdout=subprocess.DEVNULL)
        signature_path = Path(str(payload_path) + ".sig")
        signature = signature_path.read_bytes()
        allowed = Path(temporary) / "allowed_signers"
        allowed.write_text("btck-release " + public_key.read_text(encoding="utf-8").strip() + "\n")
        subprocess.run([
            "ssh-keygen", "-Y", "verify", "-f", str(allowed),
            "-I", "btck-release", "-n", "btck-mainnet-release", "-s", str(signature_path),
        ], check=True, input=payload, stdout=subprocess.DEVNULL)

    public_target = release_dir / "RELEASE_SIGNING_PUBLIC_KEY.pem"
    shutil.copyfile(public_key, public_target)
    public_bytes = public_target.read_bytes()
    envelope = {
        "format": "btck-mainnet-release-signature-v1",
        "algorithm": "OpenSSH-Ed25519-SSHSIG",
        "signed_domain": DOMAIN.rstrip(b"\0").decode(),
        "release_manifest_sha256": hashlib.sha256(
            (release_dir / "RELEASE_MANIFEST.json").read_bytes()
        ).hexdigest(),
        "sha256sums_sha256": hashlib.sha256(
            (release_dir / "SHA256SUMS").read_bytes()
        ).hexdigest(),
        "public_key_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "signature_base64": base64.b64encode(signature).decode(),
    }
    target = release_dir / "RELEASE_SIGNATURE.json"
    target.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(target, 0o644)
    print(json.dumps(envelope, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

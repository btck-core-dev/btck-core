#!/usr/bin/env python3
"""Fail closed unless release artifacts and detached signature all verify."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

DOMAIN = b"BTC-K PUBLIC MAINNET RELEASE SIGNATURE V1\0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--trusted-public-key", type=Path, required=True)
    args = parser.parse_args()
    root = args.release_dir.resolve()
    manifest_path = root / "RELEASE_MANIFEST.json"
    sums_path = root / "SHA256SUMS"
    signature_path = root / "RELEASE_SIGNATURE.json"
    public_key = root / "RELEASE_SIGNING_PUBLIC_KEY.pem"
    trusted_public_key = args.trusted_public_key.resolve()
    if not trusted_public_key.is_file():
        raise SystemExit("trusted release public key is missing")
    if public_key.read_bytes() != trusted_public_key.read_bytes():
        raise SystemExit("release public key does not match the externally trusted key")
    envelope = json.loads(signature_path.read_text(encoding="utf-8"))
    if envelope.get("format") != "btck-mainnet-release-signature-v1":
        raise SystemExit("unsupported release signature envelope")
    if envelope.get("algorithm") != "OpenSSH-Ed25519-SSHSIG":
        raise SystemExit("release signature must use OpenSSH Ed25519 SSHSIG")

    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        artifact = root / name
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"artifact checksum mismatch: {name}")
    expected = {
        "release_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "sha256sums_sha256": hashlib.sha256(sums_path.read_bytes()).hexdigest(),
        "public_key_sha256": hashlib.sha256(public_key.read_bytes()).hexdigest(),
    }
    if any(envelope.get(key) != value for key, value in expected.items()):
        raise SystemExit("release signature envelope digest mismatch")
    payload = DOMAIN + bytes.fromhex(expected["release_manifest_sha256"]) + bytes.fromhex(expected["sha256sums_sha256"])
    try:
        signature = base64.b64decode(envelope["signature_base64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise SystemExit("release signature encoding is invalid") from exc
    with tempfile.TemporaryDirectory(prefix="btck-release-verify-") as temporary:
        payload_path = Path(temporary) / "payload.bin"
        detached_path = Path(temporary) / "signature.bin"
        payload_path.write_bytes(payload)
        detached_path.write_bytes(signature)
        allowed = Path(temporary) / "allowed_signers"
        allowed.write_text("btck-release " + public_key.read_text(encoding="utf-8").strip() + "\n")
        completed = subprocess.run([
            "ssh-keygen", "-Y", "verify", "-f", str(allowed),
            "-I", "btck-release", "-n", "btck-mainnet-release", "-s", str(detached_path),
        ], input=payload, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if completed.returncode != 0:
        raise SystemExit("release signature verification failed")
    release = json.loads(manifest_path.read_text(encoding="utf-8"))
    if release.get("status") != "PUBLIC-MAINNET-CHECKPOINT-BOUND":
        raise SystemExit("release manifest is not a public MAINNET release")
    sbom = root / "SBOM.spdx.json"
    if not sbom.is_file() or release.get("sbom_sha256") != hashlib.sha256(sbom.read_bytes()).hexdigest():
        raise SystemExit("SBOM digest mismatch")
    print(json.dumps({
        "verification": "PASS",
        "version": release.get("version"),
        "source_commit": release.get("source_commit"),
        "checkpoint_hash": release.get("checkpoint_hash"),
        **expected,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

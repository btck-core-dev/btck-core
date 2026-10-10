#!/usr/bin/env python3
"""Fail-closed preflight for the optional BTC-K HTTPS edge."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Malformed env line {number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def require_static_guards() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    overlay = (ROOT / "deploy/public-edge/docker-compose.public-edge.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy/public-edge/default.conf.template").read_text(encoding="utf-8")
    checks = {
        "Explorer must bind host loopback": '"127.0.0.1:8088:8088"' in compose,
        "RPC must bind host loopback": '"127.0.0.1:16110:16110"' in compose,
        "edge must be profile-gated": 'profiles: ["public-edge"]' in overlay,
        "Explorer hostname must be required": "BTCK_PUBLIC_HOST is required" in overlay,
        "pool hostname must be required": "BTCK_POOL_PUBLIC_HOST is required" in overlay,
        "both hostnames must be allowlisted": "map $host $btck_valid_host" in nginx,
        "TLS 1.2/1.3 required": "ssl_protocols TLSv1.2 TLSv1.3;" in nginx,
        "per-IP request limit required": "limit_req zone=btck_per_ip" in nginx,
        "connection limit required": "limit_conn btck_connections" in nginx,
        "non-read methods denied": "limit_except GET HEAD { deny all; }" in nginx,
        "HSTS required": "Strict-Transport-Security" in nginx,
        "access log required": "access_log /dev/stdout btck_json;" in nginx,
    }
    failed = [label for label, result in checks.items() if not result]
    if failed:
        raise ValueError("; ".join(failed))


def validate_certificates(values: dict[str, str]) -> None:
    hosts = [values.get("BTCK_PUBLIC_HOST", ""), values.get("BTCK_POOL_PUBLIC_HOST", "")]
    if any(not HOST_RE.fullmatch(host) for host in hosts):
        raise ValueError("public hosts must be lowercase DNS hostnames")
    if len(set(hosts)) != 2:
        raise ValueError("Explorer and pool public hosts must be distinct")
    chain = Path(values.get("BTCK_TLS_FULLCHAIN", ""))
    key = Path(values.get("BTCK_TLS_PRIVKEY", ""))
    for label, path in (("full chain", chain), ("private key", key)):
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"{label} must be an existing absolute file")
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise ValueError("TLS private key must not be accessible by group or other users")
    for host in hosts:
        subprocess.run(
            ["openssl", "x509", "-in", str(chain), "-noout", "-checkend", "604800", "-checkhost", host],
            check=True,
        )
    cert_pub = subprocess.check_output(["openssl", "x509", "-in", str(chain), "-pubkey", "-noout"])
    key_pub = subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout"])
    if cert_pub != key_pub:
        raise ValueError("TLS certificate and private key do not match")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--config-only", action="store_true")
    args = parser.parse_args()
    require_static_guards()
    if not args.config_only:
        if args.env_file is None:
            raise SystemExit("--env-file is required unless --config-only is used")
        values = load_env(args.env_file)
        validate_certificates(values)
    print("BTC-K public edge preflight: PASS")


if __name__ == "__main__":
    main()

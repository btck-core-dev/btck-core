#!/usr/bin/env python3
"""Issue/renew only the isolated L2 certificate after its DNS is configured."""
from pathlib import Path
import os
import socket
import json
import sqlite3
import time
import fcntl
import subprocess

HOST = 'compute.bitcoin-k.com'
EXPECTED_IP = '47.131.226.181'
STORE = Path('/etc/btck-l2-acme')
CERTIFICATE = STORE / 'certificates' / (HOST + '.crt')
GATEWAY_CONFIG = Path('/etc/btck-l2-preview/gateway.json')
DRAIN = Path('/run/btck-l2-certificate/draining')


def active_customer_tasks():
    config = json.loads(GATEWAY_CONFIG.read_text())
    database = Path(config['data_dir']) / 'pilot.sqlite3'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        return db.execute("SELECT COUNT(*) FROM tasks WHERE status='IN_FLIGHT'").fetchone()[0]


def wait_for_idle(timeout=150, quiet=10):
    # The admission flag blocks new inference, while Worker results still flow.
    # Wait beyond the five-second upstream body deadline before taking 443.
    deadline = time.monotonic() + timeout
    quiet_since = None
    while time.monotonic() < deadline:
        now = time.monotonic()
        if active_customer_tasks():
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= quiet:
            return True
        time.sleep(1)
    return False


def issue():
    if os.geteuid() != 0:
        raise SystemExit('Run as the L2 deployment operator on its gateway server')
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise SystemExit('DNS_NOT_READY: create A compute.bitcoin-k.com -> ' + EXPECTED_IP) from None
    if resolved != {EXPECTED_IP}:
        raise SystemExit('DNS_NOT_READY: expected only the dedicated L2 IPv4 address')
    STORE.mkdir(mode=0o700, exist_ok=True)
    if CERTIFICATE.exists() and subprocess.run(['openssl', 'x509', '-checkend', '2592000', '-noout', '-in', str(CERTIFICATE)], stdout=subprocess.DEVNULL).returncode == 0:
        return
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'btck-l2-edge.service']).returncode == 0
    success = False
    stopped = False
    try:
        if was_active:
            DRAIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            DRAIN.touch(mode=0o644)
            if not wait_for_idle():
                print('L2_RENEWAL_DEFERRED: active customer tasks; edge kept online')
                return
            stopped = True
            subprocess.run(['systemctl', 'stop', 'btck-l2-edge.service'], check=True)
        command = ['lego', '--path', str(STORE), '--accept-tos', '--email', 'sakatokio@proton.me', '--domains', HOST, '--tls', '--tls.port', ':443']
        command += ['renew', '--days', '30'] if CERTIFICATE.exists() else ['run']
        subprocess.run(command, check=True, timeout=180)
        success = True
    finally:
        try:
            if stopped or success:
                subprocess.run(['systemctl', 'start', 'btck-l2-edge.service'], check=True)
        finally:
            DRAIN.unlink(missing_ok=True)
    print('L2_CERTIFICATE_READY: isolated HTTPS edge started')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as the L2 deployment operator on its gateway server')
    DRAIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    DRAIN.parent.chmod(0o755)
    with (DRAIN.parent / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('L2_CERTIFICATE_BUSY: another certificate operation is running')
            return
        issue()


if __name__ == '__main__': main()

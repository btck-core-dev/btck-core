"""Single-use, expiring invitation enrollment for development-credit workers."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from gateway.inference_pilot import PilotError


class WorkerEnrollment:
    def __init__(self, path, public_url, models, *, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parsed = urlsplit(public_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Enrollment requires an explicit HTTPS origin')
        self.url, self.models, self.clock = public_url.rstrip('/'), set(models), clock
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        if os.name == 'posix' and self.path.stat().st_mode & 0o077:
            raise ValueError('Enrollment database must be private')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS invitations(code_hash TEXT PRIMARY KEY, worker TEXT UNIQUE NOT NULL, models TEXT NOT NULL, expires INTEGER NOT NULL, nonce_hash TEXT, profile TEXT)')

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db

    def issue(self, worker, models, *, ttl=86400):
        if not isinstance(worker, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', worker):
            raise ValueError('Invalid unique Worker identity')
        if not isinstance(models, list) or not models or any(not isinstance(m, str) or m not in self.models for m in models):
            raise ValueError('Invite models must be in the configured catalog')
        if type(ttl) is not int or not 60 <= ttl <= 7 * 86400:
            raise ValueError('Invitation lifetime must be one minute to seven days')
        code = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO invitations VALUES(?,?,?,?,NULL,NULL)',
                       (hashlib.sha256(code.encode()).hexdigest(), worker, json.dumps(sorted(set(models))), int(self.clock()) + ttl))
        return {'gateway_url': self.url, 'invitation_code': code, 'worker_id': worker, 'allowed_models': sorted(set(models))}

    def redeem(self, data, fleet):
        if not isinstance(data, dict) or set(data) != {'code', 'client_nonce'}:
            raise PilotError(400, 'invalid_invitation', 'Supply an invitation and a stable client nonce')
        code, nonce = data['code'], data['client_nonce']
        if any(not isinstance(s, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{32,128}', s) for s in (code, nonce)):
            raise PilotError(400, 'invalid_invitation', 'Invalid invitation or client nonce')
        with fleet.lock:
            with self.db() as db:
                row = db.execute('SELECT * FROM invitations WHERE code_hash=?', (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
                if not row:
                    raise PilotError(403, 'invitation_unavailable', 'Invitation is invalid or expired')
                nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
                if row['profile']:
                    if not secrets.compare_digest(row['nonce_hash'], nonce_hash):
                        raise PilotError(409, 'invitation_used', 'Invitation already redeemed by another client')
                    profile = json.loads(row['profile'])
                else:
                    if row['expires'] <= self.clock():
                        raise PilotError(403, 'invitation_unavailable', 'Invitation is invalid or expired')
                    if row['worker'] in fleet.workers:
                        raise PilotError(409, 'worker_exists', 'Worker identity is already provisioned')
                    if len(fleet.workers) >= 256:
                        raise PilotError(503, 'preview_full', 'Preview worker capacity is full; retry later')
                    if not set(json.loads(row['models'])) <= fleet.models.keys():
                        raise PilotError(503, 'model_unavailable', 'Invitation models are no longer configured')
                    profile = {'gateway_url': self.url, 'worker_id': row['worker'], 'token': secrets.token_urlsafe(48),
                               'allowed_models': json.loads(row['models']), 'currency': 'development_satk', 'mainnet_funds_enabled': False}
                    db.execute('UPDATE invitations SET nonce_hash=?,profile=? WHERE code_hash=?',
                               (nonce_hash, json.dumps(profile), row['code_hash']))
            # Persistence commits before the in-memory credential is admitted.
            if profile['worker_id'] not in fleet.workers:
                fleet.add_worker(profile['worker_id'], profile['token'], profile['allowed_models'])
            return profile

    def restore_workers(self, fleet):
        with self.db() as db:
            rows = db.execute('SELECT profile FROM invitations WHERE profile IS NOT NULL').fetchall()
        for row in rows:
            profile = json.loads(row['profile'])
            fleet.add_worker(profile['worker_id'], profile['token'], profile['allowed_models'])

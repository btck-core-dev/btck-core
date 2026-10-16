"""Fail-closed, expiring operator health lease. Contains no credentials."""
import json
from pathlib import Path
import time


class SourceHealthGate:
    def __init__(self, path, *, max_lease_seconds=90, clock=time.time):
        self.path = Path(path)
        self.max_lease_seconds = max_lease_seconds
        self.clock = clock

    def __call__(self):
        try:
            if self.path.stat().st_size > 4096:
                return False
            value = json.loads(self.path.read_text())
            now = self.clock()
            issued = float(value['issued_at'])
            expires = float(value['expires_at'])
            return (value.get('allowed') is True and value.get('network') == 'mainnet'
                    and issued <= now <= expires and now-issued <= self.max_lease_seconds
                    and 0 < expires-issued <= self.max_lease_seconds)
        except (OSError, ValueError, KeyError, TypeError):
            return False

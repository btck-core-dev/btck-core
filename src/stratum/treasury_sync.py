"""Incremental treasury scans reuse the wallet's authenticated encrypted cache.

Payment workers read a cache snapshot, apply a bounded suffix in memory, and
publish balances only when the canonical endpoint reports completion. A
separate resource-limited updater can keep the encrypted checkpoint fresh.
Neither path silently falls back to a genesis scan.
"""
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from privacy.wallet_engine import StealthWallet


class CachedTreasurySync:
    def __init__(self, cache_file, password_file, *,
                 sync_url='http://127.0.0.1:18088/api/wallet/sync',
                 max_pages=4, persist=False, progress=None):
        self.cache_file = Path(cache_file)
        self.password_file = Path(password_file)
        parsed = urlparse(sync_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1'):
            raise ValueError('Treasury incremental sync requires a loopback endpoint')
        if not str(self.cache_file).endswith('.sync.json'):
            raise ValueError('Treasury cache must use the encrypted wallet cache format')
        if type(max_pages) is not int or not 1 <= max_pages <= 120:
            raise ValueError('Treasury page budget must be between 1 and 120')
        self.sync_url = sync_url
        self.max_pages = max_pages
        self.persist = persist
        self.progress = progress

    def __call__(self, treasury_wallet):
        from tools import btck_cli
        for path in (self.cache_file, self.password_file):
            if path.stat().st_mode & 0o077:
                raise ValueError('Treasury cache and password permissions must be 600')
        candidate = StealthWallet(treasury_wallet.keypair, 'incremental-treasury-scan')
        candidate._source_path = Path(str(self.cache_file)[:-len('.sync.json')])
        candidate._unlock_password = self.password_file.read_text().rstrip('\r\n')
        state = btck_cli._load_sync_cache(candidate)
        if state.get('network') is None or int(state['next_height']) <= 0:
            raise ValueError('Verified encrypted treasury checkpoint is required')
        btck_cli.validate_network_identity(state['network'])
        if not self.persist:
            # Only the isolated updater writes the shared checkpoint. Payment
            # scans keep their suffix in memory, including confirmed spends.
            candidate._source_path = None
        candidate._live_sync_state = state
        candidate._live_sync_saved_at = time.monotonic()
        args = SimpleNamespace(sync_url=self.sync_url, timeout=5,
                               sync_page_size=250, sync_workers=1,
                               live_session_cache=True)
        try:
            info, _ = btck_cli.scan_wallet(args, candidate, self.progress,
                                          allow_full_rescan=False, max_pages=self.max_pages)
        finally:
            # A bounded catch-up can retain validated progress without making
            # any partial balance available to the payment worker.
            if self.persist and candidate._live_sync_state != state:
                btck_cli._save_sync_cache(candidate, candidate._live_sync_state)
        if candidate._live_sync_state['next_height'] != info['selected_height'] + 1:
            raise ValueError('Treasury scan has not reached the reported canonical tip')
        return candidate, info['selected_height']

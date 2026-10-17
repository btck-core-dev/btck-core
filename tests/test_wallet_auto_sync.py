"""Desktop wallet live-tip and automatic synchronization safety tests."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "tools"))
sys.path.insert(0, os.fspath(ROOT / "src"))

import btck_cli
from btck_wallet_gui import WalletApp


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestWalletAutoSync(unittest.TestCase):
    def test_network_failure_revokes_live_state_without_discarding_cache(self):
        badges, progress, scheduled, controls = [], [], [], []
        wallet = object()
        app = SimpleNamespace(
            _closing=False, _wallet_generation=7, wallet=wallet,
            _auto_sync_failures=0, AUTO_SYNC_POLL_MS=5000,
            AUTO_SYNC_MAX_BACKOFF_MS=60000, height=456, cache_height=456,
            sync_progress_value=100, GOLD='gold',
            height_label=SimpleNamespace(configure=lambda **kw: badges.append(kw)),
            status_var=SimpleNamespace(set=lambda text: None),
            _log=lambda text: None,
            _set_sync_progress=lambda *args, **kw: progress.append(args),
            _schedule_auto_sync=scheduled.append,
        )
        app._update_controls=lambda: controls.append(app.height)
        for expected_delay in (5000, 10000):
            WalletApp._schedule_auto_retry(app, 7, OSError('connection lost'))
            self.assertEqual(app.height, -1)
            self.assertEqual(app.cache_height, 456)
            self.assertIs(app.wallet, wallet)
            self.assertEqual(scheduled[-1], expected_delay)
            self.assertEqual(badges[-1]['text'], 'CACHED #456')
            self.assertEqual(progress[-1][1], 'RETRYING')
            self.assertEqual(controls[-1], -1)
        # A completed callback from a previous wallet cannot alter the new one.
        WalletApp._schedule_auto_retry(app, 6, OSError('old connection'))
        self.assertEqual(len(scheduled), 2)

    def test_tip_check_validates_network_and_cursor(self):
        identity = dict(btck_cli.APPROVED_NETWORKS["mainnet"])
        response = _Response({
            "format": "btck-wallet-tip-v1",
            "network": identity,
            "tip_height": 456,
            "tip_hash": "ab" * 32,
            "observed_at": 123.5,
        })
        with patch.object(btck_cli, "urlopen", return_value=response):
            tip = btck_cli.fetch_wallet_tip("https://wallet.example/api/wallet/tip")
        self.assertEqual(tip["tip_height"], 456)
        self.assertEqual(tip["tip_hash"], "ab" * 32)
        self.assertEqual(tip["network"], identity)

    def test_tip_check_rejects_plaintext_remote_transport(self):
        with self.assertRaisesRegex(btck_cli.CLIError, "HTTPS"):
            btck_cli.fetch_wallet_tip("http://wallet.example/api/wallet/tip")

    def test_tip_check_rejects_wrong_network(self):
        identity = dict(btck_cli.APPROVED_NETWORKS["mainnet"])
        identity["chain_id"] = "wrong-chain"
        response = _Response({
            "format": "btck-wallet-tip-v1",
            "network": identity,
            "tip_height": 456,
            "tip_hash": "ab" * 32,
        })
        with patch.object(btck_cli, "urlopen", return_value=response):
            with self.assertRaisesRegex(btck_cli.CLIError, "approved"):
                btck_cli.fetch_wallet_tip("https://wallet.example/api/wallet/tip")

    def test_gui_contains_startup_and_periodic_auto_sync_hooks(self):
        source = (ROOT / "tools" / "btck_wallet_gui.py").read_text(encoding="utf-8")
        self.assertIn("self._schedule_auto_sync(100)", source)
        self.assertIn("btck_cli.fetch_wallet_tip", source)
        self.assertIn("AUTO_SYNC_POLL_MS = 5_000", source)
        self.assertIn("self._stop_auto_sync(invalidate=True)", source)

    def test_gui_routes_worker_results_through_main_thread_queue(self):
        source = (ROOT / "tools" / "btck_wallet_gui.py").read_text(encoding="utf-8")
        self.assertIn("self._ui_queue.put(callback)", source)
        self.assertIn("self.after(25, self._drain_ui_queue)", source)
        self.assertIn("lambda: self._open_wallet_finished(", source)
        self.assertIn("progress_callback=lambda progress: self._post_ui", source)

    def test_wallet_selection_cannot_reenter_password_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            wallet_path = Path(directory) / "wallet.json"
            wallet_path.write_text("{}", encoding="utf-8")
            prompts = []
            app = SimpleNamespace(
                _refreshing_wallet_list=False,
                busy=False,
                _password_prompt_active=False,
                registry=SimpleNamespace(
                    get=lambda wallet_id: {"id": wallet_id, "path": str(wallet_path)},
                ),
                active_wallet_id=None,
                wallet=None,
                _selected_wallet_id=lambda: "wallet-id",
                _update_controls=lambda: None,
                _refresh_wallet_list=lambda *_args: None,
                _stop_auto_sync=lambda **_kwargs: None,
                _schedule_auto_sync=lambda *_args: None,
                _open_wallet_path=lambda *_args, **_kwargs: self.fail(
                    "cancelled prompt must not open a wallet"
                ),
            )

            def ask_password():
                prompts.append(1)
                WalletApp._wallet_selected(app)
                return None

            app._ask_password = ask_password
            WalletApp._wallet_selected(app)
            self.assertEqual(len(prompts), 1)
            self.assertFalse(app._password_prompt_active)


if __name__ == "__main__":
    unittest.main()

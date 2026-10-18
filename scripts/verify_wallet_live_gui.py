#!/usr/bin/env python3
"""Real Tk/HTTP/encrypted-wallet integration against an isolated test ledger.

NOT a live-mainnet balance or installed-app acceptance claim. No node, user
wallet, real registry, production endpoint or environment HOME is modified.
"""
import argparse
from contextlib import ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools')]
import btck_cli
import btck_wallet_gui as gui
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet
from privacy.wallet_registry import WalletRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    identity = dict(btck_cli.APPROVED_NETWORKS['mainnet'])
    blocks = [{'height': 0, 'hash': identity['genesis_hash'], 'created': [], 'spent': []}]
    lock = threading.Lock()
    requests = []
    unavailable = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            with lock:
                last = len(blocks)-1
                tip = dict(network=identity, tip_height=last, tip_hash=blocks[last]['hash'])
                status = 200
                if unavailable.is_set():
                    status, payload = 503, {'error': 'isolated transport outage'}
                elif parsed.path == '/api/wallet/tip':
                    payload = dict(tip, format='btck-wallet-tip-v1', observed_at=time.time())
                elif parsed.path == '/api/wallet/sync':
                    start = int(query.get('from_height', ['0'])[0])
                    limit = int(query.get('limit', ['1000'])[0])
                    anchor = query.get('anchor_hash', [''])[0]
                    requests.append(start)
                    if start < 0 or start > last+1 or (start and anchor != blocks[start-1]['hash']):
                        status, payload = 409, {'error': 'stale test ledger cursor'}
                    else:
                        end = min(start+limit, last+1)
                        payload = dict(tip, format='btck-wallet-sync-v1', from_height=start,
                                       next_height=end, done=end>last, unspent_only=False,
                                       anchor_hash=blocks[end-1]['hash'] if end else '',
                                       blocks=blocks[start:end])
                else:
                    status, payload = 404, {'error': 'test endpoint not found'}
                encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    base = f'http://127.0.0.1:{server.server_port}'
    evidence = {'scope': 'isolated real Tk GUI with synthetic ledger; NOT live mainnet',
                'verification': 'FAIL', 'production_access': False}
    real_tip = btck_cli.fetch_wallet_tip
    real_registry = WalletRegistry
    app = None
    try:
        with tempfile.TemporaryDirectory(prefix='btck-wallet-gui-') as directory, ExitStack() as stack:
            isolated_cache_root = Path(directory)
            wallets = [StealthWallet.generate('disposable-a'), StealthWallet.generate('disposable-b')]
            password = secrets.token_urlsafe(24)
            paths = [isolated_cache_root/f'disposable-{i}.json' for i in range(2)]
            for wallet, path in zip(wallets, paths):
                btck_cli.write_private_json(path, wallet.export_encrypted(password))
            registry = real_registry(isolated_cache_root/'registry.json')
            # Patch only this test process. Do not alter HOME or touch a user's
            # real public cache/registry; production callbacks still run as-is.
            stack.enter_context(patch.object(gui, 'WalletRegistry', return_value=registry))
            stack.enter_context(patch.object(gui.Path, 'home', return_value=isolated_cache_root))
            stack.enter_context(patch.object(gui.os, 'cpu_count', return_value=3))
            stack.enter_context(patch.object(btck_cli, 'DEFAULT_SYNC_URL', base+'/api/wallet/sync'))
            stack.enter_context(patch.object(btck_cli, 'fetch_wallet_tip',
                side_effect=lambda timeout=5.0: real_tip(base+'/api/wallet/tip', timeout=timeout)))
            app = gui.WalletApp()
            app.withdraw()
            progress = {'phase': 'opening', 'started': time.monotonic()}
            errors = []

            def append_owned_block(wallet, amount):
                ephemeral, onetime = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
                with lock:
                    height = len(blocks)
                    block_hash = hashlib.sha256(f'isolated-wallet-block-{height}'.encode()).hexdigest()
                    output = {'txid': block_hash, 'vout': 0, 'amount_satk': amount,
                              'R_ephem_hex': ephemeral.hex(), 'P_onetime_hex': onetime.hex(),
                              'height': height, 'is_coinbase': False,
                              'timelock_until_height': 0, 'kind': 'standard'}
                    blocks.append({'height': height, 'hash': block_hash,
                                   'created': [output], 'spent': []})

            def finish_error(exc):
                evidence['verification'] = 'FAIL'
                errors.append(f'{type(exc).__name__}: {exc}')
                app._close_application()

            def callback_error(kind, value, traceback):
                finish_error(value)

            app.report_callback_exception = callback_error

            def advance():
                try:
                    if time.monotonic()-progress['started'] > 55:
                        raise TimeoutError('real GUI did not complete automatic sync acceptance')
                    phase = progress['phase']
                    if phase == 'opening' and app.height == 0 and not app.busy:
                        assert sum(u.amount_satk for u in app.wallet.utxos if not u.spent) == 0
                        append_owned_block(wallets[0], 123456789)
                        progress.update(phase='waiting-credit', credit_at=time.monotonic())
                    elif phase == 'waiting-credit' and app.height == 1 and not app.busy:
                        assert app.wallet.get_spendable_balance_satk(1) == 123456789
                        assert app.balance_label.cget('text') == '1.23456789'
                        evidence['automatic_credit_seconds'] = round(time.monotonic()-progress['credit_at'], 3)
                        assert evidence['automatic_credit_seconds'] <= 12
                        evidence['first_wallet_balance_satk'] = 123456789
                        with lock:
                            evidence['requests_before_switch'] = list(requests)
                        append_owned_block(wallets[1], 234567891)
                        progress['phase'] = 'switching'
                        # Exercise the actual list selection and modal dialog,
                        # including a tip callback sent by the previous wallet
                        # while the nested Tk dialog event loop is running.
                        record = registry.add(paths[1], 'disposable-b')
                        app._refresh_wallet_list(record['id'])
                        old_generation = app._wallet_generation
                        app.after(50, lambda: app._auto_tip_finished(old_generation,
                            dict(network=identity, tip_height=2, tip_hash=blocks[2]['hash'])))
                        def enter_disposable_password():
                            dialog = next(w for w in app.winfo_children()
                                          if isinstance(w, gui.PasswordDialog))
                            dialog.entry.insert(0, password)
                            dialog.accept()
                        app.after(150, enter_disposable_password)
                        app._wallet_selected()
                        evidence['actual_modal_list_switch_with_old_tip_callback'] = True
                    elif phase == 'switching' and app.height == 2 and not app.busy:
                        assert app.wallet.get_spendable_balance_satk(2) == 234567891
                        assert app.balance_label.cget('text') == '2.34567891'
                        with lock:
                            after = list(requests)
                        previous = evidence['requests_before_switch']
                        assert after[:len(previous)] == previous
                        assert all(start >= 2 for start in after[len(previous):]), 'redownloaded shared history'
                        public_cache = isolated_cache_root/'.btck/public-ledger.sqlite3'
                        assert public_cache.is_file()
                        contents = public_cache.read_bytes()
                        assert all(wallet.address.encode() not in contents for wallet in wallets)
                        assert len(registry.records) == 2
                        evidence.update(verification='PASS', second_wallet_balance_satk=234567891,
                                        history_requests=after, shared_history_reused=True,
                                        manual_sync_clicks=0, real_tk_event_loop=True,
                                        real_encrypted_wallet_unlock=True,
                                        elapsed_seconds=round(time.monotonic()-progress['started'], 3))
                        # Reorganize the same-height tail after a genuine
                        # encrypted reopen. Actual GUI polling must notice the
                        # changed hash, undo old credit and apply replacement.
                        ephemeral,onetime=StealthKeyPair.generate_one_time_stealth_output(wallets[1].address)
                        fork_hash=hashlib.sha256(b'isolated-real-gui-fork').hexdigest()
                        with lock:
                            blocks[2]={'height':2,'hash':fork_hash,'spent':[],
                                'created':[{'txid':fork_hash,'vout':0,'amount_satk':345678912,
                                    'R_ephem_hex':ephemeral.hex(),'P_onetime_hex':onetime.hex(),
                                    'height':2,'is_coinbase':False,'timelock_until_height':0,'kind':'standard'}]}
                        progress.update(phase='forked',fork_at=time.monotonic())
                    elif (phase=='forked' and app.height==2 and not app.busy
                          and app.wallet.get_spendable_balance_satk(2)==345678912):
                        assert app.balance_label.cget('text')=='3.45678912'
                        evidence['automatic_reorg_seconds']=round(time.monotonic()-progress['fork_at'],3)
                        assert evidence['automatic_reorg_seconds']<=12
                        evidence['reorganized_balance_satk']=345678912
                        unavailable.set()
                        progress.update(phase='offline',offline_at=time.monotonic())
                    elif phase=='offline' and app.height<0 and not app.busy:
                        assert app.height_label.cget('text')=='CACHED #2'
                        assert app.balance_label.cget('text')=='3.45678912'
                        assert not app.send_button.enabled
                        evidence['outage_revokes_live']=True
                        evidence['outage_preserves_balance']=True
                        unavailable.clear()
                        progress.update(phase='reconnecting',reconnect_at=time.monotonic())
                    elif phase=='reconnecting' and app.height==2 and not app.busy:
                        assert app.balance_label.cget('text')=='3.45678912'
                        evidence['automatic_reconnect_seconds']=round(time.monotonic()-progress['reconnect_at'],3)
                        assert evidence['automatic_reconnect_seconds']<=15
                        # Stop automatic refresh only in this fixture, so a
                        # decrypted cache must render its own spendable balance
                        # immediately, before any further ledger scan.
                        app._schedule_auto_sync = lambda *a, **kw: None
                        app._cancel_auto_sync_timer()
                        app._open_wallet_path(paths[0], password)
                        progress['phase'] = 'cached-reopen'
                    elif phase=='cached-reopen' and not app.busy and app.wallet.address==wallets[0].address:
                        assert app.height == -1
                        assert app.cache_height == 1
                        assert app.balance_label.cget('text') == '1.23456789'
                        assert not app.send_button.enabled
                        evidence['cached_switch_balance_before_scan'] = True
                        evidence['elapsed_seconds']=round(time.monotonic()-progress['started'],3)
                        app._close_application()
                        return
                    app.after(25, advance)
                except Exception as exc:
                    finish_error(exc)

            app.after(0, lambda: app._open_wallet_path(paths[0], password))
            app.after(25, advance)
            app.mainloop()
            if errors:
                evidence['errors'] = errors
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True)+'\n')
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence['verification'] == 'PASS' else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())

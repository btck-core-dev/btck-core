#!/usr/bin/env python3
"""Exercise the delivered Windows ZIP, using disposable wallets and loopback only."""
import argparse
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from privacy.private_files import restrict_private_path
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet, transaction_from_dict


def verify_gui(executable, env):
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    windows = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    process = subprocess.Popen([str(executable)], cwd=executable.parent, env=env)

    @callback
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid:
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, title, len(title))
            if title.value.startswith("BTC-K Wallet "):
                windows.append((hwnd, title.value))
        return True

    try:
        deadline = time.monotonic() + 20
        while not windows:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("Delivered GUI failed to open its main window")
            user.EnumWindows(visit, 0)
            time.sleep(.1)
        user.PostMessageW(windows[0][0], 0x0010, 0, 0)
        assert process.wait(timeout=10) == 0
        return windows[0][1]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run on Windows")
    assert hashlib.sha256(args.archive.read_bytes()).hexdigest() == args.sha256
    checks = {"archive_sha256": args.sha256, "production_access": False,
              "real_funds_used": False, "scope": "delivered ZIP; synthetic loopback ledger"}
    with tempfile.TemporaryDirectory(prefix="btck-final-", dir=args.evidence.parent) as temporary:
        scratch = Path(temporary)
        extract = scratch / "Windows 钱包 test"
        with zipfile.ZipFile(args.archive) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                assert not Path(name).is_absolute() and ".." not in Path(name).parts
            archive.extractall(extract)
        package = next(extract.glob("BTC-K-Wallet-*"))
        identity = json.loads((package / "network_identity.json").read_text())
        network = {k: identity[k] for k in ("name", "chain_id", "genesis_hash", "consensus_fingerprint")}
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTHON", "BTCK_")) and k not in ("TCL_LIBRARY", "TK_LIBRARY")}
        system_root = os.environ["SystemRoot"]
        env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root])
        env["LOCALAPPDATA"] = str(scratch / "localappdata")
        cli = package / "btck-wallet.exe"
        checks["gui_title"] = verify_gui(package / "BTC-K Wallet.exe", env)
        checks["clean_environment_launch"] = True

        def run(label, *arguments, password=None, expected=0):
            command = [str(cli)]
            if password:
                command += ["--password-file", str(password)]
            result = subprocess.run(command + list(map(str, arguments)), env=env,
                                    cwd=scratch, capture_output=True, timeout=45)
            if result.returncode != expected:
                # A failed 'new' may contain recovery words. Never print raw output.
                raise AssertionError(f"{label}: unexpected exit code {result.returncode}")
            return result.stdout.decode("utf-8", errors="replace")

        password = scratch / "password.txt"
        password_value = secrets.token_urlsafe(24)
        password.write_text(password_value, encoding="utf-8")
        restrict_private_path(password)
        wallet_path, restored_path = scratch / "新钱包.json", scratch / "恢复钱包.json"
        output = run("create", "new", "--words", "12", "--name", "disposable",
                     "--skip-immediate-recovery-check", "-o", wallet_path, password=password)
        words = re.findall(r"\b\d{2}\. ([a-z]+)\b", output)
        assert len(words) == 12
        mnemonic_file = scratch / "recovery.txt"
        mnemonic_file.write_text(" ".join(words), encoding="utf-8")
        restrict_private_path(mnemonic_file)
        run("restore", "restore", "--words", "12", "--mnemonic-file", mnemonic_file,
            "-o", restored_path, password=password)
        run("recovery proof", "verify-recovery", restored_path,
            "--mnemonic-file", mnemonic_file, password=password)
        original = StealthWallet.load_from_json(str(wallet_path), password_value)
        recovered = StealthWallet.load_from_json(str(restored_path), password_value)
        assert original.address == recovered.address
        checks["create_recover_and_verify_address"] = True
        bad_password = scratch / "wrong-password.txt"
        bad_password.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        restrict_private_path(bad_password)
        run("wrong password", "info", wallet_path, password=bad_password, expected=1)
        checks["wrong_password_rejected"] = True
        # A separate externally encrypted JSON proves import compatibility.
        imported = scratch / "imported.json"
        with imported.open("w", encoding="utf-8") as handle:
            json.dump(original.export_encrypted(password_value), handle)
        restrict_private_path(imported)
        run("encrypted JSON import", "info", imported, password=password)
        checks["encrypted_json_import"] = True

        r_point, p_point = StealthKeyPair.generate_one_time_stealth_output(original.address)
        blocks = [dict(height=0, hash=network["genesis_hash"], created=[], spent=[]),
                  dict(height=1, hash="12" * 32, spent=[], created=[dict(
                      txid="34" * 32, vout=0, amount_satk=1_000_000,
                      R_ephem_hex=r_point.hex(), P_onetime_hex=p_point.hex(),
                      height=1, is_coinbase=False, timelock_until_height=0, kind="standard")])]
        requested = []
        wrong_chain = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                start = int(query.get("from_height", ["0"])[0])
                requested.append(start)
                payload = dict(format="btck-wallet-sync-v1", network=dict(network),
                               from_height=start, next_height=2, anchor_hash=blocks[-1]["hash"],
                               tip_height=1, done=True, blocks=blocks[start:])
                if wrong_chain.is_set():
                    payload["network"]["chain_id"] = "untrusted-test-network"
                encoded = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        url = f"http://127.0.0.1:{server.server_port}/api/wallet/sync"
        try:
            common = ["--sync-url", url, "--sync-workers", "2"]
            balance = run("parallel scan", *common, "balance", wallet_path,
                          "--unit", "satk", password=password)
            assert "1,000,000 SatK" in balance
            run("cached scan", *common, "balance", wallet_path, password=password)
            assert requested[-1] == 2
            checks["packaged_spawn_scan_and_encrypted_resume"] = True
            watch, unsigned, signed = [scratch / x for x in ("watch.json", "unsigned.json", "signed.json")]
            run("watch export", "export-watch", wallet_path, "--out", watch, password=password)
            recipient = StealthWallet.generate("disposable-recipient")
            run("prepare", *common, "prepare", watch, "--to", recipient.address,
                "--amount", "0.003", "--fee", "0.0001", "--out", unsigned)
            package_id = json.loads(unsigned.read_text())["package_id"]
            run("offline sign", "sign", wallet_path, unsigned, "--approve-package-id",
                package_id, "--out", signed, password=password)
            transaction = transaction_from_dict(json.loads(signed.read_text())["transaction"])
            assert StealthWallet.verify_transaction(transaction)
            assert sum(x.amount_satk for x in transaction.inputs) == sum(x.amount_satk for x in transaction.outputs) + 10_000
            checks["offline_signatures_and_asset_conservation"] = True
            wrong_chain.set()
            run("reject wrong network", *common, "balance", restored_path,
                password=password, expected=1)
            checks["wrong_chain_rejected"] = True
        finally:
            server.shutdown()
            server.server_close()
            serving.join(timeout=2)
        checks["unicode_and_space_paths"] = True
    checks["temporary_wallets_removed"] = True
    checks["verification"] = "PASS"
    args.evidence.write_text(json.dumps(checks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(checks, sort_keys=True))


if __name__ == "__main__":
    main()

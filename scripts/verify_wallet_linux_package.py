#!/usr/bin/env python3
"""Exercise the delivered Linux archive, using disposable wallets and loopback only."""
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
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from privacy.private_files import restrict_private_path
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import StealthWallet, transaction_from_dict


def verify_gui(executable, env, desktop=None):
    process = subprocess.Popen(["gio", "launch", str(desktop)] if desktop else [str(executable)], cwd=executable.parent, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        window = None
        while window is None:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("Delivered Linux GUI failed to open its main window")
            tree = subprocess.check_output(["xwininfo", "-root", "-tree"], env=env, text=True)
            match = re.search(r'(0x[0-9a-f]+) "(BTC-K Wallet [^"]+)"', tree)
            if match:
                window, title = int(match[1], 16), match[2]
            else:
                time.sleep(.1)
        # Request normal WM_DELETE_WINDOW shutdown through the actual X11 API.
        import ctypes.util
        x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                  ctypes.c_long, ctypes.c_void_p]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        class ClientMessage(ctypes.Structure):
            _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                        ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                        ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                        ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]
        class Event(ctypes.Union):
            _fields_ = [("client", ClientMessage), ("pad", ctypes.c_long * 24)]
        display = x11.XOpenDisplay(None)
        assert display
        try:
            event = Event()
            event.client.type = 33
            event.client.display = display
            event.client.window = window
            event.client.message_type = x11.XInternAtom(display, b"WM_PROTOCOLS", 0)
            event.client.format = 32
            event.client.data[0] = x11.XInternAtom(display, b"WM_DELETE_WINDOW", 0)
            assert x11.XSendEvent(display, window, 0, 0, ctypes.byref(event))
            x11.XFlush(display)
            assert process.wait(timeout=10) == 0
        finally:
            x11.XCloseDisplay(display)
        assert not process.stderr.read(), "Delivered GUI wrote unexpected errors"
        return title
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
    if not sys.platform.startswith("linux"):
        parser.error("Run on Linux")
    assert hashlib.sha256(args.archive.read_bytes()).hexdigest() == args.sha256
    checks = {"archive_sha256": args.sha256, "production_access": False,
              "real_funds_used": False, "scope": "delivered tar.gz; synthetic loopback ledger"}
    with tempfile.TemporaryDirectory(prefix="btck-final-", dir=args.evidence.parent) as temporary:
        scratch = Path(temporary)
        extract = scratch / "Linux 钱包 test"
        with tarfile.open(args.archive) as archive:
            for member in archive.getmembers():
                assert not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
                assert member.isfile() or member.isdir(), "No links permitted in release archive"
            archive.extractall(extract)
        package = next(extract.glob("BTC-K-Wallet-*"))
        identity = json.loads((package / "network_identity.json").read_text())
        network = {k: identity[k] for k in ("name", "chain_id", "genesis_hash", "consensus_fingerprint")}
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTHON", "BTCK_")) and k not in ("TCL_LIBRARY", "TK_LIBRARY")}
        env["PATH"] = "/usr/bin:/bin"
        env["HOME"] = str(scratch / "home")
        env["XDG_DATA_HOME"] = str(scratch / "share")
        Path(env["HOME"]).mkdir()
        cli = package / "btck-wallet"
        checks["gui_title"] = verify_gui(package / "launch-wallet.sh", env)
        # Install and actually launch the desktop entry, including reserved
        # percent, quote, dollar and backtick characters in its parent path.
        special = package.parent / 'desktop 20% "quote" $cash `tick`'
        package.rename(special)
        package = special
        cli = package / "btck-wallet"
        subprocess.run([str(package / "install-desktop.sh")], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        desktop = Path(env["XDG_DATA_HOME"]) / "applications/bitcoin-k-wallet.desktop"
        subprocess.run(["desktop-file-validate", str(desktop)], check=True, env=env)
        checks["desktop_entry_valid"] = True
        checks["gui_special_path_title"] = verify_gui(package / "launch-wallet.sh", env, desktop)
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
        for path in (wallet_path, restored_path, imported, password, mnemonic_file):
            assert path.stat().st_mode & 0o077 == 0
        checks["posix_private_permissions"] = True
        checks["unicode_and_space_paths"] = True
    checks["temporary_wallets_removed"] = True
    checks["verification"] = "PASS"
    args.evidence.write_text(json.dumps(checks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(checks, sort_keys=True))


if __name__ == "__main__":
    main()

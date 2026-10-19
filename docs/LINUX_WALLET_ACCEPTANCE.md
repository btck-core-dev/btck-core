# Linux x86_64 wallet acceptance (3.2.13)

The Linux platform package shares the L1 wallet engine, encrypted JSON format and
approved mainnet identity with the released macOS and Windows wallets. It adds
Linux fonts/cursors, XDG registry paths, a portable GUI/CLI archive and an optional
per-user desktop menu installer. No production node or consensus change is included.

Build and acceptance target: Ubuntu 22.04 x86_64, glibc 2.35, Python 3.10.12,
Tcl/Tk 8.6, PyInstaller 6.16.0, with a real Tk event loop under Xvfb.
Runtime Python and Tcl/Tk are bundled; X11/XWayland host libraries are required.
Other distributions, native Wayland-only sessions and ARM64 remain unvalidated.

Focused client suite: 62 tests, 61 passed and one Windows-only NTFS check skipped.
The actual source GUI passed automatic tip/credit refresh, two-wallet modal
switching, reuse of public history, same-height suffix replacement, transport loss,
automatic reconnect and encrypted cached reopening without enabling SEND offline.

The extracted final archive separately passed:

- GUI startup and normal window-manager close with no external Python paths.
- Desktop entry validation and actual GIO launch from a path containing Chinese,
  spaces, percent signs, quotes, dollar signs and backticks.
- CLI create, mnemonic restore/address equality, encrypted JSON import and wrong
  password rejection, using only temporary disposable wallets.
- A loopback synthetic ledger, cached resume and packaged scan workers.
- Offline transaction signing, signature verification, asset conservation and
  rejection of the wrong chain identity.
- POSIX private wallet/registry permissions and temporary-wallet cleanup.

No user wallet, real-funds transfer, production chain replay or full node was used.
This acceptance does not constitute an independent security audit or prove
real-mainnet Linux send/receive, long-term availability, or binary reproducibility.
The detached release signature establishes provenance only. Verify using the
separately trusted BTC-K release key, identity `btck-release`, namespace
`btck-linux-wallet`, against `LINUX_WALLET_MANIFEST.json`.

Build and repeat the focused checks:

```sh
python3 -m venv build/linux-venv
build/linux-venv/bin/pip install -r packaging/requirements-linux-build.txt
build/linux-venv/bin/python -m unittest tests.test_wallet_linux tests.test_wallet_windows tests.test_wallet_security tests.test_wallet_registry tests.test_wallet_auto_sync tests.test_wallet_switch_race tests.test_wallet_live_session tests.test_wallet_sync_index tests.test_wallet_suffix_recovery tests.test_stealth_wallet_transfer
xvfb-run -a build/linux-venv/bin/python scripts/verify_wallet_live_gui.py --evidence build/source-gui.json
xvfb-run -a build/linux-venv/bin/python packaging/build_wallet_linux.py --version 3.2.13
```

Run `scripts/verify_wallet_linux_package.py` under Xvfb with the built archive,
its independently calculated `--sha256` and a private temporary `--evidence` path.
The package acceptance host additionally needs `x11-utils`, `desktop-file-utils`
and `libglib2.0-bin` (GIO). Tests do not start `btckd` or contact production.

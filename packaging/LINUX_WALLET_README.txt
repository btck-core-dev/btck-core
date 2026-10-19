BTC-K Wallet 3.2.13 for Linux x86_64

GET STARTED
1. Extract the complete tar.gz into a permanent folder owned by your account:
   tar -xzf BTC-K-Wallet-3.2.13-Linux-x86_64.tar.gz
2. Open a terminal in the extracted folder and run ./launch-wallet.sh
   No Python installation, GPU, full node or root access is required.
3. Optionally run ./install-desktop.sh to add BTC-K Wallet to your application menu.
   Keep the extracted folder in place. Rerun the installer if you move it.
4. Create a wallet and back up the recovery words offline, or open an existing
   password-encrypted BTC-K wallet JSON. Keep your original backup.
5. Unlocking starts automatic synchronization. Wait for LIVE and the current height.
   A cached balance does not enable sending. Review recipient, amount and fee.

The package bundles Python, Tcl/Tk and wallet code. Keep BTC-K Wallet beside its
_internal folder. ./btck-wallet --help provides the optional command-line interface.
A graphical X11 or XWayland session is required for the GUI. A headless SSH terminal
can use the CLI; it cannot display a wallet window without a graphical session.
Ubuntu 22.04 x86_64 (glibc 2.35) is the tested build and acceptance environment.
Other distributions, native Wayland-only sessions and ARM64 are not validated.
The host must supply glibc 2.35 or newer and normal X11/font system libraries.

WALLETS AND BACKUPS
The encrypted wallet format is shared with Mac and Windows. Passwords, private
keys and scanning stay local; synchronization downloads public ledger data.
The wallet list lives in $XDG_DATA_HOME/bitcoin-k-wallet/wallets.json, defaulting to
~/.local/share/bitcoin-k-wallet/wallets.json. Relative XDG_DATA_HOME is ignored.
The list contains paths and labels, not private keys. Encrypted balance checkpoints
are stored beside each wallet JSON. Public ledger cache is ~/.btck/public-ledger.sqlite3.
Working wallets and indexes are restricted to the current account (files 0600,
private directories 0700). Use a local filesystem supporting POSIX permissions.
Keep independent recovery words and original encrypted JSON backups for each wallet.
Do not share passwords, viewing keys, recovery words or wallet files with support.
An interrupted broadcast may already have reached the network: synchronize and
check the transaction before trying again. A local signature is not confirmation.

UNINSTALL
Remove ~/.local/share/applications/bitcoin-k-wallet.desktop (or its XDG_DATA_HOME
location) and the extracted program folder. Back up your encrypted wallet JSON and
recovery words first. Removing the program does not delete your wallet files.

VERIFY AND BUILD
Verify LINUX_WALLET_SHA256SUMS and the signed LINUX_WALLET_MANIFEST.json using the
separately trusted BTC-K release public key. SSH signature identity: btck-release;
namespace: btck-linux-wallet. Do not trust a public key solely because it came with
a download. No independent security audit or byte-identical binary build is claimed.
Acceptance uses disposable wallets and a synthetic loopback ledger, not real funds.
L2 inference and mining software are separate from this L1 wallet.

Build on Linux x86_64, Python 3.10+ with Tcl/Tk and a display (Xvfb is sufficient):
python3 -m venv build/linux-venv
build/linux-venv/bin/python -m pip install -r packaging/requirements-linux-build.txt
xvfb-run -a build/linux-venv/bin/python packaging/build_wallet_linux.py --version 3.2.13
On Ubuntu the build host needs python3-venv, python3-tk, xvfb and binutils.

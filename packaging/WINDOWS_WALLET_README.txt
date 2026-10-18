BTC-K Wallet 3.2.13 for Windows x64

GET STARTED
1. Extract the complete ZIP to a folder you control on a local NTFS drive.
2. Open BTC-K Wallet.exe. No Python installation or administrator access is needed.
3. Create a wallet and back up the displayed recovery words offline, or open your
   existing encrypted BTC-K wallet JSON using its password. Keep your original backup.
4. Unlocking starts automatic synchronization. Wait for LIVE and the current height.
5. Use your public BTC-K address in the miner worker field: address.worker-name.
   Use x as the miner password. Never use your wallet password in a miner.

Keep BTC-K Wallet.exe beside its _internal folder. The included btck-wallet.exe
provides an optional command-line interface; run btck-wallet.exe --help in Terminal.
SOLO and PPLNS setup: https://bitcoin-k.com/mining.html and MINING_ACCESS.md.

WALLETS AND BACKUPS
Wallet files use the same password-encrypted format as the Mac wallet. Passwords,
private keys and scanning stay local. Synchronization downloads public ledger data;
it does not start a full node. The wallet list is stored under
%LOCALAPPDATA%\Bitcoin K Wallet\wallets.json and contains paths and labels, not keys.
Encrypted balance checkpoints are stored beside each wallet file. You may choose a
private folder for wallet files; the application restricts their Windows permissions
to your account and SYSTEM. FAT/exFAT media do not support these permissions: keep
the working wallet on NTFS and use offline media for encrypted backups.

Each independently created wallet has its own recovery words and password. Keep
original encrypted JSON backups, particularly for imported legacy wallets that may
not have recovery words. A viewing key cannot spend but remains sensitive.
Do not send keys, recovery words, passwords or wallet files to support staff.
Do not store working wallets in shared, network or public cloud-sync folders.

SEND AND SYNC
Check the recipient, amount and fee before confirming a transfer. An interrupted
broadcast may already have reached the network; sync and check before trying again.
An offline or cached balance does not enable SEND. Multiple wallets remain separate.

RELEASE STATUS
This is experimental software. The package is not Authenticode signed; Windows may
show an unknown-publisher warning. Check the official download source and SHA-256.
Do not disable Windows security protections. Windows 10 x64 is the tested platform;
Windows 11 x64 requires separate validation. ARM64 Windows is not validated.
The Windows package is not claimed to be independently security audited or a
byte-for-byte reproducible PE build. L2 GPU inference is not enabled by this wallet.

BUILD FROM SOURCE (Windows x64, Python 3.13 with Tcl/Tk)
python -m venv build\windows-venv
build\windows-venv\Scripts\python -m pip install -r packaging\requirements-windows-build.txt
build\windows-venv\Scripts\python packaging\build_wallet_windows.py --version 3.2.13

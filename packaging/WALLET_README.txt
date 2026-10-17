BTC-K Wallet (macOS)
====================

Scope
-----
The exact version and approved MAINNET genesis/checkpoint identity are recorded in the adjacent
network_identity.json and archive build-evidence file. The wallet rejects a node whose network,
genesis hash, or consensus fingerprint differs. Guardian and public post-checkpoint packages are
separate artifacts and must never be substituted for one another.

Private keys, view keys, and transaction signing always remain on this Mac. Balance lookup downloads
address-neutral chain output pages through the public HTTPS endpoint; the server receives no wallet
address or key. The incremental sync cursor and identified UTXOs are stored beside the wallet in an
encrypted .sync.json file protected by the wallet password. Current releases use a
compressed v2 cache for large mining wallets and remain able to read legacy v1 caches.

Opening the wallet
------------------
Extract the archive in Finder, then double-click "BTC-K Wallet.app". The adjacent
"Open BTC-K Wallet.command" launches the same application and falls back to the terminal menu on
older packages. If macOS blocks the unsigned application on first launch, verify the archive SHA-256
and allow it under System Settings, Privacy & Security.

The graphical wallet supports encrypted wallet creation and recovery, local unlock, address-neutral
balance synchronization, receiving, local transaction signing and signed-package broadcast. The
public relay accepts signed transactions only; it cannot access wallet keys or create a transaction.

Multiple wallets
----------------
The MY WALLETS panel indexes multiple independently encrypted wallet files. Opening, creating or
restoring a wallet adds it to this local list. Selecting another entry asks for that wallet's own
password and locks the previously active wallet. RENAME changes only the local display label. FORGET
removes only the index entry and never deletes the encrypted wallet file.

The registry contains wallet labels and local file paths only. It never contains passwords, recovery
phrases, spend keys or view keys. Keep separate recovery phrases and preferably separate passwords
for personal, mining, pool treasury and ecosystem-fund roles. This independent-account structure also
leaves room for future L2 asset views without merging L1 private keys.

The default personal wallet location is:
  ~/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json

Wallet creation and recovery
----------------------------
The graphical Create Wallet flow creates one encrypted wallet file and one 24-word BIP-39 recovery
phrase. Most users need only a personal/mining wallet. Operators who also maintain a pool treasury or
ecosystem fund must create each role as a separate wallet with a separate password and recovery phrase.
Write every phrase on paper. Never capture, copy, upload, email, or send it to anyone. Recovery words
are not stored in the wallet file.

Recovery verification is visible while typing so spelling can be checked. Ensure nobody can see or
record the screen. The wallet refuses to overwrite an existing file.

Balance sync
------------
The launcher has one BTC-K/SatK display toggle. It changes only presentation:
1 BTC-K is always exactly 100,000,000 SatK, and there is no separate L2 coin or wallet.

Select option 3 in the launcher, or run:
  ./btck-wallet balance ~/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json --unit btck
  ./btck-wallet balance ~/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json --unit satk

The first scan shows height progress. Later scans download only new blocks. The graphical wallet
starts synchronization automatically after unlock, then checks a shared public chain cursor every
five seconds and downloads a ledger page only when the chain advances. Temporary network failures
back off automatically without discarding the last verified balance. SYNC LEDGER remains available
as a manual recovery control. Deleting the encrypted .sync.json cache causes a full rescan but does
not affect private keys or on-chain assets. The public endpoint requires no wallet address, SSH
account, or full local node.

Sending
-------
The graphical Send flow displays the complete recipient, amount and fee before local signing. It then
submits a network-bound signed package to https://wallet.bitcoin-k.com/api/wallet/broadcast. The relay
does not expose general node RPC and the node independently verifies signatures, inputs, maturity,
timelocks, conservation and double-spend rules before accepting a transaction.

Offline transaction workflow
----------------------------
Export a watch-only wallet on the offline Mac:
  ./btck-wallet export-watch personal_wallet.json --out personal_watch.json

On an online computer, scan and prepare an unsigned package:
  ./btck-wallet prepare personal_watch.json --to btck1s... --amount 1.25 --fee 0.0001 --out unsigned.json

Return only unsigned.json to the offline Mac and verify every field before signing:
  ./btck-wallet sign personal_wallet.json unsigned.json --approve-package-id <verified-package-id> --out signed.json

Return signed.json to the online computer and broadcast:
  ./btck-wallet broadcast signed.json

Security boundaries
-------------------
1. Wallet secrets use scrypt plus ChaCha20-Poly1305 encryption.
2. A watch-only file contains a private view key. It cannot spend funds but remains sensitive.
3. Verify the network, recipient, amount, fee, inputs, and package ID before offline signing.
4. Never upload wallet files, passwords, RPC tokens, recovery phrases, or unchecked signing material.
5. This build is not Apple Developer ID signed or notarized and has no third-party security audit.
6. Use prepare/sign/broadcast for offline signing; the sign command makes no node connection.
7. Verify every release archive using its adjacent .sha256 file.
8. This build rejects any genesis or consensus fingerprint not recorded in network_identity.json.
9. Transfers select only mature, unlocked outputs.

Current service status (17 October 2026)
--------------------------------------
The public signed-transaction relay is currently held in maintenance. Do not
interpret local signing or a relay timeout as a confirmed payment. Automatic
PPLNS payouts remain disabled. This wallet update does not enable pool payments.

Mining modes: PPLNS uses port 3332; SOLO uses ports 3333-3336 by initial difficulty
tier. Full endpoints, hashrate guidance and current payout status are published at:
  https://github.com/btck-core-dev/btck-core/blob/main/docs/mining_pool_access.md

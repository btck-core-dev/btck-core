BTC-K Wallet 3.2.2-guardian1 (macOS)
=====================================

Scope
-----
This package approves the current private guardian MAINNET identity and also supports DEVNET and TESTNET.
It is not the public wallet for the post-checkpoint chain. A newly built public release will be required
after the height 271,828 checkpoint changes the consensus identity.

Private keys, view keys, and transaction signing always remain on this Mac. Balance lookup downloads
address-neutral chain output pages through the private HTTPS endpoint; the server receives no wallet
address or key. The incremental sync cursor and identified UTXOs are stored beside the wallet in an
encrypted .sync.json file protected by the wallet password.

Opening the wallet
------------------
Extract the archive in Finder, then double-click "Open BTC-K Wallet.command". If macOS blocks the
unsigned application on first launch, verify the archive SHA-256 and allow it under System Settings,
Privacy & Security.

The default personal wallet location is:
  ~/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json

Wallet creation and recovery
----------------------------
The setup flow creates separate personal/mining, pool treasury, and ecosystem fund wallets. Each wallet
uses a separate password and 24-word BIP-39 recovery phrase. Write every phrase on paper. Never capture,
copy, upload, email, or send it to anyone. The recovery words are not stored in the wallet file.

Recovery verification is visible while typing so spelling can be checked. Ensure nobody can see or
record the screen. Closing the window does not overwrite an existing wallet; reopen the launcher and
resume setup.

Balance sync
------------
Select option 3 in the launcher, or run:
  ./btck-wallet balance ~/Documents/BTC-K-Mainnet-Wallets/personal_wallet.json

The first scan shows height progress. Later scans download only new blocks. Deleting the encrypted
.sync.json cache causes a full rescan but does not affect private keys or on-chain assets. The private
endpoint is reachable only from the approved management IP; no SSH account or full local node is used.

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
8. This guardian build rejects any unapproved genesis or consensus fingerprint.
9. Transfers select only mature, unlocked outputs.

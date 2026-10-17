# BTC-K 3.2.13 stability and wallet release

This release includes bounded node reorganizations, managed Seed-B native
following and reconnect recovery, automatic wallet synchronization and reliable
multiwallet switching. The mainnet identity, signature rules and issuance
parameters are unchanged.

## Transactions and node stability

Ordinary transaction preview and block application stage only touched UTXOs,
retain cached conservation totals and preserve rollback after failed commits.
They no longer copy the accumulated UTXO map. Submission duplicate checks use
consensus accepted-transaction records instead of loading unrelated block bodies.
Confirmation lookups scan at most the latest 2,048 canonical blocks; older
receipts require an exact canonical height and block hash. An unknown scoped
lookup does not prove absence from all history.

The exact transaction candidate passed 49 checks on macOS and 49 in isolated
Linux. Coverage includes nonempty transfers without history iteration, late
validation failures, partial-write rollback, durable-commit rollback and reorgs.
A local 300,000-output synthetic map using a real signed 100-input payment
verified value conservation and reversal without a node or chain replay.
Production acceptance separately checked recovery, actual mining and payments.
These are observed acceptance windows, not a guarantee of uninterrupted service.

## Wallet and mining

The desktop wallet follows the public tip automatically. Public ledger data is
cached locally while private wallet balances remain separately encrypted.
Switching wallets pauses old callbacks before the selected password dialog.
Real multiwallet switching and mining-wallet balance growth were accepted.

PPLNS uses verified native share targets and preserves original allocation
windows, including when DAG rewards become final later. Historical accounting
was reconciled without inventing shares or erasing already-paid balances.
Payment preparation applies a bounded suffix to an authenticated encrypted
checkpoint. Incomplete or invalid funds and unsafe reward changes hold new
payments. Durable intents preserve the same signed transaction across retries.
The production observer checks receipts every 30 seconds and settles at six
confirmations independently of UTC new-payment batches.

An operator-triggered initial catch-up batch was signed and broadcast through
the automatic payment worker, then confirmed and settled by the production
observer. Automatic subsequent batches are enabled for UTC 00/06/12/18 with a
5 BTC-K minimum. This does not claim that every scheduled future batch has run.
The rate-limited public signed-transaction relay is open. Local signing or a
submission timeout is not proof of confirmation.

Full SOLO/PPLNS endpoints, difficulty tiers and fees are in the
[mining guide](mining_pool_access.md). Domain long-connection behavior remains
under investigation; stable direct-IP miners should retain their settings.

## Artifacts and limits

The macOS arm64 wallet was built twice in isolated roots with identical bytes.
Verify SHA256SUMS, RELEASE_MANIFEST.json and the detached release signature.
The wallet is ad-hoc signed, not Apple Developer ID signed or notarized. No
third-party security audit is claimed. Private wallets, ledgers, credentials
and operational incident archives are excluded. L2 inference, DEX, bridges and
exchange integrations remain separate, unavailable services.

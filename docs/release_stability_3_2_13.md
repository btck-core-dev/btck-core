# Stability and wallet update: 3.2.13 candidate

This update addresses live node following, wallet synchronization and pool
accounting. It does not enable PPLNS automatic payments or the public signed
transaction relay while their production holds remain in place.

## Resulting behavior

- The node bounds ordinary shallow reorganization work while retaining atomic
  rollback of UTXO, issuance and Combo state after a failed durable commit.
- Managed P2P preserves dependency requests across reconnects, bounds orphan
  pressure and avoids echoing a dependency batch back to its source. Seed-B's
  live recovery retained its journal and applied only the existing snapshot's
  suffix. No new genesis replay was required.
- The desktop wallet follows the public tip automatically. Shared public ledger
  data is cached locally; wallet keys, ownership and balances remain separately
  encrypted. Switching wallets pauses old asynchronous callbacks before asking
  for the selected wallet's password.
- PPLNS payment preparation requires a complete scan, mature inputs and bounded
  input selection. Durable intents preserve the same transaction after an
  ambiguous response; unpaid balances are settled only after confirmation.
  Native-protocol shares use the actual assigned target for accounting weight.
- PPLNS freezes the original allocation window even when a new DAG block has
  no reward yet. Later canonical reward changes update the same allocation
  atomically and conserve integer SatK. Unknown legacy windows or unsafe debits
  hold payments rather than guessing or erasing paid balances.

## Acceptance and current limits

Loopback TCP/simnet checks cover subscription, authorization, credited work,
reward conservation, duplicate retry, durable accounting and payment
confirmation. Live external ports 3332-3336 pass subscription probes. These
checks are distinct from actual production payments. The exact production
candidate passed 57 focused checks on both macOS and isolated Linux. The
approved historical accounting migration was verified offline, then applied
atomically to 2,490 production records with automatic payouts disabled.

The daily mining wallet completed its real history scan and then advanced both
height and balance automatically without pressing Sync. Real multiwallet
switching was confirmed after the password-dialog race repair.

PPLNS automatic payouts remain disabled. A configured five-coin threshold and
six-hour UTC schedule do not mean payments are active. Complete current treasury
backing and production broadcast/confirmation acceptance remain required before
an explicit payment-enablement decision. New PPLNS onboarding remains pending.

The public signed-transaction relay remains in maintenance. Local signing is
not proof of a broadcast or confirmed payment. Domain long-connection behavior
is still under investigation; currently stable direct-IP miners should retain
their configured endpoints.

The macOS arm64 candidate is locally ad-hoc signed and is not Developer ID
signed or notarized. The candidate retains the disclosed payment and relay holds; it is not a
claim of complete production payment acceptance. No private wallet data,
backup databases, credentials or operational incident archives are included.

See [mining access](mining_pool_access.md) for both modes, exact URLs, ports,
hashrate tiers, fee configuration and current payout status.

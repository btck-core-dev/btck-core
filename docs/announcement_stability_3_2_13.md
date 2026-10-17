# Stability update announcement draft

DRAFT — post only after the linked Release is published and separately authorized.

BTC-K 3.2.13 improves transaction processing, bounded reorganizations, Seed-B
native following, automatic wallet synchronization and multiwallet switching.
PPLNS share accounting and historical rewards have been reconciled. The initial
catch-up payment batch confirmed through the automatic payment worker;
subsequent automatic batches are enabled with a 5 BTC-K minimum, UTC 00/06/12/18
and six-confirmation settlement. The rate-limited transaction relay is open.

Mining interfaces:
- PPLNS: stratum+tcp://185.130.45.240:3332 — shared VarDiff, initial difficulty128.
- SOLO: stratum+tcp://185.130.45.240:3333 — below1 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3334 — typical1–3 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3335 — typical6–10 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3336 — typical20–100+ TH/s initial tier.

The official domain pool.bitcoin-k.com uses the same ports. Stable direct-IP
miners should keep their settings while domain long connections are verified.
Worker: BTC-K address plus a unique machine name. Password: x.

Release: https://github.com/btck-core-dev/btck-core/releases/tag/v3.2.13
Complete mode, fees, hashrate guidance and payment status:
https://github.com/btck-core-dev/btck-core/blob/main/docs/mining_pool_access.md

Verify artifacts against SHA256SUMS and the detached release signature. This
announcement does not claim live L2 providers, an exchange, a bridge or an
external audit. Posting order remains GitHub, Bitcointalk, X, Moltbook.

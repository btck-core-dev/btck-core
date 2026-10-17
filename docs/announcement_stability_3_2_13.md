# Stability update announcement draft

DRAFT — publish only after the release gates pass and the release URL exists.

BTC-K stability and wallet update

The update improves bounded node reorganizations, Seed-B native following and
reconnect recovery, automatic wallet synchronization and multiwallet switching.
PPLNS accounting and payment-safety changes are included; automatic payouts
remain disabled pending production payment acceptance. Historical unpaid pool rewards have been reconciled against canonical
block rewards; this accounting correction is not a payment.

Mining interfaces:
- PPLNS: stratum+tcp://185.130.45.240:3332 — payouts disabled; onboarding pending.
- SOLO: stratum+tcp://185.130.45.240:3333 — below 1 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3334 — typical 1–3 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3335 — typical 6–10 TH/s initial tier.
- SOLO: stratum+tcp://185.130.45.240:3336 — typical 20–100+ TH/s initial tier.

Official domain: pool.bitcoin-k.com, with the same ports. Current direct-IP
miners should retain their stable settings while domain long connections are
verified. Worker is the BTC-K payout address plus a unique machine name;
Password is x. Hashrate tiers guide initial difficulty, not income.

Complete mode, fee and payout status:
https://github.com/btck-core-dev/btck-core/blob/main/docs/mining_pool_access.md

Insert the verified GitHub release permalink and artifact SHA-256 before posting.
The public transaction relay remains in maintenance. This update does not
announce active PPLNS payments, L2 providers, an exchange, a bridge or an external
audit. Post in order: GitHub, Bitcointalk, X, Moltbook.

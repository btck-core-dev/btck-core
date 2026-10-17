# Official BTC-K mining endpoints

Status: 18 October 2026. SOLO and PPLNS mining are operating. PPLNS automatic
payouts are enabled after an operator-triggered first batch was signed,
broadcast and confirmed through the production automatic payment worker.
Subsequent new-payment batches run at UTC 00:00/06:00/12:00/18:00. A connection
or unpaid credit is not a confirmed payment; check the on-chain receipt.

| Mode | Current direct endpoint | Official domain endpoint | Initial difficulty tier |
|---|---|---|---|
| PPLNS | `stratum+tcp://185.130.45.240:3332` | `stratum+tcp://pool.bitcoin-k.com:3332` | Shared VarDiff endpoint; ASIC bootstrap difficulty 128 |
| SOLO | `stratum+tcp://185.130.45.240:3333` | `stratum+tcp://pool.bitcoin-k.com:3333` | Below 1 TH/s; base difficulty 128 |
| SOLO | `stratum+tcp://185.130.45.240:3334` | `stratum+tcp://pool.bitcoin-k.com:3334` | Typical 1–3 TH/s; base difficulty 4096 |
| SOLO | `stratum+tcp://185.130.45.240:3335` | `stratum+tcp://pool.bitcoin-k.com:3335` | Typical 6–10 TH/s; base difficulty 16384 |
| SOLO | `stratum+tcp://185.130.45.240:3336` | `stratum+tcp://pool.bitcoin-k.com:3336` | Typical 20–100+ TH/s; base difficulty 65536 |

The official hostname is `pool.bitcoin-k.com`, with the same ports. Historical
long-connection failures on the domain route are still under investigation;
miners already stable on the direct IP should keep their current endpoint.
These addresses reach the same server and are not independent backup pools.

The tier ranges guide initial share frequency; they are not admission limits.
For intermediate rates, start with the adjacent lower tier and observe accepted
shares and rejection rate. VarDiff adjusts afterward. The node's current target
and the ASIC firmware floor can override initial tiers so valid blocks are not
filtered out. A higher port does not improve reward probability.
1 TH/s = 1,000 GH/s.

Set Worker to `<your BTC-K wallet address>.<unique machine name>`, and Password to
`x`. Never enter the wallet unlock password in miner settings. The port selects
SOLO or PPLNS; changing a worker name or password does not change pool mode.
Keep any failover endpoints in the same mode unless deliberately switching.

SOLO assigns ordinary block rewards directly to the finder, subject to the
published fee-block policy. PPLNS assigns ordinary block income by accepted
share difficulty in a rolling 10,000-share window. It reduces individual block
variance; it does not guarantee income or returns. The two modes must remain
separately visible regardless of miner hashrate.

The configured pool fee schedule is 5% on launch days 1–7, 3% on days 8–30, and
1% afterward. The PPLNS worker has a minimum of 5 BTC-K, UTC batch
times 00:00/06:00/12:00/18:00, mature-input filtering and six-confirmation payment
settlement. Pending receipts are checked every 30 seconds independently of new-payment
batches. Mature funds, canonical rewards and successful confirmation are required;
a safety hold or incomplete treasury checkpoint prevents new payments. Balances
below the minimum remain unpaid and recorded until eligible.

HTTP status ports 3337/3338 are loopback-only monitoring interfaces, not mining
endpoints. Node RPC 16110 and P2P 16111 are not Stratum mining ports.

Use the [official pool](https://pool.bitcoin-k.com),
[participation guide](https://bitcoin-k.com/participate), and
[GitHub releases](https://github.com/btck-core-dev/btck-core/releases) for published
status. Check the payout notice before moving hashrate to PPLNS.

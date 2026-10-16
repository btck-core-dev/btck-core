# Node, pool and wallet repairs: work in progress

This branch preserves incremental source repairs. It is not a stable release,
main-branch merge, mainnet deployment approval, or an announcement of completed
real-time standby following or wallet acceptance. Do not auto-deploy it.

Included:

- Bounded empty-block paths avoiding unnecessary lifetime UTXO/chain copies.
- Durable recovery snapshots, wallet output indexing and incremental RPC paging.
- P2P dependency/retry/timeout isolation and explicit managed source-health gates.
- PPLNS atomic scans, maturity/input limits, durable payout intents, idempotent
  settlement after confirmations, and a disabled-payment operating mode.
- Desktop automatic refresh, shared public history, encrypted per-wallet state,
  independent wallet registry and bounded shallow-reorganization recovery.
- Online-worker reporting, bounded backup logic and corresponding regressions.

Deployment and acceptance boundaries:

| Area | Status at branch preparation |
|---|---|
| Empty-block performance changes | Isolated regression/differential tests passed; not installed in live mainnet daemons |
| Standby following | Interrupted by source-health incident hold; sustained near-tip following outstanding |
| Startup recovery point | A newer isolated intermediate artifact is being built; not yet accepted or installed |
| PPLNS safety module | Deployed with automatic payouts disabled; mature treasury backing and payment re-enablement outstanding |
| Desktop wallet | Local build installed; disposable-wallet tests passed, owner-unlocked real-wallet balance acceptance outstanding |
| Public transaction relay | Maintenance mode; source support is not permission to enable sending |
| Bounded backup | Implementation installed; fresh completed production backup outstanding |
| RPC stability | Recent favorable observations do not close the intermittent-timeout incident |

Excluded from this publication: credentials, wallet/key files, chain databases,
runtime evidence, private incident handoffs, host-bound one-shot activation and
recovery scripts, workstation proxy/SSH configuration, social automation and
unrelated website/announcement drafts. Generic tests use disposable credentials.

The pool frontend's address-scoped lookup and online-only worker rendering are
included with their tests; unpublished maintenance-marquee edits are excluded.
Other website status pages remain at the remote base revision, not a statement
of current runtime status. The explicit table above governs this repair branch.

Publication does not restart nodes, move funds, change miners or replace public
upgrade notices. A future production deployment requires its own approved plan.

## Reproducing the regression run

Use Python with the project's test dependencies and Node.js available on PATH
for the browser rendering regression, then run:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

Tests are local fixtures; they do not require production SSH credentials,
production wallets, a mainnet database, or source-server replay. Packaging smoke
checks use a temporary password file, not a stored deployment credential.

Branch preparation regression result: **388 tests, PASS**, 58.725seconds on
macOS/Python3.12 with Node.js on PATH. No tests were skipped. This records local
regressions, not sustained mainnet following, owner wallet acceptance or an
independent security audit. Earlier publication-copy runs caught a missing
frontend dependency, mismatched legacy webpage assertions and missing Node.js
on PATH; those integration/setup issues were corrected before this full run.

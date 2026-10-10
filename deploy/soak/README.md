# BTC-K multi-node soak gate

This collector is read-only. It must run from an independent observer after
two DEVNET nodes in separate failure domains expose `/info` through valid
HTTPS endpoints. Do not start or deploy it during the current RC4 fixed
window.

Start a new seven-day evidence set only after both nodes have passed isolated
restore checks:

```bash
python3 deploy/soak/monitor_multi_node.py \
  --node primary=https://node-a.example \
  --node secondary=https://node-b.example \
  --evidence /var/lib/btck-soak/evidence.jsonl \
  --report /var/lib/btck-soak/report.json \
  --interval 300 \
  --duration 604800
```

Production runs must not use `--allow-http`. The observer must itself have
bounded, persistent storage and time synchronization. Supervise the process
with a service manager and alert on a non-zero exit, missing samples, or a
report other than `PASS` after seven complete days.

The JSONL evidence is fsync'd after every sample. Records form a SHA-256 hash
chain; editing, removing, reordering, or splicing records causes the next load
to fail closed. Archive the evidence, final report, observer clock status,
node deployment manifests, and TLS certificate fingerprints together.

`PASS` requires all of the following for the entire window:

- the same named node set, URLs, network identity, genesis and consensus
  fingerprint from start to finish;
- at least two nodes, each with a P2P peer, safe mode off, advancing blue score,
  bounded lag, valid selected tip, bounded tips and durable checkpoints;
- issued supply exactly equal to UTXO total on every node and every sample;
- identical roots when nodes report the same checkpoint sequence;
- no failed sample and no sample gap greater than twice the configured interval.

Keep Mainnet and DEVNET launch status `NO-GO` until this report and every
earlier launch gate have independently passed. A restarted or repaired soak
starts a fresh seven-day window; do not concatenate separate windows.

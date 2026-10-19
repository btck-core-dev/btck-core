#!/usr/bin/env python3
"""Read-only HTTPS catalog availability observation; never generates inference."""
import argparse
import json
import math
import os
from pathlib import Path
import time
import urllib.request


def assess(catalog, expected):
    cards = {m['id']: m for m in catalog['data']}
    models = {}
    for model in expected:
        card = cards.get(model, {})
        online = card.get('online_workers', 0)
        models[model] = {'online_workers': online, 'status': card.get('status', 'missing')}
    return {'healthy': all(type(m['online_workers']) is int and m['online_workers'] > 0 and
                           m['status'] in ('available', 'busy') for m in models.values()),
            'models': models}


def qualification(summary, elapsed, required, interval):
    if elapsed < required: return 'RUNNING'
    return 'PASS' if summary['unhealthy_samples'] == 0 and summary['max_sample_gap_seconds'] <= interval * 3 else 'FAIL'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gateway', required=True)
    p.add_argument('--agent-config', type=Path, required=True)
    p.add_argument('--model', action='append', required=True)
    p.add_argument('--hours', type=float, default=24)
    p.add_argument('--interval', type=float, default=30)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not args.gateway.startswith('https://') or not math.isfinite(args.hours) or args.hours <= 0 or not 1 <= args.interval <= 300:
        p.error('Use HTTPS, a positive duration and an interval from 1 to 300 seconds')
    if args.agent_config.is_symlink() or (os.name != 'nt' and args.agent_config.stat().st_mode & 0o077):
        p.error('Use a private regular Agent profile')
    profile = json.loads(args.agent_config.read_text())
    if profile['gateway_url'].rstrip('/') != args.gateway.rstrip('/'):
        p.error('Gateway must match the selected Agent profile')
    token = profile['token']
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic(); previous = None
    summary = {'qualification': 'RUNNING', 'scope': 'public_catalog_availability_only',
               'started_at': time.time(), 'required_seconds': args.hours * 3600,
               'models': args.model, 'samples': 0, 'unhealthy_samples': 0,
               'max_sample_gap_seconds': 0, 'inference_generated': 0,
               'miner_control_enabled': False}
    with (args.output / 'samples.jsonl').open('x') as log:
        while True:
            now = time.monotonic()
            try:
                request = urllib.request.Request(args.gateway.rstrip('/') + '/v1/models', headers={'Authorization': 'Bearer ' + token})
                with opener.open(request, timeout=10) as response:
                    raw = response.read(1048577)
                if len(raw) > 1048576: raise ValueError('Oversized catalog')
                row = assess(json.loads(raw), args.model)
            except Exception as error:
                row = {'healthy': False, 'error_type': type(error).__name__}
            row['at'] = time.time()
            summary['samples'] += 1
            summary['unhealthy_samples'] += not row['healthy']
            if previous is not None: summary['max_sample_gap_seconds'] = max(summary['max_sample_gap_seconds'], now - previous)
            previous = now
            elapsed = time.monotonic() - started
            summary.update(elapsed_seconds=elapsed, last_sample_at=row['at'],
                           qualification=qualification(summary, elapsed, args.hours * 3600, args.interval))
            log.write(json.dumps(row) + '\n'); log.flush()
            temporary = args.output / 'summary.new'
            temporary.write_text(json.dumps(summary, indent=2))
            temporary.replace(args.output / 'summary.json')
            if summary['qualification'] != 'RUNNING': break
            time.sleep(min(args.interval, max(0, args.hours * 3600 - elapsed)))
    print(json.dumps(summary))
    return 0 if summary['qualification'] == 'PASS' else 2


if __name__ == '__main__': raise SystemExit(main())

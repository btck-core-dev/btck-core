#!/usr/bin/env python3
"""Issue a private one-use Worker invitation without restarting the gateway."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from worker.resident_worker import private_json
from gateway.worker_enrollment import WorkerEnrollment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--model', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ttl', type=int, default=86400)
    args = parser.parse_args()
    os.umask(0o077)
    config = private_json(args.config)
    if args.worker in config['workers']:
        parser.error('Worker identity is reserved by the gateway configuration')
    entry = config['enrollment']
    registry = WorkerEnrollment(entry['database'], entry['public_url'], config['models'])
    # Reserve the output before issuing so an overwrite cannot lose the only code.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            invite = registry.issue(args.worker, args.model, ttl=args.ttl)
            json.dump(invite, handle, indent=2)
            handle.flush(); os.fsync(handle.fileno())
    except BaseException:
        # Preserve any partial invitation file for operator investigation.
        raise
    print('Private invitation saved to ' + str(args.output))


if __name__ == '__main__': main()

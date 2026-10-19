#!/usr/bin/env python3
"""Summarize or extract UTF-8 documents through a pinned-receipt L2 Agent."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'sdk')]
from worker.resident_worker import private_json
from btck_agent.inference_pilot import PilotClient
from btck_agent.document_workflow import execute_job, MAX_DOCUMENT_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--job', required=True, type=Path, help='Private durable job file; use a new path per task')
    parser.add_argument('--resume', action='store_true', help='Reuse the saved request; no new task or repair call')
    parser.add_argument('--operation', choices=['summary', 'extract'])
    parser.add_argument('--document', type=Path, help='UTF-8 TXT, Markdown or JSON; PDF/OCR not supported')
    parser.add_argument('--fields', nargs='+', default=[])
    parser.add_argument('--model')
    parser.add_argument('--max-tokens', type=int, default=512)
    parser.add_argument('--max-charge-satk', type=int, default=1000000)
    args = parser.parse_args()
    if args.resume:
        if any((args.operation, args.document, args.fields, args.model)):
            parser.error('--resume uses the saved request; omit new task arguments')
    elif not all((args.operation, args.document, args.model)):
        parser.error('Provide --operation, --document and --model')
    try:
        config = private_json(args.config)
        client = PilotClient(config['gateway_url'], config['token'], config['trusted_receipt_pubkey'], timeout=130)
        document = None
        if args.document:
            with args.document.open('rb') as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 4)
            document = raw.decode('utf-8-sig')
        job = execute_job(client, args.job, operation=args.operation, document=document,
                          fields=args.fields, model=args.model, max_tokens=args.max_tokens,
                          max_charge_satk=args.max_charge_satk, resume=args.resume)
        result = job['result']
        print(json.dumps({'state': job['state'], 'request_id': job['idempotency_key'],
              'output': job.get('output'), 'validation_error': job.get('validation_error'),
              'usage': result['usage'], 'receipt_verified': True,
              'currency': result['currency'], 'job_file': str(args.job)}, ensure_ascii=False, indent=2))
        return 0 if job['state'] == 'validated' else 4
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # Do not dump credentials, documents or HTTP headers.
        print('Task incomplete (' + type(error).__name__ + '). Keep the job file and resume with the same config.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

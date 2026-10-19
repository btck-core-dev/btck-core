#!/usr/bin/env python3
"""Run signed L2 inference with a caller-owned, read-only service lookup tool."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'sdk')]
from worker.resident_worker import private_json
from btck_agent.inference_pilot import PilotClient
from btck_agent.tool_agent import ToolAgent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--prompt-file', required=True, type=Path)
    parser.add_argument('--services', required=True, type=Path, help='Caller-owned JSON object keyed by service name')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--budget', type=int, default=1_000_000)
    parser.add_argument('--max-tokens', type=int, default=1024)
    args = parser.parse_args()
    config = private_json(args.config)
    client = PilotClient(config['gateway_url'], config['token'], config['trusted_receipt_pubkey'], timeout=135)
    cards = client.models()['data']
    card = next((c for c in cards if c['id'] == args.model), None)
    if not card or not card.get('supports_tool_calls') or card['status'] != 'available':
        parser.error('Select an available model with tool routing enabled. No text-only fallback is used.')
    if args.max_tokens > card['max_output_tokens']:
        parser.error('Requested output exceeds the model endpoint limit')
    prompt_limit=max(0,min(60000,card.get('max_prompt_bytes',16384)-4096))
    if args.services.stat().st_size > 16384:
        parser.error('Service catalog must fit within 16 KiB')
    if args.prompt_file.stat().st_size > prompt_limit:
        parser.error('Prompt exceeds the selected model byte limit after tool-message reserve')
    services = json.loads(args.services.read_text(encoding='utf-8'))
    if not isinstance(services, dict) or not services or any(not isinstance(k, str) or not k for k in services):
        parser.error('Service catalog must be a nonempty JSON object')
    tools = [{'type': 'function', 'function': {'name': 'lookup_service',
              'description': 'Read an entry in the customer-owned service catalog. Available names: ' + ', '.join(services),
              'parameters': {'type': 'object', 'properties': {'name': {'type': 'string', 'enum': list(services)}},
                             'required': ['name'], 'additionalProperties': False}}}]

    def lookup(arguments):
        if set(arguments) != {'name'} or not isinstance(arguments['name'], str) or arguments['name'] not in services:
            raise ValueError('Unknown service or unsupported lookup arguments')
        return {'name': arguments['name'], 'record': services[arguments['name']]}

    result = ToolAgent(client, tools, {'lookup_service': lookup}).run(model=args.model,
        prompt=args.prompt_file.read_text(encoding='utf-8'), run_id=args.run_id, journal=args.journal,
        total_budget_satk=args.budget, max_tokens=args.max_tokens, require_initial_tool=True)
    calls = sum(len(r['choices'][0]['message'].get('tool_calls', [])) for r in result['receipts'])
    micros=sum(r['receipt']['payload']['price']['task_kcu_micros'] for r in result['receipts'])
    kcu=f'{micros//1000000}.{micros%1000000:06d}'.rstrip('0').rstrip('.')
    print(json.dumps({'answer': result['answer'], 'tool_calls': calls, 'spent_kcu': kcu,
                      'spent_development_satk': result['spent_satk'],
                      'signed_rounds': len(result['receipts']), 'journal': str(args.journal)}, ensure_ascii=False))
    if calls == 0:
        raise SystemExit('The model returned no tool calls. This is not a tool-call acceptance pass.')


if __name__ == '__main__': main()

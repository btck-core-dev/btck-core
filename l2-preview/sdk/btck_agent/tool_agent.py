"""Bounded caller-side tool loop with durable results and signed inference.

An interrupted tool execution is uncertain and is never executed again on resume.
Only explicitly supplied local functions can run; no remote shell tool is added.
"""
import hashlib
import json
import os
from pathlib import Path
import re


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


class ToolAgent:
    def __init__(self, client, definitions, handlers):
        self.client, self.definitions, self.handlers = client, definitions, handlers
        names = [tool['function']['name'] for tool in definitions]
        if len(names) != len(set(names)) or set(names) != set(handlers):
            raise ValueError('Every advertised tool must have exactly one local handler')

    @staticmethod
    def _save(path, state):
        encoded = json.dumps(state, ensure_ascii=False, indent=2).encode()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(path.name + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def run(self, **kwargs):
        path = Path(kwargs['journal'])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = path.with_name(path.name + '.lock')
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            return self._run(**kwargs)
        finally:
            lock.unlink()

    def _run(self, *, model, prompt, run_id, journal, total_budget_satk, max_tokens=1024, max_rounds=4, require_initial_tool=False):
        if type(require_initial_tool) is not bool:
            raise ValueError('Initial tool requirement must be explicit boolean')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run_id) or not 1 <= max_rounds <= 8:
            raise ValueError('Use a bounded stable run ID and at most eight rounds')
        if type(total_budget_satk) is not int or not 0 < total_budget_satk <= 10**9:
            raise ValueError('Invalid total development-credit budget')
        path = Path(journal)
        binding = {'model': model, 'prompt': prompt, 'run_id': run_id, 'tools': self.definitions,
                   'budget': total_budget_satk, 'max_tokens': max_tokens, 'max_rounds': max_rounds,
                   'gateway': self.client.base_url, 'signer': self.client.trusted_receipt_pubkey,
                   'credential_sha256': hashlib.sha256(self.client.token.encode()).hexdigest()}
        if require_initial_tool:
            binding['require_initial_tool'] = True
        if path.exists():
            if os.name == 'posix' and path.stat().st_mode & 0o077:
                raise ValueError('Agent journal must be private')
            state = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object)
            if state['binding'] != binding:
                raise ValueError('Run binding changed; use a new journal and run ID')
        else:
            state = {'binding': binding, 'messages': [{'role': 'user', 'content': prompt}],
                     'round': 0, 'spent_satk': 0, 'receipts': [], 'phase': 'inference'}
            self._save(path, state)
        # Verify prior responses before any local handler can execute on resume.
        index = 1
        for round_index, result in enumerate(state['receipts']):
            original = {'model': model, 'messages': state['messages'][:index],
                        'tools': self.definitions, 'max_tokens': max_tokens,
                        'max_charge_satk': total_budget_satk - sum(r['usage']['cost_satk'] for r in state['receipts'][:round_index])}
            if require_initial_tool and round_index == 0:
                original['tool_choice'] = 'required'
            self.client.verify_completion(result, original)
            if state['messages'][index] != result['choices'][0]['message']:
                raise ValueError('Journal assistant message differs from its signed response')
            index += 1
            while index < len(state['messages']) and state['messages'][index]['role'] == 'tool':
                index += 1
        if state['round'] != len(state['receipts']) or state['spent_satk'] != sum(r['usage']['cost_satk'] for r in state['receipts']):
            raise ValueError('Journal accounting mismatch')
        if state['phase'] in ('tools', 'tool_executing'):
            if state['pending'] != state['receipts'][-1]['choices'][0]['message'].get('tool_calls'):
                raise ValueError('Journal tools differ from signed calls')
        if state['phase'] == 'tool_executing':
            raise RuntimeError('A local tool outcome is uncertain. Inspect the journal; it will not execute again automatically.')
        while state['phase'] != 'done':
            if state['phase'] == 'inference':
                if state['round'] >= max_rounds or state['spent_satk'] >= total_budget_satk:
                    raise RuntimeError('Agent round or total budget limit reached')
                result, _ = self.client.complete(model=model, messages=state['messages'],
                    tools=self.definitions, idempotency_key=run_id + '-' + str(state['round']),
                    **({'tool_choice': 'required'} if require_initial_tool and state['round'] == 0 else {}),
                    max_tokens=max_tokens, max_charge_satk=total_budget_satk - state['spent_satk'])
                state['receipts'].append(result)
                state['spent_satk'] += result['usage']['cost_satk']
                message = result['choices'][0]['message']
                state['messages'].append(message)
                state['round'] += 1
                if result['choices'][0]['finish_reason'] == 'length':
                    state['phase'] = 'truncated'
                elif message.get('tool_calls'):
                    state.update(phase='tools', pending=message['tool_calls'], tool_index=0)
                else:
                    state.update(phase='done', answer=message['content'])
                self._save(path, state)
            elif state['phase'] == 'tools':
                while state['tool_index'] < len(state['pending']):
                    call = state['pending'][state['tool_index']]
                    fn = call['function']
                    if fn['name'] not in self.handlers:
                        raise ValueError('Model requested a tool outside the local allowlist')
                    arguments = json.loads(fn['arguments'], object_pairs_hook=_object)
                    if not isinstance(arguments, dict):
                        raise ValueError('Tool arguments must be an object')
                    state['phase'] = 'tool_executing'
                    self._save(path, state)
                    output = self.handlers[fn['name']](arguments)
                    content = json.dumps(output, ensure_ascii=False, allow_nan=False)
                    if len(content.encode()) > 16384:
                        raise ValueError('Local tool result exceeded 16 KiB')
                    state['messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
                    state['tool_index'] += 1
                    state['phase'] = 'tools'
                    self._save(path, state)
                state['phase'] = 'inference'
                self._save(path, state)
            else:
                raise RuntimeError('Previous inference was truncated; inspect the journal without automatic repair')
        # Recheck stored signed responses on resume, including all tool call arguments.
        for result in state['receipts']:
            from btck_agent.inference_receipt import verify_receipt
            if not verify_receipt(result, self.client.trusted_receipt_pubkey):
                raise ValueError('Journal receipt failed signature verification')
        return state

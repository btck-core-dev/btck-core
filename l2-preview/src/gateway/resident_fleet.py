"""Authenticated pull workers and health-derived model catalog for private L2.

Tokens identify execution sessions, not fund wallets. Lease loss never causes
an automatic second execution. All accounting uses development credits.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from collections import deque
from gateway.inference_pilot import BackendRejected, PilotError
from worker.pricing_engine import TieredPricingEngine
from worker.agent_protocol import completion_message
from gateway.service_verifier import ServiceVerifier


class ResidentFleet:
    model = None

    def __init__(self, models, workers, *, ttl=30, task_timeout=120, clock=time.monotonic):
        self.models = {}
        self.verifiers = {}
        for model, card in models.items():
            card = dict(card)
            policy = card.pop('service_validation', None)
            if policy is not None:self.verifiers[model]=ServiceVerifier(policy,model)
            if not isinstance(model, str) or not model or len(model) > 200:
                raise ValueError('Invalid model ID')
            rate = str(card['rate_kcu_per_1k'])
            TieredPricingEngine.quote_kcu_usage(1, rate, '0.05')
            context = card.get('context_tokens', 4096)
            if type(context) is not int or context < 128:
                raise ValueError('Invalid context limit')
            output = card.get('max_output_tokens', 128)
            if type(output) is not int or not 1 <= output <= min(2048, context):
                raise ValueError('Invalid output limit')
            if type(card.get('supports_tool_calls', False)) is not bool:
                raise ValueError('Invalid tool routing flag')
            prompt_limit=card.get('max_prompt_bytes',65536 if card.get('supports_tool_calls') else 16384)
            if type(prompt_limit) is not int or not 1024<=prompt_limit<=65536:
                raise ValueError('Invalid prompt byte limit')
            self.models[model] = {**card, 'id': model, 'rate_kcu_per_1k': rate,
                                  'context_tokens': context, 'max_output_tokens': output,
                                  'max_prompt_bytes': prompt_limit,
                                  'supports_tool_calls': card.get('supports_tool_calls', False),
                                  'tool_call_qualification': card.get('tool_call_qualification', 'not_verified') if card.get('supports_tool_calls') else 'not_enabled'}
        self.workers = {}
        for worker, config in workers.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', worker):
                raise ValueError('Invalid worker ID')
            if not isinstance(config['token'], str) or len(config['token']) < 32:
                raise ValueError('Use random worker tokens of at least 32 characters')
            allowed = set(config['models'])
            if not allowed or not allowed <= self.models.keys():
                raise ValueError('Worker models must be in the configured catalog')
            self.workers[worker] = {'token_hash': hashlib.sha256(config['token'].encode()).hexdigest(),
                                    'allowed': allowed, 'session': None, 'ready': set(),
                                    'heartbeat': float('-inf'), 'busy': None, 'telemetry': {}}
        hashes = [w['token_hash'] for w in self.workers.values()]
        if len(hashes) != len(set(hashes)):
            raise ValueError('Each worker needs a distinct token')
        self.ttl, self.timeout, self.clock = ttl, task_timeout, clock
        self.lock = threading.RLock()
        self.tasks = {}
        self.queues = {w: deque() for w in workers}

    def add_worker(self, name, token, models):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name) or len(token) < 32:
            raise ValueError('Invalid worker credential')
        allowed = set(models)
        if not allowed or not allowed <= self.models.keys():
            raise ValueError('Worker model is not in the catalog')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            if name in self.workers or any(w['token_hash'] == digest for w in self.workers.values()):
                raise ValueError('Worker identity or credential already exists')
            if len(self.workers) >= 256:
                raise ValueError('Preview worker limit reached')
            self.workers[name] = {'token_hash': digest, 'allowed': allowed, 'session': None,
                                  'ready': set(), 'heartbeat': float('-inf'), 'busy': None, 'telemetry': {}}
            self.queues[name] = deque()

    def supports_model(self, model):
        return isinstance(model, str) and model in self.models

    def rate_for_model(self, model):
        return self.models[model]['rate_kcu_per_1k']

    def supports_tools(self, model):
        return self.models[model].get('supports_tool_calls', False)

    def output_limit(self, model):
        return self.models[model]['max_output_tokens']

    def prompt_limit(self, model):
        return self.models[model]['max_prompt_bytes']

    def _online(self, worker):
        return self.clock() - worker['heartbeat'] < self.ttl

    def catalog(self):
        with self.lock:
            data = []
            for worker in self.workers.values():self._filter_verified(worker)
            for name, card in self.models.items():
                healthy = [w for w in self.workers.values() if self._online(w) and name in w['ready']]
                ready = sum(w['busy'] is None for w in healthy)
                data.append({**card, 'object': 'model', 'online_workers': len(healthy),
                             'available_workers': ready,
                             'status': 'available' if ready else ('busy' if healthy else 'offline')})
            return {'object': 'list', 'data': data, 'currency': 'development_satk',
                    'mainnet_funds_enabled': False}

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            for name, worker in self.workers.items():
                if hmac.compare_digest(digest, worker['token_hash']):
                    return name
        raise PilotError(401, 'worker_unauthorized', 'Invalid worker credential')

    def _session(self, worker, data):
        if not worker['session'] or not hmac.compare_digest(str(data.get('session', '')), worker['session']):
            raise PilotError(409, 'worker_session', 'Register a fresh worker session')

    def _readiness(self, worker, data):
        ready = data.get('ready_models', [])
        if not isinstance(ready, list) or any(not isinstance(m, str) for m in ready) or not set(ready) <= worker['allowed']:
            raise PilotError(400, 'worker_models', 'Worker reported an unauthorized model')
        telemetry = data.get('telemetry', {})
        if not isinstance(telemetry, dict) or len(json.dumps(telemetry)) > 8192:
            raise PilotError(400, 'worker_telemetry', 'Invalid worker telemetry')
        worker.update(reported=set(ready),ready=set(ready), heartbeat=self.clock(), telemetry=telemetry)
        self._filter_verified(worker)

    def _filter_verified(self, worker):
        now=self.clock()
        if worker.get('blocked_until',0)>now:
            worker['ready']=set();return
        worker['ready']={m for m in worker['ready'] if m not in self.verifiers or
                         worker.get('verified',{}).get(m,0)>now}

    def _verification_status(self, worker):
        models=worker.get('reported',set()) & self.verifiers.keys()
        if not models:return 'not_configured'
        if worker.get('blocked_until',0)>self.clock():return 'unavailable'
        return 'verified' if all(worker.get('verified',{}).get(m,0)>self.clock() for m in models) else 'pending'

    def _expire_verification(self, worker):
        task=self.tasks.get(worker['busy']) if worker['busy'] else None
        if task and task.get('verification') and self.clock()>task['deadline']:
            self.tasks.pop(task['id'],None)
            worker['busy']=None;worker['ready']=set()
            worker['blocked_until']=self.clock()+self.verifiers[task['request']['model']].cooldown

    def _schedule_verification(self, name, worker):
        from gateway.inference_pilot import canonical
        if worker['busy'] or worker.get('blocked_until',0)>self.clock():return
        self._filter_verified(worker)
        for model in sorted(worker.get('reported',set()) & self.verifiers.keys()):
            policy=self.verifiers[model]
            # Renew while the previous result is still valid. Leave enough time
            # for an existing customer task and the verification to finish.
            renewal_margin=min(policy.interval/2,self.timeout+policy.timeout+5)
            if worker.get('verified',{}).get(model,0)>self.clock()+renewal_margin:continue
            check=policy.choose();request=check['request']
            task_id=uuid.uuid4().hex
            task={'id':task_id,'worker':name,'session':worker['session'],'challenge':secrets.token_hex(32),
                  'request':request,'request_hash':hashlib.sha256(canonical(request)).hexdigest(),
                  'status':'QUEUED','verification':True,'expected':check['accepted_sha256'],
                  'deadline':self.clock()+policy.timeout}
            self.tasks[task_id]=task;worker['busy']=task_id;self.queues[name].append(task_id)
            break

    def handle(self, path, token, data):
        name = self.authenticate(token)
        with self.lock:
            worker = self.workers[name]
            self._expire_verification(worker)
            if path == '/v1/workers/register':
                if worker['busy']:
                    raise PilotError(409, 'worker_busy', 'A previous lease is still unresolved')
                self._readiness(worker, data)
                worker['session'] = secrets.token_hex(24)
                worker['verified']={};self._filter_verified(worker)
                return {'worker_id': name, 'session': worker['session'], 'heartbeat_seconds': 5,
                        'verification_status':self._verification_status(worker)}
            self._session(worker, data)
            if path == '/v1/workers/heartbeat':
                self._readiness(worker, data)
                return {'worker_id': name, 'busy': worker['busy'], 'accepted': True,
                        'verification_status':self._verification_status(worker)}
            if path == '/v1/workers/next':
                if not self._online(worker):
                    raise PilotError(409, 'worker_stale', 'Refresh backend readiness first')
                self._schedule_verification(name,worker)
                while self.queues[name]:
                    task = self.tasks.get(self.queues[name].popleft())
                    if task and task['status'] == 'QUEUED':
                        task['status'] = 'LEASED'
                        return {'task': {k: task[k] for k in ('id', 'challenge', 'request', 'request_hash')}}
                return {'task': None}
            if path == '/v1/workers/complete':
                task = self.tasks.get(data.get('task_id'))
                if not task or task['worker'] != name or task['session'] != worker['session']:
                    raise PilotError(403, 'task_owner', 'Task belongs to another worker or session')
                if not hmac.compare_digest(str(data.get('challenge', '')), task['challenge']):
                    raise PilotError(403, 'task_challenge', 'Task challenge mismatch')
                if task['status'] != 'LEASED':
                    raise PilotError(409, 'task_state', 'Task is expired or already completed')
                if data.get('request_hash') != task['request_hash']:
                    raise PilotError(403, 'task_request', 'Task request hash mismatch')
                if task.get('verification'):
                    model=task['request']['model'];policy=self.verifiers[model]
                    passed=False
                    try:
                        result=self.validate_result(data.get('result'),task['request'])
                        passed=not data.get('error') and any(hmac.compare_digest(policy.digest(result),x) for x in task['expected'])
                    except (ValueError,KeyError,TypeError):pass
                    worker['busy']=None;self.tasks.pop(task['id'],None)
                    if passed:worker.setdefault('verified',{})[model]=self.clock()+policy.interval
                    else:
                        worker.setdefault('verified',{}).pop(model,None)
                        worker['ready']=set();worker['blocked_until']=self.clock()+policy.cooldown
                    worker['ready']=set(worker.get('reported',set()));self._filter_verified(worker)
                    return {'accepted':True,'verification_status':self._verification_status(worker)}
                task['result'] = data.get('result')
                task['error'] = data.get('error')
                task['error_kind'] = data.get('error_kind')
                task['status'] = 'RETURNED'
                worker['busy'] = None
                task['event'].set()
                return {'accepted': True}
            raise PilotError(404, 'not_found', 'Unknown worker route')

    def complete(self, request):
        from gateway.inference_pilot import canonical
        model = request['model']
        with self.lock:
            for w in self.workers.values():self._filter_verified(w)
            candidates = [(name, w) for name, w in self.workers.items()
                          if model in w['ready'] and self._online(w) and not w['busy']]
            if not candidates:
                raise BackendRejected('No healthy resident worker is available for this model')
            name, worker = candidates[0]
            task_id = uuid.uuid4().hex
            task = {'id': task_id, 'worker': name, 'session': worker['session'],
                    'challenge': secrets.token_hex(32), 'request': request,
                    'request_hash': hashlib.sha256(canonical(request)).hexdigest(),
                    'status': 'QUEUED', 'event': threading.Event()}
            self.tasks[task_id] = task
            worker['busy'] = task_id
            self.queues[name].append(task_id)
        try:
            returned = task['event'].wait(self.timeout)
            with self.lock:
                if not returned:
                    never_leased = task['status'] == 'QUEUED'
                    task['status'] = 'EXPIRED'
                    worker['ready'] = set()
                    worker['session'] = None
                    worker['busy'] = None
                    if never_leased:
                        raise BackendRejected('Worker did not claim the task; no inference executed')
                    raise TimeoutError('Leased worker result is unknown; never re-dispatch this request')
                if task.get('error'):
                    if task.get('error_kind') == 'rejected_before_inference':
                        raise BackendRejected('Worker/backend rejected the request before a usable inference')
                    raise RuntimeError('Worker reported an uncertain inference failure')
                result = self.validate_result(task.get('result'), request)
                return {**result, 'worker_id': name, 'worker_request_id': task_id,
                        'worker_request_hash': task['request_hash']}
        finally:
            with self.lock:
                self.tasks.pop(task_id, None)
                try:self.queues[name].remove(task_id)
                except ValueError:pass

    @staticmethod
    def validate_result(result, request):
        if not isinstance(result, dict) or result.get('model') != request['model']:
            raise ValueError('Worker model mismatch')
        message = completion_message(result, request)
        text, usage = message['content'], result.get('usage', {})
        if len(json.dumps(message).encode()) > 512000:
            raise ValueError('Worker output is too large')
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            if type(usage.get(field)) is not int or not 0 < usage[field] <= 10**7:
                raise ValueError('Worker did not report measured token usage')
        if usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens'] or usage['completion_tokens'] > request['max_tokens']:
            raise ValueError('Worker usage is inconsistent')
        return {**({'tool_calls': message['tool_calls']} if 'tool_calls' in message else {}),
                'text': text, 'model': result['model'],
                'finish_reason': result.get('finish_reason','stop'),
                'usage': {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                'provider_request_id': str(result.get('provider_request_id', ''))[:200],
                'backend_evidence': result.get('backend_evidence', {})}

"""Private L2 inference pilot: real backend usage, durable development credits.

No L1 RPC, treasury, deposits, withdrawals or on-chain burn is enabled here.
The SQLite ledger records synthetic SatK-denominated development credits only.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import schnorr_sign, schnorr_verify
from worker.pricing_engine import TieredPricingEngine
from worker.agent_protocol import validate_agent_request, completion_message

CURRENCY = 'development_satk'
MAX_CREDITS = 10**12


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


class PilotError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class BackendRejected(Exception):
    """A definite provider rejection before a usable result."""


class HTTPInferenceProvider:
    """A configured OpenAI-compatible text backend; no simulated fallback."""
    def __init__(self, base_url, model, *, timeout=30, extra_body=None):
        if not base_url.startswith(('http://', 'https://')) or not model:
            raise ValueError('An explicit backend URL and model are required')
        self.base_url, self.model, self.timeout = base_url.rstrip('/'), model, timeout
        self.extra_body = extra_body or {}
        if set(self.extra_body) - {'chat_template_kwargs'}:
            raise ValueError('Only explicit chat-template options are supported')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, request):
        payload = {**self.extra_body, 'model': self.model,
                   'messages': request['messages'], 'max_tokens': request['max_tokens'],
                   'temperature': 0, 'stream': False}
        payload.update({k: request[k] for k in ('tools', 'tool_choice') if k in request})
        req = urllib.request.Request(self.base_url + '/chat/completions',
                                     canonical(payload), {'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(1024**2 + 1)
                if len(raw) > 1024**2:
                    raise ValueError('Backend response exceeded the pilot limit')
                result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise BackendRejected(f'Backend rejected the request: HTTP {exc.code}') from None
            raise RuntimeError(f'Backend unavailable: HTTP {exc.code}') from None
        choices = result.get('choices', [])
        text = choices[0].get('message', {}).get('content') if choices else None
        usage = result.get('usage', {})
        if result.get('model') != self.model:
            raise ValueError('Backend returned a different model')
        message = choices[0].get('message', {}) if choices else {}
        routed = {'text': text, 'finish_reason': (choices[0].get('finish_reason') or 'stop') if choices else None}
        if 'tool_calls' in message:
            routed['tool_calls'] = message['tool_calls']
        completion_message(routed, request)
        for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            if type(usage.get(name)) is not int or not 0 < usage[name] <= 10**7:
                raise ValueError('Backend did not provide valid measured token usage')
        if usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']:
            raise ValueError('Backend token usage is inconsistent')
        if usage['completion_tokens'] > request['max_tokens']:
            raise ValueError('Backend exceeded the requested output limit')
        return {**routed, 'usage': {k: usage[k] for k in
                ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                'finish_reason': choices[0].get('finish_reason') or 'stop',
                'model': self.model, 'provider_request_id': result.get('id', ''),
                'backend_evidence': result.get('backend_evidence', {})}


class BoundedPilotServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler, max_connections=16):
        self.connections = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, address):
        request.settimeout(5)
        if not self.connections.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()


class InferencePilot:
    def __init__(self, data_dir, provider, *, rate_kcu_per_1k='0.8', reference_price_usd='0.05', max_concurrent_requests=1):
        if type(max_concurrent_requests) is not int or not 1 <= max_concurrent_requests <= 32:
            raise ValueError('Concurrent inference limit must be an integer from 1 to 32')
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._lockfile = (self.root/'owner.lock').open('a+b')
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockfile.close()
            raise RuntimeError('This pilot ledger is already open by another server') from None
        self.provider = provider
        self.rate, self.reference_price = rate_kcu_per_1k, reference_price_usd
        TieredPricingEngine.quote_kcu_usage(1, self.rate, self.reference_price)
        self._slot = threading.BoundedSemaphore(max_concurrent_requests)
        self.database = self.root/'pilot.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS balances(account TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount>=0));
                CREATE TABLE IF NOT EXISTS agents(agent TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('issued_development_credits',0);
                CREATE TABLE IF NOT EXISTS tasks(
                    agent TEXT NOT NULL, idem TEXT NOT NULL, request_hash TEXT NOT NULL,
                    task_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
                    budget INTEGER NOT NULL CHECK(budget>=0), response TEXT, error TEXT,
                    PRIMARY KEY(agent,idem));
                CREATE TABLE IF NOT EXISTS reconciliations(
                    agent TEXT NOT NULL, idem TEXT NOT NULL, reason TEXT NOT NULL,
                    released_satk INTEGER NOT NULL, created_at INTEGER NOT NULL,
                    PRIMARY KEY(agent,idem));
            ''')
            db.execute("UPDATE tasks SET status='UNKNOWN',error='Server restarted during provider execution; no automatic retry' WHERE status='IN_FLIGHT'")
        os.chmod(self.database, 0o600)
        identity = self.root/'receipt-identity.json'
        if identity.exists():
            key = json.loads(identity.read_text())
            self.signer = StealthKeyPair(spend_priv_scalar=int(key['spend'], 16),
                                         view_priv_scalar=int(key['view'], 16))
        else:
            self.signer = StealthKeyPair()
            with os.fdopen(os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
                json.dump({'spend': format(self.signer.spend_priv, 'x'),
                           'view': format(self.signer.view_priv, 'x')}, f)
                f.flush(); os.fsync(f.fileno())

    @contextmanager
    def db(self, *, write=False):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=FULL')
        try:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self, destination):
        """Snapshot this pilot's financial DB and receipt identity together."""
        import shutil
        root=Path(destination).resolve();root.mkdir(mode=0o700,parents=True,exist_ok=False)
        with self.db() as source, closing(sqlite3.connect(root/'pilot.sqlite3')) as target:
            source.backup(target)
        (root/'pilot.sqlite3').chmod(0o600)
        shutil.copy2(self.root/'receipt-identity.json',root/'receipt-identity.json')
        (root/'receipt-identity.json').chmod(0o600)
        manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
        (root/'backup-manifest.json').write_bytes(canonical(manifest))
        return manifest

    @staticmethod
    def restore_backup(source, destination):
        import shutil
        source=Path(source).resolve();destination=Path(destination).resolve()
        manifest=json.loads((source/'backup-manifest.json').read_text())
        if set(manifest)!={'pilot.sqlite3','receipt-identity.json'}:
            raise ValueError('Unsupported pilot backup manifest')
        for name,digest in manifest.items():
            path=source/name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Pilot backup integrity mismatch')
        destination.mkdir(mode=0o700,parents=True,exist_ok=False)
        for name in manifest:
            shutil.copy2(source/name,destination/name);(destination/name).chmod(0o600)
        return destination

    def close(self):
        if not self._lockfile.closed:
            fcntl.flock(self._lockfile, fcntl.LOCK_UN)
            self._lockfile.close()

    @staticmethod
    def account(agent):
        return 'agent:' + agent

    def create_agent(self, agent, credits, *, token=None):
        if not isinstance(agent, str) or not 1 <= len(agent) <= 80:
            raise ValueError('Invalid agent identifier')
        if type(credits) is not int or not 0 <= credits <= MAX_CREDITS:
            raise ValueError('Credits must be bounded development integers')
        token = token or secrets.token_hex(32)
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('Agent tokens must contain at least 32 characters')
        with self.db(write=True) as db:
            db.execute('INSERT INTO agents VALUES(?,?)', (agent, hashlib.sha256(token.encode()).hexdigest()))
            db.execute('INSERT INTO balances VALUES(?,?)', (self.account(agent), credits))
            db.execute("UPDATE meta SET value=value+? WHERE key='issued_development_credits'", (credits,))
        return token

    def authenticate(self, token):
        if not token:
            raise PilotError(401, 'unauthorized', 'Agent authentication is required')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            row = db.execute('SELECT agent FROM agents WHERE token_hash=?', (digest,)).fetchone()
        if row is None:
            raise PilotError(401, 'unauthorized', 'Agent authentication failed')
        return row['agent']

    def balance(self, agent):
        with self.db() as db:
            available = db.execute('SELECT amount FROM balances WHERE account=?', (self.account(agent),)).fetchone()
            held = db.execute("SELECT COALESCE(SUM(budget),0) FROM tasks WHERE agent=? AND status IN ('IN_FLIGHT','UNKNOWN')", (agent,)).fetchone()[0]
        return {'currency': CURRENCY, 'available_satk': available[0] if available else 0, 'held_satk': held}

    def task(self, agent, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE agent=? AND idem=?', (agent, key)).fetchone()
        if not row:
            raise PilotError(404, 'not_found', 'Task not found for this agent')
        return dict(row)

    def conservation(self):
        with self.db() as db:
            issued = db.execute("SELECT value FROM meta WHERE key='issued_development_credits'").fetchone()[0]
            balances = db.execute('SELECT COALESCE(SUM(amount),0) FROM balances').fetchone()[0]
            held = db.execute("SELECT COALESCE(SUM(budget),0) FROM tasks WHERE status IN ('IN_FLIGHT','UNKNOWN')").fetchone()[0]
        return {'issued': issued, 'balances': balances, 'held': held, 'conserved': issued == balances + held}

    def validate(self, body, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise PilotError(400, 'idempotency_required', 'A bounded Idempotency-Key is required')
        if not isinstance(body, dict) or set(body) - {'model','messages','max_tokens','max_charge_satk','tools','tool_choice'}:
            raise PilotError(400, 'invalid_request', 'Unsupported request fields')
        supported = (self.provider.supports_model(body.get('model'))
                     if hasattr(self.provider, 'supports_model') else body.get('model') == self.provider.model)
        if not supported:
            raise PilotError(400, 'unsupported_model', 'Select the configured real backend model')
        try:
            validate_agent_request(body)
        except (ValueError, RecursionError) as error:
            raise PilotError(400, 'invalid_messages', str(error)) from None
        if body.get('tools') and not (hasattr(self.provider, 'supports_tools') and self.provider.supports_tools(body['model'])):
            raise PilotError(400, 'tools_not_enabled', 'Select a model endpoint enabled for tool routing')
        # Apply approved model limits before reserving any customer budget.
        prompt_limit=(self.provider.prompt_limit(body['model']) if hasattr(self.provider,'prompt_limit')
                      else (65536 if body.get('tools') else 16384))
        prompt_bytes=len(json.dumps({'messages':body['messages'],'tools':body.get('tools')},ensure_ascii=False).encode())
        if prompt_bytes > prompt_limit:
            raise PilotError(400, 'prompt_limit', 'Prompt exceeded the pilot limit')
        output_limit = self.provider.output_limit(body['model']) if hasattr(self.provider, 'output_limit') else 128
        for name, low, high in [('max_tokens',1,output_limit),('max_charge_satk',1,10**9)]:
            if type(body.get(name)) is not int or not low <= body[name] <= high:
                raise PilotError(400, 'invalid_budget', f'{name} must be an integer in {low}..{high}')
        return hashlib.sha256(canonical(body)).hexdigest()

    def complete(self, agent, key, body):
        request_hash = self.validate(body, key)
        with self.db() as db:
            existing = db.execute('SELECT * FROM tasks WHERE agent=? AND idem=?', (agent,key)).fetchone()
        if existing:
            return self.replay(existing, request_hash)
        if not self._slot.acquire(blocking=False):
            raise PilotError(503, 'provider_busy', 'The configured inference capacity is busy')
        try:
            with self.db(write=True) as db:
                existing = db.execute('SELECT * FROM tasks WHERE agent=? AND idem=?', (agent,key)).fetchone()
                if existing:
                    return self.replay(existing, request_hash)
                budget = body['max_charge_satk']
                changed = db.execute('UPDATE balances SET amount=amount-? WHERE account=? AND amount>=?', (budget,self.account(agent),budget))
                if changed.rowcount != 1:
                    raise PilotError(402, 'insufficient_budget', 'Insufficient available development credits')
                task_id = 'pilot_' + uuid.uuid4().hex
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)', (agent,key,request_hash,task_id,'IN_FLIGHT',budget,None,None))
            try:
                result = self.provider.complete(body)
            except BackendRejected as exc:
                self.fail(agent,key,'FAILED',str(exc),release=True)
                raise PilotError(502, 'provider_rejected', str(exc)) from None
            except Exception:
                self.fail(agent,key,'UNKNOWN','Provider outcome/usage is uncertain; no automatic retry',release=False)
                raise PilotError(502, 'provider_unknown', 'Provider outcome is uncertain; inspect the existing task') from None
            try:
                message = completion_message(result, body)
            except (ValueError, RecursionError):
                self.fail(agent,key,'UNKNOWN','Invalid provider result; usage uncertain',release=False)
                raise PilotError(502, 'provider_unknown', 'Provider result is invalid; inspect the existing task') from None
            usage = result['usage']
            units = usage['prompt_tokens'] + (3*usage['completion_tokens']+1)//2
            rate = self.provider.rate_for_model(body['model']) if hasattr(self.provider, 'rate_for_model') else self.rate
            quote = TieredPricingEngine.quote_kcu_usage(units, rate, self.reference_price)
            cost = quote['amount_satk']
            if cost > budget:
                self.fail(agent,key,'FAILED','Measured charge exceeded the authorized budget; no credits charged',release=True)
                raise PilotError(402, 'budget_exceeded', 'Measured charge exceeded the authorized budget; no credits charged')
            split = TieredPricingEngine.calculate_revenue_split(cost, year=1)
            distribution = {name: split[name+'_satk'] for name in ('miner','standby','combo','burn','eco')}
            assert sum(distribution.values()) == cost
            receipt = {'task_id':task_id,'agent':agent,'request_hash':request_hash,
                       'result_sha256':hashlib.sha256((result.get('text') or '').encode()).hexdigest(),
                       'finish_reason':result.get('finish_reason','stop'),
                       'model':result['model'],'provider_request_id':result.get('provider_request_id',''),
                       'worker_id':result.get('worker_id'),
                       'backend_evidence':result.get('backend_evidence',{}),
                       'usage':usage,'cost_satk':cost,'pricing_units':units,'price':quote,
                       'currency':CURRENCY,'allocation_year':1,'distribution':distribution,
                       'burn_status':'reserved_development_credits_only','created_at':int(time.time())}
            if 'tool_calls' in message:
                receipt['message_sha256'] = hashlib.sha256(canonical(message)).hexdigest()
            digest = hashlib.sha256(canonical(receipt)).digest()
            signed = {'payload':receipt,'signer_pubkey':self.signer.spend_pub_bytes.hex(),
                      'signature':schnorr_sign(self.signer.spend_priv,digest).hex()}
            response = {'id':task_id,'object':'chat.completion','model':result['model'],
                        'choices':[{'index':0,'message':message,'finish_reason':result.get('finish_reason','stop')}],
                        'usage':{**usage,'cost_satk':cost,'pricing_units':units},'receipt':signed,
                        'currency':CURRENCY,'mainnet_funds_enabled':False}
            with self.db(write=True) as db:
                current = db.execute('SELECT status FROM tasks WHERE agent=? AND idem=?',(agent,key)).fetchone()
                if current['status'] != 'IN_FLIGHT':
                    raise RuntimeError('Task state changed during provider execution')
                db.execute('UPDATE balances SET amount=amount+? WHERE account=?',(budget-cost,self.account(agent)))
                for name, amount in distribution.items():
                    account = ('worker:'+result['worker_id'] if name == 'miner' and result.get('worker_id') else 'allocation:'+name)
                    db.execute('INSERT INTO balances VALUES(?,?) ON CONFLICT(account) DO UPDATE SET amount=amount+excluded.amount',(account,amount))
                db.execute("UPDATE tasks SET status='SETTLED',response=? WHERE agent=? AND idem=?",(canonical(response).decode(),agent,key))
            return response, False
        finally:
            self._slot.release()

    @staticmethod
    def replay(row, request_hash):
        if row['request_hash'] != request_hash:
            raise PilotError(409,'idempotency_conflict','This key belongs to a different request')
        if row['status'] == 'SETTLED':
            return json.loads(row['response']), True
        raise PilotError(409,'task_'+row['status'].lower(),'Inspect the existing task; it will not be executed again')

    def fail(self, agent, key, status, error, *, release):
        with self.db(write=True) as db:
            row=db.execute('SELECT status,budget FROM tasks WHERE agent=? AND idem=?',(agent,key)).fetchone()
            if row['status'] != 'IN_FLIGHT':
                raise RuntimeError('Task state changed before failure recording')
            if release:
                db.execute('UPDATE balances SET amount=amount+? WHERE account=?',(row['budget'],self.account(agent)))
            db.execute('UPDATE tasks SET status=?,error=? WHERE agent=? AND idem=?',(status,error,agent,key))

    def waive_unknown(self, agent, key, reason):
        """Operator-only development-credit waiver; never exposed through HTTP.

        The operator accepts any unbilled compute cost and releases the hold.
        This does not prove a worker failed to execute or authorize real funds.
        """
        if not isinstance(reason,str) or not 10 <= len(reason) <= 2000:
            raise ValueError('A documented reconciliation reason/evidence reference is required')
        with self.db(write=True) as db:
            row=db.execute('SELECT status,budget FROM tasks WHERE agent=? AND idem=?',(agent,key)).fetchone()
            if not row or row['status'] != 'UNKNOWN':
                raise PilotError(409,'reconciliation_state','Only an UNKNOWN development task may be waived')
            db.execute('INSERT INTO reconciliations VALUES(?,?,?,?,?)',(agent,key,reason,row['budget'],int(time.time())))
            db.execute('UPDATE balances SET amount=amount+? WHERE account=?',(row['budget'],self.account(agent)))
            db.execute("UPDATE tasks SET status='WAIVED',error=? WHERE agent=? AND idem=?",(reason,agent,key))
        return {'agent':agent,'status':'WAIVED','released_development_satk':row['budget']}

    def make_server(self, port=0):
        pilot=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,status,body,extra=None):
                raw=canonical(body);self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                for k,v in (extra or {}).items():self.send_header(k,v)
                self.end_headers();self.wfile.write(raw)
            def agent(self):
                auth=self.headers.get('Authorization','')
                return pilot.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
            def do_POST(self):
                try:
                    is_worker=self.path.startswith('/v1/workers/') and hasattr(pilot.provider,'handle')
                    is_join=self.path=='/v1/join/redeem' and getattr(pilot,'enrollment',None) is not None
                    if self.path!='/v1/chat/completions' and not is_worker and not is_join:raise PilotError(404,'not_found','Route not found')
                    if not is_worker and not is_join:agent=self.agent()
                    try:length=int(self.headers.get('Content-Length','0'))
                    except ValueError:raise PilotError(400,'request_size','Invalid body length') from None
                    limit=8192 if is_join else (1048576 if is_worker else 131072)
                    if not 1<=length<=limit:raise PilotError(400,'request_size','Invalid body length')
                    self.connection.settimeout(5)
                    try:body=json.loads(self.rfile.read(length))
                    except (ValueError,TimeoutError):raise PilotError(400,'invalid_json','Invalid JSON body') from None
                    if is_join:
                        self.respond(200,pilot.enrollment.redeem(body,pilot.provider));return
                    if is_worker:
                        if not isinstance(body,dict):raise PilotError(400,'invalid_json','An object is required')
                        auth=self.headers.get('Authorization','')
                        token=auth[7:] if auth.startswith('Bearer ') else ''
                        reply=pilot.provider.handle(self.path,token,body)
                        if self.path.endswith('/heartbeat'):
                            with pilot.db() as db:
                                row=db.execute('SELECT amount FROM balances WHERE account=?',('worker:'+reply['worker_id'],)).fetchone()
                            reply['earned_development_satk']=row[0] if row else 0
                            allowed=pilot.provider.workers[reply['worker_id']]['allowed']
                            reply['pricing']={'unit':'KCU','weighted_output_multiplier':'1.5',
                                'rates':{m:pilot.provider.rate_for_model(m) for m in sorted(allowed)},
                                'settlement_currency':CURRENCY}
                        self.respond(200,reply);return
                    response,replayed=pilot.complete(agent,self.headers.get('Idempotency-Key',''),body)
                    self.respond(200,response,{'X-Idempotent-Replay':'true' if replayed else 'false'})
                except PilotError as e:self.respond(e.status,{'error':{'code':e.code,'message':str(e)}})
                except Exception:self.respond(500,{'error':{'code':'internal_error','message':'Inspect the existing task before retrying'}})
            def do_GET(self):
                try:
                    if self.path=='/health':
                        self.respond(200,{'service':'BTC-K L2 preview','currency':CURRENCY,'mainnet_funds_enabled':False});return
                    if self.path=='/' and hasattr(pilot.provider,'catalog'):
                        raw=(Path(__file__).resolve().parents[2]/'web'/'l2_pilot.html').read_bytes()
                        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                        self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                        self.end_headers();self.wfile.write(raw);return
                    agent=self.agent()
                    if self.path=='/v1/models' and hasattr(pilot.provider,'catalog'):
                        body=pilot.provider.catalog();body['btck_reference_price_usd']=pilot.reference_price
                        body['receipt_signer_pubkey']=pilot.signer.spend_pub_bytes.hex()
                    elif self.path=='/v1/balance':body=pilot.balance(agent)
                    elif self.path.startswith('/v1/tasks/'):
                        from urllib.parse import unquote
                        row=pilot.task(agent,unquote(self.path[len('/v1/tasks/'):]))
                        body={k:row[k] for k in ('task_id','status','budget','error')}
                    else:raise PilotError(404,'not_found','Route not found')
                    self.respond(200,body)
                except PilotError as e:self.respond(e.status,{'error':{'code':e.code,'message':str(e)}})
                except Exception:self.respond(500,{'error':{'code':'internal_error','message':'Request failed'}})
        server=BoundedPilotServer(('127.0.0.1',port),Handler)
        return server


def verify_receipt(response, trusted_pubkey):
    try:
        signed=response['receipt'];receipt=signed['payload']
        if signed['signer_pubkey'] != trusted_pubkey:
            return False
        digest=hashlib.sha256(canonical(receipt)).digest()
        valid=schnorr_verify(bytes.fromhex(trusted_pubkey),digest,bytes.fromhex(signed['signature']))
        message=response['choices'][0]['message']
        text=message['content'] or ''
        if 'message_sha256' in receipt or 'tool_calls' in message:
            if receipt.get('message_sha256') != hashlib.sha256(canonical(message)).hexdigest():
                return False
        return bool(valid and receipt['result_sha256']==hashlib.sha256(text.encode()).hexdigest()
                    and receipt['task_id']==response['id']
                    and receipt.get('finish_reason','stop')==response['choices'][0]['finish_reason']
                    and receipt['usage']=={k:response['usage'][k] for k in ('prompt_tokens','completion_tokens','total_tokens')}
                    and receipt['cost_satk']==response['usage']['cost_satk']
                    and receipt['pricing_units']==response['usage']['pricing_units']
                    and receipt['model']==response['model']
                    and response['currency']==receipt['currency']==CURRENCY
                    and response['mainnet_funds_enabled'] is False)
    except (KeyError,IndexError,TypeError,ValueError):
        return False

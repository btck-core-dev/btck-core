"""L2 worker for a miner-owned resident model.

Default mode observes mining without control. Explicit pause_on_request mode uses
an independent identity-bound pause lease and restores mining after inference.
Only dedicated Worker credentials are used; no BTC-K wallet keys are required.
"""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import csv
import getpass
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def windows_process_birth(pid):
    """Read process identity without using os.kill (which terminates on Windows)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        if times[1].dwLowDateTime or times[1].dwHighDateTime:  # Exited process
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def gpu_processes():
    raw = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,process_name',
                                   '--format=csv,noheader,nounits'], timeout=4, stderr=subprocess.DEVNULL).decode()
    found = {}
    for row in csv.reader(raw.splitlines()):
        if len(row) == 2 and row[0].strip().isdigit():
            pid = int(row[0].strip())
            name = row[1].strip().replace('\\', '/').rsplit('/', 1)[-1]
            found[pid] = {'pid': pid, 'name': name}
    return list(found.values())


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DefiniteRejection(Exception):
    """Local admission failure or an explicit backend HTTP 4xx rejection."""


def private_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Use a private regular configuration file')
    if os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Configuration must have mode 0600')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'token' and (not isinstance(item, str) or item.startswith('REPLACE_')):
                    raise ValueError('Replace example credentials with private random tokens')
                check(item)
        elif isinstance(value, list):
            for item in value:check(item)
    check(data)
    return data


class ResidentWorker:
    def __init__(self, config, *, probe=None):
        self.config = config
        selected=config.get('gpu_indices',[0])
        if not isinstance(selected,list) or not selected or any(type(i) is not int or i<0 for i in selected) or len(set(selected))!=len(selected):
            raise ValueError('GPU indices must be distinct non-negative integers')
        self.gateway = config['gateway_url'].rstrip('/')
        self.backend = config['backend_url'].rstrip('/')
        for url in (self.gateway, self.backend):
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or not parsed.hostname:
                raise ValueError('Invalid explicit endpoint')
            if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise ValueError('Plain HTTP is allowed only through a localhost SSH tunnel')
        if urlsplit(self.backend).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('The resident model backend must be local')
        if not isinstance(config['token'], str) or len(config['token']) < 32:
            raise ValueError('A dedicated random worker token is required')
        self.model = config['model']
        self.backend_model = config.get('backend_model', self.model)
        self.session = None
        self.stop = threading.Event()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects())
        self.probe = probe or self.observe_hardware
        self.calls = 0
        self.earned = 0
        self.verification_status = 'not_configured'
        self.pricing = {}
        self._guard_lock = threading.Lock()
        self._hashrate_lock = threading.Lock()
        self._hashrate_low_since = None
        self.hashrate_grace_seconds = float(config.get('hashrate_grace_seconds', 0))
        if not math.isfinite(self.hashrate_grace_seconds) or not 0 <= self.hashrate_grace_seconds <= 600:
            raise ValueError('Hashrate grace must be between 0 and 600 seconds')
        self.last_ready = False
        self.last_gate = 'starting'
        self.status_path = Path(config['status_file']) if config.get('status_file') else None
        self._last_summary = None
        self.telemetry = {}
        self.miner_name = config.get('miner_process_name')
        if self.miner_name is not None:
            if not isinstance(self.miner_name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', self.miner_name):
                raise ValueError('Use an exact miner executable basename')
            if config.get('miner_pid'):
                raise ValueError('Choose automatic miner name or a fixed PID, not both')
        self.miner_birth = self._miner_birth()
        if config.get('miner_pid') and self.miner_birth is None:
            raise ValueError('The configured existing miner is not running')

    def _miner_birth(self, pid=None):
        pid = pid if pid is not None else self.config.get('miner_pid')
        if not pid:
            return None
        if type(pid) is not int or pid <= 1:
            raise ValueError('Invalid miner PID')
        try:
            if os.name == 'nt':
                return windows_process_birth(pid)
            path = Path('/proc')/str(pid)/'stat'
            if Path('/proc').is_dir():
                return path.read_text().rsplit(')', 1)[1].split()[19]
            try:os.kill(pid, 0)  # Read-only existence check, never a control signal.
            except PermissionError:pass  # Existing miner may run as another OS user.
            return str(pid)
        except (OSError, IndexError):
            return None

    def miner_status(self):
        if self.miner_name:
            candidates = [p['pid'] for p in gpu_processes() if p['name'].casefold() == self.miner_name.casefold()]
            if len(candidates) != 1:
                return False, {'miner_pid': None, 'miner_alive': False, 'miner_detection':
                               'missing' if not candidates else 'ambiguous', 'miner_candidates': len(candidates)}
            pid = candidates[0]
            birth = self._miner_birth(pid)
            return birth is not None, {'miner_pid': pid, 'miner_alive': birth is not None,
                                      'miner_detection': 'automatic', 'miner_birth': birth}
        if self.config.get('miner_pid'):
            alive = self._miner_birth() == self.miner_birth
            return alive, {'miner_pid': self.config['miner_pid'], 'miner_alive': alive, 'miner_detection': 'pinned'}
        return True, {'miner_pid': None, 'miner_detection': 'not_configured'}

    def http(self, url, body=None, *, token=None, timeout=5):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer '+token
        request = urllib.request.Request(url, canonical(body) if body is not None else None, headers)
        with self.opener.open(request, timeout=timeout) as response:
            raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError('Response too large')
            return json.loads(raw)

    def observe_hardware(self):
        selected = self.config.get('gpu_indices', [0])
        raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.free,temperature.gpu',
                                       '--format=csv,noheader,nounits'], timeout=4, stderr=subprocess.DEVNULL).decode()
        gpus = []
        for line in raw.strip().splitlines():
            index, name, free, temp = [v.strip() for v in line.split(',')]
            if int(index) in selected:
                gpus.append({'index': int(index), 'name': name, 'free_mib': int(free), 'temperature_c': int(temp)})
        if len(gpus) != len(selected) or not selected:
            raise ValueError('Configured GPUs not present')
        ok = all(g['free_mib'] >= self.config.get('min_free_mib', 512) and
                 g['temperature_c'] < self.config.get('max_temperature_c', 80) for g in gpus)
        alive, miner = self.miner_status()
        telemetry = {'gpus': gpus, 'miner_managed': False, **miner}
        ok = ok and alive
        if self.config.get('mining_mode') == 'pause_on_request':
            telemetry.update(miner_managed=True, mining_mode='pause_on_request')
            from .mining_lease import read_state, guard_alive
            lease=read_state(self.config)
            paused=bool(lease and lease.get('phase') in ('pausing','paused','resuming') and
                        lease.get('miner_pid')==miner.get('miner_pid') and guard_alive(lease))
            telemetry['mining_paused_for_inference']=paused
            # A stopped process remains alive; its API cannot respond until resumed.
            # Temperature, free memory and process identity checks stay mandatory.
            return ok, telemetry
        if self.config.get('pearl_api_url'):
            parsed = urlsplit(self.config['pearl_api_url'])
            if parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise ValueError('Only the local Pearl API may be read')
            status = self.http(self.config['pearl_api_url'], timeout=3)
            pearl = next(a for a in status['algorithms'] if a['name'] == 'pearlhash')
            rate = float(pearl['hashrate']['1min'])
            telemetry.update(pearl_hashrate_hs=rate, pearl_accepted=pearl['shares']['accepted'],
                             pearl_rejected=pearl['shares']['rejected'])
            floor = self.config.get('min_pearl_hashrate_hs', 0)
            with self._hashrate_lock:
                now = time.monotonic()
                valid = math.isfinite(rate) and rate > 0
                if valid and rate >= floor:
                    self._hashrate_low_since = None
                elif self._hashrate_low_since is None:
                    self._hashrate_low_since = now
                elapsed = 0 if self._hashrate_low_since is None else now - self._hashrate_low_since
                transient = valid and rate < floor and elapsed < self.hashrate_grace_seconds
                telemetry.update(hashrate_transient=transient, hashrate_low_seconds=round(elapsed, 2),
                                 hashrate_grace_seconds=self.hashrate_grace_seconds, hashrate_recovery_floor_hs=floor)
                ok = ok and valid and (rate >= floor or transient)
        return ok, telemetry

    def readiness(self):
        with self._guard_lock:
            try:
                available = self.http(self.backend+'/models')
                loaded = self.backend_model in [m['id'] for m in available.get('data', [])]
                safe, telemetry = self.probe()
                self.last_ready = loaded and safe
                self.last_gate = 'ready' if self.last_ready else ('backend_model_missing' if not loaded else 'resource_or_mining_gate')
            except Exception:
                telemetry = {}
                self.last_ready = False
                self.last_gate = 'backend_or_probe_unavailable'
            self.telemetry = telemetry
            return {'ready_models': [self.model] if self.last_ready else [],
                    'telemetry': {**telemetry, 'gate': self.last_gate, 'provider_calls': self.calls}}

    def post(self, route, body):
        return self.http(self.gateway+'/v1/workers/'+route,
                         {**body, 'session': self.session}, token=self.config['token'])

    def register(self):
        response = self.post('register', self.readiness())
        self.session = response['session']
        self.worker_id = response['worker_id']
        self.verification_status = response.get('verification_status', 'not_configured')
        self.write_status()

    def write_status(self):
        summary = (self.last_gate, self.calls, self.earned)
        if summary != self._last_summary:
            print(f'{self.model} | {self.last_gate} | calls={self.calls} | earned={self.earned} development SatK', flush=True)
            self._last_summary = summary
        if self.status_path is None:
            return
        self.status_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {'worker_id': getattr(self, 'worker_id', None), 'model': self.model,
                'ready': self.last_ready, 'gate': self.last_gate, 'provider_calls': self.calls,
                'earned_development_satk': self.earned, 'mainnet_funds_enabled': False,
                'miner_control_enabled': self.config.get('mining_mode') == 'pause_on_request',
                'telemetry': self.telemetry, 'at': time.time()}
        data['verification_status'] = self.verification_status
        data['pricing'] = self.pricing
        temporary = self.status_path.with_suffix('.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600), 'w') as handle:
            json.dump(data, handle);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary, self.status_path)

    def heartbeat(self):
        response = self.post('heartbeat', self.readiness())
        self.earned = response.get('earned_development_satk', 0)
        self.verification_status = response.get('verification_status', 'not_configured')
        self.pricing = response.get('pricing', {})
        self.write_status()

    def execute(self, task):
        # The server sends the full canonical request bound into its nonce lease.
        request = task['request']
        if hashlib.sha256(canonical(request)).hexdigest() != task['request_hash'] or request['model'] != self.model:
            raise ValueError('Task binding mismatch')
        if not self.readiness()['ready_models']:
            raise DefiniteRejection('Resource gate closed before inference')
        payload = {'model': self.backend_model, 'messages': request['messages'], 'max_tokens': request['max_tokens'],
                   'temperature': 0, 'stream': False}
        payload.update({k: request[k] for k in ('tools', 'tool_choice') if k in request})
        if self.config.get('chat_template_kwargs'):
            payload['chat_template_kwargs'] = self.config['chat_template_kwargs']
        backend_started = False
        try:
            context = nullcontext()
            if self.config.get('mining_mode') == 'pause_on_request':
                from .mining_lease import MiningLease
                alive, miner = self.miner_status()
                if not alive or not miner.get('miner_pid'):
                    raise DefiniteRejection('Miner identity unavailable before pause')
                context = MiningLease(self.config, miner['miner_pid'],
                                      self._miner_birth(miner['miner_pid']), self.miner_name)
            with context:
                self.calls += 1
                backend_started = True
                response = self.http(self.backend+'/chat/completions', payload,
                                     timeout=self.config.get('inference_timeout', 90))
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise DefiniteRejection('Backend rejected the request') from None
            raise
        except Exception:
            if not backend_started:
                raise DefiniteRejection('Miner pause admission failed; backend not called') from None
            raise
        if response.get('model') != self.backend_model:
            raise ValueError('Backend returned a different local model')
        message = response['choices'][0]['message']
        # Compatible backends serialize absent calls as null or an empty array.
        # Keep malformed nonempty values for strict protocol rejection downstream.
        return {**({'tool_calls': message['tool_calls']} if message.get('tool_calls') not in (None, []) else {}),
                'model': self.model, 'text': message.get('content'),
                'finish_reason': response['choices'][0].get('finish_reason') or 'stop',
                'usage': response.get('usage', {}), 'provider_request_id': response.get('id', ''),
                'backend_evidence': {**response.get('backend_evidence', {}),
                                     'resident_model': True,
                                     'miner_control_enabled': self.config.get('mining_mode') == 'pause_on_request',
                                     'backend_model': self.backend_model}}

    def run(self):
        while not self.stop.is_set():
            try:self.register();break
            except urllib.error.HTTPError as error:
                if error.code in (400,401,403,404):raise
            except (urllib.error.URLError,TimeoutError,ConnectionError):pass
            self.last_ready=False;self.last_gate='gateway_unavailable';self.write_status()
            self.stop.wait(2)
        if self.stop.is_set():return
        def pulse():
            while not self.stop.wait(5):
                try:self.heartbeat()
                except Exception:pass  # Main loop re-registers after session/gateway loss.
        heart = threading.Thread(target=pulse, daemon=True);heart.start()
        try:
            while not self.stop.is_set():
                try:
                    task = self.post('next', {}).get('task')
                    if task:
                        body = {'task_id': task['id'], 'challenge': task['challenge'], 'request_hash': task['request_hash']}
                        try:body['result'] = self.execute(task)
                        except DefiniteRejection:
                            body.update(error='Local/backend admission rejected',error_kind='rejected_before_inference')
                        except Exception:body['error'] = 'Local execution outcome unavailable; manual reconciliation required'
                        # If this POST is lost, never execute the leased task again.
                        self.post('complete', body)
                        self.heartbeat()
                    else:self.stop.wait(.25)
                except Exception:
                    self.last_ready=False;self.last_gate='gateway_or_session_unavailable';self.write_status()
                    self.stop.wait(2)
                    try:self.register()
                    except Exception:pass
        finally:
            self.stop.set();heart.join(6)
            # Remove availability promptly on graceful shutdown. TTL covers crashes.
            try:self.post('heartbeat', {'ready_models': [], 'telemetry': {'gate': 'stopped'}})
            except Exception:pass
            self.last_ready=False;self.last_gate='stopped';self.write_status()



def inventory():
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.free,temperature.gpu',
                                   '--format=csv,noheader,nounits'], timeout=4, stderr=subprocess.DEVNULL).decode()
    gpus = []
    for row in csv.reader(raw.splitlines()):
        index, name, free, temperature = [v.strip() for v in row]
        gpus.append({'index': int(index), 'name': name, 'free_mib': int(free), 'temperature_c': int(temperature)})
    return {'gpus': gpus, 'gpu_processes': gpu_processes(), 'miner_control_enabled': False}


def configure(path, *, ask=input, secret=getpass.getpass):
    """Write a new private config only; never install models or start services."""
    path = Path(path).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('Configuration already exists; use a new path or edit it explicitly')
    report = inventory()
    print(json.dumps(report, indent=2))
    def prompt(label, default):
        return ask(label + ' [' + str(default) + ']: ').strip() or str(default)
    gateway = prompt('Gateway URL (use your operator TLS endpoint or local SSH tunnel)', 'http://127.0.0.1:18088')
    backend = prompt('Existing local model backend URL', 'http://127.0.0.1:18085/v1')
    token = secret('Dedicated Worker access token (hidden): ').strip()
    if len(token) < 32 or token.startswith('REPLACE_'):
        raise ValueError('Use a dedicated random Worker token, not an example')
    model = prompt('Approved model ID (use your operator profile)', '')
    if not model:
        raise ValueError('Choose an explicit approved model ID')
    indices = [int(x.strip()) for x in prompt('GPU indices, comma-separated', '0').split(',')]
    if not set(indices) <= {g['index'] for g in report['gpus']}:
        raise ValueError('Selected GPU is not installed')
    miner = prompt('Miner executable basename for automatic PID detection', 'SRBMiner-MULTI')
    pearl_url = prompt('Local Pearl API URL', 'http://127.0.0.1:21550')
    # Explicit rate selection: no silent relaxation of an existing mining gate.
    minimum = float(prompt('Minimum aggregate Pearl hashrate (TH/s; set from your own baseline)', '0'))
    if not math.isfinite(minimum) or not minimum > 0:
        raise ValueError('Set a positive mining hashrate floor from your own baseline')
    config = {'gateway_url': gateway, 'backend_url': backend, 'token': token, 'model': model,
              'gpu_indices': indices, 'miner_process_name': miner, 'pearl_api_url': pearl_url,
              'min_pearl_hashrate_hs': minimum * 1e12, 'min_free_mib': 512, 'max_temperature_c': 80,
              'inference_timeout': 90, 'status_file': str(path.with_name(path.stem + '-status.json'))}
    worker = ResidentWorker(config)
    parsed = urlsplit(pearl_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password:
        raise ValueError('Use the local Pearl HTTP API')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, indent=2); handle.flush(); os.fsync(handle.fileno())
    print('Private configuration saved to ' + str(path))
    print('Run --config <file> --check before starting. No service or model was started.')


def saved_status(config):
    path = Path(config['status_file'])
    data = json.loads(path.read_text(encoding='utf-8'))
    age = max(0, time.time() - data['at'])
    data['age_seconds'] = round(age, 1)
    if age > 15:
        data['ready'] = False
        data['gate'] = 'status_stale'
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--inventory', action='store_true', help='List GPUs and process basenames, without credentials')
    modes.add_argument('--setup', action='store_true', help='Create a new private config interactively; no services started')
    modes.add_argument('--status', action='store_true', help='Read local readiness, mining health and development earnings')
    modes.add_argument('--check', action='store_true', help='Read-only backend/GPU/miner readiness check')
    args = parser.parse_args()
    if args.inventory:
        print(json.dumps(inventory(), indent=2));return
    if not args.config:parser.error('--config is required except for --inventory')
    if args.setup:
        configure(args.config);return
    config = private_json(args.config)
    if args.status:
        print(json.dumps(saved_status(config), indent=2));return
    worker = ResidentWorker(config)
    if args.check:
        report=worker.readiness();print(json.dumps(report))
        raise SystemExit(0 if report['ready_models'] else 3)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    worker.run()


if __name__ == '__main__':
    main()

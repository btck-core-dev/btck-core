#!/usr/bin/env python3
"""B-local, permanent, self-recovering guard using A's existing cached web API.

No SSH keys, private RPC access, model sessions, Mac or chain replay required.
Only the standby lease/explicit peer list are changed. Consensus stays in btckd.
"""
import json
import math
import os
from pathlib import Path
import signal
import threading
import time
import urllib.request

URL = 'https://bitcoin-k.com/api/stats'
FINGERPRINT = '305828370fdbd5c116dd264ab29c9ba908a0f2409ba9f2b5d7e865ea7177c4f0'
GENESIS = 'b9249be0f1b57021a9cfef8241a499c6e975a91e24d6a3fc22ce850000000000'
LEASE = Path('/var/lib/btck/source-health-lease.json')
PEERS = Path('/etc/btck/standby-managed-peers.json')
STATE = Path('/var/lib/btck/source-follow-guard.json')
INCIDENT_HOLD = Path('/var/lib/btck/source-follow-hold.json')

def pressure_rejections(before, after):
    """Separate invalid PoW submissions from source service degradation.

    A complete, monotonic reason breakdown is required before excluding any
    low-difficulty submissions. Missing or inconsistent telemetry retains the
    conservative all-rejection gate. No share validation changes here.
    """
    rejected = after.get('rejected', 0) - before.get('rejected', 0)
    old, new = before.get('rejected_by_reason'), after.get('rejected_by_reason')
    if not isinstance(old, dict) or not isinstance(new, dict) or set(old) != set(new):
        return rejected
    if 'low_difficulty' not in old:
        return rejected
    if any(type(v) is not int or v < 0 for row in (old, new) for v in row.values()):
        return rejected
    if sum(old.values()) != before.get('rejected') or sum(new.values()) != after.get('rejected'):
        return rejected
    delta = {key:new[key]-old[key] for key in old}
    if any(value < 0 for value in delta.values()):
        return rejected
    return rejected-delta['low_difficulty']

def rpc_health_reason(value, *, now):
    """Cached HTTP freshness is NOT evidence of healthy mining RPC calls."""
    def number(item):
        return isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
    if not isinstance(value, dict) or value.get('format') != 'btck-node-rpc-health-v1':
        return 'source RPC health unavailable'
    observed = value.get('observed_at')
    if not number(observed) or not 0 <= now-observed <= 30:
        return 'source RPC health observation stale'
    methods = value.get('methods')
    if not isinstance(methods, dict): return 'source RPC method health unavailable'
    for method in ('getBlockTemplate', 'getDashboardInfo', 'submitBlock'):
        row = methods.get(method)
        # No block submission can be legitimate (e.g. no recent solution).
        # Once observed, a submission failure must not be hidden by healthy reads.
        if method == 'submitBlock' and row is None:
            continue
        if not isinstance(row, dict): return 'source RPC method health missing: '+method
        success, duration = row.get('last_success_at'), row.get('last_duration_seconds')
        if method != 'submitBlock' and (not number(success) or not 0 <= now-success <= 30):
            return 'source RPC success stale: '+method
        if not number(duration) or not 0 <= duration <= 4:
            return 'source RPC latency unhealthy: '+method
        if row.get('consecutive_failures') != 0 or (method != 'submitBlock' and
                (not isinstance(row.get('successes'), int) or row['successes'] < 1)):
            return 'source RPC failed: '+method
        failure = row.get('last_failure_at')
        if failure is not None:
            if not number(failure) or now-failure < 60:
                return 'source RPC failure within recovery window: '+method
    return None

def assess(data, previous=None, *, now=None):
    now = time.time() if now is None else now
    network = data.get('network', {})
    if (network.get('name') != 'mainnet' or network.get('consensus_fingerprint') != FINGERPRINT
            or network.get('genesis_hash') != GENESIS):
        return 'source identity mismatch'
    telemetry = data.get('telemetry', {})
    for key, maximum in [('node_age_seconds',15), ('pool_age_seconds',25)]:
        age = telemetry.get(key)
        if not isinstance(age,(float,int)) or not 0 <= age < maximum:
            return 'source cached measurements stale'
    generated = data.get('generated_at', 0)
    if not 0 <= now-generated <= 30:
        return 'source HTTP response stale'
    rpc_reason = rpc_health_reason(data.get('node_rpc_health'), now=now)
    if rpc_reason:
        return rpc_reason
    if data.get('active_worker_count',0)<1 or data.get('active_connection_count',0)<1:
        return 'source mining unavailable'
    # Individual miners may leave normally: a fixed seven-worker floor is not
    # evidence of overload. Availability, fresh blocks and share quality govern.
    latest = max((b.get('timestamp',0) for b in data.get('blocks',[])),default=0)
    if not 0 <= now-latest <= 45:
        return 'source block acceptance stale'
    if previous:
        if data['ledger_height'] < previous['ledger_height']:
            return 'source height regressed'
        before,after = previous.get('stratum_share_counters') or {},data.get('stratum_share_counters') or {}
        if not before.get('epoch') or before.get('epoch') != after.get('epoch'):
            return 'source share epoch changed'
        accepted=after.get('accepted',0)-before.get('accepted',0)
        rejected=after.get('rejected',0)-before.get('rejected',0)
        if accepted<0 or rejected<0:
            return 'source counters regressed'
        pressure=pressure_rejections(before,after)
        if accepted+pressure>=30 and pressure/(accepted+pressure)>.05:
            return 'source interval rejections exceed five percent'
    return None

def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(value,handle,sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o644)
    os.replace(temporary,path)

def authorize(allowed, *, now=None):
    now=time.time() if now is None else now
    atomic_json(LEASE,{'network':'mainnet','allowed':allowed,'issued_at':now,'expires_at':now+75})
    atomic_json(PEERS,{'network':'mainnet','peers':['185.130.45.240:16111'] if allowed else []})

class RecoveryPolicy:
    def __init__(self):
        self.healthy_samples=0

    def observe(self, reason):
        self.healthy_samples = self.healthy_samples+1 if reason is None else 0
        return self.healthy_samples >= 2

def apply_incident_hold(reason):
    try:
        if INCIDENT_HOLD.exists():
            return 'operator incident hold: source RPC health requires verification'
    except OSError:
        return 'operator incident hold status unreadable'
    return reason

def main():
    stop=threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):
        signal.signal(signum,lambda *_:stop.set())
    policy=RecoveryPolicy()
    previous=None
    last_advance=time.monotonic()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        while not stop.is_set():
            started=time.monotonic()
            current=None
            try:
                request=urllib.request.Request(URL,headers={'User-Agent':'BTCK-SeedB-SourceGuard/1.0','Cache-Control':'no-cache'})
                with opener.open(request,timeout=8) as response:
                    if response.geturl()!=URL:
                        raise ValueError('unexpected source redirect')
                    raw=response.read(1048577)
                    if len(raw)>1048576:
                        raise ValueError('source response too large')
                    current=json.loads(raw)
                reason=assess(current,previous)
                if previous is None or current['ledger_height']>previous['ledger_height']:
                    last_advance=time.monotonic()
                if time.monotonic()-last_advance>180:
                    reason='source height stalled for three minutes'
                # Compare the next interval, not an indefinitely old bad window.
                # This also allows a normal source restart/epoch change to heal.
                if current.get('network',{}).get('consensus_fingerprint')==FINGERPRINT:
                    previous=current
            except Exception as exc:
                reason='source observation unavailable: '+type(exc).__name__
            # Persist an actual-fault isolation across service/host restart.
            # Cached public stats cannot clear an observed private RPC failure.
            reason=apply_incident_hold(reason)
            allowed=policy.observe(reason)
            authorize(allowed)
            result={'observed_at':time.time(),'allowed':allowed,'reason':reason,
                'healthy_samples':policy.healthy_samples,'source_height':(current or {}).get('ledger_height'),
                'http_seconds':round(time.monotonic()-started,3),'source_url':URL}
            atomic_json(STATE,result)
            print(json.dumps(result,sort_keys=True),flush=True)
            stop.wait(30)
    finally:
        authorize(False)

if __name__=='__main__':
    main()

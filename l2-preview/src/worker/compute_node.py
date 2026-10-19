#!/usr/bin/env python3
"""One process entry: start miner/model, hold model resident, pause mining per request."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from worker.resident_worker import ResidentWorker,private_json
from worker.mining_guard import lock_file,process_birth
from worker.mining_lease import MiningLease,read_state,guard_alive


def recover_abandoned(config):
    state=read_state(config)
    if state and state.get('phase') in ('pausing','paused','resuming'):
        if guard_alive(state):
            # Previous Worker may have died just before this restart. Let its guard resume.
            end=time.monotonic()+8
            while guard_alive(state) and time.monotonic()<end:
                time.sleep(.1);state=read_state(config) or state
            if state.get('phase')=='resumed':return
            if guard_alive(state):raise RuntimeError('Another mining lease is still active')
        lease=MiningLease(config,state['miner_pid'],state['miner_birth'],state['miner_name'])
        lease.id=state['lease_id'];child=lease.spawn(recover=True)
        if child.wait(timeout=10)!=0:raise RuntimeError('Abandoned miner lease recovery failed')


def launch(entry):
    command=entry.get('command')
    if not isinstance(command,list) or not command or any(not isinstance(x,str) or not x for x in command):
        raise ValueError('Use an explicit executable and argument list')
    cwd=entry.get('cwd')
    env={**os.environ,**entry.get('env',{})}
    options={'cwd':cwd,'env':env,'stdin':subprocess.DEVNULL}
    if os.name!='nt':options['start_new_session']=True
    else:options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP
    # Child output may contain wallet/model parameters. Keep it in the operator's private log.
    log=Path(entry['log']);log.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(log,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    with os.fdopen(fd,'ab',buffering=0) as handle:
        return subprocess.Popen(command,stdout=handle,stderr=handle,**options)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    p.add_argument('--check',action='store_true');a=p.parse_args()
    run_node(a.config,check=a.check)


def run_node(config_path, *, worker=None, check=False):
    config=private_json(config_path)
    if config.get('mining_mode')!='pause_on_request':raise ValueError('Set mining_mode=pause_on_request')
    if not config.get('miner_process_name'):raise ValueError('An exact miner executable name is required')
    config.setdefault('preemption_max_seconds',120)
    if not 1<=config['inference_timeout']<config['preemption_max_seconds']<=180:
        raise ValueError('Inference timeout must be shorter than the 180-second maximum pause lease')
    config.setdefault('mining_lease_file',str(Path(config_path).resolve().with_name('mining-lease.json')))
    worker=worker or ResidentWorker(config)
    if check:
        print(json.dumps({'configuration':'PASS','mining_mode':'pause_on_request',
                          'pause_limit_seconds':config['preemption_max_seconds'],'starts_processes':False}));return
    lock=lock_file(Path(config_path).resolve().with_suffix('.runner.lock'))
    recover_abandoned(config)
    children=[]
    registry=Path(config_path).resolve().with_suffix('.processes.json')
    def start_or_adopt(role,entry):
        digest=hashlib.sha256(json.dumps(entry,sort_keys=True).encode()).hexdigest()
        saved=json.loads(registry.read_text()) if registry.exists() else {}
        previous=saved.get(role,{})
        if previous and process_birth(previous.get('pid'))==previous.get('birth'):
            if previous.get('command_sha256')!=digest:raise RuntimeError('Live process configuration changed; stop it before changing launch command')
            return None
        child=launch(entry)
        saved[role]={'pid':child.pid,'birth':process_birth(child.pid),'command_sha256':digest}
        temporary=registry.with_suffix('.new')
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump(saved,f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,registry)
        children.append(child);return child
    try:
        # Existing miners/models can be adopted without restarting them.
        alive,miner=worker.miner_status()
        if not alive:
            if config.get('miner_service'):
                import re
                if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',config['miner_service']):raise ValueError('Invalid explicit miner service')
                subprocess.run(['systemctl','start',config['miner_service']],check=True,timeout=30)
                child=None
            else:
                if not config.get('miner_launch'):raise RuntimeError('Start your miner or configure miner_launch')
                child=start_or_adopt('miner',config['miner_launch'])
            end=time.monotonic()+60
            while not worker.miner_status()[0]:
                if (child is not None and child.poll() is not None) or time.monotonic()>end:raise RuntimeError('Miner startup failed')
                time.sleep(.5)
        try:loaded=worker.backend_model in [x['id'] for x in worker.http(worker.backend+'/models')['data']]
        except Exception:loaded=False
        if not loaded:
            if not config.get('model_launch'):raise RuntimeError('Start your local model or configure model_launch')
            start_or_adopt('model',config['model_launch'])
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:worker.stop.set())
        print(json.dumps({'node':'RUNNING','mining_mode':'pause_on_request','model_kept_resident':True}),flush=True)
        worker.run()
    finally:
        worker.stop.set()
        # Worker inference finally releases its lease; a detached guard covers process crashes.
        recover_abandoned(config)
        # A graceful disconnect leaves the chosen miner and resident model running.
        lock.close()


if __name__=='__main__':main()

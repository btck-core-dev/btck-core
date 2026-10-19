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
from worker.mining_guard import lock_file,process_birth,replace_file
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


def validate_startup_protection(config):
    marker=config.get('model_startup_pause_marker')
    if not marker:return
    if not isinstance(marker,str) or not 1<=len(marker)<=200 or '\n' in marker:
        raise ValueError('Use an explicit bounded local model startup marker')
    stop_command=config.get('model_stop_command')
    if not isinstance(stop_command,list) or not stop_command or any(not isinstance(x,str) or not x for x in stop_command):
        raise ValueError('Startup protection requires an explicit local model stop command')
    seconds=config.get('model_startup_pause_seconds',180)
    startup_timeout=config.get('model_startup_timeout',600)
    if type(seconds) is not int or not 30<=seconds<=180 or type(startup_timeout) is not int or not 30<=startup_timeout<=900:
        raise ValueError('Use startup timeout30..900 and pause30..180 seconds')
    if not isinstance(config.get('model_launch',{}).get('log'),str):raise ValueError('Startup protection requires the private model log')
    return marker,stop_command,seconds,startup_timeout


def guard_model_warmup(worker,config,process):
    """Pause only after the owner's runtime reports completed weight loading."""
    protection=validate_startup_protection(config)
    if protection is None:return
    marker,stop_command,seconds,startup_timeout=protection
    log=Path(config['model_launch']['log']);offset=process.get('log_offset',0)
    pending=b'';deadline=time.monotonic()+startup_timeout;ready=False
    def stop_model():
        subprocess.run(stop_command,check=True,timeout=8,stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while True:
            if process_birth(process['pid'])!=process['birth']:raise RuntimeError('Model exited before startup protection')
            if worker.stop.is_set():raise RuntimeError('Model startup interrupted')
            try:
                with log.open('rb') as stream:
                    stream.seek(offset);raw=stream.read(65536);offset+=len(raw)
                pending=(pending+raw)[-65536:]
                if marker.encode() in pending:break
            except FileNotFoundError:pass
            if time.monotonic()>=deadline:raise TimeoutError('Model startup marker deadline exceeded')
            time.sleep(.2)
        alive,miner=worker.miner_status()
        if not alive or not miner.get('miner_pid'):raise RuntimeError('Miner identity unavailable during model startup')
        pause={**config,'preemption_max_seconds':seconds}
        with MiningLease(pause,miner['miner_pid'],miner['miner_birth'],worker.miner_name):
            try:
                deadline=time.monotonic()+seconds-10
                while time.monotonic()<deadline and not worker.stop.is_set():
                    if process_birth(process['pid'])!=process['birth']:raise RuntimeError('Model exited during warmup')
                    try:
                        models=worker.http(worker.backend+'/models',timeout=1)
                        if worker.backend_model in [m['id'] for m in models.get('data',[])]:
                            ready=True;return
                    except Exception:pass
                    time.sleep(.2)
                raise TimeoutError('Model warmup exceeded the bounded mining pause')
            finally:
                # Stop a failed startup before releasing mining, preserving a healthy resident model.
                if not ready:stop_model()
    except BaseException:
        if not ready:
            try:stop_model()
            except Exception:pass
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    p.add_argument('--log-file',type=Path)
    p.add_argument('--check',action='store_true');a=p.parse_args()
    if a.log_file:
        a.log_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(a.log_file,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        sys.stdout=sys.stderr=os.fdopen(fd,'a',buffering=1,encoding='utf-8')
    run_node(a.config,check=a.check)


def run_node(config_path, *, worker=None, check=False):
    config=private_json(config_path)
    validate_startup_protection(config)
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
    startup_failure=Path(config_path).resolve().with_suffix('.startup-failed.json')
    if config.get('model_startup_pause_marker') and startup_failure.exists():
        raise RuntimeError('Model startup previously failed; inspect the model log and remove '+str(startup_failure)+' only after correcting the local runtime')
    print(json.dumps({'node':'STARTING','step':'acquire_single_instance'}),flush=True)
    lock=lock_file(Path(config_path).resolve().with_suffix('.runner.lock'))
    recover_abandoned(config)
    children=[];monitor=None;model_failed=threading.Event()
    registry=Path(config_path).resolve().with_suffix('.processes.json')
    def start_or_adopt(role,entry):
        digest=hashlib.sha256(json.dumps(entry,sort_keys=True).encode()).hexdigest()
        saved=json.loads(registry.read_text()) if registry.exists() else {}
        previous=saved.get(role,{})
        if previous and process_birth(previous.get('pid'))==previous.get('birth'):
            if previous.get('command_sha256')!=digest:raise RuntimeError('Live process configuration changed; stop it before changing launch command')
            return None
        log=Path(entry['log']);offset=log.stat().st_size if log.exists() else 0
        child=launch(entry)
        saved[role]={'pid':child.pid,'birth':process_birth(child.pid),'command_sha256':digest,'log_offset':offset}
        temporary=registry.with_suffix('.new')
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump(saved,f);f.flush();os.fsync(f.fileno())
        replace_file(temporary,registry)
        children.append(child);return child
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:worker.stop.set())
    try:
        # Existing miners/models can be adopted without restarting them.
        print(json.dumps({'node':'STARTING','step':'detect_miner'}),flush=True)
        alive,miner=worker.miner_status()
        if not alive:
            if config.get('miner_task'):
                import re
                task=config['miner_task']
                if os.name!='nt' or not isinstance(task,str) or not re.fullmatch(r'[A-Za-z0-9_. -]{1,100}',task):
                    raise ValueError('Use an explicit Windows miner task name')
                subprocess.run(['schtasks.exe','/Run','/TN',task],check=True,timeout=30)
                child=None
            elif config.get('miner_service'):
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
        print(json.dumps({'node':'STARTING','step':'detect_model'}),flush=True)
        try:loaded=worker.backend_model in [x['id'] for x in worker.http(worker.backend+'/models')['data']]
        except Exception:loaded=False
        if not loaded:
            if not config.get('model_launch'):raise RuntimeError('Start your local model or configure model_launch')
            start_or_adopt('model',config['model_launch'])
            saved=json.loads(registry.read_text())
            try:guard_model_warmup(worker,config,saved['model'])
            except BaseException as error:
                if config.get('model_startup_pause_marker') and not worker.stop.is_set():
                    with os.fdopen(os.open(startup_failure,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:
                        json.dump({'at':time.time(),'error_type':type(error).__name__,'automatic_model_retry':False},f)
                raise
        saved=json.loads(registry.read_text()) if registry.exists() else {}
        model_process=saved.get('model')
        if model_process and (not loaded or process_birth(model_process['pid'])==model_process['birth']):
            def watch_model():
                while not worker.stop.wait(1):
                    if process_birth(model_process['pid'])!=model_process['birth']:
                        model_failed.set();worker.stop.set();return
            monitor=threading.Thread(target=watch_model,daemon=True);monitor.start()
        print(json.dumps({'node':'RUNNING','mining_mode':'pause_on_request','model_kept_resident':True}),flush=True)
        worker.run()
        if model_failed.is_set():raise RuntimeError('Managed model process exited; inspect the private model log before restarting')
    finally:
        worker.stop.set()
        if monitor:monitor.join(2)
        # Worker inference finally releases its lease; a detached guard covers process crashes.
        recover_abandoned(config)
        # A graceful disconnect leaves the chosen miner and resident model running.
        lock.close()


if __name__=='__main__':main()

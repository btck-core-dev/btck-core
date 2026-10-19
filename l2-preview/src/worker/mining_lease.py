"""Client for an independent, PID-identity-bound local miner pause guard."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .mining_guard import process_birth


def read_state(config):
    path=config.get('mining_lease_file')
    if not path:return None
    try:
        p=Path(path)
        if p.stat().st_size>16384:return None
        state=json.loads(p.read_text())
        if process_birth(state['miner_pid'])!=state['miner_birth']:return None
        return state
    except (OSError,ValueError,KeyError,TypeError):return None


def guard_alive(state):
    return process_birth(state.get('guard_pid'))==state.get('guard_birth')


class MiningLease:
    def __init__(self,config,pid,birth,name):
        self.config,self.pid,self.birth,self.name=config,pid,str(birth),name
        self.path=Path(config['mining_lease_file']);self.id=uuid.uuid4().hex;self.child=None;self.acquired=False
        self.seconds=float(config.get('preemption_max_seconds',180))
        if not math.isfinite(self.seconds) or not .2<=self.seconds<=180:raise ValueError('Invalid miner pause duration')

    def command(self,*,recover=False):
        command=self.config.get('mining_guard_command')
        if command is None:
            command=[sys.executable,'--mining-lease-guard'] if getattr(sys,'frozen',False) else [sys.executable,'-m','worker.mining_guard']
        if not isinstance(command,list) or not command or any(not isinstance(x,str) for x in command):raise ValueError('Invalid local guard command')
        args=['--pid',str(self.pid),'--birth',self.birth,'--name',self.name,
              '--owner-pid',str(os.getpid()),'--owner-birth',str(process_birth(os.getpid())),
              '--lease-file',str(self.path),'--lease-id',self.id,'--seconds',str(self.seconds)]
        return command+args+(['--recover'] if recover else [])

    def spawn(self,*,recover=False):
        options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,
                 'env':{**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}}
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        else:options['start_new_session']=True
        return subprocess.Popen(self.command(recover=recover),**options)

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.child=self.spawn();end=time.monotonic()+12
        try:
            while time.monotonic()<end:
                state=read_state(self.config)
                if state and state.get('lease_id')==self.id and state.get('phase')=='paused' and guard_alive(state):
                    self.acquired=True;return self
                if self.child.poll() is not None:raise RuntimeError('Miner pause was not admitted')
                time.sleep(.02)
            raise TimeoutError('Miner pause acknowledgement timed out')
        except BaseException:
            self.release();raise

    def release(self):
        if self.child is None:return
        marker=self.path.with_name(self.path.name+'.release-'+self.id)
        marker.touch(mode=0o600,exist_ok=True)
        end=time.monotonic()+12
        while time.monotonic()<end:
            state=read_state(self.config)
            if state and state.get('lease_id')==self.id:
                if state.get('phase')=='resumed':
                    self.acquired=False
                    try:self.child.wait(timeout=2)
                    except subprocess.TimeoutExpired:pass
                    return state
                if not guard_alive(state):
                    recovery=self.spawn(recover=True)
                    recovery.wait(timeout=8)
            elif self.child.poll() is not None and not self.acquired:return None
            time.sleep(.05)
        raise RuntimeError('Miner recovery acknowledgement unavailable; independent watchdog remains bounded')

    def __exit__(self,*exc):
        state=self.release()
        if state and state.get('resume_reason')=='watchdog_timeout' and exc[0] is None:
            raise TimeoutError('Inference exceeded the miner pause lease')

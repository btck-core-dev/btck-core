"""Bounded, independent miner pause lease. No model loading or network listener."""
from __future__ import annotations
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time


def enable_debug_privilege():
    """Enable an existing administrator right for a SYSTEM-owned miner.

    This cannot grant a privilege absent from the caller's Windows token.
    """
    if os.name!='nt':return False
    from ctypes import wintypes as w
    class Luid(ctypes.Structure):
        _fields_=[('low',w.DWORD),('high',w.LONG)]
    class Privileges(ctypes.Structure):
        _fields_=[('count',w.DWORD),('luid',Luid),('attributes',w.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    api.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    api.LookupPrivilegeValueW.argtypes=[w.LPCWSTR,w.LPCWSTR,ctypes.POINTER(Luid)]
    api.AdjustTokenPrivileges.argtypes=[w.HANDLE,w.BOOL,ctypes.POINTER(Privileges),w.DWORD,w.LPVOID,w.LPVOID]
    token=w.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(),0x20|0x08,ctypes.byref(token)):return False
    try:
        value=Privileges();value.count=1;value.attributes=2
        if not api.LookupPrivilegeValueW(None,'SeDebugPrivilege',ctypes.byref(value.luid)):return False
        ctypes.set_last_error(0)
        return bool(api.AdjustTokenPrivileges(token,False,ctypes.byref(value),0,None,None) and ctypes.get_last_error()==0)
    finally:kernel.CloseHandle(token)


def process_birth(pid):
    if type(pid) is not int or pid <= 1: return None
    if sys.platform.startswith('linux'):
        try: return (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
        except (OSError,IndexError): return None
    if os.name == 'nt':
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None
        try:
            times=[w.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in times]):return None
            if times[1].dwHighDateTime or times[1].dwLowDateTime:return None
            return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        finally:kernel.CloseHandle(handle)
    return None


class NativeMiner:
    def __init__(self,pid,birth,name,*,recover=False):
        self.pid,self.birth,self.name=pid,str(birth),name
        self.handle=None;self.owned=False
        if os.name=='nt':enable_debug_privilege()
        if process_birth(pid)!=self.birth:raise ValueError('Miner identity changed')
        if sys.platform.startswith('linux'):
            raw=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')[0].decode()
            if Path(raw).name!=name:raise ValueError('Miner executable mismatch')
            self.handle=os.pidfd_open(pid)
            if process_birth(pid)!=self.birth:
                self.close();raise ValueError('Miner identity changed while opening handle')
            if self.state() in ('Z','X') or (self.state() in ('T','t') and not recover):
                self.close();raise ValueError('Miner is not running or is already paused')
        elif os.name=='nt':
            from ctypes import wintypes as w
            self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.kernel.OpenProcess.restype=w.HANDLE
            self.kernel.CloseHandle.argtypes=[w.HANDLE]
            self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
            self.handle=self.kernel.OpenProcess(0x1000|0x0800,False,pid)
            if not self.handle:raise PermissionError('Cannot open miner for bounded suspension')
            buffer=ctypes.create_unicode_buffer(32768);size=w.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(self.handle,0,buffer,ctypes.byref(size)) or Path(buffer.value).name.casefold()!=name.casefold() or process_birth(pid)!=self.birth:
                self.close();raise ValueError('Miner executable or identity mismatch')
            native=ctypes.WinDLL('ntdll')
            self.suspend=native.NtSuspendProcess;self.resume_process=native.NtResumeProcess
            for fn in (self.suspend,self.resume_process):fn.argtypes=[w.HANDLE];fn.restype=w.LONG
        else:raise RuntimeError('Pause leases support Linux and Windows only')

    def state(self):
        if sys.platform.startswith('linux'):
            try:return (Path('/proc')/str(self.pid)/'stat').read_text().rsplit(')',1)[1].split()[0]
            except OSError:return 'X'
        return 'unknown'

    def pause(self):
        if os.name=='nt':
            if self.suspend(self.handle)<0:raise PermissionError('Miner suspension failed')
        else:signal.pidfd_send_signal(self.handle,signal.SIGSTOP)
        self.owned=True
        if sys.platform.startswith('linux'):
            end=time.monotonic()+2
            while self.state() not in ('T','t'):
                if time.monotonic()>=end:raise RuntimeError('Miner did not enter stopped state')
                time.sleep(.01)

    def resume(self,*,recovery=False):
        if not self.owned and not recovery:return
        if os.name=='nt':
            if self.resume_process(self.handle)<0:raise RuntimeError('Miner resume failed')
        else:signal.pidfd_send_signal(self.handle,signal.SIGCONT)
        self.owned=False

    def close(self):
        if self.handle is not None:
            if os.name=='nt':self.kernel.CloseHandle(self.handle)
            else:os.close(self.handle)
            self.handle=None


def replace_file(temporary,path):
    # Windows readers and antivirus scanners can briefly deny replacement.
    # Keep the old complete state until the new file can be swapped in.
    deadline=time.monotonic()+1
    while True:
        try:
            os.replace(temporary,path)
            return
        except PermissionError:
            if os.name!='nt' or time.monotonic()>=deadline:raise
            time.sleep(.01)


def write_state(path,data):
    temporary=path.with_name(path.name+'.'+data['lease_id']+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    try:
        with os.fdopen(fd,'w') as handle:
            json.dump(data,handle);handle.flush();os.fsync(handle.fileno())
        replace_file(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def lock_file(path):
    handle=open(path,'a+b')
    try:
        if os.name=='nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):handle.write(b'0');handle.flush()
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except BaseException:handle.close();raise


def guard(*,pid,birth,name,owner_pid,owner_birth,lease_file,lease_id,seconds,recover=False):
    if not math.isfinite(seconds) or not .2<=seconds<=180:raise ValueError('Pause limit must be .2 to 180 seconds')
    if not lease_id or any(c not in '0123456789abcdef' for c in lease_id) or len(lease_id)!=32:raise ValueError('Invalid lease ID')
    path=Path(lease_file);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=lock_file(path.with_suffix('.lock'));miner=None;state=None
    release=path.with_name(path.name+'.release-'+lease_id)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    try:
        if recover:
            previous=json.loads(path.read_text())
            if previous['lease_id']!=lease_id or previous['miner_pid']!=pid or previous['miner_birth']!=str(birth) or previous['miner_name']!=name or previous['phase'] not in ('pausing','paused','resuming'):
                raise ValueError('Recovery does not match an abandoned lease')
            if process_birth(previous['guard_pid'])==previous['guard_birth']:raise ValueError('Lease guard is still alive')
            miner=NativeMiner(pid,birth,name,recover=True)
            if os.name=='nt' or miner.state() in ('T','t'):miner.resume(recovery=True)
            state={**previous,'phase':'resumed','resumed_at':time.time(),'resume_reason':'abandoned_guard_recovery'}
            write_state(path,state);return
        if process_birth(owner_pid)!=str(owner_birth):raise ValueError('Lease owner is not alive')
        miner=NativeMiner(pid,birth,name)
        started=time.monotonic();deadline=started+seconds
        state={'lease_id':lease_id,'miner_pid':pid,'miner_birth':str(birth),'miner_name':name,
               'owner_pid':owner_pid,'owner_birth':str(owner_birth),'guard_pid':os.getpid(),
               'guard_birth':process_birth(os.getpid()),'phase':'pausing','started_at':time.time(),
               'deadline':time.time()+seconds,'max_seconds':seconds}
        write_state(path,state)
        miner.pause();state['phase']='paused';write_state(path,state)
        reason='released'
        while not release.exists():
            if stop.wait(.05):reason='guard_stopping';break
            if process_birth(owner_pid)!=str(owner_birth):reason='owner_disconnected';break
            if time.monotonic()>=deadline:reason='watchdog_timeout';break
        state['resume_reason']=reason
    finally:
        try:
            if miner is not None:
                if miner.owned:
                    # Recovery must never depend on being able to write a status file.
                    miner.resume()
                if state is not None and state['phase']!='resumed':
                    state.update(phase='resumed',resumed_at=time.time())
                    write_state(path,state)
                miner.close()
        finally:
            lock.close()
            if release.exists():release.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('pid','owner-pid'):p.add_argument('--'+field,required=True,type=int)
    for field in ('birth','name','owner-birth','lease-file','lease-id'):p.add_argument('--'+field,required=True)
    p.add_argument('--seconds',required=True,type=float);p.add_argument('--recover',action='store_true')
    a=p.parse_args()
    guard(pid=a.pid,birth=a.birth,name=a.name,owner_pid=a.owner_pid,owner_birth=a.owner_birth,
          lease_file=a.lease_file,lease_id=a.lease_id,seconds=a.seconds,recover=a.recover)


if __name__=='__main__':main()

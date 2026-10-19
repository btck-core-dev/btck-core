#!/usr/bin/env python3
"""Read-only resident/Pearl observation. Never launches inference or controls processes."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import threading
import time


def assess(status, *, now, miner_pid, minimum_hs, allow_transient=False, max_transient_seconds=180):
    if not isinstance(status,dict) or not isinstance(status.get('telemetry',{}),dict):
        raise ValueError('Invalid worker status')
    telemetry=status.get('telemetry',{})
    reasons=[]
    if not 0 <= now-status.get('at',0) <= 20:reasons.append('stale_status')
    if not status.get('ready'):reasons.append('worker_unavailable')
    if telemetry.get('miner_pid')!=miner_pid or not telemetry.get('miner_alive'):reasons.append('miner_identity_changed_or_missing')
    rate=telemetry.get('pearl_hashrate_hs')
    valid_rate=type(rate) in (int,float) and math.isfinite(rate) and rate>0
    grace=telemetry.get('hashrate_grace_seconds',0)
    elapsed=telemetry.get('hashrate_low_seconds',-1)
    numeric=lambda value:type(value) in (int,float) and math.isfinite(value)
    transient=(allow_transient and valid_rate and rate<minimum_hs and
               telemetry.get('hashrate_transient') is True and
               numeric(grace) and 0<grace<=max_transient_seconds and
               numeric(elapsed) and 0<=elapsed<grace and
               telemetry.get('hashrate_recovery_floor_hs')==minimum_hs)
    if not valid_rate or (rate<minimum_hs and not transient):reasons.append('mining_rate_gate')
    if allow_transient:
        gpus=telemetry.get('gpus',[])
        if not isinstance(gpus,list) or not gpus or any(not isinstance(g,dict) or not numeric(g.get('free_mib')) or g['free_mib']<512 or
                           not numeric(g.get('temperature_c')) or g['temperature_c']>=80 for g in gpus):
            reasons.append('gpu_resource_gate')
    return {'at':now,'healthy':not reasons,'reasons':reasons,'miner_pid':telemetry.get('miner_pid'),
            'hashrate_hs':rate,'accepted':telemetry.get('pearl_accepted'),'rejected':telemetry.get('pearl_rejected'),
            'gpus':telemetry.get('gpus',[]),'provider_calls':status.get('provider_calls'),
            'worker_gate':status.get('gate'),'miner_control_enabled':False,
            'transient_rate_allowance':bool(transient and not reasons),
            'hashrate_grace_seconds':grace,'hashrate_low_seconds':elapsed}


def observe(status_file, output, *, seconds, interval, miner_pid, minimum_hs, stop=None,
            allow_transient=False, max_transient_seconds=180):
    if not all(math.isfinite(x) for x in (seconds,interval,minimum_hs)) or seconds<=0 or not 1<=interval<=300 or miner_pid<=1 or not minimum_hs>0:
        raise ValueError('Use positive duration/rate, interval 1..300 seconds and an existing miner PID')
    if type(allow_transient) is not bool or not math.isfinite(max_transient_seconds) or not 0<max_transient_seconds<=600:
        raise ValueError('Transient observation requires an explicit policy capped at 600 seconds')
    output=Path(output);output.mkdir(parents=True,exist_ok=False,mode=0o700)
    stop=stop or threading.Event();start=time.monotonic();started=time.time()
    summary={'qualification':'RUNNING','started_at':started,'required_seconds':seconds,'interval_seconds':interval,
             'minimum_hashrate_hs':minimum_hs,'samples':0,'unhealthy_samples':0,'read_errors':0,
             'allow_transient':allow_transient,'max_transient_seconds':max_transient_seconds,
             'transient_samples':0,'below_baseline_samples':0,'max_observed_drop_seconds':0,
             'min_observed_hashrate_hs':None,'max_sample_gap_seconds':0,'miner_pid':miner_pid,
             'inference_generated_by_observer':0,'miner_control_enabled':False}
    previous=None;low_since=None
    with open(output/'samples.jsonl','x',encoding='utf-8') as stream:
        os.chmod(output/'samples.jsonl',0o600)
        while True:
            now=time.time()
            try:
                with open(status_file,encoding='utf-8') as handle:
                    raw=handle.read(1048577)
                if len(raw)>1048576:raise ValueError('Status too large')
                row=assess(json.loads(raw),now=now,miner_pid=miner_pid,minimum_hs=minimum_hs,
                           allow_transient=allow_transient,max_transient_seconds=max_transient_seconds)
            except (OSError,ValueError,TypeError):
                row={'at':now,'healthy':False,'reasons':['status_read_error']};summary['read_errors']+=1
            rate=row.get('hashrate_hs');tick=time.monotonic()
            valid_rate=type(rate) in (int,float) and math.isfinite(rate) and rate>0
            if valid_rate and rate>=minimum_hs:low_since=None
            elif valid_rate:
                if low_since is None:low_since=tick
                summary['below_baseline_samples']+=1
            observed_drop=0 if low_since is None else tick-low_since
            summary['max_observed_drop_seconds']=max(summary['max_observed_drop_seconds'],observed_drop)
            if row.get('transient_rate_allowance') and observed_drop>=min(max_transient_seconds,row['hashrate_grace_seconds']):
                row['healthy']=False;row['transient_rate_allowance']=False
                row['reasons'].append('observer_transient_window_exceeded')
            summary['samples']+=1;summary['unhealthy_samples']+=not row['healthy']
            summary['transient_samples']+=bool(row.get('transient_rate_allowance'))
            if previous is not None:summary['max_sample_gap_seconds']=max(summary['max_sample_gap_seconds'],now-previous)
            previous=now
            rate=row.get('hashrate_hs')
            if valid_rate:
                old=summary['min_observed_hashrate_hs'];summary['min_observed_hashrate_hs']=rate if old is None else min(rate,old)
            stream.write(json.dumps(row,separators=(',',':'))+'\n');stream.flush();os.fsync(stream.fileno())
            elapsed=time.monotonic()-start;summary['elapsed_seconds']=elapsed;summary['last_sample_at']=now
            if elapsed>=seconds:
                summary['ended_below_baseline']=not valid_rate or rate<minimum_hs
                summary['qualification']='PASS' if summary['unhealthy_samples']==0 and summary['max_sample_gap_seconds']<=interval*3 and not summary['ended_below_baseline'] else 'FAIL'
            elif stop.is_set():summary['qualification']='INTERRUPTED'
            temporary=output/'summary.tmp'
            with open(temporary,'w',encoding='utf-8') as handle:
                os.chmod(temporary,0o600);json.dump(summary,handle,indent=2);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,output/'summary.json')
            if summary['qualification']!='RUNNING':break
            stop.wait(min(interval,max(0,seconds-elapsed)))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--status-file',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--hours',type=float,default=24);p.add_argument('--interval',type=float,default=30)
    p.add_argument('--miner-pid',required=True,type=int);p.add_argument('--minimum-hs',required=True,type=float)
    p.add_argument('--allow-hashrate-transient',action='store_true')
    p.add_argument('--max-transient-seconds',type=float,default=180)
    a=p.parse_args();stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    report=observe(a.status_file,a.output_dir,seconds=a.hours*3600,interval=a.interval,miner_pid=a.miner_pid,minimum_hs=a.minimum_hs,stop=stop,
                   allow_transient=a.allow_hashrate_transient,max_transient_seconds=a.max_transient_seconds)
    print(json.dumps(report));return 0 if report['qualification']=='PASS' else 3


if __name__=='__main__':raise SystemExit(main())

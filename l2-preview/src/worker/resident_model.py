"""Linux resident model launcher with a guard for its own child process only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from .resident_worker import ResidentWorker, private_json


def verified_model(manifest_path):
    manifest=json.loads(Path(manifest_path).read_text())
    if manifest.get('verification')!='PASS' or not manifest.get('revision'):
        raise ValueError('A pinned, verified model manifest is required')
    root=Path(manifest['path']).resolve()
    if not (root/'config.json').is_file() or not manifest.get('files'):
        raise ValueError('Model configuration and weight digests are required')
    for name,expected in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Model manifest path escaped its root')
        digest=hashlib.sha256()
        with path.open('rb') as handle:
            while chunk:=handle.read(4*1024**2):digest.update(chunk)
        if digest.hexdigest()!=expected:raise ValueError('Model file digest mismatch')
    return root


def stop_owned_child(child, grace=15):
    """Only accepts the Popen handle returned by this launcher's new session."""
    try:os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:return
    end=time.monotonic()+grace
    while time.monotonic()<end:
        child.poll()  # Reap the leader while also waiting for any descendants.
        try:os.killpg(child.pid,0)
        except ProcessLookupError:break
        time.sleep(.05)
    else:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    child.wait(timeout=5)


def supervise(command, probe, *, stop=None, interval=5, bad_limit=3, log_samples=False):
    if os.name!='posix':raise RuntimeError('Run model supervision on Linux; the client is cross-platform')
    stop=stop or threading.Event()
    child=subprocess.Popen(command,start_new_session=True)
    bad=0
    try:
        while child.poll() is None:
            if stop.wait(interval):return 130
            try:safe,telemetry=probe()
            except Exception as exc:safe,telemetry=False,{'probe_error':type(exc).__name__}
            if log_samples:
                print(json.dumps({'guard_time':time.time(),'safe':safe,'telemetry':telemetry}),flush=True)
            bad=0 if safe else bad+1
            if bad>=bad_limit:
                print('Resource/mining guard closed; stopping only the optional model process group',flush=True)
                return 75
        return child.returncode
    finally:stop_owned_child(child)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,help='Private resident-worker config')
    p.add_argument('--model-manifest',required=True)
    p.add_argument('--python',required=True,help='Existing vLLM environment Python')
    p.add_argument('--port',type=int,default=18085)
    p.add_argument('--memory-fraction',type=float,default=.45)
    p.add_argument('--context-tokens',type=int,default=2048)
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    if not .1<=a.memory_fraction<=.8 or not 128<=a.context_tokens<=32768 or not 1024<=a.port<=65535:
        p.error('Use bounded memory, context and port settings')
    config=private_json(a.config);worker=ResidentWorker(config)
    safe,telemetry=worker.observe_hardware()
    if not safe:raise RuntimeError('Mining/resource gate is closed before model launch')
    selected=config.get('gpu_indices',[0])
    from urllib.parse import urlsplit
    parsed=urlsplit(config['backend_url'])
    if (parsed.port or 80)!=a.port or parsed.scheme!='http':
        raise ValueError('The worker local backend port must match the model server')
    raw=subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.total,memory.free','--format=csv,noheader,nounits'],text=True,timeout=4)
    for line in raw.strip().splitlines():
        index,total,free=[int(x.strip()) for x in line.split(',')]
        if index in selected and total*a.memory_fraction+config.get('min_free_mib',512)>free:
            raise RuntimeError('Requested vLLM allocation exceeds currently available GPU memory')
    model=verified_model(a.model_manifest)
    report={'model':worker.model,'gpu_indices':selected,'memory_fraction':a.memory_fraction,
            'context_tokens':a.context_tokens,'miner_control_enabled':False,'model_path':str(model)}
    print(json.dumps(report),flush=True)
    if a.check:return
    os.environ.update(CUDA_VISIBLE_DEVICES=','.join(str(i) for i in selected),CUDA_DEVICE_ORDER='PCI_BUS_ID',
                      HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='2',TOKENIZERS_PARALLELISM='false')
    command=[a.python,'-m','vllm.entrypoints.openai.api_server','--host','127.0.0.1','--port',str(a.port),
             '--model',str(model),'--served-model-name',worker.model,'--tensor-parallel-size',str(len(selected)),
             '--dtype','float16','--max-model-len',str(a.context_tokens),'--max-num-seqs','1',
             '--max-num-batched-tokens',str(a.context_tokens),'--gpu-memory-utilization',str(a.memory_fraction),
             '--quantization','awq_marlin','--safetensors-load-strategy','prefetch']
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    raise SystemExit(supervise(command,worker.observe_hardware,stop=stop))


if __name__=='__main__':main()

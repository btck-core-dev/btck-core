#!/usr/bin/env python3
"""Install the standalone Linux compute node and private configuration as root."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from worker.resident_worker import private_json


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,type=Path)
    p.add_argument('--start',action='store_true');args=p.parse_args()
    if not sys.platform.startswith('linux') or os.geteuid()!=0:p.error('Run this installer as root on Linux')
    config=private_json(args.config)
    if config.get('mining_mode')!='pause_on_request':p.error('Use the pause_on_request compute-node configuration')
    source=ROOT;target=Path('/opt/btck-compute-node');destination=Path('/etc/btck-compute-node.json')
    unit=Path('/etc/systemd/system/btck-compute-node.service')
    if target.exists() or destination.exists() or unit.exists():p.error('Installation exists; preserve it and perform an explicit versioned upgrade')
    target.mkdir(mode=0o755)
    files=['scripts/l2_compute_node.py','src/worker/__init__.py','src/worker/resident_worker.py',
           'src/worker/agent_protocol.py','src/worker/mining_guard.py','src/worker/mining_lease.py','src/worker/compute_node.py',
           'src/worker/inference_advisor.py','src/worker/connect_app.py','src/worker/advisor_dialog.py',
           'src/worker/service_info.py','scripts/l2_model_advisor.py']
    files.append('src/worker/qualified_profiles.py')
    for name in files:
        dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,dst);dst.chmod(0o644)
    Path('/var/lib/btck-compute').mkdir(mode=0o700,exist_ok=True)
    config.update(status_file='/var/lib/btck-compute/status.json',mining_lease_file='/var/lib/btck-compute/mining-lease.json')
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(config,f,indent=2);f.flush();os.fsync(f.fileno())
    unit=Path('/etc/systemd/system/btck-compute-node.service')
    if unit.exists():raise RuntimeError('Service already exists; no existing unit was overwritten')
    shutil.copyfile(source/'deploy/btck-compute-node.service',unit);unit.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable',*(['--now'] if args.start else []),'btck-compute-node.service'],check=True)
    print('Compute node installed; private config preserved. '+('Service started.' if args.start else 'Start with systemctl start btck-compute-node.service.'))


if __name__=='__main__':main()

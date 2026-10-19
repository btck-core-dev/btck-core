#!/usr/bin/env python3
"""Install versioned client files and a disabled Linux user unit. No service starts."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from worker.resident_worker import private_json,ResidentWorker


def install(config, *, home=None):
    if sys.platform!='linux':raise ValueError('The user service installer requires Linux')
    home=Path(home or Path.home()).resolve();config=Path(config).absolute()
    settings=private_json(config);config=config.resolve();ResidentWorker(settings)
    raw=(ROOT/'src/worker/resident_worker.py').read_bytes()
    version=hashlib.sha256(raw).hexdigest()[:16]
    target=home/'.local/share/btck-resident-worker'/version
    unit=home/'.config/systemd/user/btck-l2-resident-worker.service'
    if unit.exists() or unit.is_symlink():raise ValueError('User service already exists; refusing to overwrite')
    if any(c in str(p) for p in (config,target,home) for c in ('\n','\r','%','"','\\')):
        raise ValueError('Unsupported user unit path characters')
    target.mkdir(parents=True,exist_ok=False,mode=0o700)
    (target/'worker').mkdir(mode=0o700)
    for name in ['__init__.py','resident_worker.py']:
        shutil.copy2(ROOT/'src/worker'/name,target/'worker'/name)
    unit.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    text=('''[Unit]
Description=BTC-K resident GPU client (development credits)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="PYTHONPATH=TARGET"
ExecStart=/usr/bin/python3 -m worker.resident_worker --config "CONFIG"
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
NoNewPrivileges=yes
PrivateTmp=yes
UMask=0077
MemoryMax=128M
CPUQuota=20%
TasksMax=32

[Install]
WantedBy=default.target
'''.replace('TARGET',str(target)).replace('CONFIG',str(config)))
    with os.fdopen(os.open(unit,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as handle:handle.write(text)
    print('Client installed: '+str(target))
    print('Disabled user service: '+str(unit))
    print('After stopping any previous client using the same credential:')
    print('systemctl --user daemon-reload')
    print('systemctl --user enable --now btck-l2-resident-worker.service')
    print('No model or miner was started, stopped or modified.')
    return unit


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    install(p.parse_args().config)

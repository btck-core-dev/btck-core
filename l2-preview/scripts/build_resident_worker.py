#!/usr/bin/env python3
"""Build a deterministic stdlib-only resident worker ZIP; no runtime credentials."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=['LICENSE','docs/L2_COMPUTE_CONNECT_QUICKSTART.md',
       'scripts/l2_compute_node.py','deploy/install_compute_node.py','docs/examples/compute-node.example.json','deploy/btck-compute-node.service',
       'src/worker/mining_guard.py','src/worker/mining_lease.py','src/worker/compute_node.py',
       'src/worker/inference_advisor.py','src/worker/advisor_dialog.py','src/worker/service_info.py',
       'src/worker/qualified_profiles.py',
       'scripts/l2_model_advisor.py',
       'src/worker/agent_protocol.py','src/worker/connect_app.py','deploy/compute-connect.sh',
       'src/worker/__init__.py','src/worker/resident_worker.py','src/worker/resident_model.py','deploy/worker.sh',
       'deploy/start_worker.bat','deploy/btck-resident-worker.service','deploy/install_resident_worker.py',
       'scripts/observe_resident_coexistence.py','docs/examples/resident-worker.example.json']


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    payload={name:(ROOT/name).read_bytes() for name in FILES}
    payload['README.md']=payload['docs/L2_COMPUTE_CONNECT_QUICKSTART.md']
    payload['MANIFEST.json']=(json.dumps({'format':'btck-resident-worker-pilot-v1','currency':'development_satk',
        'files':{k:hashlib.sha256(v).hexdigest() for k,v in payload.items()}},sort_keys=True,indent=2)+'\n').encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(a.output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,raw in sorted(payload.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,10,18,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100755 if name.endswith('.sh') else 0o100644)<<16
            z.writestr(info,raw)
    print(json.dumps({'artifact':str(a.output),'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest(),'files':len(payload)}))


if __name__=='__main__':main()

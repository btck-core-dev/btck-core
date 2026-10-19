#!/usr/bin/env python3
"""Build the explicit signed-inference SDK dependency closure; no private profiles."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['LICENSE', 'scripts/l2_tool_agent.py', 'docs/L2_AGENT_QUICKSTART.md',
         'docs/examples/resident-agent.example.json', 'docs/examples/l2-preview/task.txt',
         'docs/examples/l2-preview/services.json',
         *['sdk/btck_agent/'+n+'.py' for n in ('__init__','wallet','ai_client','delegation','state_machine',
             'robot_wallet','inference_pilot','inference_receipt','tool_agent')],
         *['src/privacy/'+n+'.py' for n in ('__init__','stealth_address','wallet_engine','wallet_crypto')],
         *['src/tokenomics/'+n+'.py' for n in ('__init__','emission_schedule','canonical_daily_quotas')],
         *['src/worker/'+n+'.py' for n in ('__init__','resident_worker','agent_protocol')]]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    payload={name:(ROOT/name).read_bytes() for name in FILES}
    payload['README.md']=payload['docs/L2_AGENT_QUICKSTART.md']
    manifest={'format':'btck-l2-agent-preview-v1','currency':'development_satk','mainnet_funds_enabled':False,
              'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in payload.items()}}
    payload['MANIFEST.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(payload.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,10,18,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16;archive.writestr(info,raw)
    print(json.dumps({'artifact':str(args.output),'files':len(payload),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__=='__main__':main()

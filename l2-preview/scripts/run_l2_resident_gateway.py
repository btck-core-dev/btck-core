#!/usr/bin/env python3
"""Run the private development-credit gateway with resident GPU workers."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gateway.inference_pilot import InferencePilot
from gateway.resident_fleet import ResidentFleet
from worker.resident_worker import private_json


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);a=p.parse_args()
    os.umask(0o077);config=private_json(a.config)
    credentials=[w['token'] for w in config['workers'].values()]+[a['token'] for a in config['agents'].values()]
    if len(credentials)!=len(set(credentials)):raise ValueError('Each Agent and Worker requires a distinct credential')
    fleet=ResidentFleet(config['models'],config['workers'],task_timeout=config.get('task_timeout',120))
    pilot=InferencePilot(config['data_dir'],fleet,reference_price_usd=config.get('reference_price_usd','0.05'))
    try:
        for agent,info in config['agents'].items():
            with pilot.db() as db:present=db.execute('SELECT 1 FROM agents WHERE agent=?',(agent,)).fetchone()
            if not present:pilot.create_agent(agent,info['development_credits'],token=info['token'])
        if config.get('enrollment'):
            from gateway.worker_enrollment import WorkerEnrollment
            entry=config['enrollment']
            pilot.enrollment=WorkerEnrollment(entry['database'],entry['public_url'],config['models'])
            pilot.enrollment.restore_workers(fleet)
        server=pilot.make_server(config.get('port',18088))
        print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),
                          'receipt_signer_pubkey':pilot.signer.spend_pub_bytes.hex(),
                          'currency':'development_satk','mainnet_funds_enabled':False}),flush=True)
        try:server.serve_forever()
        finally:server.server_close()
    finally:pilot.close()


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Inspect local models and actual free VRAM; optionally save a new private node config."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from worker.inference_advisor import gpu_snapshot, scan_models, recommendations, estimate, launch_entry, runtime_metadata
from worker.resident_worker import private_json
from worker.connect_app import save_new


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--models',required=True,type=Path)
    p.add_argument('--gpus',help='Comma-separated GPU indices; default all measured GPUs')
    p.add_argument('--model',help='Explicit local directory name for manual selection')
    p.add_argument('--context',type=int,default=8192)
    p.add_argument('--kv',choices=['auto','fp8','turboquant_4bit_nc'],default='auto')
    p.add_argument('--prefill',type=int,choices=[128,256,512,1024],default=256)
    p.add_argument('--prefix-cache',choices=['auto','enabled','disabled'],default='auto')
    p.add_argument('--python',help='Python executable in your installed vLLM environment')
    p.add_argument('--node-config',type=Path,help='Existing private configuration to copy')
    p.add_argument('--save-config',type=Path,help='Save to a new private path; never overwrite')
    args=p.parse_args()
    cards=gpu_snapshot()
    if args.gpus:
        selected=[int(i.strip()) for i in args.gpus.split(',')]
        available={c['index']:c for c in cards};cards=[available[i] for i in selected]
    found=scan_models(args.models)
    runtime=runtime_metadata(args.python)
    plans=([estimate(next(m for m in found['models'] if m['id']==args.model),cards,args.context,args.kv,args.prefill,runtime,args.prefix_cache)]
           if args.model else recommendations(found['models'],cards,runtime))
    print(json.dumps({'gpus':cards,'runtime':runtime,'plans':plans,'skipped':found['skipped'],
                      'acceptance':'estimated_not_benchmarked'},ensure_ascii=False,indent=2))
    if args.save_config:
        if not (args.python and args.node_config and plans):p.error('Saving requires --python, --node-config and a fitting model')
        config=private_json(args.node_config);plan=plans[0]
        plan['backend_alias']=config.get('backend_model',config['model'])
        config['model_launch']=launch_entry(plan,args.python,config['backend_url'],config.get('backend_model',config['model']),
                                            args.save_config.resolve().with_name('model.log'))
        config.update(gpu_indices=plan['gpu_indices'],inference_recommendation=plan)
        if plan.get('chat_template_kwargs'):
            config['chat_template_kwargs']={**config.get('chat_template_kwargs',{}),**plan['chat_template_kwargs']}
        save_new(config,args.save_config.resolve())
    return 0 if plans else 2


if __name__=='__main__':raise SystemExit(main())

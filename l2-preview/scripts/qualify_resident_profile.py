#!/usr/bin/env python3
"""Opt-in local GPU qualification using bounded mining leases; no customer billing."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--worker-source',type=Path,default=Path(__file__).resolve().parents[1]/'src')
    p.add_argument('--label',required=True)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--target-tokens',type=int,default=3000)
    p.add_argument('--cases',default='short,tools,long')
    args=p.parse_args()
    sys.path.insert(0,str(args.worker_source))
    from worker.resident_worker import ResidentWorker,private_json
    from worker.mining_guard import process_birth
    from worker.mining_lease import MiningLease
    config=private_json(args.config)
    if config.get('mining_mode')!='pause_on_request':raise ValueError('Explicit mining pause mode required')
    request_limit=min(150,config.get('inference_timeout',90))
    worker=ResidentWorker(config)
    alive,miner=worker.miner_status()
    if not alive or not miner.get('miner_pid'):raise ValueError('Exact miner identity unavailable')
    pid=miner['miner_pid'];birth=process_birth(pid)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def post(route,body,timeout=request_limit):
        req=urllib.request.Request(worker.backend+route,json.dumps(body).encode(),{'Content-Type':'application/json'})
        return opener.open(req,timeout=timeout)
    def tokenize(messages,tools=None):
        body={'model':worker.backend_model,'messages':messages,'add_generation_prompt':True}
        if tools:body['tools']=tools
        # Tokenizer endpoint belongs to the API origin, outside /v1.
        req=urllib.request.Request(worker.backend.removesuffix('/v1')+'/tokenize',json.dumps(body).encode(),{'Content-Type':'application/json'})
        with opener.open(req,timeout=10) as response:return json.load(response)['count']
    def cpu_temperatures():
        values=[]
        for hw in Path('/sys/class/hwmon').glob('hwmon*'):
            try:
                if (hw/'name').read_text().strip()!='coretemp':continue
                for label in hw.glob('temp*_label'):
                    if label.read_text().startswith('Package id'):
                        values.append(float(label.with_name(label.name.replace('_label','_input')).read_text())/1000)
            except OSError:pass
        return values
    report={'profile':args.label,'started_at':time.time(),'cases':{},'customer_billing':False,
            'request_timeout_seconds':request_limit,
            'miner_pid':pid,'miner_birth':birth,'cpu_c_before':cpu_temperatures()}
    argv=config.get('model_launch',{}).get('command',[])
    report['launch_parameters']={k:argv[argv.index(k)+1] for k in
        ('--max-model-len','--max-num-batched-tokens','--gpu-memory-utilization','--kv-cache-dtype') if k in argv}
    models=worker.http(worker.backend+'/models')['data']
    report['backend_context_tokens']=next(m.get('max_model_len') for m in models if m['id']==worker.backend_model)
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with os.fdopen(os.open(args.output,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600),'w') as f:
            json.dump(report,f,indent=2);f.flush();os.fsync(f.fileno())
    def stream(messages,tools=None,choice=None,max_tokens=96):
        if any(t>=85 for t in cpu_temperatures()):raise RuntimeError('CPU temperature gate closed')
        payload={'model':worker.backend_model,'messages':messages,'max_tokens':max_tokens,
                 'temperature':0,'stream':True,'stream_options':{'include_usage':True},
                 'chat_template_kwargs':config.get('chat_template_kwargs',{})}
        if tools:payload['tools']=tools
        if choice:payload['tool_choice']=choice
        text='';calls={};usage=None;ttft=None;finish=None;bytes_read=0
        with MiningLease(config,pid,birth,worker.miner_name):
            started=time.monotonic()
            with post('/chat/completions',payload) as response:
                for line in response:
                    bytes_read+=len(line)
                    if bytes_read>1048576 or time.monotonic()-started>request_limit:raise TimeoutError('Qualification request exceeded bound')
                    if any(t>=85 for t in cpu_temperatures()):raise RuntimeError('CPU temperature gate closed')
                    if not line.startswith(b'data: '):continue
                    data=line[6:].strip()
                    if data==b'[DONE]':break
                    chunk=json.loads(data)
                    if chunk.get('usage'):usage=chunk['usage']
                    for item in chunk.get('choices',[]):
                        delta=item.get('delta',{})
                        if delta.get('content'):
                            if ttft is None:ttft=time.monotonic()-started
                            text+=delta['content']
                        for call in delta.get('tool_calls') or []:
                            target=calls.setdefault(call['index'],{'id':'','type':'function','function':{'name':'','arguments':''}})
                            if call.get('id'):target['id']=call['id']
                            for key in ('name','arguments'):
                                value=call.get('function',{}).get(key)
                                if value:
                                    if ttft is None:ttft=time.monotonic()-started
                                    target['function'][key]+=value
                        if item.get('finish_reason'):finish=item['finish_reason']
            elapsed=time.monotonic()-started
        if not usage or not ttft:raise ValueError('Missing measured streaming usage or first token')
        return {'text':text,'tool_calls':[calls[i] for i in sorted(calls)],'finish_reason':finish,
                'ttft_seconds':round(ttft,3),'elapsed_seconds':round(elapsed,3),'usage':usage}
    def record(name,value):
        report['cases'][name]=value;save()
        print(json.dumps({'case':name,**{k:v for k,v in value.items() if k not in ('text','tool_calls')}}),flush=True)
    save()
    try:
        cases=args.cases.split(',')
        if 'short' in cases:
            result=stream([{'role':'user','content':'Reply with exactly READY and nothing else.'}],max_tokens=16)
            result['passed']=result['text'].strip()=='READY' and result['finish_reason']=='stop';record('short',result)
        if 'tools' in cases:
            nonce=secrets.token_hex(8)
            tools=[{'type':'function','function':{'name':'lookup_service','description':'Read the current service status and its nonce.',
                     'parameters':{'type':'object','properties':{'service':{'type':'string'}},'required':['service'],'additionalProperties':False}}}]
            messages=[{'role':'user','content':'Call lookup_service for service btck-l2. Then return exactly the nonce from the tool response, with no other text.'}]
            first=stream(messages,tools,'required',128)
            valid=len(first['tool_calls'])==1 and first['finish_reason']=='tool_calls'
            if valid:
                call=first['tool_calls'][0]
                valid=call['function']['name']=='lookup_service' and json.loads(call['function']['arguments'])=={'service':'btck-l2'}
            first['passed']=valid;record('tool_request',first)
            if valid:
                messages += [{'role':'assistant','content':first['text'] or None,'tool_calls':first['tool_calls']},
                             {'role':'tool','tool_call_id':call['id'],'content':json.dumps({'status':'online','nonce':nonce})}]
                second=stream(messages,tools,'auto',96)
                second['passed']=second['text'].strip()==nonce and second['finish_reason']=='stop';record('tool_result',second)
        if 'long' in cases:
            expected={k:secrets.token_hex(6) for k in ('begin','middle','end')}
            def document(n):
                lines=[f'Record {i:05d}: ordinary reference, no special key, unused value.\n' for i in range(n)]
                lines.insert(n//2,'SPECIAL middle='+expected['middle']+'\n')
                return 'SPECIAL begin='+expected['begin']+'\n'+''.join(lines)+'SPECIAL end='+expected['end']
            n=max(1,args.target_tokens//20)
            for _ in range(5):
                messages=[{'role':'system','content':'Extract the SPECIAL begin, middle, end values from DATA. Return exactly a JSON object with those three keys and string values. No markdown.'},
                          {'role':'user','content':'DATA:\n'+document(n)}]
                count=tokenize(messages)
                if .995*args.target_tokens<=count<=args.target_tokens:break
                n=max(1,int(n*args.target_tokens/max(1,count)*.999))
            if report['backend_context_tokens'] and count+128>report['backend_context_tokens']:
                raise ValueError('Tokenized input does not leave room for the response')
            result=stream(messages,max_tokens=96)
            try:passed=json.loads(result['text'])==expected
            except ValueError:passed=False
            result.update(passed=passed and result['finish_reason']=='stop',tokenized_input=count,
                          prompt_bytes=len(json.dumps(messages,ensure_ascii=False).encode()))
            record('long_retrieval',result)
        report['passed']=all(v['passed'] for v in report['cases'].values()) and bool(report['cases'])
    except Exception as error:
        report.update(passed=False,error_type=type(error).__name__)
        print(json.dumps({'qualification_error':type(error).__name__}),flush=True)
    finally:
        report.update(cpu_c_after=cpu_temperatures(),finished_at=time.time(),miner_same_identity=process_birth(pid)==birth)
        save()
    return 0 if report['passed'] else 2


if __name__=='__main__':raise SystemExit(main())

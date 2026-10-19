"""Read-only local inventory and conservative, explicitly estimated launch plans.

No downloads, model imports, benchmarks, miner control or executable discovery by
guessing. Only directories and runtime executables selected by the owner are used.
"""
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from worker.qualified_profiles import REFERENCE_PROFILES, ENGINE_CONSTRAINTS


def runtime_metadata(python_executable):
    """Ask the owner-selected Python for installed metadata; never import the model."""
    if not python_executable or not Path(python_executable).is_file():return None
    program = '''import hashlib,importlib.metadata,json
d=importlib.metadata.distribution('vllm')
files=['vllm/config/cache.py','vllm/model_executor/models/qwen3_5.py','vllm/v1/attention/backends/flash_attn.py']
paths=[d.locate_file(name) for name in files]
if any(not p.is_file() or p.stat().st_size>4194304 for p in paths):raise ValueError('Unsupported installation')
print(json.dumps({'engine':'vllm','version':d.version,'code_sha256':hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()}))'''
    try:
        import os
        raw=subprocess.check_output([str(python_executable),'-I','-c',program],timeout=5,
                                    stderr=subprocess.DEVNULL,env={**os.environ,'CUDA_VISIBLE_DEVICES':''})
        data=json.loads(raw)
        if set(data)!= {'engine','version','code_sha256'} or data['engine']!='vllm':return None
        return data
    except (OSError,ValueError,subprocess.SubprocessError):return None


def reference_matches(model,cards,runtime,reference):
    return bool(runtime and runtime==reference['runtime'] and
                model.get('metadata_sha256')==reference['model_metadata_sha256'] and
                len(cards)==reference['gpu_count'] and
                all(c['name']==reference['gpu_name'] and
                    abs(c['total_mib']-reference['per_gpu_mib'])<=128 for c in cards))


def gpu_snapshot():
    raw = subprocess.check_output([
        'nvidia-smi', '--query-gpu=index,name,memory.total,memory.free,temperature.gpu',
        '--format=csv,noheader,nounits'], timeout=5, stderr=subprocess.DEVNULL).decode()
    cards = []
    for row in csv.reader(raw.splitlines()):
        index, name, total, free, temperature = [x.strip() for x in row]
        card = dict(index=int(index), name=name, total_mib=int(total), free_mib=int(free),
                    temperature_c=int(temperature))
        if not 0 <= card['free_mib'] <= card['total_mib']:
            raise ValueError('Invalid GPU memory measurement')
        cards.append(card)
    if not cards:
        raise ValueError('No NVIDIA GPU memory measurement available')
    return cards


def read_json(path, limit=4 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('Model metadata is too large')
    return json.loads(path.read_text(encoding='utf-8'))


def inspect_model(directory):
    """HF safetensors only; measure files, never load weights or execute model code."""
    root = Path(directory).resolve()
    config = read_json(root / 'config.json')
    text = config.get('text_config', config)
    index = root / 'model.safetensors.index.json'
    if index.exists():
        names = set(read_json(index, 32 * 1024 * 1024)['weight_map'].values())
        if any(Path(n).name != n or not n.endswith('.safetensors') for n in names):
            raise ValueError('Weight index must reference local safetensors filenames')
        files = [root / n for n in names]
    else:
        files = list(root.glob('*.safetensors'))
    if not files or len(files) > 512:
        raise ValueError('Complete local safetensors weights are required')
    size = sum(p.stat().st_size for p in files)
    if size <= 0:
        raise ValueError('Empty weights')
    tokenizer = root / 'tokenizer_config.json'
    template = read_json(tokenizer).get('chat_template', '') if tokenizer.exists() else ''
    quant = config.get('quantization_config') or text.get('quantization_config') or {}
    bits = quant.get('bits', quant.get('weight_bits'))
    kind = str(text.get('model_type', config.get('model_type', 'unknown')))
    layers = text.get('layer_types')
    attention_layers = (sum(x == 'full_attention' for x in layers)
                        if layers and all(x in ('full_attention', 'linear_attention') for x in layers)
                        else text.get('num_hidden_layers'))
    heads = text.get('num_key_value_heads', text.get('num_attention_heads'))
    dim = text.get('head_dim')
    if not dim and text.get('hidden_size') and text.get('num_attention_heads'):
        dim = text['hidden_size'] // text['num_attention_heads']
    metadata_hash=hashlib.sha256((root/'config.json').read_bytes()+(index.read_bytes() if index.exists() else b'')).hexdigest()
    return dict(path=str(root), id=root.name, weight_mib=math.ceil(size / 1048576),metadata_sha256=metadata_hash,
                weight_bits=bits, quantization=quant.get('quant_method', ''), model_type=kind,
                attention_layers=attention_layers, kv_heads=heads, head_dim=dim,
                attention_heads=text.get('num_attention_heads'),
                model_context=text.get('max_position_embeddings'),
                hybrid=bool(layers and 'linear_attention' in layers),
                tool_template_detected='tools' in str(template),
                acceptance='estimated; tool accuracy and latency not benchmarked')


def scan_models(directory):
    """Selected folder itself or immediate children, bounded to 128 entries."""
    root = Path(directory).expanduser().resolve()
    if (root / 'config.json').is_file():
        paths = [root]
    else:
        paths = sorted(root.iterdir())[:128]
    models, skipped = [], []
    for path in paths:
        if not path.is_dir() or not (path / 'config.json').is_file():
            continue
        try:
            models.append(inspect_model(path))
        except (OSError, ValueError, KeyError, TypeError):
            skipped.append(path.name)
    return {'models': models, 'skipped': skipped}


def estimate(model, cards, context=8192, kv='auto', prefill=256, runtime=None, prefix_mode='auto', speculation='off'):
    """Per-card estimate reserves miner allocations already included in free VRAM.

    Do not reclaim any currently loaded model's memory: its ownership is unknown.
    Memory used by another process remains unavailable, even if its name is vLLM.
    """
    if not cards or len({c['index'] for c in cards}) != len(cards):
        raise ValueError('Select distinct GPUs')
    if type(context) is not int or context not in (4096, 8192, 16384, 32768, 65536, 131072):
        raise ValueError('Choose a supported context size')
    if kv not in ('auto', 'fp8', 'turboquant_4bit_nc') or prefill not in (128, 256, 512, 1024):
        raise ValueError('Choose a supported KV/prefill setting')
    if prefix_mode not in ('auto','enabled','disabled'):raise ValueError('Choose a prefix-cache mode')
    if speculation not in ('off','ngram'):raise ValueError('Choose a speculative method')
    tp = len(cards)
    reasons = []
    heads = model.get('attention_heads')
    if not isinstance(heads, int) or heads % tp:
        reasons.append('Attention heads do not establish support for this TP size')
    if not model.get('model_context') or context > model['model_context']:
        reasons.append('Requested context is not established by model metadata')
    values = [model.get(k) for k in ('attention_layers', 'kv_heads', 'head_dim')]
    if any(type(x) is not int or x <= 0 for x in values):
        reasons.append('KV layout is unknown; choose a model with complete metadata')
        kv_mib = None
    else:
        layers, kv_heads, head_dim = values
        # TP can replicate KV heads; never divide aggregate KV bytes blindly.
        per_rank_heads = math.ceil(kv_heads / tp)
        kv_bytes = {'auto': 2, 'fp8': 1, 'turboquant_4bit_nc': .625}[kv]
        kv_mib = math.ceil(2 * layers * per_rank_heads * head_dim * context * kv_bytes / 1048576)
    if not model.get('tool_template_detected'):
        reasons.append('Tool-call template not detected; Agent capability needs verification')
    prefix_enabled=not model.get('hybrid') if prefix_mode=='auto' else prefix_mode=='enabled'
    compatible=True
    minimum=None
    for constraint in ENGINE_CONSTRAINTS:
        if reference_matches(model,cards,runtime,constraint) and kv==constraint['kv_cache_dtype']:
            minimum=constraint['minimum_prefix_prefill_tokens'];break
    if prefix_mode=='auto' and minimum is not None:prefix_enabled=prefill>=minimum
    if prefix_enabled and minimum is not None and prefill<minimum:
        compatible=False
        reasons.append(f'Prefix caching requires prefill >= {minimum} for this runtime/model')
    unverified_prefix='Hybrid prefix-cache compatibility needs validation'
    if prefix_enabled and model.get('hybrid') and minimum is None:reasons.append(unverified_prefix)
    # Weight partitioning, activations, hybrid state, allocator and prefill workspace.
    # These are planning margins, not proof the runtime can load this model.
    weights = math.ceil(model['weight_mib'] * 1.15 / tp)
    workspace = 1536 + (1024 if model.get('hybrid') else 0) + prefill
    required = weights + workspace + (kv_mib or 0)
    budgets = [{'index': c['index'], 'free_mib': c['free_mib'],
                'reserve_mib': max(1024, math.ceil(c['total_mib'] * .08))}
               for c in cards]
    for b in budgets:
        b['model_budget_mib'] = max(0, b['free_mib'] - b['reserve_mib'])
    fits = kv_mib is not None and all(required <= b['model_budget_mib'] for b in budgets)
    if not fits:
        reasons.append('Insufficient measured free memory after reserves, or unknown KV layout')
    if kv != 'auto':
        reasons.append('Quantized KV backend compatibility and accuracy require local validation')
    result=dict(model=model, cards=cards, gpu_indices=[c['index'] for c in cards], context=context,
                kv_cache_dtype=kv, prefill_tokens=prefill, tensor_parallel_size=tp,
                prefix_caching=prefix_enabled, prefix_mode=prefix_mode, chunked_prefill=True, speculation=speculation,
                launch_compatible=compatible,
                estimated_required_per_gpu_mib=required, kv_per_gpu_mib=kv_mib,
                budgets=budgets, fits_estimate=fits, reasons=reasons,
                recommended=fits and not reasons, acceptance='estimated_not_benchmarked')
    for reference in REFERENCE_PROFILES:
        if (reference_matches(model,cards,runtime,reference) and
            speculation==reference.get('speculation','off') and
            (prefix_mode=='auto' or (prefix_mode=='enabled')==reference.get('prefix_caching',True)) and
            (context,kv,prefill)==(reference['context'],reference['kv_cache_dtype'],reference['prefill_tokens'])):
            result['reference_qualification']={k:reference[k] for k in ('id','measurements','context','prefill_tokens')}
            if unverified_prefix in result['reasons']:result['reasons'].remove(unverified_prefix)
            result['prefix_caching']=reference.get('prefix_caching',True)
            result['chat_template_kwargs']=dict(reference.get('chat_template_kwargs',{}))
            if reference.get('measured_model_peak_mib'):
                required=math.ceil(reference['measured_model_peak_mib']*1.05)
                result['estimated_required_per_gpu_mib']=required
                result['fits_estimate']=all(required<=b['model_budget_mib'] for b in budgets)
                memory_reason='Insufficient measured free memory after reserves, or unknown KV layout'
                if result['fits_estimate'] and memory_reason in result['reasons']:result['reasons'].remove(memory_reason)
                elif not result['fits_estimate'] and memory_reason not in result['reasons']:result['reasons'].append(memory_reason)
                result['memory_basis']='reference_measurement_with_5_percent_headroom_plus_GPU_reserve'
                result['gpu_memory_utilization']=reference['gpu_memory_utilization']
            result['recommended']=result['fits_estimate'] and not result['reasons']
            break
    return result


def recommendations(models, cards, runtime=None):
    """Recommend local tool-capable models only. Never substitute a tiny model."""
    candidates = []
    for model in models:
        # Prefer practical Agent capacity; disk size is not a model quality score.
        if model['weight_mib'] < 6144:
            continue
        reference_plan=None
        reference_contexts=[]
        for reference in REFERENCE_PROFILES:
            if reference_matches(model,cards,runtime,reference):
                reference_contexts.append(reference['context'])
                plan=estimate(model,cards,reference['context'],reference['kv_cache_dtype'],reference['prefill_tokens'],runtime,
                              'enabled' if reference.get('prefix_caching',True) else 'disabled')
                if plan['recommended']:reference_plan=plan;break
        if reference_plan:
            candidates.append(reference_plan);continue
        for context in (32768, 16384, 8192, 4096):
            if reference_contexts and context>max(reference_contexts):continue
            plan = estimate(model, cards, context)
            if plan['recommended']:
                candidates.append(plan)
                break
    return sorted(candidates, key=lambda p: (not bool(p.get('reference_qualification')),-p['context'], -p['model']['weight_mib'], p['model']['id']))


def launch_entry(plan, python_executable, backend_url, alias, log, speculation=None):
    """Build actual vLLM startup arguments, never shell text or a remote endpoint."""
    if not plan['fits_estimate']:
        raise ValueError('Memory estimate does not fit selected GPUs')
    if not plan.get('launch_compatible',True):raise ValueError('Selected cache and prefill settings are incompatible')
    if speculation is None:speculation=plan.get('speculation','off')
    if speculation not in ('off', 'ngram'):
        raise ValueError('Unsupported speculative method')
    u = urlsplit(backend_url)
    if u.scheme != 'http' or u.hostname != '127.0.0.1' or u.username or u.password or u.query or u.fragment or u.path.rstrip('/') != '/v1':
        raise ValueError('Use http://127.0.0.1:PORT/v1 for a managed local model')
    if not Path(python_executable).is_file() or not alias.strip():
        raise ValueError('Choose your installed vLLM Python executable and model alias')
    model = plan['model']
    command = [str(python_executable), '-m', 'vllm.entrypoints.openai.api_server',
               '--host', '127.0.0.1', '--port', str(u.port or 80), '--model', model['path'],
               '--served-model-name', alias, '--tensor-parallel-size', str(plan['tensor_parallel_size']),
               '--distributed-executor-backend', 'mp', '--max-model-len', str(plan['context']),
               '--max-num-seqs', '1', '--max-num-batched-tokens', str(plan['prefill_tokens']),
               '--enable-prefix-caching' if plan['prefix_caching'] else '--no-enable-prefix-caching',
               '--enable-chunked-prefill', '--kv-cache-dtype', plan['kv_cache_dtype'],
               '--enforce-eager']
    if model.get('quantization') == 'awq':
        command.extend(['--quantization', 'awq_marlin', '--dtype', 'float16'])
    if plan['tensor_parallel_size']>1:
        command.append('--disable-custom-all-reduce')
    if model.get('tool_template_detected') and model['model_type'].startswith(('qwen3_5', 'qwen3_6')):
        command.extend(['--enable-auto-tool-choice', '--tool-call-parser', 'qwen3_xml', '--language-model-only'])
    if speculation == 'ngram':
        command.extend(['--speculative-config', json.dumps({'method': 'ngram', 'num_speculative_tokens': 3})])
    # gpu-memory-utilization is a fraction of physical total, not current free memory.
    fraction = min(b['model_budget_mib'] / c['total_mib'] for b, c in zip(plan['budgets'], plan['cards']))
    fraction = min(fraction,plan.get('gpu_memory_utilization',fraction))
    command.extend(['--gpu-memory-utilization', str(math.floor(min(.90, fraction) * 1000) / 1000)])
    return {'command': command, 'env': {'CUDA_VISIBLE_DEVICES': ','.join(map(str, plan['gpu_indices'])),
            'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false',
            'OMP_NUM_THREADS':'4','MKL_NUM_THREADS':'4'}, 'log': str(log)}

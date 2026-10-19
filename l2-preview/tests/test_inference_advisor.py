import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from worker.inference_advisor import gpu_snapshot, inspect_model, scan_models, estimate, recommendations, launch_entry, runtime_metadata
from worker.connect_app import make_config
from worker.service_info import BILLING_TEXT


class AdvisorTests(unittest.TestCase):
    def model(self):
        return dict(id='local-agent',path='/local/model',weight_mib=20000,attention_layers=10,
                    kv_heads=2,head_dim=256,attention_heads=16,model_context=262144,
                    hybrid=True,tool_template_detected=True,model_type='qwen3_5_moe_text',quantization='awq')
    def cards(self):
        return [dict(index=i,total_mib=16384,free_mib=11264,name='4070 Ti SUPER',temperature_c=60) for i in range(4)]
    def test_per_rank_kv_replication_and_smallest_card_budget(self):
        p=estimate(self.model(),self.cards(),32768)
        self.assertEqual(p['kv_per_gpu_mib'],320)
        self.assertTrue(p['recommended'])
        cards=self.cards();cards[3]['free_mib']=4000
        self.assertFalse(estimate(self.model(),cards,4096)['fits_estimate'])
        self.assertEqual(recommendations([self.model()],cards),[])
    def test_quantized_kv_is_not_automatically_qualified(self):
        p=estimate(self.model(),self.cards(),32768,'turboquant_4bit_nc')
        self.assertLess(p['kv_per_gpu_mib'],320)
        self.assertFalse(p['recommended'])
        self.assertEqual(recommendations([self.model()],self.cards())[0]['kv_cache_dtype'],'auto')
    def test_no_generic_fallback_no_tiny_agent_recommendation(self):
        with patch('worker.inference_advisor.subprocess.check_output',return_value=b''):
            with self.assertRaises(ValueError):gpu_snapshot()
        self.assertFalse(recommendations([{**self.model(),'weight_mib':4000}],self.cards()))
        self.assertFalse(recommendations([{**self.model(),'tool_template_detected':False}],self.cards()))
    def test_memory_snapshot_uses_actual_free_not_total(self):
        raw=b'0, RTX 4070, 16384, 1000, 60\n'
        with patch('worker.inference_advisor.subprocess.check_output',return_value=raw):
            self.assertEqual(gpu_snapshot()[0]['free_mib'],1000)
        with patch('worker.inference_advisor.subprocess.check_output',return_value=b'0, GPU, 100, 200, 60\n'):
            with self.assertRaises(ValueError):gpu_snapshot()
    def test_local_metadata_scan_does_not_load_or_execute_weights(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);m=root/'local';m.mkdir()
            (m/'config.json').write_text(json.dumps({'model_type':'qwen3_5_moe_text','num_hidden_layers':2,
                'num_key_value_heads':2,'num_attention_heads':16,'head_dim':128,'max_position_embeddings':8192}))
            (m/'tokenizer_config.json').write_text(json.dumps({'chat_template':'{% if tools %}'}))
            (m/'model.safetensors').write_bytes(b'not loaded as tensors')
            result=scan_models(root)
            self.assertEqual(len(result['models']),1)
            self.assertTrue(result['models'][0]['tool_template_detected'])
            # MoE indexes contain many expert names and can exceed 4 MiB.
            (m/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'x':'model.safetensors'},'metadata':{'note':'x'*(4*1024*1024)}}))
            self.assertEqual(inspect_model(m)['weight_mib'],1)
            (m/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'x':'../outside.safetensors'}}))
            with self.assertRaises(ValueError):inspect_model(m)
    def test_launch_uses_list_and_correct_physical_memory_fraction(self):
        with tempfile.TemporaryDirectory() as td:
            python=Path(td)/'python';python.touch()
            p=estimate(self.model(),self.cards(),16384)
            entry=launch_entry(p,python,'http://127.0.0.1:8000/v1','agent',Path(td)/'log')
            self.assertEqual(entry['env']['CUDA_VISIBLE_DEVICES'],'0,1,2,3')
            argv=entry['command'];self.assertIn('--enable-chunked-prefill',argv)
            self.assertEqual(argv[argv.index('--max-model-len')+1],'16384')
            self.assertLess(float(argv[argv.index('--gpu-memory-utilization')+1]),.69)
            with self.assertRaises(ValueError):launch_entry(p,python,'http://remote:8000/v1','agent','log')
    def test_unknown_or_invalid_layout_cannot_be_recommended(self):
        for changed in ({'kv_heads':None},{'model_context':None},{'attention_heads':15}):
            self.assertFalse(recommendations([{**self.model(),**changed}],self.cards()))
        with self.assertRaises(ValueError):estimate(self.model(),self.cards(),-1)
    def test_launch_settings_survive_gui_save_and_billing_is_public_only(self):
        values=dict(gateway='https://compute.example.invalid',backend='http://127.0.0.1:8000/v1',
            token='w'*64,model='agent',gpus='0',miner='SRBMiner-MULTI',pearl='http://127.0.0.1:21550',floor='')
        launch={'command':['/local/python','-m','local_server'],'log':'/tmp/private.log'}
        config=make_config(values,Path('/tmp/worker.json'),{'model_launch':launch})
        self.assertEqual(config['model_launch'],launch)
        self.assertIn('KCU',BILLING_TEXT)
        for hidden in ('canary','logprobs','hardware fingerprint'):
            self.assertNotIn(hidden,BILLING_TEXT.lower())

    def test_reference_measurements_match_runtime_and_model_and_exact_settings(self):
        runtime={'engine':'vllm','version':'fixture-version','code_sha256':'a'*64}
        model={**self.model(),'metadata_sha256':'b'*64}
        reference={'id':'reference-fixture','runtime':runtime,'model_metadata_sha256':'b'*64,
                   'gpu_count':4,'gpu_name':'4070 Ti SUPER','per_gpu_mib':16384,
                   'context':16384,'kv_cache_dtype':'auto','prefill_tokens':128,
                   'measurements':{'fixture':True}}
        with patch('worker.inference_advisor.REFERENCE_PROFILES',[reference]):
            plan=recommendations([model],self.cards(),runtime)[0]
            self.assertEqual(plan['context'],16384)
            self.assertEqual(plan['prefill_tokens'],128)
            self.assertIn('reference_qualification',plan)
            for different in (None,{**runtime,'version':'other'}):
                plan=recommendations([model],self.cards(),different)[0]
                self.assertNotIn('reference_qualification',plan)
            wrong=estimate(model,self.cards(),16384,'fp8',128,runtime)
            self.assertNotIn('reference_qualification',wrong)
            small=self.cards();small[-1]['free_mib']=1000
            self.assertEqual(recommendations([model],small,runtime),[])

    def test_runtime_probe_uses_selected_python_and_hides_gpu(self):
        self.assertIsNone(runtime_metadata('/does-not-exist/python'))
        with tempfile.TemporaryDirectory() as td:
            python=Path(td)/'python';python.touch()
            data={'engine':'vllm','version':'0.20.0','code_sha256':'a'*64}
            with patch('worker.inference_advisor.subprocess.check_output',return_value=json.dumps(data).encode()) as run:
                self.assertEqual(runtime_metadata(str(python)),data)
                self.assertEqual(run.call_args.args[0][:2],[str(python),'-I'])
                self.assertEqual(run.call_args.kwargs['env']['CUDA_VISIBLE_DEVICES'],'')

    def test_hybrid_prefix_alignment_failure_is_rejected_before_launch(self):
        runtime={'engine':'vllm','version':'fixture','code_sha256':'a'*64}
        model={**self.model(),'metadata_sha256':'b'*64}
        constraint={'runtime':runtime,'model_metadata_sha256':'b'*64,'gpu_count':4,
                    'gpu_name':'4070 Ti SUPER','per_gpu_mib':16384,'kv_cache_dtype':'auto',
                    'minimum_prefix_prefill_tokens':528}
        with patch('worker.inference_advisor.ENGINE_CONSTRAINTS',[constraint]):
            auto=estimate(model,self.cards(),16384,'auto',128,runtime)
            self.assertFalse(auto['prefix_caching'])
            forced=estimate(model,self.cards(),16384,'auto',128,runtime,'enabled')
            self.assertFalse(forced['launch_compatible'])
            with self.assertRaisesRegex(ValueError,'cache and prefill'):launch_entry(forced,'unused','http://127.0.0.1:8000/v1','agent','log')
            large=estimate(model,self.cards(),16384,'auto',1024,runtime,'enabled')
            self.assertTrue(large['launch_compatible']);self.assertTrue(large['prefix_caching'])

    def test_dual3090_dense27b_prefix_prefill_constraint_does_not_claim_gpu_qualification(self):
        from worker.qualified_profiles import ENGINE_CONSTRAINTS
        constraint=next(c for c in ENGINE_CONSTRAINTS if c['gpu_count']==2)
        model={**self.model(),'metadata_sha256':constraint['model_metadata_sha256'],
               'attention_heads':24,'model_type':'qwen3_5_text','kv_heads':4}
        cards=[dict(index=i,total_mib=24576,free_mib=21882,name='NVIDIA GeForce RTX 3090',temperature_c=65) for i in range(2)]
        small=estimate(model,cards,16384,'auto',512,constraint['runtime'],prefix_mode='enabled')
        self.assertFalse(small['launch_compatible'])
        self.assertTrue(any('784' in reason for reason in small['reasons']))
        compatible=estimate(model,cards,16384,'auto',1024,constraint['runtime'],prefix_mode='enabled')
        self.assertTrue(compatible['launch_compatible'])
        self.assertNotIn('reference_qualification',compatible)

    def test_reference_memory_keeps_headroom_and_does_not_override_manual_cache_choice(self):
        runtime={'engine':'vllm','version':'fixture','code_sha256':'a'*64}
        model={**self.model(),'metadata_sha256':'b'*64}
        reference={'id':'reference','runtime':runtime,'model_metadata_sha256':'b'*64,'gpu_count':4,
                   'gpu_name':'4070 Ti SUPER','per_gpu_mib':16384,'kv_cache_dtype':'auto',
                   'context':16384,'prefill_tokens':1024,'prefix_caching':True,'measurements':{},
                   'measured_model_peak_mib':9000,'gpu_memory_utilization':.55}
        with patch('worker.inference_advisor.REFERENCE_PROFILES',[reference]):
            plan=recommendations([model],self.cards(),runtime)[0]
            self.assertEqual(plan['estimated_required_per_gpu_mib'],9450)
            self.assertEqual(plan['gpu_memory_utilization'],.55)
            manual=estimate(model,self.cards(),16384,'auto',1024,runtime,'disabled')
            self.assertFalse(manual['prefix_caching']);self.assertNotIn('reference_qualification',manual)
            speculative=estimate(model,self.cards(),16384,'auto',1024,runtime,'enabled','ngram')
            self.assertNotIn('reference_qualification',speculative)
            small=self.cards();small[-1]['free_mib']=9500
            plan=estimate(model,small,16384,'auto',1024,runtime,'enabled')
            self.assertFalse(plan['fits_estimate'])

    def test_reference_reasoning_and_model_changes_do_not_keep_stale_qualification(self):
        values=dict(gateway='https://compute.example.invalid',backend='http://127.0.0.1:8000/v1',
                    token='w'*64,model='agent',gpus='0',miner='SRBMiner-MULTI',pearl='http://127.0.0.1:21550',floor='')
        reference={'model':{'id':'artifact'},'backend_alias':'agent','reference_qualification':{'id':'reference'},
                   'chat_template_kwargs':{'enable_thinking':False}}
        previous={'inference_recommendation':reference,'model_launch':{'command':['local-model'],'log':'model.log'}}
        selected=make_config({**values,'reasoning':'Disabled'},Path('/tmp/node.json'),previous)
        self.assertFalse(selected['chat_template_kwargs']['enable_thinking'])
        self.assertIn('reference_qualification',selected['inference_recommendation'])
        changed=make_config({**values,'reasoning':'Enabled'},Path('/tmp/node.json'),previous)
        self.assertNotIn('reference_qualification',changed['inference_recommendation'])
        changed=make_config({**values,'model':'other-model'},Path('/tmp/node.json'),previous)
        self.assertNotIn('model_launch',changed);self.assertNotIn('inference_recommendation',changed)


if __name__=='__main__':unittest.main()

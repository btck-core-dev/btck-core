import copy
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parents[1]/'src'),str(Path(__file__).parents[1]/'sdk')]
from gateway.inference_pilot import InferencePilot,PilotError,BackendRejected,HTTPInferenceProvider,verify_receipt
from btck_agent.inference_pilot import PilotClient
from worker.pricing_engine import TieredPricingEngine

class Provider:
    model='fixture-model'
    def __init__(self):self.calls=0;self.error=None
    def complete(self,request):
        self.calls+=1
        if self.error:raise self.error
        return {'text':'一段真实用量格式的测试文本','usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10},'model':self.model,'provider_request_id':str(self.calls)}

class TestInferencePilot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.provider=Provider();self.path=Path(self.tmp.name)/'live';self.pilot=InferencePilot(self.path,self.provider)
        self.addCleanup(lambda:self.pilot.close());self.token=self.pilot.create_agent('agent',5_000_000)
        self.request={'model':self.provider.model,'messages':[{'role':'user','content':'中文没有空格'}],'max_tokens':32,'max_charge_satk':1_000_000}
    def test_measured_usage_exact_split_and_conservation(self):
        result,replayed=self.pilot.complete('agent','one',self.request)
        self.assertFalse(replayed);self.assertEqual(result['usage']['prompt_tokens'],7)
        self.assertEqual(result['usage']['completion_tokens'],3);self.assertEqual(result['usage']['pricing_units'],12)
        cost=1920  # Fixed pricing vector: 12 units, rate 0.8, reference price 0.05.
        self.assertEqual(result['usage']['cost_satk'],cost)
        self.assertEqual(sum(result['receipt']['payload']['distribution'].values()),cost)
        self.assertEqual(self.pilot.balance('agent')['available_satk'],5_000_000-cost)
        self.assertTrue(self.pilot.conservation()['conserved']);self.assertTrue(verify_receipt(result,self.pilot.signer.spend_pub_bytes.hex()))
    def test_completed_request_replays_exactly_after_restart(self):
        first,_=self.pilot.complete('agent','one',self.request);pub=self.pilot.signer.spend_pub_bytes.hex()
        self.pilot.close();self.pilot=InferencePilot(self.path,self.provider)
        second,replayed=self.pilot.complete('agent','one',self.request)
        self.assertTrue(replayed);self.assertEqual(first,second);self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.pilot.signer.spend_pub_bytes.hex(),pub)
    def test_idempotency_payload_conflict_does_not_infer_or_charge(self):
        self.pilot.complete('agent','one',self.request);before=self.pilot.balance('agent')
        other={**self.request,'messages':[{'role':'user','content':'different'}]}
        with self.assertRaises(PilotError) as raised:self.pilot.complete('agent','one',other)
        self.assertEqual(raised.exception.code,'idempotency_conflict');self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.pilot.balance('agent'),before)
    def test_insufficient_authorized_budget_is_checked_before_inference(self):
        request={**self.request,'max_charge_satk':6_000_000}
        with self.assertRaises(PilotError) as raised:self.pilot.complete('agent','one',request)
        self.assertEqual(raised.exception.status,402);self.assertEqual(self.provider.calls,0)
    def test_measured_cost_over_cap_refunds_without_fabricated_charge(self):
        with self.assertRaises(PilotError) as raised:self.pilot.complete('agent','one',{**self.request,'max_charge_satk':1})
        self.assertEqual(raised.exception.code,'budget_exceeded')
        self.assertEqual(self.pilot.balance('agent'),{'currency':'development_satk','available_satk':5_000_000,'held_satk':0})
        self.assertTrue(self.pilot.conservation()['conserved'])
    def test_uncertain_provider_is_not_reexecuted_or_automatically_refunded(self):
        self.provider.error=TimeoutError('fixture timeout')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.pilot.task('agent','one')['status'],'UNKNOWN')
        self.assertEqual(self.pilot.balance('agent')['held_satk'],1_000_000)
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.provider.calls,1);self.assertTrue(self.pilot.conservation()['conserved'])
    def test_operator_unknown_waiver_is_atomic_and_cannot_release_twice(self):
        self.provider.error=TimeoutError('fixture uncertainty')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.pilot.waive_unknown('agent','one','Operator accepts compute loss; evidence fixture-1')
        self.assertEqual(self.pilot.balance('agent')['available_satk'],5000000)
        self.assertEqual(self.pilot.balance('agent')['held_satk'],0)
        self.assertTrue(self.pilot.conservation()['conserved'])
        with self.assertRaises(PilotError):self.pilot.waive_unknown('agent','one','Duplicate waiver evidence fixture-1')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.provider.calls,1)
        with self.pilot.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0],1)
    def test_waiver_storage_failure_keeps_hold_and_rolls_back_audit(self):
        self.provider.error=TimeoutError('fixture uncertainty')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        with self.pilot.db(write=True) as db:db.execute("CREATE TRIGGER fail_waiver BEFORE UPDATE ON tasks WHEN NEW.status='WAIVED' BEGIN SELECT RAISE(ABORT,'injected waiver failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.pilot.waive_unknown('agent','one','Operator fixture evidence reference')
        self.assertEqual(self.pilot.balance('agent')['held_satk'],1000000)
        self.assertEqual(self.pilot.task('agent','one')['status'],'UNKNOWN')
        with self.pilot.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0],0)
        self.assertTrue(self.pilot.conservation()['conserved'])

    def test_operator_waiver_never_refunds_a_settled_task(self):
        self.pilot.complete('agent','one',self.request);before=self.pilot.balance('agent')
        with self.assertRaises(PilotError):self.pilot.waive_unknown('agent','one','Must not alter a completed payment')
        self.assertEqual(self.pilot.balance('agent'),before)

    def test_definite_rejection_releases_reservation(self):
        self.provider.error=BackendRejected('invalid provider request')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.pilot.task('agent','one')['status'],'FAILED')
        self.assertEqual(self.pilot.balance('agent')['available_satk'],5_000_000)
        self.assertEqual(self.pilot.balance('agent')['held_satk'],0)
    def test_crash_reservation_becomes_unknown_after_restart(self):
        self.provider.error=SystemExit('process crash fixture')
        with self.assertRaises(SystemExit):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.pilot.task('agent','one')['status'],'IN_FLIGHT')
        self.pilot.close();self.pilot=InferencePilot(self.path,self.provider)
        self.assertEqual(self.pilot.task('agent','one')['status'],'UNKNOWN')
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.provider.calls,1);self.assertTrue(self.pilot.conservation()['conserved'])
    def test_sqlite_failure_rolls_back_entire_settlement(self):
        with self.pilot.db(write=True) as db:db.execute("CREATE TRIGGER fail_settlement BEFORE UPDATE ON tasks WHEN NEW.status='SETTLED' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.pilot.balance('agent')['available_satk'],4_000_000)
        self.assertEqual(self.pilot.balance('agent')['held_satk'],1_000_000)
        self.assertTrue(self.pilot.conservation()['conserved'])
        self.pilot.close();self.pilot=InferencePilot(self.path,self.provider)
        with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        self.assertEqual(self.provider.calls,1)
    def test_concurrent_duplicate_has_one_provider_execution(self):
        entered=threading.Event();release=threading.Event();original=self.provider.complete;results=[]
        def slow(request):entered.set();release.wait(5);return original(request)
        self.provider.complete=slow
        t=threading.Thread(target=lambda:results.append(self.pilot.complete('agent','one',self.request)),daemon=True);t.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(PilotError):self.pilot.complete('agent','one',self.request)
        finally:release.set();t.join(5)
        self.assertFalse(t.is_alive());self.assertEqual(self.provider.calls,1);self.assertEqual(len(results),1)
    def test_backup_restores_ledger_identity_receipt_and_no_second_gpu_call(self):
        result,_=self.pilot.complete('agent','one',self.request);dest=Path(self.tmp.name)/'backup'
        manifest=self.pilot.backup(dest);self.assertEqual(set(manifest),{'pilot.sqlite3','receipt-identity.json'})
        restored_path=InferencePilot.restore_backup(dest,Path(self.tmp.name)/'restored')
        restored=InferencePilot(restored_path,self.provider);self.addCleanup(restored.close)
        reply,replayed=restored.complete('agent','one',self.request)
        self.assertTrue(replayed);self.assertEqual(result,reply);self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.pilot.balance('agent'),restored.balance('agent'))
        self.assertTrue(restored.conservation()['conserved'])
    def test_corrupt_backup_is_rejected_before_restore(self):
        dest=Path(self.tmp.name)/'backup';self.pilot.backup(dest)
        with (dest/'pilot.sqlite3').open('r+b') as f:f.write(b'corrupt')
        target=Path(self.tmp.name)/'rejected'
        with self.assertRaises(ValueError):InferencePilot.restore_backup(dest,target)
        self.assertFalse(target.exists())
    def test_receipt_rejects_tampered_result_usage_id_and_currency(self):
        result,_=self.pilot.complete('agent','one',self.request);pub=self.pilot.signer.spend_pub_bytes.hex()
        for change in [lambda v:v.update(id='different'),lambda v:v['choices'][0].update(finish_reason='length'),lambda v:v['usage'].update(cost_satk=1),lambda v:v.update(currency='BTC-K'),lambda v:v.update(mainnet_funds_enabled=True),lambda v:v['choices'][0]['message'].update(content='different')]:
            with self.subTest(change=change):
                altered=copy.deepcopy(result);change(altered);self.assertFalse(verify_receipt(altered,pub))
        self.assertFalse(verify_receipt({},pub))
    def test_http_sdk_auth_agent_isolation_and_replay(self):
        server=self.pilot.make_server();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        client=PilotClient(base,self.token,self.pilot.signer.spend_pub_bytes.hex())
        args={'model':self.provider.model,'messages':self.request['messages'],'max_tokens':32,'idempotency_key':'one'}
        first,_=client.complete(**args);second,headers=client.complete(**args)
        self.assertEqual(first,second);self.assertEqual(headers['X-Idempotent-Replay'],'true');self.assertEqual(self.provider.calls,1)
        other=self.pilot.create_agent('other',0);outsider=PilotClient(base,other,self.pilot.signer.spend_pub_bytes.hex())
        with self.assertRaises(urllib.error.HTTPError) as e:outsider.task('one')
        self.assertEqual(e.exception.code,404)
        invalid=PilotClient(base,'invalid',self.pilot.signer.spend_pub_bytes.hex())
        with self.assertRaises(urllib.error.HTTPError) as e:invalid.balance()
        self.assertEqual(e.exception.code,401)
    def test_single_server_owner_and_integer_request_bounds(self):
        with self.assertRaises(RuntimeError):InferencePilot(self.path,self.provider)
        for bad in [True,1.0,-1,129]:
            with self.subTest(bad=bad),self.assertRaises(PilotError):self.pilot.complete('agent','one',{**self.request,'max_tokens':bad})
        self.assertEqual(self.provider.calls,0)
    def test_sdk_rejects_valid_signed_receipt_for_a_different_request(self):
        from unittest.mock import Mock
        result,_=self.pilot.complete('agent','one',self.request)
        client=PilotClient('http://127.0.0.1:1',self.token,self.pilot.signer.spend_pub_bytes.hex())
        client.request=Mock(return_value=(result,{}))
        with self.assertRaisesRegex(ValueError,'different request'):
            client.complete(model=self.provider.model,messages=[{'role':'user','content':'different'}],
                            idempotency_key='other')

class TestHTTPProvider(unittest.TestCase):
    def test_actual_response_requires_real_usage_and_exact_model(self):
        from unittest.mock import Mock
        response={'model':'real-model','choices':[{'message':{'content':'hello'}}],'usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6}}
        provider=HTTPInferenceProvider('http://127.0.0.1:1/v1','real-model')
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return json.dumps(response).encode()
        provider.opener=Mock();provider.opener.open.return_value=Reply()
        request={'messages':[{'role':'user','content':'hi'}],'max_tokens':8}
        self.assertEqual(provider.complete(request)['usage'],response['usage'])
        response['usage']['prompt_tokens']=True
        with self.assertRaises(ValueError):provider.complete(request)
        response['usage']={'prompt_tokens':4,'completion_tokens':2,'total_tokens':7}
        with self.assertRaises(ValueError):provider.complete(request)
        response['usage']['total_tokens']=6;response['model']='wrong-model'
        with self.assertRaises(ValueError):provider.complete(request)

if __name__=='__main__':unittest.main()

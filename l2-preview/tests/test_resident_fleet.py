import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parents[1]/'src'),str(Path(__file__).parents[1]/'sdk')]
from gateway.inference_pilot import InferencePilot, PilotError, BackendRejected
from gateway.resident_fleet import ResidentFleet
from worker.resident_worker import ResidentWorker
from btck_agent.inference_pilot import PilotClient


class TestResidentFleet(unittest.TestCase):
    def setUp(self):
        self.time=100.;self.token='w'*64
        self.fleet=ResidentFleet({'model-a':{'rate_kcu_per_1k':'0.8'},'model-b':{'rate_kcu_per_1k':'2.8'}},
                                {'rig':{'token':self.token,'models':['model-a']},
                                 'other':{'token':'o'*64,'models':['model-b']}},
                                clock=lambda:self.time,task_timeout=.15)
        self.session=self.fleet.handle('/v1/workers/register',self.token,{'ready_models':['model-a']})['session']
        self.body={'model':'model-a','messages':[{'role':'user','content':'hi'}],'max_tokens':16,'max_charge_satk':100000}
        self.result={'model':'model-a','text':'hello','usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6}}
    def call(self,route,body=None,token=None,session=None):
        return self.fleet.handle('/v1/workers/'+route,token or self.token,{'session':session or self.session,**(body or {})})
    def dispatch(self):
        replies=[];errors=[]
        def execute():
            try:replies.append(self.fleet.complete(self.body))
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=execute);thread.start()
        for _ in range(100):
            if self.fleet.tasks:break
            time.sleep(.001)
        return thread,replies,errors
    def test_catalog_is_loaded_and_fresh_not_configured(self):
        self.assertEqual([m['status'] for m in self.fleet.catalog()['data']],['available','offline'])
        self.time+=31
        self.assertTrue(all(m['status']=='offline' for m in self.fleet.catalog()['data']))
        with self.assertRaises(BackendRejected):self.fleet.complete(self.body)
        with self.assertRaises(PilotError):self.call('next')
    def test_unauthorized_worker_model_and_sessions_rejected(self):
        with self.assertRaises(PilotError):self.call('heartbeat',token='wrong')
        with self.assertRaises(PilotError):self.call('heartbeat',{'ready_models':['model-b']})
        with self.assertRaises(PilotError):self.call('heartbeat',session='wrong')
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'available')
    def test_task_owner_challenge_result_binding_and_duplicate(self):
        thread,replies,errors=self.dispatch();self.addCleanup(thread.join)
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'busy')
        task=self.call('next')['task']
        body={'task_id':task['id'],'challenge':task['challenge'],'request_hash':task['request_hash'],'result':self.result}
        other=self.fleet.handle('/v1/workers/register','o'*64,{'ready_models':['model-b']})['session']
        with self.assertRaises(PilotError):self.call('complete',body,token='o'*64,session=other)
        with self.assertRaises(PilotError):self.call('complete',{**body,'challenge':'bad'})
        with self.assertRaises(PilotError):self.call('complete',{**body,'request_hash':'bad'})
        self.call('complete',body)
        with self.assertRaises(PilotError):self.call('complete',body)
        thread.join(2);self.assertFalse(errors);self.assertEqual(replies[0]['worker_id'],'rig')
        self.assertFalse(self.fleet.tasks)
    def test_unclaimed_task_releases_but_claimed_timeout_is_uncertain(self):
        with self.assertRaises(BackendRejected):self.fleet.complete(self.body)
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'offline')
        self.assertFalse(self.fleet.tasks);self.assertFalse(self.fleet.queues['rig'])
        self.session=self.fleet.handle('/v1/workers/register',self.token,{'ready_models':['model-a']})['session']
        thread,replies,errors=self.dispatch();task=self.call('next')['task'];thread.join(2)
        self.assertIsInstance(errors[0],TimeoutError)
        with self.assertRaises(PilotError):self.call('complete',{'task_id':task['id']})
        self.assertFalse(self.fleet.tasks)
    def test_explicit_worker_admission_rejection_releases_the_task(self):
        thread,replies,errors=self.dispatch();task=self.call('next')['task']
        self.call('complete',{'task_id':task['id'],'challenge':task['challenge'],'request_hash':task['request_hash'],
                              'error':'context too long','error_kind':'rejected_before_inference'})
        thread.join(2);self.assertIsInstance(errors[0],BackendRejected)
        self.assertFalse(replies);self.assertFalse(self.fleet.tasks)

    def test_invalid_usage_cannot_be_settled(self):
        for key,value in [('prompt_tokens',True),('total_tokens',9),('completion_tokens',100)]:
            bad=copy.deepcopy(self.result);bad['usage'][key]=value
            with self.assertRaises(ValueError):self.fleet.validate_result(bad,self.body)


class TestResidentHTTPFlow(unittest.TestCase):
    def test_real_protocol_routes_to_worker_and_credits_correct_provider(self):
        """Backend fixture tests the network protocol; GPU proof is a separate real run."""
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        calls=[]
        class Backend(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,data):
                raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):self.reply({'data':[{'id':'model-a'}]})
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
                self.reply({'id':'provider-id','model':'model-a','usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6},'choices':[{'message':{'content':'hello'}}]})
        with tempfile.TemporaryDirectory() as td:
            backend=ThreadingHTTPServer(('127.0.0.1',0),Backend)
            threading.Thread(target=backend.serve_forever,daemon=True).start()
            self.addCleanup(backend.server_close);self.addCleanup(backend.shutdown)
            fleet=ResidentFleet({'model-a':{'rate_kcu_per_1k':'0.8'}},{'rig':{'token':'w'*64,'models':['model-a']}},task_timeout=3)
            pilot=InferencePilot(Path(td)/'ledger',fleet);self.addCleanup(pilot.close)
            token=pilot.create_agent('customer',100000);server=pilot.make_server()
            gateway_port=server.server_port;server.server_close()
            worker=ResidentWorker({'gateway_url':'http://127.0.0.1:'+str(server.server_port),
                                   'backend_url':'http://127.0.0.1:'+str(backend.server_port)+'/v1',
                                   'model':'model-a','token':'w'*64,'status_file':str(Path(td)/'status.json')},probe=lambda:(True,{}))
            thread=threading.Thread(target=worker.run,daemon=True);thread.start()
            self.addCleanup(lambda:thread.join(3));self.addCleanup(worker.stop.set)
            for _ in range(100):
                if worker.last_gate=='gateway_unavailable':break
                time.sleep(.01)
            self.assertEqual(worker.last_gate,'gateway_unavailable')
            server=pilot.make_server(gateway_port)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            client=PilotClient('http://127.0.0.1:'+str(server.server_port),token,pilot.signer.spend_pub_bytes.hex())
            for _ in range(350):
                if client.models()['data'][0]['status']=='available':break
                time.sleep(.01)
            args={'model':'model-a','messages':[{'role':'user','content':'hello'}],'idempotency_key':'one','max_tokens':16,'max_charge_satk':10000}
            first,_=client.complete(**args);second,_=client.complete(**args)
            self.assertEqual(first,second);self.assertEqual(len(calls),1)
            self.assertEqual(first['receipt']['payload']['worker_id'],'rig')
            self.assertEqual(first['usage']['cost_satk'],1120)
            with pilot.db() as db:self.assertEqual(db.execute("SELECT amount FROM balances WHERE account='worker:rig'").fetchone()[0],672)
            worker.heartbeat();self.assertEqual(worker.earned,672)
            self.assertEqual(worker.pricing['unit'],'KCU')
            self.assertEqual(worker.pricing['rates'],{'model-a':'0.8'})
            self.assertEqual(first['receipt']['payload']['price']['task_kcu'],.0056)
            self.assertTrue(pilot.conservation()['conserved'])
            # Runtime backend health loss removes availability before another task.
            worker.probe=lambda:(False,{'reason':'fixture resource guard'})
            worker.heartbeat();self.assertEqual(client.models()['data'][0]['status'],'offline')
            worker.stop.set();thread.join(3);self.assertFalse(thread.is_alive())
            self.assertFalse(json.loads((Path(td)/'status.json').read_text())['ready'])
    def test_persisted_receipt_replays_with_offline_fleet_after_restore(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as td:
            fleet=ResidentFleet({'model-b':{'rate_kcu_per_1k':'2.8'}},{'rig':{'token':'w'*64,'models':['model-b']}})
            fleet.complete=Mock(return_value={'model':'model-b','text':'hello','usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6},'worker_id':'rig'})
            pilot=InferencePilot(Path(td)/'live',fleet)
            pilot.create_agent('agent',100000)
            request={'model':'model-b','messages':[{'role':'user','content':'hi'}],'max_tokens':16,'max_charge_satk':10000}
            result,_=pilot.complete('agent','one',request)
            self.assertEqual(result['usage']['cost_satk'],3920)
            pilot.backup(Path(td)/'backup');pilot.close()
            fresh=ResidentFleet({'model-b':{'rate_kcu_per_1k':'2.8'}},{'rig':{'token':'w'*64,'models':['model-b']}})
            restored=InferencePilot(InferencePilot.restore_backup(Path(td)/'backup',Path(td)/'restored'),fresh)
            try:
                replay,duplicate=restored.complete('agent','one',request)
                self.assertEqual(result,replay);self.assertTrue(duplicate)
                self.assertEqual(fresh.catalog()['data'][0]['status'],'offline')
                self.assertTrue(restored.conservation()['conserved'])
                with restored.db() as db:self.assertEqual(db.execute("SELECT amount FROM balances WHERE account='worker:rig'").fetchone()[0],2352)
            finally:restored.close()

    def test_root_owned_miner_uses_read_only_process_identity(self):
        from unittest.mock import patch
        config={'gateway_url':'http://127.0.0.1:1','backend_url':'http://127.0.0.1:2/v1','token':'w'*64,'model':'x','miner_pid':2552}
        stat='2552 (miner) S '+'0 '*18+'123456 0'
        with patch('worker.resident_worker.Path.is_dir',return_value=True), patch('worker.resident_worker.Path.read_text',return_value=stat), patch('worker.resident_worker.os.kill',side_effect=PermissionError) as kill:
            worker=ResidentWorker(config)
            self.assertEqual(worker.miner_birth,'123456');kill.assert_not_called()

    def test_remote_plaintext_and_missing_gpu_fail_closed(self):
        with self.assertRaises(ValueError):ResidentWorker({'gateway_url':'http://public.example','backend_url':'http://127.0.0.1:1/v1','token':'w'*64,'model':'x'})
        worker=ResidentWorker({'gateway_url':'http://127.0.0.1:1','backend_url':'http://127.0.0.1:2/v1','token':'w'*64,'model':'x'})
        self.assertEqual(worker.readiness()['ready_models'],[])

if __name__=='__main__':unittest.main()

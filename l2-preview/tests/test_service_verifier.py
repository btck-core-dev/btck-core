import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gateway.resident_fleet import ResidentFleet
from gateway.service_verifier import ServiceVerifier
from gateway.inference_pilot import BackendRejected, PilotError


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.now=[10.0]
        self.request={'model':'model-a','messages':[{'role':'user','content':'private fixture'}],'max_tokens':8}
        self.result={'model':'model-a','text':'reference','finish_reason':'stop',
                     'usage':{'prompt_tokens':5,'completion_tokens':1,'total_tokens':6}}
        self.policy={'interval_seconds':60,'timeout_seconds':10,'cooldown_seconds':60,
                     'checks':[{'request':self.request,'accepted_sha256':[ServiceVerifier.digest(self.result)]}]}
        self.fleet=ResidentFleet({'model-a':{'rate_kcu_per_1k':'1','service_validation':self.policy}},
            {'rig':{'token':'w'*64,'models':['model-a']}},clock=lambda:self.now[0])
        self.session=self.fleet.handle('/v1/workers/register','w'*64,{'ready_models':['model-a']})['session']
    def call(self,route,body=None):
        return self.fleet.handle('/v1/workers/'+route,'w'*64,{'session':self.session,**(body or {})})
    def respond(self,task,result):
        return self.call('complete',{'task_id':task['id'],'challenge':task['challenge'],
                                    'request_hash':task['request_hash'],'result':result})
    def test_pending_worker_cannot_receive_customer_task_and_policy_is_private(self):
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'offline')
        with self.assertRaises(BackendRejected):self.fleet.complete(self.request)
        public=json.dumps(self.fleet.catalog())
        for value in ('private fixture','accepted_sha256','service_validation'):
            self.assertNotIn(value,public)
        task=self.call('next')['task']
        self.assertNotIn('expected',task)
        self.assertNotIn('verification',task)
        reply=self.respond(task,self.result)
        self.assertEqual(reply['verification_status'],'verified')
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'available')
        self.assertFalse(self.fleet.tasks)
        with self.assertRaises(PilotError):self.respond(task,self.result)
    def test_invalid_result_blocks_and_heartbeat_cannot_override(self):
        task=self.call('next')['task'];wrong=copy.deepcopy(self.result);wrong['text']='wrong'
        self.assertEqual(self.respond(task,wrong)['verification_status'],'unavailable')
        self.call('heartbeat',{'ready_models':['model-a']})
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'offline')
        self.assertIsNone(self.call('next')['task'])
    def test_timeout_releases_internal_task_but_never_admits_node(self):
        task=self.call('next')['task'];self.now[0]+=11
        self.call('heartbeat',{'ready_models':['model-a']})
        self.assertFalse(self.fleet.tasks)
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'offline')
        with self.assertRaises(PilotError):self.respond(task,self.result)
    def test_expiry_and_new_session_require_revalidation(self):
        task=self.call('next')['task'];self.respond(task,self.result)
        self.now[0]+=61
        self.assertEqual(self.fleet.catalog()['data'][0]['status'],'offline')
        self.call('heartbeat',{'ready_models':['model-a']})
        self.assertIsNotNone(self.call('next')['task'])
    def test_renewal_keeps_valid_worker_online_and_failure_still_closes_admission(self):
        for valid in (True,False):
            with self.subTest(valid=valid):
                self.setUp()
                self.respond(self.call('next')['task'],self.result)
                self.now[0]+=30
                self.call('heartbeat',{'ready_models':['model-a']})
                task=self.call('next')['task'];self.assertIsNotNone(task)
                card=self.fleet.catalog()['data'][0]
                self.assertEqual(card['online_workers'],1)
                self.assertEqual(card['status'],'busy')
                result=copy.deepcopy(self.result)
                if not valid:result['text']='wrong'
                self.respond(task,result)
                card=self.fleet.catalog()['data'][0]
                self.assertEqual(card['status'],'available' if valid else 'offline')
                self.assertEqual(card['online_workers'],1 if valid else 0)
    def test_malformed_policy_and_usage_do_not_pass(self):
        with self.assertRaises(ValueError):ServiceVerifier({**self.policy,'timeout_seconds':0},'model-a')
        task=self.call('next')['task'];bad=copy.deepcopy(self.result);bad['usage']['total_tokens']=False
        self.assertEqual(self.respond(task,bad)['verification_status'],'unavailable')


if __name__=='__main__':unittest.main()

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parents[1]/'sdk')]
from gateway.inference_pilot import InferencePilot,PilotError
from gateway.resident_fleet import ResidentFleet
from btck_agent.inference_pilot import PilotClient


class LocalProvider:
    model='local-agent'
    def prompt_limit(self,model):return 65536
    def complete(self,request):
        return {'model':self.model,'text':'OK','usage':{'prompt_tokens':5,'completion_tokens':1,'total_tokens':6}}


class LongContextAdmission(unittest.TestCase):
    def test_large_unicode_request_fits_http_and_round_trips_signed_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            pilot=InferencePilot(td,LocalProvider());self.addCleanup(pilot.close)
            token=pilot.create_agent('customer',1000000)
            server=pilot.make_server();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            client=PilotClient('http://127.0.0.1:'+str(server.server_port),token,pilot.signer.spend_pub_bytes.hex())
            kwargs=dict(model='local-agent',messages=[{'role':'user','content':'界'*20000}],
                        idempotency_key='large-unicode',max_tokens=16,max_charge_satk=10000)
            one,_=client.complete(**kwargs);two,_=client.complete(**kwargs)
            self.assertEqual(one,two);self.assertTrue(pilot.conservation()['conserved'])
            before=pilot.balance('customer')
            with self.assertRaises(urllib.error.HTTPError):client.complete(**{**kwargs,'messages':[{'role':'user','content':'界'*23000}],
                                                                 'idempotency_key':'oversize'})
            self.assertEqual(pilot.balance('customer'),before)
    def test_model_specific_prompt_limit_rejects_before_budget_hold(self):
        with tempfile.TemporaryDirectory() as td:
            provider=LocalProvider();provider.prompt_limit=lambda model:16384
            pilot=InferencePilot(td,provider);self.addCleanup(pilot.close)
            pilot.create_agent('customer',1000000);before=pilot.balance('customer')
            body={'model':'local-agent','messages':[{'role':'user','content':'x'*20000}],
                  'max_tokens':16,'max_charge_satk':10000}
            with self.assertRaises(PilotError):pilot.complete('customer','too-long',body)
            self.assertEqual(before,pilot.balance('customer'))
    def test_invalid_catalog_limit_cannot_be_advertised(self):
        for value in (True,65537,0,'65536'):
            with self.assertRaises(ValueError):ResidentFleet({'agent':{'rate_kcu_per_1k':'2.8','max_prompt_bytes':value}}, {})


if __name__=='__main__':unittest.main()

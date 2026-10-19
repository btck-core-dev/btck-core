import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from worker.connect_app import make_config, save_new, local_models, redeem_invitation


class TestComputeConnect(unittest.TestCase):
    def values(self):
        return {'gateway': 'https://compute.example.invalid', 'backend': 'http://127.0.0.1:8000/v1',
                'token': 'w' * 64, 'model': 'miner-selected-agent-model', 'gpus': '0',
                'miner': 'SRBMiner-MULTI', 'pearl': 'http://127.0.0.1:21550', 'floor': '560'}

    def test_private_config_refuses_overwrite_and_requires_real_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'worker.json'
            values = self.values()
            for floor in ('-1', 'nan', 'inf'):
                with self.assertRaises(ValueError): make_config({**values, 'floor': floor}, path)
            self.assertEqual(make_config({**values,'floor':''},path)['min_pearl_hashrate_hs'],0)
            with self.assertRaises(ValueError):make_config({**values,'floor':'0','mode':'Run alongside Pearl'},path)
            config = make_config(values, path)
            self.assertEqual(config['model'], 'miner-selected-agent-model')
            self.assertEqual(config['min_pearl_hashrate_hs'], 560e12)
            save_new(config, path)
            self.assertEqual(json.loads(path.read_text()), config)
            if os.name == 'posix': self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): save_new(config, path)
            with self.assertRaises(ValueError): make_config({**values, 'gateway': 'http://remote.invalid'}, path)

    def test_model_detection_reads_existing_backend_only(self):
        calls = []
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"data":[{"id":"miner-selected-model"}]}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.assertEqual(local_models('http://127.0.0.1:' + str(server.server_port) + '/v1'), ['miner-selected-model'])
            self.assertEqual(calls, ['/v1/models'])
            with self.assertRaises(ValueError): local_models('https://remote.invalid/v1')
            with self.assertRaises(ValueError): local_models('http://secret@localhost/v1')
        finally: server.shutdown(); server.server_close()

    def test_miner_selects_bounded_grace_and_reasoning_without_losing_backend_settings(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'worker.json';values={**self.values(),'grace':'180','reasoning':'Disabled'}
            previous={**make_config(self.values(),path),'inference_timeout':180,
                      'min_free_mib':768,'chat_template_kwargs':{'preserve_thinking':False}}
            config=make_config(values,path,previous)
            self.assertEqual(config['hashrate_grace_seconds'],180)
            self.assertEqual(config['chat_template_kwargs'],{'preserve_thinking':False,'enable_thinking':False})
            self.assertEqual(config['inference_timeout'],90)
            self.assertEqual(config['mining_mode'],'pause_on_request')
            self.assertEqual(config['min_free_mib'],768)
            default=make_config({**values,'reasoning':'Backend default'},path,config)
            self.assertNotIn('enable_thinking',default['chat_template_kwargs'])
            changed=make_config({**values,'model':'different-model','reasoning':'Backend default'},path,config)
            self.assertNotIn('chat_template_kwargs',changed)
            self.assertEqual(make_config({**values,'grace':'0'},path)['hashrate_grace_seconds'],0)

    def test_invalid_grace_and_reasoning_are_rejected_before_connect(self):
        for grace in ('-1','601','nan','inf','not-a-number'):
            with self.assertRaises(ValueError):make_config({**self.values(),'grace':grace},Path('unused.json'))
        with self.assertRaises(ValueError):make_config({**self.values(),'reasoning':'arbitrary'},Path('unused.json'))

    def test_invitation_retry_keeps_nonce_and_requires_https(self):
        profile = {'gateway_url': 'https://compute.example.invalid', 'invitation_code': 'c' * 48}
        returned = {'gateway_url': profile['gateway_url'], 'token': 'w' * 64}
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps(returned).encode()
        opener = Mock()
        opener.open.side_effect = [urllib.error.URLError('Lost response fixture'), Reply()]
        with tempfile.TemporaryDirectory() as td, patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(urllib.error.URLError): redeem_invitation(profile, td)
            self.assertEqual(redeem_invitation(profile, td), returned)
            bodies = [json.loads(call.args[0].data) for call in opener.open.call_args_list]
            self.assertEqual(bodies[0], bodies[1])
            with self.assertRaises(ValueError): redeem_invitation({**profile, 'gateway_url': 'http://remote.invalid'}, td)


if __name__ == '__main__': unittest.main()

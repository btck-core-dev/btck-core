"""Protocol fixtures, not GPU/model ability evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'sdk')]
from worker.agent_protocol import validate_agent_request
from worker.resident_worker import ResidentWorker
from gateway.inference_pilot import InferencePilot, PilotError, verify_receipt
from gateway.resident_fleet import ResidentFleet
from btck_agent.inference_pilot import PilotClient
from btck_agent.inference_receipt import verify_receipt as sdk_verify
from btck_agent.tool_agent import ToolAgent

TOOLS = [{'type': 'function', 'function': {'name': 'lookup_service', 'description': 'Read the local service catalog',
         'parameters': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'], 'additionalProperties': False}}}]
CALL = {'id': 'call_lookup_1', 'type': 'function', 'function': {'name': 'lookup_service', 'arguments': '{"name":"catalog"}'}}


class TestAgentTools(unittest.TestCase):
    def test_reject_orphan_duplicate_and_undeclared_calls(self):
        assistant = {'role': 'assistant', 'content': None, 'tool_calls': [CALL]}
        reply = {'role': 'tool', 'content': '{}', 'tool_call_id': CALL['id']}
        body = {'messages': [{'role': 'user', 'content': 'lookup'}, assistant, reply], 'tools': TOOLS}
        self.assertEqual(validate_agent_request(body), {'lookup_service'})
        for messages in ([reply], body['messages'] + [reply], body['messages'][:-1]):
            with self.assertRaises(ValueError): validate_agent_request({**body, 'messages': messages})
        bad = copy.deepcopy(body)
        bad['messages'][1]['tool_calls'][0]['function']['name'] = 'run_shell'
        with self.assertRaises(ValueError): validate_agent_request(bad)

    def test_tools_disabled_rejected_before_balance_hold(self):
        with tempfile.TemporaryDirectory() as td:
            fleet = ResidentFleet({'m': {'rate_kcu_per_1k': '0.8'}}, {})
            pilot = InferencePilot(td, fleet)
            try:
                pilot.create_agent('a', 100000)
                body = {'model': 'm', 'messages': [{'role': 'user', 'content': 'lookup'}], 'tools': TOOLS,
                        'max_tokens': 128, 'max_charge_satk': 10000}
                with self.assertRaises(PilotError) as error: pilot.complete('a', 'disabled', body)
                self.assertEqual(error.exception.code, 'tools_not_enabled')
                self.assertEqual(pilot.balance('a')['held_satk'], 0)
            finally: pilot.close()

    def test_worker_gateway_sdk_tool_loop_receipts_restart_and_charging(self):
        calls = []
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'data': [{'id': 'local-model-alias'}]}).encode())
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(body)
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                if len(calls) == 1:
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [CALL]}
                    finish = 'tool_calls'
                else:
                    message = {'role': 'assistant', 'content': 'The catalog service is online in EU.', 'tool_calls': []}
                    finish = 'stop'
                self.wfile.write(json.dumps({'id': 'fixture-' + str(len(calls)), 'model': 'local-model-alias',
                    'choices': [{'message': message, 'finish_reason': finish}],
                    'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}).encode())
        backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        self.addCleanup(backend.server_close); self.addCleanup(backend.shutdown)
        with tempfile.TemporaryDirectory() as td:
            fleet = ResidentFleet({'agent-capable-fixture': {'rate_kcu_per_1k': '0.8', 'supports_tool_calls': True,
                'max_output_tokens': 1024}}, {'rig': {'token': 'w' * 64, 'models': ['agent-capable-fixture']}}, task_timeout=3)
            pilot = InferencePilot(Path(td) / 'ledger', fleet)
            token = pilot.create_agent('a', 1_000_000)
            server = pilot.make_server()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            worker = ResidentWorker({'gateway_url': 'http://127.0.0.1:' + str(server.server_port),
                'backend_url': 'http://127.0.0.1:' + str(backend.server_port) + '/v1',
                'model': 'agent-capable-fixture', 'backend_model': 'local-model-alias', 'token': 'w' * 64}, probe=lambda: (True, {}))
            thread = threading.Thread(target=worker.run, daemon=True); thread.start()
            try:
                client = PilotClient(worker.gateway, token, pilot.signer.spend_pub_bytes.hex())
                for _ in range(200):
                    if client.models()['data'][0]['status'] == 'available': break
                    time.sleep(.01)
                executions = []
                def lookup(args):
                    self.assertEqual(args, {'name': 'catalog'})
                    executions.append(args)
                    return {'name': 'catalog', 'online': True, 'region': 'EU'}
                agent = ToolAgent(client, TOOLS, {'lookup_service': lookup})
                kwargs = {'model': worker.model, 'prompt': 'Look up the catalog service and report its region.',
                          'run_id': 'protocol-fixture', 'journal': Path(td) / 'job.json', 'total_budget_satk': 100000,
                          'require_initial_tool': True}
                save = agent._save
                def interrupt_after_tool(path, state):
                    if state['phase'] == 'inference' and state['round'] == 1:
                        raise RuntimeError('Injected process interruption after persisted tool result')
                    save(path, state)
                with patch.object(agent, '_save', side_effect=interrupt_after_tool):
                    with self.assertRaises(RuntimeError): agent.run(**kwargs)
                self.assertEqual(len(calls), 1); self.assertEqual(len(executions), 1)
                result = agent.run(**kwargs)
                self.assertEqual(result['answer'], 'The catalog service is online in EU.')
                self.assertEqual(len(executions), 1); self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0]['tools'], TOOLS)
                self.assertEqual(calls[0]['model'], 'local-model-alias')
                self.assertEqual(calls[0]['tool_choice'], 'required')
                self.assertNotIn('tool_choice', calls[1])
                self.assertEqual(calls[1]['messages'][-1]['role'], 'tool')
                self.assertEqual(result['spent_satk'], 19200)
                self.assertEqual(pilot.balance('a')['held_satk'], 0)
                self.assertTrue(pilot.conservation()['conserved'])
                first = result['receipts'][0]
                for verifier in (verify_receipt, sdk_verify):
                    self.assertTrue(verifier(first, client.trusted_receipt_pubkey))
                    for mutation in ('arguments', 'stripped', 'name', 'id'):
                        altered = copy.deepcopy(first)
                        message = altered['choices'][0]['message']
                        if mutation == 'arguments': message['tool_calls'][0]['function']['arguments'] = '{"name":"other"}'
                        if mutation == 'stripped': message.pop('tool_calls')
                        if mutation == 'name': message['tool_calls'][0]['function']['name'] = 'run_shell'
                        if mutation == 'id': message['tool_calls'][0]['id'] = 'different'
                        self.assertFalse(verifier(altered, client.trusted_receipt_pubkey))
                restored = agent.run(**kwargs)
                self.assertEqual(restored, result)
                self.assertEqual(len(calls), 2); self.assertEqual(len(executions), 1)
                state = json.loads(Path(kwargs['journal']).read_text())
                state['phase'] = 'tool_executing'; state['pending'] = [CALL]
                state['receipts'] = state['receipts'][:1]; state['round'] = 1
                state['spent_satk'] = state['receipts'][0]['usage']['cost_satk']
                state['messages'] = state['messages'][:2]; state['tool_index'] = 0
                Path(kwargs['journal']).write_text(json.dumps(state))
                with self.assertRaises(RuntimeError): agent.run(**kwargs)
                self.assertEqual(len(executions), 1)
            finally:
                worker.stop.set(); thread.join(8)
                server.shutdown(); server.server_close(); pilot.close()


if __name__ == '__main__': unittest.main()

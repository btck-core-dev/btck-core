import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
sys.path[:0] = [str(Path(__file__).parents[1] / 'src'), str(Path(__file__).parents[1] / 'sdk')]
from btck_agent.document_workflow import workflow_request, validated_output, execute_job
from btck_agent.inference_pilot import PilotClient
from gateway.inference_pilot import InferencePilot, PilotError
from gateway.resident_fleet import ResidentFleet


class Provider:
    model = 'document-model'
    def __init__(self):
        self.calls = 0
        self.text = '{"customer":"Cedar Research","email":null}'
        self.finish = 'stop'
    def complete(self, request):
        self.calls += 1
        return {'text': self.text, 'model': self.model, 'finish_reason': self.finish,
                'usage': {'prompt_tokens': 100, 'completion_tokens': 150, 'total_tokens': 250}}
    def output_limit(self, model):
        return 512


class TestDocumentAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.provider = Provider()
        self.pilot = InferencePilot(Path(self.tmp.name) / 'ledger', self.provider)
        self.addCleanup(self.pilot.close)
        token = self.pilot.create_agent('documents', 5000000)
        self.server = self.pilot.make_server(0)
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = PilotClient('http://127.0.0.1:' + str(self.server.server_port), token,
                                  self.pilot.signer.spend_pub_bytes.hex())
        self.path = Path(self.tmp.name) / 'private-job.json'
        self.args = dict(operation='extract', document='Customer Cedar Research',
                         fields=['customer', 'email'], model=self.provider.model)

    def test_actual_signed_job_resume_no_extra_inference_or_charge(self):
        job = execute_job(self.client, self.path, **self.args)
        self.assertEqual(job['state'], 'validated')
        self.assertIsNone(job['output']['email'])
        self.assertEqual(job['result']['usage']['completion_tokens'], 150)
        balance = self.client.balance()
        resumed = execute_job(self.client, self.path, resume=True)
        self.assertEqual(resumed, job); self.assertEqual(self.provider.calls, 1)
        self.assertEqual(balance, self.client.balance())
        self.assertTrue(self.pilot.conservation()['conserved'])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertNotIn(self.client.token, self.path.read_text())
        with self.assertRaises(FileExistsError):
            execute_job(self.client, self.path, **self.args)
        self.assertEqual(self.provider.calls, 1)

    def test_crash_after_gateway_settlement_recovers_original_receipt(self):
        real_complete = self.client.complete
        def crash(**kwargs):
            real_complete(**kwargs)
            raise ConnectionError('lost reply')
        self.client.complete = crash
        with self.assertRaises(ConnectionError): execute_job(self.client, self.path, **self.args)
        self.assertEqual(json.loads(self.path.read_text())['state'], 'pending')
        self.client.complete = real_complete
        job = execute_job(self.client, self.path, resume=True)
        self.assertEqual(job['state'], 'validated'); self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.client.balance()['held_satk'], 0)

    def test_paid_invalid_output_is_retained_and_not_repaired(self):
        for text, finish in [('not JSON', 'stop'), ('{"customer":"Cedar Research","email":null}', 'length'),
                             ('{"customer":"a","customer":"b","email":null}', 'stop'),
                             ('{"customer":123,"email":null}', 'stop')]:
            with self.subTest(text=text, finish=finish):
                self.provider.text = text; self.provider.finish = finish
                path = self.path.with_name(str(self.provider.calls) + '.json')
                job = execute_job(self.client, path, **self.args)
                self.assertEqual(job['state'], 'invalid_output'); self.assertNotIn('output', job)
                calls = self.provider.calls; balance = self.client.balance()
                execute_job(self.client, path, resume=True)
                self.assertEqual(calls, self.provider.calls); self.assertEqual(balance, self.client.balance())
                self.assertGreater(job['result']['usage']['cost_satk'], 0)

    def test_cached_receipt_tamper_and_wrong_agent_are_rejected_without_call(self):
        execute_job(self.client, self.path, **self.args)
        raw = json.loads(self.path.read_text()); raw['result']['choices'][0]['message']['content'] = '{}'
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): execute_job(self.client, self.path, resume=True)
        self.client.token = 'another-agent'
        with self.assertRaises(ValueError): execute_job(self.client, self.path, resume=True)
        self.assertEqual(self.provider.calls, 1)

    def test_unknown_execution_keeps_same_hold_and_never_reexecutes(self):
        def uncertain(request):
            self.provider.calls += 1
            raise TimeoutError()
        self.provider.complete = uncertain
        for resume in (False, True):
            with self.assertRaises(Exception):
                execute_job(self.client, self.path, resume=resume, **({} if resume else self.args))
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.client.balance()['held_satk'], 1000000)

    def test_bad_inputs_and_utf8_byte_limits_fail_before_job_or_call(self):
        for update in ({'document': '中' * 4001}, {'fields': ['x', 'x']},
                       {'fields': ['invalid field']}, {'document': '   '}, {'operation': 'browse'},
                       {'operation': 'summary', 'fields': [], 'document': 'a\n' * 5000}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                execute_job(self.client, self.path, **{**self.args, **update})
        self.assertFalse(self.path.exists()); self.assertEqual(self.provider.calls, 0)
        body = workflow_request(**{**self.args, 'document': '中' * 4000})
        self.assertLess(sum(len(m['content'].encode()) for m in body['messages']), 16384)

    def test_summary_shape_and_nulls_are_not_fabricated(self):
        result = {'choices': [{'finish_reason': 'stop', 'message': {'content':
            '{"summary":"处理240份描述。","key_points":["首批30份。"]}'}}]}
        self.assertEqual(validated_output(result, 'summary')['key_points'], ['首批30份。'])
        result['choices'][0]['message']['content'] = '{"summary":"ok","key_points":[],"extra":true}'
        with self.assertRaises(ValueError): validated_output(result, 'summary')

    def test_schema_valid_but_invented_summary_or_fields_fail_source_check(self):
        def result(text):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
        source = 'First batch due on 2026-10-21. Customer Cedar Research.'
        with self.assertRaises(ValueError):
            validated_output(result('{"summary":"First batch due on 2026-10-18.","key_points":["Customer Cedar Research."]}'),
                             'summary', document=source)
        with self.assertRaises(ValueError):
            validated_output(result('{"customer":"Cedar Research","email":"madeup@example.com"}'),
                             'extract', ['customer', 'email'], document=source)
        value = validated_output(result('{"summary":"First batch due on 2026-10-21.","key_points":["Customer Cedar Research."]}'),
                                 'summary', document=source)
        self.assertEqual(value['summary'], 'First batch due on 2026-10-21.')

    def test_summary_source_ids_reconstruct_chinese_without_rewriting(self):
        source = '计划\n\n处理240份说明。\n首批30份，截止2026-10-21。'
        def result(value):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
        output = validated_output(result({'summary': 2, 'key_points': [3]}), 'summary', document=source)
        self.assertEqual(output, {'summary': '处理240份说明。', 'key_points': ['首批30份，截止2026-10-21。']})
        for value in [{'summary': 0, 'key_points': [3]}, {'summary': 2, 'key_points': [99]},
                      {'summary': 2, 'key_points': [True]}, {'summary': 2, 'key_points': [3, 3]},
                      {'summary': 2, 'key_points': [3], 'extra': 0}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validated_output(result(value), 'summary', document=source)
        request = workflow_request(operation='summary', document=source, model='model')
        self.assertIn('[3] 首批30份，截止2026-10-21。', request['messages'][-1]['content'])

    def test_per_model_limit_rejects_before_reserving_budget(self):
        fleet = ResidentFleet({'small': {'rate_kcu_per_1k': '0.8'},
                               'large': {'rate_kcu_per_1k': '0.8', 'max_output_tokens': 512,
                                         'context_tokens': 2048}},
                              {'rig': {'token': 'w' * 64, 'models': ['small', 'large']}})
        body = workflow_request(**{**self.args, 'model': 'large'})
        pilot = InferencePilot(Path(self.tmp.name) / 'limit-ledger', fleet)
        self.addCleanup(pilot.close); pilot.create_agent('a', 1000000)
        self.assertTrue(pilot.validate(body, 'limit'))
        before = pilot.balance('a')
        for model, tokens in [('small', 129), ('large', 513), ('large', True)]:
            with self.assertRaises(PilotError):
                pilot.complete('a', 'bad', {**body, 'model': model, 'max_tokens': tokens})
        self.assertEqual(pilot.balance('a'), before)
        cards = {card['id']: card for card in fleet.catalog()['data']}
        self.assertEqual(cards['small']['max_output_tokens'], 128)
        self.assertEqual(cards['large']['max_output_tokens'], 512)
        for limit in [0, True, 2049]:
            with self.assertRaises(ValueError):
                ResidentFleet({'x': {'rate_kcu_per_1k': '0.8', 'max_output_tokens': limit}}, {})


if __name__ == '__main__': unittest.main()

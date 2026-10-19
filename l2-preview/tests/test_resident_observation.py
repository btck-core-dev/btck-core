import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('observation',ROOT/'scripts/observe_resident_coexistence.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class TestResidentObservation(unittest.TestCase):
    def status(self):
        return {'at':time.time(),'ready':True,'gate':'ready','provider_calls':9,'telemetry':{
            'miner_pid':2552,'miner_alive':True,'pearl_hashrate_hs':593e12,'pearl_accepted':7,'pearl_rejected':0}}
    def test_stale_missing_changed_and_low_hashrate_are_unhealthy(self):
        status=self.status()
        self.assertTrue(m.assess(status,now=time.time(),miner_pid=2552,minimum_hs=560e12)['healthy'])
        for edit in ({'at':0},{'ready':False},{'telemetry':{'miner_pid':2552,'miner_alive':True,'pearl_hashrate_hs':533e12}},
                     {'telemetry':{'miner_pid':999,'miner_alive':True,'pearl_hashrate_hs':593e12}}):
            self.assertFalse(m.assess({**status,**edit},now=time.time(),miner_pid=2552,minimum_hs=560e12)['healthy'])
    def test_short_fixture_records_duration_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            status=Path(td)/'status.json';status.write_text(json.dumps(self.status()))
            output=Path(td)/'observation'
            report=m.observe(status,output,seconds=.01,interval=1,miner_pid=2552,minimum_hs=560e12)
            self.assertEqual(report['qualification'],'PASS');self.assertEqual(report['required_seconds'],.01)
            self.assertEqual(report['inference_generated_by_observer'],0)
            with self.assertRaises(FileExistsError):m.observe(status,output,seconds=.01,interval=1,miner_pid=2552,minimum_hs=560e12)
            self.assertEqual(len((output/'samples.jsonl').read_text().splitlines()),2)
    def test_missing_status_finishes_fail_and_not_zero_sample_pass(self):
        with tempfile.TemporaryDirectory() as td:
            report=m.observe(Path(td)/'missing',Path(td)/'observation',seconds=.01,interval=1,miner_pid=2552,minimum_hs=560e12)
            self.assertEqual(report['qualification'],'FAIL');self.assertGreater(report['read_errors'],0)

    def transient(self):
        status=self.status();status['telemetry'].update(pearl_hashrate_hs=533e12,hashrate_transient=True,
            hashrate_grace_seconds=180,hashrate_low_seconds=10,hashrate_recovery_floor_hs=560e12,
            gpus=[{'free_mib':1800,'temperature_c':65}])
        return status

    def test_transient_policy_never_excuses_hard_gates_or_expired_window(self):
        status=self.transient()
        assess=lambda s:m.assess(s,now=time.time(),miner_pid=2552,minimum_hs=560e12,allow_transient=True)
        self.assertTrue(assess(status)['healthy'])
        for update in ({'miner_alive':False},{'miner_pid':999},{'hashrate_low_seconds':180},
                       {'hashrate_grace_seconds':600},{'pearl_hashrate_hs':0},{'pearl_hashrate_hs':float('inf')},
                       {'gpus':[{'free_mib':100,'temperature_c':65}]},
                       {'gpus':[{'free_mib':1800,'temperature_c':80}]}):
            self.assertFalse(assess({**status,'telemetry':{**status['telemetry'],**update}})['healthy'])
        self.assertFalse(assess({**status,'at':0})['healthy'])
        self.assertFalse(assess({**status,'ready':False})['healthy'])

    def test_observer_bounds_continuous_drop_even_if_worker_clock_never_advances(self):
        with tempfile.TemporaryDirectory() as td:
            status=self.transient();status['telemetry'].update(hashrate_grace_seconds=1,hashrate_low_seconds=0)
            path=Path(td)/'status.json';path.write_text(json.dumps(status));output=Path(td)/'observation'
            report=m.observe(path,output,seconds=1.01,interval=1,miner_pid=2552,minimum_hs=560e12,
                             allow_transient=True,max_transient_seconds=1)
            self.assertEqual(report['qualification'],'FAIL');self.assertTrue(report['ended_below_baseline'])
            rows=[json.loads(s) for s in (output/'samples.jsonl').read_text().splitlines()]
            self.assertTrue(any('observer_transient_window_exceeded' in s['reasons'] for s in rows))

    def test_observation_pass_requires_recovery_and_keeps_the_actual_low_sample(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'status.json';path.write_text(json.dumps(self.transient()));original=m.assess
            def recovering(*args,**kwargs):
                row=original(*args,**kwargs)
                recovered=self.transient();recovered['telemetry'].update(pearl_hashrate_hs=593e12,hashrate_transient=False)
                path.write_text(json.dumps(recovered));return row
            with patch.object(m,'assess',side_effect=recovering):
                report=m.observe(path,Path(td)/'observation',seconds=.01,interval=1,miner_pid=2552,
                                 minimum_hs=560e12,allow_transient=True)
            self.assertEqual(report['qualification'],'PASS');self.assertEqual(report['transient_samples'],1)
            self.assertEqual(report['min_observed_hashrate_hs'],533e12)

if __name__=='__main__':unittest.main()

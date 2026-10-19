import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from worker.resident_worker import ResidentWorker, configure, saved_status, gpu_processes

BASE = {'gateway_url':'http://127.0.0.1:1','backend_url':'http://127.0.0.1:2/v1',
        'token':'w'*64,'model':'model','miner_process_name':'SRBMiner-MULTI'}

class TestResidentSetup(unittest.TestCase):
    def test_automatic_restart_rediscovers_unique_gpu_miner_and_never_signals(self):
        worker=ResidentWorker(BASE)
        with patch('worker.resident_worker.gpu_processes',return_value=[{'pid':10,'name':'SRBMiner-MULTI'}]) as procs, patch.object(worker,'_miner_birth',return_value='birth-10'), patch('worker.resident_worker.os.kill') as kill:
            self.assertEqual(worker.miner_status()[1]['miner_pid'],10)
            procs.return_value=[{'pid':20,'name':'SRBMiner-MULTI'}]
            self.assertEqual(worker.miner_status()[1]['miner_pid'],20)
            kill.assert_not_called()
    def test_missing_ambiguous_and_unreadable_process_are_not_ready(self):
        worker=ResidentWorker(BASE)
        for candidates in ([],[{'pid':10,'name':'SRBMiner-MULTI'},{'pid':20,'name':'SRBMiner-MULTI'}]):
            with patch('worker.resident_worker.gpu_processes',return_value=candidates):
                self.assertFalse(worker.miner_status()[0])
        with patch('worker.resident_worker.gpu_processes',return_value=[{'pid':10,'name':'SRBMiner-MULTI'}]),patch.object(worker,'_miner_birth',return_value=None):
            self.assertFalse(worker.miner_status()[0])
        with self.assertRaises(ValueError): ResidentWorker({**BASE,'miner_pid':22})
        with self.assertRaises(ValueError): ResidentWorker({**BASE,'miner_process_name':'.*'})
    def test_csv_deduplicates_one_multi_gpu_process_and_preserves_windows_basename(self):
        raw=b'2552, ./SRBMiner-MULTI\n2552, ./SRBMiner-MULTI\n40, C:\\miners\\other.exe\n'
        with patch('worker.resident_worker.subprocess.check_output',return_value=raw):
            self.assertEqual(gpu_processes(),[{'pid':2552,'name':'SRBMiner-MULTI'},{'pid':40,'name':'other.exe'}])
    def test_windows_pinned_identity_never_uses_kill(self):
        worker=ResidentWorker({k:v for k,v in BASE.items() if k!='miner_process_name'})
        with patch('worker.resident_worker.os.name','nt'),patch('worker.resident_worker.windows_process_birth',return_value='creation'),patch('worker.resident_worker.os.kill') as kill:
            self.assertEqual(worker._miner_birth(1234),'creation');kill.assert_not_called()
    def test_windows_session_zero_resolves_only_gpu_reported_pids(self):
        raw=b'9344, [Insufficient Permissions]\n9344, [Insufficient Permissions]\n20, [Insufficient Permissions]\n'
        with patch('worker.resident_worker.os.name','nt'),patch('worker.resident_worker.subprocess.check_output',return_value=raw),patch('worker.resident_worker.windows_process_image',side_effect=[r'C:\miners\SRBMiner-MULTI.exe',None]) as images:
            self.assertEqual(gpu_processes(),[{'pid':9344,'name':'SRBMiner-MULTI.exe'},{'pid':20,'name':'[Insufficient Permissions]'}])
            self.assertEqual([c.args[0] for c in images.call_args_list],[9344,20])
    def test_setup_produces_private_config_without_starting_any_process_or_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'worker.json'
            answers=iter(['','','miner-selected-agent-model','0','','','560'])
            with patch('worker.resident_worker.inventory',return_value={'gpus':[{'index':0}],'gpu_processes':[]}),patch('worker.resident_worker.subprocess.Popen') as launch:
                configure(path,ask=lambda _:next(answers),secret=lambda _:'s'*64)
                launch.assert_not_called()
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                config=json.loads(path.read_text());self.assertNotIn('miner_pid',config)
                self.assertEqual(config['min_pearl_hashrate_hs'],560e12)
                with self.assertRaises(ValueError):configure(path,ask=lambda _:self.fail('unexpected prompt'))
    def test_stale_status_never_claims_ready_and_preserves_earnings(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'status.json';path.write_text(json.dumps({'at':time.time()-60,'ready':True,'gate':'ready','earned_development_satk':42}))
            report=saved_status({'status_file':str(path)})
            self.assertFalse(report['ready']);self.assertEqual(report['gate'],'status_stale')
            self.assertEqual(report['earned_development_satk'],42)

if __name__=='__main__':unittest.main()

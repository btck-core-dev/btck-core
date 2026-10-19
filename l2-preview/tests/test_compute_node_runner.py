import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from worker.compute_node import run_node,guard_model_warmup


class NodeRunnerTests(unittest.TestCase):
    def test_failed_startup_requires_operator_correction_before_another_gpu_launch(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'node.json'
            config={'mining_mode':'pause_on_request','miner_process_name':'miner','inference_timeout':90,
                    'model_launch':{'command':['local-model'],'log':str(Path(td)/'model.log')},
                    'model_startup_pause_marker':'Loading weights took','model_stop_command':['stop-local-model']}
            path.write_text(json.dumps(config));path.chmod(0o600)
            path.with_suffix('.startup-failed.json').write_text('{}')
            with patch('worker.compute_node.launch') as launch:
                with self.assertRaisesRegex(RuntimeError,'previously failed'):
                    run_node(path,worker=Mock())
                launch.assert_not_called()

    def test_startup_guard_keeps_a_ready_model_and_stops_failed_warmup_before_resume(self):
        from contextlib import contextmanager
        for success in (True,False):
            with tempfile.TemporaryDirectory() as td:
                log=Path(td)/'model.log';log.write_text('old record\nLoading weights took 1 second\n')
                config={'model_launch':{'log':str(log)},'model_startup_pause_marker':'Loading weights took',
                        'model_stop_command':['local-stop-model'],'model_startup_pause_seconds':30}
                worker=Mock();worker.stop=threading.Event();worker.backend_model='agent';worker.miner_name='miner'
                worker.backend='http://127.0.0.1:9/v1'
                worker.miner_status.return_value=(True,{'miner_pid':123,'miner_birth':'miner-birth'})
                events=[]
                @contextmanager
                def lease(*args):
                    events.append('paused')
                    try:yield
                    finally:events.append('resumed')
                def http(*args,**kwargs):
                    if success:return {'data':[{'id':'agent'}]}
                    worker.stop.set();raise ConnectionError('Failed local runtime')
                worker.http.side_effect=http
                with patch('worker.compute_node.MiningLease',side_effect=lease),patch('worker.compute_node.process_birth',return_value='model-birth'),patch('worker.compute_node.subprocess.run',side_effect=lambda *a,**kw:events.append('stopped')):
                    operation=lambda:guard_model_warmup(worker,config,{'pid':456,'birth':'model-birth','log_offset':11})
                    if success:
                        operation();self.assertEqual(events,['paused','resumed'])
                    else:
                        with self.assertRaises(TimeoutError):operation()
                        self.assertLess(events.index('stopped'),events.index('resumed'))

    def test_failed_managed_model_exits_for_service_recovery_without_stopping_miner(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'node.json'
            config={'mining_mode':'pause_on_request','miner_process_name':'miner','inference_timeout':90,
                    'model_launch':{'command':['local-model'],'log':str(Path(td)/'model.log')}}
            path.write_text(json.dumps(config));path.chmod(0o600)
            worker=Mock();worker.backend_model='local-agent';worker.backend='http://127.0.0.1:9/v1'
            worker.stop=threading.Event();worker.http.side_effect=ConnectionError('Not loaded yet')
            worker.miner_status.return_value=(True,{})
            worker.run.side_effect=lambda:worker.stop.wait(5)
            child=Mock();child.pid=456
            with patch('worker.compute_node.launch',return_value=child),patch('worker.compute_node.process_birth',side_effect=['birth','birth',None]):
                with self.assertRaisesRegex(RuntimeError,'Managed model process exited'):run_node(path,worker=worker)
            child.kill.assert_not_called();child.terminate.assert_not_called()

    def test_desktop_thread_starts_once_and_reuses_resident_process(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'node.json'
            config={'mining_mode':'pause_on_request','miner_process_name':'miner',
                    'inference_timeout':90,'model_launch':{'command':['local-model'],'log':str(Path(td)/'model.log')}}
            path.write_text(json.dumps(config));path.chmod(0o600)
            worker=Mock();worker.backend_model='local-agent';worker.backend='http://127.0.0.1:9/v1'
            worker.stop=threading.Event();worker.http.side_effect=ConnectionError('Not loaded yet')
            worker.miner_status.return_value=(True,{})
            child=Mock();child.pid=456
            errors=[]
            def run():
                try:run_node(path,worker=worker)
                except Exception as e:errors.append(e)
            with patch('worker.compute_node.launch',return_value=child) as launch, \
                 patch('worker.compute_node.process_birth',return_value='fixture-birth'), \
                 patch('worker.compute_node.signal.signal') as signals:
                for _ in range(2):
                    thread=threading.Thread(target=run);thread.start();thread.join(3)
                    self.assertFalse(thread.is_alive())
                self.assertFalse(errors);launch.assert_called_once();signals.assert_not_called()
                self.assertEqual(worker.run.call_count,2)
                config['model_launch']['command']=['changed-model']
                path.write_text(json.dumps(config))
                with self.assertRaises(RuntimeError):run_node(path,worker=worker)


if __name__=='__main__':unittest.main()

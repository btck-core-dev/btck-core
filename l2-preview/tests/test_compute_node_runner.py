import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from worker.compute_node import run_node


class NodeRunnerTests(unittest.TestCase):
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

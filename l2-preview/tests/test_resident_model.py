import hashlib,json,os,sys,tempfile,time,subprocess
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from worker.resident_model import verified_model,supervise,stop_owned_child

class TestResidentModel(unittest.TestCase):
    def test_manifest_rejects_corruption_and_path_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'config.json').write_text('{}');(root/'weight.safetensors').write_bytes(b'fixture')
            manifest={'verification':'PASS','revision':'pinned','path':str(root),'files':{'weight.safetensors':hashlib.sha256(b'fixture').hexdigest()}}
            file=root/'manifest.json';file.write_text(json.dumps(manifest));self.assertEqual(verified_model(file),root.resolve())
            (root/'weight.safetensors').write_bytes(b'bad')
            with self.assertRaises(ValueError):verified_model(file)
            manifest['files']={'../escape':'bad'};file.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):verified_model(file)
    @unittest.skipUnless(os.name=='posix','Model process groups require POSIX')
    def test_resource_guard_stops_only_its_own_child_not_an_existing_miner(self):
        miner=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            code=supervise([sys.executable,'-c','import time;time.sleep(30)'],lambda:(False,{}),interval=.01,bad_limit=2)
            self.assertEqual(code,75);self.assertIsNone(miner.poll())
        finally:miner.terminate();miner.wait(3)
    @unittest.skipUnless(os.name=='posix','Model process groups require POSIX')
    def test_cleanup_reaches_descendants_after_the_leader_has_exited(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/'child-pid'
            code="import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path(sys.argv[1]).write_text(str(p.pid))"
            leader=subprocess.Popen([sys.executable,'-c',code,str(file)],start_new_session=True)
            leader.wait(3);self.assertTrue(file.exists())
            stop_owned_child(leader,grace=.1)
            child_pid=int(file.read_text())
            status=subprocess.run(['ps','-p',str(child_pid),'-o','stat='],capture_output=True,text=True)
            self.assertTrue(status.returncode!=0 or status.stdout.strip().startswith('Z'))

    @unittest.skipUnless(os.name=='posix','Model process groups require POSIX')
    def test_child_failure_returns_without_retry(self):
        self.assertEqual(supervise([sys.executable,'-c','raise SystemExit(7)'],lambda:(True,{}),interval=.01),7)

if __name__=='__main__':unittest.main()

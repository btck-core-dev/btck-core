import json,os,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from worker.mining_guard import process_birth
from worker.mining_lease import MiningLease

@unittest.skipUnless(sys.platform.startswith('linux'),'Linux process suspension integration')
class TestMiningLease(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.miner=subprocess.Popen(['/usr/bin/sleep','60'])
        self.config={'mining_lease_file':str(self.path/'lease.json'),'preemption_max_seconds':1}
        self.birth=process_birth(self.miner.pid)
    def tearDown(self):
        try:os.kill(self.miner.pid,signal.SIGCONT)
        except ProcessLookupError:pass
        self.miner.terminate();self.miner.wait(3);self.temp.cleanup()
    def state(self):return Path('/proc',str(self.miner.pid),'stat').read_text().rsplit(')',1)[1].split()[0]
    def test_normal_and_backend_exception_always_resume(self):
        for error in (False,True):
            lease=MiningLease(self.config,self.miner.pid,self.birth,'sleep')
            try:
                with lease:
                    self.assertIn(self.state(),('T','t'))
                    if error:raise ValueError('backend fixture')
            except ValueError:
                self.assertTrue(error)
            self.assertNotIn(self.state(),('T','t'))
            self.assertEqual(json.loads((self.path/'lease.json').read_text())['phase'],'resumed')
    def test_watchdog_resumes_when_request_hangs(self):
        lease=MiningLease(self.config,self.miner.pid,self.birth,'sleep')
        with self.assertRaises(TimeoutError):
            with lease:
                time.sleep(1.3)
        self.assertNotIn(self.state(),('T','t'))
    def test_wrong_identity_never_pauses(self):
        with self.assertRaises(RuntimeError):
            with MiningLease(self.config,self.miner.pid,'wrong','sleep'):pass
        self.assertNotIn(self.state(),('T','t'))
    def test_owner_crash_resumes_without_client_finally(self):
        code='''import os,sys,json,time
sys.path.insert(0,sys.argv[1])
from worker.mining_lease import MiningLease
with MiningLease(json.loads(sys.argv[2]),int(sys.argv[3]),sys.argv[4],'sleep'):
 print('paused',flush=True)
 os._exit(7)
'''
        result=subprocess.run([sys.executable,'-c',code,str(ROOT/'src'),json.dumps(self.config),str(self.miner.pid),self.birth],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,7);self.assertIn('paused',result.stdout)
        end=time.monotonic()+3
        while self.state() in ('T','t') and time.monotonic()<end:time.sleep(.05)
        self.assertNotIn(self.state(),('T','t'))
    def test_conflicting_lease_cannot_resume_another_owner(self):
        with MiningLease({**self.config,'preemption_max_seconds':5},self.miner.pid,self.birth,'sleep'):
            with self.assertRaises(RuntimeError):
                with MiningLease(self.config,self.miner.pid,self.birth,'sleep'):pass
            self.assertIn(self.state(),('T','t'))
        self.assertNotIn(self.state(),('T','t'))

    def test_guard_killed_is_recovered_by_its_surviving_owner(self):
        lease=MiningLease({**self.config,'preemption_max_seconds':5},self.miner.pid,self.birth,'sleep')
        with lease:
            lease.child.kill();lease.child.wait(3)
            self.assertIn(self.state(),('T','t'))
        self.assertNotIn(self.state(),('T','t'))

    def test_status_write_failure_after_pause_still_resumes_miner(self):
        code='''import os,sys
sys.path.insert(0,sys.argv[1])
import worker.mining_guard as m
original=m.write_state
def fail(path,state):
 if state['phase']=='paused':raise OSError('disk write fixture')
 original(path,state)
m.write_state=fail
m.guard(pid=int(sys.argv[2]),birth=sys.argv[3],name='sleep',owner_pid=int(sys.argv[4]),
 owner_birth=sys.argv[5],lease_file=sys.argv[6],lease_id='a'*32,seconds=1)
'''
        result=subprocess.run([sys.executable,'-c',code,str(ROOT/'src'),str(self.miner.pid),self.birth,
                               str(os.getpid()),process_birth(os.getpid()),self.config['mining_lease_file']],capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(self.state(),('T','t'))

if __name__=='__main__':unittest.main()

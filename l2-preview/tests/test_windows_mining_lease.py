"""Actual Windows process suspension and recovery, using a disposable heartbeat."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from worker.mining_guard import process_birth
from worker.mining_lease import MiningLease, read_state


@unittest.skipUnless(os.name == 'nt', 'Windows native suspension integration')
class WindowsLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.beat = self.root / 'heartbeat'
        code = "import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(.02)"
        self.miner = subprocess.Popen([sys.executable, '-c', code, str(self.beat)])
        self.addCleanup(self.cleanup)
        self.wait_for(lambda: self.beat.exists())
        self.config = {'mining_lease_file': str(self.root / 'lease.json'), 'preemption_max_seconds': 8}

    def cleanup(self):
        self.miner.kill()
        self.miner.wait(timeout=5)
        time.sleep(.2)
        self.tmp.cleanup()

    def wait_for(self, condition, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition(): return
            time.sleep(.03)
        self.fail('Expected process/lease transition did not occur')

    def test_native_pause_and_release_resume_heartbeat(self):
        with MiningLease(self.config, self.miner.pid, process_birth(self.miner.pid), Path(sys.executable).name):
            time.sleep(.1)
            paused = self.beat.stat().st_mtime_ns
            time.sleep(.2)
            self.assertEqual(self.beat.stat().st_mtime_ns, paused)
        self.wait_for(lambda: self.beat.stat().st_mtime_ns != paused)
        self.assertEqual(read_state(self.config)['phase'], 'resumed')

    def test_owner_crash_restores_miner_without_customer_retry(self):
        code = '''import json,sys,time
from worker.mining_guard import process_birth
from worker.mining_lease import MiningLease
c=json.loads(sys.argv[1]); pid=int(sys.argv[2])
with MiningLease(c,pid,process_birth(pid),sys.argv[3]): time.sleep(60)
'''
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
        owner = subprocess.Popen([sys.executable, '-c', code, json.dumps(self.config), str(self.miner.pid), Path(sys.executable).name], env=env)
        try:
            self.wait_for(lambda: (read_state(self.config) or {}).get('phase') == 'paused')
            time.sleep(.1)
            paused = self.beat.stat().st_mtime_ns
            owner.kill(); owner.wait(timeout=5)
            self.wait_for(lambda: (read_state(self.config) or {}).get('phase') == 'resumed')
            self.wait_for(lambda: self.beat.stat().st_mtime_ns != paused)
            self.assertEqual(read_state(self.config)['resume_reason'], 'owner_disconnected')
        finally:
            if owner.poll() is None: owner.kill(); owner.wait(timeout=5)


if __name__ == '__main__': unittest.main()

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('install_worker',ROOT/'deploy/install_resident_worker.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class TestResidentInstall(unittest.TestCase):
    def test_installs_disabled_unit_with_quoted_paths_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)/'home with spaces';home.mkdir()
            config=home/'config with spaces.json'
            config.write_text(json.dumps({'gateway_url':'http://127.0.0.1:1','backend_url':'http://127.0.0.1:2/v1','token':'w'*64,'model':'x','miner_process_name':'SRBMiner-MULTI'}));config.chmod(0o600)
            with patch('sys.platform','linux'),patch('subprocess.run') as command:
                unit=m.install(config,home=home)
                command.assert_not_called()
                text=unit.read_text();self.assertIn('--config "'+str(config.resolve())+'"',text)
                self.assertFalse((home/'.config/systemd/user/default.target.wants').exists())
                with self.assertRaises(ValueError):m.install(config,home=home)
            candidates=list((home/'.local/share/btck-resident-worker').iterdir());self.assertEqual(len(candidates),1)
            env={**os.environ,'PYTHONPATH':str(candidates[0])}
            result=subprocess.run([sys.executable,'-m','worker.resident_worker','--help'],env=env,cwd=home,capture_output=True,text=True)
            self.assertEqual(result.returncode,0);self.assertIn('--setup',result.stdout)

if __name__=='__main__':unittest.main()

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]

class TestResidentPackage(unittest.TestCase):
    def test_deterministic_package_has_no_runtime_credentials_and_runs_without_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);one=root/'one.zip';two=root/'two.zip'
            for archive in (one,two):
                subprocess.run([sys.executable,str(ROOT/'scripts/build_resident_worker.py'),'--output',str(archive)],check=True,capture_output=True)
            self.assertEqual(one.read_bytes(),two.read_bytes())
            with zipfile.ZipFile(one) as z:
                self.assertIn('deploy/install_compute_node_windows.ps1', z.namelist())
                manifest=json.loads(z.read('MANIFEST.json'))
                for name,digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
                    self.assertNotIn('private',Path(name).name)
                z.extractall(root/'client')
            env={**os.environ,'PYTHONPATH':str(root/'client/src')}
            result=subprocess.run([sys.executable,'-m','worker.resident_worker','--help'],cwd=root/'client',env=env,capture_output=True,text=True,check=True)
            self.assertIn('--check',result.stdout)
            result=subprocess.run([sys.executable,'-S',str(root/'client/scripts/l2_compute_node.py'),'--help'],
                                  cwd=root,env={k:v for k,v in env.items() if k!='PYTHONPATH'},capture_output=True,text=True,check=True)
            self.assertIn('--config',result.stdout)
            result=subprocess.run([sys.executable,'-S',str(root/'client/scripts/l2_model_advisor.py'),'--help'],
                                  cwd=root,env={k:v for k,v in env.items() if k!='PYTHONPATH'},capture_output=True,text=True,check=True)
            self.assertIn('--models',result.stdout)
            result=subprocess.run(['bash',str(root/'client/deploy/worker.sh')],cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            config=root/'offline.json';config.write_text(json.dumps({'gateway_url':'http://127.0.0.1:9','backend_url':'http://127.0.0.1:9/v1','model':'fixture','token':'w'*64}),encoding='utf-8-sig');config.chmod(0o600)
            result=subprocess.run([sys.executable,'-m','worker.resident_worker','--config',str(config),'--check'],env=env,cwd=root/'client',capture_output=True,text=True)
            self.assertEqual(result.returncode,3);self.assertEqual(json.loads(result.stdout)['ready_models'],[])


if __name__=='__main__':unittest.main()

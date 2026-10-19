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

class TestAgentKit(unittest.TestCase):
    def test_extracted_sdk_runs_without_repository_or_third_party_packages(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archives=[root/'one.zip',root/'two.zip']
            for archive in archives:
                subprocess.run([sys.executable,str(ROOT/'scripts/build_l2_agent_kit.py'),'--output',str(archive)],capture_output=True,check=True)
            self.assertEqual(archives[0].read_bytes(),archives[1].read_bytes())
            with zipfile.ZipFile(archives[0]) as archive:
                manifest=json.loads(archive.read('MANIFEST.json'))
                self.assertFalse(manifest['mainnet_funds_enabled'])
                for name,digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),digest)
                archive.extractall(root/'kit')
            env={**os.environ};env.pop('PYTHONPATH',None)
            result=subprocess.run([sys.executable,'-S',str(root/'kit/scripts/l2_tool_agent.py'),'--help'],cwd=root,env=env,capture_output=True,text=True,check=True)
            self.assertIn('--journal',result.stdout)
            self.assertIn('--budget',result.stdout)

if __name__=='__main__':unittest.main()

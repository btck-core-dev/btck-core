"""Deployment evidence must identify the exact runtime content, even in a dirty checkout."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.generate_deployment_manifest import build_manifest


class TestDeploymentManifest(unittest.TestCase):
    def test_runtime_digest_changes_with_runtime_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            source = root / "src" / "node.py"
            source.write_text("VERSION = 1\n")
            first = build_manifest(root)
            source.write_text("VERSION = 2\n")
            second = build_manifest(root)

            self.assertEqual(first["schema"], "btck-deployment-manifest-v1")
            self.assertEqual(first["runtime_file_count"], 1)
            self.assertNotEqual(
                first["runtime_manifest_sha256"], second["runtime_manifest_sha256"]
            )

    def test_dirty_git_checkout_is_reported_without_hashing_dot_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "web").mkdir()
            page = root / "web" / "index.html"
            page.write_text("production")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "web/index.html"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(root), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline",
                ],
                check=True,
            )
            page.write_text("changed")

            manifest = build_manifest(root)
            self.assertTrue(manifest["git"]["dirty"])
            self.assertIn(" M web/index.html", manifest["git"]["status"])
            json.dumps(manifest)

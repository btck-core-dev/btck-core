"""Static release guards for the opt-in public HTTPS edge."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "deploy/public-edge/validate_public_edge.py"
SPEC = importlib.util.spec_from_file_location("validate_public_edge", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class TestPublicEdgeConfig(unittest.TestCase):
    def test_required_security_guards_are_present(self):
        module.require_static_guards()

    def test_env_parser_and_hostname_policy(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "edge.env"
            path.write_text(
                "BTCK_PUBLIC_HOST=explorer.devnet.example.org\n"
                "BTCK_POOL_PUBLIC_HOST=pool.devnet.example.org\n",
                encoding="utf-8",
            )
            self.assertEqual(module.load_env(path)["BTCK_PUBLIC_HOST"], "explorer.devnet.example.org")
            self.assertEqual(module.load_env(path)["BTCK_POOL_PUBLIC_HOST"], "pool.devnet.example.org")
        self.assertIsNotNone(module.HOST_RE.fullmatch("explorer.devnet.example.org"))
        self.assertIsNone(module.HOST_RE.fullmatch("UPPER.example.org"))
        self.assertIsNone(module.HOST_RE.fullmatch("localhost"))


if __name__ == "__main__":
    unittest.main()

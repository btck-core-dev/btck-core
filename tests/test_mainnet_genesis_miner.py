"""Pure tests for the release-locked fixed MAINNET genesis job."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mine_mainnet_genesis", PROJECT_ROOT / "scripts" / "mine_mainnet_genesis.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TestMainnetGenesisMiner(unittest.TestCase):
    def test_locked_manifest_builds_exact_asic_payload_without_mutation(self):
        manifest = PROJECT_ROOT / "params" / "networks" / "mainnet.json"
        before = manifest.read_bytes()
        job = MODULE.load_job(manifest, 1_791_417_600, None)

        self.assertEqual(job.bits, 0x1C017668)
        self.assertEqual(job.version, 2)
        self.assertEqual(len(job.pre_pow_hash), 32)
        self.assertEqual(len(job.kaspa_payload), 80)
        self.assertEqual(manifest.read_bytes(), before)

    def test_non_mainnet_or_approved_manifest_is_rejected(self):
        source = json.loads(
            (PROJECT_ROOT / "params" / "networks" / "mainnet.json").read_text()
        )
        with tempfile.TemporaryDirectory() as root:
            candidate = Path(root) / "candidate.json"
            source["network"] = "devnet"
            candidate.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "MAINNET"):
                MODULE.load_job(candidate, 1_791_417_600, None)

            source["network"] = "mainnet"
            source["release"]["approved"] = True
            candidate.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "release-locked"):
                MODULE.load_job(candidate, 1_791_417_600, None)

    def test_nonce_parser_is_uint64_hex(self):
        self.assertEqual(MODULE.parse_nonce("000301a0aaf30494"), 0x000301A0AAF30494)
        with self.assertRaises(ValueError):
            MODULE.parse_nonce("1" * 17)


if __name__ == "__main__":
    unittest.main()

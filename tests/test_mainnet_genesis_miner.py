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

FINALIZE_SPEC = importlib.util.spec_from_file_location(
    "finalize_mainnet_genesis", PROJECT_ROOT / "scripts" / "finalize_mainnet_genesis.py"
)
FINALIZE_MODULE = importlib.util.module_from_spec(FINALIZE_SPEC)
assert FINALIZE_SPEC.loader is not None
sys.modules[FINALIZE_SPEC.name] = FINALIZE_MODULE
FINALIZE_SPEC.loader.exec_module(FINALIZE_MODULE)


class TestMainnetGenesisMiner(unittest.TestCase):
    def test_locked_manifest_builds_exact_asic_payload_without_mutation(self):
        source = json.loads(
            (PROJECT_ROOT / "params" / "networks" / "mainnet.json").read_text()
        )
        source["release"] = {"approved": False, "approval_id": ""}
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root) / "locked.json"
            manifest.write_text(json.dumps(source), encoding="utf-8")
            before = manifest.read_bytes()
            job = MODULE.load_job(manifest, 1_791_417_600, None)
            self.assertEqual(manifest.read_bytes(), before)

        self.assertEqual(job.bits, 0x1C017668)
        self.assertEqual(job.version, 2)
        self.assertEqual(len(job.pre_pow_hash), 32)
        self.assertEqual(len(job.kaspa_payload), 80)

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

    def test_verified_result_creates_guardian_manifest(self):
        source = json.loads(
            (PROJECT_ROOT / "params" / "networks" / "mainnet.json").read_text()
        )
        source["release"] = {"approved": False, "approval_id": ""}
        source["consensus"]["hard_checkpoints"] = {}
        source["consensus"]["pow_limit_bits"] = "0x207fffff"
        source["consensus"]["daa"]["initial_bits"] = "0x207fffff"
        timestamp = 1_791_442_800
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            locked = root / "locked.json"
            result_path = root / "result.json"
            approved = root / "approved.json"
            locked.write_text(json.dumps(source), encoding="utf-8")
            job = MODULE.load_job(locked, timestamp, 0x207FFFFF)
            server = MODULE.FixedGenesisStratum(job, result_path)
            for nonce in range(100):
                pow_hash = FINALIZE_MODULE.calculate_block_pow(
                    version=job.version, chain_id=job.chain_id, parents=[],
                    timestamp=float(timestamp), bits=job.bits, nonce=nonce,
                    coinbase_output=job.coinbase_output,
                    payload_hash=FINALIZE_MODULE.hashlib.sha256(job.headline.encode()).digest(),
                    pre_pow_hash=job.pre_pow_hash, matrix=server.matrix,
                )
                if FINALIZE_MODULE.hash_to_target_int(pow_hash) <= server.network_target:
                    break
            else:
                self.fail("easy genesis nonce not found")
            result_path.write_text(
                json.dumps(MODULE.result_artifact(job, nonce, pow_hash)), encoding="utf-8"
            )
            report = FINALIZE_MODULE.finalize(
                locked, result_path, approved, timestamp, "BTCK-GUARDIAN-20261008-1500-CST"
            )
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["stratum_enabled"])
            self.assertEqual(json.loads(approved.read_text())["consensus"]["hard_checkpoints"], {})

            tampered = json.loads(result_path.read_text())
            tampered["expected_hash"] = "00" * 32
            result_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not recompute"):
                FINALIZE_MODULE.finalize(
                    locked, result_path, approved, timestamp, "BTCK-GUARDIAN-20261008-1500-CST"
                )


if __name__ == "__main__":
    unittest.main()

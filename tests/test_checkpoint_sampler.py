import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "mine_checkpoint_sampler.py"
SPEC = importlib.util.spec_from_file_location("mine_checkpoint_sampler", SCRIPT)
sampler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sampler)


class CheckpointSamplerTests(unittest.TestCase):
    def test_record_evidence_is_an_atomic_checksummed_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = sampler.record_evidence(
                {"ledger_height": 270_000}, output, "test"
            )
            payload = evidence.read_bytes()
            parsed = json.loads(payload)

            self.assertEqual(parsed["evidence_schema"], "btck-checkpoint-sampler-v1")
            self.assertEqual(parsed["stats"]["ledger_height"], 270_000)
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(output.glob("*.tmp")))

            checksum_line = evidence.with_suffix(".json.sha256").read_text().strip()
            expected = f"{hashlib.sha256(payload).hexdigest()}  {evidence.name}"
            self.assertEqual(checksum_line, expected)

    def test_checkpoint_constants_are_exact(self):
        self.assertEqual(sampler.SAMPLING_TRIGGER_HEIGHT, 270_000)
        self.assertEqual(sampler.TARGET_CHECKPOINT_HEIGHT, 271_828)
        self.assertIn(271_828, sampler.KEY_EVIDENCE_HEIGHTS)

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestCheckpointReleaseTools(unittest.TestCase):
    def test_stage_b_approval_requires_two_matching_environments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate.json"
            candidate.write_text(json.dumps({
                "network": "mainnet",
                "consensus": {"hard_checkpoints": {"271828": "11" * 32}},
                "release": {"approved": False, "approval_id": ""},
            }), encoding="utf-8")
            candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
            common = {
                "format": "btck-stage-b-independent-replay-v1",
                "verification": "PASS",
                "stage_b_manifest_sha256": candidate_sha,
                "checkpoint_hash": "11" * 32,
                "stage_b_consensus_fingerprint": "22" * 32,
                "genesis_hash": "33" * 32,
                "source_db_sha256": "44" * 32,
                "state_root": "55" * 32,
                "ledger_height": 271_828,
                "total_blocks": 271_829,
                "issued_supply_sats": 123,
                "utxo_total_sats": 123,
                "pool_balance_sats": 10,
            }
            evidence = []
            for index, environment in enumerate(("isolated-a", "isolated-b"), 1):
                evidence_dir = root / f"replay-{index}"
                evidence_dir.mkdir()
                path = evidence_dir / "replay-evidence.json"
                path.write_text(json.dumps({**common, "environment_id": environment}), encoding="utf-8")
                evidence.append(path)
            output = root / "approved.json"
            command = [
                sys.executable, str(ROOT / "scripts/approve_mainnet_checkpoint.py"),
                "--candidate-manifest", str(candidate),
                "--replay-evidence", str(evidence[0]),
                "--replay-evidence", str(evidence[1]),
                "--approval-id", "BTCK-PUBLIC-UNIT-TEST-0001",
                "--output", str(output),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            approved = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(approved["release"]["approved"])
            self.assertEqual(approved["release"]["approval_id"], "BTCK-PUBLIC-UNIT-TEST-0001")
            self.assertEqual(
                set(approved["release"]["replay_evidence_sha256"]),
                {"replay-01", "replay-02"},
            )
            self.assertEqual(
                set(approved["release"]["replay_evidence_sha256"].values()),
                {hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence},
            )
            self.assertNotIn("isolated-a", output.read_text())
            self.assertNotIn("isolated-b", output.read_text())
            public_bytes = output.read_bytes()
            reordered = list(command)
            positions = [i + 1 for i, value in enumerate(command) if value == "--replay-evidence"]
            reordered[positions[0]], reordered[positions[1]] = reordered[positions[1]], reordered[positions[0]]
            subprocess.run(reordered, check=True, capture_output=True, text=True)
            self.assertEqual(public_bytes, output.read_bytes())
            legacy_path = root / "legacy-approved.json"
            subprocess.run(command[:-1] + [str(legacy_path), "--include-environment-ids"],
                           check=True, capture_output=True, text=True)
            legacy = json.loads(legacy_path.read_text())
            self.assertEqual(set(legacy["release"]["replay_evidence_sha256"]),
                             {"isolated-a", "isolated-b"})

            duplicate = json.loads(evidence[1].read_text(encoding="utf-8"))
            duplicate["environment_id"] = "isolated-a"
            evidence[1].write_text(json.dumps(duplicate), encoding="utf-8")
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("environment IDs", rejected.stderr + rejected.stdout)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required")
    def test_release_verification_requires_external_trust_anchor_and_bound_sbom(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            release.mkdir()
            artifact = release / "node.tar.gz"
            artifact.write_bytes(b"artifact")
            artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (release / "SHA256SUMS").write_text(
                f"{artifact_sha}  {artifact.name}\n", encoding="utf-8"
            )
            sbom = release / "SBOM.spdx.json"
            sbom.write_text('{"spdxVersion":"SPDX-2.3"}\n', encoding="utf-8")
            (release / "RELEASE_MANIFEST.json").write_text(json.dumps({
                "status": "PUBLIC-MAINNET-CHECKPOINT-BOUND",
                "version": "unit-test",
                "source_commit": "00" * 20,
                "checkpoint_hash": "11" * 32,
                "sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            trusted = root / "trusted"
            attacker = root / "attacker"
            for key in (trusted, attacker):
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                    check=True,
                )
            subprocess.run([
                sys.executable, str(ROOT / "scripts/sign_mainnet_release.py"),
                "--release-dir", str(release), "--private-key", str(trusted),
                "--public-key", str(trusted.with_suffix(".pub")),
            ], check=True, capture_output=True, text=True)
            verify = [
                sys.executable, str(ROOT / "scripts/verify_mainnet_release.py"), str(release),
                "--trusted-public-key", str(trusted.with_suffix(".pub")),
            ]
            subprocess.run(verify, check=True, capture_output=True, text=True)
            wrong_anchor = subprocess.run(
                verify[:-1] + [str(attacker.with_suffix(".pub"))],
                capture_output=True, text=True,
            )
            self.assertNotEqual(wrong_anchor.returncode, 0)
            self.assertIn("externally trusted", wrong_anchor.stderr + wrong_anchor.stdout)
            sbom.write_text('{"tampered":true}\n', encoding="utf-8")
            tampered = subprocess.run(verify, capture_output=True, text=True)
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("SBOM digest mismatch", tampered.stderr + tampered.stdout)


if __name__ == "__main__":
    unittest.main()

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_guardian_stage_a.py"
SPEC = importlib.util.spec_from_file_location("verify_guardian_stage_a", MODULE_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verifier)


class TestGuardianStageAVerifier(unittest.TestCase):
    def make_snapshot(self, root: Path, captured_at: int) -> Path:
        name = datetime.fromtimestamp(captured_at, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot = root / name
        snapshot.mkdir()
        manifest = ROOT / "params" / "networks" / "mainnet.guardian.json"
        (snapshot / "mainnet.guardian.json").write_bytes(manifest.read_bytes())
        network = verifier.load_network("mainnet", manifest)
        node = {
            "network": {
                "name": "mainnet",
                "chain_id": network.chain_id,
                "genesis_hash": network.genesis.expected_hash,
                "consensus_fingerprint": network.consensus_fingerprint,
            },
            "dag": {
                "virtual_blue_order_length": 1001,
                "issued_supply_sats": 50,
                "utxo_total_sats": 50,
            },
        }
        pool = {"workers": [
            {"worker_name": "ks01", "online": True},
            {"worker_name": "ks02", "online": True},
        ]}
        (snapshot / "node-info.json").write_text(json.dumps(node), encoding="utf-8")
        (snapshot / "pool-status.json").write_text(json.dumps(pool), encoding="utf-8")
        for name in ("chain.sqlite3", "pool_accounting.db"):
            with sqlite3.connect(snapshot / name) as connection:
                connection.execute("CREATE TABLE evidence (value INTEGER)")
        files = sorted(verifier.REQUIRED_FILES)
        (snapshot / "SHA256SUMS").write_text(
            "".join(f"{hashlib.sha256((snapshot / item).read_bytes()).hexdigest()}  {item}\n" for item in files),
            encoding="utf-8",
        )
        return snapshot

    def test_valid_recent_snapshot_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = 1_800_000_000
            snapshot = self.make_snapshot(Path(temporary), now - 60)
            result = verifier.verify_snapshot(
                snapshot,
                expected_workers={"ks01", "ks02"},
                min_online_workers=2,
                max_age_seconds=300,
                now=now,
            )
            self.assertEqual(result["verification"], "PASS")
            self.assertEqual(result["sqlite_integrity"], {
                "chain.sqlite3": "ok", "pool_accounting.db": "ok"
            })

    def test_stale_or_tampered_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = 1_800_000_000
            snapshot = self.make_snapshot(Path(temporary), now - 600)
            (snapshot / "pool-status.json").write_text("{}", encoding="utf-8")
            result = verifier.verify_snapshot(
                snapshot,
                expected_workers={"ks01", "ks02"},
                min_online_workers=2,
                max_age_seconds=300,
                now=now,
            )
            self.assertEqual(result["verification"], "FAIL")
            self.assertTrue(any("stale" in item for item in result["failures"]))
            self.assertTrue(any("checksum mismatch" in item for item in result["failures"]))
            self.assertTrue(any("offline or missing" in item for item in result["failures"]))


if __name__ == "__main__":
    unittest.main()

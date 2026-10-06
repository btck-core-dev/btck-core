"""Machine comparison helpers for isolated long-chain replay evidence."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "deploy/verify_chain_replay.py"
SPEC = importlib.util.spec_from_file_location("verify_chain_replay", PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def evidence():
    return {
        "network": "devnet",
        "chain_id": "btck-devnet-v3",
        "genesis_hash": "11" * 32,
        "consensus_fingerprint": "22" * 32,
        "ledger_height": 100,
        "total_blocks": 110,
        "selected_tip": "33" * 32,
        "tips": ["33" * 32, "44" * 32],
        "state_root": "55" * 32,
        "issued_supply_sats": 1000,
        "utxo_total_sats": 1000,
        "pool_balance_sats": 200,
        "eco_fund_utxo_sats": 50,
    }


class TestVerifyChainReplay(unittest.TestCase):
    def test_identical_consensus_evidence_passes(self):
        self.assertEqual(module.compare_consensus(evidence(), evidence()), [])

    def test_missing_and_changed_fields_fail(self):
        expected = evidence()
        del expected["state_root"]
        actual = evidence()
        actual["issued_supply_sats"] += 1
        failures = "; ".join(module.compare_consensus(actual, expected))
        self.assertIn("expected evidence missing state_root", failures)
        self.assertIn("issued_supply_sats mismatch", failures)

    def test_atomic_evidence_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            module.atomic_write(path, evidence())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), evidence())


if __name__ == "__main__":
    unittest.main()

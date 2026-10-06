"""Machine gates and seven-day evidence summarization for node soak."""

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "deploy/soak/monitor_multi_node.py"
SPEC = importlib.util.spec_from_file_location("monitor_multi_node", PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def node(name, score=1000, root="ab" * 32):
    return {
        "name": name,
        "url": f"https://{name}.example",
        "identity": {"network": "devnet", "chain_id": "chain", "genesis_hash": "11" * 32, "consensus_fingerprint": "22" * 32},
        "blue_score": score,
        "total_blocks": score + 10,
        "tips_count": 2,
        "selected_tip": "33" * 32,
        "issued_supply_sats": 123,
        "utxo_total_sats": 123,
        "checkpoint_sequence": 1000,
        "checkpoint_root": root,
        "safe_mode": False,
        "connected_peers": 1,
    }


class TestMultiNodeSoak(unittest.TestCase):
    def test_healthy_nodes_and_completed_window_pass(self):
        self.assertEqual(module.evaluate_sample([node("a"), node("b", 1001)], 10, 16), [])
        start = 1_700_000_000.0
        samples = [
            {"timestamp": start, "nodes": [node("a"), node("b")], "failures": []},
            {"timestamp": start + 604800, "nodes": [node("a", 2000), node("b", 2001)], "failures": []},
        ]
        report = module.summarize(samples, 604800, 604800)
        self.assertEqual(report["verdict"], "PASS")

    def test_identity_accounting_checkpoint_and_peer_failures(self):
        bad = node("b", root="cd" * 32)
        bad["identity"]["chain_id"] = "other"
        bad["utxo_total_sats"] = 122
        bad["safe_mode"] = True
        bad["connected_peers"] = 0
        failures = module.evaluate_sample([node("a"), bad], 10, 16)
        joined = "; ".join(failures)
        self.assertIn("identity mismatch", joined)
        self.assertIn("asset conservation", joined)
        self.assertIn("safe mode", joined)
        self.assertIn("no connected", joined)
        self.assertIn("checkpoint root mismatch", joined)

    def test_incomplete_duration_never_passes(self):
        samples = [
            {"timestamp": 100.0, "nodes": [node("a"), node("b")], "failures": []},
            {"timestamp": 200.0, "nodes": [node("a", 1100), node("b", 1100)], "failures": []},
        ]
        self.assertEqual(module.summarize(samples, 604800, 600)["verdict"], "INCOMPLETE")

    def test_evidence_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.jsonl"
            first = module.append_evidence(path, {"timestamp": 100.0, "nodes": [], "failures": []})
            second = module.append_evidence(path, {"timestamp": 200.0, "nodes": [], "failures": []})
            self.assertEqual(first["previous_hash"], module.ZERO_HASH)
            self.assertEqual(second["previous_hash"], first["record_hash"])
            self.assertEqual(len(module.load_samples(path)), 2)

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            records[0]["timestamp"] = 101.0
            path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                module.load_samples(path)

    def test_incomplete_identity_and_non_hex_checkpoint_fail(self):
        bad = node("b")
        bad["identity"]["genesis_hash"] = None
        bad["checkpoint_root"] = "z" * 64
        failures = "; ".join(module.evaluate_sample([node("a"), bad], 10, 16))
        self.assertIn("genesis hash invalid", failures)
        self.assertIn("durable checkpoint missing", failures)

    def test_identity_cannot_change_mid_window(self):
        start = 1_700_000_000.0
        changed_a = node("a", 1500)
        changed_b = node("b", 1501)
        changed_a["identity"]["chain_id"] = "replacement"
        changed_b["identity"]["chain_id"] = "replacement"
        samples = [
            {"timestamp": start, "nodes": [node("a"), node("b")], "failures": []},
            {"timestamp": start + 300, "nodes": [changed_a, changed_b], "failures": []},
            {"timestamp": start + 604800, "nodes": [node("a", 2000), node("b", 2001)], "failures": []},
        ]
        report = module.summarize(samples, 604800, 604800)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("topology or identity changed", "; ".join(report["reasons"]))


if __name__ == "__main__":
    unittest.main()

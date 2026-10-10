import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestAcceptanceSummary(unittest.TestCase):
    def write_snapshot(self, root: Path, stamp: str, height: int, dag: int, shares: int) -> None:
        path = root / stamp
        path.mkdir()
        summary = {
            "chain": {"tips_count": 4},
            "stratum_last_65m": {},
            "runtime_errors_last_65m": {},
            "wire_rejections_since_monitor_start": {
                "stale_21": 0,
                "duplicate_22": 0,
                "low_difficulty_23": 0,
                "rate_limit_25": 0,
            },
            "stratum_share_counters": {
                "epoch": "test-epoch",
                "accepted": shares,
                "rejected": 0,
                "rejected_by_reason": {
                    "stale": 0,
                    "invalid": 0,
                    "duplicate": 0,
                    "rate_limit": 0,
                    "low_difficulty": 0,
                },
            },
        }
        dashboard = {
            "total_blocks": height,
            "total_dag_blocks": dag,
            "total_combos": 0,
            "combo_reward_btck": float(height),
            "accumulated_eco_reserve_btck": float(height) / 3,
            "accumulated_treasury_fee_btck": float(height) / 5,
            "pool_fee_policy": {"current_fee_rate": 0.05},
            "total_pool_hashrate_str": "~800 GH/s",
            "active_worker_count": 1,
            "miners_summary": {
                "ks01": {
                    "shares": shares,
                    "blocks": height,
                    "payout_btck": float(height),
                    "hashrate_str": "~800 GH/s",
                    "status": "ONLINE",
                }
            },
        }
        (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (path / "dashboard-stats.json").write_text(json.dumps(dashboard), encoding="utf-8")

    def test_reports_wall_clock_blue_cadence_and_accounting_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "20261003T080000Z"
            last = "20261004T080000Z"
            self.write_snapshot(root, first, 100, 110, 20)
            self.write_snapshot(root, last, 86_500, 90_000, 220)
            os.symlink(first, root / "acceptance-start")
            os.symlink(last, root / "latest")

            script = Path(__file__).resolve().parents[1] / "deploy" / "summarize_v3_acceptance.py"
            output = subprocess.check_output(
                [sys.executable, str(script), "--observation-dir", str(root)],
                text=True,
            )
            report = json.loads(output)
            self.assertTrue(report["window"]["is_24h_complete"])
            self.assertEqual(report["chain"]["blue_ledger_delta"], 86_400)
            self.assertEqual(report["chain"]["observed_seconds_per_blue"], 1.0)
            self.assertEqual(report["pool"]["miners"]["ks01"]["shares"], 200)
            self.assertEqual(report["acceptance"]["verdict"], "FAIL")
            self.assertTrue(report["acceptance"]["performance_pass"])
            self.assertFalse(report["acceptance"]["checks"]["at_least_two_online_workers"])

    def test_skips_unreadable_or_invalid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "20261003T080000Z"
            bad = "20261003T090000Z"
            last = "20261004T080000Z"
            self.write_snapshot(root, first, 100, 110, 20)
            self.write_snapshot(root, last, 86_500, 90_000, 220)
            (root / bad).mkdir()
            (root / bad / "summary.json").write_text("not-json", encoding="utf-8")
            os.symlink(first, root / "acceptance-start")
            os.symlink(last, root / "latest")

            script = Path(__file__).resolve().parents[1] / "deploy" / "summarize_v3_acceptance.py"
            output = subprocess.check_output(
                [sys.executable, str(script), "--observation-dir", str(root)],
                text=True,
            )
            report = json.loads(output)
            self.assertTrue(report["window"]["is_24h_complete"])
            self.assertEqual(report["window"]["snapshot_count"], 2)
            self.assertEqual(report["window"]["skipped_snapshot_count"], 1)
            self.assertEqual(report["window"]["skipped_snapshots"][0]["name"], bad)

    def test_reports_conditional_pass_when_only_cadence_misses_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "20261003T080000Z"
            last = "20261004T080000Z"
            self.write_snapshot(root, first, 100, 110, 20)
            self.write_snapshot(root, last, 57_700, 60_000, 220)
            for stamp, shares in ((first, 15), (last, 215)):
                dashboard_path = root / stamp / "dashboard-stats.json"
                dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
                dashboard["active_worker_count"] = 2
                dashboard["miners_summary"]["ks02"] = {
                    "shares": shares,
                    "blocks": shares,
                    "payout_btck": float(shares),
                    "hashrate_str": "~400 GH/s",
                    "status": "ONLINE",
                }
                dashboard_path.write_text(json.dumps(dashboard), encoding="utf-8")
            os.symlink(first, root / "acceptance-start")
            os.symlink(last, root / "latest")

            script = Path(__file__).resolve().parents[1] / "deploy" / "summarize_v3_acceptance.py"
            report = json.loads(subprocess.check_output(
                [sys.executable, str(script), "--observation-dir", str(root)], text=True
            ))
            self.assertTrue(report["acceptance"]["stability_pass"])
            self.assertFalse(report["acceptance"]["performance_pass"])
            self.assertEqual(report["acceptance"]["verdict"], "CONDITIONAL_PASS")

    def test_counter_epoch_change_fails_stability_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "20261003T080000Z"
            last = "20261004T080000Z"
            self.write_snapshot(root, first, 100, 110, 20)
            self.write_snapshot(root, last, 86_500, 90_000, 220)
            last_summary_path = root / last / "summary.json"
            last_summary = json.loads(last_summary_path.read_text(encoding="utf-8"))
            last_summary["stratum_share_counters"]["epoch"] = "restarted-process"
            last_summary_path.write_text(json.dumps(last_summary), encoding="utf-8")
            os.symlink(first, root / "acceptance-start")
            os.symlink(last, root / "latest")

            script = Path(__file__).resolve().parents[1] / "deploy" / "summarize_v3_acceptance.py"
            report = json.loads(subprocess.check_output(
                [sys.executable, str(script), "--observation-dir", str(root)], text=True
            ))
            self.assertFalse(report["pool"]["rejection_counter_valid"])
            self.assertFalse(report["acceptance"]["stability_pass"])
            self.assertEqual(report["acceptance"]["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()

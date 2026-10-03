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


if __name__ == "__main__":
    unittest.main()

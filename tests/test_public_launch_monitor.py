import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "public_launch_monitor.py"
SPEC = importlib.util.spec_from_file_location("public_launch_monitor", MODULE_PATH)
monitor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(monitor)


class TestPublicLaunchMonitor(unittest.TestCase):
    def test_counter_delta_requires_same_epoch(self):
        current = {"epoch": "new", "accepted": 50, "rejected": 2, "duplicate_retries": 4}
        previous = {
            "counter_epoch": "old",
            "lifetime_accepted": 40,
            "lifetime_rejected": 1,
            "lifetime_duplicate_retries": 2,
        }
        delta, comparable = monitor.counter_delta(current, previous)
        self.assertFalse(comparable)
        self.assertEqual(delta, {"accepted": 0, "rejected": 0, "duplicate_retries": 0})

    def test_pool_health_uses_interval_not_lifetime_rejections(self):
        payload = {
            "pool_mode": "SOLO",
            "workers": [],
            "active_workers": 1,
            "active_connections": 1,
            "measurement_age_seconds": 1,
            "share_counters": {
                "epoch": "same",
                "accepted": 1100,
                "rejected": 400,
                "duplicate_retries": 250,
                "rejected_by_reason": {"stale": 400},
            },
            "fee_policy": {
                "schedule": "5% on days 1-7; 3% on days 8-30; 1% from day 31 onward",
                "current_fee_rate": 0.05,
            },
        }
        previous = {
            "counter_epoch": "same",
            "lifetime_accepted": 1000,
            "lifetime_rejected": 400,
            "lifetime_duplicate_retries": 200,
        }
        result, failures = monitor.evaluate_pool(
            "solo", payload, "SOLO", 0.05, previous, 20
        )
        self.assertEqual(failures, [])
        self.assertEqual(result["interval_accepted"], 100)
        self.assertEqual(result["interval_rejected"], 0)
        self.assertEqual(result["interval_duplicate_retries"], 50)
        self.assertEqual(result["interval_rejection_rate"], 0)


if __name__ == "__main__":
    unittest.main()

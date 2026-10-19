import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('fleet_observer', Path(__file__).resolve().parents[1] / 'scripts/observe_l2_fleet.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class FleetObservationTests(unittest.TestCase):
    def test_busy_worker_is_online_but_missing_or_offline_worker_is_unhealthy(self):
        cards = [{'id': 'a', 'online_workers': 1, 'status': 'busy'}, {'id': 'b', 'online_workers': 1, 'status': 'available'}]
        self.assertTrue(module.assess({'data': cards}, ['a', 'b'])['healthy'])
        self.assertFalse(module.assess({'data': cards}, ['a', 'b', 'c'])['healthy'])
        cards[1]['online_workers'] = 0
        self.assertFalse(module.assess({'data': cards}, ['a', 'b'])['healthy'])

    def test_short_clean_window_cannot_pass_and_failures_or_gaps_are_retained(self):
        summary = {'unhealthy_samples': 0, 'max_sample_gap_seconds': 30}
        self.assertEqual(module.qualification(summary, 100, 86400, 30), 'RUNNING')
        self.assertEqual(module.qualification(summary, 86400, 86400, 30), 'PASS')
        summary['unhealthy_samples'] = 1
        self.assertEqual(module.qualification(summary, 86400, 86400, 30), 'FAIL')
        summary.update(unhealthy_samples=0, max_sample_gap_seconds=100)
        self.assertEqual(module.qualification(summary, 86400, 86400, 30), 'FAIL')

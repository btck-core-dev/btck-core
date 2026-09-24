"""
Unit Tests for BTCKStateDB (SQLite-WAL Persistence)
---------------------------------------------------
"""

import unittest
import os
import tempfile
import time
import sys

sys.path.insert(0, '/Users/a1234/btck-core/src')
from node.statedb import BTCKStateDB

class TestBTCKStateDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_chain.db")
        self.db = BTCKStateDB(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wal_journal_mode_enabled(self):
        with self.db._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

    def test_block_commit_and_miner_accounting(self):
        self.db.commit_block_solution(
            height=100,
            block_hash="000000abcdef12345678",
            timestamp_str="2026-09-23 10:00:00",
            epoch_time=time.time(),
            worker="ks001",
            mode="SOLO",
            address="btck1s022fd5...",
            base_btck=5.04,
            net_btck=5.04,
            pool_reserve_btck=1.26,
            issued_supply_btck=504.0,
            network_diff=350.0,
            is_combo=False
        )

        state = self.db.get_full_state()
        self.assertEqual(state["current_height"], 100)
        self.assertEqual(state["issued_supply_btck"], 504.0)
        self.assertEqual(state["pool_balance_btck"], 1.26)
        
        miners = state["miners_summary"]
        self.assertIn("ks001", miners)
        self.assertEqual(miners["ks001"]["blocks"], 1)
        self.assertEqual(miners["ks001"]["payout_btck"], 5.04)

    def test_combo_jackpot_recording(self):
        self.db.commit_block_solution(
            height=43200,
            block_hash="000000combohash1234",
            timestamp_str="2026-09-23 12:00:00",
            epoch_time=time.time(),
            worker="miner_154",
            mode="PPLNS",
            address="btck1s02095f...",
            base_btck=5.04,
            net_btck=54421.12,
            pool_reserve_btck=0.0,
            issued_supply_btck=217700.0,
            network_diff=380.0,
            is_combo=True,
            combo_payout_btck=54421.12
        )

        combos = self.db.get_combo_hof()
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0]["draw_height"], 43200)
        self.assertEqual(combos[0]["winner_miner"], "miner_154")
        self.assertEqual(combos[0]["payout_btck"], 54421.12)

    def test_legacy_json_import_and_export(self):
        legacy_file = os.path.join(self.temp_dir.name, "legacy_state.json")
        sample_data = {
            "current_height": 258250,
            "issued_supply_btck": 1442001.73,
            "pool_balance_btck": 41581.59,
            "miners_summary": {
                "ks001": {"mode": "SOLO", "address": "btck1s022f...", "blocks": 132234, "combos": 2, "payout_btck": 649575.23, "shares": 207717},
                "miner_154": {"mode": "PPLNS", "address": "btck1s0209...", "blocks": 46646, "combos": 2, "payout_btck": 258439.15, "shares": 108509}
            }
        }
        import json
        with open(legacy_file, "w") as f:
            json.dump(sample_data, f)

        # Import into StateDB
        self.db.import_from_legacy_json(legacy_file)
        state = self.db.get_full_state()
        self.assertEqual(state["current_height"], 258250)
        self.assertEqual(state["miners_summary"]["ks001"]["blocks"], 132234)
        self.assertEqual(state["miners_summary"]["miner_154"]["combos"], 2)

        # Export back
        export_file = os.path.join(self.temp_dir.name, "exported_state.json")
        self.db.export_to_legacy_json(export_file)
        with open(export_file) as f:
            exported = json.load(f)
        self.assertEqual(exported["current_height"], 258250)
        self.assertEqual(exported["miners_summary"]["ks001"]["payout_btck"], 649575.23)

if __name__ == "__main__":
    unittest.main()

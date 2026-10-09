import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "backup_guardian_state.py"
SPEC = importlib.util.spec_from_file_location("backup_guardian_state", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(backup)


class TestGuardianBackupRetention(unittest.TestCase):
    def test_keeps_recent_hourly_and_one_snapshot_per_recent_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = [
                "20261009T030000Z",
                "20261009T020000Z",
                "20261009T010000Z",
                "20261008T230000Z",
                "20261008T220000Z",
                "20261007T230000Z",
                "20261006T230000Z",
            ]
            for name in names:
                (root / name).mkdir()
            keep = backup.retention_plan(backup.snapshot_directories(root), 2, 3)
            self.assertEqual(
                {path.name for path in keep},
                {
                    "20261009T030000Z",
                    "20261009T020000Z",
                    "20261008T230000Z",
                    "20261007T230000Z",
                },
            )

    def test_prune_ignores_unrecognised_directories_and_latest_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ["20261009T030000Z", "20261009T020000Z", "20261008T230000Z"]
            for name in names:
                (root / name).mkdir()
            (root / "do-not-delete").mkdir()
            (root / "latest").symlink_to(names[0])
            removed = backup.prune_snapshots(root, keep_hourly=1, keep_daily=1)
            self.assertEqual(removed, names[1:])
            self.assertTrue((root / "do-not-delete").is_dir())
            self.assertTrue((root / "latest").is_symlink())


if __name__ == "__main__":
    unittest.main()

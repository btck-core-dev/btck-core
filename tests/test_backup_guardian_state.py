import importlib.util
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "backup_guardian_state.py"
SPEC = importlib.util.spec_from_file_location("backup_guardian_state", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(backup)


class TestGuardianBackupRetention(unittest.TestCase):
    def test_wal_backup_pins_a_consistent_snapshot_while_writer_keeps_committing(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.sqlite3';target=Path(directory)/'target.sqlite3'
            writer=sqlite3.connect(source)
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE values_data(id INTEGER PRIMARY KEY,payload BLOB)')
            writer.executemany('INSERT INTO values_data(payload) VALUES(?)',[(b'a'*4096,)]*700)
            writer.commit()
            commits=[]
            def concurrent_write(_delay):
                writer.execute('INSERT INTO values_data(payload) VALUES(?)',(b'new',));writer.commit()
                commits.append(1)
            with patch.object(backup.time,'sleep',side_effect=concurrent_write):
                backup.backup_sqlite(source,target,page_sleep=.001)
            with sqlite3.connect(target) as snapshot:
                self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM values_data').fetchone()[0],700)
            self.assertGreater(len(commits),1)
            self.assertGreater(writer.execute('SELECT COUNT(*) FROM values_data').fetchone()[0],700)
            writer.close()

    def test_backup_deadline_closes_connections_and_does_not_touch_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.sqlite3';target=Path(directory)/'target.sqlite3'
            with sqlite3.connect(source) as db:db.execute('CREATE TABLE saved(value)')
            with self.assertRaises(TimeoutError):
                backup.backup_sqlite(source,target,page_sleep=0,timeout_seconds=1,
                                     clock=iter([0,2]).__next__)
            with sqlite3.connect(source) as db:
                db.execute('INSERT INTO saved VALUES(42)')

    def test_keeps_recent_daily_and_weekly_points(self):
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
            keep = backup.retention_plan(backup.snapshot_directories(root), 3, 0)
            self.assertEqual(
                {path.name for path in keep},
                {
                    "20261009T030000Z",
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
            removed = backup.prune_snapshots(root, keep_daily=1, keep_weekly=0)
            self.assertEqual(removed, names[1:])
            self.assertTrue((root / "do-not-delete").is_dir())
            self.assertTrue((root / "latest").is_symlink())

    def test_disk_headroom_includes_source_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.db"
            second = root / "two.db"
            first.write_bytes(b"a" * 10)
            second.write_bytes(b"b" * 20)
            self.assertEqual(backup.required_free_bytes([first, second], 100), 130)


if __name__ == "__main__":
    unittest.main()

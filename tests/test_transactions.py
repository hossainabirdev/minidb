"""Tests for MiniDB v2 transactions: BEGIN / COMMIT / ROLLBACK."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.parser import MiniDBError


REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _run_child(code: str) -> None:
    """Run a child MiniDB process with the repo on PYTHONPATH."""
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT, env=env, check=False,
    )


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)
        self.db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
        )
        self.db.execute("INSERT INTO users (name, age) VALUES ('Abir', 20)")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def _count(self):
        return self.db.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"]

    def test_commit_survives(self):
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO users (name, age) VALUES ('Sara', 25)")
        self.assertEqual(self._count(), 2)  # visible inside the txn
        self.db.execute("COMMIT")
        self.assertEqual(self._count(), 2)
        self.db.close()
        db2 = Database(self.path)
        try:
            self.assertEqual(
                db2.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 2
            )
        finally:
            db2.close()

    def test_rollback_discards(self):
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO users (name, age) VALUES ('Temp', 99)")
        self.db.execute("UPDATE users SET age = 21 WHERE name = 'Abir'")
        self.assertEqual(self._count(), 2)
        self.db.execute("ROLLBACK")
        self.assertEqual(self._count(), 1)
        rows = self.db.execute("SELECT age FROM users WHERE name = 'Abir'").rows
        self.assertEqual(rows[0]["age"], 20)  # update was rolled back too

    def test_rollback_discards_delete_and_ddl(self):
        self.db.execute("BEGIN")
        self.db.execute("DELETE FROM users WHERE name = 'Abir'")
        self.db.execute("CREATE TABLE temp (id INTEGER PRIMARY KEY)")
        self.assertEqual(self._count(), 0)
        self.assertIn("temp", self.db.tables())
        self.db.execute("ROLLBACK")
        self.assertEqual(self._count(), 1)
        self.assertNotIn("temp", self.db.tables())

    def test_commit_without_begin_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("COMMIT")
        with self.assertRaises(MiniDBError):
            self.db.execute("ROLLBACK")

    def test_nested_begin_raises(self):
        self.db.execute("BEGIN")
        try:
            with self.assertRaises(MiniDBError):
                self.db.execute("BEGIN")
        finally:
            self.db.execute("ROLLBACK")

    def test_begin_transaction_spelling(self):
        self.db.execute("BEGIN TRANSACTION")
        self.db.execute("INSERT INTO users (name, age) VALUES ('Sara', 25)")
        self.db.execute("COMMIT TRANSACTION")
        self.assertEqual(self._count(), 2)

    def test_uncommitted_lost_on_crash(self):
        """Insert in a transaction, kill the process, reopen: row is gone."""
        code = (
            "from minidb import Database;"
            f"db = Database({self.path!r});"
            "db.execute('BEGIN');"
            "db.execute(\"INSERT INTO users (name, age) VALUES ('Ghost', 1)\");"
            "import os; os._exit(1)"
        )
        _run_child(code)
        db2 = Database(self.path)
        try:
            self.assertEqual(
                db2.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 1
            )
        finally:
            db2.close()

    def test_committed_survives_crash(self):
        """Commit, then kill the process without close: row survives."""
        code = (
            "from minidb import Database;"
            f"db = Database({self.path!r});"
            "db.execute('BEGIN');"
            "db.execute(\"INSERT INTO users (name, age) VALUES ('Kept', 30)\");"
            "db.execute('COMMIT');"
            "import os; os._exit(1)"
        )
        _run_child(code)
        db2 = Database(self.path)
        try:
            self.assertEqual(
                db2.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 2
            )
        finally:
            db2.close()


if __name__ == "__main__":
    unittest.main()

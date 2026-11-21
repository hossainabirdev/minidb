"""Tests for persistence -- reopen, and crash recovery (kill -9 style).

The crash test is the most important test in this file: it starts
a separate Python process, makes it insert rows, then kills it
with os._exit(1) -- no close(), no checkpoint(), no cleanup.
When MiniDB reopens the same file, SELECT must return the rows,
because every INSERT was fsynced to the WAL before it returned.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database

ROOT = Path(__file__).resolve().parents[1]


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "crash.minidb")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reopen_after_close(self):
        db = Database(self.path)
        db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO t (name) VALUES ('Abir')")
        db.execute("INSERT INTO t (name) VALUES ('Sara')")
        db.close()
        db2 = Database(self.path)
        try:
            rows = db2.execute("SELECT * FROM t ORDER BY id").rows
            self.assertEqual([r["name"] for r in rows], ["Abir", "Sara"])
        finally:
            db2.close()

    def test_crash_recovery_os_exit(self):
        """Insert, then os._exit(1) in a child process.  Rows must survive."""
        crash_script = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from minidb.api import Database\n"
            "import os\n"
            "db = Database(r'%s')\n"
            "db.execute(\"CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)\")\n"
            "db.execute(\"INSERT INTO t (name) VALUES ('Abir')\")\n"
            "db.execute(\"INSERT INTO t (name) VALUES ('Sara')\")\n"
            "db.execute(\"INSERT INTO t (name) VALUES ('Rafi')\")\n"
            "os._exit(1)\n" % (str(ROOT), self.path)
        )
        proc = subprocess.run(
            [sys.executable, "-c", crash_script],
            capture_output=True, text=True, timeout=30,
        )
        # os._exit(1) means the child died with code 1 -- that is the point.
        self.assertEqual(proc.returncode, 1, proc.stderr)
        # Reopen: WAL replay must bring the rows back.
        db = Database(self.path)
        try:
            rows = db.execute("SELECT * FROM t ORDER BY id").rows
            self.assertEqual([r["name"] for r in rows], ["Abir", "Sara", "Rafi"])
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM t").rows[0]["COUNT(*)"], 3
            )
        finally:
            db.close()

    def test_crash_after_update_and_delete(self):
        crash_script = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from minidb.api import Database\n"
            "import os\n"
            "db = Database(r'%s')\n"
            "db.execute(\"CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)\")\n"
            "db.execute(\"INSERT INTO t (v) VALUES ('a')\")\n"
            "db.execute(\"INSERT INTO t (v) VALUES ('b')\")\n"
            "db.execute(\"UPDATE t SET v = 'A' WHERE v = 'a'\")\n"
            "db.execute(\"DELETE FROM t WHERE v = 'b'\")\n"
            "os._exit(1)\n" % (str(ROOT), self.path)
        )
        proc = subprocess.run(
            [sys.executable, "-c", crash_script],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        db = Database(self.path)
        try:
            rows = db.execute("SELECT * FROM t").rows
            self.assertEqual(rows, [{"id": 1, "v": "A"}])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

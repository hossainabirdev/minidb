"""Tests for minidb.storage -- WAL append, replay, checkpoint."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.storage import MAGIC, Storage


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.minidb"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_reload(self):
        s = Storage(self.path)
        s.append({"op": "insert", "table": "users", "row": {"id": 1}})
        s.append({"op": "insert", "table": "users", "row": {"id": 2}})
        # A fresh Storage object must see the same records.
        records = Storage(self.path).load()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["row"]["id"], 1)
        self.assertEqual(records[1]["row"]["id"], 2)

    def test_empty_load(self):
        self.assertEqual(Storage(self.path).load(), [])

    def test_checkpoint_writes_magic_and_clears_wal(self):
        s = Storage(self.path)
        s.append({"op": "insert", "table": "t", "row": {"id": 1}})
        s.checkpoint([{"op": "insert", "table": "t", "row": {"id": 1}}])
        self.assertTrue(self.path.exists())
        self.assertTrue(self.path.read_bytes().startswith(MAGIC))
        self.assertFalse(Path(str(self.path) + "-wal").exists())
        records = Storage(self.path).load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["row"]["id"], 1)

    def test_torn_wal_tail_is_ignored(self):
        s = Storage(self.path)
        s.append({"op": "insert", "table": "t", "row": {"id": 1}})
        # Simulate a crash mid-write: append garbage bytes.
        with open(str(self.path) + "-wal", "ab") as f:
            f.write(b"\x00\x00\x00\x10\xde\xad\xbe\xefhalf-writ")
        records = Storage(self.path).load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["row"]["id"], 1)
        # After load, the torn tail is truncated: a new append works.
        Storage(self.path).append({"op": "insert", "table": "t", "row": {"id": 2}})
        self.assertEqual(len(Storage(self.path).load()), 2)

    def test_bad_magic_raises(self):
        self.path.write_bytes(b"NOTADB!!" + b"\x00" * 20)
        with self.assertRaises(ValueError):
            Storage(self.path).load()

    def test_wal_size(self):
        s = Storage(self.path)
        self.assertEqual(s.wal_size(), 0)
        s.append({"op": "insert", "table": "t", "row": {"id": 1}})
        self.assertGreater(s.wal_size(), 0)


if __name__ == "__main__":
    unittest.main()

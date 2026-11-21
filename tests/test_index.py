"""Tests for minidb.index -- HashIndex lookup and rebuild."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.index import HashIndex


class TestHashIndex(unittest.TestCase):
    def test_add_and_lookup(self):
        idx = HashIndex("idx_users_id", "users", "id", unique=True)
        idx.add(1, 101)
        idx.add(2, 102)
        self.assertEqual(idx.lookup(1), [101])
        self.assertEqual(idx.lookup(2), [102])
        self.assertEqual(idx.lookup(99), [])

    def test_non_unique_many_rows_per_value(self):
        idx = HashIndex("idx_city", "users", "city")
        idx.add("Dhaka", 1)
        idx.add("Dhaka", 2)
        idx.add("Chittagong", 3)
        self.assertEqual(sorted(idx.lookup("Dhaka")), [1, 2])
        self.assertEqual(idx.lookup("Chittagong"), [3])

    def test_remove(self):
        idx = HashIndex("idx_id", "t", "id", unique=True)
        idx.add(5, 50)
        idx.remove(5, 50)
        self.assertEqual(idx.lookup(5), [])
        self.assertFalse(idx.has(5))

    def test_has(self):
        idx = HashIndex("idx_id", "t", "id", unique=True)
        self.assertFalse(idx.has(7))
        idx.add(7, 70)
        self.assertTrue(idx.has(7))

    def test_rebuild(self):
        idx = HashIndex("idx_id", "t", "id")
        rows = [
            {"_rowid": 1, "id": 10, "name": "A"},
            {"_rowid": 2, "id": 20, "name": "B"},
            {"_rowid": 3, "id": 10, "name": "C"},
        ]
        idx.rebuild(rows)
        self.assertEqual(sorted(idx.lookup(10)), [1, 3])
        self.assertEqual(idx.lookup(20), [2])
        self.assertEqual(len(idx), 2)

    def test_roundtrip_dict(self):
        idx = HashIndex("myidx", "t", "col", unique=True)
        data = idx.to_dict()
        back = HashIndex.from_dict(data)
        self.assertEqual(back.name, "myidx")
        self.assertEqual(back.table, "t")
        self.assertEqual(back.column, "col")
        self.assertTrue(back.unique)


if __name__ == "__main__":
    unittest.main()

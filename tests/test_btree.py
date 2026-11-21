"""Tests for MiniDB v2 B-tree index: range queries and ORDER BY."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.btree import BTree


class BTreeUnitTest(unittest.TestCase):
    def test_insert_search_and_range(self):
        tree = BTree()
        for key, rid in [(5, 1), (2, 2), (8, 3), (1, 4), (9, 5), (3, 6)]:
            tree.insert(key, rid)
        self.assertEqual(tree.search(5), [1])
        self.assertEqual(tree.search(7), [])
        # 30 inserts force splits (a node holds at most 5 keys).
        big = BTree()
        for i in range(30):
            big.insert(i, i + 1)
        self.assertEqual(big.search(17), [18])
        self.assertEqual(
            big.range_query(low=10, high=14),
            [11, 12, 13, 14, 15],
        )
        self.assertEqual(
            [k for k, _ in big.inorder()], list(range(30))
        )

    def test_duplicate_keys_share_a_bucket(self):
        tree = BTree()
        tree.insert(2016, 1)
        tree.insert(2016, 2)
        tree.insert(2014, 3)
        self.assertEqual(tree.search(2016), [1, 2])
        self.assertEqual(tree.range_query(low=2016, high=2016), [1, 2])


class BTreeIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)
        self.db.execute(
            "CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT, year INTEGER)"
        )
        for title, year in [
            ("Gotham", 2014), ("Doctor Strange", 2016),
            ("Rogue One", 2016), ("Luke Cage", 2016),
            ("Old Film", 1999),
        ]:
            self.db.execute(
                f"INSERT INTO movies (title, year) VALUES ('{title}', {year})"
            )
        self.db.execute("CREATE INDEX idx_year ON movies (year) USING BTREE")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_range_greater_and_less(self):
        rows = self.db.execute(
            "SELECT title FROM movies WHERE year > 2014 ORDER BY title"
        ).rows
        self.assertEqual(len(rows), 3)
        rows = self.db.execute(
            "SELECT title FROM movies WHERE year < 2014"
        ).rows
        self.assertEqual([r["title"] for r in rows], ["Old Film"])

    def test_between(self):
        rows = self.db.execute(
            "SELECT title FROM movies WHERE year BETWEEN 2014 AND 2016 ORDER BY title"
        ).rows
        self.assertEqual(len(rows), 4)

    def test_order_by_uses_btree(self):
        rows = self.db.execute("SELECT year FROM movies ORDER BY year").rows
        self.assertEqual(
            [r["year"] for r in rows], [1999, 2014, 2016, 2016, 2016]
        )
        rows = self.db.execute("SELECT year FROM movies ORDER BY year DESC").rows
        self.assertEqual(
            [r["year"] for r in rows], [2016, 2016, 2016, 2014, 1999]
        )

    def test_equality_still_works(self):
        rows = self.db.execute(
            "SELECT title FROM movies WHERE year = 2016 ORDER BY title"
        ).rows
        self.assertEqual(len(rows), 3)

    def test_btree_survives_reopen(self):
        self.db.close()
        db2 = Database(self.path)
        try:
            rows = db2.execute(
                "SELECT title FROM movies WHERE year >= 2016 ORDER BY title"
            ).rows
            self.assertEqual(len(rows), 3)
        finally:
            db2.close()


if __name__ == "__main__":
    unittest.main()

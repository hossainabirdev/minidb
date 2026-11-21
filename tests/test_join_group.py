"""Tests for MiniDB v2: JOIN, GROUP BY / HAVING, DISTINCT."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.parser import MiniDBError


class JoinGroupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)
        self.db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, city TEXT)"
        )
        self.db.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total REAL)"
        )
        self.db.execute(
            "INSERT INTO users (name, city) VALUES "
            "('Abir', 'Dhaka'), ('Sara', 'Dhaka'), ('Rafi', 'Chittagong')"
        )
        self.db.execute(
            "INSERT INTO orders (user_id, total) VALUES "
            "(1, 99.5), (2, 150.0), (2, 50.0), (3, 200.0)"
        )

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_inner_join(self):
        rows = self.db.execute(
            "SELECT users.name, orders.total FROM users "
            "JOIN orders ON users.id = orders.user_id "
            "ORDER BY orders.total"
        ).rows
        self.assertEqual(
            [(r["name"], r["total"]) for r in rows],
            [("Sara", 50.0), ("Abir", 99.5), ("Sara", 150.0), ("Rafi", 200.0)],
        )

    def test_join_star_uses_qualified_columns(self):
        result = self.db.execute(
            "SELECT * FROM users JOIN orders ON users.id = orders.user_id "
            "WHERE orders.total > 100"
        )
        self.assertEqual(result.rowcount, 2)
        self.assertIn("users.name", result.columns)
        self.assertIn("orders.total", result.columns)

    def test_join_with_where_and_count(self):
        rows = self.db.execute(
            "SELECT COUNT(*) FROM users "
            "JOIN orders ON users.id = orders.user_id "
            "WHERE users.city = 'Dhaka'"
        ).rows
        self.assertEqual(rows[0]["COUNT(*)"], 3)

    def test_join_no_match_returns_nothing(self):
        self.db.execute("DELETE FROM orders")
        rows = self.db.execute(
            "SELECT users.name FROM users "
            "JOIN orders ON users.id = orders.user_id"
        ).rows
        self.assertEqual(rows, [])

    def test_group_by_count(self):
        rows = self.db.execute(
            "SELECT city, COUNT(*) FROM users GROUP BY city ORDER BY city"
        ).rows
        self.assertEqual(
            [(r["city"], r["COUNT(*)"]) for r in rows],
            [("Chittagong", 1), ("Dhaka", 2)],
        )

    def test_group_by_sum_avg(self):
        rows = self.db.execute(
            "SELECT user_id, COUNT(*), SUM(total), AVG(total) "
            "FROM orders GROUP BY user_id ORDER BY user_id"
        ).rows
        self.assertEqual(rows[0]["COUNT(*)"], 1)
        self.assertEqual(rows[1]["SUM(total)"], 200.0)
        self.assertEqual(rows[1]["AVG(total)"], 100.0)

    def test_group_by_having(self):
        rows = self.db.execute(
            "SELECT city, COUNT(*) FROM users GROUP BY city "
            "HAVING COUNT(*) > 1"
        ).rows
        self.assertEqual([(r["city"], r["COUNT(*)"]) for r in rows], [("Dhaka", 2)])

    def test_group_by_non_grouped_column_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("SELECT name, COUNT(*) FROM users GROUP BY city")

    def test_distinct(self):
        rows = self.db.execute("SELECT DISTINCT city FROM users ORDER BY city").rows
        self.assertEqual([r["city"] for r in rows], ["Chittagong", "Dhaka"])

    def test_distinct_multiple_columns(self):
        self.db.execute("INSERT INTO users (name, city) VALUES ('Abir', 'Dhaka')")
        rows = self.db.execute("SELECT DISTINCT name, city FROM users").rows
        # Two Abir/Dhaka rows collapse into one.
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()

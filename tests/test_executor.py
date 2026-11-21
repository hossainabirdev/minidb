"""Tests for minidb.executor -- full SQL behaviour against memory + WAL."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.parser import MiniDBError


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)
        self.db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER, city TEXT)"
        )
        self.db.execute("INSERT INTO users (name, age, city) VALUES ('Abir', 20, 'Dhaka')")
        self.db.execute("INSERT INTO users (name, age, city) VALUES ('Sara', 25, 'Dhaka')")
        self.db.execute("INSERT INTO users (name, age, city) VALUES ('Rafi', 30, 'Chittagong')")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_select_all_and_auto_increment(self):
        rows = self.db.execute("SELECT * FROM users").rows
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])

    def test_select_where_equals(self):
        rows = self.db.execute("SELECT name FROM users WHERE id = 2").rows
        self.assertEqual(rows, [{"name": "Sara"}])

    def test_where_comparisons_and_or_not(self):
        rows = self.db.execute("SELECT name FROM users WHERE age >= 25").rows
        self.assertEqual([r["name"] for r in rows], ["Sara", "Rafi"])
        rows = self.db.execute(
            "SELECT name FROM users WHERE city = 'Dhaka' OR city = 'Chittagong'"
        ).rows
        self.assertEqual(len(rows), 3)
        rows = self.db.execute(
            "SELECT name FROM users WHERE NOT (age < 25)"
        ).rows
        self.assertEqual([r["name"] for r in rows], ["Sara", "Rafi"])
        rows = self.db.execute(
            "SELECT name FROM users WHERE age > 20 AND age < 30"
        ).rows
        self.assertEqual([r["name"] for r in rows], ["Sara"])

    def test_where_not_equal(self):
        rows = self.db.execute("SELECT name FROM users WHERE city != 'Dhaka'").rows
        self.assertEqual([r["name"] for r in rows], ["Rafi"])

    def test_order_by_limit_offset(self):
        rows = self.db.execute(
            "SELECT name FROM users ORDER BY age DESC LIMIT 2"
        ).rows
        self.assertEqual([r["name"] for r in rows], ["Rafi", "Sara"])
        rows = self.db.execute(
            "SELECT name FROM users ORDER BY age ASC LIMIT 2 OFFSET 1"
        ).rows
        self.assertEqual([r["name"] for r in rows], ["Sara", "Rafi"])

    def test_aggregates(self):
        r = self.db.execute("SELECT COUNT(*) FROM users").rows[0]
        self.assertEqual(r["COUNT(*)"], 3)
        r = self.db.execute("SELECT SUM(age), AVG(age), MIN(age), MAX(age) FROM users").rows[0]
        self.assertEqual(r["SUM(age)"], 75)
        self.assertEqual(r["AVG(age)"], 25.0)
        self.assertEqual(r["MIN(age)"], 20)
        self.assertEqual(r["MAX(age)"], 30)
        r = self.db.execute("SELECT COUNT(*) FROM users WHERE age > 100").rows[0]
        self.assertEqual(r["COUNT(*)"], 0)

    def test_update_and_delete(self):
        self.db.execute("UPDATE users SET age = 21 WHERE name = 'Abir'")
        rows = self.db.execute("SELECT age FROM users WHERE name = 'Abir'").rows
        self.assertEqual(rows[0]["age"], 21)
        self.db.execute("DELETE FROM users WHERE name = 'Rafi'")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 2
        )
        self.db.execute("DELETE FROM users")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 0
        )

    def test_primary_key_duplicate_rejected(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("INSERT INTO users (id, name) VALUES (1, 'Dup')")

    def test_not_null_rejected(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("INSERT INTO users (age) VALUES (99)")

    def test_unique_rejected(self):
        self.db.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
        self.db.execute("INSERT INTO emails (email) VALUES ('a@x.com')")
        with self.assertRaises(MiniDBError):
            self.db.execute("INSERT INTO emails (email) VALUES ('a@x.com')")

    def test_wrong_type_rejected(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("INSERT INTO users (name, age) VALUES ('X', 'not-a-number')")

    def test_create_drop_index_and_table(self):
        self.db.execute("CREATE INDEX idx_city ON users (city)")
        self.assertIn("idx_city", self.db.engine.index_names())
        # Index lookup path must return the same rows as a scan.
        rows = self.db.execute("SELECT name FROM users WHERE city = 'Dhaka'").rows
        self.assertEqual(len(rows), 2)
        self.db.execute("DROP INDEX idx_city")
        self.assertNotIn("idx_city", self.db.engine.index_names())
        self.db.execute("DROP TABLE users")
        self.assertNotIn("users", self.db.tables())
        with self.assertRaises(MiniDBError):
            self.db.execute("SELECT * FROM users")

    def test_unknown_table_and_column(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("SELECT * FROM nope")
        with self.assertRaises(MiniDBError):
            self.db.execute("SELECT nope FROM users")

    def test_insert_explicit_columns_and_multi_row(self):
        self.db.execute(
            "INSERT INTO users (name, age) VALUES ('A', 1), ('B', 2)"
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"], 5
        )

    def test_checkpoint_then_reopen(self):
        self.db.checkpoint()
        self.db.close()
        db2 = Database(self.path)
        try:
            rows = db2.execute("SELECT * FROM users ORDER BY id").rows
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["name"], "Abir")
        finally:
            db2.close()


if __name__ == "__main__":
    unittest.main()

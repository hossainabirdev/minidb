"""Tests for MiniDB v2 ALTER TABLE ADD COLUMN."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.parser import MiniDBError


class AlterTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)
        self.db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
        )
        self.db.execute("INSERT INTO users (name, age) VALUES ('Abir', 20)")
        self.db.execute("INSERT INTO users (name, age) VALUES ('Sara', 25)")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_add_column_with_default(self):
        self.db.execute(
            "ALTER TABLE users ADD COLUMN email TEXT DEFAULT 'no-email'"
        )
        rows = self.db.execute("SELECT name, email FROM users ORDER BY id").rows
        self.assertEqual(
            [(r["name"], r["email"]) for r in rows],
            [("Abir", "no-email"), ("Sara", "no-email")],
        )

    def test_add_column_without_default_is_null(self):
        self.db.execute("ALTER TABLE users ADD COLUMN city TEXT")
        rows = self.db.execute("SELECT city FROM users").rows
        self.assertEqual(rows, [{"city": None}, {"city": None}])

    def test_add_column_numeric_default(self):
        self.db.execute("ALTER TABLE users ADD COLUMN score INTEGER DEFAULT 0")
        rows = self.db.execute("SELECT score FROM users").rows
        self.assertEqual(rows, [{"score": 0}, {"score": 0}])

    def test_new_inserts_use_default(self):
        self.db.execute(
            "ALTER TABLE users ADD COLUMN city TEXT DEFAULT 'Dhaka'"
        )
        self.db.execute("INSERT INTO users (name, age) VALUES ('Rafi', 30)")
        rows = self.db.execute(
            "SELECT name, city FROM users WHERE name = 'Rafi'"
        ).rows
        self.assertEqual(rows[0]["city"], "Dhaka")

    def test_add_column_survives_reopen(self):
        self.db.execute(
            "ALTER TABLE users ADD COLUMN email TEXT DEFAULT 'x@y.com'"
        )
        self.db.close()
        db2 = Database(self.path)
        try:
            rows = db2.execute("SELECT email FROM users").rows
            self.assertEqual(rows, [{"email": "x@y.com"}, {"email": "x@y.com"}])
            # The new column can be used in WHERE and INSERT.
            db2.execute("INSERT INTO users (name, age, email) VALUES ('Rafi', 30, 'r@x.com')")
            self.assertEqual(
                db2.execute(
                    "SELECT COUNT(*) FROM users WHERE email = 'r@x.com'"
                ).rows[0]["COUNT(*)"],
                1,
            )
        finally:
            db2.close()

    def test_add_existing_column_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("ALTER TABLE users ADD COLUMN name TEXT")

    def test_add_not_null_without_default_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("ALTER TABLE users ADD COLUMN city TEXT NOT NULL")

    def test_add_primary_key_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("ALTER TABLE users ADD COLUMN x INTEGER PRIMARY KEY")

    def test_alter_unknown_table_raises(self):
        with self.assertRaises(MiniDBError):
            self.db.execute("ALTER TABLE nope ADD COLUMN x TEXT")


if __name__ == "__main__":
    unittest.main()

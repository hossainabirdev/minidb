"""Tests for minidb.api -- Database, TableHandle, Collection, REPL."""
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.parser import MiniDBError
from minidb.repl import run_repl


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        self.db = Database(self.path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_execute_and_tables(self):
        self.db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self.assertIn("t", self.db.tables())

    def test_table_handle(self):
        self.db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        users = self.db.table("users")
        users.insert({"name": "Abir"})
        users.insert({"name": "Sara"})
        self.assertEqual(len(users.all()), 2)
        self.assertEqual(users.count(), 2)
        self.assertEqual(users.find("name = 'Sara'")[0]["name"], "Sara")
        with self.assertRaises(MiniDBError):
            self.db.table("nope")

    def test_collection_litedb_style(self):
        movies = self.db.collection("movies")
        mid = movies.insert({"title": "Gotham", "year": 2014, "genres": ["Action", "Crime"]})
        movies.insert({"title": "Doctor Strange", "year": 2016})
        self.assertEqual(movies.count(), 2)
        all_docs = movies.find_all()
        self.assertEqual(len(all_docs), 2)
        self.assertEqual(all_docs[0]["title"], "Gotham")
        self.assertEqual(all_docs[0]["genres"], ["Action", "Crime"])
        self.assertEqual(all_docs[0]["_id"], mid)
        one = movies.find_one(mid)
        self.assertEqual(one["title"], "Gotham")
        self.assertIsNone(movies.find_one(9999))
        # Collection survives reopen (same executor, same WAL).
        self.db.close()
        db2 = Database(self.path)
        try:
            self.assertEqual(db2.collection("movies").count(), 2)
        finally:
            db2.close()

    def test_kv_set_get(self):
        self.assertIsNone(self.db.kv_get("missing"))
        self.db.kv_set("hello", "world")
        self.assertEqual(self.db.kv_get("hello"), "world")
        self.db.kv_set("hello", "again")
        self.assertEqual(self.db.kv_get("hello"), "again")

    def test_context_manager(self):
        with Database(self.path) as db:
            db.execute("CREATE TABLE c (id INTEGER PRIMARY KEY)")
            self.assertIn("c", db.tables())

    def test_repl_scripted_session(self):
        # Pipe a scripted session into the REPL and check real output.
        script = (
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);\n"
            "INSERT INTO users (name) VALUES ('Abir');\n"
            "INSERT INTO users (name) VALUES ('Sara');\n"
            "SELECT * FROM users;\n"
            "SELECT COUNT(*) FROM users;\n"
            ".exit\n"
        )
        out = io.StringIO()
        run_repl(self.path, stdin=io.StringIO(script), stdout=out)
        text = out.getvalue()
        self.assertIn("Abir", text)
        self.assertIn("Sara", text)
        self.assertIn("2", text)


if __name__ == "__main__":
    unittest.main()

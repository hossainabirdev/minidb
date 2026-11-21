"""Tests for MiniDB v2 Studio viewer and TCP server (real, live)."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.api import Database
from minidb.server import query, serve, serve_once
from minidb.studio import serve as studio_serve


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StudioTcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.minidb")
        db = Database(self.path)
        db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
        )
        db.execute("INSERT INTO users (name, age) VALUES ('Abir', 20)")
        db.execute("INSERT INTO users (name, age) VALUES ('Sara', 25)")
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_studio_tables_and_query(self):
        port = _free_port()
        server = studio_serve("127.0.0.1", port, self.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                page = resp.read().decode("utf-8")
            self.assertIn("MiniDB Studio", page)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/tables"
            ) as resp:
                data = json.loads(resp.read())
            self.assertEqual(data["tables"][0]["name"], "users")
            self.assertEqual(data["tables"][0]["rows"], 2)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/query?sql="
                "SELECT%20name%20FROM%20users%20WHERE%20age%20%3E%2020"
            ) as resp:
                data = json.loads(resp.read())
            self.assertEqual(data["rows"], [{"name": "Sara"}])
        finally:
            server.shutdown()
            server.server_close()
            server.db.close()

    def test_studio_rejects_non_select(self):
        port = _free_port()
        server = studio_serve("127.0.0.1", port, self.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/query",
                data=json.dumps(
                    {"sql": "DELETE FROM users"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            self.assertIn("error", data)
            self.assertIn("read-only", data["error"])
            # The rows are still there.
            db = Database(self.path)
            try:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM users").rows[0]["COUNT(*)"],
                    2,
                )
            finally:
                db.close()
        finally:
            server.shutdown()
            server.server_close()
            server.db.close()

    def test_tcp_server_select_returns_json(self):
        port = _free_port()
        thread = threading.Thread(
            target=serve_once, args=("127.0.0.1", port, self.path), daemon=True
        )
        thread.start()
        time.sleep(0.3)
        result = query("127.0.0.1", port, "SELECT name FROM users ORDER BY age")
        self.assertEqual(
            result["rows"], [{"name": "Abir"}, {"name": "Sara"}]
        )
        self.assertEqual(result["columns"], ["name"])
        thread.join(timeout=5)

    def test_tcp_server_error_is_json(self):
        port = _free_port()
        thread = threading.Thread(
            target=serve, args=("127.0.0.1", port, self.path, 1), daemon=True
        )
        thread.start()
        time.sleep(0.3)
        result = query("127.0.0.1", port, "SELECT * FROM nope")
        self.assertIn("error", result)
        thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

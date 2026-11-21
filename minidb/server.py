"""TCP server mode -- MiniDB v2 (real, single-database server).

What it does
------------
Listens on a TCP port.  Clients connect one at a time and send
one SQL statement per line; the server runs it against one
database file and sends back one JSON object per line::

    {"columns": [...], "rows": [...], "rowcount": n, "message": "..."}
    {"error": "..."}

Transactions work across lines inside one connection: BEGIN,
then INSERTs, then COMMIT or ROLLBACK -- all on the same open
Database, so the transaction state is shared for that client.

How to run it (on your own computer)::

    python -m minidb.server 127.0.0.1 8765

Then, in another terminal::

    printf 'SELECT * FROM users;\\n' | nc 127.0.0.1 8765

Or with the small Python client in examples/tcp_client.py::

    python examples/tcp_client.py "SELECT * FROM users"

Honest limits (v2)
-------------------
* One client at a time.  A second client waits until the first
  disconnects -- that is deliberate for v2, not a bug.
* No authentication, no TLS, localhost only by default.
  Do NOT expose this port to the internet.
* One database file per server process.
"""

from __future__ import annotations

import json
import socket
import sys

from .api import Database
from .parser import MiniDBError

# No auth, no TLS.  Localhost only by default.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _handle_client(conn, db: Database) -> None:
    """Serve one client until it disconnects (or sends QUIT)."""
    reader = conn.makefile("r", encoding="utf-8")
    for line in reader:
        line = line.strip()
        if not line:
            continue
        if line.upper() in ("QUIT", "EXIT"):
            break
        try:
            result = db.execute(line)
            payload = {
                "columns": result.columns,
                "rows": result.rows,
                "rowcount": result.rowcount,
                "message": result.message,
            }
        except MiniDBError as exc:
            payload = {"error": str(exc)}
        except Exception as exc:  # never kill the server on bad SQL
            payload = {"error": str(exc)}
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def serve_once(host: str, port: int, db_path: str) -> None:
    """Serve exactly one client, then stop.  Used by tests."""
    db = Database(db_path)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(1)
            conn, _addr = server.accept()
            with conn:
                _handle_client(conn, db)
    finally:
        db.close()


def serve(host: str, port: int, db_path: str, max_clients: int | None = None) -> None:
    """Serve clients one after another, on one shared Database.

    max_clients limits how many clients are served (None means
    forever).  Tests pass max_clients=1 to stop after one client.
    """
    db = Database(db_path)
    served = 0
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(5)
            print(f"MiniDB server on {host}:{port} (db: {db_path})")
            print("One client at a time, no auth, no TLS, localhost only.")
            while max_clients is None or served < max_clients:
                conn, _addr = server.accept()
                served += 1
                with conn:
                    _handle_client(conn, db)
    finally:
        db.close()


def query(host: str, port: int, sql: str) -> dict:
    """Small client helper: send one SQL line, return the JSON reply."""
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall((sql.strip() + "\n").encode("utf-8"))
        sock.sendall(b"QUIT\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        line = data.decode("utf-8").strip().split("\n")[0]
        return json.loads(line)


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    db_path = sys.argv[3] if len(sys.argv) > 3 else "data/mydata.minidb"
    serve(host, port, db_path)


if __name__ == "__main__":
    main()

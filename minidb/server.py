"""TCP server mode -- v2 PLACEHOLDER, not part of MiniDB v1.

Why is this file here at all?  So the shape of v2 is visible
and nobody has to guess where a server would live later.  It is
deliberately small and clearly marked: do not use it for real
work yet.

What it does (v2 sketch, single client only)
--------------------------------------------
Listens on a TCP port.  One client at a time sends one SQL
statement per line; the server runs it and sends back one JSON
object per line: {"columns": [...], "rows": [...]} or
{"error": "..."}.  There is no authentication, no TLS and no
concurrency in this sketch -- that is exactly why it is v2.

How to try it (for learning only, on your own computer)::

    python -m minidb.server 127.0.0.1 8765

Then, in another terminal::

    printf 'SELECT * FROM users;\\n' | nc 127.0.0.1 8765

Real server features -- many clients at once, authentication,
a proper wire protocol, BEGIN/COMMIT -- are v2/LATER.  See
README.md, section "What is v2 / later".
"""

from __future__ import annotations

import json
import socket
import sys

from .api import Database
from .parser import MiniDBError

# NOTE (v2): single client, no auth, no TLS.  Localhost only by
# default.  Do not expose this port to the internet.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def serve_once(host: str, port: int, db_path: str) -> None:
    """Serve exactly one client, then stop.  Used by tests only."""
    db = Database(db_path)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(1)
            conn, _addr = server.accept()
            with conn:
                # Text lines, one SQL statement per line.
                reader = conn.makefile("r", encoding="utf-8")
                for line in reader:
                    line = line.strip()
                    if not line:
                        continue
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
                    conn.sendall(
                        (json.dumps(payload) + "\n").encode("utf-8")
                    )
    finally:
        db.close()


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    db_path = sys.argv[3] if len(sys.argv) > 3 else "data/mydata.minidb"
    print(f"MiniDB server (v2 placeholder, single client) on {host}:{port}")
    print("WARNING: no auth, no TLS, localhost only.  For learning only.")
    # v2 sketch: serve clients one after another, forever.
    while True:
        serve_once(host, port, db_path)


if __name__ == "__main__":
    main()

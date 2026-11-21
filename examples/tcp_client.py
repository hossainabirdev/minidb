"""MiniDB TCP client example (v2).

Start the server first, in another terminal::

    python -m minidb.server 127.0.0.1 8765

Then run this client with one SQL statement::

    python examples/tcp_client.py "SELECT * FROM users"
    python examples/tcp_client.py "SELECT COUNT(*) FROM users"

The server replies with one JSON object per line; this script
prints it nicely.  No auth, no TLS, localhost only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.server import query

HOST = "127.0.0.1"
PORT = 8765


def main() -> None:
    sql = " ".join(sys.argv[1:]) or "SELECT * FROM users"
    result = query(HOST, PORT, sql)
    if "error" in result:
        print(f"Error: {result['error']}")
        return
    if result["columns"]:
        print(" | ".join(result["columns"]))
        for row in result["rows"]:
            print(" | ".join(str(row.get(c)) for c in result["columns"]))
        print(f"({len(result['rows'])} row(s))")
    else:
        print(result["message"])


if __name__ == "__main__":
    main()

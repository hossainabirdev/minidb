"""MiniDB Studio: a small read-only web viewer for MiniDB v2.

What it is
----------
A single web page (stdlib ``http.server`` only -- no framework,
no JavaScript library, no external files) that shows what is
inside a database file: its tables and collections, their rows,
and the result of a SELECT typed into a box.

What it is NOT
--------------
* Not an editor: it only runs SELECT.  INSERT / UPDATE / DELETE
  / CREATE / DROP are rejected with an error.  The viewer can
  never change your data.
* Not connected to the engine's write path: it opens the same
  Database the REPL uses and calls ``db.execute()`` -- the
  engine is untouched by this file.
* Not for the internet: it listens on 127.0.0.1 only, no auth,
  no TLS.  Run it on your own computer only.

How to run it::

    python -m minidb.studio
    python -m minidb.studio 127.0.0.1 8766 data/mydata.minidb

Then open http://127.0.0.1:8766 in a browser.

API (all JSON, all read-only)
-----------------------------
GET  /api/tables            -> {"tables": [{"name": ..., "columns": [...], "rows": n}]}
GET  /api/table?name=users  -> {"columns": [...], "rows": [...]}
GET  /api/query?sql=SELECT...  -> {"columns": [...], "rows": [...]}
POST /api/query  {"sql": "SELECT ..."} -> same as GET
"""

from __future__ import annotations

import html
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .api import Database
from .parser import MiniDBError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>MiniDB Studio</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:24px} h2{font-size:18px;margin-top:28px}
table{border-collapse:collapse;width:100%} th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}
th{background:#f2f4f7} textarea{width:100%;height:64px;font-family:monospace}
button{padding:8px 16px;cursor:pointer} .note{color:#666;font-size:14px}
pre{background:#f2f4f7;padding:12px;white-space:pre-wrap}
</style></head><body>
<h1>MiniDB Studio</h1>
<p class="note">Read-only viewer. Only SELECT runs here — nothing in this page can change your data.</p>
<h2>Tables</h2><div id="tables">Loading…</div>
<h2>Run a SELECT</h2>
<textarea id="sql">SELECT * FROM users LIMIT 10</textarea><br>
<button onclick="runQuery()">Run</button>
<pre id="out">Type a SELECT above and press Run.</pre>
<script>
async function loadTables(){
  const r = await fetch('/api/tables'); const d = await r.json();
  if(d.error){ document.getElementById('tables').textContent = d.error; return; }
  if(!d.tables.length){ document.getElementById('tables').textContent = '(no tables yet)'; return; }
  document.getElementById('tables').innerHTML = d.tables.map(t =>
    '<p><b>'+esc(t.name)+'</b> ('+t.columns.map(esc).join(', ')+') — '+t.rows+' rows '+
    '<button onclick="showTable(\\''+esc(t.name)+'\\')">Show rows</button></p>').join('');
}
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/'/g,'&#39;'); }
async function showTable(name){
  const r = await fetch('/api/table?name='+encodeURIComponent(name)); const d = await r.json();
  showResult(d, 'Table '+name);
}
async function runQuery(){
  const sql = document.getElementById('sql').value;
  const r = await fetch('/api/query', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sql})});
  const d = await r.json(); showResult(d, sql);
}
function showResult(d, title){
  const out = document.getElementById('out');
  if(d.error){ out.textContent = 'Error: '+d.error; return; }
  if(!d.rows.length){ out.textContent = title+'\\n(0 rows)'; return; }
  let s = d.columns.join(' | ')+'\\n'+d.columns.map(()=> '---').join(' | ')+'\\n';
  d.rows.forEach(row => { s += d.columns.map(c => String(row[c]===null?'NULL':row[c])).join(' | ')+'\\n'; });
  s += '('+d.rows.length+' row(s))';
  out.textContent = s;
}
loadTables();
</script></body></html>
"""


def _tables_payload(db: Database) -> dict:
    tables = []
    for name in db.tables():
        rows = db.execute(f"SELECT COUNT(*) FROM {name}").rows
        count = int(rows[0]["COUNT(*)"]) if rows else 0
        # Column names: ask the engine, not the file.
        table = db.engine._get_table(name)
        tables.append(
            {"name": name, "columns": table.column_names(), "rows": count}
        )
    return {"tables": tables}


def _run_select(db: Database, sql: str) -> dict:
    """Run one SELECT and return its rows as JSON data.

    Only SELECT is allowed here.  Anything else is rejected
    before it reaches the engine.
    """
    if sql is None or not str(sql).strip():
        return {"error": "Empty SQL statement."}
    first = str(sql).strip().split(None, 1)[0].upper().rstrip(";")
    if first != "SELECT":
        return {"error": "Studio is read-only: only SELECT is allowed."}
    try:
        result = db.execute(sql)
    except MiniDBError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # never crash the viewer on bad SQL
        return {"error": str(exc)}
    return {"columns": result.columns, "rows": result.rows}


def make_handler(db: Database):
    """Build a request handler bound to one open Database."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - http.server naming
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/tables":
                self._send_json(_tables_payload(db))
                return
            if parsed.path == "/api/table":
                name = (query.get("name") or [""])[0]
                if name not in db.tables():
                    self._send_json(
                        {"error": f"Table {name!r} does not exist."},
                        status=400,
                    )
                    return
                try:
                    result = db.execute(f"SELECT * FROM {name}")
                    self._send_json(
                        {"columns": result.columns, "rows": result.rows}
                    )
                except MiniDBError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                return
            if parsed.path == "/api/query":
                sql = (query.get("sql") or [""])[0]
                self._send_json(_run_select(db, sql))
                return
            self._send_json({"error": "Not found."}, status=404)

        def do_POST(self):  # noqa: N802 - http.server naming
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api/query":
                self._send_json({"error": "Not found."}, status=404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeDecodeError):
                self._send_json({"error": "Bad JSON body."}, status=400)
                return
            sql = data.get("sql", "") if isinstance(data, dict) else ""
            self._send_json(_run_select(db, sql))

    return Handler


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          db_path: str = "data/mydata.minidb") -> ThreadingHTTPServer:
    """Open the database and build a Studio server (does not run it)."""
    db = Database(db_path)
    server = ThreadingHTTPServer((host, port), make_handler(db))
    server.db = db  # type: ignore[attr-defined]
    return server


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    db_path = sys.argv[3] if len(sys.argv) > 3 else "data/mydata.minidb"
    server = serve(host, port, db_path)
    print(f"MiniDB Studio at http://{host}:{port}  (read-only, localhost only)")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.db.close()
        server.server_close()


if __name__ == "__main__":
    main()

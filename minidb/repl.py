"""REPL: a small interactive prompt for MiniDB.

Run it with::

    python -m minidb.repl
    python -m minidb.repl data/mydata.minidb

You get a ``minidb>`` prompt.  Type SQL, end it with ``;``,
and press Enter.  Multi-line statements work: the prompt waits
until it sees a ``;``.

    minidb> CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
    minidb> INSERT INTO users (name) VALUES ('Abir');
    minidb> SELECT * FROM users;
    id | name
    ---+------
    1  | Abir

Stepping stone (first week only)
-------------------------------
Before SQL feels natural, two tiny commands help:

    minidb> SET mykey hello
    minidb> GET mykey
    hello

SET / GET store one key and one value.  They are real: they use
the same _kv table and the same WAL as everything else.  They
are a stepping stone, not the main language -- SQL is.

Dot commands (start with a dot, no semicolon needed):
    .help        show this help
    .tables      list all tables
    .checkpoint  write a snapshot and clear the WAL
    .exit        quit (also Ctrl-D / Ctrl-C)
"""

from __future__ import annotations

import sys

from .api import Database
from .parser import MiniDBError


def _print_rows(result, out=None) -> None:
    """Print a SELECT Result as a simple text table."""
    import sys as _sys
    out = out if out is not None else _sys.stdout
    def _say(text=""):
        print(text, file=out)
    if not result.columns:
        _say(result.message or "OK.")
        return
    if not result.rows:
        _say("(0 rows)")
        return
    # Column widths: the longest value in each column.
    widths = []
    for col in result.columns:
        best = len(str(col))
        for row in result.rows:
            best = max(best, len(str(row.get(col))))
        widths.append(best)
    header = " | ".join(
        str(col).ljust(w) for col, w in zip(result.columns, widths)
    )
    _say(header)
    _say("-+-".join("-" * w for w in widths))
    for row in result.rows:
        _say(
            " | ".join(
                str(row.get(col)).ljust(w)
                for col, w in zip(result.columns, widths)
            )
        )
    _say(f"({len(result.rows)} row(s))")


def _print_help(out=None) -> None:
    import sys as _sys
    out = out if out is not None else _sys.stdout
    print("MiniDB REPL -- type SQL ending with ;  (.help, .tables, .exit)", file=out)
    print("  SET key value   store a value (stepping stone)", file=out)
    print("  GET key         read a value back", file=out)
    print("  .tables         list tables", file=out)
    print("  .checkpoint     write a snapshot, clear the WAL", file=out)
    print("  .exit           quit", file=out)


def run_repl(db_path: str = "data/mydata.minidb", stdin=None, stdout=None) -> None:
    """Run the REPL.  stdin/stdout are injectable for tests."""
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout

    def say(text: str = "") -> None:
        print(text, file=out)

    db = Database(db_path)
    say("MiniDB 2.0.1 — built by Abir Hossain -- type .help for help, .exit to quit.")
    buffer = ""
    try:
        while True:
            prompt = "minidb> " if not buffer else "    ...> "
            try:
                if inp is sys.stdin:
                    line = input(prompt)
                else:
                    line = inp.readline()
                    if line == "":
                        break  # end of piped input
                    line = line.rstrip("\n")
                    # Echo piped input so a scripted session is readable.
                    say(f"{prompt}{line}")
            except EOFError:
                break
            except KeyboardInterrupt:
                say("")
                break
            stripped = line.strip()
            if not buffer and not stripped:
                continue
            # Dot commands, only when not inside a statement.
            if not buffer and stripped.startswith("."):
                cmd = stripped[1:].strip().lower()
                if cmd in ("exit", "quit"):
                    break
                if cmd == "help":
                    _print_help(out)
                    continue
                if cmd == "tables":
                    tables = db.tables()
                    say(", ".join(tables) if tables else "(no tables)")
                    continue
                if cmd == "checkpoint":
                    db.checkpoint()
                    say("Checkpoint done.")
                    continue
                say(f"Unknown command: {stripped}  (try .help)")
                continue
            # SET / GET stepping stone, only at statement start.
            if not buffer and stripped.upper().startswith("SET "):
                parts = stripped.split(None, 2)
                if len(parts) < 3:
                    say("Usage: SET key value")
                    continue
                db.kv_set(parts[1], parts[2])
                say("OK.")
                continue
            if not buffer and stripped.upper().startswith("GET "):
                parts = stripped.split(None, 1)
                value = db.kv_get(parts[1].strip()) if len(parts) > 1 else None
                say(value if value is not None else "(NULL)")
                continue
            buffer += line + "\n"
            if ";" not in buffer:
                continue
            # Run each complete statement in the buffer.
            statements, buffer = buffer, ""
            for part in statements.split(";"):
                if not part.strip():
                    continue
                try:
                    result = db.execute(part)
                except MiniDBError as exc:
                    say(f"Error: {exc}")
                except Exception as exc:  # never crash the REPL
                    say(f"Error: {exc}")
                else:
                    if result.columns:
                        # Print through the real stdout so tests
                        # that capture it see the table.
                        _print_rows(result, out)
                    else:
                        say(result.message or "OK.")
    finally:
        db.close()
        say("Bye.")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/mydata.minidb"
    run_repl(path)


if __name__ == "__main__":
    main()

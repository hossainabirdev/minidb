# MiniDB

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Stdlib only](https://img.shields.io/badge/stdlib-only-green.svg)](https://docs.python.org/3/library/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/hossainabirdev/mini-db/actions/workflows/tests.yml/badge.svg)](https://github.com/hossainabirdev/mini-db/actions/workflows/tests.yml)

**A tiny database engine built from scratch in Python — no SQLite, no ORM, no libraries.**

Built by **Abir Hossain** (BSc CSE, Uttara University, Dhaka) — GitHub: [hossainabirdev](https://github.com/hossainabirdev) — primary identity: Software Engineering.

## Why I built this

I am a CSE student, and I wanted to understand how a database actually works inside — not just how to call one. So I built one myself, from zero: how a row becomes bytes in a file, how data survives a crash, how an index makes a search fast. MiniDB is my way of learning that, line by line.

MiniDB is a real, working database, not a demo with preset output. You create a table, insert rows, and they are written to a file on disk. Kill the process, reopen the file, and the rows are still there. Every line of the engine is hand-written in Python using only the standard library, so a 2nd-year CSE student can read it and understand how a database actually works inside.

Inspired by the ideas behind LiteDB, SQLite and Redis — single-file storage, a write-ahead log (WAL), hash indexes, and SQL-like queries — rebuilt from zero, in Python, at a size you can read in an afternoon.

## Why from scratch?

Using SQLite would teach you SQLite's API. Building MiniDB teaches you what a database *is*:

- how a row becomes bytes in a file,
- how a checksum catches a half-written record after a crash,
- how an index turns a full scan into an O(1) lookup,
- how a hand-written parser turns `SELECT ... WHERE ...` into a tree,
- how an executor walks that tree row by row.

## Layer diagram

```
SQL text
   |
   v
parser.py        tokenizer + recursive-descent parser  -> AST
   |
   v
executor.py      validate -> WAL append -> apply in memory
   |                |                         |
   |                v                         v
   |           storage.py              index.py / schema.py
   |           (WAL + snapshot)        (hash index / table shape)
   v
api.py           Database / TableHandle / Collection (LiteDB-style)
   |
   +-- repl.py   interactive minidb> prompt
   +-- server.py TCP server sketch (v2 placeholder only)
```

| File | Job | Lines |
| --- | --- | --- |
| `minidb/storage.py` | Append-only WAL + snapshot, magic `MINIDB1`, length + CRC32 records, fsync, replay, checkpoint | ~250 |
| `minidb/index.py` | In-memory hash index `dict[value, list[_rowid]]`, rebuilt on open | ~120 |
| `minidb/schema.py` | `Table`, `Column`, types INTEGER/TEXT/REAL/BLOB | ~150 |
| `minidb/parser.py` | Hand-written tokenizer + recursive-descent parser | ~450 |
| `minidb/executor.py` | Row-by-row execution, constraints, aggregates, index fast-path | ~500 |
| `minidb/api.py` | `Database`, `table()`, `collection()` (documents as rows) | ~250 |
| `minidb/repl.py` | `minidb>` prompt, SET/GET stepping stone | ~150 |
| `minidb/server.py` | TCP server sketch — **v2 placeholder only** | ~80 |

## Quick start

Python 3.10+ is enough. There is nothing to install.

```bash
# 1. Try the demo (run it twice -- the second run proves persistence)
python examples/demo.py
python examples/demo.py

# 2. Open the REPL
python -m minidb.repl
```

```sql
minidb> CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER);
minidb> INSERT INTO users (name, age) VALUES ('Abir', 20), ('Sara', 25);
minidb> SELECT * FROM users WHERE age >= 21 ORDER BY age DESC;
minidb> SELECT COUNT(*) FROM users;
minidb> .exit
```

Or from Python:

```python
from minidb import Database

db = Database("data/mydata.minidb")
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute("INSERT INTO users (name) VALUES ('Abir')")
print(db.execute("SELECT * FROM users").rows)  # [{'id': 1, 'name': 'Abir'}]

# LiteDB-style documents -- same engine, same file
movies = db.collection("movies")
movies.insert({"title": "Gotham", "year": 2014, "genres": ["Action", "Crime"]})
print(movies.find_all())
db.close()
```

## SQL supported in v1

- `CREATE TABLE` / `DROP TABLE` — types `INTEGER`, `TEXT`, `REAL`, `BLOB`
- `INSERT` — single and multi-row, explicit column list or all columns
- `SELECT` — `*`, column list, `WHERE` with `= != <> < > <= >= AND OR NOT` and parentheses, `ORDER BY ... ASC/DESC`, `LIMIT`, `OFFSET`
- `UPDATE` / `DELETE` — with optional `WHERE`
- `CREATE INDEX` / `DROP INDEX` — hash index, `O(1)` for `WHERE id = ?`
- Constraints — `PRIMARY KEY` (INTEGER auto-increments), `NOT NULL`, `UNIQUE`
- Aggregates — `COUNT(*)`, `COUNT(col)`, `SUM(col)`, `AVG(col)`, `MIN(col)`, `MAX(col)`

Constraints are enforced, not decorative: a duplicate PRIMARY KEY, a NULL in a NOT NULL column, a duplicate UNIQUE value, or a wrong type all raise an error and write nothing.

## How data survives a crash

1. Every change is appended to `data/mydata.minidb-wal` first, as a length-prefixed JSON record with a CRC32 checksum, then `fsync()`ed.
2. Only then is it applied in memory.
3. On open, MiniDB loads the snapshot (`data/mydata.minidb`, magic `MINIDB1`), then replays the WAL. A half-written last record (torn write) fails its checksum and is ignored and truncated.
4. `CHECKPOINT` (called automatically on `db.close()`) writes all live rows into a fresh snapshot and deletes the WAL.

Test it yourself: `tests/test_persistence.py` inserts rows in a child process, kills it with `os._exit(1)` — no close, no checkpoint — reopens, and `SELECT` returns the rows.

## Running the tests

```bash
python -m unittest discover -s tests -v
# or
python -m pytest tests/
```

One test file per layer: `test_storage`, `test_index`, `test_parser`, `test_executor`, `test_api`, `test_persistence` (includes the crash-recovery test).

## Honest limits (v1)

MiniDB v1 is a learning engine. It is correct for what it does, and it is honest about what it does not do yet:

- **No transactions** — `BEGIN` / `COMMIT` / `ROLLBACK` are v2.
- **No JOIN, no GROUP BY** — single-table queries only in v1.
- **No encryption** — the data file is plain JSON records. (LiteDB's own docs warn its DES/AES implementation is not secure — do not use it either.)
- **No file/blob storage** — `BLOB` accepts str/bytes values, but storing large files GridFS-style is v2.
- **Single writer, single process** — no locking, no concurrent clients. The TCP server in `server.py` is a v2 placeholder (single client, no auth, no TLS, localhost only).
- **In-memory tables** — all live rows are held in memory; the index is rebuilt on open. This is fine for a small database and is a deliberate trade-off for readability, not a bug.
- **No query planner** — the only fast path is an indexed `WHERE col = literal`; everything else is a scan.

## What is v2 / later

Transactions (BEGIN/COMMIT/ROLLBACK), JOIN, GROUP BY, a real multi-client TCP server with auth, B-tree index alongside the hash index, file/blob storage, and a small Studio-style viewer (a web page that shows tables and runs queries, like LiteDB Studio).

## Learning order

Read the files in this order: `schema.py` → `storage.py` → `index.py` → `parser.py` → `executor.py` → `api.py` → `repl.py`. `BANGLA_GUIDE.md` explains each file in simple Bengali, section by section.

## License

MIT — see `LICENSE`.

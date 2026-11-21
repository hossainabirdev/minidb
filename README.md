# MiniDB

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Stdlib only](https://img.shields.io/badge/stdlib-only-green.svg)](https://docs.python.org/3/library/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/hossainabirdev/minidb/actions/workflows/tests.yml/badge.svg)](https://github.com/hossainabirdev/minidb/actions/workflows/tests.yml)

**A tiny database engine built from scratch in Python — no SQLite, no ORM, no libraries.**

Built by **Abir Hossain** (BSc CSE, Uttara University, Dhaka) — GitHub: [hossainabirdev](https://github.com/hossainabirdev) — primary identity: Software Engineering.

## What MiniDB is

MiniDB is a real, working database, not a demo with preset output. You create a table, insert rows, and they are written to a file on disk. Kill the process, reopen the file, and the rows are still there. Every line of the engine is hand-written in Python using only the standard library, so a 2nd-year CSE student can read it and understand how a database actually works inside.

Inspired by the ideas behind LiteDB, SQLite and Redis — single-file storage, a write-ahead log (WAL), hash and B-tree indexes, and SQL-like queries — rebuilt from zero, in Python, at a size you can read in a few sittings.

## Why I built this

I am a CSE student, and I wanted to understand how a database actually works inside — not just how to call one. So I built one myself, from zero: how a row becomes bytes in a file, how data survives a crash, how an index makes a search fast, how a transaction can be rolled back. MiniDB is my way of learning that, line by line.

Using SQLite would teach you SQLite's API. Building MiniDB teaches you what a database *is*:

- how a row becomes bytes in a file,
- how a checksum catches a half-written record after a crash,
- how an index turns a full scan into an O(1) lookup, and a B-tree into a range scan,
- how a hand-written parser turns `SELECT ... WHERE ...` into a tree,
- how an executor walks that tree row by row, and how a transaction buffers its writes.

## What is new in v2.0.0

- Transactions — `BEGIN` / `COMMIT` / `ROLLBACK`, real via the WAL: uncommitted writes never reach the file.
- B-tree index — `CREATE INDEX ... USING BTREE` for range queries (`>`, `<`, `BETWEEN`) and `ORDER BY` without a sort.
- JOIN — one `INNER JOIN` on two tables with `ON` equality.
- GROUP BY / HAVING and `SELECT DISTINCT` with `COUNT` / `SUM` / `AVG` / `MIN` / `MAX`.
- `ALTER TABLE ... ADD COLUMN` with a `DEFAULT` value, persisted for old and new rows.
- MiniDB Studio — a small read-only web viewer that lists tables and runs a `SELECT` from a box.
- TCP server — a real single-database server: one SQL line in, one JSON object out.
- README rewritten for reviewers, and `BANGLA_GUIDE.md` updated to v2.

## Architecture

```
SQL text
   |
   v
parser.py        tokenizer + recursive-descent parser  -> AST
   |
   v
executor.py      validate -> WAL append (or txn buffer) -> apply in memory
   |                |                              |
   |                v                              v
   |           storage.py              index.py / btree.py / schema.py
   |           (WAL + snapshot)        (hash index / B-tree / table shape)
   v
api.py           Database / TableHandle / Collection (LiteDB-style)
   |
   +-- repl.py   interactive minidb> prompt
   +-- studio.py read-only web viewer (SELECT only)
   +-- server.py TCP server (one client at a time, no auth/TLS)
```

| File | Job |
| --- | --- |
| `minidb/storage.py` | Append-only WAL + snapshot, magic `MINIDB1`, length + CRC32 records, fsync, replay, checkpoint |
| `minidb/index.py` | In-memory hash index `dict[value, list[_rowid]]`, rebuilt on open |
| `minidb/btree.py` | Real B-tree index: sorted keys, range queries, `ORDER BY` without a sort |
| `minidb/schema.py` | `Table`, `Column`, types INTEGER/TEXT/REAL/BLOB, DEFAULT values |
| `minidb/parser.py` | Hand-written tokenizer + recursive-descent parser |
| `minidb/executor.py` | Row-by-row execution, constraints, aggregates, JOIN, GROUP BY, transactions |
| `minidb/api.py` | `Database`, `table()`, `collection()` (documents as rows) |
| `minidb/repl.py` | `minidb>` prompt, SET/GET stepping stone |
| `minidb/studio.py` | Read-only web viewer (SELECT only) |
| `minidb/server.py` | TCP server, one SQL line in, one JSON object out |

## SQL supported in v2

**Tables and schema**

- `CREATE TABLE` / `DROP TABLE` — types `INTEGER`, `TEXT`, `REAL`, `BLOB`
- `ALTER TABLE ... ADD COLUMN ... [DEFAULT value] [NOT NULL] [UNIQUE]`
- `CREATE INDEX` / `DROP INDEX` — hash index by default, `USING BTREE` for range queries

**Rows**

- `INSERT` — single and multi-row, explicit column list or all columns
- `UPDATE` / `DELETE` — with optional `WHERE`
- Constraints — `PRIMARY KEY` (INTEGER auto-increments), `NOT NULL`, `UNIQUE`, enforced before any write

**Queries**

- `SELECT` — `*`, column list, `table.column`
- `WHERE` with `= != <> < > <= >= AND OR NOT`, parentheses, `BETWEEN`
- `JOIN` — one `INNER JOIN ... ON a.col = b.col`
- `GROUP BY` / `HAVING`, `SELECT DISTINCT`
- `ORDER BY ... ASC/DESC`, `LIMIT`, `OFFSET`
- Aggregates — `COUNT(*)`, `COUNT(col)`, `SUM(col)`, `AVG(col)`, `MIN(col)`, `MAX(col)`

**Transactions**

- `BEGIN` / `COMMIT` / `ROLLBACK` (also `BEGIN TRANSACTION`)

Constraints are enforced, not decorative: a duplicate PRIMARY KEY, a NULL in a NOT NULL column, a duplicate UNIQUE value, or a wrong type all raise an error and write nothing.

## How to use

### 1. Install — nothing to install

Python 3.11+ is enough. There is nothing to install, no pip packages, no database server.

```bash
git clone https://github.com/hossainabirdev/minidb.git
cd minidb
```

### 2. Demo — run it twice, the second run proves persistence

```bash
python examples/demo.py
python examples/demo.py
python examples/demo_v2.py
python examples/demo_v2.py
```

`demo.py` is the v1 demo (movies, COUNT, AVG). `demo_v2.py` shows transactions, JOIN, GROUP BY, B-tree range queries and `ALTER TABLE` — every number comes from the file, nothing is preset.

### 3. REPL — type SQL, see real results

```bash
python -m minidb.repl
```

```sql
minidb> CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER);
minidb> INSERT INTO users (name, age) VALUES ('Abir', 20), ('Sara', 25);
minidb> SELECT * FROM users WHERE age >= 21 ORDER BY age DESC;
minidb> SELECT city, COUNT(*) FROM users GROUP BY city;
minidb> BEGIN;
minidb> INSERT INTO users (name, age) VALUES ('Temp', 99);
minidb> ROLLBACK;
minidb> SELECT COUNT(*) FROM users;
minidb> .tables
minidb> .exit
```

Dot commands: `.help`, `.tables`, `.checkpoint`, `.exit`. `SET key value` / `GET key` is a first-week stepping stone, stored in a normal table.

### 4. Python API

```python
from minidb import Database

db = Database("data/mydata.minidb")
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute("INSERT INTO users (name) VALUES ('Abir')")
print(db.execute("SELECT * FROM users").rows)  # [{'id': 1, 'name': 'Abir'}]

# Transactions from Python
db.begin()
db.execute("INSERT INTO users (name) VALUES ('Sara')")
db.commit()  # or db.rollback()

# LiteDB-style documents -- same engine, same file
movies = db.collection("movies")
movies.insert({"title": "Gotham", "year": 2014, "genres": ["Action", "Crime"]})
print(movies.find_all())
db.close()
```

### 5. Studio viewer — see the database in a browser

```bash
python -m minidb.studio
```

Open http://127.0.0.1:8766 — it lists tables, shows rows, and runs a `SELECT` typed into a box. Read-only: only `SELECT` runs, anything else is rejected, so the viewer can never change your data.

### 6. TCP server — query MiniDB over a socket

```bash
# Terminal 1: start the server
python -m minidb.server 127.0.0.1 8765

# Terminal 2: ask it something
python examples/tcp_client.py "SELECT * FROM users"
```

The server replies with one JSON object per line: `{"columns": [...], "rows": [...], "rowcount": n}` or `{"error": "..."}`. One client at a time, no auth, no TLS, localhost only.

### 7. Running the tests

```bash
python -m unittest discover -s tests -v
# or
python -m pytest tests/
```

One test file per area: `test_storage`, `test_index`, `test_parser`, `test_executor`, `test_api`, `test_persistence` (crash recovery), `test_transactions`, `test_btree`, `test_join_group`, `test_alter`, `test_studio_tcp` — 88 tests in total, including a test that kills a process mid-transaction and reopens the file.

## How data survives a crash — and transactions

1. Every change is appended to `data/mydata.minidb-wal` first, as a length-prefixed JSON record with a CRC32 checksum, then `fsync()`ed.
2. Only then is it applied in memory.
3. On open, MiniDB loads the snapshot (`data/mydata.minidb`, magic `MINIDB1`), then replays the WAL. A half-written last record (torn write) fails its checksum and is ignored and truncated.
4. `CHECKPOINT` (called automatically on `db.close()`) writes all live rows into a fresh snapshot and deletes the WAL.

Transactions use the same WAL: inside `BEGIN`, writes are applied in memory but only kept in a pending list — they are appended to the WAL on `COMMIT`, in order. `ROLLBACK` (or a crash before `COMMIT`) restores a snapshot taken at `BEGIN`, so the uncommitted rows never reach the file.

## Honest limits (v2)

MiniDB v2 is a learning engine. It is correct for what it does, and it is honest about what it does not do yet:

- **No encryption** — the data file is plain JSON records. (LiteDB's own docs warn its DES/AES implementation is not secure — do not use it either.)
- **No multi-writer** — single writer, single process, no locking. The TCP server serves one client at a time, with no auth and no TLS, localhost only.
- **No replication or sharding** — one file, one machine.
- **No full SQL dialect** — one INNER JOIN only, no subqueries, no views, no stored procedures, no triggers.
- **In-memory tables** — all live rows are held in memory; indexes are rebuilt on open. This is fine for a small database and is a deliberate trade-off for readability, not a bug.
- **B-tree is in-memory** — the tree is rebuilt from rows on open, like the hash index; it is not yet paged to disk.

## Learning order

Read the files in this order: `schema.py` → `storage.py` → `index.py` → `btree.py` → `parser.py` → `executor.py` → `api.py` → `repl.py` → `studio.py` → `server.py`. `BANGLA_GUIDE.md` explains each file in simple Bengali, section by section.

## License

MIT — see `LICENSE`.

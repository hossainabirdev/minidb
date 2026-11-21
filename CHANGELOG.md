# Changelog

All notable changes to MiniDB are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.0.0] — 2026-11-21

The next version, all at once: the remaining v1 features, built
for real and tested.

### Added

- Transactions — `BEGIN` / `COMMIT` / `ROLLBACK` (also `BEGIN
  TRANSACTION`), real via the WAL: uncommitted writes are kept
  in a pending list and only reach the file on `COMMIT`;
  `ROLLBACK` (or a crash before `COMMIT`) restores a snapshot
  taken at `BEGIN`. Tests include a crash test for both cases.
- B-tree index (`minidb/btree.py`) — `CREATE INDEX ... USING
  BTREE` for range queries (`>`, `<`, `>=`, `<=`, `BETWEEN`) and
  `ORDER BY` without a sort. The hash index is kept for `=`
  lookups.
- `JOIN` — one `INNER JOIN ... ON a.col = b.col`, nested loops,
  qualified column names (`users.name`, `orders.total`) in
  `SELECT *`.
- `GROUP BY` / `HAVING` and `SELECT DISTINCT` with `COUNT` /
  `SUM` / `AVG` / `MIN` / `MAX`.
- `ALTER TABLE ... ADD COLUMN ... [DEFAULT value] [NOT NULL]
  [UNIQUE]`, persisted for old rows (DEFAULT or NULL) and new
  rows (DEFAULT when omitted).
- MiniDB Studio (`minidb/studio.py`) — a small read-only web
  viewer on http://127.0.0.1:8766: lists tables, shows rows,
  runs a `SELECT` typed in a box. Only `SELECT` is allowed.
- TCP server (`minidb/server.py`, now real, not a placeholder)
  — one SQL line in, one JSON object out; one client at a time,
  no auth, no TLS, localhost only. Small client in
  `examples/tcp_client.py`.
- `examples/demo_v2.py` — transactions, JOIN, GROUP BY, B-tree
  range, `ALTER TABLE`, all with real output from the file.
- Tests: 88 in total (50 from v1 + 38 new) — `test_transactions`,
  `test_btree`, `test_join_group`, `test_alter`,
  `test_studio_tcp`.

### Changed

- Version bumped to 2.0.0 in `minidb/__init__.py` and
  `pyproject.toml`; REPL banner now reads
  `MiniDB 2.0.0 — built by Abir Hossain`.
- README rewritten for internship / job / scholarship reviewers:
  one What's new section, full How to use (install, demo, REPL,
  Python API, Studio, TCP server, tests), honest limits.
- `BANGLA_GUIDE.md` updated to v2 in simple Bengali.

### Fixed

- README Tests badge pointed to `hossainabirdev/mini-db` —
  now points to the real repo `hossainabirdev/minidb`
  (also fixed in `pyproject.toml` URLs and `GITHUB_UPDATE.md`).

## [1.1.0] — 2026-11-21

No engine change. This release makes v1.0.0 ready for GitHub.

### Added

- Author and personal note in `README.md` — built by Abir Hossain
  (BSc CSE, Uttara University, Dhaka).
- Badges at the top of `README.md` (Python 3.12, stdlib only, MIT,
  and the real GitHub Actions tests badge).
- `pyproject.toml` — project metadata for `mini-db` 1.1.0,
  Python >= 3.11, no dependencies.
- `.github/workflows/tests.yml` — GitHub Actions runs the test suite
  on Python 3.11 and 3.12 on every push and pull request.
- `CHANGELOG.md` (this file) and `GITHUB_UPDATE.md` (paste-ready
  repo description, topics, and first-push commands).

### Changed

- Version bumped to 1.1.0 in `minidb/__init__.py`
  (`__version__ = "1.1.0"`, `__author__ = "Abir Hossain"`).
- REPL banner now reads
  `MiniDB 1.1.0 — built by Abir Hossain`.

## [1.0.0] — 2026-11-21

Initial real engine, built from scratch in Python (standard library
only — no SQLite, no ORM, no libraries).

### Added

- Single-file storage (`mydata.minidb`) with append-only WAL
  (`mydata.minidb-wal`), CRC32 checksums, `fsync` on write, replay
  on open, torn-write truncation, and `CHECKPOINT` snapshots.
- In-memory hash index, rebuilt on open; O(1) lookup for
  `WHERE col = ?` on indexed columns.
- Hand-written SQL tokenizer and recursive-descent parser.
- Executor with row-by-row execution and constraint enforcement.
- SQL: `CREATE TABLE` / `DROP TABLE`, `INSERT` (single and
  multi-row), `SELECT` with `WHERE` (`= != <> < > <= >= AND OR NOT`,
  parentheses), `ORDER BY`, `LIMIT`, `OFFSET`, `UPDATE`, `DELETE`,
  `CREATE INDEX` / `DROP INDEX`, `PRIMARY KEY` (INTEGER
  auto-increment), `NOT NULL`, `UNIQUE`, and aggregates
  `COUNT(*)` / `COUNT(col)` / `SUM` / `AVG` / `MIN` / `MAX`.
- Python API (`Database`, `table()`) and LiteDB-style collections
  (`collection()` stores documents as rows with a JSON `doc` column,
  on the same engine).
- `minidb>` REPL with `SET` / `GET` stepping stone and dot commands
  (`.help`, `.tables`, `.checkpoint`, `.exit`).
- TCP server sketch in `server.py` (v2 placeholder only).
- Tests: 50 tests, one file per layer, including crash-recovery
  tests (insert, then kill the process, reopen, rows survive).
- `README.md`, `BANGLA_GUIDE.md`, MIT `LICENSE`, `.gitignore`.

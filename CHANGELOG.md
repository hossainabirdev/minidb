# Changelog

All notable changes to MiniDB are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

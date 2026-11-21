"""Python API: use MiniDB without writing SQL strings by hand.

Two styles, one engine -- both go through the same executor and
the same WAL, so there is no second engine to keep in sync:

1. SQL style (the main style)::

       db = Database("data/mydata.minidb")
       db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
       db.execute("INSERT INTO users (name) VALUES ('Abir')")
       db.execute("SELECT * FROM users").rows

2. Table style (a small helper over SQL)::

       users = db.table("users")
       users.insert({"name": "Abir"})
       users.all()

3. Collection style (LiteDB-style documents)::

       movies = db.collection("movies")
       movies.insert({"title": "Gotham", "year": 2014,
                      "genres": ["Action", "Crime"]})
       movies.find_all()   # returns the original dicts back

A collection is stored as a normal table with two columns:
``id INTEGER PRIMARY KEY`` and ``doc TEXT`` (the document as a
JSON string).  Storing documents as rows means collections get
persistence, WAL replay, CHECKPOINT and indexes for free.
"""

from __future__ import annotations

import json

from .executor import Engine, Result
from .parser import MiniDBError


def _sql_literal(value: object) -> str:
    """Turn a Python value into a safe SQL literal.

    Strings are quoted and a single quote inside becomes two
    quotes (the SQL way to escape a quote).  This is only used
    by the table/collection helpers, which build SQL from dicts.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise MiniDBError(f"Cannot store value of type {type(value).__name__}.")


class TableHandle:
    """A friendly wrapper around one table, using SQL underneath."""

    def __init__(self, db: "Database", table: str):
        self._db = db
        self._table = table

    def insert(self, row: dict) -> Result:
        """Insert one row given as a dict, e.g. {"name": "Abir"}."""
        if not row:
            raise MiniDBError("insert() needs at least one column.")
        columns = ", ".join(row.keys())
        values = ", ".join(_sql_literal(v) for v in row.values())
        return self._db.execute(
            f"INSERT INTO {self._table} ({columns}) VALUES ({values})"
        )

    def all(self) -> list[dict]:
        """All rows in the table, in insertion order."""
        return self._db.execute(f"SELECT * FROM {self._table}").rows

    def find(self, where: str) -> list[dict]:
        """Rows matching a WHERE fragment, e.g. "age > 20"."""
        return self._db.execute(
            f"SELECT * FROM {self._table} WHERE {where}"
        ).rows

    def count(self) -> int:
        """Number of rows in the table."""
        result = self._db.execute(f"SELECT COUNT(*) FROM {self._table}")
        return int(result.rows[0]["COUNT(*)"])


class Collection:
    """LiteDB-style document collection, stored as rows.

    Example::

        movies = db.collection("movies")
        movies.insert({"title": "Gotham", "year": 2014})
        movies.find_all()  # [{'title': 'Gotham', 'year': 2014, '_id': 1}]
    """

    def __init__(self, db: "Database", name: str):
        self._db = db
        self._name = name
        # Create the backing table on first use.  If it already
        # exists this is a no-op (we check first, we do not rely
        # on catching an error).
        if name not in db.engine.table_names():
            db.execute(
                f"CREATE TABLE {name} "
                "(id INTEGER PRIMARY KEY, doc TEXT NOT NULL)"
            )

    def insert(self, doc: dict) -> int:
        """Insert a document.  Returns the new id."""
        if not isinstance(doc, dict):
            raise MiniDBError("A document must be a dict.")
        text = json.dumps(doc, ensure_ascii=False)
        self._db.execute(
            f"INSERT INTO {self._name} (doc) VALUES ({_sql_literal(text)})"
        )
        # The id is max(id) after the insert: the table only grows
        # by this method, and id auto-increments, so this is safe.
        rows = self._db.execute(
            f"SELECT MAX(id) FROM {self._name}"
        ).rows
        return int(rows[0]["MAX(id)"])

    def find_all(self) -> list[dict]:
        """All documents, each with its "_id" added."""
        out: list[dict] = []
        for row in self._db.execute(f"SELECT * FROM {self._name}").rows:
            doc = json.loads(row["doc"])
            doc["_id"] = row["id"]
            out.append(doc)
        return out

    def find_one(self, doc_id: int) -> dict | None:
        """One document by id, or None if it does not exist."""
        rows = self._db.execute(
            f"SELECT * FROM {self._name} WHERE id = {int(doc_id)}"
        ).rows
        if not rows:
            return None
        doc = json.loads(rows[0]["doc"])
        doc["_id"] = rows[0]["id"]
        return doc

    def count(self) -> int:
        result = self._db.execute(f"SELECT COUNT(*) FROM {self._name}")
        return int(result.rows[0]["COUNT(*)"])


class Database:
    """The main entry point.  Opens (or creates) a database file.

    Usage::

        db = Database("data/mydata.minidb")
        db.execute("SELECT * FROM users")
        db.close()

    Or as a context manager (close is automatic)::

        with Database("data/mydata.minidb") as db:
            db.execute("...")

    The default path is data/mydata.minidb in the current folder.
    The data file and its WAL are ignored by git (see .gitignore):
    code goes to GitHub, data stays on your own computer.
    """

    def __init__(self, path: str = "data/mydata.minidb"):
        self.path = path
        self.engine = Engine(path)

    # -- SQL ----------------------------------------------------------

    def execute(self, sql: str) -> Result:
        """Run one SQL statement and return a Result."""
        return self.engine.execute(sql)

    def execute_many(self, sql_script: str) -> list[Result]:
        """Run several statements separated by ';'."""
        return self.engine.execute_many(sql_script)

    # -- helpers ------------------------------------------------------

    def table(self, name: str) -> TableHandle:
        """A TableHandle for an existing table."""
        if name not in self.engine.table_names():
            raise MiniDBError(f"Table {name!r} does not exist.")
        return TableHandle(self, name)

    def collection(self, name: str) -> Collection:
        """A LiteDB-style document collection (created if needed)."""
        return Collection(self, name)

    def tables(self) -> list[str]:
        """Names of all tables in this database."""
        return self.engine.table_names()

    def checkpoint(self) -> None:
        """Write a compact snapshot and clear the WAL."""
        self.engine.checkpoint()

    # -- key/value stepping stone (used by the REPL) ------------------

    def kv_set(self, key: str, value: str) -> None:
        """SET key value -- the REPL's first-week stepping stone.

        Stored in a normal table (_kv), so it is durable exactly
        like any other row.  Kept here so the REPL stays tiny.
        """
        if "_kv" not in self.engine.table_names():
            self.execute(
                "CREATE TABLE _kv (key TEXT PRIMARY KEY, value TEXT)"
            )
        existing = self.execute(
            f"SELECT * FROM _kv WHERE key = {_sql_literal(key)}"
        ).rows
        if existing:
            self.execute(
                f"UPDATE _kv SET value = {_sql_literal(value)} "
                f"WHERE key = {_sql_literal(key)}"
            )
        else:
            self.execute(
                f"INSERT INTO _kv (key, value) VALUES "
                f"({_sql_literal(key)}, {_sql_literal(value)})"
            )

    def kv_get(self, key: str) -> str | None:
        """GET key -- returns None if the key does not exist."""
        if "_kv" not in self.engine.table_names():
            return None
        rows = self.execute(
            f"SELECT * FROM _kv WHERE key = {_sql_literal(key)}"
        ).rows
        return rows[0]["value"] if rows else None

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Checkpoint and close.  Safe to call more than once."""
        self.engine.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

"""Executor: run a parsed statement against tables held in memory.

How the layers fit together
---------------------------
    SQL text --parser--> AST --executor--> tables + rows (memory)
                                                      |
                                                  storage (WAL)
                                                      v
                                                file on disk

The executor is the only place that changes tables or rows.
Every change follows the same safe order:

    1. Validate everything first (types, NOT NULL, UNIQUE, ...).
       If validation fails, nothing is written anywhere.
    2. Append the change to the WAL (storage.append, with fsync).
    3. Apply the change in memory and update the indexes.

If the process dies between steps 2 and 3, the WAL record is
replayed on the next open, so the change is not lost.  If it
dies before step 2, the change never happened -- also correct.

Rows in memory
--------------
Each row is a plain dict, e.g. {"id": 1, "name": "Abir"}, plus a
hidden key "_rowid": a number MiniDB assigns itself (1, 2, 3, ...)
that never changes for that row.  _rowid is how UPDATE and
DELETE find a row again when the WAL is replayed.  It is never
shown to the user: SELECT output strips it out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .index import HashIndex
from .parser import (
    Aggregate,
    BinaryOp,
    ColumnRef,
    CreateIndexStmt,
    CreateTableStmt,
    DeleteStmt,
    DropIndexStmt,
    DropTableStmt,
    InsertStmt,
    Literal,
    MiniDBError,
    SelectStmt,
    UnaryOp,
    UpdateStmt,
    parse,
)
from .schema import Table
from .storage import Storage


@dataclass
class Result:
    """What execute() returns.  Always the same shape."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    rowcount: int = 0  # rows changed, or rows returned for SELECT
    message: str = ""


def _plain_name(name: str) -> str:
    """Strip a table prefix: "users.id" -> "id"."""
    return name.split(".")[-1]


def _is_true(value: object) -> bool:
    """SQL truthiness: NULL is false, 0 is false, else bool(value)."""
    if value is None:
        return False
    return bool(value)


class Engine:
    """The database engine: tables, rows, indexes, and the WAL."""

    def __init__(self, path: str = "data/mydata.minidb"):
        self.storage = Storage(path)
        self.tables: dict[str, Table] = {}
        self.rows: dict[str, list[dict]] = {}
        # index name -> HashIndex
        self.indexes: dict[str, HashIndex] = {}
        # table name -> next _rowid to assign
        self._next_rowid: dict[str, int] = {}
        self._load()

    # -- loading / replay -------------------------------------------

    def _load(self) -> None:
        """Replay snapshot + WAL records to rebuild memory state."""
        for record in self.storage.load():
            self._apply_record(record)

    def _apply_record(self, record: dict) -> None:
        """Apply one WAL/snapshot record in memory, no WAL write.

        Used only during load/replay.  The record is trusted: it
        was written by MiniDB itself and passed its checksum.
        """
        op = record.get("op")
        if op == "create_table":
            table = Table.from_dict(record["table"])
            self.tables[table.name] = table
            self.rows.setdefault(table.name, [])
            self._next_rowid.setdefault(table.name, 1)
        elif op == "drop_table":
            name = record["table"]
            self.tables.pop(name, None)
            self.rows.pop(name, None)
            self._next_rowid.pop(name, None)
            for idx_name in [
                n for n, ix in self.indexes.items() if ix.table == name
            ]:
                del self.indexes[idx_name]
        elif op == "create_index":
            idx = HashIndex.from_dict(record["index"])
            self.indexes[idx.name] = idx
            idx.rebuild(self.rows.get(idx.table, []))
        elif op == "drop_index":
            self.indexes.pop(record["name"], None)
        elif op == "insert":
            table = record["table"]
            row = dict(record["row"])
            self.rows.setdefault(table, []).append(row)
            rid = int(row.get("_rowid", 1))
            self._next_rowid[table] = max(self._next_rowid.get(table, 1), rid + 1)
            for ix in self.indexes.values():
                if ix.table == table:
                    ix.add(row.get(ix.column), rid)
        elif op == "update":
            table = record["table"]
            for new_row in record["rows"]:
                new_row = dict(new_row)
                rid = int(new_row["_rowid"])
                for i, old in enumerate(self.rows.get(table, [])):
                    if int(old["_rowid"]) == rid:
                        for ix in self.indexes.values():
                            if ix.table == table:
                                ix.remove(old.get(ix.column), rid)
                                ix.add(new_row.get(ix.column), rid)
                        self.rows[table][i] = new_row
                        break
        elif op == "delete":
            table = record["table"]
            doomed = set(int(r) for r in record["rowids"])
            kept: list[dict] = []
            for old in self.rows.get(table, []):
                if int(old["_rowid"]) in doomed:
                    for ix in self.indexes.values():
                        if ix.table == table:
                            ix.remove(old.get(ix.column), int(old["_rowid"]))
                else:
                    kept.append(old)
            self.rows[table] = kept
        else:
            # Unknown op: ignore it rather than crash on open.
            # A future version may add ops; old data must still load.
            return

    # -- public entry point ------------------------------------------

    def execute(self, sql: str) -> Result:
        """Parse and run one SQL statement.  Returns a Result."""
        stmt = parse(sql)
        if isinstance(stmt, CreateTableStmt):
            return self._create_table(stmt)
        if isinstance(stmt, DropTableStmt):
            return self._drop_table(stmt)
        if isinstance(stmt, CreateIndexStmt):
            return self._create_index(stmt)
        if isinstance(stmt, DropIndexStmt):
            return self._drop_index(stmt)
        if isinstance(stmt, InsertStmt):
            return self._insert(stmt)
        if isinstance(stmt, SelectStmt):
            return self._select(stmt)
        if isinstance(stmt, UpdateStmt):
            return self._update(stmt)
        if isinstance(stmt, DeleteStmt):
            return self._delete(stmt)
        raise MiniDBError(f"Unsupported statement: {type(stmt).__name__}")

    def execute_many(self, sql_script: str) -> list[Result]:
        """Run several statements separated by ';'.  For scripts."""
        results: list[Result] = []
        for part in sql_script.split(";"):
            if part.strip():
                results.append(self.execute(part))
        return results

    # -- helpers ------------------------------------------------------

    def _get_table(self, name: str) -> Table:
        table = self.tables.get(name)
        if table is None:
            # Try case-insensitive lookup, so "Users" finds "users".
            for key, value in self.tables.items():
                if key.lower() == name.lower():
                    return value
            raise MiniDBError(f"Table {name!r} does not exist.")
        return table

    def _table_rows(self, name: str) -> list[dict]:
        table = self._get_table(name)
        return self.rows.get(table.name, [])

    def _strip_row(self, row: dict) -> dict:
        """A row without the hidden _rowid, for output."""
        return {k: v for k, v in row.items() if k != "_rowid"}

    def _validate_row(
        self, table: Table, row: dict, exclude_rowid: int | None = None
    ) -> None:
        """Check types, NOT NULL and UNIQUE for one full row."""
        for col in table.columns:
            value = row.get(col.name)
            table.check_value_type(col, value)
            if col.not_null and value is None:
                raise MiniDBError(
                    f"Column {col.name!r} is NOT NULL, got NULL."
                )
        for col in table.columns:
            if not col.unique:
                continue
            value = row.get(col.name)
            if value is None:
                continue  # NULLs are never equal to each other
            for other in self.rows.get(table.name, []):
                if exclude_rowid is not None and int(other["_rowid"]) == exclude_rowid:
                    continue
                if other.get(col.name) == value:
                    raise MiniDBError(
                        f"UNIQUE constraint failed on {table.name}.{col.name}: "
                        f"value {value!r} already exists."
                    )

    def _next_id_for_pk(self, table: Table) -> int:
        """Next auto-increment value for an INTEGER PRIMARY KEY."""
        pk = table.primary_key
        if pk is None:
            return 1
        best = 0
        for row in self.rows.get(table.name, []):
            value = row.get(pk)
            if isinstance(value, int) and not isinstance(value, bool):
                best = max(best, value)
        return best + 1

    def _index_for(self, table: str, column: str) -> HashIndex | None:
        """The index on this table+column, if one exists."""
        for ix in self.indexes.values():
            if ix.table == table and ix.column.lower() == column.lower():
                return ix
        return None

    # -- DDL: CREATE / DROP ------------------------------------------

    def _create_table(self, stmt: CreateTableStmt) -> Result:
        if stmt.table in self.tables:
            raise MiniDBError(f"Table {stmt.table!r} already exists.")
        # Build Column objects first: Table() validates them.
        from .schema import Column

        columns = [
            Column(
                name=c["name"],
                type=c["type"],
                primary_key=c["primary_key"],
                not_null=c["not_null"],
                unique=c["unique"],
            )
            for c in stmt.columns
        ]
        table = Table(name=stmt.table, columns=columns)
        record = {"op": "create_table", "table": table.to_dict()}
        self.storage.append(record)
        self._apply_record(record)
        return Result(message=f"Table {stmt.table} created.", rowcount=0)

    def _drop_table(self, stmt: DropTableStmt) -> Result:
        if stmt.table not in self.tables:
            if stmt.if_exists:
                return Result(message=f"Table {stmt.table} does not exist.", rowcount=0)
            raise MiniDBError(f"Table {stmt.table!r} does not exist.")
        record = {"op": "drop_table", "table": stmt.table}
        self.storage.append(record)
        self._apply_record(record)
        return Result(message=f"Table {stmt.table} dropped.", rowcount=0)

    def _create_index(self, stmt: CreateIndexStmt) -> Result:
        if stmt.name in self.indexes:
            raise MiniDBError(f"Index {stmt.name!r} already exists.")
        table = self._get_table(stmt.table)
        if not table.has_column(stmt.column):
            raise MiniDBError(
                f"Table {table.name!r} has no column {stmt.column!r}."
            )
        if stmt.unique:
            seen: set[object] = set()
            for row in self.rows.get(table.name, []):
                value = row.get(stmt.column)
                if value is None:
                    continue
                if value in seen:
                    raise MiniDBError(
                        f"Cannot create UNIQUE index: duplicate value {value!r}."
                    )
                seen.add(value)
        record = {
            "op": "create_index",
            "index": {
                "name": stmt.name,
                "table": table.name,
                "column": stmt.column,
                "unique": stmt.unique,
            },
        }
        self.storage.append(record)
        self._apply_record(record)
        return Result(message=f"Index {stmt.name} created.", rowcount=0)

    def _drop_index(self, stmt: DropIndexStmt) -> Result:
        if stmt.name not in self.indexes:
            if stmt.if_exists:
                return Result(message=f"Index {stmt.name} does not exist.", rowcount=0)
            raise MiniDBError(f"Index {stmt.name!r} does not exist.")
        record = {"op": "drop_index", "name": stmt.name}
        self.storage.append(record)
        self._apply_record(record)
        return Result(message=f"Index {stmt.name} dropped.", rowcount=0)

    # -- INSERT ------------------------------------------------------

    def _insert(self, stmt: InsertStmt) -> Result:
        table = self._get_table(stmt.table)
        if stmt.columns is not None:
            for name in stmt.columns:
                if not table.has_column(name):
                    raise MiniDBError(
                        f"Table {table.name!r} has no column {name!r}."
                    )
        # Build and validate ALL rows before writing anything.
        # That way a bad second row does not leave a half-done INSERT.
        prepared: list[dict] = []
        for values in stmt.rows:
            expected = len(stmt.columns) if stmt.columns else len(table.columns)
            if len(values) != expected:
                raise MiniDBError(
                    f"INSERT into {table.name}: expected {expected} values, "
                    f"got {len(values)}."
                )
            row: dict = {c.name: None for c in table.columns}
            if stmt.columns is None:
                for col, expr in zip(table.columns, values):
                    row[col.name] = self._eval(expr, {})
            else:
                for col_name, expr in zip(stmt.columns, values):
                    row[col_name] = self._eval(expr, {})
            # Auto-increment: INTEGER PRIMARY KEY left as NULL.
            pk = table.primary_key
            if pk is not None:
                pk_col = table.column(pk)
                if pk_col.type == "INTEGER" and row.get(pk) is None:
                    row[pk] = self._next_id_for_pk(table)
                    # If we are inserting several rows at once, the
                    # next id must keep growing inside this batch.
                    # _next_id_for_pk looks at stored rows only, so
                    # bump it by counting prepared rows with a pk.
                    # Simpler: recompute from prepared rows too.
                    for done in prepared:
                        if isinstance(done.get(pk), int):
                            row[pk] = max(row[pk], done[pk] + 1)
            self._validate_row(table, row)
            # UNIQUE check must also see rows prepared in this batch.
            for col in table.columns:
                if not col.unique:
                    continue
                value = row.get(col.name)
                if value is None:
                    continue
                for done in prepared:
                    if done.get(col.name) == value:
                        raise MiniDBError(
                            f"UNIQUE constraint failed on "
                            f"{table.name}.{col.name}: {value!r}."
                        )
            prepared.append(row)
        # All rows are valid: write them to the WAL, then to memory.
        count = 0
        for row in prepared:
            row["_rowid"] = self._next_rowid.get(table.name, 1)
            self._next_rowid[table.name] = int(row["_rowid"]) + 1
            record = {"op": "insert", "table": table.name, "row": dict(row)}
            self.storage.append(record)
            self._apply_record(record)
            count += 1
        return Result(
            message=f"{count} row(s) inserted into {table.name}.",
            rowcount=count,
        )

    # -- SELECT ------------------------------------------------------

    def _select(self, stmt: SelectStmt) -> Result:
        table = self._get_table(stmt.table)
        all_rows = self._table_rows(table.name)

        # Fast path: WHERE is exactly "indexed_col = literal".
        # Use the hash index instead of scanning every row.
        candidates = self._indexed_candidates(stmt, table, all_rows)

        filtered: list[dict] = []
        for row in candidates:
            if stmt.where is None or _is_true(self._eval(stmt.where, row)):
                filtered.append(row)

        # Aggregates: COUNT(*), SUM(col), AVG(col), MIN(col), MAX(col).
        # In v1 an aggregate query has only aggregates, no GROUP BY.
        has_agg = any(
            isinstance(item.expr, Aggregate) for item in stmt.columns
        )
        if has_agg:
            if any(
                not isinstance(item.expr, Aggregate)
                for item in stmt.columns
            ):
                raise MiniDBError(
                    "Cannot mix aggregate functions with plain columns "
                    "in v1 (no GROUP BY yet)."
                )
            out_row: dict = {}
            for item in stmt.columns:
                agg = item.expr
                assert isinstance(agg, Aggregate)
                out_row[self._agg_label(agg)] = self._compute_agg(agg, filtered)
            return Result(
                columns=list(out_row.keys()),
                rows=[out_row],
                rowcount=1,
                message="1 row.",
            )

        # ORDER BY, on the full rows (before projection).
        ordered = list(filtered)
        for col_name, direction in reversed(stmt.order_by):
            plain = _plain_name(col_name)
            if not table.has_column(plain):
                raise MiniDBError(
                    f"Table {table.name!r} has no column {plain!r}."
                )
            ordered.sort(
                key=lambda r, c=plain: (r.get(c) is None, r.get(c)),
                reverse=(direction == "DESC"),
            )

        # LIMIT / OFFSET.
        start = stmt.offset or 0
        end = None if stmt.limit is None else start + stmt.limit
        sliced = ordered[start:end]

        # Projection.
        columns: list[str] = []
        out_rows: list[dict] = []
        is_star = (
            len(stmt.columns) == 1
            and isinstance(stmt.columns[0].expr, ColumnRef)
            and stmt.columns[0].expr.name == "*"
        )
        if is_star:
            columns = table.column_names()
            for row in sliced:
                out_rows.append(
                    {name: row.get(name) for name in columns}
                )
        else:
            for item in stmt.columns:
                if not isinstance(item.expr, ColumnRef):
                    raise MiniDBError("Only columns, * and aggregates are allowed in SELECT.")
                plain = _plain_name(item.expr.name)
                if not table.has_column(plain):
                    raise MiniDBError(
                        f"Table {table.name!r} has no column {plain!r}."
                    )
                columns.append(plain)
            for row in sliced:
                out_rows.append(
                    {name: row.get(name) for name in columns}
                )
        return Result(columns=columns, rows=out_rows, rowcount=len(out_rows))

    def _indexed_candidates(
        self, stmt: SelectStmt, table: Table, all_rows: list[dict]
    ) -> list[dict]:
        """Return index-hit rows if WHERE is "col = literal", else all."""
        where = stmt.where
        if not isinstance(where, BinaryOp) or where.op != "=":
            return all_rows
        # One side must be a column, the other a literal value.
        col_name: str | None = None
        value: object = None
        if isinstance(where.left, ColumnRef) and isinstance(where.right, Literal):
            col_name, value = where.left.name, where.right.value
        elif isinstance(where.right, ColumnRef) and isinstance(where.left, Literal):
            col_name, value = where.right.name, where.left.value
        else:
            return all_rows
        plain = _plain_name(col_name)
        index = self._index_for(table.name, plain)
        if index is None:
            return all_rows
        wanted = set(index.lookup(value))
        return [r for r in all_rows if int(r["_rowid"]) in wanted]

    @staticmethod
    def _agg_label(agg: Aggregate) -> str:
        if agg.is_star:
            return f"{agg.func}(*)"
        return f"{agg.func}({_plain_name(agg.arg.name)})"  # type: ignore[union-attr]

    def _compute_agg(self, agg: Aggregate, rows: list[dict]) -> object:
        if agg.is_star:
            return len(rows)  # COUNT(*)
        col = _plain_name(agg.arg.name)  # type: ignore[union-attr]
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if agg.func == "COUNT":
            return len(values)
        if not values:
            return None
        if agg.func == "SUM":
            return sum(values)  # type: ignore[arg-type]
        if agg.func == "AVG":
            return sum(values) / len(values)  # type: ignore[arg-type]
        if agg.func == "MIN":
            return min(values)  # type: ignore[type-var]
        if agg.func == "MAX":
            return max(values)  # type: ignore[type-var]
        raise MiniDBError(f"Unknown aggregate {agg.func}.")

    # -- UPDATE / DELETE ----------------------------------------------

    def _update(self, stmt: UpdateStmt) -> Result:
        table = self._get_table(stmt.table)
        for col_name, _ in stmt.assignments:
            if not table.has_column(col_name):
                raise MiniDBError(
                    f"Table {table.name!r} has no column {col_name!r}."
                )
        targets = [
            r for r in self._table_rows(table.name)
            if stmt.where is None or _is_true(self._eval(stmt.where, r))
        ]
        # Build all new rows first, validate them, then write.
        new_rows: list[dict] = []
        for old in targets:
            new = dict(old)
            for col_name, expr in stmt.assignments:
                new[col_name] = self._eval(expr, old)
            # An INTEGER PRIMARY KEY that becomes NULL is an error;
            # auto-increment only happens on INSERT, not UPDATE.
            self._validate_row(table, new, exclude_rowid=int(old["_rowid"]))
            new_rows.append(new)
        for new in new_rows:
            record = {"op": "update", "table": table.name, "rows": [dict(new)]}
            self.storage.append(record)
            self._apply_record(record)
        return Result(
            message=f"{len(new_rows)} row(s) updated in {table.name}.",
            rowcount=len(new_rows),
        )

    def _delete(self, stmt: DeleteStmt) -> Result:
        table = self._get_table(stmt.table)
        targets = [
            r for r in self._table_rows(table.name)
            if stmt.where is None or _is_true(self._eval(stmt.where, r))
        ]
        if not targets:
            return Result(message="0 row(s) deleted.", rowcount=0)
        record = {
            "op": "delete",
            "table": table.name,
            "rowids": [int(r["_rowid"]) for r in targets],
        }
        self.storage.append(record)
        self._apply_record(record)
        return Result(
            message=f"{len(targets)} row(s) deleted from {table.name}.",
            rowcount=len(targets),
        )

    # -- expression evaluation -----------------------------------------

    def _eval(self, expr: object, row: dict) -> object:
        """Evaluate an expression against one row.  Returns a value."""
        if isinstance(expr, Literal):
            return expr.value
        if isinstance(expr, ColumnRef):
            if expr.name == "*":
                raise MiniDBError("'*' is not a value.")
            plain = _plain_name(expr.name)
            if plain not in row:
                raise MiniDBError(f"Unknown column {expr.name!r}.")
            return row.get(plain)
        if isinstance(expr, UnaryOp):
            value = self._eval(expr.operand, row)
            if expr.op == "NOT":
                return not _is_true(value)
            if expr.op == "-":
                if value is None:
                    return None
                return -value  # type: ignore[operator]
            raise MiniDBError(f"Unknown operator {expr.op!r}.")
        if isinstance(expr, BinaryOp):
            if expr.op == "AND":
                return _is_true(self._eval(expr.left, row)) and _is_true(
                    self._eval(expr.right, row)
                )
            if expr.op == "OR":
                return _is_true(self._eval(expr.left, row)) or _is_true(
                    self._eval(expr.right, row)
                )
            left = self._eval(expr.left, row)
            right = self._eval(expr.right, row)
            return self._compare(expr.op, left, right)
        raise MiniDBError(f"Cannot evaluate {expr!r}.")

    @staticmethod
    def _compare(op: str, left: object, right: object) -> bool:
        """Compare two values the SQL way (NULL-aware)."""
        if op == "=":
            if left is None or right is None:
                return left is None and right is None
            return left == right
        if op == "!=":
            if left is None or right is None:
                return not (left is None and right is None)
            return left != right
        # <, >, <=, >= with NULL is always false in SQL.
        if left is None or right is None:
            return False
        try:
            if op == "<":
                return left < right  # type: ignore[operator]
            if op == ">":
                return left > right  # type: ignore[operator]
            if op == "<=":
                return left <= right  # type: ignore[operator]
            if op == ">=":
                return left >= right  # type: ignore[operator]
        except TypeError:
            # Comparing text with a number, for example: false,
            # not a crash.  The type checker should have caught
            # most of these on INSERT.
            return False
        raise MiniDBError(f"Unknown comparison {op!r}.")

    # -- checkpoint / close ---------------------------------------------

    def checkpoint(self) -> None:
        """Write a compact snapshot and clear the WAL.

        Snapshot records are the same "create_table / insert /
        create_index" records replay understands, so loading a
        snapshot is exactly the same code path as replaying a WAL.
        """
        records: list[dict] = []
        for table in self.tables.values():
            records.append({"op": "create_table", "table": table.to_dict()})
            for row in self.rows.get(table.name, []):
                records.append(
                    {"op": "insert", "table": table.name, "row": dict(row)}
                )
        for idx in self.indexes.values():
            records.append({"op": "create_index", "index": idx.to_dict()})
        self.storage.checkpoint(records)

    def close(self) -> None:
        """Checkpoint (so the next open is fast) and close."""
        try:
            self.checkpoint()
        finally:
            self.storage.close()

    # -- helpers for the API / REPL --------------------------------------

    def table_names(self) -> list[str]:
        return list(self.tables.keys())

    def index_names(self) -> list[str]:
        return list(self.indexes.keys())

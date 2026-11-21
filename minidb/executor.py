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

import copy
from dataclasses import dataclass, field

from .btree import BTreeIndex
from .index import HashIndex
from .parser import (
    Aggregate,
    AlterTableStmt,
    BeginStmt,
    BinaryOp,
    ColumnRef,
    CommitStmt,
    CreateIndexStmt,
    CreateTableStmt,
    DeleteStmt,
    DropIndexStmt,
    DropTableStmt,
    InsertStmt,
    Literal,
    MiniDBError,
    RollbackStmt,
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
        # Transaction state (v2): while a transaction is open,
        # records are kept in self._txn_records and applied in
        # memory, but NOT written to the WAL.  COMMIT writes
        # them to the WAL in order; ROLLBACK (or a crash) drops
        # them, so they never reach the file.
        self._txn_active = False
        self._txn_records: list[dict] = []
        self._txn_snapshot = None
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
            data = record["index"]
            if data.get("kind") == "btree":
                idx = BTreeIndex.from_dict(data)
            else:
                idx = HashIndex.from_dict(data)
            self.indexes[idx.name] = idx
            idx.rebuild(self.rows.get(idx.table, []))
        elif op == "alter_table_add_column":
            table = self.tables[record["table"]]
            from .schema import Column

            col = Column.from_dict(record["column"])
            table.columns.append(col)
            default = record.get("default")
            has_default = bool(record.get("has_default", False))
            for row in self.rows.get(table.name, []):
                row[col.name] = default if has_default else None
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

    # -- writes + transactions (v2) -----------------------------------

    def _write_record(self, record: dict) -> None:
        """Write one change: to the WAL now, or buffer it in a txn.

        Outside a transaction this is the old behaviour: append
        to the WAL (fsync) then apply in memory.  Inside a
        transaction the record is applied in memory straight
        away (so SELECT inside the transaction sees it) but
        only appended to the pending list -- the WAL does not
        see it until COMMIT.
        """
        if self._txn_active:
            self._txn_records.append(record)
            self._apply_record(record)
        else:
            self.storage.append(record)
            self._apply_record(record)

    def _begin(self) -> Result:
        if self._txn_active:
            raise MiniDBError("A transaction is already open.")
        self._txn_active = True
        self._txn_records = []
        # Snapshot everything, so ROLLBACK can restore it exactly.
        self._txn_snapshot = (
            copy.deepcopy(self.tables),
            copy.deepcopy(self.rows),
            copy.deepcopy(self.indexes),
            copy.deepcopy(self._next_rowid),
        )
        return Result(message="Transaction started.", rowcount=0)

    def _commit(self) -> Result:
        if not self._txn_active:
            raise MiniDBError("No transaction is open.")
        records = self._txn_records
        self._txn_active = False
        self._txn_records = []
        self._txn_snapshot = None
        for record in records:
            self.storage.append(record)
        return Result(
            message=f"Transaction committed ({len(records)} change(s)).",
            rowcount=len(records),
        )

    def _rollback(self) -> Result:
        if not self._txn_active:
            raise MiniDBError("No transaction is open.")
        tables, rows, indexes, next_rowid = self._txn_snapshot
        self.tables = tables
        self.rows = rows
        self.indexes = indexes
        self._next_rowid = next_rowid
        self._txn_active = False
        self._txn_records = []
        self._txn_snapshot = None
        return Result(message="Transaction rolled back.", rowcount=0)

    # -- public entry point ------------------------------------------

    def execute(self, sql: str) -> Result:
        """Parse and run one SQL statement.  Returns a Result."""
        stmt = parse(sql)
        if isinstance(stmt, BeginStmt):
            return self._begin()
        if isinstance(stmt, CommitStmt):
            return self._commit()
        if isinstance(stmt, RollbackStmt):
            return self._rollback()
        if isinstance(stmt, AlterTableStmt):
            return self._alter_table(stmt)
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

    def _index_for(self, table: str, column: str):
        """The index on this table+column, if one exists."""
        for ix in self.indexes.values():
            if ix.table == table and ix.column.lower() == column.lower():
                return ix
        return None

    def _btree_for(self, table: str, column: str) -> BTreeIndex | None:
        """The B-tree index on this table+column, if one exists."""
        ix = self._index_for(table, column)
        return ix if isinstance(ix, BTreeIndex) else None

    def _btree_range_rowids(self, where: object, table: str) -> set[int] | None:
        """Rowids from a B-tree range/equality, or None if not usable.

        Handles ``col > 5``, ``col <= 10``, ``col = 7`` and an AND
        of two such conditions on the same column (the shape
        BETWEEN expands into).  Returns None when the WHERE
        does not use a B-tree at all, so the caller falls back
        to a full scan.
        """
        if isinstance(where, BinaryOp) and where.op == "AND":
            left = self._btree_range_rowids(where.left, table)
            right = self._btree_range_rowids(where.right, table)
            if left is None:
                return right
            if right is None:
                return left
            return left & right
        if not isinstance(where, BinaryOp):
            return None
        if where.op not in ("=", "!=", "<", ">", "<=", ">="):
            return None
        col_name: str | None = None
        value: object = None
        if isinstance(where.left, ColumnRef) and isinstance(where.right, Literal):
            col_name, value = where.left.name, where.right.value
        elif isinstance(where.right, ColumnRef) and isinstance(where.left, Literal):
            col_name, value = where.right.name, where.left.value
        else:
            return None
        plain = _plain_name(col_name)
        btree = self._btree_for(table, plain)
        if btree is None:
            return None
        if where.op == "=":
            return set(btree.lookup(value))
        if where.op == "!=":
            return None  # != is a scan, not a range
        if value is None:
            return set()
        if where.op == ">":
            return set(btree.range(low=value, low_inclusive=False))
        if where.op == ">=":
            return set(btree.range(low=value, low_inclusive=True))
        if where.op == "<":
            return set(btree.range(high=value, high_inclusive=False))
        if where.op == "<=":
            return set(btree.range(high=value, high_inclusive=True))
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
                default=c.get("default"),
                has_default=bool(c.get("has_default", False)),
            )
            for c in stmt.columns
        ]
        table = Table(name=stmt.table, columns=columns)
        record = {"op": "create_table", "table": table.to_dict()}
        self._write_record(record)
        return Result(message=f"Table {stmt.table} created.", rowcount=0)

    def _alter_table(self, stmt: AlterTableStmt) -> Result:
        """ALTER TABLE t ADD COLUMN c TYPE [DEFAULT x]."""
        from .schema import Column

        table = self._get_table(stmt.table)
        col_data = stmt.column
        if table.has_column(col_data["name"]):
            raise MiniDBError(
                f"Table {table.name!r} already has column "
                f"{col_data['name']!r}."
            )
        col = Column(
            name=col_data["name"],
            type=col_data["type"],
            primary_key=False,
            not_null=col_data["not_null"],
            unique=col_data["unique"],
            default=col_data.get("default"),
            has_default=bool(col_data.get("has_default", False)),
        )
        if col.not_null and not col.has_default:
            raise MiniDBError(
                "Cannot ADD a NOT NULL column without a DEFAULT: "
                "existing rows would have no value."
            )
        if col.unique:
            default = col.default if col.has_default else None
            if default is not None:
                raise MiniDBError(
                    "Cannot ADD a UNIQUE column with a non-NULL "
                    "DEFAULT: every existing row would share it."
                )
        record = {
            "op": "alter_table_add_column",
            "table": table.name,
            "column": col.to_dict(),
            "default": col.default,
            "has_default": col.has_default,
        }
        self._write_record(record)
        return Result(
            message=f"Column {col.name} added to {table.name}.",
            rowcount=0,
        )

    def _drop_table(self, stmt: DropTableStmt) -> Result:
        if stmt.table not in self.tables:
            if stmt.if_exists:
                return Result(message=f"Table {stmt.table} does not exist.", rowcount=0)
            raise MiniDBError(f"Table {stmt.table!r} does not exist.")
        record = {"op": "drop_table", "table": stmt.table}
        self._write_record(record)
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
                "kind": stmt.index_type,
            },
        }
        self._write_record(record)
        return Result(message=f"Index {stmt.name} created.", rowcount=0)

    def _drop_index(self, stmt: DropIndexStmt) -> Result:
        if stmt.name not in self.indexes:
            if stmt.if_exists:
                return Result(message=f"Index {stmt.name} does not exist.", rowcount=0)
            raise MiniDBError(f"Index {stmt.name!r} does not exist.")
        record = {"op": "drop_index", "name": stmt.name}
        self._write_record(record)
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
            # Columns with a DEFAULT get it when they are omitted.
            if stmt.columns is not None:
                for c in table.columns:
                    if c.has_default and c.name not in stmt.columns:
                        row[c.name] = c.default
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
            self._write_record(record)
            count += 1
        return Result(
            message=f"{count} row(s) inserted into {table.name}.",
            rowcount=count,
        )

    # -- SELECT ------------------------------------------------------

    # -- SELECT (v2 dispatch) -----------------------------------------

    def _select(self, stmt: SelectStmt) -> Result:
        """SELECT entry point (v2): JOIN / GROUP BY / DISTINCT first."""
        if stmt.join_table is not None:
            return self._select_join(stmt)
        if stmt.group_by:
            return self._select_group(stmt)
        if stmt.distinct:
            return self._select_distinct(stmt)
        return self._select_single(stmt)

    def _row_value(self, row: dict, name: str) -> object:
        """Value of a column in a row (plain or qualified, e.g. users.id)."""
        if name in row:
            return row.get(name)
        plain = _plain_name(name)
        if plain in row:
            return row.get(plain)
        for key, value in row.items():
            if key.endswith("." + plain):
                return value
        raise MiniDBError(f"Unknown column {name!r}.")

    def _eval_having(self, expr: object, group_rows: list[dict],
                     key_values: dict) -> object:
        """Evaluate a HAVING expression over one group.

        ColumnRefs read the GROUP BY key; Aggregates are computed
        over the group's rows; everything else works like _eval.
        """
        if isinstance(expr, Aggregate):
            return self._compute_agg(expr, group_rows)
        if isinstance(expr, ColumnRef):
            plain = _plain_name(expr.name)
            if plain in key_values:
                return key_values[plain]
            raise MiniDBError(
                f"HAVING column {expr.name!r} is not a GROUP BY column "
                "and is not an aggregate."
            )
        if isinstance(expr, Literal):
            return expr.value
        if isinstance(expr, UnaryOp):
            value = self._eval_having(expr.operand, group_rows, key_values)
            return not _is_true(value) if expr.op == "NOT" else -value  # type: ignore[operator]
        if isinstance(expr, BinaryOp):
            if expr.op == "AND":
                return _is_true(self._eval_having(expr.left, group_rows, key_values)) and _is_true(
                    self._eval_having(expr.right, group_rows, key_values)
                )
            if expr.op == "OR":
                return _is_true(self._eval_having(expr.left, group_rows, key_values)) or _is_true(
                    self._eval_having(expr.right, group_rows, key_values)
                )
            left = self._eval_having(expr.left, group_rows, key_values)
            right = self._eval_having(expr.right, group_rows, key_values)
            return self._compare(expr.op, left, right)
        raise MiniDBError(f"Cannot evaluate HAVING expression {expr!r}.")

    def _select_join(self, stmt: SelectStmt) -> Result:
        """One INNER JOIN: two tables, ON equality, nested loops."""
        left_table = self._get_table(stmt.table)
        right_table = self._get_table(stmt.join_table)  # type: ignore[arg-type]
        left_rows = self._table_rows(left_table.name)
        right_rows = self._table_rows(right_table.name)
        combined: list[dict] = []
        for left in left_rows:
            for right in right_rows:
                merged: dict = {"_rowid": left["_rowid"]}
                for key, value in left.items():
                    if key != "_rowid":
                        merged[f"{left_table.name}.{key}"] = value
                for key, value in right.items():
                    if key != "_rowid":
                        merged[f"{right_table.name}.{key}"] = value
                if stmt.join_on is None or _is_true(
                    self._eval(stmt.join_on, merged)
                ):
                    if stmt.where is None or _is_true(
                        self._eval(stmt.where, merged)
                    ):
                        combined.append(merged)
        return self._finish_select(stmt, combined, left_table, right_table)

    def _select_group(self, stmt: SelectStmt) -> Result:
        """GROUP BY with aggregates, plus HAVING."""
        table = self._get_table(stmt.table)
        all_rows = self._table_rows(table.name)
        filtered = [
            row for row in all_rows
            if stmt.where is None or _is_true(self._eval(stmt.where, row))
        ]
        for name in stmt.group_by:
            if not table.has_column(_plain_name(name)):
                raise MiniDBError(
                    f"Table {table.name!r} has no column {name!r}."
                )
        groups: dict[tuple, list[dict]] = {}
        order: list[tuple] = []
        for row in filtered:
            key = tuple(row.get(_plain_name(name)) for name in stmt.group_by)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        out_rows: list[dict] = []
        columns: list[str] = []
        for item in stmt.columns:
            if isinstance(item.expr, Aggregate):
                columns.append(self._agg_label(item.expr))
            elif isinstance(item.expr, ColumnRef):
                columns.append(_plain_name(item.expr.name))
            else:
                raise MiniDBError("GROUP BY SELECT needs columns or aggregates.")
        for key in order:
            grows = groups[key]
            key_values = {
                _plain_name(name): value
                for name, value in zip(stmt.group_by, key)
            }
            if stmt.having is not None and not _is_true(
                self._eval_having(stmt.having, grows, key_values)
            ):
                continue
            out_row: dict = {}
            for item in stmt.columns:
                if isinstance(item.expr, Aggregate):
                    out_row[self._agg_label(item.expr)] = self._compute_agg(
                        item.expr, grows
                    )
                else:
                    plain = _plain_name(item.expr.name)  # type: ignore[union-attr]
                    if plain not in key_values:
                        raise MiniDBError(
                            f"Column {plain!r} must be in GROUP BY or "
                            "inside an aggregate function."
                        )
                    out_row[plain] = key_values[plain]
            out_rows.append(out_row)
        # ORDER BY / LIMIT / OFFSET on the grouped rows.
        for col_name, direction in reversed(stmt.order_by):
            plain = _plain_name(col_name)
            if plain not in columns:
                raise MiniDBError(f"No such output column {plain!r}.")
            out_rows.sort(
                key=lambda r, c=plain: (r.get(c) is None, r.get(c)),
                reverse=(direction == "DESC"),
            )
        start = stmt.offset or 0
        end = None if stmt.limit is None else start + stmt.limit
        sliced = out_rows[start:end]
        return Result(columns=columns, rows=sliced, rowcount=len(sliced))

    def _select_distinct(self, stmt: SelectStmt) -> Result:
        """SELECT DISTINCT: run the plain query, then dedupe rows."""
        import dataclasses

        plain_stmt = dataclasses.replace(stmt, distinct=False, limit=None, offset=None)
        base = self._select_single(plain_stmt)
        seen: set[tuple] = set()
        out_rows: list[dict] = []
        for row in base.rows:
            key = tuple(row.get(c) for c in base.columns)
            if key not in seen:
                seen.add(key)
                out_rows.append(row)
        start = stmt.offset or 0
        end = None if stmt.limit is None else start + stmt.limit
        sliced = out_rows[start:end]
        return Result(columns=base.columns, rows=sliced, rowcount=len(sliced))

    def _finish_select(
        self, stmt: SelectStmt, combined: list[dict],
        left_table: Table, right_table: Table,
    ) -> Result:
        """Shared tail for JOIN SELECT: aggregates, ORDER BY, LIMIT, projection."""
        has_agg = any(isinstance(item.expr, Aggregate) for item in stmt.columns)
        if has_agg:
            if any(not isinstance(item.expr, Aggregate) for item in stmt.columns):
                raise MiniDBError(
                    "Cannot mix aggregate functions with plain columns."
                )
            out_row: dict = {}
            for item in stmt.columns:
                agg = item.expr
                assert isinstance(agg, Aggregate)
                if agg.is_star:
                    out_row[self._agg_label(agg)] = len(combined)
                else:
                    col = _plain_name(agg.arg.name)  # type: ignore[union-attr]
                    values = [
                        self._row_value(r, col) for r in combined
                        if self._row_value(r, col) is not None
                    ]
                    if agg.func == "COUNT":
                        out_row[self._agg_label(agg)] = len(values)
                    elif not values:
                        out_row[self._agg_label(agg)] = None
                    elif agg.func == "SUM":
                        out_row[self._agg_label(agg)] = sum(values)  # type: ignore[arg-type]
                    elif agg.func == "AVG":
                        out_row[self._agg_label(agg)] = sum(values) / len(values)  # type: ignore[arg-type]
                    elif agg.func == "MIN":
                        out_row[self._agg_label(agg)] = min(values)  # type: ignore[type-var]
                    elif agg.func == "MAX":
                        out_row[self._agg_label(agg)] = max(values)  # type: ignore[type-var]
            return Result(columns=list(out_row.keys()), rows=[out_row], rowcount=1)
        ordered = list(combined)
        for col_name, direction in reversed(stmt.order_by):
            ordered.sort(
                key=lambda r, c=col_name: (
                    self._row_value(r, c) is None, self._row_value(r, c)
                ),
                reverse=(direction == "DESC"),
            )
        start = stmt.offset or 0
        end = None if stmt.limit is None else start + stmt.limit
        sliced = ordered[start:end]
        is_star = (
            len(stmt.columns) == 1
            and isinstance(stmt.columns[0].expr, ColumnRef)
            and stmt.columns[0].expr.name == "*"
        )
        if is_star:
            columns = (
                [f"{left_table.name}.{c}" for c in left_table.column_names()]
                + [f"{right_table.name}.{c}" for c in right_table.column_names()]
            )
            out_rows = [{name: row.get(name) for name in columns} for row in sliced]
            return Result(columns=columns, rows=out_rows, rowcount=len(out_rows))
        columns = []
        for item in stmt.columns:
            if not isinstance(item.expr, ColumnRef):
                raise MiniDBError("Only columns, * and aggregates are allowed in SELECT.")
            columns.append(_plain_name(item.expr.name))
        out_rows = []
        for row in sliced:
            out_rows.append(
                {name: self._row_value(row, name) for name in columns}
            )
        return Result(columns=columns, rows=out_rows, rowcount=len(out_rows))

    def _select_single(self, stmt: SelectStmt) -> Result:
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
        # B-tree fast path: one ORDER BY column with a B-tree index
        # is already in order -- read it from the tree, no sort.
        ordered = list(filtered)
        order_done = False
        if len(stmt.order_by) == 1:
            plain = _plain_name(stmt.order_by[0][0])
            btree = self._btree_for(table.name, plain)
            if btree is not None and not any(
                isinstance(item.expr, Aggregate) for item in stmt.columns
            ):
                direction = stmt.order_by[0][1]
                by_rowid = {
                    int(r["_rowid"]): r for r in filtered
                }
                ordered = [
                    by_rowid[rid]
                    for rid in btree.sorted_rowids(
                        descending=(direction == "DESC")
                    )
                    if rid in by_rowid
                ]
                # Rows whose order column is NULL are not in the
                # tree; they go last for ASC, first for DESC.
                nulls = [r for r in filtered if r.get(plain) is None]
                ordered = (ordered + nulls) if direction == "ASC" else (nulls + ordered)
                order_done = True
        for col_name, direction in reversed(stmt.order_by):
            if order_done:
                # Already in B-tree order; still validate the column.
                if not table.has_column(_plain_name(col_name)):
                    raise MiniDBError(
                        f"Table {table.name!r} has no column {col_name!r}."
                    )
                continue
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
        """Return index-hit rows if WHERE uses an index, else all rows.

        Hash index: WHERE col = literal.  B-tree index: range
        WHEREs (>, <, >=, <=, BETWEEN) -- see _btree_range_rowids.
        """
        where = stmt.where
        if where is not None:
            wanted = self._btree_range_rowids(where, table.name)
            if wanted is not None:
                return [r for r in all_rows if int(r["_rowid"]) in wanted]
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
            self._write_record(record)
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
        self._write_record(record)
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
            # Joined rows use qualified keys ("users.id"); plain
            # rows use plain keys ("id").  Try the exact name
            # first, then the plain name, then any qualified key
            # ending in ".name" (left table wins).
            if expr.name in row:
                return row.get(expr.name)
            plain = _plain_name(expr.name)
            if plain in row:
                return row.get(plain)
            for key, value in row.items():
                if key.endswith("." + plain):
                    return value
            raise MiniDBError(f"Unknown column {expr.name!r}.")
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
        if self._txn_active:
            # A snapshot must never contain uncommitted rows:
            # roll the transaction back first, then checkpoint.
            self._rollback()
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

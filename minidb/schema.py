"""Schema: Table and Column definitions for MiniDB.

This file only describes the *shape* of a table. It does not read
or write any file and it does not run any SQL. Keeping the shape
separate makes the rest of the code easier to read and to test.

Types in v1
----------
INTEGER  -- whole numbers, e.g. 42.  An INTEGER PRIMARY KEY
            auto-increments: if you do not give a value, MiniDB
            gives the next number (max + 1).
TEXT     -- text, e.g. 'Abir'.  Stored as a Python str.
REAL     -- decimal numbers, e.g. 3.14.  Stored as a Python float.
BLOB     -- raw bytes.  In v1 we accept str or bytes and store
            them as-is; real file/blob storage is a v2 feature.

Constraints in v1
-----------------
PRIMARY KEY  -- one column per table, values must be unique and
                never NULL.  Only one PRIMARY KEY per table.
NOT NULL     -- the value may never be NULL.
UNIQUE       -- values in this column must all be different.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parser import MiniDBError


# The four types MiniDB v1 understands.  Anything else is an error.
VALID_TYPES = {"INTEGER", "TEXT", "REAL", "BLOB"}


@dataclass
class Column:
    """One column of a table, e.g. ``id INTEGER PRIMARY KEY``."""

    name: str
    type: str  # one of VALID_TYPES, always stored upper-case
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False

    def __post_init__(self) -> None:
        # Normalise the type so "integer", "INTEGER" and "Integer"
        # all mean the same thing.
        self.type = self.type.upper()
        if self.type not in VALID_TYPES:
            raise MiniDBError(
                f"Unknown type {self.type!r} for column {self.name!r}. "
                f"Valid types: {sorted(VALID_TYPES)}"
            )
        # A primary key is always NOT NULL and UNIQUE, even if the
        # user did not write those words.
        if self.primary_key:
            self.not_null = True
            self.unique = True

    def to_dict(self) -> dict:
        """Turn this column into plain data for the WAL/snapshot."""
        return {
            "name": self.name,
            "type": self.type,
            "primary_key": self.primary_key,
            "not_null": self.not_null,
            "unique": self.unique,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Column":
        """Rebuild a Column from data produced by to_dict()."""
        return cls(
            name=data["name"],
            type=data["type"],
            primary_key=bool(data.get("primary_key", False)),
            not_null=bool(data.get("not_null", False)),
            unique=bool(data.get("unique", False)),
        )


@dataclass
class Table:
    """A table: a name plus an ordered list of columns."""

    name: str
    columns: list[Column] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.columns:
            raise MiniDBError(f"Table {self.name!r} needs at least one column.")
        # Column names must be unique inside one table.
        seen: set[str] = set()
        for col in self.columns:
            lowered = col.name.lower()
            if lowered in seen:
                raise MiniDBError(
                    f"Duplicate column {col.name!r} in table {self.name!r}."
                )
            seen.add(lowered)
        # At most one PRIMARY KEY column.
        pks = [c for c in self.columns if c.primary_key]
        if len(pks) > 1:
            raise MiniDBError(
                f"Table {self.name!r} has more than one PRIMARY KEY column."
            )

    @property
    def primary_key(self) -> str | None:
        """Name of the PRIMARY KEY column, or None if there is none."""
        for col in self.columns:
            if col.primary_key:
                return col.name
        return None

    def column(self, name: str) -> Column:
        """Find a column by name (case-insensitive)."""
        for col in self.columns:
            if col.name.lower() == name.lower():
                return col
        raise MiniDBError(f"Table {self.name!r} has no column {name!r}.")

    def has_column(self, name: str) -> bool:
        """True if this table has a column with this name."""
        return any(c.name.lower() == name.lower() for c in self.columns)

    def column_names(self) -> list[str]:
        """Column names in the order they were declared."""
        return [c.name for c in self.columns]

    def to_dict(self) -> dict:
        """Turn this table into plain data for the WAL/snapshot."""
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Table":
        """Rebuild a Table from data produced by to_dict()."""
        return cls(
            name=data["name"],
            columns=[Column.from_dict(c) for c in data["columns"]],
        )

    def check_value_type(self, column: Column, value: object) -> None:
        """Check one value against one column's type.

        NULL (None) is allowed here -- the NOT NULL rule is checked
        separately, because an auto-increment PRIMARY KEY may start
        as NULL and be filled in later.
        """
        if value is None:
            return
        if column.type == "INTEGER":
            # bool is a subclass of int in Python, but True is not
            # an integer for a database, so reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise MiniDBError(
                    f"Column {column.name!r} is INTEGER, "
                    f"got {value!r} ({type(value).__name__})."
                )
        elif column.type == "REAL":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MiniDBError(
                    f"Column {column.name!r} is REAL, "
                    f"got {value!r} ({type(value).__name__})."
                )
        elif column.type == "TEXT":
            if not isinstance(value, str):
                raise MiniDBError(
                    f"Column {column.name!r} is TEXT, "
                    f"got {value!r} ({type(value).__name__})."
                )
        elif column.type == "BLOB":
            if not isinstance(value, (str, bytes, bytearray)):
                raise MiniDBError(
                    f"Column {column.name!r} is BLOB, "
                    f"got {value!r} ({type(value).__name__})."
                )

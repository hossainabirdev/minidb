"""Index: a fast in-memory lookup for one column.

Why an index?
-------------
Without an index, ``WHERE id = 7`` must look at every row one by
one (a "full scan").  That is O(n): twice as many rows means twice
as much work.  With a hash index, the same lookup is O(1): it
takes the same time no matter how many rows there are, because a
Python dict finds a key directly.

What MiniDB v1 indexes
-----------------------
* PRIMARY KEY and UNIQUE columns get an index automatically,
  because they are looked up by value all the time.
* CREATE INDEX builds an index on any other column.
* The index lives only in memory.  It is rebuilt by scanning the
  log on open, so it never needs to be stored on disk.  That is a
  deliberate trade-off: rebuild is fast for a small database, and
  it means the index can never disagree with the data on disk.

A HashIndex maps a column value to the _rowid values of the rows
that have it.  _rowid is MiniDB's hidden per-row number (see
executor.py).  Most columns have one row per value; a non-unique
index may have several, so the value is always a list.
"""

from __future__ import annotations


class HashIndex:
    """Hash index on one column of one table."""

    def __init__(self, name: str, table: str, column: str, unique: bool = False):
        self.name = name
        self.table = table
        self.column = column
        self.unique = unique
        # value -> list of _rowid
        self._map: dict[object, list[int]] = {}

    def __len__(self) -> int:
        """Number of distinct values in the index."""
        return len(self._map)

    def rebuild(self, rows: list[dict]) -> None:
        """Rebuild the whole index from a list of rows.

        Called on open (after replaying the log) and after a
        CHECKPOINT.  Rows are dicts that contain "_rowid".
        """
        self._map = {}
        for row in rows:
            self.add(row.get(self.column), row["_rowid"])

    def add(self, value: object, rowid: int) -> None:
        """Add one row to the index."""
        # None is a real value in SQL; a dict can hold None as a
        # key, but an index on NULL is rarely useful.  We still
        # store it so lookups stay consistent.
        bucket = self._map.setdefault(value, [])
        if rowid not in bucket:
            bucket.append(rowid)

    def remove(self, value: object, rowid: int) -> None:
        """Remove one row from the index."""
        bucket = self._map.get(value)
        if bucket is None:
            return
        if rowid in bucket:
            bucket.remove(rowid)
        if not bucket:
            del self._map[value]

    def lookup(self, value: object) -> list[int]:
        """Return the _rowid list for this value, or [] if none.

        This is the O(1) operation the executor uses for
        ``WHERE id = ?`` on an indexed column.
        """
        return list(self._map.get(value, []))

    def has(self, value: object) -> bool:
        """True if any row has this value (used for UNIQUE checks)."""
        return value in self._map and bool(self._map[value])

    def to_dict(self) -> dict:
        """Plain data for the WAL/snapshot (the map is rebuilt)."""
        return {
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "unique": self.unique,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HashIndex":
        return cls(
            name=data["name"],
            table=data["table"],
            column=data["column"],
            unique=bool(data.get("unique", False)),
        )

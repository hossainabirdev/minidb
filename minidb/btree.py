"""B-tree index: a real ordered index for MiniDB v2.

Why a B-tree, when we already have a hash index?
--------------------------------------------------
A hash index is O(1) for ``WHERE col = value`` -- but it cannot
answer ``WHERE age > 25``, ``WHERE year BETWEEN 2014 AND 2016``,
or ``ORDER BY age`` without sorting every row.  A B-tree keeps
its keys sorted, so:

* ``=``  -- search, O(log n)
* ``>`` / ``<`` / ``>=`` / ``<=`` -- walk the tree in order, O(log n + hits)
* ``BETWEEN`` -- walk a key range, in order
* ``ORDER BY col`` -- read the keys in order, no sort step at all

How it works (small, honest version)
-------------------------------------
A B-tree is a balanced tree.  Each node holds up to ``2*t - 1``
keys (here ``t = 3``, so at most 5 keys per node).  When a node
gets full it splits into two, and the middle key moves up to
the parent -- that is what keeps the tree balanced, so every
search is O(log n) no matter how many rows there are.

Each key maps to a *list* of _rowid values (several rows can
share one value, e.g. many movies from 2016).  NULL keys are
never stored in the tree: in SQL, NULL is not a value you can
order or compare, so range queries simply skip those rows
(the executor adds NULL rows back where the query needs them).

The tree is rebuilt from the rows on open (like the hash
index), so it never needs to be stored on disk.  Deleting the
last row for a key rebuilds the tree from the remaining keys --
slower than a textbook delete, but short and obviously correct,
which is the right trade-off for a learning engine.
"""

from __future__ import annotations


class _Node:
    """One B-tree node: sorted keys, one value-list per key."""

    def __init__(self, leaf: bool = True):
        self.leaf = leaf
        self.keys: list[object] = []
        # values[i] is the list of _rowid values for keys[i].
        self.values: list[list[int]] = []
        self.children: list["_Node"] = []


class BTree:
    """A small B-tree.  Keys must be comparable with < and ==."""

    def __init__(self, t: int = 3):
        # t = minimum degree.  A node holds at most 2*t - 1 keys.
        self.t = t
        self.root = _Node(leaf=True)

    # -- lookup -------------------------------------------------------

    def search(self, key: object) -> list[int]:
        """Rowids for exactly this key, or [] if the key is absent."""
        node, idx = self._find(self.root, key)
        if node is None:
            return []
        return list(node.values[idx])

    def _find(self, node: _Node, key: object):
        """Find (node, position) of key, or (None, -1)."""
        i = 0
        while i < len(node.keys) and key > node.keys[i]:  # type: ignore[operator]
            i += 1
        if i < len(node.keys) and node.keys[i] == key:
            return node, i
        if node.leaf:
            return None, -1
        return self._find(node.children[i], key)

    # -- insert -------------------------------------------------------

    def insert(self, key: object, rowid: int) -> None:
        """Add one rowid under key (append if the key exists)."""
        node, idx = self._find(self.root, key)
        if node is not None:
            if rowid not in node.values[idx]:
                node.values[idx].append(rowid)
            return
        root = self.root
        if len(root.keys) == 2 * self.t - 1:
            # Root is full: grow the tree by one level.
            new_root = _Node(leaf=False)
            new_root.children.append(root)
            self._split_child(new_root, 0)
            self.root = new_root
        self._insert_nonfull(self.root, key, rowid)

    def _split_child(self, parent: _Node, index: int) -> None:
        """Split parent's full child at `index` into two nodes."""
        t = self.t
        full = parent.children[index]
        right = _Node(leaf=full.leaf)
        # The middle key moves up to the parent.
        mid_key = full.keys[t - 1]
        mid_values = full.values[t - 1]
        right.keys = full.keys[t:]
        right.values = full.values[t:]
        if not full.leaf:
            right.children = full.children[t:]
            full.children = full.children[:t]
        full.keys = full.keys[: t - 1]
        full.values = full.values[: t - 1]
        parent.children.insert(index + 1, right)
        parent.keys.insert(index, mid_key)
        parent.values.insert(index, mid_values)

    def _insert_nonfull(self, node: _Node, key: object, rowid: int) -> None:
        i = len(node.keys) - 1
        if node.leaf:
            # Make room and insert the key in sorted order.
            node.keys.append(None)  # type: ignore[arg-type]
            node.values.append([])
            while i >= 0 and key < node.keys[i]:  # type: ignore[operator]
                node.keys[i + 1] = node.keys[i]
                node.values[i + 1] = node.values[i]
                i -= 1
            node.keys[i + 1] = key
            node.values[i + 1] = [rowid]
            return
        while i >= 0 and key < node.keys[i]:  # type: ignore[operator]
            i -= 1
        i += 1
        if len(node.children[i].keys) == 2 * self.t - 1:
            self._split_child(node, i)
            if key > node.keys[i]:  # type: ignore[operator]
                i += 1
        self._insert_nonfull(node.children[i], key, rowid)

    # -- ordered reads --------------------------------------------------

    def inorder(self) -> list[tuple[object, list[int]]]:
        """All (key, rowids) pairs, in sorted key order."""
        out: list[tuple[object, list[int]]] = []
        self._inorder(self.root, out)
        return out

    def _inorder(self, node: _Node, out: list) -> None:
        for i, key in enumerate(node.keys):
            if not node.leaf:
                self._inorder(node.children[i], out)
            out.append((key, list(node.values[i])))
        if not node.leaf:
            self._inorder(node.children[len(node.keys)], out)

    def range_query(
        self,
        low: object | None = None,
        high: object | None = None,
        low_inclusive: bool = True,
        high_inclusive: bool = True,
    ) -> list[int]:
        """Rowids whose key is in [low, high] (bounds optional)."""
        out: list[int] = []
        for key, rowids in self.inorder():
            if low is not None:
                if low_inclusive and key < low:  # type: ignore[operator]
                    continue
                if not low_inclusive and key <= low:  # type: ignore[operator]
                    continue
            if high is not None:
                if high_inclusive and key > high:  # type: ignore[operator]
                    continue
                if not high_inclusive and key >= high:  # type: ignore[operator]
                    continue
            out.extend(rowids)
        return out


class BTreeIndex:
    """B-tree index on one column of one table.

    Same public shape as HashIndex (see index.py) so the
    executor can hold either kind in one dict: rebuild(),
    add(), remove(), lookup(), to_dict(), from_dict().
    Extra: range() and sorted_rowids() for range queries and
    ORDER BY without a sort.
    """

    def __init__(self, name: str, table: str, column: str, unique: bool = False):
        self.name = name
        self.table = table
        self.column = column
        self.unique = unique
        self.kind = "btree"
        self._tree = BTree()
        # value -> list of _rowid, kept beside the tree so exact
        # lookups and deletes stay simple.
        self._map: dict[object, list[int]] = {}

    def __len__(self) -> int:
        return len(self._map)

    def rebuild(self, rows: list[dict]) -> None:
        """Rebuild the whole index from a list of rows."""
        self._tree = BTree()
        self._map = {}
        for row in rows:
            self.add(row.get(self.column), row["_rowid"])

    def add(self, value: object, rowid: int) -> None:
        """Add one row to the index.  NULL values are skipped."""
        if value is None:
            return
        bucket = self._map.setdefault(value, [])
        if rowid not in bucket:
            bucket.append(rowid)
            self._tree.insert(value, rowid)

    def remove(self, value: object, rowid: int) -> None:
        """Remove one row.  If its key becomes empty, rebuild the tree."""
        if value is None:
            return
        bucket = self._map.get(value)
        if bucket is None or rowid not in bucket:
            return
        bucket.remove(rowid)
        if bucket:
            return
        del self._map[value]
        # Rebuild is O(n log n) and short: deleting a key from a
        # B-tree by hand is long and easy to get wrong.
        self._tree = BTree()
        for key, rowids in self._map.items():
            for rid in rowids:
                self._tree.insert(key, rid)

    def lookup(self, value: object) -> list[int]:
        """Rowids for exactly this value, or [] if none."""
        return list(self._map.get(value, []))

    def has(self, value: object) -> bool:
        return value in self._map and bool(self._map[value])

    def range(
        self,
        low: object | None = None,
        high: object | None = None,
        low_inclusive: bool = True,
        high_inclusive: bool = True,
    ) -> list[int]:
        """Rowids in key order whose key is in the given range."""
        return self._tree.range_query(low, high, low_inclusive, high_inclusive)

    def sorted_rowids(self, descending: bool = False) -> list[int]:
        """All indexed rowids, in key order (NULL rows not included)."""
        out: list[int] = []
        for _key, rowids in self._tree.inorder():
            out.extend(rowids)
        return list(reversed(out)) if descending else out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "unique": self.unique,
            "kind": "btree",
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BTreeIndex":
        return cls(
            name=data["name"],
            table=data["table"],
            column=data["column"],
            unique=bool(data.get("unique", False)),
        )

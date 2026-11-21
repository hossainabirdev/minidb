"""MiniDB -- a tiny database engine built from scratch in Python.

Quick start::

    from minidb import Database

    db = Database("data/mydata.minidb")
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users (name) VALUES ('Abir')")
    result = db.execute("SELECT * FROM users")
    print(result.rows)   # [{'id': 1, 'name': 'Abir'}]
    db.close()

Standard library only.  No external database, no ORM, no pip packages.
See README.md for the full SQL list and BANGLA_GUIDE.md to
learn how each file works, in Bengali.
"""

from .api import Collection, Database, TableHandle
from .btree import BTreeIndex
from .executor import Engine, Result
from .parser import MiniDBError

__all__ = ["Database", "TableHandle", "Collection", "Engine", "Result", "MiniDBError", "BTreeIndex"]
__version__ = "2.0.0"
__author__ = "Abir Hossain"

"""MiniDB v2 demo -- transactions, JOIN, GROUP BY, B-tree range.

Run it twice to see persistence:

    python examples/demo_v2.py
    python examples/demo_v2.py     # second run: data is still there

No output here is preset: every number comes from the file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb import Database

DB_PATH = "data/mydata-v2.minidb"


def show(title, result):
    print(f"\n-- {title}")
    if result.columns:
        print(" | ".join(result.columns))
        for row in result.rows:
            print(" | ".join(str(row.get(c)) for c in result.columns))
    else:
        print(result.message)


def main():
    db = Database(DB_PATH)
    try:
        if "users" not in db.tables():
            print("Creating tables users and orders ...")
            db.execute(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "age INTEGER, city TEXT)"
            )
            db.execute(
                "CREATE TABLE orders ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, total REAL)"
            )
            db.execute(
                "INSERT INTO users (name, age, city) VALUES "
                "('Abir', 20, 'Dhaka'), ('Sara', 25, 'Dhaka'), "
                "('Rafi', 30, 'Chittagong'), ('Nila', 25, 'Dhaka')"
            )
            db.execute(
                "INSERT INTO orders (user_id, total) VALUES "
                "(1, 99.5), (2, 150.0), (2, 50.0), (3, 200.0)"
            )
            # B-tree index on age: range queries + ORDER BY use it.
            db.execute("CREATE INDEX idx_users_age ON users (age) USING BTREE")
            print("Inserted 4 users and 4 orders.")
        else:
            print("Tables already exist -- data survived from last run.")

        show("B-tree range: SELECT name FROM users WHERE age BETWEEN 21 AND 29",
             db.execute(
                 "SELECT name FROM users WHERE age BETWEEN 21 AND 29 ORDER BY name"
             ))
        show("JOIN: users + orders, ORDER BY total",
             db.execute(
                 "SELECT users.name, orders.total FROM users "
                 "JOIN orders ON users.id = orders.user_id "
                 "ORDER BY orders.total"
             ))
        show("GROUP BY city with COUNT and AVG(age)",
             db.execute(
                 "SELECT city, COUNT(*), AVG(age) FROM users "
                 "GROUP BY city ORDER BY city"
             ))
        show("GROUP BY + HAVING COUNT(*) > 1",
             db.execute(
                 "SELECT city, COUNT(*) FROM users GROUP BY city "
                 "HAVING COUNT(*) > 1"
             ))
        show("SELECT DISTINCT city",
             db.execute("SELECT DISTINCT city FROM users ORDER BY city"))

        # Transaction: this insert is committed, so it survives.
        db.execute("BEGIN")
        db.execute(
            "INSERT INTO users (name, age, city) "
            "VALUES ('Demo', 28, 'Sylhet')"
        )
        db.execute("COMMIT")
        show("After a committed transaction",
             db.execute("SELECT COUNT(*) FROM users"))

        # Transaction: this insert is rolled back, so it never lands.
        db.execute("BEGIN")
        db.execute(
            "INSERT INTO users (name, age, city) "
            "VALUES ('Ghost', 99, 'Nowhere')"
        )
        db.execute("ROLLBACK")
        show("After a rolled-back transaction (Ghost is gone)",
             db.execute("SELECT COUNT(*) FROM users"))

        # ALTER TABLE: a new column with a default for old rows.
        if "email" not in db.engine._get_table("users").column_names():
            db.execute(
                "ALTER TABLE users ADD COLUMN email TEXT DEFAULT 'no-email'"
            )
            print("\nAdded column email with DEFAULT 'no-email'.")
        show("SELECT name, email FROM users WHERE id = 1",
             db.execute("SELECT name, email FROM users WHERE id = 1"))

        print("\nDone. Run me again -- the rows will still be here.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

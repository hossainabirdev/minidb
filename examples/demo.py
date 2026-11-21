"""MiniDB demo -- creates a table, inserts rows, runs real queries.

Run it twice to see persistence:

    python examples/demo.py
    python examples/demo.py     # second run: data is still there

The first run creates the database.  The second run finds the
table already exists, so it skips creation and shows the rows
that survived from the first run, then adds one more row.
No output here is preset: every number comes from the file.
"""
import sys
from pathlib import Path

# Allow running as `python examples/demo.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb import Database

DB_PATH = "data/mydata.minidb"


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
        if "movies" not in db.tables():
            print("Creating table movies ...")
            db.execute(
                "CREATE TABLE movies ("
                "id INTEGER PRIMARY KEY, "
                "title TEXT NOT NULL, "
                "year INTEGER, "
                "genre TEXT)"
            )
            db.execute(
                "INSERT INTO movies (title, year, genre) VALUES "
                "('Gotham', 2014, 'Crime'), "
                "('Doctor Strange', 2016, 'Fantasy'), "
                "('Rogue One', 2016, 'Sci-Fi'), "
                "('Luke Cage', 2016, 'Crime')"
            )
            db.execute("CREATE INDEX idx_movies_year ON movies (year)")
            print("Inserted 4 movies.")
        else:
            print("Table movies already exists -- data survived from last run.")
            db.execute(
                "INSERT INTO movies (title, year, genre) "
                "VALUES ('Assassin''s Creed', 2016, 'Action')"
            )
            print("Inserted 1 more movie.")

        show("SELECT * FROM movies ORDER BY year, title",
             db.execute("SELECT * FROM movies ORDER BY year, title"))
        show("SELECT title, year FROM movies WHERE year = 2016 ORDER BY title LIMIT 3",
             db.execute("SELECT title, year FROM movies WHERE year = 2016 ORDER BY title LIMIT 3"))
        show("SELECT COUNT(*) FROM movies",
             db.execute("SELECT COUNT(*) FROM movies"))
        show("SELECT COUNT(*) FROM movies WHERE genre = 'Crime'",
             db.execute("SELECT COUNT(*) FROM movies WHERE genre = 'Crime'"))
        show("SELECT MIN(year), MAX(year), AVG(year) FROM movies",
             db.execute("SELECT MIN(year), MAX(year), AVG(year) FROM movies"))

        # LiteDB-style collection, same engine, same file.
        movies = db.collection("movie_docs")
        if movies.count() == 0:
            movies.insert({"title": "Gotham", "year": 2014, "genres": ["Action", "Crime"]})
            movies.insert({"title": "Doctor Strange", "year": 2016, "genres": ["Action", "Fantasy"]})
        print(f"\n-- Collection movie_docs: {movies.count()} document(s)")
        for doc in movies.find_all():
            print(f"   {doc}")

        print("\nDone. Run me again -- the rows will still be here.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

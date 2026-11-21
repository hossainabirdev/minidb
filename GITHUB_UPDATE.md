# GitHub setup for MiniDB — paste-ready

Use this file once, when creating the repo. It is not part of the
engine — it is the checklist for the GitHub "About" section and the
first push. Do not push this file's instructions anywhere else; the
file itself is fine to keep in the repo.

## 1. Create the repo on GitHub

- Repository name: `minidb`
- Visibility: Public
- Description (paste exactly this one line):

```
A tiny database engine built from scratch in Python — no SQLite, no ORM, no libraries.
```

- Website: leave blank. There is no live site — MiniDB is a Python
  engine that runs in a terminal, not a website.
- License: MIT (already in the repo as `LICENSE`).

## 2. Topics (About → ⚙️ → Topics, max 10, comma-separated)

```
python, database, sqlite-clone, embedded-database, wal, b-tree, sql, from-scratch, cse, portfolio
```

## 3. "About" checklist

- [ ] Description pasted (see above)
- [ ] Website left blank (no live site — Python engine, not a website)
- [ ] Topics added (10, see above)
- [ ] License shows MIT
- [ ] "Releases" — after the first push, create a release `v2.0.0`
      from the `CHANGELOG.md` v2.0.0 entry (optional, but nice)

## 4. First push (run in the extracted project folder)

```bash
git init
git add -A
git commit -m "MiniDB v2.0.0 — transactions, B-tree, JOIN, GROUP BY, Studio viewer"
git branch -M main
git remote add origin https://github.com/hossainabirdev/minidb.git
git push -u origin main
```

Notes:

- Run these commands in the project folder (the one containing
  `README.md`), not inside the `minidb/` subfolder.
- `data/` is ignored by `.gitignore` — your local database files
  are never pushed to GitHub. Only the code goes up.
- Do not run `git push` until you have tested the code locally
  (`python -m unittest discover -s tests` → 88/88 pass).

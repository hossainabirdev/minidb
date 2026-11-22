# MiniDB — বাংলা গাইড (v2.0.1)

এই ফাইলটা তোমার জন্য, মাস্টার আবির। README ইংরেজিতে professional লেখা, আর এখানে একই জিনিস সহজ বাংলায়, section by section, যাতে তুমি বুঝে বুঝে শিখতে পারো।

## ১. MiniDB আসলে কী?

একটা ছোট database engine। তুমি terminal-এ লিখবে:

```sql
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
INSERT INTO users (name) VALUES ('Abir');
SELECT * FROM users;
```

আর MiniDB data-টা তোমার PC-র একটা ফাইলে (`data/mydata.minidb`) সেভ করে রাখবে। কম্পিউটার বন্ধ করে আবার খুললেও data থাকবে। এটাই database-এর আসল কাজ — data হারিয়ে না যাওয়া।

SQLite, Redis, LiteDB — সবাই ভেতরে ভেতরে এই একই কাজ করে। আমরা শুধু সেটা নিজে হাতে, ছোট করে, Python-এ বানিয়েছি, যাতে ভেতরের কাজটা তুমি দেখতে পাও।

## ২. v2-তে নতুন কী আছে?

- **Transaction** — `BEGIN` লিখে কয়েকটা কাজ করো, তারপর `COMMIT` করলে সব save হবে, `ROLLBACK` করলে কিছুই save হবে না। Crash হলে commit না করা কাজ হারিয়ে যাবে — এটাই সঠিক।
- **B-tree index** — `CREATE INDEX ... USING BTREE`। Hash index শুধু `=` খুঁজতে পারে, B-tree `>`, `<`, `BETWEEN` খুঁজতে পারে, আর `ORDER BY` করতে পারে sort না করেই, কারণ ওর key-গুলো সাজানো থাকে।
- **JOIN** — দুটো টেবিল একসাথে জোড়া লাগানো। যেমন `users` আর `orders` — `JOIN orders ON users.id = orders.user_id`।
- **GROUP BY / HAVING আর DISTINCT** — একই city-র user কতজন, গড় বয়স কত — এই হিসাবগুলো।
- **ALTER TABLE ADD COLUMN** — পুরনো টেবিলে নতুন কলাম যোগ করা, সাথে DEFAULT value।
- **Studio** — browser-এ database দেখার ছোট viewer। শুধু SELECT চলে, কিছু বদলানো যায় না।
- **TCP server** — socket দিয়ে দূর থেকে query করা যায়, উত্তর আসে JSON-এ।

## ৩. কোন ফাইল কী করে? (পড়ার order)

### ১) `minidb/schema.py` — টেবিলের আকৃতি

এখানে শুধু লেখা থাকে একটা টেবিল কেমন দেখতে হবে। যেমন `users` টেবিলে `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL` আছে।

- `Column` মানে একটা কলাম — নাম, type, PRIMARY KEY কিনা, NOT NULL কিনা, UNIQUE কিনা, DEFAULT কী।
- `Table` মানে অনেকগুলো কলাম মিলে একটা টেবিল।
- Type মাত্র ৪টা: `INTEGER` (পূর্ণ সংখ্যা), `TEXT` (লেখা), `REAL` (দশমিক), `BLOB` (raw data)।
- এই ফাইল কোনো ফাইল পড়ে না, কোনো SQL চালায় না। শুধু আকৃতি বোঝে। তাই এটা সবার আগে পড়ো — সবচেয়ে সহজ।

### ২) `minidb/storage.py` — ফাইলে কীভাবে লেখে?

এটা MiniDB-র সবচেয়ে important ফাইলগুলোর একটা। দুটো ফাইল থাকে:

- `mydata.minidb` — **snapshot**। সব live data-র পরিষ্কার একটা copy। শুরুতে লেখা থাকে `MINIDB1` — এটাকে বলে magic header, যাতে খুলে বোঝা যায় এটা MiniDB-র ফাইল।
- `mydata.minidb-wal` — **WAL (Write-Ahead Log)**। প্রতিটা পরিবর্তন প্রথমে এখানে লেখা হয়, তারপর memory-তে বসে।

একটা record কেমন করে লেখা হয়?
- প্রথমে ৪ byte: payload কত বড় (length)
- তারপর ৪ byte: checksum (CRC32) — data ঠিক আছে কিনা বোঝার জন্য
- তারপর payload: একটা JSON object, যেমন `{"op": "insert", "table": "users", "row": {"id": 1}}`

Crash হলে কী হয়? ধরো লেখার মাঝখানে বিদ্যুৎ চলে গেল। শেষ record-টা অর্ধেক লেখা থাকবে। খোলার সময় checksum মিলবে না, তাই MiniDB সেই অর্ধেক record-টা ফেলে দেবে, আর আগের ভালো data-গুলো ঠিক থাকবে। এটাকে বলে **torn write** সামলানো।

`fsync()` মানে কী? Python-এ `write()` করলেই data সাথে সাথে disk-এ যায় না, কিছুক্ষণ memory-তে থাকে। `fsync()` বললে disk-কে জোর করে লিখতে বলা হয়। তাই INSERT-এর পর `fsync()` না করলে crash-এ data হারিয়ে যেতে পারতো।

`checkpoint()` মানে কী? WAL-এ অনেক record জমে গেলে MiniDB সব live data একবারে snapshot ফাইলে লিখে WAL-টা মুছে দেয়। `db.close()` করলে এটা নিজে নিজেই হয়।

### WAL কেন data-র আগে লেখা হয়?

WAL-এ লেখা হয় data বসানোর **আগে**, কারণ crash হলে WAL-ই একমাত্র প্রমাণ। ধরো INSERT লিখতে লিখতে প্রসেস মরে গেল — WAL-এ record থাকলে খোলার সময় replay করে data ফিরিয়ে আনা যায়। উল্টোটা হলে, মানে data আগে বসিয়ে WAL পরে লিখলে, মাঝখানে মরে গেলে পরিবর্তনটা হয়ে যেত, কিন্তু তার কোনো record থাকত না — data হারিয়ে যেত, আর কেউ জানতেও পারত না। তাই নিয়মটা সহজ: প্রথমে WAL-এ লিখো আর `fsync()` করো, তারপর memory-তে বসাও।

### ৩) `minidb/index.py` — দ্রুত খোঁজা (hash index)

Index না থাকলে `WHERE id = 7` খুঁজতে সব row একটা একটা করে দেখতে হয় — এটা O(n), মানে row দ্বিগুণ হলে সময়ও দ্বিগুণ।

Index থাকলে Python-এর `dict` দিয়ে সরাসরি খুঁজে পাওয়া যায় — এটা O(1), মানে row যতই হোক সময় প্রায় একই।

- `HashIndex` একটা কলামের জন্য একটা dict রাখে: `value -> [_rowid-গুলো]`।
- `_rowid` হলো MiniDB-র নিজের দেওয়া লুকানো নম্বর (১, ২, ৩...) — প্রতিটা row-এর জন্য আলাদা। SELECT-এ এটা দেখানো হয় না।
- Index শুধু memory-তে থাকে। খোলার সময় log scan করে আবার বানানো হয়। তাই index কখনো disk-এর data-র সাথে মিল না খাওয়ার ভয় নেই।
- PRIMARY KEY আর UNIQUE কলামে index নিজে থেকেই তৈরি হয়। অন্য কলামে `CREATE INDEX` দিয়ে বানাতে হয়।

### ৪) `minidb/btree.py` — সাজানো খোঁজা (B-tree index, v2)

Hash index শুধু `=` পারে। B-tree পারে আরও অনেক কিছু, কারণ ওর key-গুলো সাজানো থাকে:

- `WHERE age > 25` — বড় key-গুলো order-এ হেঁটে যাওয়া যায়
- `WHERE year BETWEEN 2014 AND 2016` — একটা range হেঁটে যাওয়া যায়
- `ORDER BY age` — key-গুলো already সাজানো, তাই sort করতে হয় না

B-tree কীভাবে balanced থাকে? একটা node-এ সর্বোচ্চ ৫টা key থাকে (এখানে `t = 3`)। Node ভরে গেলে দুই ভাগ হয়ে যায়, মাঝের key উপরে উঠে যায়। তাই গাছটা সবসময় balanced থাকে, আর খোঁজা সবসময় O(log n) থাকে — row যতই বাড়ুক, গভীরতা খুব ধীরে বাড়ে।

একটা key-তে অনেক row থাকতে পারে (যেমন ২০১৬ সালের অনেক movie) — তাই প্রতিটা key-র সাথে একটা list থাকে, `_rowid`-দের list। NULL key গাছে রাখা হয় না — SQL-এ NULL কোনো value না, তাই range-এ ওটা আসে না।

Delete করার সময় শেষ row-টা চলে গেলে key-টা মুছে যায়। গাছ থেকে key মুছে ফেলার আসল algorithm লম্বা আর ভুল হওয়ার ভয় আছে, তাই MiniDB সহজ রাস্তা নেয় — বাকি key-গুলো দিয়ে গাছটা আবার বানিয়ে ফেলে। ছোট database-এ এটা ঠিক আছে, আর code-টা বোঝা সহজ।

ব্যবহার:

```sql
CREATE INDEX idx_users_age ON users (age) USING BTREE;
SELECT name FROM users WHERE age > 20 AND age < 30 ORDER BY age;
```

### ৫) `minidb/parser.py` — SQL লেখা বোঝা

এই ফাইল দুই ধাপে কাজ করে:

**ধাপ ১ — Tokenizer:** লেখাটাকে ছোট ছোট টুকরো (token) করে। যেমন `SELECT * FROM users` হবে `SELECT`, `*`, `FROM`, `users`। এটা string, number, symbol চেনে, কিন্তু SQL-এর মানে বোঝে না।

**ধাপ ২ — Parser (recursive descent):** token-গুলোকে order অনুযায়ী পড়ে একটা গাছ (AST) বানায়। যেমন `WHERE age > 20 AND city = 'Dhaka'`-কে বোঝে — প্রথমে AND, তার ভেতরে দুটো comparison।

Recursive descent মানে: OR বোঝার জন্য AND-কে ডাকে, AND বোঝার জন্য NOT-কে ডাকে, NOT বোঝার জন্য comparison-কে ডাকে — এভাবে নিচে নামতে নামতে একটা value-তে পৌঁছায়।

v2-তে parser আরও পারে: `BEGIN` / `COMMIT` / `ROLLBACK`, `JOIN ... ON`, `GROUP BY` / `HAVING`, `SELECT DISTINCT`, `BETWEEN`, `ALTER TABLE ADD COLUMN`, `USING BTREE`। আর `HAVING COUNT(*) > 1` লিখলে `COUNT(*)`-টাও expression হিসেবে বোঝে।

### ৬) `minidb/executor.py` — আসল কাজ

এই ফাইল SQL-এর গাছটা (AST) নিয়ে আসল কাজ করে। প্রতিটা পরিবর্তনে ৩টা ধাপ:

1. **Validate** — প্রথমে সব check করে: type ঠিক আছে? NOT NULL ভাঙছে? UNIQUE ভাঙছে? ভুল হলে কিছুই লেখে না।
2. **WAL-এ লেখে** — `storage.append()` দিয়ে fsync করে। Transaction-এর ভেতরে হলে WAL-এ না লিখে pending list-এ রাখে।
3. **Memory-তে বসায়** — table-এর row list-এ ঢুকায়, index update করে।

মাঝখানে crash হলে? ধাপ ২ হয়ে গেলে WAL-এ আছে, খোলার সময় replay হবে। ধাপ ২-এর আগে crash হলে পরিবর্তনটা হয়ইনি — দুটোই সঠিক।

**Transaction কীভাবে কাজ করে?** `BEGIN` করলে executor সবকিছুর একটা snapshot (deep copy) নিয়ে রাখে — tables, rows, indexes। তারপর প্রতিটা পরিবর্তন memory-তে বসে, কিন্তু WAL-এ যায় না, শুধু pending list-এ জমে। `COMMIT` করলে pending record-গুলো একটা একটা করে WAL-এ লেখা হয়। `ROLLBACK` করলে snapshot-টা ফিরিয়ে আনা হয় — যেন কিছুই হয়নি। আর crash হলে pending list-টা memory-তেই মরে যায়, ফাইলে কিছু যায় না।

**JOIN কীভাবে হয়?** দুটো টেবিলের row nested loop-এ মেলানো হয় — বামের প্রতিটা row-এর জন্য ডানের প্রতিটা row দেখা হয়, `ON` condition মিললে জোড়া রাখা হয়। ভেতরে row-গুলো `users.name`, `orders.total` — এই qualified নামে থাকে, যাতে দুটো টেবিলে একই কলাম থাকলেও গুলিয়ে না যায়।

**GROUP BY কীভাবে হয়?** WHERE দিয়ে filter করা row-গুলোকে group key অনুযায়ী ভাগ করা হয় (যেমন city অনুযায়ী)। তারপর প্রতিটা group-এর উপরে aggregate হিসাব হয় — `COUNT(*)`, `SUM`, `AVG`। `HAVING` হলো group-এর উপরে WHERE — যেমন `HAVING COUNT(*) > 1` মানে শুধু সেই group-গুলো, যাদের row একটার বেশি।

**SELECT DISTINCT** — সাধারণ SELECT চালিয়ে তারপর একই row-গুলোকে একটা করে রাখা হয়।

**ALTER TABLE** — নতুন কলাম table-এর shape-এ যোগ হয়, আর পুরনো সব row-তে DEFAULT value (না থাকলে NULL) বসে। এই পরিবর্তনটাও WAL-এ লেখা হয়, তাই reopen করলেও কলামটা থাকে।

SELECT কীভাবে চলে? Row by row:
- প্রথমে WHERE দিয়ে filter করে (index থাকলে index দিয়ে, না থাকলে scan করে)
- তারপর ORDER BY দিয়ে সাজায় (B-tree index থাকলে গাছ থেকে order-এ পড়ে, sort করে না)
- তারপর LIMIT / OFFSET দিয়ে কাটে
- শেষে শুধু যে কলাম চাওয়া হয়েছে সেগুলো দেখায়

### ৭) `minidb/api.py` — Python থেকে ব্যবহার

SQL না লিখে Python দিয়ে ব্যবহার করার রাস্তা:

```python
db = Database("data/mydata.minidb")
db.execute("SELECT * FROM users")
users = db.table("users")
users.insert({"name": "Abir"})
movies = db.collection("movies")   # LiteDB-style document
movies.insert({"title": "Gotham", "year": 2014})

# Transaction Python থেকে
db.begin()
db.execute("INSERT INTO users (name) VALUES ('Sara')")
db.commit()   # বা db.rollback()
```

Collection মানে কী? LiteDB-তে data থাকে document আকারে (JSON-এর মতো)। MiniDB-তে collection আসলে একটা সাধারণ টেবিল — `id INTEGER PRIMARY KEY, doc TEXT` — আর document-টা JSON string হিসেবে `doc` কলামে থাকে। তাই collection-ও একই WAL, একই persistence পায় — দ্বিতীয় কোনো engine নেই।

### ৮) `minidb/repl.py` — terminal-এ কথা বলা

`python -m minidb.repl` লিখলে `minidb>` prompt আসে। সেখানে SQL লিখে `;` দিয়ে Enter দিলেই চলে।

প্রথম সপ্তাহের জন্য দুটো ছোট command আছে — `SET key value` আর `GET key`। এগুলো আসলে `_kv` নামে একটা সাধারণ টেবিলে data রাখে। এটা শুধু stepping stone, আসল ভাষা SQL-ই।

### ৯) `minidb/studio.py` — browser-এ database দেখা (v2, নতুন)

`python -m minidb.studio` লিখলে http://127.0.0.1:8766 খুলে যায়। সেখানে টেবিলের list, row-গুলো, আর একটা box-এ SELECT লিখে চালানো যায়।

Important: Studio **শুধু SELECT** চালায়। INSERT / UPDATE / DELETE লিখলে error দেয় — viewer দিয়ে data বদলানো যায় না। আর এটা শুধু তোমার নিজের PC-তে চলে (127.0.0.1), internet-এ না।

### ১০) `minidb/server.py` — TCP server (v2, এখন আসল)

`python -m minidb.server 127.0.0.1 8765` লিখলে server চলে। অন্য terminal থেকে এক লাইনে একটা SQL পাঠালে উত্তর আসে একটা JSON object:

```json
{"columns": ["name"], "rows": [{"name": "Abir"}], "rowcount": 1}
```

একবারে একটা client — দ্বিতীয় client অপেক্ষা করে। কোনো auth নেই, কোনো TLS নেই, তাই শুধু নিজের PC-তে ব্যবহার করো, internet-এ port খুলো না। একটা connection-এর ভেতরে BEGIN / COMMIT-ও কাজ করে, কারণ একই Database খোলা থাকে।

## ৪. কী কী শিখবে এই প্রজেক্ট থেকে?

- ফাইলে data কীভাবে থাকে (length + checksum + JSON)
- crash-এর পরেও data কীভাবে বাঁচে (WAL + fsync + replay)
- index কীভাবে O(n) কে O(1) বানায়, আর B-tree কীভাবে range আর ORDER BY দেয়
- transaction কীভাবে কাজ করে — commit না হলে কেন কিছুই save হয় না
- JOIN কীভাবে দুটো টেবিল মেলায়, GROUP BY কীভাবে হিসাব করে
- parser কীভাবে লেখাকে গাছ বানায়
- executor কীভাবে row by row কাজ করে
- constraint (PRIMARY KEY, NOT NULL, UNIQUE) কেন দরকার

## ৫. কী কী এখনো নেই? (v2-র পরেও)

- Encryption (ফাইল plain JSON — ইচ্ছা করে, কারণ LiteDB-র নিজের encryption-ও নিরাপদ না বলে ওরা নিজেরাই মানা করেছে)
- অনেক writer একসাথে / locking (একবারে একটাই writer)
- Replication — এক ফাইল, এক মেশিন
- পুরো SQL dialect — একটার বেশি JOIN না, subquery না, view না

এগুলো না থাকা মানে MiniDB খারাপ না — মানে এটা ছোট আর পরিষ্কার রাখা হয়েছে, যাতে তুমি প্রতিটা লাইন বুঝতে পারো।

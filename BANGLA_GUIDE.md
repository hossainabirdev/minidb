# MiniDB — বাংলা গাইড

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

## ২. কোন ফাইল কী করে? (পড়ার order)

### ১) `minidb/schema.py` — টেবিলের আকৃতি

এখানে শুধু লেখা থাকে একটা টেবিল কেমন দেখতে হবে। যেমন `users` টেবিলে `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL` আছে।

- `Column` মানে একটা কলাম — নাম, type, PRIMARY KEY কিনা, NOT NULL কিনা, UNIQUE কিনা।
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

### ৩) `minidb/index.py` — দ্রুত খোঁজা

Index না থাকলে `WHERE id = 7` খুঁজতে সব row একটা একটা করে দেখতে হয় — এটা O(n), মানে row দ্বিগুণ হলে সময়ও দ্বিগুণ।

Index থাকলে Python-এর `dict` দিয়ে সরাসরি খুঁজে পাওয়া যায় — এটা O(1), মানে row যতই হোক সময় প্রায় একই।

- `HashIndex` একটা কলামের জন্য একটা dict রাখে: `value -> [_rowid-গুলো]`।
- `_rowid` হলো MiniDB-র নিজের দেওয়া লুকানো নম্বর (১, ২, ৩...) — প্রতিটা row-এর জন্য আলাদা। SELECT-এ এটা দেখানো হয় না।
- Index শুধু memory-তে থাকে। খোলার সময় log scan করে আবার বানানো হয়। তাই index কখনো disk-এর data-র সাথে মিল না খাওয়ার ভয় নেই।
- PRIMARY KEY আর UNIQUE কলামে index নিজে থেকেই তৈরি হয়। অন্য কলামে `CREATE INDEX` দিয়ে বানাতে হয়।

### ৪) `minidb/parser.py` — SQL লেখা বোঝা

এই ফাইল দুই ধাপে কাজ করে:

**ধাপ ১ — Tokenizer:** লেখাটাকে ছোট ছোট টুকরো (token) করে। যেমন `SELECT * FROM users` হবে `SELECT`, `*`, `FROM`, `users`। এটা string, number, symbol চেনে, কিন্তু SQL-এর মানে বোঝে না।

**ধাপ ২ — Parser (recursive descent):** token-গুলোকে order অনুযায়ী পড়ে একটা গাছ (AST) বানায়। যেমন `WHERE age > 20 AND city = 'Dhaka'`-কে বোঝে — প্রথমে AND, তার ভেতরে দুটো comparison।

Recursive descent মানে: OR বোঝার জন্য AND-কে ডাকে, AND বোঝার জন্য NOT-কে ডাকে, NOT বোঝার জন্য comparison-কে ডাকে — এভাবে নিচে নামতে নামতে একটা value-তে পৌঁছায়।

এখানে কোনো regex-only shortcut নেই — আসল grammar আছে। তাই `(a = 1 OR b = 2) AND c = 3` লিখলে bracket-এর মানে ঠিক থাকে।

### ৫) `minidb/executor.py` — আসল কাজ

এই ফাইল SQL-এর গাছটা (AST) নিয়ে আসল কাজ করে। প্রতিটা পরিবর্তনে ৩টা ধাপ:

1. **Validate** — প্রথমে সব check করে: type ঠিক আছে? NOT NULL ভাঙছে? UNIQUE ভাঙছে? ভুল হলে কিছুই লেখে না।
2. **WAL-এ লেখে** — `storage.append()` দিয়ে fsync করে।
3. **Memory-তে বসায়** — table-এর row list-এ ঢুকায়, index update করে।

মাঝখানে crash হলে? ধাপ ২ হয়ে গেলে WAL-এ আছে, খোলার সময় replay হবে। ধাপ ২-এর আগে crash হলে পরিবর্তনটা হয়ইনি — দুটোই সঠিক।

SELECT কীভাবে চলে? Row by row:
- প্রথমে WHERE দিয়ে filter করে
- তারপর ORDER BY দিয়ে সাজায়
- তারপর LIMIT / OFFSET দিয়ে কাটে
- শেষে শুধু যে কলাম চাওয়া হয়েছে সেগুলো দেখায়

একটা shortcut আছে: WHERE যদি ঠিক `indexed_col = value` হয়, তাহলে index দিয়ে সরাসরি row খুঁজে আনে, scan করে না।

Aggregate কীভাবে হয়? `COUNT(*)`, `SUM(age)`, `AVG(age)`, `MIN(age)`, `MAX(age)` — filter করা row-গুলোর উপরে হিসাব করে একটা row ফেরত দেয়।

### ৬) `minidb/api.py` — Python থেকে ব্যবহার

SQL না লিখে Python দিয়ে ব্যবহার করার রাস্তা:

```python
db = Database("data/mydata.minidb")
db.execute("SELECT * FROM users")
users = db.table("users")
users.insert({"name": "Abir"})
movies = db.collection("movies")   # LiteDB-style document
movies.insert({"title": "Gotham", "year": 2014})
```

Collection মানে কী? LiteDB-তে data থাকে document আকারে (JSON-এর মতো)। MiniDB-তে collection আসলে একটা সাধারণ টেবিল — `id INTEGER PRIMARY KEY, doc TEXT` — আর document-টা JSON string হিসেবে `doc` কলামে থাকে। তাই collection-ও একই WAL, একই persistence পায় — দ্বিতীয় কোনো engine নেই।

### ৭) `minidb/repl.py` — terminal-এ কথা বলা

`python -m minidb.repl` লিখলে `minidb>` prompt আসে। সেখানে SQL লিখে `;` দিয়ে Enter দিলেই চলে।

প্রথম সপ্তাহের জন্য দুটো ছোট command আছে — `SET key value` আর `GET key`। এগুলো আসলে `_kv` নামে একটা সাধারণ টেবিলে data রাখে। এটা শুধু stepping stone, আসল ভাষা SQL-ই।

### ৮) `minidb/server.py` — v2-র sketch (এখন ব্যবহার করো না)

এটা TCP server-এর ছোট একটা sketch — একটা client, এক লাইনে একটা SQL, JSON-এ উত্তর। এতে কোনো auth নেই, কোনো TLS নেই, তাই এটা শুধু শেখার জন্য। আসল multi-client server v2-তে আসবে।

## ৩. কী কী শিখবে এই প্রজেক্ট থেকে?

- ফাইলে data কীভাবে থাকে (length + checksum + JSON)
- crash-এর পরেও data কীভাবে বাঁচে (WAL + fsync + replay)
- index কীভাবে O(n) কে O(1) বানায়
- parser কীভাবে লেখাকে গাছ বানায়
- executor কীভাবে row by row কাজ করে
- constraint (PRIMARY KEY, NOT NULL, UNIQUE) কেন দরকার

## ৪. কী কী এখনো নেই? (v2-তে আসবে)

- Transaction: BEGIN / COMMIT / ROLLBACK
- JOIN আর GROUP BY
- Encryption (এখন ফাইল plain JSON — ইচ্ছা করে, কারণ LiteDB-র নিজের encryption-ও নিরাপদ না বলে ওরা নিজেরাই মানা করেছে)
- বড় ফাইল / blob storage
- অনেক client একসাথে (multi-client server)

এগুলো না থাকা মানে MiniDB খারাপ না — মানে v1 ছোট আর পরিষ্কার রাখা হয়েছে, যাতে তুমি প্রতিটা লাইন বুঝতে পারো। v2-তে একটা একটা করে যোগ করবো।

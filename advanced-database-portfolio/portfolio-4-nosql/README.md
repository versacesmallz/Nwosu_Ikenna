# Portfolio 4 — NoSQL and Advanced Data Models

Case study: **Kasuwa Marketplace** (same data as Portfolios 1–3). The aim is not to replace the relational database but to identify the parts of Kasuwa whose access patterns fit a different data model better, implement them, and justify the split (**polyglot persistence**).

| Part | Model | Engine used | Kasuwa data |
|---|---|---|---|
| A | Key-value (plus Redis hashes, counters, sorted sets) | **Redis 7.0.15** (real server) | sessions, carts, product-page cache, flash-sale stock, bestseller leaderboard |
| B | Document | **PostgreSQL 16 JSONB** with GIN index | product catalogue with category-specific attributes and embedded reviews |
| C | Document (MongoDB API) | PyMongo API via `mongomock` (see note) | the same catalogue, queried with `find()` / `aggregate()` |

## A. Key-value model — Redis ([`redis_keyvalue.py`](redis_keyvalue.py), output: [`evidence/redis_output.txt`](evidence/redis_output.txt))

Key design follows `namespace:entity:id` so keys are self-describing and can be scanned by prefix:

| Key | Redis type | TTL | Why this fits key-value |
|---|---|---|---|
| `session:{token}` | HASH | 30 min | always fetched by its token, never queried by content; expiry replaces a cleanup job |
| `cart:{customer_id}` | HASH (product_id → qty) | 7 days | written on every click, read only by its owner, disposable |
| `cache:product:{id}` | STRING (JSON) | 10 min | cache-aside copy of a 5-table join; DEL on price/stock change |
| `stock:{product_id}` | STRING (integer) | — | flash-sale counter decremented by an atomic Lua script |
| `rank:bestsellers:{yyyy-mm}` | ZSET | — | `ZINCRBY` on each sale, `ZREVRANGE` for the top 10 |

Measured results:

| Test | Result |
|---|---|
| Session expiry | a 2-second session existed (`EXISTS` = 1) and was gone after 2.2 s (`EXISTS` = 0) |
| Product page, 2,000 reads | PostgreSQL join median 0.62 ms vs Redis cache hit 0.22 ms (≈2.8×) — modest because PostgreSQL also served from RAM; the bigger win is removing repeat reads from the OLTP database |
| Flash sale, 60 concurrent buyers, 20 units | **20 sold, 40 rejected, stock 0** — correct, because Redis runs each Lua script atomically |
| Top-10 bestsellers | Redis `ZREVRANGE` 0.19 ms vs PostgreSQL `GROUP BY` 72 ms; same top-3 ranking as SQL (`True`) |
| Memory | whole leaderboard of 4,978 products ≈ 529 KB; a session ≈ 152 bytes |

## B. Document model — PostgreSQL JSONB ([`document_model.sql`](document_model.sql), [`document_queries.sql`](document_queries.sql), output: [`evidence/document_queries_output.txt`](evidence/document_queries_output.txt))

**Problem it solves.** Product attributes depend on category: a phone has RAM, storage and network; an Ankara gown has sizes, colours and fabric; a bag of rice has weight and state of origin. In the relational schema this would need either dozens of mostly-NULL columns or an EAV table that is slow and hard to validate. As a document, each product carries exactly the attributes it has.

**Document design** (one document per product, `_id` = SKU):

```json
{ "_id": "KSW-000028", "name": "Infinix Smartphone Model 28", "price": 861186.83, "currency": "NGN",
  "category": { "name": "Mobile Phones", "path": ["Electronics", "Mobile Phones"] },
  "seller":   { "id": 80, "name": "Mega Ventures 80", "state": "Lagos" },
  "attributes": { "brand": "Infinix", "ram_gb": 6, "storage_gb": 128, "network": ["4G"], "dual_sim": true, "battery_mah": 5000 },
  "rating": { "avg": 3.89, "count": 62 },
  "recent_reviews": [ {"customer_id": 43198, "rating": 4, "text": "...", "date": "2026-10-29"}, ... ] }
```

Modelling decisions:
- **Embed** what is always read with the product page (seller summary, rating summary, category path) so the page is one read with no joins.
- **Bounded embedding** for reviews: only the 3 newest are embedded (`$slice: 3` in part C); the full review history stays in the relational `product_review` table. Unbounded arrays would make documents grow without limit.
- **Reference, not embed**, for anything shared and frequently updated: stock lives in Redis/PostgreSQL, and the seller's full record stays relational.

Evidence:
- D1–D3 run containment queries into nested objects and arrays (e.g. gowns with `"12"` in `sizes` and `"Green"` in `colours`).
- **GIN index (`jsonb_path_ops`)**: same containment query went from Seq Scan, 552 buffers, 3.3 ms → Bitmap Index Scan, 123 buffers, 0.40 ms. Index size 2 MB.
- **Schema flexibility**: a new "Solar Generators" category with nested attributes (`outputs.ac_sockets`, `battery_chemistry`) was inserted and queried with no `ALTER TABLE`.
- **Validation lesson**: my first `CHECK (jsonb_typeof(doc->'price') = 'number')` *accepted* a document with no price, because the missing key gives NULL and a CHECK that evaluates to NULL passes. Rewriting it with `coalesce(..., 'missing')` makes the same insert fail (last lines of the output). Schema-less storage still needs explicit validation.

## C. Same documents through the MongoDB API ([`mongo_catalogue.py`](mongo_catalogue.py), output: [`evidence/mongo_api_output.txt`](evidence/mongo_api_output.txt))

The 5,001 documents are loaded into a `kasuwa.products` collection and queried with MongoDB syntax: `find()` with dot-notation and `$gte/$lt`, an aggregation pipeline (`$match → $group → $sort`), `$unwind` over embedded reviews, and an atomic `updateOne` using `$push` with `$position`/`$slice` plus `$inc`. The script then cross-checks every answer against the JSONB queries: **D1 same products in same order, D2 same brand counts, D3 same count — all `True`.**

> **Engine note:** MongoDB server binaries could not be downloaded in the build environment, so this part runs on `mongomock`, an in-memory implementation of the PyMongo API. The code is unchanged for a real MongoDB or Atlas cluster: replace `mongomock.MongoClient()` with `pymongo.MongoClient("mongodb://...")`. The equivalent `mongosh` command is printed above each query. Performance numbers are therefore only reported for Redis and JSONB, not for this part.

## D. Suitability — what stays relational, and why

| Data | Best model | Reason |
|---|---|---|
| Orders, payments, stock of record | **Relational** | multi-row ACID transactions, foreign keys, and the concurrency control shown in Portfolio 3; money must not be eventually consistent |
| Product catalogue | **Document** | variable attributes, read-mostly, read as one unit |
| Sessions, carts, cache, counters, rankings | **Key-value (Redis)** | single-key access, very high write rate, data can expire or be rebuilt |
| "Customers who bought X also bought Y" | Graph (e.g. Neo4j) | multi-hop traversals — *considered, not implemented*; at Kasuwa's size a SQL self-join on `order_item` is sufficient |
| Delivery GPS pings / clickstream | Wide-column (e.g. Cassandra) | append-only, time-partitioned, write-heavy — *considered, not implemented* |

Costs of the split: data now lives in three places, so the application must keep them in step (cache invalidation, writing Redis flash-sale reservations back to `orders`). Redis by default persists asynchronously and can lose the last second of writes on a crash, which is acceptable for carts and caches but is the reason stock **of record** stays in PostgreSQL.

## How to run

```bash
redis-server --daemonize yes
pip install redis psycopg2-binary mongomock pymongo
python3 redis_keyvalue.py      > evidence/redis_output.txt
psql -d kasuwa -f document_model.sql
psql -d kasuwa -f document_queries.sql > evidence/document_queries_output.txt 2>&1
python3 mongo_catalogue.py     > evidence/mongo_api_output.txt
```

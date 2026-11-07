"""
MIT 8103 - Portfolio 4 (part A): Key-value model for Kasuwa Marketplace in Redis.

Redis key design (namespace:entity:id)
  session:{token}                 HASH    logged-in session, TTL 30 min (sliding)
  cart:{customer_id}              HASH    product_id -> qty, TTL 7 days
  cache:product:{product_id}      STRING  JSON product page (cache-aside from PostgreSQL), TTL 10 min
  stock:{product_id}              STRING  integer counter for flash-sale stock (atomic Lua decrement)
  rank:bestsellers:{yyyy-mm}      ZSET    product_id scored by units sold (real-time leaderboard)

Each section prints its commands and results as evidence.
Usage: python3 redis_keyvalue.py > evidence/redis_output.txt
Needs: redis-server on localhost:6379, PostgreSQL 'kasuwa' DB from Portfolio 1.
"""
import json, os, statistics, threading, time, uuid
import psycopg2, redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
pg = psycopg2.connect(os.environ.get("KASUWA_DSN", "host=localhost dbname=kasuwa user=postgres"))
pg.autocommit = True
cur = pg.cursor()
cur.execute("SET search_path TO kasuwa")


def banner(t):
    print("\n" + "=" * 90 + f"\n{t}\n" + "=" * 90)


def show(cmd, result):
    print(f"redis> {cmd}\n       {result}")


# ---------------------------------------------------------------------------
banner("0. Environment")
info = r.info("server")
print(f"Redis {info['redis_version']}  mode={info['redis_mode']}  (flushing demo keys only)")
for pattern in ("session:*", "cart:*", "cache:product:*", "stock:*", "rank:*"):
    for k in r.scan_iter(pattern):
        r.delete(k)

# ---------------------------------------------------------------------------
banner("1. SESSION STORE - HASH with sliding expiry (TTL)")
token = uuid.uuid4().hex[:16]
key = f"session:{token}"
cur.execute("SELECT c.full_name, s.state_name FROM customer c JOIN state s USING (state_id) WHERE customer_id = 20211")
cname, cstate = cur.fetchone()
r.hset(key, mapping={"customer_id": 20211, "name": cname, "state": cstate, "device": "android"})
r.expire(key, 1800)
show(f"HSET {key} customer_id 20211 name '{cname}' state '{cstate}' device android", "4 fields")
show(f"EXPIRE {key} 1800", "1")
show(f"HGETALL {key}", r.hgetall(key))
show(f"TTL {key}", f"{r.ttl(key)} seconds left")
short = f"session:{uuid.uuid4().hex[:16]}"
r.hset(short, "customer_id", 1); r.expire(short, 2)
show(f"(second session with 2 s TTL) EXISTS {short}", r.exists(short))
time.sleep(2.2)
show(f"(after 2.2 s) EXISTS {short}", f"{r.exists(short)}  <- expired automatically, no cleanup job needed")

# ---------------------------------------------------------------------------
banner("2. SHOPPING CART - HASH per customer, atomic field increments")
ck = "cart:20211"
r.hincrby(ck, "5001", 1); r.hincrby(ck, "17", 2); r.hincrby(ck, "17", 1)
r.expire(ck, 7 * 24 * 3600)
show(f"HINCRBY {ck} 5001 1 ; HINCRBY {ck} 17 2 ; HINCRBY {ck} 17 1", "")
show(f"HGETALL {ck}", r.hgetall(ck))
r.hdel(ck, "5001")
show(f"HDEL {ck} 5001  (customer removes item)", r.hgetall(ck))
print("Why Redis: carts are written on every click, read by one user, keyed by customer, and are"
      " disposable. Keeping them out of PostgreSQL removes the highest-frequency writes from the OLTP database.")

# ---------------------------------------------------------------------------
banner("3. PRODUCT PAGE CACHE - cache-aside pattern, latency vs PostgreSQL")
PRODUCT_SQL = """
SELECT p.product_id, p.sku, p.product_name, p.unit_price::float, c.category_name, s.business_name,
       i.qty_on_hand, round(avg(r.rating),2)::float AS avg_rating, count(r.review_id) AS reviews
FROM product p JOIN category c USING (category_id) JOIN seller s USING (seller_id)
JOIN inventory i USING (product_id) LEFT JOIN product_review r USING (product_id)
WHERE p.product_id = %s
GROUP BY p.product_id, c.category_name, s.business_name, i.qty_on_hand"""


def product_from_pg(pid):
    cur.execute(PRODUCT_SQL, (pid,))
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone()))


def product_cached(pid):
    k = f"cache:product:{pid}"
    hit = r.get(k)
    if hit is not None:
        return json.loads(hit), True
    doc = product_from_pg(pid)
    r.set(k, json.dumps(doc), ex=600)
    return doc, False


doc, was_hit = product_cached(1)
print(f"first request  -> cache {'HIT' if was_hit else 'MISS'}: loaded from PostgreSQL and stored with TTL 600 s")
doc, was_hit = product_cached(1)
print(f"second request -> cache {'HIT' if was_hit else 'MISS'}: {doc}")

N = 2000
pids = [1 + (i * 37) % 300 for i in range(N)]          # 300 popular products, repeated views
for p in set(pids):
    product_cached(p)                                    # warm the cache
t_pg, t_rd = [], []
for p in pids:
    t = time.perf_counter(); product_from_pg(p); t_pg.append((time.perf_counter() - t) * 1000)
    t = time.perf_counter(); product_cached(p); t_rd.append((time.perf_counter() - t) * 1000)
print(f"\n{N} product-page reads (same products, warm caches on both sides):")
print(f"  PostgreSQL 5-table join : median {statistics.median(t_pg):.3f} ms, p95 {sorted(t_pg)[int(N*.95)]:.3f} ms")
print(f"  Redis GET (cache hit)   : median {statistics.median(t_rd):.3f} ms, p95 {sorted(t_rd)[int(N*.95)]:.3f} ms")
print(f"  => {statistics.median(t_pg) / statistics.median(t_rd):.1f}x faster per read; PostgreSQL is then only hit on a miss.")
print("Invalidation rule: when a seller changes price/stock the application runs DEL cache:product:{id};"
      " the TTL bounds staleness to 10 minutes even if an invalidation is missed.")

# ---------------------------------------------------------------------------
banner("4. FLASH-SALE STOCK COUNTER - atomic Lua script (check + decrement in one step)")
LUA_BUY = """
local left = tonumber(redis.call('GET', KEYS[1]) or '-1')
if left >= tonumber(ARGV[1]) then
  return redis.call('DECRBY', KEYS[1], ARGV[1])
end
return -1
"""
buy = r.register_script(LUA_BUY)
sk = "stock:5001"
r.set(sk, 20)
show(f"SET {sk} 20", "OK")
outcome = []
barrier = threading.Barrier(60)


def buyer():
    c = redis.Redis(host="localhost", port=6379, decode_responses=True)
    s = c.register_script(LUA_BUY)
    barrier.wait()
    outcome.append("sold" if s(keys=[sk], args=[1]) >= 0 else "rejected")


ths = [threading.Thread(target=buyer) for _ in range(60)]
t = time.perf_counter()
[th.start() for th in ths]; [th.join() for th in ths]
print(f"60 concurrent buyers, 20 units: sold={outcome.count('sold')}, rejected={outcome.count('rejected')}, "
      f"stock left={r.get(sk)}  ({(time.perf_counter() - t) * 1000:.1f} ms)")
print("Redis executes each Lua script atomically (single-threaded command execution), so check-and-decrement"
      " cannot interleave - the same guarantee Portfolio 3 obtained with a conditional UPDATE, but without"
      " touching the database. PostgreSQL remains the system of record: successful reservations are written"
      " to orders asynchronously and a failed payment returns the unit with INCRBY.")

# ---------------------------------------------------------------------------
banner("5. REAL-TIME BESTSELLER LEADERBOARD - SORTED SET")
cur.execute("""SELECT oi.product_id, SUM(oi.quantity) FROM orders o JOIN order_item oi USING (order_id)
               WHERE o.order_date >= timestamptz '2026-10-01' AND o.order_status <> 'CANCELLED'
               GROUP BY oi.product_id""")
rows = cur.fetchall()
lk = "rank:bestsellers:2026-10"
pipe = r.pipeline()
for pid, units in rows:
    pipe.zadd(lk, {str(pid): int(units)})
pipe.execute()
print(f"loaded {len(rows)} products into {lk} (one-off backfill from PostgreSQL; afterwards each sale runs ZINCRBY)")
r.zincrby(lk, 3, "42")
show(f"ZINCRBY {lk} 3 42   (a new order for 3 units of product 42)", r.zscore(lk, "42"))
top = r.zrevrange(lk, 0, 4, withscores=True)
show(f"ZREVRANGE {lk} 0 4 WITHSCORES", top)
show(f"ZREVRANK {lk} 42", f"rank #{r.zrevrank(lk, '42') + 1}")

t = time.perf_counter()
for _ in range(200):
    r.zrevrange(lk, 0, 9, withscores=True)
t_z = (time.perf_counter() - t) / 200 * 1000
t = time.perf_counter()
for _ in range(20):
    cur.execute("""SELECT oi.product_id, SUM(oi.quantity) u FROM orders o JOIN order_item oi USING (order_id)
                   WHERE o.order_date >= timestamptz '2026-10-01' AND o.order_status <> 'CANCELLED'
                   GROUP BY oi.product_id ORDER BY u DESC LIMIT 10""")
    sql_top = cur.fetchall()
t_s = (time.perf_counter() - t) / 20 * 1000
print(f"top-10 query: Redis ZREVRANGE {t_z:.3f} ms vs PostgreSQL GROUP BY {t_s:.1f} ms")
print(f"same ranking as SQL (excluding the extra demo sale)? "
      f"{[int(p) for p in r.zrevrange(lk, 0, 2)] == [p for p, _ in sql_top[:3]]}")

# ---------------------------------------------------------------------------
banner("6. Memory footprint")
for k in (key, ck, "cache:product:1", sk, lk):
    print(f"MEMORY USAGE {k:<28} {r.memory_usage(k)} bytes   TYPE {r.type(k)}")
print(f"INFO memory used_memory_human = {r.info('memory')['used_memory_human']}")

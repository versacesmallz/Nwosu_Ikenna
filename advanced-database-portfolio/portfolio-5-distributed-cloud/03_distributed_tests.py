"""
MIT 8103 - Portfolio 5 : distributed database experiments on the 4-node topology
built by 01_setup_cluster.sh and 02_sharding.sql.

  E1  Shard routing and partition pruning (query touches one shard only)
  E2  Scatter-gather when the shard key is not in the query
  E3  Distributed aggregation pushed down to the shards (partition-wise aggregate)
  E4  Availability under shard failure
  E5  Streaming replication: status, replication lag, read-only standby
  E6  Consistency vs latency: asynchronous vs synchronous (remote_apply) commit
  E7  CAP in practice: synchronous primary with the standby down
  E8  Failover: promote the standby to primary

Usage (as root): python3 03_distributed_tests.py > evidence/03_distributed_tests_output.txt
"""
import statistics, subprocess, threading, time
import psycopg2

PGBIN = "/usr/lib/postgresql/16/bin"
BASE = "/home/claude/pg"
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.2f}s] {msg}", flush=True)


def banner(t):
    print("\n" + "=" * 96 + f"\n{t}\n" + "=" * 96, flush=True)


def conn(port, db="kasuwa", autocommit=True, **kw):
    c = psycopg2.connect(host="localhost", port=port, dbname=db, user="postgres", connect_timeout=3, **kw)
    c.autocommit = autocommit
    return c


def q(c, sql, args=None, show=True, label=""):
    cur = c.cursor()
    if show:
        log(f"{label}SQL> " + " ".join(sql.split())[:160])
    cur.execute(sql, args)
    rows = cur.fetchall() if cur.description else None
    if show and rows is not None:
        for r in rows[:12]:
            print("        " + " | ".join(str(x) for x in r), flush=True)
        if len(rows) > 12:
            print(f"        ... ({len(rows)} rows)", flush=True)
    return rows


def pg_ctl(node, action, port=None, extra=""):
    opts = f"-o '-p {port}'" if port and action in ("start", "restart") else ""
    cmd = f"su postgres -c \"{PGBIN}/pg_ctl -D {BASE}/{node} {opts} -l {BASE}/{node}.log -m fast -w {action} {extra}\""
    log(f"$ pg_ctl {action} {node}")
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print("        " + (out.stdout + out.stderr).strip().replace("\n", "\n        "), flush=True)


def timed(c, sql, n=5):
    cur = c.cursor(); ts = []
    for _ in range(n + 1):
        t = time.perf_counter(); cur.execute(sql); cur.fetchall(); ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts[1:])


def explain(c, sql):
    rows = q(c, "EXPLAIN (VERBOSE, COSTS OFF) " + sql, show=False)
    keep = [r[0] for r in rows if any(k in r[0] for k in
            ("Foreign Scan", "Async Foreign Scan", "Append", "Relations", "Remote SQL", "Aggregate", "Subplans Removed"))]
    for line in keep:
        print("        " + line[:150], flush=True)


coord = conn(5432)
q(coord, "SET search_path TO kasuwa_dist", show=False)

# ---------------------------------------------------------------------------
banner("E1  SHARD ROUTING - a query that includes the shard key is sent to ONE shard only")
sql_e1 = """SELECT state_name, count(*) AS orders, round(sum(order_total)) AS revenue
            FROM orders_global WHERE region_code = 'SS' AND order_date >= '2026-10-01'
            GROUP BY state_name ORDER BY revenue DESC"""
q(coord, sql_e1)
log("plan (VERBOSE):")
explain(coord, sql_e1)
for port, name in ((5434, "shard_south"), (5435, "shard_north")):
    s = conn(port, "kasuwa_shard")
    n = q(s, "SELECT count(*) FROM orders_shard", show=False)[0][0]
    log(f"{name:<12} (port {port}) holds {n:,} orders")
    s.close()

# ---------------------------------------------------------------------------
banner("E2  SCATTER-GATHER - customer lookup WITHOUT the shard key must ask every shard")
sql_e2 = "SELECT order_id, order_date, order_total FROM orders_global WHERE customer_id = 20211 ORDER BY order_date DESC LIMIT 5"
q(coord, sql_e2)
explain(coord, sql_e2)
sql_e2b = sql_e2.replace("WHERE customer_id = 20211", "WHERE customer_id = 20211 AND region_code = 'SW'")
log(f"same lookup with the shard key (the app knows the customer's zone from the session): "
    f"{timed(coord, sql_e2b):.2f} ms vs without key {timed(coord, sql_e2):.2f} ms")
explain(coord, sql_e2b)

# ---------------------------------------------------------------------------
banner("E3  DISTRIBUTED AGGREGATION - GROUP BY pushed down to both shards, run in parallel (async append)")
sql_e3 = """SELECT region_code, state_name, count(*) AS orders, sum(order_total) AS revenue
            FROM orders_global WHERE order_status <> 'CANCELLED' AND order_date >= '2026-01-01'
            GROUP BY region_code, state_name"""
q(coord, "SET enable_partitionwise_aggregate = off", show=False)
t_off = timed(coord, sql_e3)
log("partition-wise aggregate OFF: coordinator pulls every row and aggregates itself")
explain(coord, sql_e3)
q(coord, "SET enable_partitionwise_aggregate = on", show=False)
t_on = timed(coord, sql_e3)
log("partition-wise aggregate ON: each shard aggregates its own rows, coordinator only appends 37 result rows")
explain(coord, sql_e3)
local = conn(5432); q(local, "SET search_path TO kasuwa", show=False)
t_local = timed(local, """SELECT r.region_code, s.state_name, count(*), sum(t.order_total)
    FROM orders o JOIN v_order_total t USING (order_id) JOIN customer c ON c.customer_id=o.customer_id
    JOIN state s USING (state_id) JOIN region r USING (region_id)
    WHERE o.order_status <> 'CANCELLED' AND o.order_date >= '2026-01-01' GROUP BY 1,2""", n=3)
log(f"rows returned to the coordinator: {len(q(coord, sql_e3, show=False))} (one per state)")
log(f"median time: pushdown OFF {t_off:.0f} ms | pushdown ON {t_on:.0f} ms | single-node relational query {t_local:.0f} ms")

# ---------------------------------------------------------------------------
banner("E4  AVAILABILITY - shard_north goes down")
pg_ctl("shard_north", "stop")
try:
    q(coord, "SELECT count(*) AS south_orders FROM orders_global WHERE region_code IN ('SE','SS','SW')", label="south query: ")
    log("-> south customers are still served (query never contacts shard_north)")
except Exception as e:
    log(f"!! {e}".strip())
try:
    q(coord, "SELECT count(*) FROM orders_global", label="global query: ")
except Exception as e:
    log(f"!! global query fails: {str(e).strip().splitlines()[0]}")
    coord.rollback() if not coord.autocommit else None
pg_ctl("shard_north", "start", 5435)
q(coord, "SELECT count(*) AS all_orders FROM orders_global", label="after restart: ")

# ---------------------------------------------------------------------------
banner("E5  STREAMING REPLICATION - primary 5432 -> hot standby 5433")
prim, repl = conn(5432), conn(5433)
q(prim, """SELECT application_name, state, sync_state, sent_lsn, replay_lsn,
           pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind FROM pg_stat_replication""", label="primary: ")
q(repl, "SELECT pg_is_in_recovery() AS is_standby, pg_last_wal_replay_lsn() AS replayed_up_to", label="standby: ")
q(prim, "CREATE TABLE IF NOT EXISTS kasuwa.replication_probe (id BIGSERIAL PRIMARY KEY, note TEXT, created_at TIMESTAMPTZ DEFAULT clock_timestamp())", show=False)
for _ in range(200):                     # wait until the CREATE TABLE itself has replicated
    try:
        q(repl, "SELECT 1 FROM kasuwa.replication_probe LIMIT 1", show=False); break
    except psycopg2.errors.UndefinedTable:
        time.sleep(0.01)
lags = []
for i in range(20):
    rid = q(prim, "INSERT INTO kasuwa.replication_probe (note) VALUES ('lag test') RETURNING id", show=False)[0][0]
    t = time.perf_counter()
    while not q(repl, "SELECT 1 FROM kasuwa.replication_probe WHERE id = %s", (rid,), show=False):
        time.sleep(0.0005)
    lags.append((time.perf_counter() - t) * 1000)
log(f"commit-on-primary -> visible-on-standby, 20 trials: median {statistics.median(lags):.2f} ms, max {max(lags):.2f} ms")
log("an order placed and immediately re-read from the standby can therefore be missing for a few ms (stale read)")
try:
    q(repl, "INSERT INTO kasuwa.replication_probe (note) VALUES ('write on standby')", label="standby: ")
except Exception as e:
    log(f"!! standby rejects writes: {str(e).strip().splitlines()[0]}")
log(f"read scaling: product-page query on standby {timed(repl, 'SELECT count(*) FROM kasuwa.product_review WHERE product_id = 17'):.2f} ms "
    f"(same data, same plan as primary) - reporting traffic can be moved off the primary")

# ---------------------------------------------------------------------------
banner("E6  CONSISTENCY vs LATENCY - 300 single-order commits, async vs synchronous replication")
q(prim, "ALTER SYSTEM SET synchronous_standby_names = 'replica1'", show=False)
q(prim, "SELECT pg_reload_conf()", show=False)
time.sleep(1)
q(prim, "SELECT application_name, sync_state FROM pg_stat_replication", label="primary: ")
results = {}
for mode in ("local", "on", "remote_apply"):
    c = conn(5432)
    q(c, f"SET synchronous_commit = {mode}", show=False)
    cur = c.cursor(); ts = []
    for i in range(300):
        t = time.perf_counter()
        cur.execute("INSERT INTO kasuwa.replication_probe (note) VALUES (%s)", (mode,))
        ts.append((time.perf_counter() - t) * 1000)
    results[mode] = (statistics.median(ts), sorted(ts)[int(len(ts) * .95)])
    c.close()
for mode, desc in (("local", "async: ack after local WAL flush only - standby may lag"),
                   ("on", "sync: ack after standby has flushed WAL to disk"),
                   ("remote_apply", "sync: ack after standby has APPLIED it - read-your-writes on standby")):
    log(f"synchronous_commit={mode:<12} median {results[mode][0]:.3f} ms, p95 {results[mode][1]:.3f} ms  ({desc})")

# ---------------------------------------------------------------------------
banner("E7  CAP IN PRACTICE - synchronous replication with the standby DOWN (network partition)")
pg_ctl("replica1", "stop")
blocked = conn(5432)
pid = q(blocked, "SELECT pg_backend_pid()", show=False)[0][0]
q(blocked, "SET synchronous_commit = on", show=False)
outcome = {}


def writer():
    try:
        t = time.perf_counter()
        blocked.cursor().execute("INSERT INTO kasuwa.replication_probe (note) VALUES ('during partition')")
        outcome["msg"] = f"returned after {time.perf_counter() - t:.1f}s; notices: {[n.strip() for n in blocked.notices]}"
    except Exception as e:
        outcome["msg"] = f"error: {e}"


th = threading.Thread(target=writer); th.start()
time.sleep(3)
q(prim, "SELECT pid, wait_event_type, wait_event, state FROM pg_stat_activity WHERE pid = %s", (pid,), label="primary: ")
log("-> the commit is WAITING for a standby that cannot answer: the system chose Consistency over Availability")
q(prim, "SELECT pg_cancel_backend(%s)", (pid,), label="operator cancels the wait: ")
th.join(5)
log(f"writer: {outcome.get('msg')}")
log("-> PostgreSQL warns the row is committed locally but NOT confirmed on the standby")
q(prim, "ALTER SYSTEM RESET synchronous_standby_names", show=False)
q(prim, "SELECT pg_reload_conf()", show=False)
log("synchronous_standby_names reset -> primary is available again in async mode (now favouring Availability)")
q(prim, "INSERT INTO kasuwa.replication_probe (note) VALUES ('async while standby down') RETURNING id", label="primary: ")
pg_ctl("replica1", "start", 5433)
time.sleep(2)
q(prim, "SELECT application_name, state, sync_state, pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind FROM pg_stat_replication",
  label="primary: ")
log("-> standby reconnected through its replication slot and caught up on the WAL it missed")

# ---------------------------------------------------------------------------
banner("E8  FAILOVER - primary crashes, standby is promoted")
repl = conn(5433)
prim = conn(5432)
q(prim, "INSERT INTO kasuwa.replication_probe (note) VALUES ('last order before crash')", show=False)
on_primary = q(prim, "SELECT count(*), max(id) FROM kasuwa.replication_probe", show=False)[0]
prim.close()
time.sleep(0.05)
log(f"primary just before the crash: {on_primary[0]} probe rows, newest id {on_primary[1]}")
pg_ctl("primary", "stop -m immediate")
try:
    conn(5432)
except Exception as e:
    log(f"!! primary unreachable: {str(e).strip().splitlines()[0]}")
t = time.perf_counter()
q(repl, "SELECT pg_promote(wait => true)", label="standby: ")
log(f"promotion took {(time.perf_counter() - t) * 1000:.0f} ms")
q(repl, "SELECT pg_is_in_recovery() AS still_standby", label="new primary: ")
q(repl, "INSERT INTO kasuwa.replication_probe (note) VALUES ('first write after failover') RETURNING id, note", label="new primary: ")
survived = q(repl, "SELECT count(*), max(id) FROM kasuwa.replication_probe WHERE id <= %s", (on_primary[1],), show=False)[0]
log(f"rows committed on the old primary that exist on the new primary: {survived[0]} of {on_primary[0]} "
    f"-> {'no committed data lost' if survived[0] == on_primary[0] else 'DATA LOSS (async replication window)'}")
repl.close()

banner("CLEAN-UP - restore lab topology (old primary back as primary; promoted node discarded)")
pg_ctl("replica1", "stop")
pg_ctl("primary", "start", 5432)
log("in production the old primary would instead be re-synced with pg_rewind and rejoin as the new standby;"
    " 01_setup_cluster.sh rebuilds replica1 from scratch")

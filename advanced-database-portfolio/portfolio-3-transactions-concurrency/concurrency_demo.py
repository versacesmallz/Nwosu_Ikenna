"""
MIT 8103 - Portfolio 3 : concurrency problems and control mechanisms on Kasuwa Marketplace.

Two (or more) real database sessions are interleaved step by step so the anomaly
is reproducible every run. Every SQL statement and its result is logged.

Scenarios
  S1  Lost update (READ COMMITTED, read-modify-write in the application)
  S2  Fix A - pessimistic locking: SELECT ... FOR UPDATE  (2nd buyer waits)
  S3  Fix B - REPEATABLE READ: 2nd writer gets a serialization failure and retries
  S4  Fix C - optimistic locking with a version column
  S5  Isolation levels: non-repeatable read (READ COMMITTED) vs stable snapshot (REPEATABLE READ)
  S6  Deadlock between two multi-item checkouts, and the fix (lock rows in key order)
  S7  Flash-sale stress test: 60 concurrent buyers, 20 units - naive vs atomic conditional UPDATE

Usage: python3 concurrency_demo.py  > evidence/concurrency_demo_output.txt
"""
import threading, time, os
import psycopg2
from psycopg2 import errors
from psycopg2.extensions import (ISOLATION_LEVEL_READ_COMMITTED, ISOLATION_LEVEL_REPEATABLE_READ)

DSN = os.environ.get("KASUWA_DSN", "host=localhost dbname=kasuwa user=postgres")
T0 = time.time()
_print_lock = threading.Lock()


def log(who, msg):
    with _print_lock:
        print(f"[{time.time() - T0:7.3f}s] {who:<4} {msg}", flush=True)


class Session:
    def __init__(self, name, isolation=ISOLATION_LEVEL_READ_COMMITTED):
        self.name = name
        self.conn = psycopg2.connect(DSN)
        self.conn.set_isolation_level(isolation)
        self.cur = self.conn.cursor()
        self.cur.execute("SET search_path TO kasuwa; SET lock_timeout = '10s'")
        self.conn.commit()

    def q(self, sql, args=None, show=True):
        if show:
            log(self.name, sql if args is None else self.cur.mogrify(sql, args).decode())
        self.cur.execute(sql, args)
        rows = self.cur.fetchall() if self.cur.description else None
        if show:
            log(self.name, f"  -> {rows if rows is not None else self.cur.statusmessage}")
        return rows

    def commit(self):
        self.conn.commit(); log(self.name, "COMMIT")

    def rollback(self):
        self.conn.rollback(); log(self.name, "ROLLBACK")

    def close(self):
        self.conn.close()


def setup_product(sku, qty, price=50000):
    s = Session("SETUP")
    s.q("""INSERT INTO product (sku, seller_id, category_id, product_name, unit_price)
           VALUES (%s, 1, 11, %s, %s) ON CONFLICT (sku) DO NOTHING""", (sku, "Demo " + sku, price), show=False)
    pid = s.q("SELECT product_id FROM product WHERE sku = %s", (sku,), show=False)[0][0]
    s.q("""INSERT INTO inventory (product_id, qty_on_hand) VALUES (%s, %s)
           ON CONFLICT (product_id) DO UPDATE SET qty_on_hand = EXCLUDED.qty_on_hand, version = 0""",
        (pid, qty), show=False)
    s.q("DELETE FROM stock_movement WHERE product_id = %s", (pid,), show=False)
    s.conn.commit(); s.close()
    log("SETUP", f"product {sku} (id {pid}) stock reset to {qty}")
    return pid


def stock(pid):
    s = Session("CHK")
    v = s.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,), show=False)[0][0]
    n = s.q("SELECT coalesce(-sum(change_qty),0) FROM stock_movement WHERE product_id=%s AND reason='SALE'",
            (pid,), show=False)[0][0]
    s.close()
    return v, n


def banner(title):
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100, flush=True)


# ---------------------------------------------------------------------------
def s1_lost_update():
    banner("S1  LOST UPDATE - two buyers, READ COMMITTED, application computes new stock (read-modify-write)")
    pid = setup_product("DEMO-P3-LU", 5)
    a, b = Session("T1"), Session("T2")
    qa = a.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,))[0][0]
    qb = b.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,))[0][0]
    a.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (qa - 1, pid))
    a.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    a.commit()
    b.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (qb - 1, pid))
    b.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    b.commit()
    v, sold = stock(pid)
    print(f"RESULT: units sold = {sold}, stock should be {5 - sold}, stock recorded = {v}  "
          f"=> {'LOST UPDATE: one sale vanished from stock' if v != 5 - sold else 'consistent'}")
    a.close(); b.close()


def s2_for_update():
    banner("S2  FIX A - PESSIMISTIC LOCKING with SELECT ... FOR UPDATE")
    pid = setup_product("DEMO-P3-LU", 5)
    a, b = Session("T1"), Session("T2")
    ready = threading.Event()

    def buyer2():
        ready.wait()
        t = time.time()
        q = b.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s FOR UPDATE", (pid,))[0][0]
        log("T2", f"  (waited {time.time() - t:.2f}s for T1's row lock, then read the COMMITTED value {q})")
        b.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (q - 1, pid))
        b.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
        b.commit()

    q = a.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s FOR UPDATE", (pid,))[0][0]
    th = threading.Thread(target=buyer2); th.start(); ready.set()
    time.sleep(1.0)
    obs = Session("OBS")
    obs.q("""SELECT pid, wait_event_type, wait_event, state, left(query, 60) AS query
             FROM pg_stat_activity WHERE datname = 'kasuwa' AND wait_event_type = 'Lock'""")
    obs.close()
    a.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (q - 1, pid))
    a.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    a.commit()
    th.join()
    v, sold = stock(pid)
    print(f"RESULT: units sold = {sold}, stock recorded = {v} (expected {5 - sold}) => "
          f"{'CORRECT - T2 was serialised behind T1' if v == 5 - sold else 'WRONG'}")
    a.close(); b.close()


def s3_repeatable_read():
    banner("S3  FIX B - REPEATABLE READ: concurrent update detected, second transaction must retry")
    pid = setup_product("DEMO-P3-LU", 5)
    a = Session("T1", ISOLATION_LEVEL_REPEATABLE_READ)
    b = Session("T2", ISOLATION_LEVEL_REPEATABLE_READ)
    qa = a.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,))[0][0]
    qb = b.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,))[0][0]
    a.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (qa - 1, pid))
    a.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    a.commit()
    for attempt in (1, 2):
        try:
            if attempt == 2:
                qb = b.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,))[0][0]
            b.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (qb - 1, pid))
            b.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
            b.commit()
            break
        except errors.SerializationFailure as e:
            log("T2", f"  !! SQLSTATE {e.pgcode} {e.pgerror.strip()}")
            b.rollback()
            log("T2", "  application retries the whole transaction with a fresh snapshot")
    v, sold = stock(pid)
    print(f"RESULT: units sold = {sold}, stock recorded = {v} (expected {5 - sold}) => "
          f"{'CORRECT after one retry' if v == 5 - sold else 'WRONG'}")
    a.close(); b.close()


def s4_optimistic():
    banner("S4  FIX C - OPTIMISTIC LOCKING: UPDATE ... WHERE version = <version read>")
    pid = setup_product("DEMO-P3-LU", 5)
    a, b = Session("T1"), Session("T2")
    qa, va = a.q("SELECT qty_on_hand, version FROM inventory WHERE product_id = %s", (pid,))[0]
    a.commit()                                   # no lock held while the user is on the payment page
    qb, vb = b.q("SELECT qty_on_hand, version FROM inventory WHERE product_id = %s", (pid,))[0]
    b.commit()
    a.q("""UPDATE inventory SET qty_on_hand = %s, version = version + 1
           WHERE product_id = %s AND version = %s""", (qa - 1, pid, va))
    a.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    a.commit()
    b.q("""UPDATE inventory SET qty_on_hand = %s, version = version + 1
           WHERE product_id = %s AND version = %s""", (qb - 1, pid, vb))
    if b.cur.rowcount == 0:
        log("T2", "  0 rows updated -> version changed since it was read -> conflict detected, re-read and retry")
        b.rollback()
        qb, vb = b.q("SELECT qty_on_hand, version FROM inventory WHERE product_id = %s", (pid,))[0]
        b.q("""UPDATE inventory SET qty_on_hand = %s, version = version + 1
               WHERE product_id = %s AND version = %s""", (qb - 1, pid, vb))
    b.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s, -1, 'SALE')", (pid,))
    b.commit()
    v, sold = stock(pid)
    print(f"RESULT: units sold = {sold}, stock recorded = {v} (expected {5 - sold}) => "
          f"{'CORRECT - no locks held between read and write' if v == 5 - sold else 'WRONG'}")
    a.close(); b.close()


def s5_isolation():
    banner("S5  ISOLATION LEVELS: finance report reads a price twice while a seller changes it")
    pid = setup_product("DEMO-P3-ISO", 10, price=50000)
    s = Session("SETUP"); s.q("UPDATE product SET unit_price = 50000 WHERE product_id = %s", (pid,), show=False)
    s.conn.commit(); s.close()
    for level, name in ((ISOLATION_LEVEL_READ_COMMITTED, "READ COMMITTED"),
                        (ISOLATION_LEVEL_REPEATABLE_READ, "REPEATABLE READ")):
        print(f"\n--- report transaction at {name} ---", flush=True)
        rpt, seller = Session("RPT", level), Session("SELL")
        p1 = rpt.q("SELECT unit_price FROM product WHERE product_id = %s", (pid,))[0][0]
        seller.q("UPDATE product SET unit_price = unit_price + 5000 WHERE product_id = %s", (pid,))
        seller.commit()
        p2 = rpt.q("SELECT unit_price FROM product WHERE product_id = %s", (pid,))[0][0]
        rpt.commit()
        print(f"RESULT ({name}): first read {p1}, second read {p2} in the SAME transaction => "
              f"{'NON-REPEATABLE READ occurred' if p1 != p2 else 'stable snapshot, no anomaly'}", flush=True)
        rpt.close(); seller.close()


def s6_deadlock():
    banner("S6  DEADLOCK - two baskets containing the same two products, locked in opposite order")
    p1 = setup_product("DEMO-P3-DL1", 50)
    p2 = setup_product("DEMO-P3-DL2", 50)

    def run(order_a, order_b, label):
        print(f"\n--- {label} ---", flush=True)
        a, b = Session("T1"), Session("T2")
        b_has_first = threading.Event()
        outcome = {}

        def attempt(sess, name, order, after_first=None):
            try:
                sess.q("UPDATE inventory SET qty_on_hand = qty_on_hand - 1 WHERE product_id = %s", (order[0],))
                if after_first:
                    after_first()
                sess.q("UPDATE inventory SET qty_on_hand = qty_on_hand - 1 WHERE product_id = %s", (order[1],))
                sess.commit(); outcome[name] = "committed"
            except errors.DeadlockDetected as e:
                log(name, f"  !! SQLSTATE {e.pgcode} {e.pgerror.strip().splitlines()[0]}")
                sess.rollback(); outcome[name] = "DEADLOCK VICTIM - rolled back by PostgreSQL (app would retry)"

        th = threading.Thread(target=attempt, args=(b, "T2", order_b, lambda: (b_has_first.set(), time.sleep(0.5))))
        th.start()
        b_has_first.wait(timeout=2)
        attempt(a, "T1", order_a)
        th.join()
        print(f"RESULT: T1 {outcome['T1']}; T2 {outcome['T2']}", flush=True)
        a.close(); b.close()

    run((p1, p2), (p2, p1), "PROBLEM: T1 locks DL1 then DL2, T2 locks DL2 then DL1")
    run((p1, p2), (p1, p2), "FIX: application always locks basket items in ascending product_id order")


def s7_flash_sale():
    banner("S7  FLASH SALE STRESS TEST - 60 concurrent buyers, 20 units in stock, 1 unit each")
    N_BUYERS, STOCK = 60, 20

    def naive(pid, results, i, barrier):
        s = Session(f"B{i:02d}")
        barrier.wait()
        q = s.q("SELECT qty_on_hand FROM inventory WHERE product_id = %s", (pid,), show=False)[0][0]
        if q >= 1:
            time.sleep(0.01)                                       # time spent calling the payment gateway
            s.q("UPDATE inventory SET qty_on_hand = %s WHERE product_id = %s", (q - 1, pid), show=False)
            s.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s,-1,'SALE')", (pid,), show=False)
            s.conn.commit(); results.append("sold")
        else:
            s.conn.rollback(); results.append("rejected")
        s.close()

    def atomic(pid, results, i, barrier):
        s = Session(f"B{i:02d}")
        barrier.wait()
        rows = s.q("""UPDATE inventory SET qty_on_hand = qty_on_hand - 1
                      WHERE product_id = %s AND qty_on_hand >= 1 RETURNING qty_on_hand""", (pid,), show=False)
        if rows:
            time.sleep(0.01)
            s.q("INSERT INTO stock_movement (product_id, change_qty, reason) VALUES (%s,-1,'SALE')", (pid,), show=False)
            s.conn.commit(); results.append("sold")
        else:
            s.conn.rollback(); results.append("rejected")
        s.close()

    for fn, label in ((naive, "NAIVE: SELECT stock, check in app, UPDATE stock = value_read - 1"),
                      (atomic, "ATOMIC: UPDATE ... SET qty = qty - 1 WHERE qty >= 1 RETURNING (row lock + recheck)")):
        pid = setup_product("DEMO-P3-FLASH", STOCK)
        results, barrier = [], threading.Barrier(N_BUYERS)
        ths = [threading.Thread(target=fn, args=(pid, results, i, barrier)) for i in range(N_BUYERS)]
        t = time.time()
        for th in ths: th.start()
        for th in ths: th.join()
        v, sold = stock(pid)
        print(f"{label}\n   buyers told 'sold' = {results.count('sold')}, rejected = {results.count('rejected')}, "
              f"sale records = {sold}, stock left = {v}, stock should be = {STOCK - sold}, "
              f"oversold units = {max(0, sold - STOCK)}  ({time.time() - t:.2f}s)", flush=True)


if __name__ == "__main__":
    print(f"Kasuwa concurrency demonstrations - {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    s = Session("INFO"); s.q("SELECT version()"); s.q("SHOW default_transaction_isolation"); s.close()
    s1_lost_update(); s2_for_update(); s3_repeatable_read(); s4_optimistic()
    s5_isolation(); s6_deadlock(); s7_flash_sale()

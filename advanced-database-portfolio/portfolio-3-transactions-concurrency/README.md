# Portfolio 3 — Transactions and Concurrency

Case study: **Kasuwa Marketplace**. The critical resource is **stock**: two customers must never both buy the last unit, and a failed payment must never leave half an order behind. PostgreSQL 16 uses MVCC with row-level locks; default isolation is READ COMMITTED.

| File | Purpose |
|---|---|
| [`01_commit_rollback_savepoint.sql`](01_commit_rollback_savepoint.sql) | atomic checkout: COMMIT, ROLLBACK, abort on constraint error, SAVEPOINT |
| [`concurrency_demo.py`](concurrency_demo.py) | real concurrent sessions reproducing anomalies and their fixes (S1–S7) |
| [`evidence/01_commit_rollback_savepoint_output.txt`](evidence/01_commit_rollback_savepoint_output.txt) | full psql transcript |
| [`evidence/concurrency_demo_output.txt`](evidence/concurrency_demo_output.txt) | timestamped log of every statement in every session |

## 1. Atomicity: COMMIT, ROLLBACK, SAVEPOINT

A checkout is one unit of work: insert order → insert lines → reduce stock → write stock movement → record payment.

| Case | What happens | Evidence (from the transcript) |
|---|---|---|
| 1. Successful checkout | all five steps `COMMIT` together | order saved as PAID, total ₦170,000, stock 10 → 8 |
| 2. Payment declined | application issues `ROLLBACK` | inside the transaction stock showed 5; after rollback stock is back to 8, order count unchanged, the order id does not exist |
| 3. Constraint violation | second product would go to −4; `CHECK (qty_on_hand >= 0)` fails | PostgreSQL rejects every later statement ("current transaction is aborted") and turns the `COMMIT` into `ROLLBACK`, so the first, valid update is undone too |
| 4. Partial failure | `SAVEPOINT before_line2` then `ROLLBACK TO SAVEPOINT` | the in-stock line is kept and paid; only the out-of-stock line is discarded |

Case 3 shows the DBMS enforcing atomicity even when the application forgets to roll back. Case 4 shows a design choice: Kasuwa ships what is available rather than cancelling the whole basket.

## 2. Concurrency problems and control mechanisms

Each scenario uses separate database connections whose steps are interleaved deliberately, so the result is the same on every run.

| # | Scenario | Result |
|---|---|---|
| S1 | **Lost update.** Two buyers read stock = 5 under READ COMMITTED; the app writes `value_read − 1` | 2 units sold, stock recorded = 4 (should be 3). One sale vanished. |
| S2 | **Fix A — pessimistic lock** `SELECT … FOR UPDATE` | T2 is shown waiting in `pg_stat_activity` (`wait_event_type = Lock`), waits ~1 s, then reads the committed value 4. Stock = 3. ✔ |
| S3 | **Fix B — REPEATABLE READ** | T2's update fails with `SQLSTATE 40001 could not serialize access due to concurrent update`; the app retries with a fresh snapshot. Stock = 3. ✔ |
| S4 | **Fix C — optimistic lock** (`UPDATE … WHERE version = :v`) | T2's update affects 0 rows, so the conflict is detected without holding any lock while the customer pays; T2 re-reads and retries. Stock = 3. ✔ |
| S5 | **Isolation levels** — a finance report reads a price twice while the seller changes it | READ COMMITTED: 50,000 then 55,000 in the same transaction (non-repeatable read). REPEATABLE READ: same value both times (stable snapshot). |
| S6 | **Deadlock** — two baskets lock the same two products in opposite order | PostgreSQL detects the cycle after `deadlock_timeout` (1 s), aborts T1 with `40P01 deadlock detected`, T2 commits. **Fix:** always lock basket items in ascending `product_id` order → T1 simply waits, both commit. |
| S7 | **Flash-sale stress test** — 60 concurrent buyers, 20 units | **Naive** read-check-write: all 60 buyers told "sold", 60 sale records, stock shows 19 → **40 units oversold**. **Atomic** `UPDATE … SET qty = qty − 1 WHERE qty >= 1 RETURNING`: exactly 20 sold, 40 rejected, stock 0, **0 oversold**. |

The S7 naive result is the important one: the `CHECK (qty_on_hand >= 0)` from Portfolio 1 did **not** prevent overselling, because no write ever produced a negative number — each buyer wrote "19". Constraints protect a single row's value; they do not replace concurrency control.

## 3. Why the atomic UPDATE works

`UPDATE inventory SET qty_on_hand = qty_on_hand − 1 WHERE product_id = $1 AND qty_on_hand >= 1` takes a row lock. Under READ COMMITTED, a second transaction blocked on that row **re-evaluates the WHERE clause against the newly committed version** once the lock is released (PostgreSQL's EvalPlanQual recheck). When stock reaches 0 the condition is false, zero rows are returned, and the buyer is rejected. The read, the check and the write become one statement, so there is no gap for another transaction to slip into.

## 4. Which mechanism Kasuwa uses where

| Situation | Choice | Reason |
|---|---|---|
| Add to basket / checkout of one item | atomic conditional UPDATE | no extra round trip, no retry loop, scales under flash-sale load |
| Multi-item checkout | `SELECT … FOR UPDATE` on all items **ordered by product_id** | consistent lock order prevents deadlocks |
| Seller editing a listing in a form for minutes | optimistic `version` column | holding a DB lock while a human types would block buyers |
| Month-end finance reports | REPEATABLE READ (read-only) | consistent snapshot across many queries without blocking writers |
| Anything at REPEATABLE READ / SERIALIZABLE that writes | must retry on `40001` | these levels detect conflicts instead of preventing them |

## 5. How to run

```bash
psql -h localhost -d kasuwa -e -f 01_commit_rollback_savepoint.sql
pip install psycopg2-binary
python3 concurrency_demo.py > evidence/concurrency_demo_output.txt
```
Timestamps differ between runs; the outcomes are the same each time. The scripts create their own `DEMO-P3-*` products and reset their stock, so they can be re-run.

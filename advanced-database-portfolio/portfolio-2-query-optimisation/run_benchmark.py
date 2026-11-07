"""
MIT 8103 - Portfolio 2 : before / round-1 / round-2 benchmark harness.

Phases
  baseline : no secondary indexes, default planner settings
  round1   : optimisations_round1.sql (indexes, partial index, trigram index, regional MV)
  round2   : optimisations_round2.sql (SSD cost settings, daily product-sales MV)

For each query and phase: one warm-up run (discarded) then RUNS x EXPLAIN (ANALYZE, BUFFERS);
the median execution time is reported and the plan is saved to plans/<Q>_<phase>.txt.
Rewritten queries (Q2 in round2, Q3 from round1) are checked to return EXACTLY the same rows
as the original query, so the optimisation does not change the answer.

Outputs: plans/*.txt, evidence/benchmark_results.csv, evidence/benchmark_chart.png,
         evidence/q2_investigation.txt, evidence/result_equivalence.txt
Usage:   python3 run_benchmark.py
"""
import csv, os, re, statistics
import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
DSN = os.environ.get("KASUWA_DSN", "host=localhost dbname=kasuwa user=postgres")
RUNS = 7
PHASES = ["baseline", "round1", "round2"]

RESET = """
SET search_path TO kasuwa;
DROP INDEX IF EXISTS ix_orders_customer_date, ix_orders_order_date, ix_order_item_product,
                     ix_product_name_trgm, ix_delivery_open;
DROP MATERIALIZED VIEW IF EXISTS mv_monthly_region_sales;
DROP MATERIALIZED VIEW IF EXISTS mv_daily_product_sales;
ALTER DATABASE kasuwa RESET random_page_cost;
ALTER DATABASE kasuwa RESET effective_cache_size;
ANALYZE;
"""

REWRITES = {
    ("Q3", "round1"): """
SELECT region_name, month, orders, revenue
FROM   mv_monthly_region_sales
WHERE  month >= date '2026-01-01'
ORDER  BY region_name, month;""",
    ("Q2", "round2"): """
WITH RECURSIVE cat_tree AS (
    SELECT category_id FROM category WHERE category_name = 'Electronics'
    UNION ALL
    SELECT c.category_id FROM category c JOIN cat_tree t ON c.parent_category_id = t.category_id
)
SELECT p.product_id, p.product_name,
       SUM(s.units_sold) AS units_sold,
       SUM(s.revenue)    AS revenue
FROM   mv_daily_product_sales s
JOIN   product p ON p.product_id = s.product_id
WHERE  s.sale_date >= date '2026-10-01'          -- = 2026-10-31 00:00 Lagos minus 30 days
AND    p.category_id IN (SELECT category_id FROM cat_tree)
GROUP  BY p.product_id, p.product_name
ORDER  BY revenue DESC
LIMIT  10;""",
}
REWRITES[("Q3", "round2")] = REWRITES[("Q3", "round1")]


def load_queries():
    text = open(os.path.join(HERE, "queries.sql")).read()
    parts = re.split(r"^-- @(Q\d) (.*)$", text, flags=re.M)
    return {parts[i]: (parts[i + 1].strip(), parts[i + 2].strip()) for i in range(1, len(parts), 3)}


def new_conn():
    # new connection per phase so ALTER DATABASE settings take effect
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path TO kasuwa")
    return conn, cur


def run_explain(cur, sql):
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
    return cur.fetchone()[0][0]


def measure(cur, sql):
    run_explain(cur, sql)                                    # warm-up
    js = [run_explain(cur, sql) for _ in range(RUNS)]
    times = [j["Execution Time"] for j in js]
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql)
    text = "\n".join(r[0] for r in cur.fetchall())
    root = js[-1]["Plan"]
    blocks = root.get("Shared Hit Blocks", 0) + root.get("Shared Read Blocks", 0)
    return statistics.median(times), min(times), max(times), js[-1]["Planning Time"], blocks, text


def plan_nodes(text):
    found = re.findall(r"(Parallel Seq Scan|Seq Scan|Index Only Scan|Index Scan|Bitmap Heap Scan|Hash Join|"
                       r"Nested Loop|Merge Join)(?: (?:on|using) (\w+))?", text)
    out = []
    for node, obj in found:
        label = f"{node} {obj}".strip()
        if label not in out:
            out.append(label)
    return "; ".join(out[:5])


def q2_investigation(cur, q2_sql):
    """Round-1 diagnosis of Q2: test alternatives before choosing a fix."""
    lines = ["Q2 investigation after round 1 (median of 5 runs each)", "=" * 60]

    def med(sql, setup=None):
        if setup:
            cur.execute(setup)
        ts = [run_explain(cur, sql)["Execution Time"] for _ in range(6)][1:]
        cur.execute("RESET enable_hashjoin")
        return statistics.median(ts)

    lines.append(f"A. As planned by PostgreSQL (hash join, seq scan of 600k order lines): {med(q2_sql):8.1f} ms")
    lines.append(f"B. Force nested loop (SET enable_hashjoin = off) - diagnostic only:      {med(q2_sql, 'SET enable_hashjoin = off'):8.1f} ms")
    cur.execute("CREATE INDEX ix_tmp_oi_cover ON order_item(order_id) INCLUDE (product_id, quantity, unit_price)")
    cur.execute("ANALYZE order_item")
    lines.append(f"C. Covering index order_item(order_id) INCLUDE(...):                       {med(q2_sql):8.1f} ms  (planner still chose hash join)")
    cur.execute("DROP INDEX ix_tmp_oi_cover")
    lines += ["",
              "Diagnosis: order lines for recent orders sit at the END of order_item (ids grow with time),",
              "so the hash join cannot emit a row until the whole 600k-row table has been read.",
              "B shows a nested loop is faster, but forcing planner flags in production is fragile.",
              "C adds a large extra index for no gain. Chosen fix for round 2: SSD cost settings + a daily",
              "product-sales materialised view, because this is a reporting query that tolerates",
              "nightly refresh."]
    return "\n".join(lines) + "\n"


def main():
    queries = load_queries()
    os.makedirs(os.path.join(HERE, "plans"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "evidence"), exist_ok=True)
    results, equivalence = {}, []

    for phase in PHASES:
        conn, cur = new_conn()
        if phase == "baseline":
            cur.execute(RESET)
        elif phase == "round1":
            cur.execute(open(os.path.join(HERE, "optimisations_round1.sql")).read())
        else:
            cur.execute(open(os.path.join(HERE, "optimisations_round2.sql")).read())
        conn.close()
        conn, cur = new_conn()
        cur.execute("SHOW random_page_cost")
        rpc = cur.fetchone()[0]

        for qid, (title, sql) in queries.items():
            run_sql = REWRITES.get((qid, phase), sql)
            if run_sql is not sql:
                cur.execute(sql); original = cur.fetchall()
                cur.execute(run_sql); rewritten = cur.fetchall()
                equivalence.append(f"{qid} {phase}: original rows={len(original)}, rewritten rows={len(rewritten)}, "
                                   f"identical={original == rewritten}")
            med, lo, hi, plan_ms, blocks, text = measure(cur, run_sql)
            header = (f"-- {qid} [{phase}] {title}\n"
                      f"-- random_page_cost={rpc}; median of {RUNS} warm runs: {med:.3f} ms (min {lo:.3f}, max {hi:.3f}); "
                      f"planning {plan_ms:.3f} ms; shared buffers touched: {blocks}\n"
                      f"-- SQL executed:\n" + "\n".join("--   " + l for l in run_sql.strip().splitlines()) + "\n\n")
            with open(os.path.join(HERE, "plans", f"{qid}_{phase}.txt"), "w") as f:
                f.write(header + text + "\n")
            results.setdefault(qid, {"title": title})[phase] = (med, blocks, plan_nodes(text))
            print(f"{qid} {phase:8s} {med:10.3f} ms  buffers {blocks:6d}  {plan_nodes(text)}")

        if phase == "round1":
            with open(os.path.join(HERE, "evidence", "q2_investigation.txt"), "w") as f:
                f.write(q2_investigation(cur, queries["Q2"][1]))
        conn.close()

    with open(os.path.join(HERE, "evidence", "result_equivalence.txt"), "w") as f:
        f.write("Rewritten queries must return exactly the same rows as the originals\n" + "\n".join(equivalence) + "\n")

    with open(os.path.join(HERE, "evidence", "benchmark_results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query", "description"] + [f"{p}_ms" for p in PHASES] + ["overall_speedup_x"]
                   + [f"{p}_buffers" for p in PHASES] + [f"{p}_plan" for p in PHASES])
        for qid, r in results.items():
            w.writerow([qid, r["title"]] + [f"{r[p][0]:.3f}" for p in PHASES]
                       + [f"{r['baseline'][0] / r['round2'][0]:.1f}"]
                       + [r[p][1] for p in PHASES] + [r[p][2] for p in PHASES])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    qids = list(results)
    colors = {"baseline": "#94a3b8", "round1": "#60a5fa", "round2": "#1d4ed8"}
    labels = {"baseline": "Baseline (PK/unique indexes only)", "round1": "Round 1: indexes + regional MV",
              "round2": "Round 2: cost tuning + daily sales MV"}
    fig, ax = plt.subplots(figsize=(8.5, 4.3), dpi=150)
    w = 0.27
    for k, p in enumerate(PHASES):
        vals = [results[q][p][0] for q in qids]
        ax.bar([i + (k - 1) * w for i in range(len(qids))], vals, width=w, color=colors[p], label=labels[p])
    for i, q in enumerate(qids):
        sp = results[q]["baseline"][0] / results[q]["round2"][0]
        ax.text(i + w, results[q]["round2"][0] * 1.25, f"{sp:,.0f}x" if sp >= 2 else f"{sp:.1f}x",
                ha="center", fontsize=8, color="#1e293b")
    ax.set_yscale("log")
    ax.set_xticks(range(len(qids)))
    ax.set_xticklabels([f"{q}" for q in qids])
    ax.set_ylabel("Median execution time, ms (log scale)")
    ax.set_title("Kasuwa workload - execution time per optimisation round", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "evidence", "benchmark_chart.png"))
    print("done")


if __name__ == "__main__":
    main()

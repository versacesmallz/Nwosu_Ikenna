-- =====================================================================
-- MIT 8103 - Portfolio 2 : optimisation ROUND 1 (indexes + one materialised view)
-- Each object is justified against a specific query in queries.sql.
-- Applied by run_benchmark.py between the BEFORE and AFTER runs.
-- =====================================================================
SET search_path TO kasuwa;

-- Q1: composite index supports BOTH the filter (customer_id) and the ORDER BY order_date DESC,
--     so PostgreSQL can read the newest orders of one customer directly.
CREATE INDEX IF NOT EXISTS ix_orders_customer_date ON orders (customer_id, order_date DESC);

-- Q2: range predicate on order_date (last 30 days ~ 8% of orders).
CREATE INDEX IF NOT EXISTS ix_orders_order_date ON orders (order_date);

-- Q2 / Q3 / general: FK column used to join order lines to products.
--     (PostgreSQL does NOT index foreign keys automatically.)
CREATE INDEX IF NOT EXISTS ix_order_item_product ON order_item (product_id);

-- Q3: the dashboard aggregates ~1 year of data each time it loads. Pre-aggregate it.
--     REFRESH ... CONCURRENTLY needs a unique index on the materialised view.
DROP MATERIALIZED VIEW IF EXISTS mv_monthly_region_sales;
CREATE MATERIALIZED VIEW mv_monthly_region_sales AS
SELECT r.region_name,
       date_trunc('month', o.order_date)::date AS month,
       COUNT(DISTINCT o.order_id)              AS orders,
       SUM(oi.quantity * oi.unit_price)        AS revenue
FROM   orders o
JOIN   order_item oi ON oi.order_id = o.order_id
JOIN   customer c    ON c.customer_id = o.customer_id
JOIN   state s       ON s.state_id = c.state_id
JOIN   region r      ON r.region_id = s.region_id
WHERE  o.order_status <> 'CANCELLED'
GROUP  BY r.region_name, date_trunc('month', o.order_date);
CREATE UNIQUE INDEX ux_mv_monthly_region_sales ON mv_monthly_region_sales (region_name, month);

-- Q4: leading-wildcard ILIKE '%...%' can never use a B-tree. A trigram GIN index can.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS ix_product_name_trgm ON product USING gin (product_name gin_trgm_ops);

-- Q5: only ~1% of deliveries are open. A PARTIAL index stores just those rows,
--     so it stays tiny and is cheap to maintain as deliveries close.
CREATE INDEX IF NOT EXISTS ix_delivery_open ON delivery (order_id)
    WHERE dlv_status IN ('PROCESSING','IN_TRANSIT');

ANALYZE;

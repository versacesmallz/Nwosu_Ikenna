-- =====================================================================
-- MIT 8103 - Portfolio 2 : optimisation ROUND 2
-- Written AFTER analysing the round-1 plans (see README section 4):
--   * Q2 did not improve: the plan still hashes all 600k order lines.
--   * Q5 got slower: the planner joins every order instead of probing 3.7k.
-- =====================================================================
SET search_path TO kasuwa;

-- Fix for Q5 (and generally): planner cost model.
-- random_page_cost defaults to 4.0, which models spinning disks. On SSD/cloud
-- storage with the working set in RAM, random reads cost about the same as
-- sequential ones, so index nested-loops were being over-priced.
-- Applied per database (persisted) rather than per session:
ALTER DATABASE kasuwa SET random_page_cost = 1.1;
ALTER DATABASE kasuwa SET effective_cache_size = '4GB';

-- Fix for Q2: pre-aggregate sales per product per day.
-- The 30-day report then sums ~30 small rows per product instead of
-- joining 45k orders to 600k order lines on every run.
DROP MATERIALIZED VIEW IF EXISTS mv_daily_product_sales;
CREATE MATERIALIZED VIEW mv_daily_product_sales AS
SELECT oi.product_id,
       (o.order_date AT TIME ZONE 'Africa/Lagos')::date AS sale_date,
       SUM(oi.quantity)                 AS units_sold,
       SUM(oi.quantity * oi.unit_price) AS revenue
FROM   orders o
JOIN   order_item oi ON oi.order_id = o.order_id
WHERE  o.order_status <> 'CANCELLED'
GROUP  BY oi.product_id, (o.order_date AT TIME ZONE 'Africa/Lagos')::date;
CREATE UNIQUE INDEX ux_mv_daily_product_sales ON mv_daily_product_sales (sale_date, product_id);

ANALYZE;

-- Refresh strategy (both views): run nightly from cron / pg_cron, without
-- blocking readers:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY kasuwa.mv_daily_product_sales;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY kasuwa.mv_monthly_region_sales;
-- Trade-off: dashboards are up to 24 h stale, which management accepted for
-- trend reports. Operational screens (Q1, Q4, Q5) still read live tables.

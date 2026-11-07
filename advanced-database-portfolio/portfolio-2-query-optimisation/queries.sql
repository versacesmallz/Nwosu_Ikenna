-- =====================================================================
-- MIT 8103 - Portfolio 2 : workload queries for Kasuwa Marketplace
-- Five queries that represent real screens / reports of the platform.
-- Each block is separated by a "-- @Qn" marker which run_benchmark.py reads.
-- Reference "today" of the data set = 2026-10-31.
-- =====================================================================

-- @Q1 Customer "My Orders" page: latest 10 orders with totals for one customer (OLTP, runs on every page view)
SELECT o.order_id, o.order_date, o.order_status,
       SUM(oi.quantity * oi.unit_price) AS order_total
FROM   customer c
JOIN   orders o      ON o.customer_id = c.customer_id
JOIN   order_item oi ON oi.order_id   = o.order_id
WHERE  c.email = 'cust20211@mail.kasuwa.ng'
GROUP  BY o.order_id
ORDER  BY o.order_date DESC
LIMIT  10;

-- @Q2 Merchandising report: top 10 Electronics products (whole category tree) by revenue in the last 30 days
WITH RECURSIVE cat_tree AS (
    SELECT category_id FROM category WHERE category_name = 'Electronics'
    UNION ALL
    SELECT c.category_id FROM category c JOIN cat_tree t ON c.parent_category_id = t.category_id
)
SELECT p.product_id, p.product_name,
       SUM(oi.quantity)                 AS units_sold,
       SUM(oi.quantity * oi.unit_price) AS revenue
FROM   orders o
JOIN   order_item oi ON oi.order_id = o.order_id
JOIN   product p     ON p.product_id = oi.product_id
WHERE  o.order_date >= timestamptz '2026-10-31' - interval '30 days'
AND    o.order_status <> 'CANCELLED'
AND    p.category_id IN (SELECT category_id FROM cat_tree)
GROUP  BY p.product_id, p.product_name
ORDER  BY revenue DESC
LIMIT  10;

-- @Q3 Management dashboard: monthly revenue and order count per geopolitical zone for 2026
SELECT r.region_name,
       date_trunc('month', o.order_date)::date AS month,
       COUNT(DISTINCT o.order_id)              AS orders,
       SUM(oi.quantity * oi.unit_price)        AS revenue
FROM   orders o
JOIN   order_item oi ON oi.order_id = o.order_id
JOIN   customer c    ON c.customer_id = o.customer_id
JOIN   state s       ON s.state_id = c.state_id
JOIN   region r      ON r.region_id = s.region_id
WHERE  o.order_date >= timestamptz '2026-01-01'
AND    o.order_status <> 'CANCELLED'
GROUP  BY r.region_name, date_trunc('month', o.order_date)
ORDER  BY r.region_name, month;

-- @Q4 Catalogue search box: customer types "air fryer"
SELECT p.product_id, p.sku, p.product_name, p.unit_price, i.qty_on_hand
FROM   product p
JOIN   inventory i USING (product_id)
WHERE  p.is_active
AND    p.product_name ILIKE '%air fryer%'
ORDER  BY p.unit_price
LIMIT  20;

-- @Q5 Operations: deliveries still open (processing / in transit) for orders older than 3 days
SELECT d.delivery_id, d.order_id, d.courier, d.tracking_no, d.dlv_status, o.order_date
FROM   delivery d
JOIN   orders o ON o.order_id = d.order_id
WHERE  d.dlv_status IN ('PROCESSING','IN_TRANSIT')
AND    o.order_date < timestamptz '2026-10-31' - interval '3 days'
ORDER  BY o.order_date;

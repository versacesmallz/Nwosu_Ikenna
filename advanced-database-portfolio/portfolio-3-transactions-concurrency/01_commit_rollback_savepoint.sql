-- =====================================================================
-- MIT 8103 - Portfolio 3 : COMMIT, ROLLBACK and SAVEPOINT
-- Kasuwa "checkout" as one atomic unit of work:
--   create order -> add lines -> reduce stock -> log stock movement -> record payment
-- Run: psql -h localhost -U postgres -d kasuwa -e -f 01_commit_rollback_savepoint.sql
-- =====================================================================
SET search_path TO kasuwa;
\set ON_ERROR_STOP off

-- Test fixtures: two demo products with known stock
DELETE FROM stock_movement WHERE product_id IN (SELECT product_id FROM product WHERE sku LIKE 'DEMO-P3-%');
DELETE FROM inventory      WHERE product_id IN (SELECT product_id FROM product WHERE sku LIKE 'DEMO-P3-%');
DELETE FROM product        WHERE sku LIKE 'DEMO-P3-%' AND product_id NOT IN (SELECT product_id FROM order_item);
INSERT INTO product (sku, seller_id, category_id, product_name, unit_price)
VALUES ('DEMO-P3-001', 1, 32, 'Demo Air Fryer 5L', 85000.00),
       ('DEMO-P3-002', 1, 13, 'Demo Wireless Earbuds', 25000.00)
ON CONFLICT (sku) DO NOTHING;
INSERT INTO inventory (product_id, qty_on_hand)
SELECT product_id, CASE sku WHEN 'DEMO-P3-001' THEN 10 ELSE 1 END FROM product WHERE sku LIKE 'DEMO-P3-%'
ON CONFLICT (product_id) DO UPDATE SET qty_on_hand = EXCLUDED.qty_on_hand, version = 0;

\echo '=== Stock before any transaction ==='
SELECT p.sku, i.qty_on_hand FROM product p JOIN inventory i USING (product_id) WHERE p.sku LIKE 'DEMO-P3-%' ORDER BY 1;

-- ---------------------------------------------------------------------
\echo '=== CASE 1: successful checkout -> COMMIT ==='
BEGIN;
  WITH new_order AS (
      INSERT INTO orders (customer_id, shipping_address_id, order_status)
      VALUES (101, 101, 'PAID') RETURNING order_id)
  SELECT order_id AS case1_order_id FROM new_order \gset
  INSERT INTO order_item (order_id, line_no, product_id, quantity, unit_price)
  SELECT :case1_order_id, 1, product_id, 2, unit_price FROM product WHERE sku = 'DEMO-P3-001';
  UPDATE inventory SET qty_on_hand = qty_on_hand - 2, updated_at = now()
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');
  INSERT INTO stock_movement (product_id, change_qty, reason, order_id)
  SELECT product_id, -2, 'SALE', :case1_order_id FROM product WHERE sku = 'DEMO-P3-001';
  INSERT INTO payment (order_id, method, amount, pay_status, gateway_ref)
  VALUES (:case1_order_id, 'CARD', 170000.00, 'SUCCESS', 'PSK-DEMO-' || :case1_order_id);
COMMIT;
SELECT o.order_id, o.order_status, t.order_total, i.qty_on_hand AS stock_after
FROM orders o JOIN v_order_total t USING (order_id), inventory i
WHERE o.order_id = :case1_order_id
AND i.product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');

-- ---------------------------------------------------------------------
\echo '=== CASE 2: payment gateway declines -> ROLLBACK (nothing persists) ==='
SELECT count(*) AS orders_before FROM orders \gset
BEGIN;
  WITH new_order AS (
      INSERT INTO orders (customer_id, shipping_address_id) VALUES (102, 102) RETURNING order_id)
  SELECT order_id AS case2_order_id FROM new_order \gset
  INSERT INTO order_item (order_id, line_no, product_id, quantity, unit_price)
  SELECT :case2_order_id, 1, product_id, 3, unit_price FROM product WHERE sku = 'DEMO-P3-001';
  UPDATE inventory SET qty_on_hand = qty_on_hand - 3
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');
  SELECT qty_on_hand AS stock_inside_txn FROM inventory
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');
  -- gateway returns "insufficient funds": application aborts the unit of work
ROLLBACK;
SELECT count(*) AS orders_after, :orders_before AS orders_before,
       (SELECT qty_on_hand FROM inventory WHERE product_id = (SELECT product_id FROM product WHERE sku='DEMO-P3-001')) AS stock_after_rollback,
       (SELECT count(*) FROM orders WHERE order_id = :case2_order_id) AS rolled_back_order_exists
FROM orders;

-- ---------------------------------------------------------------------
\echo '=== CASE 3: constraint violation aborts the whole transaction ==='
BEGIN;
  UPDATE inventory SET qty_on_hand = qty_on_hand - 1
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');   -- succeeds (8 -> 7)
  UPDATE inventory SET qty_on_hand = qty_on_hand - 5
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-002');   -- 1 - 5 < 0 : CHECK fails
  SELECT 'this statement is refused' AS after_error;                              -- txn is now aborted
COMMIT;   -- PostgreSQL turns this COMMIT into a ROLLBACK
SELECT p.sku, i.qty_on_hand FROM product p JOIN inventory i USING (product_id) WHERE p.sku LIKE 'DEMO-P3-%' ORDER BY 1;

-- ---------------------------------------------------------------------
\echo '=== CASE 4: SAVEPOINT - keep the in-stock line, drop only the failed line ==='
BEGIN;
  WITH new_order AS (
      INSERT INTO orders (customer_id, shipping_address_id, order_status)
      VALUES (103, 103, 'PAID') RETURNING order_id)
  SELECT order_id AS case4_order_id FROM new_order \gset
  INSERT INTO order_item (order_id, line_no, product_id, quantity, unit_price)
  SELECT :case4_order_id, 1, product_id, 1, unit_price FROM product WHERE sku = 'DEMO-P3-001';
  UPDATE inventory SET qty_on_hand = qty_on_hand - 1
  WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-001');
  SAVEPOINT before_line2;
    INSERT INTO order_item (order_id, line_no, product_id, quantity, unit_price)
    SELECT :case4_order_id, 2, product_id, 4, unit_price FROM product WHERE sku = 'DEMO-P3-002';
    UPDATE inventory SET qty_on_hand = qty_on_hand - 4                       -- only 1 in stock -> fails
    WHERE product_id = (SELECT product_id FROM product WHERE sku = 'DEMO-P3-002');
  ROLLBACK TO SAVEPOINT before_line2;                                       -- undo line 2 only
  INSERT INTO payment (order_id, method, amount, pay_status, gateway_ref)
  SELECT :case4_order_id, 'TRANSFER', order_total, 'SUCCESS', 'PSK-DEMO-' || :case4_order_id
  FROM v_order_total WHERE order_id = :case4_order_id;
COMMIT;
SELECT oi.order_id, oi.line_no, p.sku, oi.quantity FROM order_item oi JOIN product p USING (product_id)
WHERE oi.order_id = :case4_order_id;
SELECT p.sku, i.qty_on_hand FROM product p JOIN inventory i USING (product_id) WHERE p.sku LIKE 'DEMO-P3-%' ORDER BY 1;

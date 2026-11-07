-- =====================================================================
-- MIT 8103 - Portfolio 1 : constraint verification
-- Each statement below is EXPECTED TO FAIL. The error proves the rule is
-- enforced by the DBMS, not by application code.
-- Run: psql -h localhost -U postgres -d kasuwa -f 03_constraint_tests.sql
-- =====================================================================
SET search_path TO kasuwa;
\set ON_ERROR_STOP off
\echo '--- T1: stock can never go negative (CHECK qty_on_hand >= 0)'
UPDATE inventory SET qty_on_hand = -1 WHERE product_id = 1;

\echo '--- T2: phone must be Nigerian E.164 format (CHECK regex)'
INSERT INTO customer (full_name, email, phone, state_id) VALUES ('Test User','t1@x.ng','08031234567',33);

\echo '--- T3: email is a candidate key (UNIQUE)'
INSERT INTO customer (full_name, email, phone, state_id) VALUES ('Dup User','cust1@mail.kasuwa.ng','+2349999999999',33);

\echo '--- T4: only one default address per customer (partial UNIQUE index)'
INSERT INTO address (customer_id, line1, city, state_id, is_default) VALUES (1,'2 New Road','Ikeja',33,true);

\echo '--- T5: referential integrity - order for a customer that does not exist (FK)'
INSERT INTO orders (customer_id, shipping_address_id) VALUES (999999, 1);

\echo '--- T6: domain of order_status (CHECK IN list)'
UPDATE orders SET order_status = 'LOST' WHERE order_id = 1;

\echo '--- T7: one review per customer per product (composite UNIQUE)'
INSERT INTO product_review (product_id, customer_id, rating)
SELECT product_id, customer_id, 5 FROM product_review LIMIT 1;

\echo '--- T8: rating must be 1..5'
INSERT INTO product_review (product_id, customer_id, rating) VALUES (10, 10, 9);

\echo '--- T9: delivered_at cannot precede dispatched_at (table-level CHECK)'
UPDATE delivery SET delivered_at = dispatched_at - interval '1 day' WHERE delivery_id = 1;

\echo '--- T10: a product cannot be deleted while it appears on orders (FK RESTRICT)'
DELETE FROM product WHERE product_id = 1;

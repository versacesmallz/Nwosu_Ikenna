-- =====================================================================
-- MIT 8103 - Portfolio 1 : synthetic but realistic seed data
-- Kasuwa Marketplace. Deterministic (setseed) so results are reproducible.
-- Volumes: 37 states, 50,000 customers, 400 sellers, 5,000 products,
--          ~300,000 orders, ~600,000 order lines, payments, deliveries.
-- Reference "today" for the data set is 2026-10-31.
-- =====================================================================
SET search_path TO kasuwa;
SELECT setseed(0.42);

-- Geopolitical zones and the 36 states + FCT --------------------------
INSERT INTO region VALUES
 (1,'NC','North Central'),(2,'NE','North East'),(3,'NW','North West'),
 (4,'SE','South East'),(5,'SS','South South'),(6,'SW','South West');

INSERT INTO state (state_id, state_name, region_id) VALUES
 (1,'Benue',1),(2,'Kogi',1),(3,'Kwara',1),(4,'Nasarawa',1),(5,'Niger',1),(6,'Plateau',1),(7,'FCT Abuja',1),
 (8,'Adamawa',2),(9,'Bauchi',2),(10,'Borno',2),(11,'Gombe',2),(12,'Taraba',2),(13,'Yobe',2),
 (14,'Jigawa',3),(15,'Kaduna',3),(16,'Kano',3),(17,'Katsina',3),(18,'Kebbi',3),(19,'Sokoto',3),(20,'Zamfara',3),
 (21,'Abia',4),(22,'Anambra',4),(23,'Ebonyi',4),(24,'Enugu',4),(25,'Imo',4),
 (26,'Akwa Ibom',5),(27,'Bayelsa',5),(28,'Cross River',5),(29,'Delta',5),(30,'Edo',5),(31,'Rivers',5),
 (32,'Ekiti',6),(33,'Lagos',6),(34,'Ogun',6),(35,'Ondo',6),(36,'Osun',6),(37,'Oyo',6);

-- Category hierarchy (parent -> child) --------------------------------
INSERT INTO category VALUES
 (1,'Electronics',NULL),(2,'Fashion',NULL),(3,'Home & Kitchen',NULL),(4,'Groceries',NULL),(5,'Health & Beauty',NULL),
 (11,'Mobile Phones',1),(12,'Laptops',1),(13,'Audio',1),(14,'Power & Solar',1),
 (21,'Men''s Clothing',2),(22,'Women''s Clothing',2),(23,'Footwear',2),
 (31,'Cookware',3),(32,'Small Appliances',3),
 (41,'Grains & Staples',4),(42,'Beverages',4),
 (51,'Skincare',5),(52,'Hair Care',5);

-- Customers: population weighted towards Lagos, Rivers, FCT, Kano ------
WITH first_names AS (SELECT ARRAY['Chinedu','Aisha','Tunde','Ngozi','Ibrahim','Funke','Emeka','Zainab','Segun','Amaka',
                                  'Musa','Bisi','Obinna','Hauwa','Kelechi','Yetunde','Uche','Fatima','Dayo','Chioma',
                                  'Ifeanyi','Halima','Femi','Adaeze','Sani','Tolu','Nnamdi','Maryam','Kunle','Ebere'] a),
     last_names  AS (SELECT ARRAY['Okafor','Bello','Adeyemi','Eze','Abubakar','Ogunleye','Nwachukwu','Usman','Balogun','Okeke',
                                  'Yusuf','Adebayo','Obi','Danjuma','Ibe','Oladipo','Nwosu','Lawal','Ekwueme','Akande',
                                  'Okon','Etim','Ikpe','Diri','Amadi','Wike','Sule','Garba','Onyeka','Ajayi'] a)
INSERT INTO customer (full_name, email, phone, state_id, created_at)
SELECT f.a[1 + (g % 30)] || ' ' || l.a[1 + ((g / 30) % 30)],
       'cust' || g || '@mail.kasuwa.ng',
       '+234' || (8030000000 + g)::text,
       CASE WHEN r < 0.35 THEN 33          -- Lagos
            WHEN r < 0.47 THEN 31          -- Rivers
            WHEN r < 0.57 THEN 7           -- FCT
            WHEN r < 0.64 THEN 16          -- Kano
            ELSE 1 + floor(random()*37)::int END,
       timestamptz '2023-01-01' + (random() * interval '1360 days')
FROM (SELECT g, random() AS r FROM generate_series(1, 50000) g) x, first_names f, last_names l;

INSERT INTO address (customer_id, line1, city, state_id, is_default)
SELECT c.customer_id,
       (1 + floor(random()*120))::int || ' ' ||
       (ARRAY['Aba Road','Allen Avenue','Ahmadu Bello Way','Ikwerre Road','Zoo Road','Ogui Road','Herbert Macaulay Way','Rumuola Road'])[1+floor(random()*8)::int],
       s.state_name || ' City',
       c.state_id,
       true
FROM customer c JOIN state s USING (state_id)
ORDER BY c.customer_id;

-- Sellers --------------------------------------------------------------
INSERT INTO seller (business_name, email, cac_number, state_id, joined_at)
SELECT (ARRAY['Mega','Prime','Royal','Naija','Golden','Unity','Zenith','Crown'])[1+(g%8)] || ' ' ||
       (ARRAY['Gadgets','Stores','Ventures','Enterprises','Mart','Hub','Traders','Concepts'])[1+((g/8)%8)] || ' ' || g,
       'seller' || g || '@biz.kasuwa.ng',
       'RC' || (1000000 + g)::text,
       (ARRAY[33,33,33,31,31,7,16,24,30,37])[1+floor(random()*10)::int],
       date '2022-06-01' + floor(random()*1200)::int
FROM generate_series(1,400) g;

-- Products -------------------------------------------------------------
WITH leaf AS (SELECT array_agg(category_id ORDER BY category_id) ids FROM category WHERE parent_category_id IS NOT NULL)
INSERT INTO product (sku, seller_id, category_id, product_name, unit_price, is_active, created_at)
SELECT 'KSW-' || lpad(g::text, 6, '0'),
       1 + floor(random()*400)::int,
       cat,
       CASE cat
         WHEN 11 THEN (ARRAY['Tecno','Infinix','Samsung','itel','Xiaomi','Apple'])[1+floor(random()*6)::int] || ' Smartphone Model ' || g
         WHEN 12 THEN (ARRAY['HP','Lenovo','Dell','Asus'])[1+floor(random()*4)::int] || ' Laptop ' || g
         WHEN 13 THEN 'Oraimo Bluetooth Earbuds ' || g
         WHEN 14 THEN (ARRAY['Inverter','Solar Panel','Power Bank','Rechargeable Fan'])[1+floor(random()*4)::int] || ' ' || g
         WHEN 21 THEN 'Senator Native Wear ' || g
         WHEN 22 THEN 'Ankara Gown ' || g
         WHEN 23 THEN 'Leather Sandals ' || g
         WHEN 31 THEN 'Non-stick Pot Set ' || g
         WHEN 32 THEN (ARRAY['Blender','Electric Kettle','Air Fryer','Microwave'])[1+floor(random()*4)::int] || ' ' || g
         WHEN 41 THEN (ARRAY['Rice 50kg','Garri 10kg','Beans 25kg','Semovita 10kg'])[1+floor(random()*4)::int] || ' Bag ' || g
         WHEN 42 THEN 'Malt Drink Pack ' || g
         WHEN 51 THEN 'Shea Butter Cream ' || g
         ELSE 'Hair Treatment Kit ' || g END,
       round((CASE WHEN cat IN (11,12,14) THEN 40000 + random()*900000
                   WHEN cat IN (13,32)    THEN 8000  + random()*150000
                   ELSE 1500 + random()*60000 END)::numeric, 2),
       random() > 0.05,
       timestamptz '2023-01-01' + random()*interval '1300 days'
FROM (SELECT g, leaf.ids[1 + floor(random()*array_length(leaf.ids,1))::int] AS cat
      FROM generate_series(1,5000) g, leaf) c;

INSERT INTO inventory (product_id, qty_on_hand)
SELECT product_id, floor(random()*500)::int FROM product;

-- Orders: ~300k over 24 months, growth over time ------------------------
INSERT INTO orders (customer_id, shipping_address_id, order_status, order_date)
SELECT c, c,                                              -- address_id = customer_id (one default address each)
       CASE WHEN d > timestamptz '2026-10-24' THEN
                 (ARRAY['PENDING','PAID','PAID','SHIPPED','SHIPPED','DELIVERED','CANCELLED'])[1+floor(random()*7)::int]
            WHEN random() < 0.06 THEN 'CANCELLED'
            ELSE 'DELIVERED' END,
       d
FROM (SELECT 1 + floor(random()*50000)::bigint AS c,
             timestamptz '2024-11-01' + sqrt(random()) * interval '730 days' AS d   -- sqrt => more recent orders (growth)
      FROM generate_series(1,300000)) s
ORDER BY d;

-- Order lines: 1-3 lines, skewed product popularity (power law)
INSERT INTO order_item (order_id, line_no, product_id, quantity, unit_price)
SELECT o.order_id, l.line_no, p.product_id, 1 + floor(random()*random()*5)::int, p.unit_price
FROM orders o
CROSS JOIN LATERAL generate_series(1, 1 + floor(random()*3)::int + (o.order_id*0)::int) AS l(line_no)
CROSS JOIN LATERAL (SELECT 1 + floor(5000 * power(random(), 2.2))::int + (o.order_id*0 + l.line_no*0)::int AS pid) pick   -- outer refs force per-line evaluation
JOIN product p ON p.product_id = pick.pid
ON CONFLICT (order_id, product_id) DO NOTHING;

-- Payments for all non-pending, non-cancelled orders
INSERT INTO payment (order_id, method, amount, pay_status, gateway_ref, paid_at)
SELECT t.order_id,
       (ARRAY['CARD','CARD','TRANSFER','TRANSFER','USSD','WALLET','POD'])[1+floor(random()*7)::int],
       t.order_total, 'SUCCESS',
       'PSK-' || t.order_id || '-' || substr(md5(t.order_id::text),1,8),
       t.order_date + interval '3 minutes'
FROM v_order_total t
WHERE t.order_status IN ('PAID','SHIPPED','DELIVERED');

-- Deliveries for paid/shipped/delivered orders
INSERT INTO delivery (order_id, courier, tracking_no, dlv_status, dispatched_at, delivered_at)
SELECT o.order_id,
       (ARRAY['GIG Logistics','Kwik Delivery','DHL Nigeria','Red Star Express','Kasuwa Express'])[1+floor(random()*5)::int],
       'TRK' || lpad(o.order_id::text, 10, '0'),
       CASE o.order_status WHEN 'DELIVERED' THEN 'DELIVERED'
                           WHEN 'SHIPPED'   THEN 'IN_TRANSIT'
                           ELSE 'PROCESSING' END,
       CASE WHEN o.order_status IN ('SHIPPED','DELIVERED') THEN o.order_date + interval '1 day' END,
       CASE WHEN o.order_status = 'DELIVERED' THEN o.order_date + interval '1 day' + (1 + floor(random()*6)) * interval '1 day' END
FROM orders o
WHERE o.order_status IN ('PAID','SHIPPED','DELIVERED');

-- Reviews: ~40k (one per customer/product pair)
INSERT INTO product_review (product_id, customer_id, rating, review_text, created_at)
SELECT DISTINCT ON (oi.product_id, o.customer_id)
       oi.product_id, o.customer_id,
       (ARRAY[1,2,3,4,4,5,5,5])[1+floor(random()*8)::int],
       (ARRAY['Value for money','Delivery was fast','Not as described','Works perfectly','Good quality','Seller was responsive'])[1+floor(random()*6)::int],
       o.order_date + interval '10 days'
FROM orders o JOIN order_item oi USING (order_id)
WHERE o.order_status = 'DELIVERED' AND random() < 0.07;

ANALYZE;

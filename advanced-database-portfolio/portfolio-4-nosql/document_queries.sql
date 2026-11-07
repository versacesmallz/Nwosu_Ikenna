-- =====================================================================
-- MIT 8103 - Portfolio 4 (part B): querying the document catalogue
-- Run: psql -h localhost -d kasuwa -f document_queries.sql
-- =====================================================================
SET search_path TO kasuwa_doc;
\timing on

\echo '=== D1  5G phones, RAM >= 8 GB, under N500,000, best rated first ==='
SELECT doc->>'_id' AS sku, doc->>'name' AS name, (doc->>'price')::numeric AS price,
       doc#>>'{attributes,ram_gb}' AS ram_gb, doc#>>'{attributes,storage_gb}' AS storage_gb,
       doc#>>'{rating,avg}' AS rating
FROM   product_catalogue
WHERE  doc @> '{"category":{"name":"Mobile Phones"},"attributes":{"network":["5G"]},"active":true}'
AND    (doc#>>'{attributes,ram_gb}')::int >= 8
AND    (doc->>'price')::numeric < 500000
ORDER  BY (doc#>>'{rating,avg}')::numeric DESC
LIMIT  5;

\echo '=== D2  aggregation: phone count and average price per brand ==='
SELECT doc#>>'{attributes,brand}' AS brand, count(*) AS phones,
       round(avg((doc->>'price')::numeric)) AS avg_price_ngn
FROM   product_catalogue
WHERE  doc @> '{"category":{"name":"Mobile Phones"}}'
GROUP  BY 1 ORDER BY 3 DESC;

\echo '=== D3  Ankara gowns available in size 12 and in Green (array membership) ==='
SELECT doc->>'_id' AS sku, doc->>'name' AS name, doc#>'{attributes,sizes}' AS sizes, doc#>'{attributes,colours}' AS colours
FROM   product_catalogue
WHERE  doc @> '{"category":{"name":"Women''s Clothing"},"attributes":{"sizes":["12"],"colours":["Green"]}}'
LIMIT  5;

\echo '=== D4  embedded data: one read returns product + seller + rating + latest reviews (no joins) ==='
SELECT jsonb_pretty(doc) FROM product_catalogue WHERE sku = 'KSW-000028';

\echo '=== Index: containment queries before/after GIN (jsonb_path_ops) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT sku FROM product_catalogue
WHERE doc @> '{"category":{"name":"Women''s Clothing"},"attributes":{"sizes":["12"],"colours":["Green"]}}';

CREATE INDEX IF NOT EXISTS ix_catalogue_doc ON product_catalogue USING gin (doc jsonb_path_ops);
ANALYZE product_catalogue;

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT sku FROM product_catalogue
WHERE doc @> '{"category":{"name":"Women''s Clothing"},"attributes":{"sizes":["12"],"colours":["Green"]}}';

SELECT pg_size_pretty(pg_relation_size('ix_catalogue_doc')) AS gin_index_size;

\echo '=== Schema flexibility: a new category (Solar Generators) with NEW attributes - no ALTER TABLE ==='
INSERT INTO product_catalogue (sku, doc) VALUES ('KSW-SOLAR-01', '{
  "_id":"KSW-SOLAR-01","name":"Kasuwa 2kVA Solar Generator","price":1250000,"currency":"NGN","active":true,
  "category":{"name":"Solar Generators","path":["Electronics","Power & Solar","Solar Generators"]},
  "seller":{"id":12,"name":"Prime Hub 12","state":"Rivers"},"stock":14,
  "attributes":{"capacity_kva":2,"battery_chemistry":"LiFePO4","battery_wh":2048,"panel_input_w":1200,
                "outputs":{"ac_sockets":4,"usb_c":2},"pure_sine_wave":true},
  "rating":{"avg":0,"count":0},"recent_reviews":[]}')
ON CONFLICT (sku) DO NOTHING;
SELECT doc->>'name' AS name, doc#>>'{attributes,battery_chemistry}' AS chemistry,
       doc#>>'{attributes,outputs,ac_sockets}' AS ac_sockets
FROM product_catalogue WHERE doc @> '{"attributes":{"pure_sine_wave":true}}';

\echo '=== Validation still applies: a document without a numeric price is rejected ==='
INSERT INTO product_catalogue (sku, doc) VALUES ('KSW-BAD-01', '{"_id":"KSW-BAD-01","name":"No price","attributes":{}}');

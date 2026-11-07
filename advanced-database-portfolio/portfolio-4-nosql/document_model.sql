-- =====================================================================
-- MIT 8103 - Portfolio 4 (part B): Document model of the Kasuwa product catalogue
-- Store : PostgreSQL 16 JSONB (binary JSON document store with GIN indexing)
-- Each product becomes ONE self-contained document:
--   identity + price + category path + embedded seller + category-specific
--   attributes (different shape per category) + rating summary + latest reviews
-- Run: psql -h localhost -d kasuwa -f document_model.sql
-- =====================================================================
DROP SCHEMA IF EXISTS kasuwa_doc CASCADE;
CREATE SCHEMA kasuwa_doc;
SET search_path TO kasuwa_doc, kasuwa;

CREATE TABLE product_catalogue (
    sku  TEXT  PRIMARY KEY,                       -- document _id
    doc  JSONB NOT NULL,
    -- coalesce() matters: a missing key gives NULL, and a CHECK that evaluates to NULL PASSES.
    -- (first version without coalesce accepted a document with no price - see README)
    CONSTRAINT doc_has_price      CHECK (coalesce(jsonb_typeof(doc->'price'), 'missing') = 'number'),
    CONSTRAINT doc_has_attributes CHECK (coalesce(jsonb_typeof(doc->'attributes'), 'missing') = 'object')
);

-- Category-specific attributes: deterministic pseudo-random values derived from product_id (h)
INSERT INTO product_catalogue (sku, doc)
SELECT p.sku,
       jsonb_build_object(
         '_id',        p.sku,
         'name',       p.product_name,
         'price',      p.unit_price,
         'currency',   'NGN',
         'active',     p.is_active,
         'category',   jsonb_build_object('name', c.category_name,
                                          'path', jsonb_build_array(pc.category_name, c.category_name)),
         'seller',     jsonb_build_object('id', s.seller_id, 'name', s.business_name, 'state', st.state_name),
         'stock',      i.qty_on_hand,
         'attributes', CASE c.category_id
            WHEN 11 THEN jsonb_build_object(
                    'brand',       split_part(p.product_name, ' ', 1),
                    'ram_gb',      (ARRAY[2,3,4,6,8,12])[1 + h % 6],
                    'storage_gb',  (ARRAY[32,64,128,256,512])[1 + (h / 11) % 5],
                    'battery_mah', (ARRAY[4000,5000,6000,7000])[1 + h % 4],
                    'network',     CASE WHEN (h / 7) % 3 = 0 THEN '["4G","5G"]'::jsonb ELSE '["4G"]'::jsonb END,
                    'dual_sim',    h % 5 <> 0)
            WHEN 12 THEN jsonb_build_object(
                    'brand',  split_part(p.product_name, ' ', 1),
                    'cpu',    (ARRAY['Intel Core i3','Intel Core i5','Intel Core i7','AMD Ryzen 5','AMD Ryzen 7'])[1 + h % 5],
                    'ram_gb', (ARRAY[4,8,16,32])[1 + h % 4],
                    'ssd_gb', (ARRAY[256,512,1024])[1 + h % 3],
                    'screen_in', (ARRAY[13.3,14,15.6])[1 + h % 3])
            WHEN 14 THEN jsonb_build_object(
                    'type',     split_part(p.product_name, ' ', 1),
                    'watts',    (ARRAY[100,300,500,1000,1500,3500])[1 + h % 6],
                    'solar_compatible', h % 2 = 0)
            WHEN 13 THEN jsonb_build_object('bluetooth', '5.' || (h % 4), 'battery_hours', 4 + h % 20, 'noise_cancelling', h % 4 = 0)
            WHEN 32 THEN jsonb_build_object('watts', 300 + (h % 12) * 100, 'capacity_l', 1 + h % 6, 'warranty_months', (ARRAY[6,12,24])[1 + h % 3])
            WHEN 21 THEN jsonb_build_object('sizes', (ARRAY['["M","L","XL"]','["S","M","L"]','["L","XL","XXL"]'])[1 + h % 3]::jsonb,
                                            'colours', (ARRAY['["White","Navy"]','["Green","Black"]','["Wine","Cream"]'])[1 + h % 3]::jsonb,
                                            'material', (ARRAY['Cashmere','Cotton','Linen'])[1 + h % 3])
            WHEN 22 THEN jsonb_build_object('sizes', (ARRAY['["8","10","12"]','["12","14","16"]','["S","M","L","XL"]'])[1 + h % 3]::jsonb,
                                            'colours', (ARRAY['["Green","Orange"]','["Purple","Gold"]','["Blue","Yellow"]'])[1 + h % 3]::jsonb,
                                            'fabric', 'Ankara wax print', 'tailored', h % 2 = 0)
            WHEN 23 THEN jsonb_build_object('sizes', (ARRAY['[40,41,42,43]','[38,39,40]','[42,43,44,45]'])[1 + h % 3]::jsonb,
                                            'material', (ARRAY['Leather','Suede'])[1 + h % 2], 'handmade_in', (ARRAY['Aba','Kano','Lagos'])[1 + h % 3])
            WHEN 41 THEN jsonb_build_object('weight_kg', (ARRAY[5,10,25,50])[1 + h % 4], 'origin_state', (ARRAY['Kebbi','Benue','Ebonyi','Niger','Kano'])[1 + h % 5],
                                            'expiry_months', 6 + h % 18)
            WHEN 42 THEN jsonb_build_object('pack_size', (ARRAY[6,12,24])[1 + h % 3], 'volume_ml', (ARRAY[330,500])[1 + h % 2])
            ELSE         jsonb_build_object('volume_ml', (ARRAY[100,200,250,500])[1 + h % 4],
                                            'skin_or_hair_type', (ARRAY['dry','oily','all','natural hair','relaxed hair'])[1 + h % 5],
                                            'nafdac_no', 'A7-' || lpad((h % 9999)::text, 4, '0'))
         END,
         'rating',     jsonb_build_object('avg', coalesce(rv.avg_rating, 0), 'count', coalesce(rv.n, 0)),
         'recent_reviews', coalesce(rv.latest, '[]'::jsonb)
       )
FROM kasuwa.product p
JOIN kasuwa.category c   ON c.category_id = p.category_id
JOIN kasuwa.category pc  ON pc.category_id = c.parent_category_id
JOIN kasuwa.seller s     ON s.seller_id = p.seller_id
JOIN kasuwa.state st     ON st.state_id = s.state_id
JOIN kasuwa.inventory i  ON i.product_id = p.product_id
CROSS JOIN LATERAL (SELECT abs(hashtext(p.sku)) AS h) hh
LEFT JOIN LATERAL (
    SELECT round(avg(r.rating), 2) AS avg_rating, count(*) AS n,
           (SELECT jsonb_agg(jsonb_build_object('customer_id', x.customer_id, 'rating', x.rating,
                                                'text', x.review_text, 'date', x.created_at::date))
            FROM (SELECT * FROM kasuwa.product_review r2 WHERE r2.product_id = p.product_id
                  ORDER BY r2.created_at DESC LIMIT 3) x) AS latest      -- bounded embedding: only 3 newest
    FROM kasuwa.product_review r WHERE r.product_id = p.product_id
) rv ON true
WHERE p.sku NOT LIKE 'DEMO-%';

ANALYZE product_catalogue;

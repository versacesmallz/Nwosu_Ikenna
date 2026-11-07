-- =====================================================================
-- MIT 8103 - Portfolio 5 : horizontal sharding of Kasuwa orders by geopolitical zone
-- Pattern : coordinator + 2 shards using declarative LIST partitioning whose
--           partitions are FOREIGN TABLES on other servers (postgres_fdw).
-- Shard key: region_code of the customer's home state.
--   shard_south (port 5434) : SE, SS, SW   (Lagos, Rivers, Enugu ... ~70% of customers)
--   shard_north (port 5435) : NC, NE, NW   (FCT, Kano, Kaduna ...)
-- Run on the coordinator: psql -h localhost -p 5432 -d kasuwa -f 02_sharding.sql
-- =====================================================================
\set ON_ERROR_STOP on

-- 1. Create the physical shard tables on each shard server -----------------
\connect kasuwa_shard postgres localhost 5434
DROP TABLE IF EXISTS orders_shard;
CREATE TABLE orders_shard (
    order_id     BIGINT        PRIMARY KEY,
    customer_id  BIGINT        NOT NULL,
    region_code  VARCHAR(10)   NOT NULL CHECK (region_code IN ('SE','SS','SW')),   -- shard owns only its keys
    state_name   VARCHAR(40)   NOT NULL,
    order_date   TIMESTAMPTZ   NOT NULL,
    order_status VARCHAR(12)   NOT NULL,
    order_total  NUMERIC(14,2) NOT NULL
);
CREATE INDEX ON orders_shard (customer_id, order_date DESC);

\connect kasuwa_shard postgres localhost 5435
DROP TABLE IF EXISTS orders_shard;
CREATE TABLE orders_shard (
    order_id     BIGINT        PRIMARY KEY,
    customer_id  BIGINT        NOT NULL,
    region_code  VARCHAR(10)   NOT NULL CHECK (region_code IN ('NC','NE','NW')),
    state_name   VARCHAR(40)   NOT NULL,
    order_date   TIMESTAMPTZ   NOT NULL,
    order_status VARCHAR(12)   NOT NULL,
    order_total  NUMERIC(14,2) NOT NULL
);
CREATE INDEX ON orders_shard (customer_id, order_date DESC);

-- 2. Coordinator: foreign servers + partitioned parent table --------------
\connect kasuwa postgres localhost 5432
CREATE EXTENSION IF NOT EXISTS postgres_fdw;
DROP SCHEMA IF EXISTS kasuwa_dist CASCADE;
DROP SERVER IF EXISTS srv_shard_south CASCADE;
DROP SERVER IF EXISTS srv_shard_north CASCADE;
CREATE SCHEMA kasuwa_dist;

CREATE SERVER srv_shard_south FOREIGN DATA WRAPPER postgres_fdw
       OPTIONS (host 'localhost', port '5434', dbname 'kasuwa_shard', async_capable 'true', fetch_size '10000');
CREATE SERVER srv_shard_north FOREIGN DATA WRAPPER postgres_fdw
       OPTIONS (host 'localhost', port '5435', dbname 'kasuwa_shard', async_capable 'true', fetch_size '10000');
CREATE USER MAPPING FOR postgres SERVER srv_shard_south OPTIONS (user 'postgres');
CREATE USER MAPPING FOR postgres SERVER srv_shard_north OPTIONS (user 'postgres');

CREATE TABLE kasuwa_dist.orders_global (
    order_id     BIGINT        NOT NULL,
    customer_id  BIGINT        NOT NULL,
    region_code  VARCHAR(10)   NOT NULL,
    state_name   VARCHAR(40)   NOT NULL,
    order_date   TIMESTAMPTZ   NOT NULL,
    order_status VARCHAR(12)   NOT NULL,
    order_total  NUMERIC(14,2) NOT NULL
) PARTITION BY LIST (region_code);

CREATE FOREIGN TABLE kasuwa_dist.orders_south PARTITION OF kasuwa_dist.orders_global
       FOR VALUES IN ('SE','SS','SW') SERVER srv_shard_south OPTIONS (schema_name 'public', table_name 'orders_shard');
CREATE FOREIGN TABLE kasuwa_dist.orders_north PARTITION OF kasuwa_dist.orders_global
       FOR VALUES IN ('NC','NE','NW') SERVER srv_shard_north OPTIONS (schema_name 'public', table_name 'orders_shard');

-- 3. Load: the coordinator ROUTES each row to the right shard by its key ----
\timing on
INSERT INTO kasuwa_dist.orders_global
SELECT o.order_id, o.customer_id, r.region_code, s.state_name, o.order_date, o.order_status, t.order_total
FROM   kasuwa.orders o
JOIN   kasuwa.v_order_total t USING (order_id)
JOIN   kasuwa.customer c ON c.customer_id = o.customer_id
JOIN   kasuwa.state s    ON s.state_id = c.state_id
JOIN   kasuwa.region r   ON r.region_id = s.region_id;
\timing off
ANALYZE kasuwa_dist.orders_south;
ANALYZE kasuwa_dist.orders_north;

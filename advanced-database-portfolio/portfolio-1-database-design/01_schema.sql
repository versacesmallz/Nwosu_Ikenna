-- =====================================================================
-- MIT 8103 Advanced Database Systems - Portfolio 1
-- Case study : Kasuwa Marketplace (simulated Nigerian e-commerce platform)
-- File       : 01_schema.sql  - relational schema, keys and constraints
-- Target     : PostgreSQL 16
-- Run        : psql -U postgres -d kasuwa -f 01_schema.sql
-- =====================================================================

DROP SCHEMA IF EXISTS kasuwa CASCADE;
CREATE SCHEMA kasuwa;
SET search_path TO kasuwa;

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------
CREATE TABLE region (
    region_id    SMALLINT     PRIMARY KEY,
    region_code  VARCHAR(10)  NOT NULL UNIQUE,          -- e.g. 'SOUTH', 'NORTH'
    region_name  VARCHAR(60)  NOT NULL
);

CREATE TABLE state (
    state_id     SMALLINT     PRIMARY KEY,
    state_name   VARCHAR(40)  NOT NULL UNIQUE,
    region_id    SMALLINT     NOT NULL REFERENCES region(region_id)
);

CREATE TABLE category (
    category_id         INTEGER      PRIMARY KEY,
    category_name       VARCHAR(80)  NOT NULL UNIQUE,
    parent_category_id  INTEGER      REFERENCES category(category_id)   -- recursive hierarchy
);

-- ---------------------------------------------------------------------
-- Parties
-- ---------------------------------------------------------------------
CREATE TABLE customer (
    customer_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name    VARCHAR(120) NOT NULL,
    email        VARCHAR(160) NOT NULL UNIQUE,                         -- candidate key
    phone        VARCHAR(20)  NOT NULL UNIQUE
                 CHECK (phone ~ '^\+234[0-9]{10}$'),                   -- Nigerian E.164 format
    state_id     SMALLINT     NOT NULL REFERENCES state(state_id),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE address (
    address_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  BIGINT       NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    line1        VARCHAR(160) NOT NULL,
    city         VARCHAR(60)  NOT NULL,
    state_id     SMALLINT     NOT NULL REFERENCES state(state_id),
    is_default   BOOLEAN      NOT NULL DEFAULT false
);
-- business rule: at most ONE default address per customer
CREATE UNIQUE INDEX ux_address_one_default ON address(customer_id) WHERE is_default;

CREATE TABLE seller (
    seller_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_name  VARCHAR(120) NOT NULL,
    email          VARCHAR(160) NOT NULL UNIQUE,
    cac_number     VARCHAR(12)  NOT NULL UNIQUE,                       -- CAC registration no.
    state_id       SMALLINT     NOT NULL REFERENCES state(state_id),
    joined_at      DATE         NOT NULL DEFAULT current_date
);

-- ---------------------------------------------------------------------
-- Catalogue and stock
-- ---------------------------------------------------------------------
CREATE TABLE product (
    product_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku           VARCHAR(20)   NOT NULL UNIQUE,
    seller_id     INTEGER       NOT NULL REFERENCES seller(seller_id),
    category_id   INTEGER       NOT NULL REFERENCES category(category_id),
    product_name  VARCHAR(160)  NOT NULL,
    unit_price    NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),       -- Naira
    is_active     BOOLEAN       NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE TABLE inventory (
    product_id   INTEGER     PRIMARY KEY REFERENCES product(product_id) ON DELETE CASCADE,
    qty_on_hand  INTEGER     NOT NULL CHECK (qty_on_hand >= 0),         -- stock can never go negative
    version      INTEGER     NOT NULL DEFAULT 0,                        -- optimistic-locking counter (Portfolio 3)
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Orders, payments, deliveries
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    order_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id          BIGINT      NOT NULL REFERENCES customer(customer_id),
    shipping_address_id  BIGINT      NOT NULL REFERENCES address(address_id),
    order_status         VARCHAR(12) NOT NULL DEFAULT 'PENDING'
                         CHECK (order_status IN ('PENDING','PAID','SHIPPED','DELIVERED','CANCELLED')),
    order_date           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_item (
    order_id    BIGINT        NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    line_no     SMALLINT      NOT NULL CHECK (line_no > 0),
    product_id  INTEGER       NOT NULL REFERENCES product(product_id),
    quantity    INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),         -- price AT TIME OF SALE (historical fact)
    PRIMARY KEY (order_id, line_no),                                    -- composite key (weak entity)
    UNIQUE (order_id, product_id)                                       -- a product appears once per order
);

CREATE TABLE payment (
    payment_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id      BIGINT        NOT NULL REFERENCES orders(order_id),
    method        VARCHAR(12)   NOT NULL CHECK (method IN ('CARD','TRANSFER','USSD','WALLET','POD')),
    amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    pay_status    VARCHAR(10)   NOT NULL CHECK (pay_status IN ('SUCCESS','FAILED','REFUNDED')),
    gateway_ref   VARCHAR(40)   NOT NULL UNIQUE,                        -- idempotency key from gateway
    paid_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE TABLE delivery (
    delivery_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id       BIGINT       NOT NULL UNIQUE REFERENCES orders(order_id),  -- 1:1 with order
    courier        VARCHAR(40)  NOT NULL,
    tracking_no    VARCHAR(30)  NOT NULL UNIQUE,
    dlv_status     VARCHAR(12)  NOT NULL DEFAULT 'PROCESSING'
                   CHECK (dlv_status IN ('PROCESSING','IN_TRANSIT','DELIVERED','RETURNED')),
    dispatched_at  TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    CHECK (delivered_at IS NULL OR dispatched_at IS NULL OR delivered_at >= dispatched_at)
);

CREATE TABLE product_review (
    review_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id   INTEGER     NOT NULL REFERENCES product(product_id) ON DELETE CASCADE,
    customer_id  BIGINT      NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    rating       SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, customer_id)                                    -- one review per customer per product
);

-- Audit trail used by the transaction exercises in Portfolio 3
CREATE TABLE stock_movement (
    movement_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id   INTEGER     NOT NULL REFERENCES product(product_id),
    change_qty   INTEGER     NOT NULL,
    reason       VARCHAR(20) NOT NULL,
    order_id     BIGINT      REFERENCES orders(order_id),
    moved_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Derived data is computed, not stored (avoids update anomalies)
-- ---------------------------------------------------------------------
CREATE VIEW v_order_total AS
SELECT o.order_id,
       o.customer_id,
       o.order_date,
       o.order_status,
       SUM(oi.quantity * oi.unit_price) AS order_total
FROM   orders o
JOIN   order_item oi USING (order_id)
GROUP  BY o.order_id;

-- NOTE: deliberately only PK/UNIQUE indexes here. Secondary indexes are
-- added and measured in Portfolio 2 so their effect can be evidenced.

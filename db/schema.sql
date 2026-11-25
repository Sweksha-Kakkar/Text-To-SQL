-- TableTalk demo database: an online retail store ("ShopVerse").
-- 10 tables. All money values are in USD.
-- The COMMENTs below are read by the RAG index, so keep them accurate:
-- they are what the model sees when deciding which columns to use.

DROP TABLE IF EXISTS reviews, order_items, orders, inventory, employees,
                     products, warehouses, suppliers, categories, customers CASCADE;

CREATE TABLE customers (
    customer_id   INT PRIMARY KEY,
    first_name    VARCHAR(50)  NOT NULL,
    last_name     VARCHAR(50)  NOT NULL,
    email         VARCHAR(120) NOT NULL UNIQUE,
    city          VARCHAR(60)  NOT NULL,
    country       VARCHAR(60)  NOT NULL,
    signup_date   DATE         NOT NULL,
    loyalty_tier  VARCHAR(10)  NOT NULL
        CHECK (loyalty_tier IN ('bronze', 'silver', 'gold', 'platinum'))
);
COMMENT ON TABLE  customers IS 'People who registered on the store. One row per customer account.';
COMMENT ON COLUMN customers.country      IS 'Country the customer lives in, full English name (e.g. India, USA, UK).';
COMMENT ON COLUMN customers.signup_date  IS 'Date the customer created their account.';
COMMENT ON COLUMN customers.loyalty_tier IS 'Loyalty level based on lifetime spend: bronze < silver < gold < platinum.';

CREATE TABLE categories (
    category_id        INT PRIMARY KEY,
    name               VARCHAR(60) NOT NULL UNIQUE,
    parent_category_id INT REFERENCES categories (category_id)
);
COMMENT ON TABLE  categories IS 'Product categories in a two-level tree. Top-level categories have parent_category_id NULL; products are only assigned to sub-categories.';
COMMENT ON COLUMN categories.parent_category_id IS 'The top-level category this sub-category belongs to. NULL for top-level categories.';

CREATE TABLE suppliers (
    supplier_id    INT PRIMARY KEY,
    name           VARCHAR(100) NOT NULL UNIQUE,
    country        VARCHAR(60)  NOT NULL,
    rating         NUMERIC(2,1) NOT NULL CHECK (rating BETWEEN 1 AND 5),
    contract_start DATE         NOT NULL
);
COMMENT ON TABLE  suppliers IS 'Companies that supply products to the store.';
COMMENT ON COLUMN suppliers.rating IS 'Internal supplier quality score from 1.0 (poor) to 5.0 (excellent).';

CREATE TABLE products (
    product_id  INT PRIMARY KEY,
    name        VARCHAR(120)  NOT NULL UNIQUE,
    category_id INT           NOT NULL REFERENCES categories (category_id),
    supplier_id INT           NOT NULL REFERENCES suppliers (supplier_id),
    unit_price  NUMERIC(10,2) NOT NULL CHECK (unit_price > 0),
    cost_price  NUMERIC(10,2) NOT NULL CHECK (cost_price > 0),
    launched_on DATE          NOT NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE  products IS 'Items the store sells. category_id always points to a sub-category.';
COMMENT ON COLUMN products.unit_price  IS 'Current list price charged to customers, USD.';
COMMENT ON COLUMN products.cost_price  IS 'What the store pays the supplier per unit, USD. Used for profit/margin.';
COMMENT ON COLUMN products.launched_on IS 'Date the product was first available for sale.';
COMMENT ON COLUMN products.is_active   IS 'FALSE when the product is discontinued and no longer sold.';

CREATE TABLE warehouses (
    warehouse_id   INT PRIMARY KEY,
    name           VARCHAR(80) NOT NULL UNIQUE,
    city           VARCHAR(60) NOT NULL,
    country        VARCHAR(60) NOT NULL,
    capacity_units INT         NOT NULL
);
COMMENT ON TABLE  warehouses IS 'Fulfilment centres that store stock and ship orders.';
COMMENT ON COLUMN warehouses.capacity_units IS 'Maximum number of product units the warehouse can hold.';

CREATE TABLE inventory (
    warehouse_id     INT  NOT NULL REFERENCES warehouses (warehouse_id),
    product_id       INT  NOT NULL REFERENCES products (product_id),
    quantity_on_hand INT  NOT NULL CHECK (quantity_on_hand >= 0),
    reorder_level    INT  NOT NULL,
    last_restocked   DATE,
    PRIMARY KEY (warehouse_id, product_id)
);
COMMENT ON TABLE  inventory IS 'Current stock of each product in each warehouse. One row per (warehouse, product) pair that the warehouse carries.';
COMMENT ON COLUMN inventory.quantity_on_hand IS 'Units currently in stock.';
COMMENT ON COLUMN inventory.reorder_level    IS 'When quantity_on_hand falls below this number the item should be restocked.';

CREATE TABLE employees (
    employee_id  INT PRIMARY KEY,
    first_name   VARCHAR(50)   NOT NULL,
    last_name    VARCHAR(50)   NOT NULL,
    job_title    VARCHAR(60)   NOT NULL,
    department   VARCHAR(20)   NOT NULL
        CHECK (department IN ('management', 'sales', 'support', 'warehouse')),
    warehouse_id INT REFERENCES warehouses (warehouse_id),
    manager_id   INT REFERENCES employees (employee_id),
    hire_date    DATE          NOT NULL,
    salary       NUMERIC(10,2) NOT NULL
);
COMMENT ON TABLE  employees IS 'Staff. manager_id points to the employee''s direct manager (NULL only for the CEO).';
COMMENT ON COLUMN employees.warehouse_id IS 'Warehouse the employee works at. NULL for staff who are not based in a warehouse.';
COMMENT ON COLUMN employees.manager_id   IS 'employee_id of this person''s direct manager. Self-reference to employees.';
COMMENT ON COLUMN employees.salary       IS 'Annual salary in USD.';

CREATE TABLE orders (
    order_id       INT PRIMARY KEY,
    customer_id    INT          NOT NULL REFERENCES customers (customer_id),
    handled_by     INT          REFERENCES employees (employee_id),
    warehouse_id   INT          NOT NULL REFERENCES warehouses (warehouse_id),
    order_date     TIMESTAMP    NOT NULL,
    status         VARCHAR(12)  NOT NULL
        CHECK (status IN ('pending', 'processing', 'shipped', 'delivered', 'cancelled', 'returned')),
    payment_method VARCHAR(10)  NOT NULL
        CHECK (payment_method IN ('card', 'upi', 'paypal', 'cod', 'wallet')),
    shipping_cost  NUMERIC(8,2) NOT NULL,
    shipped_date   DATE,
    delivered_date DATE
);
COMMENT ON TABLE  orders IS 'One row per customer order (the order header). Line items are in order_items.';
COMMENT ON COLUMN orders.handled_by     IS 'Sales employee who handled the order. NULL for self-service web orders.';
COMMENT ON COLUMN orders.warehouse_id   IS 'Warehouse the order was shipped from.';
COMMENT ON COLUMN orders.order_date     IS 'Timestamp when the order was placed. Cast to ::date for day-level grouping.';
COMMENT ON COLUMN orders.status         IS 'Order lifecycle state.';
COMMENT ON COLUMN orders.payment_method IS 'cod = cash on delivery, upi = Indian instant bank transfer.';
COMMENT ON COLUMN orders.shipping_cost  IS 'Shipping fee charged to the customer, USD. 0 means free shipping.';
COMMENT ON COLUMN orders.shipped_date   IS 'Date the order left the warehouse. NULL if not shipped yet or cancelled.';
COMMENT ON COLUMN orders.delivered_date IS 'Date the customer received the order. NULL if not delivered yet.';

CREATE TABLE order_items (
    order_item_id INT PRIMARY KEY,
    order_id      INT           NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    product_id    INT           NOT NULL REFERENCES products (product_id),
    quantity      INT           NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,
    discount_pct  NUMERIC(4,1)  NOT NULL DEFAULT 0
);
COMMENT ON TABLE  order_items IS 'Line items: which products were in each order, how many, and at what price.';
COMMENT ON COLUMN order_items.unit_price   IS 'Price per unit at the time of the order (may differ from products.unit_price today).';
COMMENT ON COLUMN order_items.discount_pct IS 'Discount on this line as a percentage, e.g. 10.0 means 10% off.';

CREATE TABLE reviews (
    review_id   INT PRIMARY KEY,
    product_id  INT      NOT NULL REFERENCES products (product_id),
    customer_id INT      NOT NULL REFERENCES customers (customer_id),
    rating      SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_date DATE     NOT NULL,
    review_text TEXT,
    UNIQUE (product_id, customer_id)
);
COMMENT ON TABLE  reviews IS 'Product reviews written by customers after delivery. One review per customer per product.';
COMMENT ON COLUMN reviews.rating IS 'Star rating from 1 (worst) to 5 (best).';

-- Indexes on foreign keys and common filters
CREATE INDEX idx_products_category   ON products (category_id);
CREATE INDEX idx_products_supplier   ON products (supplier_id);
CREATE INDEX idx_inventory_product   ON inventory (product_id);
CREATE INDEX idx_employees_manager   ON employees (manager_id);
CREATE INDEX idx_orders_customer     ON orders (customer_id);
CREATE INDEX idx_orders_date         ON orders (order_date);
CREATE INDEX idx_orders_status       ON orders (status);
CREATE INDEX idx_order_items_order   ON order_items (order_id);
CREATE INDEX idx_order_items_product ON order_items (product_id);
CREATE INDEX idx_reviews_product     ON reviews (product_id);

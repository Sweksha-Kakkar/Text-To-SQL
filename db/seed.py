"""Generate realistic, reproducible demo data for the 10 store tables.

Everything comes from random.Random(SEED), so every run produces the same data,
which keeps the evaluation results comparable between runs.
"""
import random
from datetime import date, datetime, timedelta

SEED = 42
START = date(2024, 1, 1)   # first order date
END = date(2026, 11, 24)   # last order date (the day before the project started)

FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rahul", "Isha",
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Mia", "Lucas", "Sophia",
    "Mohammed", "Fatima", "Omar", "Aisha", "Hans", "Lena", "Felix", "Clara", "Wei", "Mei",
    "Kenji", "Yuki", "Daniel", "Grace", "Samuel", "Chloe", "Ryan", "Zara", "Aditya", "Neha",
]
LAST_NAMES = [
    "Sharma", "Patel", "Iyer", "Khan", "Reddy", "Gupta", "Singh", "Nair", "Mehta", "Das",
    "Smith", "Johnson", "Brown", "Williams", "Jones", "Miller", "Davis", "Wilson", "Taylor", "Clark",
    "Al-Farsi", "Haddad", "Schmidt", "Müller", "Weber", "Fischer", "Chen", "Wang", "Tanaka", "Sato",
    "Kim", "Lee", "Martin", "Thompson", "White", "Lewis", "Walker", "Hall", "Young", "King",
]
COUNTRIES = {  # country: (weight, cities)
    "India": (30, ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune", "Chennai"]),
    "USA": (25, ["New York", "San Francisco", "Chicago", "Austin", "Seattle"]),
    "UK": (10, ["London", "Manchester", "Birmingham"]),
    "Germany": (8, ["Berlin", "Munich", "Hamburg"]),
    "Canada": (8, ["Toronto", "Vancouver"]),
    "Australia": (7, ["Sydney", "Melbourne"]),
    "UAE": (6, ["Dubai", "Abu Dhabi"]),
    "Singapore": (6, ["Singapore"]),
}
WAREHOUSES = [  # id, name, city, country, capacity
    (1, "Mumbai Central DC", "Mumbai", "India", 120000),
    (2, "Bengaluru South DC", "Bengaluru", "India", 90000),
    (3, "New Jersey Hub", "Newark", "USA", 150000),
    (4, "Reno West Hub", "Reno", "USA", 110000),
    (5, "London Gateway", "London", "UK", 80000),
    (6, "Frankfurt EU Hub", "Frankfurt", "Germany", 85000),
]
SHIPS_FROM = {  # customer country -> candidate warehouses
    "India": [1, 2], "UAE": [1], "Singapore": [2], "Australia": [2],
    "USA": [3, 4], "Canada": [3, 4], "UK": [5], "Germany": [6],
}
CATEGORY_TREE = {  # top-level: {sub-category: (price_min, price_max, product nouns)}
    "Electronics": {
        "Smartphones": (199, 1299, ["Phone"]),
        "Laptops": (499, 2499, ["Laptop", "Notebook"]),
        "Audio": (29, 399, ["Earbuds", "Headphones", "Speaker", "Soundbar"]),
        "Accessories": (9, 89, ["Charger", "USB-C Cable", "Power Bank", "Phone Case", "Mouse"]),
    },
    "Home & Kitchen": {
        "Cookware": (19, 249, ["Frying Pan", "Pressure Cooker", "Knife Set", "Dutch Oven"]),
        "Furniture": (79, 1199, ["Office Chair", "Desk", "Bookshelf", "Sofa"]),
        "Appliances": (39, 899, ["Air Fryer", "Blender", "Coffee Maker", "Vacuum"]),
    },
    "Fashion": {
        "Men's Clothing": (15, 149, ["Shirt", "Jeans", "Jacket", "Hoodie"]),
        "Women's Clothing": (15, 179, ["Dress", "Top", "Kurta", "Coat"]),
        "Footwear": (29, 219, ["Sneakers", "Running Shoes", "Boots", "Sandals"]),
    },
    "Sports & Outdoors": {
        "Fitness": (12, 149, ["Yoga Mat", "Dumbbell Set", "Resistance Bands", "Kettlebell"]),
        "Cycling": (299, 1499, ["Road Bike", "Mountain Bike", "Hybrid Bike", "Folding Bike"]),
        "Camping": (19, 399, ["Tent", "Sleeping Bag", "Camp Stove", "Backpack"]),
    },
    "Books": {
        "Fiction": (6, 29, ["Novel", "Thriller", "Fantasy Saga"]),
        "Non-Fiction": (8, 39, ["Biography", "History Book", "Self-Help Guide"]),
        "Technical": (19, 89, ["Python Handbook", "SQL Cookbook", "Algorithms Guide", "Cloud Primer"]),
    },
    "Beauty": {
        "Skincare": (7, 89, ["Face Serum", "Moisturiser", "Sunscreen", "Cleanser"]),
        "Haircare": (6, 69, ["Shampoo", "Conditioner", "Hair Oil", "Hair Dryer"]),
    },
}
BRANDS = ["Nova", "Zenith", "Orbit", "Lumen", "Vertex", "Aurora", "Pulse", "Atlas", "Echo",
          "Nimbus", "Terra", "Kaveri", "Northwind", "Solace", "Ember"]
MODELS = ["Pro", "Max", "Lite", "Air", "Plus", "Ultra", "Mini", "X1", "S2", "Neo", "Classic", "Edge"]
SUPPLIER_WORDS = ["Apex", "Bluefin", "Crescent", "Delta", "Evergreen", "Falcon", "Granite", "Harbor",
                  "Indigo", "Juniper", "Keystone", "Lotus", "Meridian", "Nimbus", "Orion", "Pinnacle",
                  "Quartz", "Redwood", "Summit", "Trident", "Unity", "Vanguard", "Willow", "Zephyr", "Sterling"]
SUPPLIER_SUFFIX = ["Components", "Trading Co.", "Industries", "Supply", "Manufacturing", "Imports", "Goods"]
REVIEW_TEXT = {
    1: ["Stopped working after a week.", "Very poor quality, would not buy again.", "Not as described."],
    2: ["Below expectations.", "Okay-ish but overpriced.", "Packaging was damaged and quality is average."],
    3: ["Does the job.", "Average product for the price.", "Decent, nothing special."],
    4: ["Good value for money.", "Works well, fast delivery.", "Happy with the purchase."],
    5: ["Excellent! Highly recommend.", "Best purchase this year.", "Fantastic quality, exceeded expectations."],
}


def _rand_date(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def generate(seed: int = SEED) -> dict[str, tuple[list[str], list[tuple]]]:
    """Return {table: (column_names, rows)} in foreign-key-safe insert order."""
    rng = random.Random(seed)
    data = {}

    # ---- categories --------------------------------------------------------
    categories, leaf_info = [], {}  # leaf_info[cat_id] = (pmin, pmax, nouns)
    cid = 0
    for top, subs in CATEGORY_TREE.items():
        cid += 1
        top_id = cid
        categories.append((top_id, top, None))
        for sub, info in subs.items():
            cid += 1
            categories.append((cid, sub, top_id))
            leaf_info[cid] = info
    data["categories"] = (["category_id", "name", "parent_category_id"], categories)

    # ---- suppliers ---------------------------------------------------------
    suppliers, used = [], set()
    supplier_countries = ["India", "China", "USA", "Germany", "Vietnam", "Taiwan", "UK", "Japan"]
    for sid in range(1, 26):
        while True:
            name = f"{rng.choice(SUPPLIER_WORDS)} {rng.choice(SUPPLIER_SUFFIX)}"
            if name not in used:
                used.add(name)
                break
        suppliers.append((sid, name, rng.choice(supplier_countries),
                          round(rng.uniform(2.5, 5.0), 1),
                          _rand_date(rng, date(2019, 1, 1), date(2024, 6, 1))))
    data["suppliers"] = (["supplier_id", "name", "country", "rating", "contract_start"], suppliers)

    # ---- products ----------------------------------------------------------
    leaf_suppliers = {c: rng.sample(range(1, 26), 3) for c in leaf_info}
    products, names = [], set()
    leaf_ids = list(leaf_info)
    for pid in range(1, 301):
        cat = leaf_ids[(pid - 1) % len(leaf_ids)] if pid <= len(leaf_ids) else rng.choice(leaf_ids)
        pmin, pmax, nouns = leaf_info[cat]
        while True:
            name = f"{rng.choice(BRANDS)} {rng.choice(nouns)} {rng.choice(MODELS)}"
            if name not in names:
                names.add(name)
                break
        price = round(rng.uniform(pmin, pmax), 0) - 0.01
        cost = round(price * rng.uniform(0.45, 0.8), 2)
        products.append((pid, name, cat, rng.choice(leaf_suppliers[cat]), price, cost,
                         _rand_date(rng, date(2022, 1, 1), date(2026, 9, 1)),
                         rng.random() > 0.08))
    data["products"] = (["product_id", "name", "category_id", "supplier_id", "unit_price",
                         "cost_price", "launched_on", "is_active"], products)
    product_by_id = {p[0]: p for p in products}

    # ---- warehouses & inventory -------------------------------------------
    data["warehouses"] = (["warehouse_id", "name", "city", "country", "capacity_units"], WAREHOUSES)
    inventory = []
    for wid, *_ in WAREHOUSES:
        for pid in sorted(rng.sample(range(1, 301), 210)):
            reorder = rng.randint(20, 80)
            qty = rng.randint(0, reorder - 1) if rng.random() < 0.12 else rng.randint(reorder, 600)
            inventory.append((wid, pid, qty, reorder, _rand_date(rng, date(2026, 6, 1), END)))
    data["inventory"] = (["warehouse_id", "product_id", "quantity_on_hand", "reorder_level",
                          "last_restocked"], inventory)

    # ---- employees (org chart) --------------------------------------------
    employees, eid = [], 0

    def add_emp(title, dept, wh, mgr, salary_range):
        nonlocal eid
        eid += 1
        employees.append((eid, rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES), title, dept, wh, mgr,
                          _rand_date(rng, date(2018, 1, 1), date(2026, 6, 1)),
                          round(rng.uniform(*salary_range), -2)))
        return eid

    ceo = add_emp("Chief Executive Officer", "management", None, None, (240000, 260000))
    head_sales = add_emp("Head of Sales", "management", None, ceo, (150000, 170000))
    head_ops = add_emp("Head of Operations", "management", None, ceo, (150000, 170000))
    head_support = add_emp("Head of Support", "management", None, ceo, (130000, 150000))
    wh_managers = {}
    for wid, *_ in WAREHOUSES:
        wh_managers[wid] = add_emp("Warehouse Manager", "warehouse", wid, head_ops, (70000, 95000))
    for i in range(20):
        wid = WAREHOUSES[i % 6][0]
        add_emp(rng.choice(["Picker", "Packer", "Forklift Operator", "Inventory Clerk"]),
                "warehouse", wid, wh_managers[wid], (32000, 48000))
    sales_reps = [add_emp(rng.choice(["Sales Executive", "Account Manager"]), "sales", None,
                          head_sales, (45000, 85000)) for _ in range(18)]
    for _ in range(12):
        add_emp("Support Agent", "support", None, head_support, (38000, 60000))
    data["employees"] = (["employee_id", "first_name", "last_name", "job_title", "department",
                          "warehouse_id", "manager_id", "hire_date", "salary"], employees)

    # ---- customers ---------------------------------------------------------
    country_names = list(COUNTRIES)
    country_weights = [COUNTRIES[c][0] for c in country_names]
    customers = []
    for cust_id in range(1, 2001):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        country = rng.choices(country_names, country_weights)[0]
        email_last = last.lower().replace("ü", "u").replace("-", "")
        customers.append([cust_id, first, last, f"{first.lower()}.{email_last}{cust_id}@example.com",
                          rng.choice(COUNTRIES[country][1]), country,
                          _rand_date(rng, date(2023, 1, 1), date(2026, 11, 1)), None])

    # ---- orders & order_items ---------------------------------------------
    popularity = {p[0]: 1 / (1 + rng.random() * 8) ** 1.2 for p in products}  # long-tail sales
    active_pids = [p[0] for p in products]
    pid_weights = [popularity[p] for p in active_pids]
    month_weight = {1: 0.55, 2: 0.6, 3: 0.65, 4: 0.65, 5: 0.7, 6: 0.7,
                    7: 0.75, 8: 0.75, 9: 0.8, 10: 0.9, 11: 1.0, 12: 1.0}
    hours = list(range(24))
    hour_weights = [1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 7, 8, 9, 10, 11, 11, 9, 6, 3]
    product_quality = {p[0]: rng.uniform(2.8, 4.8) for p in products}

    orders, items, reviews = [], [], []
    spend = {c[0]: 0.0 for c in customers}
    reviewed = set()
    order_id = item_id = review_id = 0
    for cust in customers:
        cust_id, country, signup = cust[0], cust[5], cust[6]
        if rng.random() < 0.08:
            continue  # ~8% of customers never order
        first_day = max(signup, START)
        if first_day > END:
            continue
        years = ((END - first_day).days + 1) / 365
        n_orders = max(1, int(rng.lognormvariate(0, 0.8) * 3.2 * years))
        dates = []
        while len(dates) < n_orders:
            d = _rand_date(rng, first_day, END)
            growth = 0.6 + 0.4 * (d - START).days / (END - START).days  # business grows over time
            if rng.random() < month_weight[d.month] * growth:
                dates.append(d)
        for d in sorted(dates):
            order_id += 1
            ts = datetime(d.year, d.month, d.day, rng.choices(hours, hour_weights)[0],
                          rng.randint(0, 59), rng.randint(0, 59))
            age = (END - d).days
            if age <= 2:
                status = rng.choice(["pending", "processing"])
            elif age <= 8:
                status = rng.choices(["processing", "shipped", "cancelled"], [2, 6, 1])[0]
            else:
                status = rng.choices(["delivered", "cancelled", "returned"], [85, 8, 7])[0]
            shipped = delivered = None
            if status in ("shipped", "delivered", "returned"):
                shipped = d + timedelta(days=rng.randint(1, 3))
            if status in ("delivered", "returned"):
                far = country not in ("India", "USA", "UK", "Germany")
                delivered = shipped + timedelta(days=rng.randint(4, 10) if far else rng.randint(1, 6))
                if delivered > END:
                    status, delivered = "shipped", None

            if country == "India":
                pay = rng.choices(["upi", "card", "cod", "wallet"], [45, 30, 15, 10])[0]
            else:
                pay = rng.choices(["card", "paypal", "wallet"], [65, 25, 10])[0]

            n_items = rng.choices([1, 2, 3, 4, 5], [45, 30, 15, 7, 3])[0]
            chosen = set()
            subtotal = 0.0
            for _ in range(n_items * 3):  # a few tries to find products launched before the order
                if len(chosen) == n_items:
                    break
                pid = rng.choices(active_pids, pid_weights)[0]
                prod = product_by_id[pid]
                if pid in chosen or prod[6] > d:
                    continue
                chosen.add(pid)
                item_id += 1
                qty = rng.choices([1, 2, 3], [80, 15, 5])[0]
                price = prod[4] if rng.random() < 0.85 else round(prod[4] * rng.choice([0.95, 1.05]), 2)
                disc = rng.choices([0, 5, 10, 15, 20], [70, 10, 10, 6, 4])[0]
                items.append((item_id, order_id, pid, qty, price, disc))
                line = qty * price * (1 - disc / 100)
                subtotal += line
                if status not in ("cancelled", "returned"):
                    spend[cust_id] += line
                if status == "delivered" and (pid, cust_id) not in reviewed and rng.random() < 0.3:
                    reviewed.add((pid, cust_id))
                    review_id += 1
                    rating = min(5, max(1, round(rng.gauss(product_quality[pid], 0.9))))
                    rdate = min(END, delivered + timedelta(days=rng.randint(1, 20)))
                    reviews.append((review_id, pid, cust_id, rating, rdate, rng.choice(REVIEW_TEXT[rating])))
            if not chosen:  # no product existed yet: drop this order
                order_id -= 1
                continue
            shipping = 0.0 if subtotal >= 75 else (5.99 if country in ("India", "USA", "UK", "Germany") else 12.99)
            handled_by = rng.choice(sales_reps) if rng.random() < 0.3 else None
            orders.append((order_id, cust_id, handled_by, rng.choice(SHIPS_FROM[country]), ts,
                           status, pay, shipping, shipped, delivered))

    # loyalty tier from lifetime spend (percentiles)
    ranked = sorted(spend, key=spend.get, reverse=True)
    n = len(ranked)
    tier = {}
    for i, cust_id in enumerate(ranked):
        tier[cust_id] = ("platinum" if i < n * 0.05 else "gold" if i < n * 0.20
                         else "silver" if i < n * 0.50 else "bronze")
    for c in customers:
        c[7] = tier[c[0]] if spend[c[0]] > 0 else "bronze"

    data["customers"] = (["customer_id", "first_name", "last_name", "email", "city", "country",
                          "signup_date", "loyalty_tier"], [tuple(c) for c in customers])
    data["orders"] = (["order_id", "customer_id", "handled_by", "warehouse_id", "order_date", "status",
                       "payment_method", "shipping_cost", "shipped_date", "delivered_date"], orders)
    data["order_items"] = (["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                            "discount_pct"], items)
    data["reviews"] = (["review_id", "product_id", "customer_id", "rating", "review_date",
                        "review_text"], reviews)

    order = ["categories", "suppliers", "products", "warehouses", "inventory", "employees",
             "customers", "orders", "order_items", "reviews"]
    return {t: data[t] for t in order}


if __name__ == "__main__":
    for table, (cols, rows) in generate().items():
        print(f"{table:<12} {len(rows):>7,} rows")

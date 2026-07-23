"""BeanCounter ERP — "Enterprise Resource Punishment".

Self-contained Flask + sqlite3 test app for the Noodle framework.
Deterministic: the DB is rebuilt from seed data on every boot.
Runs on port 4444 (BusterBlock owns 3333).
"""
import os
import sqlite3
import time
from functools import wraps

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "beancounter.db")
PORT = int(os.environ.get("PORT", 4444))
# ponytail: fixed artificial API latency so tests must handle dynamic loading
API_DELAY = float(os.environ.get("BEANCOUNTER_DELAY", "0.3"))

app = Flask(__name__)
app.secret_key = "beancounter-not-a-secret"  # test app only

USERS = [
    ("bean_barry", "Lentils1!", "clerk", "Barry Bean"),
    ("edamame_edna", "Hummus2!", "manager", "Edna Edamame"),
]

PRODUCTS = [
    # sku, name, category, qty, unit_price, reorder_level, supplier
    ("BC-1001", "Arabica Beans 1kg", "Coffee", 120, 18.50, 40, "Java the Hutt Co."),
    ("BC-1002", "Robusta Beans 1kg", "Coffee", 85, 12.75, 40, "Java the Hutt Co."),
    ("BC-1003", "Decaf Beans 1kg", "Coffee", 15, 16.00, 30, "Sleepy Sipper Ltd."),
    ("BC-1004", "Espresso Blend 500g", "Coffee", 200, 11.25, 50, "Java the Hutt Co."),
    ("BC-2001", "Pinto Beans 25lb", "Legume", 340, 32.00, 100, "Full of Beans Inc."),
    ("BC-2002", "Black Beans 25lb", "Legume", 280, 34.50, 100, "Full of Beans Inc."),
    ("BC-2003", "Kidney Beans 25lb", "Legume", 95, 33.00, 100, "Full of Beans Inc."),
    ("BC-2004", "Chickpeas 25lb", "Legume", 410, 29.75, 120, "Hummus Among Us"),
    ("BC-2005", "Lentils Red 25lb", "Legume", 22, 27.50, 80, "Hummus Among Us"),
    ("BC-2006", "Edamame Frozen 10lb", "Legume", 60, 24.00, 30, "Pod Save the Bean"),
    ("BC-3001", "Jelly Beans Assorted 5kg", "Confectionery", 75, 42.00, 25, "Sugar Rush GmbH"),
    ("BC-3002", "Jelly Beans Sour 5kg", "Confectionery", 8, 44.50, 25, "Sugar Rush GmbH"),
    ("BC-3003", "Chocolate Beans 5kg", "Confectionery", 130, 51.00, 40, "Sugar Rush GmbH"),
    ("BC-4001", "Cocoa Beans Raw 10kg", "Cocoa", 55, 88.00, 20, "Theobroma Bros."),
    ("BC-4002", "Cocoa Nibs 5kg", "Cocoa", 42, 64.25, 20, "Theobroma Bros."),
    ("BC-4003", "Cocoa Butter 5kg", "Cocoa", 5, 97.50, 15, "Theobroma Bros."),
    ("BC-5001", "Vanilla Beans Grade A 100ct", "Spice", 30, 145.00, 10, "Pod Save the Bean"),
    ("BC-5002", "Tonka Beans 1kg", "Spice", 12, 118.00, 10, "Pod Save the Bean"),
    ("BC-9001", "Bean Bag Chair XL", "Misc", 18, 79.99, 5, "Sit Happens Ltd."),
    ("BC-9002", "Mr. Bean Box Set DVD", "Misc", 3, 19.99, 2, "Retro Rentals"),
]

ORDERS = [
    # order_no, customer, product_id (1-based row in PRODUCTS), qty, status, order_date
    ("SO-0001", "Cafe Ole", 1, 20, "delivered", "2026-06-02"),
    ("SO-0002", "Burrito Barn", 5, 40, "delivered", "2026-06-05"),
    ("SO-0003", "Sweet Tooth Emporium", 11, 10, "shipped", "2026-06-11"),
    ("SO-0004", "Cafe Ole", 4, 35, "delivered", "2026-06-12"),
    ("SO-0005", "Choc Full o' Joy", 14, 6, "pending", "2026-06-18"),
    ("SO-0006", "Hummus Hut", 8, 55, "shipped", "2026-06-20"),
    ("SO-0007", "Burrito Barn", 6, 30, "pending", "2026-06-24"),
    ("SO-0008", "Gelato Galaxy", 13, 12, "cancelled", "2026-06-25"),
    ("SO-0009", "Cafe Ole", 2, 25, "pending", "2026-07-01"),
    ("SO-0010", "Spice World", 17, 4, "shipped", "2026-07-03"),
    ("SO-0011", "Hummus Hut", 9, 60, "pending", "2026-07-06"),
    ("SO-0012", "Lounge Lizards Inc.", 19, 3, "delivered", "2026-07-08"),
]

EMPLOYEES = [
    ("Barry Bean", "Warehouse", "Inventory Clerk", "barry@beancounter.example", 52000, "2021-03-15"),
    ("Edna Edamame", "Operations", "Ops Manager", "edna@beancounter.example", 88000, "2019-08-01"),
    ("Gary Garbanzo", "Sales", "Account Exec", "gary@beancounter.example", 61000, "2022-01-10"),
    ("Fava Flav", "Sales", "Sales Rep", "fava@beancounter.example", 48000, "2023-05-22"),
    ("Pinto Paloma", "Warehouse", "Forklift Operator", "pinto@beancounter.example", 45000, "2020-11-30"),
    ("Lima Lars", "Finance", "Bean Counter", "lars@beancounter.example", 72000, "2018-02-14"),
    ("Cocoa Chanel", "Purchasing", "Buyer", "cocoa@beancounter.example", 65000, "2021-09-07"),
    ("Snap Pea Steve", "IT", "Sysadmin", "steve@beancounter.example", 76000, "2022-06-13"),
]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)  # deterministic seed every boot
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
                            role TEXT, display_name TEXT);
        CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT UNIQUE, name TEXT,
                               category TEXT, qty INTEGER, unit_price REAL,
                               reorder_level INTEGER, supplier TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, customer TEXT,
                             product_id INTEGER REFERENCES products(id), qty INTEGER,
                             status TEXT CHECK(status IN ('pending','shipped','delivered','cancelled')),
                             order_date TEXT);
        CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, dept TEXT, title TEXT,
                                email TEXT, salary REAL, hired TEXT);
    """)
    db.executemany("INSERT INTO users(username,password,role,display_name) VALUES (?,?,?,?)", USERS)
    db.executemany("INSERT INTO products(sku,name,category,qty,unit_price,reorder_level,supplier) "
                   "VALUES (?,?,?,?,?,?,?)", PRODUCTS)
    db.executemany("INSERT INTO orders(order_no,customer,product_id,qty,status,order_date) "
                   "VALUES (?,?,?,?,?,?)", ORDERS)
    db.executemany("INSERT INTO employees(name,dept,title,email,salary,hired) "
                   "VALUES (?,?,?,?,?,?)", EMPLOYEES)
    db.commit()
    db.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def api_auth(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify(error="authentication required"), 401
        time.sleep(API_DELAY)  # simulate real-world latency for dynamic-loading tests
        return fn(*a, **kw)
    return wrapper


# ---------- pages ----------

@app.route("/")
def home():
    return redirect(url_for("dashboard" if "user" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        row = get_db().execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (request.form.get("username", ""), request.form.get("password", ""))).fetchone()
        if row:
            session["user"] = row["username"]
            session["display_name"] = row["display_name"]
            session["role"] = row["role"]
            return redirect(url_for("dashboard"))
        error = "Invalid credentials. The beans remain uncounted."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", page="dashboard")


@app.route("/inventory")
@login_required
def inventory():
    return render_template("inventory.html", page="inventory")


@app.route("/orders")
@login_required
def orders():
    return render_template("orders.html", page="orders")


@app.route("/employees")
@login_required
def employees():
    return render_template("employees.html", page="employees")


# ---------- API ----------

@app.post("/api/login")
def api_login():
    body = request.get_json(silent=True) or {}
    row = get_db().execute("SELECT * FROM users WHERE username=? AND password=?",
                           (body.get("username", ""), body.get("password", ""))).fetchone()
    if not row:
        return jsonify(error="invalid credentials"), 401
    session["user"] = row["username"]
    session["display_name"] = row["display_name"]
    session["role"] = row["role"]
    return jsonify(username=row["username"], role=row["role"])


@app.get("/api/stats")
@api_auth
def api_stats():
    db = get_db()
    revenue = db.execute("""
        SELECT p.category AS label, ROUND(SUM(o.qty * p.unit_price), 2) AS value
        FROM orders o JOIN products p ON p.id = o.product_id
        WHERE o.status != 'cancelled'
        GROUP BY p.category ORDER BY value DESC""").fetchall()
    status = db.execute(
        "SELECT status AS label, COUNT(*) AS value FROM orders GROUP BY status ORDER BY value DESC"
    ).fetchall()
    kpis = db.execute("""
        SELECT (SELECT ROUND(SUM(o.qty * p.unit_price), 2) FROM orders o
                JOIN products p ON p.id = o.product_id WHERE o.status != 'cancelled') AS revenue,
               (SELECT COUNT(*) FROM orders WHERE status = 'pending') AS open_orders,
               (SELECT COUNT(*) FROM products WHERE qty <= reorder_level) AS low_stock,
               (SELECT COUNT(*) FROM employees) AS headcount""").fetchone()
    return jsonify(revenue_by_category=[dict(r) for r in revenue],
                   orders_by_status=[dict(r) for r in status], kpis=dict(kpis))


@app.get("/api/products")
@api_auth
def api_products():
    rows = get_db().execute("SELECT * FROM products ORDER BY sku").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/products")
@api_auth
def api_product_create():
    b = request.get_json(silent=True) or {}
    required = ("sku", "name", "category")
    if any(not str(b.get(k, "")).strip() for k in required):
        return jsonify(error="sku, name and category are required"), 400
    try:
        cur = get_db().execute(
            "INSERT INTO products(sku,name,category,qty,unit_price,reorder_level,supplier) "
            "VALUES (?,?,?,?,?,?,?)",
            (b["sku"], b["name"], b["category"], int(b.get("qty", 0)),
             float(b.get("unit_price", 0)), int(b.get("reorder_level", 0)),
             b.get("supplier", "")))
        get_db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error=f"sku {b['sku']} already exists"), 409
    except (ValueError, TypeError):
        return jsonify(error="qty, unit_price and reorder_level must be numeric"), 400
    row = get_db().execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


EDITABLE_PRODUCT_COLS = {"name", "category", "qty", "unit_price", "reorder_level", "supplier"}


@app.put("/api/products/<int:pid>")
@api_auth
def api_product_update(pid):
    b = {k: v for k, v in (request.get_json(silent=True) or {}).items()
         if k in EDITABLE_PRODUCT_COLS}
    if not b:
        return jsonify(error="no editable fields supplied"), 400
    try:
        for k in ("qty", "reorder_level"):
            if k in b:
                b[k] = int(b[k])
        if "unit_price" in b:
            b["unit_price"] = float(b["unit_price"])
    except (ValueError, TypeError):
        return jsonify(error="qty, unit_price and reorder_level must be numeric"), 400
    sets = ", ".join(f"{k}=?" for k in b)  # keys whitelisted above
    cur = get_db().execute(f"UPDATE products SET {sets} WHERE id=?", (*b.values(), pid))
    get_db().commit()
    if cur.rowcount == 0:
        return jsonify(error="product not found"), 404
    row = get_db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(dict(row))


@app.delete("/api/products/<int:pid>")
@api_auth
def api_product_delete(pid):
    cur = get_db().execute("DELETE FROM products WHERE id=?", (pid,))
    get_db().commit()
    if cur.rowcount == 0:
        return jsonify(error="product not found"), 404
    return jsonify(deleted=pid)


@app.get("/api/orders")
@api_auth
def api_orders():
    rows = get_db().execute("""
        SELECT o.id, o.order_no, o.customer, p.sku, p.name AS product, o.qty,
               ROUND(o.qty * p.unit_price, 2) AS total, o.status, o.order_date
        FROM orders o JOIN products p ON p.id = o.product_id
        ORDER BY o.order_no""").fetchall()
    return jsonify([dict(r) for r in rows])


@app.patch("/api/orders/<int:oid>")
@api_auth
def api_order_update(oid):
    status = (request.get_json(silent=True) or {}).get("status", "")
    if status not in ("pending", "shipped", "delivered", "cancelled"):
        return jsonify(error="status must be pending|shipped|delivered|cancelled"), 400
    cur = get_db().execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    get_db().commit()
    if cur.rowcount == 0:
        return jsonify(error="order not found"), 404
    return jsonify(id=oid, status=status)


@app.get("/api/employees")
@api_auth
def api_employees():
    rows = get_db().execute("SELECT * FROM employees ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    print(f"\n🫘  BeanCounter ERP  →  http://localhost:{PORT}")
    print(f"    login: {USERS[0][0]} / {USERS[0][1]}  (or {USERS[1][0]} / {USERS[1][1]})\n")
    app.run(port=PORT)

# 🫘 BeanCounter ERP

*Enterprise Resource Punishment.* A self-contained Flask + SQLite ERP test app
for the Noodle framework — the enterprise sibling of BusterBlock (`test-app/`).
They literally sell beans.

## Run

```bash
cd test-app-erp
pip install -r requirements.txt   # or: uv run --with flask app.py
python app.py
```

→ http://localhost:4444 (BusterBlock owns 3333)

The SQLite DB (`data/beancounter.db`) is **rebuilt from seed data on every
boot**, so tests always start from the same state.

## Logins

| user | password | role |
|---|---|---|
| `bean_barry` | `Lentils1!` | clerk |
| `edamame_edna` | `Hummus2!` | manager |

## Test surface

- **Login** — form auth, session cookie, error banner on bad creds.
- **Dashboard** — KPI tiles, SVG bar + donut charts, loading spinner
  (every `/api/*` call sleeps `BEANCOUNTER_DELAY` seconds, default 0.3,
  so dynamic-loading waits are exercised).
- **Inventory** — Excel-style grid: column letters, row numbers, sticky
  header, click-to-sort, filter box, footer totals, **double-click cell
  editing** (Enter saves, Esc cancels), add-row form, delete buttons,
  LOW-stock badges.
- **Orders** — sortable/filterable grid, per-row status dropdown that
  PATCHes immediately.
- **Employees** — read-only grid with salary totals.
- Every interesting element has a `data-testid`.

## API

All endpoints (except login) require a session and return JSON; 401 otherwise.

```
POST   /api/login                {username, password}
GET    /api/stats
GET    /api/products
POST   /api/products             {sku*, name*, category*, qty, unit_price, reorder_level, supplier}
PUT    /api/products/<id>        any editable subset
DELETE /api/products/<id>
GET    /api/orders
PATCH  /api/orders/<id>          {status: pending|shipped|delivered|cancelled}
GET    /api/employees
```

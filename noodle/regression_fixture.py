"""NOOD_0227 (SC-3) — the benchmark's self-contained multi-page shop.

The 65.9-AIC blowout shipped because nothing in `noodle benchmark --gate`
covered the single most common shape in web test automation: a multi-page
flow with a per-card row-scoped click and a same-URL DOM-mutation panel.
The live-site cases can't host it — retail sites bot-gate, and the benchmark
must go green from any machine — so this fixture IS that shape, four static
pages served from an ephemeral localhost port for the benchmark's lifetime:

  landing (decorated heading — the assertion-wording witness)
    → catalogue (card grid, three same-named "add to cart" buttons)
      → cart panel (appears via DOM mutation, same URL)
        → checkout (three labelled fields, a commit click)
          → confirmation (phrase + item + amount, rendered from state)

NOOD_0230 (F0) — a second leg, the search→pick→add→verify shape. Three
identical live-retail runs of that flow gave three different answers (two
distinct failures, one green) because the only host for it was a live,
personalized, inventory-driven grid. This leg is that flow on a
deterministic grid, so the pick binding, the mutation lowering and the
destination-click dedup are exercised by the gate on every machine:

  shop (search box)
    → results (three repeated-structure result cards, "3 results" summary)
      → product page (an "add to cart" control + a "cart" nav link)
        → cart (renders the added item from state)

Everything is synthetic (domain-agnostic rule); state carries through
localStorage exactly like a real shop session. python -m http.server is
banned for REPORTS (NOOD_0124) — this serves the system under test, the
same role test-apps/erp plays, in-process and torn down by the caller.
"""
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGES = {
    "index.html": """<!doctype html><html><head><title>Widget Depot</title>
</head><body>
<header><h1>\U0001f9f0 Widget Depot</h1></header>
<p>Quality widgets, delivered.</p>
<nav><a href="catalogue.html">Shop the catalogue</a></nav>
</body></html>
""",
    "catalogue.html": """<!doctype html><html><head>
<title>Catalogue — Widget Depot</title>
<style>.cards{display:flex;gap:1em}.card{border:1px solid #999;padding:1em}
#cart-panel{display:none;border:2px solid #333;padding:1em;margin-top:1em}
</style></head><body>
<h1>Catalogue</h1>
<div class="cards">
  <div class="card"><h3>Mini Widget</h3><p>$4.99</p>
    <button onclick="add('Mini Widget','$4.99')">add to cart</button></div>
  <div class="card"><h3>Turbo Widget</h3><p>$19.99</p>
    <button onclick="add('Turbo Widget','$19.99')">add to cart</button></div>
  <div class="card"><h3>Mega Widget</h3><p>$49.99</p>
    <button onclick="add('Mega Widget','$49.99')">add to cart</button></div>
</div>
<div id="cart-panel"><h2>Your cart</h2><p id="cart-item"></p>
  <a href="checkout.html">proceed to checkout</a></div>
<script>
function add(name, price){
  localStorage.setItem('item', name); localStorage.setItem('price', price);
  document.getElementById('cart-item').textContent = name + ' — ' + price;
  document.getElementById('cart-panel').style.display = 'block';}
</script></body></html>
""",
    "checkout.html": """<!doctype html><html><head>
<title>Checkout — Widget Depot</title></head><body>
<h1>Checkout</h1>
<form onsubmit="event.preventDefault();location.href='confirm.html'">
<p><label>full name <input id="name"></label></p>
<p><label>street address <input id="street"></label></p>
<p><label>postal code <input id="postal"></label></p>
<button type="submit">place order</button>
</form></body></html>
""",
    "confirm.html": """<!doctype html><html><head>
<title>Order confirmed — Widget Depot</title></head><body>
<h1>Thanks for your order</h1>
<p>Order <span id="num">10042</span> is confirmed.</p>
<p id="line"></p>
<script>
document.getElementById('line').textContent =
  (localStorage.getItem('item')||'') + ' — ' +
  (localStorage.getItem('price')||'');
</script></body></html>
""",
    # NOOD_0230 (F0) — the search leg. Result cards are the universal shape
    # build_result_items extracts (repeated anchor class, distinct hrefs,
    # captioned); the product page carries the mutation control the add_to
    # lowering proves; the cart page renders the added item from state so
    # the identity assertion has something real to find.
    "shop.html": """<!doctype html><html><head>
<title>Widget Depot Shop</title></head><body>
<h1>Widget Depot Shop</h1>
<form action="results.html" method="get">
<input type="search" name="q" aria-label="Search products"
 placeholder="Search products">
<button type="submit">Search</button>
</form></body></html>
""",
    "results.html": """<!doctype html><html><head>
<title>Results — Widget Depot Shop</title>
<style>.result{border:1px solid #999;padding:1em;margin:.5em}</style>
</head><body>
<h1>Search results</h1>
<p id="result-count">3 results</p>
<div class="result">
<a class="result-link" href="gadget_alpha.html">Alpha Gadget</a>
<p>$7.99</p></div>
<div class="result">
<a class="result-link" href="gadget_beta.html">Beta Gadget</a>
<p>$12.99</p></div>
<div class="result">
<a class="result-link" href="gadget_gamma.html">Gamma Gadget</a>
<p>$3.99</p></div>
</body></html>
""",
    "cart.html": """<!doctype html><html><head>
<title>Cart — Widget Depot Shop</title></head><body>
<h1>Your cart</h1>
<p id="cart-line"></p>
<script>
var i = localStorage.getItem('picked_item');
document.getElementById('cart-line').textContent =
  i ? i + ' — ' + (localStorage.getItem('picked_price')||'')
    : 'Your cart is empty';
</script></body></html>
""",
}

_PRODUCT_PAGE = """<!doctype html><html><head>
<title>{name} — Widget Depot Shop</title></head><body>
<h1>{name}</h1><p>{price}</p>
<button id="add-to-cart"
 onclick="localStorage.setItem('picked_item','{name}');
          localStorage.setItem('picked_price','{price}')">add to cart</button>
<p><a href="cart.html">cart</a></p>
</body></html>
"""

for _name, _price, _page in (("Alpha Gadget", "$7.99", "gadget_alpha.html"),
                             ("Beta Gadget", "$12.99", "gadget_beta.html"),
                             ("Gamma Gadget", "$3.99", "gadget_gamma.html")):
    PAGES[_page] = _PRODUCT_PAGE.format(name=_name, price=_price)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):     # per-request stderr noise, silenced
        pass


def write(dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name, html in PAGES.items():
        (dest / name).write_text(html, encoding="utf-8")
    return dest


def serve(workspace: str = ".") -> tuple[ThreadingHTTPServer, str]:
    """Write the pages under <workspace>/.noodle/fixture_shop and serve them
    on an ephemeral 127.0.0.1 port. Returns (server, base_url); the caller
    owns shutdown()."""
    dest = write(Path(workspace) / ".noodle" / "fixture_shop")
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_QuietHandler, directory=str(dest)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"

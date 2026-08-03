"""NOOD_0227 (SC-3) — the benchmark's self-contained multi-page shop.

The 65.9-AIC blowout shipped because nothing in `noodle feature-regression`
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
}


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

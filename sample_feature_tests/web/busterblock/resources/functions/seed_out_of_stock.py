#!/usr/bin/env python3
"""Demo precondition script invoked from a Gherkin step (NOOD_0016).

Manipulates BusterBlock's data — like a Java JDBC script would a real DB — by
hitting its test API: resets state, then forces "Jaws" (id 1) out of stock.
Prints a result line so the feature can assert on the script's stdout.

Usage: seed_out_of_stock.py [BASE_URL]   (default http://localhost:3333)
"""
import json
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3333").rstrip("/")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


call("POST", "/api/test/reset")
call("PATCH", "/api/test/stock", {"movieId": 1, "stock": 0})
print("Jaws is now OUT OF STOCK")

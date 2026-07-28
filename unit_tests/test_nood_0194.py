"""NOOD_0194 — the report server binds without a reverse-DNS lookup.

`http.server.HTTPServer.server_bind()` calls `socket.getfqdn(host)` only to
set `self.server_name`, which nothing in Noodle reads. Measured on one machine:
0.01s on one interpreter, 35.00s on another — and `cli._spawn_report_server`
gives the child 30 seconds to register before it reports "didn't bind within
30s". Anywhere the resolver is slow to answer a reverse lookup (VPN, a
container with no resolver, a self-hosted CI agent) that made the mandatory
report-hosting step fail while the server was coming up fine.
"""
import socket

from noodle.reporting import builder


def test_binding_the_report_server_never_resolves_a_hostname(monkeypatch):
    """The regression itself: any reverse lookup during bind is the bug."""
    def explode(*a, **kw):
        raise AssertionError("bind did a reverse-DNS lookup — see NOOD_0194")

    monkeypatch.setattr(socket, "getfqdn", explode)
    monkeypatch.setattr(socket, "gethostbyaddr", explode)

    httpd = builder._make_server(".", "127.0.0.1", 0)
    try:
        assert httpd.server_address[1] > 0        # bound to a real port
        assert httpd.server_name == "127.0.0.1"   # set from the address, not DNS
    finally:
        httpd.server_close()


def test_the_server_still_serves_after_skipping_the_lookup(tmp_path):
    """server_bind is overridden — prove the socket is actually usable, not
    just constructed, so the shortcut can't pass by not binding at all."""
    (tmp_path / "index.html").write_text("<html>noodle</html>", encoding="utf-8")
    httpd = builder._make_server(str(tmp_path), "127.0.0.1", 0)
    try:
        import threading
        import urllib.request
        threading.Thread(target=httpd.handle_request, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        with urllib.request.urlopen(url, timeout=10) as resp:
            assert b"noodle" in resp.read()
    finally:
        httpd.server_close()


def test_a_pass_that_ran_no_steps_is_not_verified(tmp_path):
    """NOOD_0194 — `noodle init`'s sample scenario is fully commented out, so a
    fresh workspace's first run reported a bare green having launched no
    browser and asserted nothing. `verified` is what says a green is backed by
    something; a zero-step pass is the clearest case of one that isn't."""
    import json as _json

    from noodle.reporting import summary

    (tmp_path / "a-result.json").write_text(_json.dumps(
        {"name": "Sample — login", "status": "passed", "steps": [],
         "historyId": "sample", "start": 0, "stop": 1}), encoding="utf-8")
    empty = summary.collect(str(tmp_path))
    assert empty["passed"] == 1 and empty["failed"] == 0
    assert empty["verified"] is False
    assert any("without running a single step" in r
               for r in empty["unverified_reasons"])

    (tmp_path / "a-result.json").write_text(_json.dumps(
        {"name": "Sample — login", "status": "passed", "historyId": "sample",
         "start": 0, "stop": 1,
         "steps": [{"name": "User clicks the 'Login' button", "status": "passed"}]}), encoding="utf-8")
    real = summary.collect(str(tmp_path))
    assert real["passed"] == 1 and real["verified"] is True

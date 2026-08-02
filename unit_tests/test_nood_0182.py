"""NOOD_0182 — REST waits: one budget (NOODLE_REST_TIMEOUT) across every wok,
overridable per step with "within N seconds", and a timeout that names itself
instead of surfacing a raw URLError.

Real sockets (a loopback server that sleeps), no browser, no network.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from noodle import config
from noodle.agents.api import rest_client
from noodle.resolver.step_resolver import resolve


@pytest.fixture
def slow_server():
    """Answers after 2s — the shape of the reported gap (a slow-but-healthy
    endpoint), just faster so the suite stays quick."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            import time
            time.sleep(2)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/slow"
    srv.shutdown()


def test_budget_default_and_env(monkeypatch):
    monkeypatch.delenv("NOODLE_REST_TIMEOUT", raising=False)
    assert config.rest_timeout() == 30.0
    monkeypatch.setenv("NOODLE_REST_TIMEOUT", "300")
    assert config.rest_timeout() == 300.0
    assert config.rest_timeout(90) == 90.0        # per-step wins over the env
    monkeypatch.setenv("NOODLE_REST_TIMEOUT", "nonsense")
    assert config.rest_timeout() == 30.0


def test_slow_response_within_budget_passes_and_returns_early(monkeypatch, slow_server):
    """The budget is a CEILING, not a sleep: a 2s answer under a 10s budget
    costs 2s, not 10 (urllib blocks on the socket, not on a timer)."""
    import time
    monkeypatch.setenv("NOODLE_REST_TIMEOUT", "10")
    t0 = time.monotonic()
    status, body, _ = rest_client.rest_call("GET", slow_server)
    elapsed = time.monotonic() - t0
    assert (status, body) == (200, '{"ok": true}')
    assert elapsed < 5, f"waited {elapsed:.1f}s for a 2s response — budget became a sleep"


def test_timeout_is_an_actionable_assertion(monkeypatch, slow_server):
    monkeypatch.setenv("NOODLE_REST_TIMEOUT", "1")
    with pytest.raises(AssertionError) as exc:
        rest_client.rest_call("GET", slow_server)
    msg = str(exc.value)
    assert "did not respond within 1s" in msg
    assert "within 2 seconds" in msg and "NOODLE_REST_TIMEOUT=2" in msg


def test_per_step_timeout_beats_a_too_small_env(monkeypatch, slow_server):
    monkeypatch.setenv("NOODLE_REST_TIMEOUT", "1")
    status, _, _ = rest_client.rest_call("GET", slow_server, timeout=10)
    assert status == 200


def test_connection_refused_is_not_dressed_up_as_a_timeout(monkeypatch):
    monkeypatch.setenv("NOODLE_ALLOWED_HOSTS", "")
    import urllib.error
    with pytest.raises(urllib.error.URLError):
        rest_client.rest_call("GET", "http://127.0.0.1:1/nope")


@pytest.mark.parametrize("sentence,key,expected", [
    ("performs a GET call at '/slow' within 90 seconds", "timeout", 90.0),
    ("performs a GET call at '/slow'", "timeout", None),
    ("calls GET 'https://api/x' within 45 seconds", "timeout", 45.0),
    ("waits for the response from '/api/orders' within 120 seconds", "timeout", 120000),
    ("waits for the '/api/orders' call to complete within 20 seconds", "timeout", 20000),
])
def test_within_seconds_grammar(sentence, key, expected):
    assert resolve(sentence)[key] == expected


def test_runner_forwards_the_per_step_budget(monkeypatch):
    """Grammar → runner → client: the wiring most likely to rot silently."""
    from types import SimpleNamespace

    from noodle.orchestrator import runner
    seen = {}

    def fake(method, url, body=None, headers=None, timeout=None):
        seen["timeout"] = timeout
        return 200, "{}", {}

    monkeypatch.setattr(rest_client, "rest_call", fake)
    ctx = SimpleNamespace(page=None, _vars={})
    runner.execute_step(
        "User performs a GET call at 'https://api.example/slow' within 150 seconds", ctx)
    assert seen["timeout"] == 150.0


def test_response_wait_uses_the_rest_budget_not_the_element_one(monkeypatch):
    """The reported gap: a 20s API response failed a `waits for the response`
    step because it inherited the 10s element timeout."""
    monkeypatch.delenv("NOODLE_REST_TIMEOUT", raising=False)
    monkeypatch.setenv("NOODLE_TIMEOUT", "10000")
    seen = {}

    class FakePage:
        url = "http://x/"

        def wait_for_response(self, pred, timeout=None):
            seen["timeout"] = timeout
            return type("R", (), {"status": 200, "url": "http://x/api"})()

    from noodle.agents.web import actions
    actions.wait_response(FakePage(), "/api")
    assert seen["timeout"] == 30000

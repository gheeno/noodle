"""NOOD_0155 — performance wok: load generator, metrics, patterns, dispatch.

Everything runs against a local in-process HTTP server or crafted results —
no external network, so this suite is CI-safe and deterministic where it
matters (counts and error classification; latency numbers are asserted only
directionally).
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from noodle import wok
from noodle.agents.perf import chart, loadgen
from noodle.orchestrator import runner
from noodle.resolver.step_resolver import resolve


def _result(latencies_ms, ok=True, duration_s=10.0, users=3):
    r = loadgen.LoadResult(url="http://test", users=users, duration_s=duration_s)
    r.samples = [loadgen.Sample(i * 0.1, ms, ok, 200 if ok else 500)
                 for i, ms in enumerate(latencies_ms)]
    return r


@pytest.fixture(scope="module")
def http_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = 500 if self.path.startswith("/err") else 200
            body = b"ok"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):        # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# --- metrics math -------------------------------------------------------------

def test_percentiles_are_nearest_rank():
    r = _result(list(range(1, 101)))          # 1..100 ms
    assert r.percentile_ms(50) == 50
    assert r.percentile_ms(95) == 95
    assert r.percentile_ms(99) == 99
    assert r.max_ms == 100
    assert r.avg_ms == pytest.approx(50.5)


def test_metric_lookup_names():
    r = _result([10, 20, 30])
    assert r.metric("p50") == 20
    assert r.metric("average") == pytest.approx(20)
    assert r.metric("slowest") == 30
    assert r.metric("error rate") == 0.0
    assert r.metric("throughput") == pytest.approx(0.3)


def test_empty_result_is_all_zeros():
    r = loadgen.LoadResult(url="http://x", users=1)
    assert r.percentile_ms(95) == 0.0 and r.avg_ms == 0.0 and r.error_rate_pct == 0.0


# --- the load generator -------------------------------------------------------

def test_request_budget_mode_sends_exactly_n(http_server):
    r = loadgen.run_load(http_server, users=4, total_requests=12)
    assert r.count == 12
    assert r.errors == 0
    assert all(s.status == 200 for s in r.samples)
    assert r.duration_s > 0 and r.throughput_rps > 0


def test_duration_mode_stops_and_counts_errors(http_server):
    r = loadgen.run_load(f"{http_server}/err", users=2, duration_s=0.4)
    assert r.count >= 1
    assert r.errors == r.count                # every hit is a 500
    assert r.error_rate_pct == 100.0
    assert all(s.status == 500 for s in r.samples)


def test_unreachable_host_is_an_error_not_a_crash():
    r = loadgen.run_load("http://127.0.0.1:1", users=1, total_requests=2, timeout_s=1)
    assert r.count == 2 and r.errors == 2
    assert all(s.status == 0 for s in r.samples)


def test_exactly_one_budget_is_required():
    with pytest.raises(ValueError):
        loadgen.run_load("http://x", duration_s=1, total_requests=1)
    with pytest.raises(ValueError):
        loadgen.run_load("http://x")


# --- the chart (this wok's screenshot capability) -----------------------------

def test_chart_renders_a_png(tmp_path):
    assert chart.render(_result([10, 250, 900], ok=True),
                        str(tmp_path / "load.png")) == str(tmp_path / "load.png")
    data = (tmp_path / "load.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 1000


# --- patterns -----------------------------------------------------------------

def test_perf_steps_resolve():
    assert resolve('User runs a load test on "http://x" with 10 users for 30 seconds') == \
        {'type': 'perf_load', 'url': 'http://x', 'users': 10, 'duration_s': 30}
    assert resolve('runs a load test on "http://x" with 50 requests') == \
        {'type': 'perf_load', 'url': 'http://x', 'requests': 50, 'users': 5}
    assert resolve('the p95 response time should be under 800 ms') == \
        {'type': 'perf_assert_time', 'metric': 'p95', 'max_ms': 800}
    assert resolve('the average response time should be below 200ms')['metric'] == 'average'
    assert resolve('the error rate should be under 1 %') == \
        {'type': 'perf_assert_error_rate', 'max_pct': 1.0}
    assert resolve('the throughput should exceed 20 requests per second') == \
        {'type': 'perf_assert_throughput', 'min_rps': 20.0}
    # Tag-aware grammar: untagged (web-first best guess) the "at least"
    # phrasing stays with the web assert_compare catch-all; inside a @perf
    # scenario the perf table outranks it and the same sentence is a real
    # throughput assertion (wok.pattern_priority).
    sentence = 'the throughput should be at least 20 requests per second'
    assert resolve(sentence)['type'] == 'assert_compare'
    assert resolve(sentence, tags={'perf'}) == \
        {'type': 'perf_assert_throughput', 'min_rps': 20.0}
    # ...and a @perf scenario still reaches web browserless verbs (fallthrough).
    assert resolve('User waits 2 seconds', tags={'perf'})['type'] == 'wait_seconds'
    assert resolve('saves the load test report as "checkout baseline"') == \
        {'type': 'perf_report', 'name': 'checkout baseline'}
    assert resolve('stores the p95 response time into "P95"') == \
        {'type': 'perf_store', 'metric': 'p95', 'var': 'P95'}


# --- runner dispatch ----------------------------------------------------------

def _context():
    # @perf scenarios are browserless — page=None proves no step needs one.
    return SimpleNamespace(page=None, _vars={})


def _prime(context, latencies, ok=True, duration_s=10.0):
    context._perf_result = _result(latencies, ok=ok, duration_s=duration_s)


def test_perf_load_step_runs_and_remembers(http_server):
    context = _context()
    runner.execute_step(f'User runs a load test on "{http_server}" with 6 requests', context)
    assert context._perf_result.count == 6


def test_assertions_grade_the_last_load():
    context = _context()
    _prime(context, [100, 200, 300])
    runner.execute_step('the p95 response time should be under 800 ms', context)
    runner.execute_step('the error rate should be under 1 %', context)
    with pytest.raises(AssertionError, match="p95 response time is 300ms"):
        runner.execute_step('the p95 response time should be under 250 ms', context)
    with pytest.raises(AssertionError, match="Throughput"):
        runner.execute_step('the throughput should exceed 99 requests per second', context)


def test_tagged_scenario_dispatches_natural_phrasing():
    # End to end through execute_step: the runner reads the scenario's
    # effective tags, so inside @perf the natural "at least" phrasing runs
    # as a throughput assertion (not a web compare of two strings).
    context = _context()
    context.scenario = SimpleNamespace(effective_tags=["perf", "capability"])
    _prime(context, [100, 200, 300], duration_s=10.0)   # 0.3 req/s
    runner.execute_step('the throughput should be at least 0.1 requests per second', context)
    with pytest.raises(AssertionError, match="Throughput"):
        runner.execute_step('the throughput should be at least 99 requests per second',
                            context)


def test_perf_feature_text_validates_with_natural_phrasing():
    # noodle validate / the agent dry-run grade with the same tag priority.
    from noodle.repl.validate import check_feature
    text = (
        "@perf\n"
        "Feature: gates\n"
        "  Scenario: throughput\n"
        '    When User runs a load test on "http://x" with 5 requests\n'
        "    Then the throughput should be at least 1 requests per second\n"
    )
    result = check_feature(text)
    assert result["error"] is None
    assert all(ok for _, ok in result["steps"]), result["steps"]


def test_assertion_without_a_load_test_says_so():
    with pytest.raises(AssertionError, match="No load test has run"):
        runner.execute_step('the p95 response time should be under 800 ms', _context())


def test_store_exposes_metric_to_vars():
    context = _context()
    _prime(context, [100, 200, 300])
    runner.execute_step('stores the p95 response time into "P95"', context)
    assert context._vars["P95"] == "300"


def test_report_step_writes_chart_through_screenshot_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
    context = _context()
    _prime(context, [50, 60, 70])
    runner.execute_step('saves the load test report as "smoke run"', context)
    expected = tmp_path / "screenshots" / "smoke_run.png"
    assert expected.exists()
    # NOOD_0153 seam — hooks.after_step attaches this to Allure/RCA
    assert context._manual_screenshot == str(expected)


# --- registry -----------------------------------------------------------------

def test_performance_wok_registry():
    w = wok.WOKS["performance"]
    assert wok.wok_for_tags(["perf"]) is w
    assert w.extras == ()                 # stdlib engine — core install runs it
    assert "chart" in w.screenshots.lower() or "PNG" in w.screenshots

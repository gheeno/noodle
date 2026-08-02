"""NOOD_0201 — the API wok grows up: batch REST calls (data table /
`repeated N times`), docstring bodies, typed JSON assertions.

The defect that motivated the batch steps: a generated seeding test held 21
pasted `performs a POST call` lines with a single trailing status assertion —
which only ever checked the LAST call. One step, N calls, every call checked.

No network — rest_client.rest_call is monkeypatched throughout.
"""
import json
from types import SimpleNamespace

import pytest

from noodle.orchestrator import runner
from noodle.resolver import api_patterns as patterns


class _Row:
    def __init__(self, headings, cells):
        self.headings, self.cells = headings, cells


class _Table:
    def __init__(self, headings, rows):
        self.headings = headings
        self._rows = [_Row(headings, r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


def _ctx(table=None, text=None):
    return SimpleNamespace(page=None, _vars={}, table=table, text=text)


def _fake_rest(monkeypatch, reply=(201, '{"ok": true}', {})):
    """Record every outgoing call; return a fixed reply (or a callable)."""
    from noodle.agents.api import rest_client
    calls = []

    def fake(method, url, body=None, headers=None, timeout=None):
        calls.append((method, url, body))
        return reply(len(calls)) if callable(reply) else reply

    monkeypatch.setattr(rest_client, "rest_call", fake)
    return calls


# --- pattern matching -----------------------------------------------------------

def test_batch_patterns_match():
    assert patterns.match(
        "performs a POST call at '/api/greeting' with body '{\"name\":\"{name}\"}' "
        "for each row expecting status 201:") == \
        ("rest_call_each", {"method": "POST", "path": "/api/greeting",
                            "body": '{"name":"{name}"}', "expect": 201,
                            "timeout": None})
    assert patterns.match("performs DELETE calls at '/objects/{id}' for each row") == \
        ("rest_call_each", {"method": "DELETE", "path": "/objects/{id}",
                            "body": None, "expect": None, "timeout": None})
    assert patterns.match(
        "performs a POST call at '/g' with body '{\"n\":\"Row {i}\"}' "
        "repeated 20 times expecting status 201") == \
        ("rest_call_repeat", {"method": "POST", "path": "/g",
                              "body": '{"n":"Row {i}"}', "count": 20,
                              "expect": 201, "timeout": None})
    assert patterns.match("performs a POST call at '/g' with this body:") == \
        ("rest_call_doc", {"method": "POST", "path": "/g", "var": None,
                           "timeout": None})
    # per-step budget still composes (NOOD_0182)
    assert patterns.match(
        "performs a GET call at '/slow' repeated 3 times within 90 seconds") == \
        ("rest_call_repeat", {"method": "GET", "path": "/slow", "body": None,
                              "count": 3, "expect": None, "timeout": 90.0})


def test_json_assert_patterns_match():
    assert patterns.match("the response json 'data.items[0].id' should equal '42'") == \
        ("rest_assert_json", {"path": "data.items[0].id", "op": "equal",
                              "expected": "42"})
    assert patterns.match("the response json 'name' should contain 'Ada'") == \
        ("rest_assert_json", {"path": "name", "op": "contain", "expected": "Ada"})
    assert patterns.match("the response json '$' should have 3 items") == \
        ("rest_assert_json_count", {"path": "$", "count": 3})


def test_plain_rest_call_still_matches():
    # the batch additions must not shadow the NOOD_0029 single-call pattern
    assert patterns.match("performs a GET request at '/x'") == \
        ("rest_call", {"method": "GET", "path": "/x", "body": None,
                       "var": None, "timeout": None})


# --- table-driven batch ----------------------------------------------------------

def test_each_row_substitutes_placeholders_and_calls_once_per_row(monkeypatch):
    calls = _fake_rest(monkeypatch)
    ctx = _ctx(table=_Table(["name"], [["Row 01"], ["Row 02"], ["Row 03"]]))
    ctx._vars["REST_BASE_URL"] = "http://localhost:8080"
    runner.execute_step(
        "User performs a POST call at '/api/greeting' with body "
        "'{\"name\":\"{name}\"}' for each row expecting status 201:", ctx)
    assert [c[2] for c in calls] == [
        '{"name":"Row 01"}', '{"name":"Row 02"}', '{"name":"Row 03"}']
    assert all(c[1] == "http://localhost:8080/api/greeting" for c in calls)


def test_each_row_placeholders_reach_the_path(monkeypatch):
    calls = _fake_rest(monkeypatch, reply=(200, "", {}))
    ctx = _ctx(table=_Table(["id"], [["7"], ["8"]]))
    runner.execute_step(
        "User performs DELETE calls at 'http://api/objects/{id}' "
        "for each row expecting status 200:", ctx)
    assert [c[1] for c in calls] == ["http://api/objects/7", "http://api/objects/8"]


def test_each_row_asserts_every_call_not_just_the_last(monkeypatch):
    # row 2 of 3 fails — a trailing `Then the response status should be 201`
    # after pasted calls would have passed this
    _fake_rest(monkeypatch,
               reply=lambda n: (400, "boom", {}) if n == 2 else (201, "ok", {}))
    ctx = _ctx(table=_Table(["name"], [["a"], ["b"], ["c"]]))
    with pytest.raises(AssertionError, match=r"Call 2 of 3 \(name=b\).*expected status 201, got 400"):
        runner.execute_step(
            "User performs a POST call at 'http://api/g' with body "
            "'{\"n\":\"{name}\"}' for each row expecting status 201:", ctx)


def test_each_row_without_table_names_the_fix(monkeypatch):
    _fake_rest(monkeypatch)
    with pytest.raises(AssertionError, match="needs a Gherkin data table"):
        runner.execute_step(
            "User performs a POST call at 'http://api/g' for each row:", _ctx())


def test_each_row_cells_resolve_env_and_var(monkeypatch):
    calls = _fake_rest(monkeypatch)
    ctx = _ctx(table=_Table(["name"], [["{var:WHO}"]]))
    ctx._vars["WHO"] = "Ada"
    runner.execute_step(
        "User performs a POST call at 'http://api/g' with body "
        "'{\"n\":\"{name}\"}' for each row:", ctx)
    assert calls[0][2] == '{"n":"Ada"}'


# --- repeated N times -------------------------------------------------------------

def test_repeat_substitutes_the_counter(monkeypatch):
    calls = _fake_rest(monkeypatch)
    ctx = _ctx()
    runner.execute_step(
        "User performs a POST call at 'http://api/g' with body "
        "'{\"name\":\"Row {i}\"}' repeated 3 times expecting status 201", ctx)
    assert [c[2] for c in calls] == [
        '{"name":"Row 1"}', '{"name":"Row 2"}', '{"name":"Row 3"}']


def test_repeat_over_cap_points_at_the_perf_wok(monkeypatch):
    calls = _fake_rest(monkeypatch)
    with pytest.raises(AssertionError, match="perf wok"):
        runner.execute_step(
            "User performs a GET call at 'http://api/g' repeated 5000 times", _ctx())
    assert calls == []                                # refused before any I/O


# --- docstring body ----------------------------------------------------------------

def test_doc_body_is_sent_with_substitution(monkeypatch):
    calls = _fake_rest(monkeypatch)
    ctx = _ctx(text='{\n  "name": "{var:WHO}"\n}')
    ctx._vars["WHO"] = "Ada"
    runner.execute_step("User performs a POST call at 'http://api/g' with this body:", ctx)
    assert json.loads(calls[0][2]) == {"name": "Ada"}
    assert ctx._vars["REST_STATUS"] == "201"


def test_doc_body_missing_docstring_names_the_fix(monkeypatch):
    _fake_rest(monkeypatch)
    with pytest.raises(AssertionError, match="docstring body"):
        runner.execute_step(
            "User performs a POST call at 'http://api/g' with this body:", _ctx())


# --- typed JSON assertions ----------------------------------------------------------

def _json_ctx(payload):
    ctx = _ctx()
    ctx._vars["REST_BODY"] = json.dumps(payload)
    return ctx


def test_json_equal_is_typed():
    ctx = _json_ctx({"count": 200, "active": True, "gone": None, "name": "Ada"})
    runner.execute_step("the response json 'count' should equal '200'", ctx)
    runner.execute_step("the response json 'active' should equal 'true'", ctx)
    runner.execute_step("the response json 'gone' should equal 'null'", ctx)
    runner.execute_step("the response json 'name' should equal 'Ada'", ctx)
    # the substring trap this exists to close: 20 is not 200
    with pytest.raises(AssertionError, match="expected '20'"):
        runner.execute_step("the response json 'count' should equal '20'", ctx)
    # and the string "true" is not the boolean
    with pytest.raises(AssertionError):
        runner.execute_step("the response json 'name' should equal 'true'", ctx)


def test_json_contain_and_nested_path():
    ctx = _json_ctx({"data": {"items": [{"id": "greeting-42"}]}})
    runner.execute_step(
        "the response json 'data.items[0].id' should contain 'greeting'", ctx)
    with pytest.raises(AssertionError, match="Key 'nope' not found"):
        runner.execute_step("the response json 'data.nope' should equal 'x'", ctx)


def test_json_count_root_and_nested():
    ctx = _json_ctx([1, 2, 3])
    runner.execute_step("the response json '$' should have 3 items", ctx)
    ctx = _json_ctx({"items": [1]})
    with pytest.raises(AssertionError, match="has 1 items, expected 2"):
        runner.execute_step("the response json 'items' should have 2 items", ctx)
    with pytest.raises(AssertionError, match="nothing to count"):
        runner.execute_step("the response json 'items[0]' should have 1 item", ctx)


def test_json_assert_on_non_json_body_is_clear():
    ctx = _ctx()
    ctx._vars["REST_BODY"] = "<html>oops</html>"
    with pytest.raises(AssertionError, match="not valid JSON"):
        runner.execute_step("the response json 'x' should equal '1'", ctx)


# --- api_probe: live discovery ------------------------------------------------------

SPEC = {
    "openapi": "3.0.1",
    "paths": {
        "/greeting/new": {"post": {"requestBody": {"content": {
            "application/json": {"schema":
                {"$ref": "#/components/schemas/GreetingRequest"}}}}}},
        "/greetings": {"get": {}},
    },
    "components": {"schemas": {"GreetingRequest": {
        "type": "object", "properties": {"name": {"type": "string"}}}}},
}


def _fake_server(monkeypatch, spec=SPEC, root_ctype="application/json",
                 server="test-server"):
    from noodle import api_probe
    from noodle.agents.api import rest_client

    def fake(method, url, body=None, headers=None, timeout=None):
        if url.endswith("/v3/api-docs") and spec is not None:
            return 200, json.dumps(spec), {}
        if url.rstrip("/").endswith(":8080") or url.endswith("8080/"):
            return 200, "{}", {"Content-Type": root_ctype, "Server": server}
        return 404, "Not Found", {}

    monkeypatch.setattr(rest_client, "rest_call", fake)
    return api_probe


def test_probe_finds_the_real_endpoint(monkeypatch):
    # the motivating failure: the ticket said POST /greeting, the app serves
    # /greeting/new — one spec fetch settles it
    api_probe = _fake_server(monkeypatch)
    out = api_probe.probe("http://127.0.0.1:8080")
    assert out["ok"] and out["spec_url"].endswith("/v3/api-docs")
    assert "POST /greeting/new" in out["endpoints"]
    assert "GET /greetings" in out["endpoints"]
    # copy-ready steps, request-body hint derived through the $ref
    steps = "\n".join(out["suggested_steps"])
    assert "sets {var:REST_BASE_URL} to 'http://127.0.0.1:8080'" in steps
    assert "performs a POST call at '/greeting/new' with body" in steps
    assert '"name"' in steps
    assert out["questions"] == []


def test_probe_without_spec_asks_instead_of_guessing(monkeypatch):
    api_probe = _fake_server(monkeypatch, spec=None)
    out = api_probe.probe("http://127.0.0.1:8080")
    assert out["ok"] and out["spec_url"] is None and out["endpoints"] == []
    assert any("which endpoints" in q for q in out["questions"])


def test_probe_dead_host_is_an_error(monkeypatch):
    from noodle import api_probe
    from noodle.agents.api import rest_client
    monkeypatch.setattr(rest_client, "rest_call",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    out = api_probe.probe("http://127.0.0.1:8080")
    assert not out["ok"] and "nothing answered" in out["error"]


def test_discover_single_api_candidate_has_no_questions(monkeypatch):
    api_probe = _fake_server(monkeypatch)
    monkeypatch.setattr(api_probe, "_port_open", lambda p: p == 8080)
    out = api_probe.discover()
    assert out["api_candidates"] == ["http://127.0.0.1:8080"]
    assert out["candidates"][0]["spec_url"].endswith("/v3/api-docs")
    assert out["candidates"][0]["endpoints_total"] == 2
    assert out["questions"] == []                     # unambiguous → an answer


def test_discover_nothing_listening_asks(monkeypatch):
    from noodle import api_probe
    monkeypatch.setattr(api_probe, "_port_open", lambda p: False)
    out = api_probe.discover()
    assert out["candidates"] == []
    assert any("is the app under test" in q for q in out["questions"])


def test_discover_filters_airplay_and_flags_ambiguity(monkeypatch):
    from noodle import api_probe
    from noodle.agents.api import rest_client

    def fake(method, url, body=None, headers=None, timeout=None):
        if ":5000" in url:
            return 403, "", {"Server": "AirTunes/770.8.1"}
        if url.endswith("/") and (":8080" in url or ":3000" in url):
            return 200, "{}", {"Content-Type": "application/json"}
        return 404, "", {}

    monkeypatch.setattr(rest_client, "rest_call", fake)
    monkeypatch.setattr(api_probe, "_port_open", lambda p: p in (5000, 8080, 3000))
    out = api_probe.discover()
    assert len(out["candidates"]) == 2                # AirPlay dropped
    assert len(out["api_candidates"]) == 2
    assert any("which base URL" in q for q in out["questions"])


def test_hint_ports_reads_repo_config(tmp_path):
    from noodle import api_probe
    (tmp_path / "application.properties").write_text(
        "spring.datasource.url=jdbc:h2:mem\nserver.port=9095\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PORT=4001\nDEBUG=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  app:\n    ports:\n      - "8090:8080"\n', encoding="utf-8")
    assert sorted(api_probe.hint_ports(tmp_path)) == [4001, 8090, 9095]
    assert api_probe.hint_ports(tmp_path / "missing") == []


# --- goal grammar: batch shapes + typed json checks -----------------------------

def _api_goal(**action_extra):
    from noodle.repl import goal as goal_mod
    g = {"scenario": "seed greetings",
         "actions": [{"do": "api", "id": "c", "method": "POST",
                      "url": "/api/greeting", **action_extra}],
         "checks": []}
    g, _ = goal_mod.normalize(g)
    return goal_mod, g


def test_goal_rows_compile_to_one_table_step_that_resolves():
    goal_mod, g = _api_goal(body='{"name":"{name}"}', expect_status=201,
                            rows=[{"name": "Row 01"}, {"name": "Row 02"}])
    assert goal_mod.validate(g) == []
    feature, pom = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "ATLAS_API")
    assert feature.startswith("@api\n") and pom is None
    assert feature.count("performs a POST call") == 1     # ONE step, not N
    assert "for each row expecting status 201:" in feature
    assert "| name   |".replace("   ", " ") in feature.replace("  ", " ")
    assert "| Row 01 |" in feature and "| Row 02 |" in feature
    # and the compiled step + table parse AND resolve end-to-end
    from noodle.repl import validate
    check = validate.check_feature(feature)
    assert check["error"] is None and validate.unmatched(check) == []


def test_goal_repeat_compiles_to_one_line_that_resolves():
    goal_mod, g = _api_goal(body='{"name":"Row {i}"}', repeat=20,
                            expect_status=201)
    assert goal_mod.validate(g) == []
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "ATLAS_API")
    assert "repeated 20 times expecting status 201" in feature
    assert feature.count("performs a POST call") == 1
    from noodle.repl import validate
    check = validate.check_feature(feature)
    assert check["error"] is None and validate.unmatched(check) == []


def test_goal_batch_with_expect_status_needs_no_extra_check():
    goal_mod, g = _api_goal(body='{"n":"{i}"}', repeat=5, expect_status=201)
    synth = goal_mod.infer_postcondition(g, goal_mod.browserless_evidence(g))
    assert synth["blocking"] == []          # every call is already asserted


def test_goal_batch_validation_catches_the_footguns():
    goal_mod, _ = _api_goal()

    def errs(**extra):
        g = {"scenario": "x", "actions": [
            {"do": "api", "method": "POST", "url": "/g", **extra}],
            "checks": [{"status": 201}]}
        return "\n".join(goal_mod.validate(g))

    assert "two batch shapes" in errs(rows=[{"a": "1"}], repeat=3)
    assert "1..1000" in errs(repeat=100000)
    assert "same keys" in errs(rows=[{"a": "1"}, {"b": "2"}])
    assert "free of '|'" in errs(rows=[{"a": "x|y"}])
    assert "only rides a batch" in errs(expect_status=201)
    assert "100-599" in errs(repeat=3, expect_status=9999)


def test_goal_json_check_compiles_and_validates():
    from noodle.repl import goal as goal_mod
    from noodle.repl import validate
    g = {"scenario": "greeting fields",
         "actions": [{"do": "api", "id": "c", "method": "POST",
                      "url": "https://api.example.com/greeting",
                      "body": '{"name":"Ada"}'}],
         "checks": [{"json": "name", "equals": "Ada"},
                    {"json": "$", "items": 1}]}
    g, _ = goal_mod.normalize(g)
    assert goal_mod.validate(g) == []
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "API")
    assert "the response json 'name' should equal 'Ada'" in feature
    assert "the response json '$' should have 1 items" in feature
    check = validate.check_feature(feature)
    assert check["error"] is None and validate.unmatched(check) == []


def test_goal_json_check_validation():
    from noodle.repl import goal as goal_mod
    base = {"scenario": "x", "actions": [
        {"do": "api", "method": "GET", "url": "/g"}]}
    two = goal_mod.validate({**base, "checks": [
        {"json": "a", "equals": "1", "contains": "1"}]})
    assert any("exactly one of equals | contains | items" in e for e in two)
    orphan = goal_mod.validate({"scenario": "x", "actions": [
        {"do": "click", "target": "Buy"}],
        "checks": [{"json": "a", "equals": "1"}]})
    assert any("no response to assert against" in e for e in orphan)
    stray = goal_mod.validate({**base, "checks": [
        {"see": "ok", "equals": "1"}]})
    assert any("only apply to json checks" in e for e in stray)


# --- prompt planner: batch phrasings -------------------------------------------

def test_prompt_batch_by_name_one_shots():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand("1. create 20 rows using POST "
                    "https://api.example.com/greetings with body "
                    '\'{"name":"Row {i}"}\' expecting status 201')
    assert exp["ok"], exp.get("error")
    act = exp["goal"]["actions"][0]
    assert act["do"] == "api" and act["method"] == "POST"
    assert act["repeat"] == 20 and act["expect_status"] == 201
    assert act["body"] == '{"name":"Row {i}"}'


def test_prompt_repeated_times_tail_one_shots():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand("1. POST https://api.example.com/greetings with body "
                    '\'{"name":"Row {i}"}\' repeated 20 times\n'
                    "2. Verify the response status is 201")
    assert exp["ok"], exp.get("error")
    act = exp["goal"]["actions"][0]
    assert act["repeat"] == 20 and "{i}" in act["body"]
    assert exp["goal"]["checks"] == [{"status": 201, "after": act["id"]}]


def test_prompt_batch_without_url_is_refused_with_the_phrasing():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand("1. Generate 20 rows using REST")
    assert exp["ok"] is False
    assert any("needs a URL" in u.get("reason", "")
               for u in exp.get("unresolved", []))


def test_prompt_write_batch_without_body_is_refused_with_the_phrasing():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand("1. create 5 rows using POST https://api.example.com/g")
    assert exp["ok"] is False
    assert any("body template" in u.get("reason", "")
               for u in exp.get("unresolved", []))


def test_prompt_unrecognized_api_tail_is_refused_not_dropped():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand("1. GET https://api.example.com/g with headers 'X: 1'\n"
                    "2. Verify the response status is 200")
    assert exp["ok"] is False       # "with headers" is outside the grammar


# --- discovery wired into the author door ---------------------------------------

GOAL_RELATIVE = {
    "scenario": "greeting persists",
    "actions": [{"do": "api", "id": "c", "method": "POST",
                 "url": "/api/greeting", "body": '{"name":"Ada"}'}],
    "checks": [{"status": 201, "after": "c"},
               {"json": "name", "equals": "Ada", "after": "c"}],
}


def _stub_discovery(monkeypatch, adopted="http://127.0.0.1:8080",
                    candidates=None, title="ATLAS API"):
    from noodle import api_probe
    from noodle.repl import core as core_mod
    cands = candidates if candidates is not None \
        else ([adopted] if adopted else [])
    monkeypatch.setattr(
        core_mod, "_discover_api_base",
        lambda ws: (adopted, {"ok": True, "candidates": [],
                              "api_candidates": cands, "questions": []}))
    monkeypatch.setattr(api_probe, "probe",
                        lambda url: {"ok": True, "title": title})


def test_author_goal_mode_adopts_the_single_discovered_localhost(
        tmp_path, monkeypatch):
    from noodle.repl import core
    _stub_discovery(monkeypatch)
    core.init_workspace(str(tmp_path))
    res = core.author_test(goal=GOAL_RELATIVE, workspace=str(tmp_path))
    assert res["ok"] and res["ready"], res.get("blocking") or res.get("error")
    assert res["discovered_base_url"] == "http://127.0.0.1:8080"
    assert "atlas_api" in res["app_dir"]          # named from the spec title
    feat = res["compiled"]["feature"]
    assert "sets {var:REST_BASE_URL} to '{env:" in feat
    assert "performs a POST call at '/api/greeting'" in feat
    assert "the response json 'name' should equal 'Ada'" in feat


def test_author_goal_mode_refuses_ambiguous_discovery(tmp_path, monkeypatch):
    from noodle.repl import core
    _stub_discovery(monkeypatch, adopted=None,
                    candidates=["http://127.0.0.1:8080",
                                "http://127.0.0.1:3000"])
    core.init_workspace(str(tmp_path))
    res = core.author_test(goal=GOAL_RELATIVE, workspace=str(tmp_path))
    assert res["ok"] is False and "ambiguous" in res["error"]
    assert res["discovery"]["api_candidates"] == [
        "http://127.0.0.1:8080", "http://127.0.0.1:3000"]


def test_author_prompt_mode_discovers_for_relative_api_prompt(
        tmp_path, monkeypatch):
    from noodle.repl import core
    _stub_discovery(monkeypatch)
    core.init_workspace(str(tmp_path))
    res = core.author_test(
        prompt=("1. POST /api/greeting with body '{\"name\":\"Ada\"}'\n"
                "2. Verify the response status is 201"),
        workspace=str(tmp_path))
    assert res["ok"] and res["ready"], res.get("blocking") or res.get("error")
    assert res["prompt_expansion"]["discovered_base_url"] == \
        "http://127.0.0.1:8080"
    assert "performs a POST call at '/api/greeting'" in \
        res["compiled"]["feature"]


# --- polling: the REST smart wait ------------------------------------------------

def test_wait_until_pattern_matches():
    assert patterns.match(
        "waits until a GET call at '/api/reviews/7' returns status 200") == \
        ("rest_wait_until", {"method": "GET", "path": "/api/reviews/7",
                             "expected": 200, "needle": None, "timeout": None})
    assert patterns.match(
        "waits until '/jobs/1' returns status 200 and the body contains "
        "'done' within 60 seconds") == \
        ("rest_wait_until", {"method": "GET", "path": "/jobs/1",
                             "expected": 200, "needle": "done",
                             "timeout": 60.0})


def test_wait_until_returns_on_the_first_satisfying_response(monkeypatch):
    calls = _fake_rest(monkeypatch,
                       reply=lambda n: (200, '{"state":"done"}', {})
                       if n >= 3 else (404, "not yet", {}))
    from noodle.agents.api import actions as api_actions
    monkeypatch.setattr(api_actions.time, "sleep", lambda s: None)
    ctx = _ctx()
    runner.execute_step(
        "waits until a GET call at 'http://api/jobs/1' returns status 200 "
        "and the body contains 'done'", ctx)
    assert len(calls) == 3                    # polled, then stopped
    assert ctx._vars["REST_STATUS"] == "200"


def test_wait_until_gives_up_with_the_budget_named(monkeypatch):
    _fake_rest(monkeypatch, reply=(503, "unavailable", {}))
    from noodle.agents.api import actions as api_actions
    monkeypatch.setattr(api_actions.time, "sleep", lambda s: None)
    with pytest.raises(AssertionError,
                       match=r"never returned status 200 within 2s.*"
                             r"Last: 503"):
        runner.execute_step(
            "waits until a GET call at 'http://api/jobs/1' returns status 200 "
            "within 2 seconds", _ctx())


# --- response schema contract -----------------------------------------------------

def test_schema_subset_validates_types_required_items_and_bounds():
    schema = {"type": "object", "required": ["id", "name", "rating"],
              "properties": {"id": {"type": "string"},
                             "name": {"type": "string"},
                             "rating": {"type": "integer", "minimum": 1,
                                        "maximum": 5},
                             "tags": {"type": "array", "minItems": 1,
                                      "items": {"type": "string"}},
                             "state": {"enum": ["new", "done"]}}}
    good = {"id": "a", "name": "Ada", "rating": 5, "tags": ["x"],
            "state": "done"}
    assert runner._schema_errors(good, schema) == []
    # the failures a substring check cannot see
    assert any("expected type integer" in e for e in runner._schema_errors(
        {**good, "rating": "5"}, schema))          # stringified number
    assert any("above maximum 5" in e for e in runner._schema_errors(
        {**good, "rating": 9}, schema))
    assert any("missing required property 'name'" in e
               for e in runner._schema_errors({"id": "a", "rating": 1}, schema))
    assert any("$.tags[0]" in e for e in runner._schema_errors(
        {**good, "tags": [7]}, schema))
    assert any("not one of" in e for e in runner._schema_errors(
        {**good, "state": "banana"}, schema))
    # a JSON boolean is never a number
    assert runner._schema_errors(True, {"type": "integer"}) != []


def test_schema_step_reads_the_apps_resources_dir(tmp_path):
    app = tmp_path / "app"
    (app / "features").mkdir(parents=True)
    (app / "resources").mkdir()
    (app / "resources" / "review.json").write_text(json.dumps(
        {"type": "object", "required": ["id"],
         "properties": {"id": {"type": "string"}}}), encoding="utf-8")
    ctx = _ctx()
    ctx.feature = SimpleNamespace(filename=str(app / "features" / "x.feature"))
    ctx._vars["REST_BODY"] = json.dumps({"id": "r1"})
    runner.execute_step("the response should match the schema 'review.json'", ctx)
    ctx._vars["REST_BODY"] = json.dumps({"id": 7})
    with pytest.raises(AssertionError, match="does not match schema"):
        runner.execute_step(
            "the response body should match schema 'review.json'", ctx)


def test_schema_step_missing_file_names_where_it_looked(tmp_path):
    app = tmp_path / "app"
    (app / "features").mkdir(parents=True)
    ctx = _ctx()
    ctx.feature = SimpleNamespace(filename=str(app / "features" / "x.feature"))
    ctx._vars["REST_BODY"] = "{}"
    with pytest.raises(AssertionError, match="schema file not found"):
        runner.execute_step(
            "the response should match the schema 'nope.json'", ctx)


# --- goal grammar: headers / auth / store chaining --------------------------------

def test_goal_api_headers_auth_and_store_compile_and_resolve():
    from noodle.repl import goal as goal_mod
    from noodle.repl import validate
    g = {"scenario": "protected review lifecycle",
         "actions": [
             {"do": "api", "id": "create", "method": "POST",
              "url": "/api/reviews", "body": '{"name":"Ada"}',
              "headers": {"Content-Type": "application/json"},
              "auth": {"scheme": "bearer", "token": "{env:BB_TOKEN}"},
              "store": {"REVIEW_ID": "id"}},
             {"do": "api", "id": "read", "method": "GET",
              "url": "/api/reviews/{var:REVIEW_ID}"}],
         "checks": [{"status": 201, "after": "create"},
                    {"json": "name", "equals": "Ada", "after": "read"}]}
    g, _ = goal_mod.normalize(g)
    assert goal_mod.validate(g) == []
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "BUSTERBLOCK")
    assert feature.startswith("@api\n")
    lines = [ln.strip() for ln in feature.splitlines() if ln.strip()]
    hdr = next(i for i, ln in enumerate(lines) if "request header" in ln)
    auth = next(i for i, ln in enumerate(lines) if "bearer token" in ln)
    call = next(i for i, ln in enumerate(lines) if "POST call" in ln)
    store = next(i for i, ln in enumerate(lines) if "extracts 'id'" in ln)
    read = next(i for i, ln in enumerate(lines) if "GET call" in ln)
    assert hdr < call and auth < call < store < read
    assert "{env:BB_TOKEN}" in feature and "{var:REVIEW_ID}" in feature
    check = validate.check_feature(feature)
    assert check["error"] is None and validate.unmatched(check) == []


def test_goal_api_auth_store_validation():
    from noodle.repl import goal as goal_mod

    def errs(**extra):
        return "\n".join(goal_mod.validate({"scenario": "x", "actions": [
            {"do": "api", "method": "GET", "url": "/g", **extra}],
            "checks": [{"status": 200}]}))

    assert "scheme: 'bearer'" in errs(auth={"scheme": "digest"})
    assert "bearer auth needs a token" in errs(auth={"scheme": "bearer"})
    assert "basic auth needs user" in errs(auth={"scheme": "basic",
                                                 "user": "bob"})
    assert "free of single quotes" in errs(headers={"X": "a'b"})
    assert "variable names" in errs(store={"9bad": "id"})
    assert errs(headers={"Accept": "application/json"},
                auth={"scheme": "bearer", "token": "{env:T}"},
                store={"ID": "data.id"}) == ""


def test_author_web_goal_never_port_scans(tmp_path, monkeypatch):
    from noodle.repl import core
    monkeypatch.setattr(
        core, "_discover_api_base",
        lambda ws: pytest.fail("a web goal must not trigger a port sweep"))
    res = core.author_test(goal={"scenario": "s", "actions": [
        {"do": "click", "target": "Buy"}], "checks": [{"see": "ok"}]},
        workspace=str(tmp_path))
    assert res["ok"] is False and "required" in res["error"]

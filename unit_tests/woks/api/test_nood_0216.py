"""NOOD_0216 — the api wok encapsulated and matured.

Encapsulation: the REST grammar owns its table (resolver/api_patterns.py,
consulted before web in every order) and its engine (agents/api/), like every
other wok. Maturity: spec-file/URL import with suite generation (the
"developer sends the spec" door), request/response evidence on Allure,
form/multipart/GraphQL/cookie message types, and goal-mode parity
(timeout / wait_until / schema).
"""
import json
from types import SimpleNamespace

import pytest

from noodle import api_probe, wok
from noodle.agents.api import actions as api_actions
from noodle.orchestrator import runner
from noodle.repl import goal as goal_mod
from noodle.resolver import step_resolver
from noodle.resolver.api_patterns import PATTERNS as API_PATTERNS


def _ctx(table=None, text=None):
    return SimpleNamespace(page=None, _vars={}, table=table, text=text)


def _fake_rest(monkeypatch, reply=(200, '{"ok": true}', {})):
    """Record (method, url, body, headers); reply fixed or callable(n)."""
    from noodle.agents.api import rest_client
    calls = []

    def fake(method, url, body=None, headers=None, timeout=None):
        calls.append((method, url, body, dict(headers or {})))
        return reply(len(calls)) if callable(reply) else reply

    monkeypatch.setattr(rest_client, "rest_call", fake)
    return calls


# --- encapsulation: the table and the priority --------------------------------

def test_api_table_registered_before_web_everywhere():
    assert "api" in step_resolver._TABLES
    for tags in (None, [], ["web"], ["api"], ["perf"], ["windows"], ["mac"],
                 ["appium", "android"]):
        order = wok.pattern_priority(tags)
        assert order.index("api") < order.index("web"), (tags, order)


@pytest.mark.parametrize("tags", [None, ["api"], ["web"], ["perf"], ["windows"]])
@pytest.mark.parametrize("sentence,expected", [
    ("performs a GET call at '/objects'", "rest_call"),
    ("the response status should be 200", "rest_assert_status"),
    ("the response json 'a.b' should equal '1'", "rest_assert_json"),
    ("the response body should contain 'id'", "rest_assert_body"),
    ("sets the bearer token to 'tok'", "rest_set_auth"),
    ("waits until '/jobs/1' returns status 200 within 60 seconds",
     "rest_wait_until"),
])
def test_rest_grammar_resolves_in_every_wok_context(tags, sentence, expected):
    """REST is plain I/O — the founding contract: resolvable under any tags."""
    assert step_resolver.resolve(sentence, tags=tags)["type"] == expected


def test_api_first_claim_does_not_shadow_web_verbs():
    a = step_resolver.resolve("clicks the 'Login' button")
    assert a["type"] == "click"
    a = step_resolver.resolve('the user sees "Products"')
    assert not a["type"].startswith("rest_")


def test_every_api_pattern_action_carries_the_rest_prefix():
    """The browserless bypass keys on the rest_ prefix (NOOD_0191)."""
    assert all(t.startswith("rest_") for _, t, _ in API_PATTERNS)


def test_old_rest_client_import_path_still_works():
    from noodle.agents.api import rest_client as new
    from noodle.agents.web import rest_client as shim
    assert shim.rest_call is new.rest_call
    assert shim.multipart_body is new.multipart_body


# --- new message types ---------------------------------------------------------

def test_form_body_sets_urlencoded_for_that_call_only(monkeypatch):
    calls = _fake_rest(monkeypatch)
    ctx = _ctx()
    runner.execute_step(
        "User performs a POST call at 'https://a.example/login' "
        "with form body 'user=bob&pass=pw'", ctx)
    runner.execute_step(
        "User performs a GET call at 'https://a.example/me'", ctx)
    assert calls[0][2] == "user=bob&pass=pw"
    assert calls[0][3]["Content-Type"] == "application/x-www-form-urlencoded"
    assert "Content-Type" not in calls[1][3]      # session headers untouched


def test_cookie_jar_persists_per_host_and_clears(monkeypatch):
    def reply(n):
        return (200, "{}", {"Set-Cookie": "session=abc; Path=/; HttpOnly"}
                if n == 1 else {})
    calls = _fake_rest(monkeypatch, reply)
    ctx = _ctx()
    runner.execute_step("User performs a GET call at 'https://a.example/login'", ctx)
    runner.execute_step("User performs a GET call at 'https://a.example/me'", ctx)
    runner.execute_step("User performs a GET call at 'https://b.example/'", ctx)
    assert calls[1][3].get("Cookie") == "session=abc"
    assert "Cookie" not in calls[2][3]            # jar is host-keyed
    runner.execute_step("User clears the rest cookies", ctx)
    runner.execute_step("User performs a GET call at 'https://a.example/me'", ctx)
    assert "Cookie" not in calls[3][3]


def test_multipart_upload_builds_a_real_multipart_body(monkeypatch, tmp_path):
    calls = _fake_rest(monkeypatch, (201, "{}", {}))
    f = tmp_path / "photo.png"
    f.write_bytes(b"\x89PNG fake")
    ctx = _ctx()
    runner.execute_step(
        f"User performs a POST call at 'https://a.example/upload' "
        f"uploading the file '{f}' as 'photo'", ctx)
    method, url, body, headers = calls[0]
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    boundary = headers["Content-Type"].split("boundary=")[1]
    assert boundary.encode() in body
    assert b'name="photo"; filename="photo.png"' in body
    assert b"\x89PNG fake" in body


def test_graphql_query_wraps_docstring_and_errors_gate(monkeypatch):
    calls = _fake_rest(
        monkeypatch,
        (200, '{"data": {"hero": "R2"}, "errors": [{"message": "boom"}]}', {}))
    ctx = _ctx(text="query { hero }")
    runner.execute_step(
        "User performs a graphql query at 'https://a.example/graphql'", ctx)
    assert calls[0][0] == "POST"
    assert json.loads(calls[0][2]) == {"query": "query { hero }"}
    runner.execute_step("the response json 'data.hero' should equal 'R2'", ctx)
    with pytest.raises(AssertionError, match="boom"):
        runner.execute_step("the response should have no graphql errors", ctx)


def test_graphql_errors_gate_green_without_errors(monkeypatch):
    _fake_rest(monkeypatch, (200, '{"data": {}}', {}))
    ctx = _ctx(text="query { hero }")
    runner.execute_step(
        "User performs a graphql query at 'https://a.example/graphql'", ctx)
    runner.execute_step("the response should have no graphql errors", ctx)


def test_docstring_body_can_store_the_response(monkeypatch):
    """NOOD_0216 — the escape hatch for bodies holding a single quote."""
    _fake_rest(monkeypatch, (201, '{"id": 7}', {}))
    ctx = _ctx(text='{"note": "it\'s got an apostrophe"}')
    runner.execute_step(
        "User performs a POST call at 'https://a.example/notes' with this "
        "body storing the response in {var:OUT}", ctx)
    assert ctx._vars["OUT"] == '{"id": 7}'


# --- evidence ------------------------------------------------------------------

def test_rest_calls_record_request_response_evidence(monkeypatch):
    _fake_rest(monkeypatch, (200, '{"ok": true}', {}))
    ctx = _ctx()
    runner.execute_step(
        "User performs a POST call at 'https://a.example/x' with body '{\"a\":1}'",
        ctx)
    ev = ctx._rest_evidence
    assert ev == [{"method": "POST", "url": "https://a.example/x",
                   "status": 200, "request_body": '{"a":1}',
                   "response_body": '{"ok": true}'}]


def test_evidence_caps_calls_and_body_size():
    ev = []
    for _ in range(api_actions._EVIDENCE_CALL_CAP + 10):
        api_actions._record(ev, "GET", "http://x/", 200, None, "b" * 10_000)
    assert len(ev) == api_actions._EVIDENCE_CALL_CAP + 1
    assert "capped" in ev[-1]["truncated"]
    assert len(ev[0]["response_body"]) == api_actions._EVIDENCE_BODY_CAP


# --- spec import + suite generation --------------------------------------------

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Petstore"},
    "servers": [{"url": "http://localhost:9999"}],
    "paths": {
        "/pets": {
            "get": {"summary": "List pets", "responses": {"200": {}}},
            "post": {"responses": {"201": {}},
                     "requestBody": {"content": {"application/json": {
                         "schema": {"example": {"name": "Rex"}}}}}},
        },
        "/pets/{petId}": {"get": {"responses": {"200": {}}}},
    },
}


def test_probe_reads_a_spec_file_and_generates_a_suite(tmp_path):
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    rep = api_probe.probe(str(spec), suite=True)
    assert rep["ok"] and rep["title"] == "Petstore"
    assert rep["base_url"] == "http://localhost:9999"
    assert rep["endpoints_total"] == 3
    content = rep["feature_content"]
    assert content.startswith("@api")
    assert "sets {var:REST_BASE_URL} to 'http://localhost:9999'" in content
    assert "When performs a POST call at '/pets' with body '{\"name\": \"Rex\"}'" \
        in content
    assert "Then the response status should be 201" in content
    assert "performs a GET call at '/pets/<petId>'" in content   # visible ask
    assert any("<placeholders>" in q for q in rep["questions"])
    assert rep["suite_omitted"] == 0


def test_generated_suite_steps_all_resolve():
    content, omitted, _ = api_probe.suite_from_doc(SPEC, "http://localhost:9999")
    assert omitted == 0
    steps = [ln.strip().split(" ", 1)[1] for ln in content.splitlines()
             if ln.strip().split(" ", 1)[0] in ("Given", "When", "Then")]
    assert steps
    for s in steps:
        assert step_resolver.resolve(s, tags=["api"])["type"], s


def test_suite_caps_scenarios_and_reports_omitted():
    big = {"openapi": "3.0.0", "info": {"title": "Big"},
           "paths": {f"/r{i}": {"get": {"responses": {"200": {}}}}
                     for i in range(40)}}
    content, omitted, _ = api_probe.suite_from_doc(big, "http://x")
    assert content.count("Scenario:") == api_probe._SUITE_CAP
    assert omitted == 40 - api_probe._SUITE_CAP


def test_spec_without_server_url_asks_for_the_base(tmp_path):
    doc = dict(SPEC)
    doc.pop("servers")
    spec = tmp_path / "api.yaml"
    import yaml
    spec.write_text(yaml.safe_dump(doc), encoding="utf-8")
    rep = api_probe.probe(str(spec), suite=True)
    assert rep["base_url"] is None
    assert "{env:API_BASE_URL}" in rep["feature_content"]
    assert any("base URL" in q for q in rep["questions"])


def test_probe_spec_url_direct(monkeypatch):
    monkeypatch.setattr(api_probe, "_get",
                        lambda url: (200, json.dumps(SPEC), {}))
    rep = api_probe.probe("http://intranet/specs/petstore.json")
    assert rep["ok"] and rep["spec_url"] == "http://intranet/specs/petstore.json"
    assert rep["endpoints_total"] == 3


# --- goal-mode parity ----------------------------------------------------------

def _valid(goal):
    g, _ = goal_mod.normalize(goal)
    errs = goal_mod.validate(g)
    assert errs == [], errs
    return g


def test_goal_timeout_compiles_within_seconds():
    g = _valid({"scenario": "slow endpoint",
                "actions": [{"do": "api", "url": "https://a.example/slow",
                             "timeout": 90}],
                "checks": [{"status": 200}]})
    feature, _ = goal_mod.compile_goal(g, {}, "APP")
    assert "performs a GET call at 'https://a.example/slow' within 90 seconds" \
        in feature


def test_goal_wait_until_compiles_the_polling_step():
    g = _valid({"scenario": "async job",
                "actions": [{"do": "api", "url": "/jobs/1",
                             "wait_until": {"status": 200, "contains": "done"},
                             "timeout": 60}]})
    assert not goal_mod.needs_browser(g)
    feature, _ = goal_mod.compile_goal(g, {}, "APP")
    assert ("waits until a GET call at '/jobs/1' returns status 200 and the "
            "body contains 'done' within 60 seconds") in feature
    # wait_until IS the assertion — the postcondition gate must not block.
    out = goal_mod.infer_postcondition(g, {})
    assert not out.get("blocking")


def test_goal_schema_check_compiles_and_is_browserless():
    g = _valid({"scenario": "contract",
                "actions": [{"do": "api", "url": "/reviews"}],
                "checks": [{"schema": "schemas/review.json"}]})
    assert not goal_mod.needs_browser(g)
    feature, _ = goal_mod.compile_goal(g, {}, "APP")
    assert "the response should match the schema 'schemas/review.json'" in feature


@pytest.mark.parametrize("goal,needle", [
    ({"actions": [{"do": "api", "url": "/x", "timeout": -1}],
      "checks": [{"status": 200}]}, "timeout must be seconds"),
    ({"actions": [{"do": "api", "url": "/x", "body": "{}",
                   "wait_until": {"status": 200}}]}, "wait_until is the whole"),
    ({"actions": [{"do": "api", "url": "/x",
                   "wait_until": {"code": 200}}]}, "wait_until must be"),
    ({"actions": [{"do": "api", "url": "/x"}],
      "checks": [{"schema": "bad'quote.json"}]}, "schema must be"),
    ({"actions": [{"do": "click", "target": "Go"}],
      "checks": [{"schema": "schemas/x.json"}]}, "needs an api action"),
])
def test_goal_rejections_name_the_defect(goal, needle):
    g, _ = goal_mod.normalize({"scenario": "g", **goal})
    errs = goal_mod.validate(g)
    assert any(needle in e for e in errs), errs


# --- workspace scaffold --------------------------------------------------------

def test_init_scaffolds_the_api_sample(tmp_path):
    from noodle import cli
    files = cli._template_files(tmp_path)
    api_files = {p for p in files if "sample_api" in str(p)}
    assert {p.name for p in api_files} == {"api_smoke.feature",
                                           "environments.yaml"}
    feature = files[next(p for p in api_files if p.suffix == ".feature")]
    assert feature.startswith("#") and "@api" in feature
    assert "api-scan" in feature          # points at spec-driven generation

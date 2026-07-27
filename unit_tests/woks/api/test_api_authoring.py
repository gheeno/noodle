"""NOOD_0192 — the api wok is authorable from a prompt, alone or with web.

Before this the deterministic authoring path was web-only: every API test —
the whole wok — had to be hand-written Gherkin, which is never intent-verified
and never measured. These pin the three things that make it work: the grammar
reads api verbs, a pure-API goal compiles to @api and launches NO browser, and
a mixed goal keeps the user's own order (REST preamble before navigation).
"""
import pytest

from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe


def _compile(prompt, **kw):
    exp = pe.expand(prompt, **kw)
    assert exp["ok"], exp.get("error")
    assert pe.review_contract(exp)["ok"], pe.review_contract(exp)["problems"]
    g, _ = goal_mod.normalize(exp["goal"])
    assert goal_mod.validate(g) == []
    return exp, g


# --- the grammar ---------------------------------------------------------------

@pytest.mark.parametrize("step,method", [
    ("GET https://api.example.com/orders", "GET"),
    ("POST https://api.example.com/orders", "POST"),
    ("sends a DELETE request to https://api.example.com/orders/1", "DELETE"),
    ("performs a PUT call at https://api.example.com/orders/1", "PUT"),
    ("call the api at https://api.example.com/orders", "GET"),
    ("hits the endpoint https://api.example.com/orders", "GET"),
    # the user's own phrasing in the NOOD_0192 request
    ("go to https://api.example.com/orders via rest", "GET"),
])
def test_every_api_call_phrasing_compiles(step, method):
    _, g = _compile(f"1. {step}\n2. Verify the response status is 200")
    call = g["actions"][0]
    assert call["do"] == "api" and call["method"] == method
    assert call["url"] == "https://api.example.com/orders" \
        or call["url"].endswith("/orders/1")


@pytest.mark.parametrize("step", [
    "Verify the response status is 200",
    "See if url is 200",                    # the user's own phrasing
    "the response status should be 200",
    "check the status code is 200",
])
def test_every_status_phrasing_compiles(step):
    _, g = _compile(f"1. GET https://api.example.com/orders\n2. {step}")
    assert g["checks"] == [{"status": 200, "after": g["actions"][0]["id"]}]


def test_a_status_claim_is_never_read_as_a_url_assertion():
    """'see if url is 200' also matches the url_contains arm. Read as a
    browser assertion it would compile `the url should contain "200"` — green
    on any page whose address happens to hold those digits, and it would never
    touch the API at all."""
    _, g = _compile("1. GET https://api.example.com/orders\n"
                    "2. See if url is 200")
    assert "url_contains" not in g["checks"][0]


def test_the_api_patterns_do_not_swallow_web_steps():
    """They lead the table, so everything below them is at their mercy: a
    plain navigation must stay a navigation (only the explicit `via rest`
    suffix diverts it) and a control whose NAME says api stays a click."""
    _, g = _compile('1. Go to the URL https://example.com\n'
                    '2. Click "API docs"\n'
                    '3. Verify "Reference"')
    assert g["navigation"] == ["https://example.com"]
    assert [a["do"] for a in g["actions"]] == ["click"]


def test_a_status_check_with_no_call_is_refused_not_guessed():
    exp = pe.expand("1. Go to the URL https://example.com\n"
                    "2. Verify the response status is 200")
    assert exp["ok"] is False
    assert exp["conflicts"], "must name the contradiction, not invent a call"


def test_the_goal_schema_refuses_an_api_check_without_an_api_action():
    errs = goal_mod.validate({"scenario": "x", "actions": [
        {"do": "click", "target": "Buy"}], "checks": [{"status": 200}]})
    assert any("no response to assert against" in e for e in errs)


def test_an_unasserted_api_call_blocks_instead_of_synthesizing():
    """A call with no assertion proves nothing — a 500 with a body is still
    'a call that happened'. Every other action kind either derives a
    postcondition or blocks; api must not silently pass."""
    goal = {"scenario": "x", "actions": [
        {"do": "api", "id": "c", "url": "https://api.example.com/orders"}]}
    synth = goal_mod.infer_postcondition(goal, goal_mod.browserless_evidence(goal))
    assert synth["blocking"] and "status: 200" in synth["blocking"][0]


# --- pure API: no browser anywhere ---------------------------------------------

PURE = ("1. GET https://api.example.com/orders\n"
        "2. Verify the response status is 200\n"
        "3. Verify the response body contains \"total\"")


def test_a_pure_api_prompt_needs_no_url_step():
    """The api-only prompt is made ENTIRELY of URLs, and it used to be
    refused with 'no URL in the prompt' — the api wok was unreachable from a
    prompt at all."""
    exp, _ = _compile(PURE)
    assert exp["base_url"] == "https://api.example.com/orders"
    assert exp["app_name"] == "api_example_com"


def test_a_pure_api_goal_compiles_to_api_with_no_browser_steps():
    _, g = _compile(PURE)
    assert goal_mod.needs_browser(g) is False
    feature, pom = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "API_EXAMPLE_COM")
    assert feature.startswith("@api\n")
    assert "User is on" not in feature and "popup" not in feature
    assert pom is None, "no page, no page objects"
    assert "performs a GET call at 'https://api.example.com/orders'" in feature
    assert "the response status should be 200" in feature
    assert "the response body should contain 'total'" in feature


def test_every_compiled_api_step_actually_resolves():
    """The compiler's phrasing and the REST client's pattern table are two
    files; a step nobody can match compiles happily and fails at run time."""
    from noodle.repl import validate
    _, g = _compile(PURE)
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "API_EXAMPLE_COM")
    check = validate.check_feature(feature)
    assert check["error"] is None
    assert validate.unmatched(check) == []


def test_authoring_a_pure_api_test_launches_no_probe(tmp_path, monkeypatch):
    """The load-bearing claim: no browser is started for an api-only goal.
    Fails loudly rather than quietly opening Chromium in a CI image that
    has none."""
    from noodle.repl import core as core_mod
    monkeypatch.setattr(core_mod, "probe_page", lambda *a, **k:
                        pytest.fail("a browser probe ran for an api-only goal"))
    core.init_workspace(str(tmp_path))
    res = core.author_test(prompt=PURE, workspace=str(tmp_path))
    assert res["ok"] and res["ready"], res.get("blocking") or res.get("error")
    assert res["evidence"]["browserless"] is True
    assert res["app_dir"].startswith("noodle_tests/api/"), \
        "an api package does not belong under noodle_tests/web/"
    assert res["compiled"]["feature"].startswith("@api\n")


def test_the_planner_does_not_bill_a_probe_that_never_ran(tmp_path):
    core.init_workspace(str(tmp_path))
    res = core.author_test(prompt=PURE, workspace=str(tmp_path))
    assert res["planner"]["budgets"]["probes"] == 0


# --- cross-wok: api + web in one scenario ---------------------------------------

MIXED = ('1. GET https://en.example.com/api/summary\n'
         '2. Verify the response status is 200\n'
         '3. Go to the URL https://en.example.com/wiki/Thing\n'
         '4. Close any popup that may appear\n'
         '5. Verify "the free encyclopedia"')


def test_a_mixed_prompt_keeps_the_users_order():
    """'fetch over REST, then prove it in the UI' — the navigation Given must
    come AFTER the call the user put first, or the compiled test tells a
    different story from the prompt (and a nav URL that consumes a stored
    response value could never work)."""
    _, g = _compile(MIXED)
    assert goal_mod.needs_browser(g) is True
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "APP", nav_keys=["APP_THING"])
    lines = [ln.strip() for ln in feature.splitlines() if ln.strip()]
    call = next(i for i, ln in enumerate(lines) if "performs a GET call" in ln)
    status = next(i for i, ln in enumerate(lines) if "response status" in ln)
    nav = next(i for i, ln in enumerate(lines) if "User is on" in ln)
    assert call < status < nav


def test_a_mixed_scenario_is_tagged_web_not_api():
    """@api means 'start no browser', which would kill the UI half. The most
    common cross-wok mistake, and the compiler must never make it."""
    _, g = _compile(MIXED)
    feature, _ = goal_mod.compile_goal(
        g, goal_mod.browserless_evidence(g), "APP", nav_keys=["APP_THING"])
    assert feature.startswith("@web\n")

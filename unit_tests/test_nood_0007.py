"""NOOD_0007 — generation validation loop, prompt templates, viewport control,
browser-less @api + REST auth, LLM cost cap + step cache.

No browser, no LLM, no network — everything external is monkeypatched.
"""
import json
from types import SimpleNamespace

import pytest

from noodle import config, hooks
from noodle.llm import client
from noodle.orchestrator import runner
from noodle.repl import generate, prompts, validate
from noodle.resolver import patterns, step_resolver

GOOD_FEATURE = """@web
Feature: Login

  Scenario: Valid login
    Given User is on "https://example.com"
    When User enters "bob" in the username field
    And User clicks the login button
    Then User should see "Welcome"
"""

UNMATCHED_FEATURE = """@web
Feature: Login

  Scenario: Valid login
    Given User is on "https://example.com"
    When User performs an interpretive dance on the login form
    Then User should see "Welcome"
"""


# --- validation (resolver dry-run) -------------------------------------------

def test_check_feature_all_pattern():
    result = validate.check_feature(GOOD_FEATURE)
    assert result["error"] is None
    assert len(result["steps"]) == 4
    assert all(ok for _, ok in result["steps"])
    assert validate.unmatched(result) == []
    assert "✅ all steps resolve deterministically" in validate.render(result)


def test_check_feature_flags_unmatched_step():
    result = validate.check_feature(UNMATCHED_FEATURE)
    assert result["error"] is None
    misses = validate.unmatched(result)
    assert misses == ["When User performs an interpretive dance on the login form"]
    assert "[LLM]" in validate.render(result)
    assert "1 step(s) need the LLM fallback" in validate.render(result)


def test_check_feature_parse_error_and_empty():
    bad = validate.check_feature("Feature broken\n  what is this")
    assert bad["error"] is not None and bad["steps"] == []
    assert "parse error" in validate.render(bad)
    empty = validate.check_feature("")
    assert empty["error"] is None and empty["steps"] == []


def test_check_feature_includes_background_steps():
    text = GOOD_FEATURE.replace(
        "  Scenario: Valid login",
        '  Background:\n    Given User is on "https://example.com"\n\n  Scenario: Valid login',
    )
    result = validate.check_feature(text)
    assert len(result["steps"]) == 5


# --- prompt templates ---------------------------------------------------------

def test_generation_prompt_embeds_vocabulary_and_request():
    p = prompts.generation_prompt("checkout flow", "https://shop.example")
    assert "https://shop.example" in p
    assert "checkout flow" in p
    assert 'User clicks the login button' in p        # core vocabulary present
    # NOOD_0101 — vocabulary is keyword-gated now: a UI-only request doesn't
    # pay for the REST family, but a request that mentions it gets it.
    assert "performs a GET request" not in p
    assert "performs a GET request" in prompts.generation_prompt(
        "checkout api returns status 200", "https://shop.example")


def test_repair_prompt_lists_unmatched_steps():
    p = prompts.repair_prompt("Feature: X", ["When User does a thing"])
    assert "- When User does a thing" in p
    assert "Feature: X" in p


# --- generate_llm validation loop ----------------------------------------------

def test_generate_llm_good_first_draft_needs_no_repair(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return GOOD_FEATURE

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, pom = generate.generate_llm("login", "https://example.com", cfg, str(tmp_path))
    assert len(calls) == 1                            # no repair pass
    assert feat.read_text().startswith("@web")
    assert pom.exists()
    assert "✅ all steps resolve deterministically" in capsys.readouterr().out


def test_generate_llm_repairs_unmatched_steps_once(tmp_path, monkeypatch, capsys):
    # NOOD_0101 — the repair reply is now just the fixed line(s), not the
    # whole file: output tokens dominate model latency, and a splice-back
    # can't mangle steps that already resolved.
    responses = [UNMATCHED_FEATURE, "When User clicks the login button"]
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, _ = generate.generate_llm("login", "https://example.com", cfg, str(tmp_path))
    assert len(calls) == 2                            # generation + one repair
    assert "interpretive dance" in calls[1]           # repair prompt names the miss
    assert "Feature: Login" not in calls[1]           # …but not the whole draft
    text = feat.read_text()
    assert "interpretive dance" not in text
    assert "When User clicks the login button" in text
    assert "✅" in capsys.readouterr().out


def test_generate_llm_keeps_original_when_repair_is_worse(tmp_path, monkeypatch, capsys):
    responses = [UNMATCHED_FEATURE, "not gherkin at all"]
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, _ = generate.generate_llm("login", "https://example.com", cfg, str(tmp_path))
    assert len(calls) == 2
    assert "interpretive dance" in feat.read_text()   # original kept, not mangled
    assert "[LLM]" in capsys.readouterr().out         # and the miss is reported


# --- viewport ------------------------------------------------------------------

def test_viewport_pattern():
    assert patterns.match('sets the viewport to "1920x1080"') == \
        ("set_viewport", {"width": 1920, "height": 1080})
    assert patterns.match("sets the viewport to 800 x 600") == \
        ("set_viewport", {"width": 800, "height": 600})


def test_viewport_from_tag_env_and_default(monkeypatch):
    monkeypatch.delenv("NOODLE_VIEWPORT", raising=False)
    assert hooks._viewport_from({"web"}) is None
    assert hooks._viewport_from({"web", "viewport:1920x1080"}) == \
        {"width": 1920, "height": 1080}
    monkeypatch.setenv("NOODLE_VIEWPORT", "1280x720")
    assert hooks._viewport_from({"web"}) == {"width": 1280, "height": 720}
    # tag wins over env
    assert hooks._viewport_from({"viewport:640x480"}) == {"width": 640, "height": 480}
    with pytest.raises(ValueError, match="expected WIDTHxHEIGHT"):
        hooks._viewport_from({"viewport:huge"})


def test_set_viewport_dispatch():
    sizes = []
    page = SimpleNamespace(set_viewport_size=lambda s: sizes.append(s))
    ctx = SimpleNamespace(page=page, _vars={})
    runner.execute_step('User sets the viewport to "1024x768"', ctx)
    assert sizes == [{"width": 1024, "height": 768}]


# --- browser-less @api ----------------------------------------------------------

def _api_ctx():
    return SimpleNamespace(page=None, _vars={})


def test_web_step_without_browser_fails_clearly():
    with pytest.raises(AssertionError, match="scenario has no browser"):
        runner.execute_step("User clicks the login button", _api_ctx())


def test_rest_step_without_browser_is_allowed(monkeypatch):
    from noodle.agents.web import rest_client
    monkeypatch.setattr(rest_client, "rest_call",
                        lambda m, u, b=None, h=None: (200, '{"ok": true}', {}))
    ctx = _api_ctx()
    runner.execute_step("User performs a GET request at 'https://api.example/x'", ctx)
    assert ctx._vars["REST_STATUS"] == "200"


def test_api_before_scenario_skips_browser(monkeypatch):
    scenario = SimpleNamespace(effective_tags=["api"], tags=["api"], name="s",
                               feature=SimpleNamespace(name="F"))
    ctx = SimpleNamespace()
    hooks.before_scenario(ctx, scenario)
    assert ctx.page is None
    assert not hasattr(ctx, "_pw")                    # Playwright never started


# --- REST auth ------------------------------------------------------------------

def test_auth_patterns():
    assert patterns.match("sets the bearer token to 'tok123'") == \
        ("rest_set_auth", {"scheme": "bearer", "token": "tok123"})
    assert patterns.match("uses basic auth with 'bob' and 'pw'") == \
        ("rest_set_auth", {"scheme": "basic", "user": "bob", "password": "pw"})
    assert patterns.match("sets the api key header 'X-Api-Key' to 'k1'") == \
        ("rest_set_header", {"name": "X-Api-Key", "value": "k1"})
    assert patterns.match(
        "fetches an oauth2 token from 'https://auth/token' with client 'id1' and secret 's1'") == \
        ("rest_oauth2", {"url": "https://auth/token", "client_id": "id1",
                         "client_secret": "s1"})


def test_bearer_and_basic_auth_set_authorization_header():
    ctx = _api_ctx()
    runner.execute_step("User sets the bearer token to 'tok123'", ctx)
    assert json.loads(ctx._vars["_REST_HEADERS"])["Authorization"] == "Bearer tok123"
    runner.execute_step("User uses basic auth with 'bob' and 'pw'", ctx)
    # base64("bob:pw")
    assert json.loads(ctx._vars["_REST_HEADERS"])["Authorization"] == "Basic Ym9iOnB3"


def test_oauth2_fetch_and_401_refresh_retry(monkeypatch):
    from noodle.agents.web import rest_client
    log = []

    def fake_rest_call(method, url, body=None, headers=None):
        log.append((method, url, body, dict(headers or {})))
        if "auth/token" in url:
            token = f"tok{sum('auth/token' in c[1] for c in log)}"
            return 200, json.dumps({"access_token": token}), {}
        # first API call 401s (expired token), retry succeeds
        auth = (headers or {}).get("Authorization", "")
        return (200, '{"ok": 1}', {}) if auth == "Bearer tok2" else (401, "expired", {})

    monkeypatch.setattr(rest_client, "rest_call", fake_rest_call)
    ctx = _api_ctx()
    runner.execute_step(
        "User fetches an oauth2 token from 'https://auth/token' with client 'id1' and secret 's1'", ctx)
    assert json.loads(ctx._vars["_REST_HEADERS"])["Authorization"] == "Bearer tok1"
    # grant is form-encoded, correct content type
    assert "grant_type=client_credentials" in log[0][2]
    assert log[0][3]["Content-Type"] == "application/x-www-form-urlencoded"

    runner.execute_step("User performs a GET request at 'https://api.example/me'", ctx)
    assert ctx._vars["REST_STATUS"] == "200"          # refreshed once, retried once
    assert json.loads(ctx._vars["_REST_HEADERS"])["Authorization"] == "Bearer tok2"
    # call sequence: token, api(401), token(refresh), api(200)
    assert len(log) == 4


# --- dotted-path JSON extraction --------------------------------------------------

def test_json_path_walks_nesting_and_indexes():
    data = {"data": {"items": [{"id": 7}, {"id": 8}]}}
    assert runner._json_path(data, "data.items[1].id") == 8
    with pytest.raises(AssertionError, match="Key 'nope' not found"):
        runner._json_path(data, "data.nope")
    with pytest.raises(AssertionError, match=r"Index \[5\] out of range"):
        runner._json_path(data, "data.items[5]")


def test_rest_extract_json_dotted_and_legacy():
    ctx = _api_ctx()
    ctx._vars["REST_BODY"] = json.dumps({"data": {"items": [{"id": 7}]}, "name": "Ada"})
    runner.execute_step("User extracts 'data.items[0].id' from the response and stores it as `uid`", ctx)
    assert ctx._vars["UID"] == "7"
    runner.execute_step("User extracts 'name' from the response and stores it as `who`", ctx)
    assert ctx._vars["WHO"] == "Ada"                  # legacy flat key still works


# --- LLM cost cap + step cache ------------------------------------------------------

def test_llm_call_cap(monkeypatch):
    monkeypatch.setenv("NOODLE_LLM_MAX_CALLS", "2")
    client.reset_calls()
    client._check_cap()
    client._check_cap()
    with pytest.raises(AssertionError, match="LLM call cap reached"):
        client._check_cap()
    client.reset_calls()
    client._check_cap()                               # reset restores budget


def test_llm_cap_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOODLE_LLM_MAX_CALLS", raising=False)
    client.reset_calls()
    for _ in range(50):
        client._check_cap()                           # unlimited — never raises


def test_llm_resolution_cache(monkeypatch):
    calls = []

    def fake_uncached(step_text):
        calls.append(step_text)
        return {"type": "click", "locator": "Login"}

    monkeypatch.setattr(step_resolver, "_llm_resolve_uncached", fake_uncached)
    step_resolver.clear_cache()
    a1 = step_resolver._llm_resolve("User does the thing")
    step_resolver._llm_resolve("User does the thing")
    assert calls == ["User does the thing"]           # one model call, two answers
    a1["locator"] = "mutated"
    assert step_resolver._llm_resolve("User does the thing")["locator"] == "Login"
    step_resolver.clear_cache()
    step_resolver._llm_resolve("User does the thing")
    assert len(calls) == 2                            # cache cleared → re-asked
    step_resolver.clear_cache()


# --- CLI --resolve -----------------------------------------------------------------

def test_cli_validate_resolve(tmp_path):
    from noodle.cli import _validate_resolve
    (tmp_path / "ok.feature").write_text(GOOD_FEATURE)
    assert _validate_resolve(tmp_path) == 0
    (tmp_path / "bad.feature").write_text("Feature broken\n  nope")
    assert _validate_resolve(tmp_path) == 1           # parse error fails
    assert _validate_resolve(tmp_path / "missing") == 1

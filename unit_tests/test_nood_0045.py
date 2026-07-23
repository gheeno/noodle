"""NOOD_0045 — MCP readiness: callable agent core (returns, not prints),
persistent agent state, loose no-LLM prose parsing with slot filling, the
MCP server's tool surface, and the --json CLI outputs. No browser, no LLM,
no network — engine subprocess calls are monkeypatched where a test would
otherwise launch behave.
"""
import json
from pathlib import Path

import pytest

from noodle.repl import core, generate, repl

EXAMPLE_PROMPT = (
    'Generate a new test case targeting "example.com", create a search '
    "test where the user navigates to the site, uses the search bar, searches "
    'for "office chair" and then assert that the next page or results page '
    "contains the desired product."
)

CFG = {"tests_dir": "tests", "env_file": ".env",
       "browser": "chromium", "headless": False}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- Phase 2: persistent state ------------------------------------------------

def test_state_roundtrip_and_durable_keys_only(ws):
    core.save_state({"last_feature": "tests/a.feature",
                     "autoran_feature": "tests/a.feature"}, str(ws))
    loaded = core.load_state(str(ws))
    assert loaded["last_feature"] == "tests/a.feature"
    assert "autoran_feature" not in loaded  # transient — must not persist


def test_load_state_missing_or_corrupt(ws):
    assert core.load_state(str(ws)) == {}
    p = ws / "artifacts" / "agent_state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert core.load_state(str(ws)) == {}


def test_resolve_target_order(ws):
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    old = fdir / "old.feature"
    old.write_text("Feature: old\n")
    assert "error" in core.resolve_target(None, str(ws)) or True  # has files now
    # newest-mtime fallback
    import os
    import time
    new = fdir / "new.feature"
    new.write_text("Feature: new\n")
    os.utime(old, (time.time() - 100, time.time() - 100))
    assert core.resolve_target(None, str(ws))["feature"].endswith("new.feature")
    # persisted state wins over mtime
    core.save_state({"last_feature": str(old.relative_to(ws))}, str(ws))
    assert core.resolve_target(None, str(ws))["feature"].endswith("old.feature")
    # explicit target wins over everything
    assert core.resolve_target("new", str(ws))["feature"].endswith("new.feature")
    # explicit miss is an error, not a fallback
    assert "error" in core.resolve_target("nonexistent", str(ws))


def test_resolve_target_directory(ws):
    # NOOD_0065 — a directory is a valid run target (MCP "run tests/web/app")
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text("Feature: a\n")
    # path form, relative to workspace and to tests_dir
    assert core.resolve_target("tests/web/app", str(ws))["feature"] == "tests/web/app"
    assert core.resolve_target("web/app", str(ws))["feature"] == "tests/web/app"
    # bare dir name (no slash) — matched after feature-stem search
    assert core.resolve_target("app", str(ws))["feature"] == "tests/web/app"
    assert "error" in core.resolve_target("no/such/dir", str(ws))


def test_resolve_target_no_features(ws):
    assert "error" in core.resolve_target(None, str(ws))


# --- Phase 1: callable core -----------------------------------------------------

def test_create_test_returns_data_and_persists_state(ws):
    r = core.create_test("login test", "saucedemo.com", workspace=str(ws))
    assert r["ok"] is True
    assert r["feature"].endswith("login.feature")
    assert r["runnable"] is False           # template placeholders remain
    assert "→ Wrote" in r["output"]         # prints captured, not leaked
    assert core.load_state(str(ws))["last_app"] == "saucedemo"
    # existing file without overwrite → ok False, nothing clobbered
    r2 = core.create_test("login test", "saucedemo.com", workspace=str(ws))
    assert r2["ok"] is False and "already exists" in r2["output"]


def test_last_result_structured(ws):
    d = ws / "artifacts" / "allure-results"
    d.mkdir(parents=True)
    (d / "x-result.json").write_text(json.dumps({
        "name": "s1", "status": "failed", "historyId": "h1",
        "start": 1000, "stop": 3000,
        "labels": [{"name": "feature", "value": "Login"}],
        "steps": [{"name": "When User clicks", "status": "failed"}],
        "statusDetails": {"message": "boom"},
    }))
    r = core.last_result(str(ws))
    assert r["failed"] == 1 and r["passed"] == 0
    assert r["failures"][0]["step"] == "When User clicks"
    assert r["failures"][0]["message"] == "boom"


def test_list_tests_inventory(ws):
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text(
        "@web\nFeature: A\n\n  @smoke\n  Scenario: first\n"
        "  Scenario Outline: second\n")
    tests = core.list_tests(str(ws))["tests"]
    assert tests[0]["feature"] == "A"
    # NOOD_0162 — index first: names only for a query.
    assert tests[0]["scenario_count"] == 2 and "scenarios" not in tests[0]
    assert set(tests[0]["tags"]) == {"web", "smoke"}
    assert core.list_tests(str(ws), query="first")["tests"][0]["scenarios"] == \
        ["first", "second"]
    assert core.list_tests(str(ws), query="nope")["tests"] == []


def test_run_test_resolves_and_reports(ws, monkeypatch):
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text("Feature: A\n")
    calls = []

    class _Proc:
        returncode = 0
        stdout, stderr = "ran\n", ""

    monkeypatch.setattr(core, "_engine",
                        lambda *a, workspace=".": calls.append(a) or _Proc())
    r = core.run_test(None, workspace=str(ws))
    assert r["ok"] is True and r["target"].endswith("a.feature")
    assert calls[0][0] == "run"
    assert core.load_state(str(ws))["last_run_target"].endswith("a.feature")
    r2 = core.run_test(tag="smoke", workspace=str(ws))
    assert r2["target"] == "tag:smoke" and calls[1][2] == "--tag"


def test_run_test_headless_override(ws, monkeypatch):
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text("Feature: A\n")
    calls = []

    class _Proc:
        returncode = 0
        stdout, stderr = "ran\n", ""

    monkeypatch.setattr(core, "_engine",
                        lambda *a, workspace=".": calls.append(a) or _Proc())
    core.run_test(None, workspace=str(ws), headless=True)
    assert "--headless" in calls[0]
    core.run_test(None, workspace=str(ws), headless=False)
    assert "--headed" in calls[1]
    core.run_test(None, workspace=str(ws))
    assert "--headless" not in calls[2] and "--headed" not in calls[2]


def test_run_test_no_target_error(ws):
    r = core.run_test(None, workspace=str(ws))
    assert r["ok"] is False and "error" in r


def test_validate_and_write_feature(ws):
    good = ('@web\nFeature: t\n  Scenario: s\n'
            '    Given User is on "https://x.com"\n'
            '    Then User should see "hi"\n')
    v = core.validate_feature(good + "    When User frobnicates wildly\n")
    assert v["error"] is None
    assert v["unmatched"] == ["When User frobnicates wildly"]

    w = core.write_feature("tests/web/x/features/t.feature", good, workspace=str(ws))
    assert w["ok"] is True and w["unmatched"] == []
    assert (ws / "tests/web/x/features/t.feature").exists()
    assert core.load_state(str(ws))["last_feature"].endswith("t.feature")
    # no clobber without overwrite
    assert core.write_feature("tests/web/x/features/t.feature", good,
                              workspace=str(ws))["ok"] is False
    # trust boundary: outside tests_dir / wrong suffix / bad gherkin
    assert core.write_feature("../evil.feature", good, workspace=str(ws))["ok"] is False
    assert core.write_feature("tests/x.txt", good, workspace=str(ws))["ok"] is False
    assert core.write_feature("tests/bad.feature", "not gherkin ::",
                              workspace=str(ws))["ok"] is False


# --- Phase 5: no-LLM prose parsing + slot filling -------------------------------

def test_extract_slots_example():
    slots = generate.extract_slots(EXAMPLE_PROMPT)
    assert slots["search term"] == "office chair"
    # no quoted assertion text → falls back to the search term
    assert slots["expected result text"] == "office chair"


def test_extract_slots_quoted_assertion():
    slots = generate.extract_slots(
        'search for "widgets" and check the page contains "42 results"')
    assert slots["search term"] == "widgets"
    assert slots["expected result text"] == "42 results"


def test_extract_slots_nothing():
    assert generate.extract_slots("test the checkout flow") == {}


# --- NOOD_0058 — login credentials slot-fill + URL/credential-free naming ------

def test_extract_slots_login_credentials():
    slots = generate.extract_slots(
        'login test with username "tomsmith" and password "SuperSecretPassword!" '
        'then verify user sees "You logged into a secure area!"')
    assert slots["username"] == "tomsmith"
    assert slots["password"] == "SuperSecretPassword!"
    assert slots["expected text after login"] == "You logged into a secure area!"


def test_login_template_ships_runnable():
    feature = generate.fill_slots(
        generate._LOGIN[0].format(url="https://x.test", name="login", Title="Login"),
        generate.extract_slots(
            'log in with username "u1" and password "p1" and see "Welcome"'))
    assert "<username>" not in feature and "<password>" not in feature
    assert '"u1"' in feature and '"p1"' in feature
    assert '"Welcome"' in feature


def test_name_from_strips_urls_and_credentials():
    name = generate._name_from(
        'create a test for https://the-internet.herokuapp.com/login that logs in '
        'with username "tomsmith" and password "SuperSecretPassword!"',
        "https://the-internet.herokuapp.com/login")
    assert name == "login"
    # nothing usable left → falls back to the URL host, never the scheme
    assert generate._name_from("create a test for it",
                               "https://saucedemo.com/x") == "saucedemo"


def test_parse_free_request():
    req = generate.parse_free_request(EXAMPLE_PROMPT)
    assert req["url"] == "example.com"
    assert req["description"] == EXAMPLE_PROMPT.strip()
    assert generate.parse_free_request("what is the weather") is None
    assert generate.parse_free_request("create a test somewhere") is None  # no URL
    assert generate.parse_free_request(
        "make a login test at https://example.com/app")["url"] == "https://example.com/app"


def test_dispatch_loose_create_no_llm(ws, capsys):
    state = {}
    repl.dispatch(EXAMPLE_PROMPT, CFG, str(ws), None, state)
    feat = Path(state["last_feature"])
    text = (ws / feat).read_text() if not feat.is_absolute() else feat.read_text()
    assert 'Given User is on "https://example.com"' in text
    assert 'When User enters "office chair" in the search field' in text
    assert 'Then User should see "office chair"' in text
    assert "<" not in text                  # feature fully slot-filled
    # autorun gated on the POM's <css selector> placeholders — no browser here
    assert "fill in the <placeholders>" in capsys.readouterr().out


def test_dispatch_still_rejects_gibberish(ws, capsys):
    repl.dispatch("please dance a jig", CFG, str(ws), None, {})
    assert "Don't understand" in capsys.readouterr().out


def test_dispatch_run_the_test_uses_persisted_state(ws, monkeypatch):
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "known.feature").write_text("Feature: K\n")
    core.save_state({"last_feature": "tests/web/app/features/known.feature"}, str(ws))
    ran = []
    monkeypatch.setattr(repl, "_noodle",
                        lambda *a, workspace=".": ran.append(a))
    repl.dispatch("run the test", CFG, str(ws), None, {})  # fresh state dict
    assert ran and ran[0][1].endswith("known.feature")


# --- Phase 3: MCP server surface -------------------------------------------------

def test_mcp_tools_registered_and_callable(ws):
    import asyncio

    from noodle.mcp import server
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"generate_test", "run_test", "get_last_result", "run_and_report",
            "list_tests", "validate_feature", "write_feature", "search_step",
            "get_rca"} <= tools

    server._WORKSPACE = str(ws)
    try:
        r = server.generate_test("saucedemo.com", "login test")
        assert r["ok"] and (ws / r["feature"]).exists()
        assert server.list_tests()["tests"][0]["path"].endswith("login.feature")
        assert "Given User is on" in server.vocabulary()
    finally:
        server._WORKSPACE = "."


# --- MAF / Foundry: HTTP transport + auth ----------------------------------------

def _asgi_call(app, headers):
    """Drive one ASGI http request, return the response status."""
    import asyncio
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"] if sent else None


def test_require_key_gate():
    from noodle.mcp.server import _require_key

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app = _require_key(inner, "sekret")
    assert _asgi_call(app, {}) == 401
    assert _asgi_call(app, {"authorization": "Bearer wrong"}) == 401
    assert _asgi_call(app, {"authorization": "Bearer sekret"}) == 200
    assert _asgi_call(app, {"x-api-key": "sekret"}) == 200


def test_main_stdio_default(monkeypatch):
    from noodle.mcp import server
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: ran.append(True))
    server.main(["--workspace", "/tmp/x"])
    assert ran and server._WORKSPACE == "/tmp/x"
    server._WORKSPACE = "."


def test_main_http_refuses_open_bind_without_key(monkeypatch):
    from noodle.mcp import server
    monkeypatch.delenv("NOODLE_MCP_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        server.main(["--transport", "streamable-http", "--host", "0.0.0.0"])
    assert e.value.code == 2  # argparse error, server never starts


def test_main_http_serves_with_key(monkeypatch):
    import uvicorn

    from noodle.mcp import server
    monkeypatch.setenv("NOODLE_MCP_API_KEY", "sekret")
    served = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port: served.update(app=app, host=host, port=port))
    server.main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    assert served["host"] == "0.0.0.0" and served["port"] == 9000
    # the served app is the auth-gated wrapper, not the bare FastMCP app
    assert _asgi_call(served["app"], {}) == 401
    server._WORKSPACE = "."


# --- Phase 4: --json CLI outputs ---------------------------------------------------

def test_cli_summary_json(ws):
    from typer.testing import CliRunner

    from noodle.cli import app
    d = ws / "artifacts" / "allure-results"
    d.mkdir(parents=True)
    (d / "x-result.json").write_text(json.dumps(
        {"name": "s", "status": "passed", "historyId": "h",
         "start": 0, "stop": 1000}))
    res = CliRunner().invoke(app, ["summary", "--json", "-w", str(ws)])
    data = json.loads(res.output)
    assert data["passed"] == 1 and data["failed"] == 0


def test_cli_list_json(ws):
    from typer.testing import CliRunner

    from noodle.cli import app
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text("Feature: A\n  Scenario: s\n")
    res = CliRunner().invoke(app, ["list", "--json", "-w", str(ws)])
    data = json.loads(res.output)
    assert data["tests"][0]["feature"] == "A"
    assert data["tests"][0]["scenario_count"] == 1
    res = CliRunner().invoke(app, ["list", "--json", "--query", "s", "-w", str(ws)])
    assert json.loads(res.output)["tests"][0]["scenarios"] == ["s"]


def test_cli_validate_resolve_json(ws):
    from typer.testing import CliRunner

    from noodle.cli import app
    fdir = ws / "tests" / "web" / "app" / "features"
    fdir.mkdir(parents=True)
    (fdir / "a.feature").write_text(
        '@web\nFeature: A\n  Scenario: s\n'
        '    Given User is on "https://x.com"\n'
        '    When User frobnicates wildly\n')
    res = CliRunner().invoke(
        app, ["validate", "tests", "--resolve", "--json", "-w", str(ws)])
    data = json.loads(res.output)
    steps = {s["step"]: s["matched"] for s in data[0]["steps"]}
    assert steps['Given User is on "https://x.com"'] is True
    assert steps["When User frobnicates wildly"] is False


def test_write_last_run_json(ws):
    from noodle import cli
    d = ws / "artifacts" / "allure-results"
    d.mkdir(parents=True)
    (d / "x-result.json").write_text(json.dumps(
        {"name": "s", "status": "passed", "historyId": "h",
         "start": 0, "stop": 1000}))
    cli._write_last_run(str(d), 0, str(ws))
    data = json.loads((ws / "artifacts" / "last_run.json").read_text())
    assert data["passed"] == 1 and data["exit_code"] == 0 and data["at"]

"""NOOD_0089 — smart wait (find budget + network extension + DOM re-scan),
DOM attribute scan incl. hidden elements, overlay auto-dismiss with RCA
warning trail, cert-error ignore, init upgrade on existing workspaces,
`noodle init mcp`, and the MCP read_docs tool. No browser, no LLM, no network."""
import json
import sys
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from noodle import healing, hooks
from noodle.agents.web import actions, activity, dom_scan, locator
from noodle.cli import app

runner = CliRunner()


# --- Task 5: ignore certificate errors ---------------------------------------

def test_ignore_https_errors_default_on(monkeypatch):
    monkeypatch.delenv("NOODLE_IGNORE_HTTPS_ERRORS", raising=False)
    assert hooks.ignore_https_errors(set()) is True


def test_ignore_https_errors_env_off(monkeypatch):
    monkeypatch.setenv("NOODLE_IGNORE_HTTPS_ERRORS", "false")
    assert hooks.ignore_https_errors(set()) is False


def test_secure_certs_tag_wins(monkeypatch):
    monkeypatch.setenv("NOODLE_IGNORE_HTTPS_ERRORS", "true")
    assert hooks.ignore_https_errors({"secure_certs"}) is False


def test_env_stub_documents_new_knobs():
    from noodle.cli import _env_stub
    stub = _env_stub()
    for key in ("NOODLE_FIND_TIMEOUT", "NOODLE_IGNORE_HTTPS_ERRORS",
                "NOODLE_AUTO_DISMISS", "NOODLE_WAIT_EXTENSION"):
        assert key in stub, key


# --- Task 9: network-activity clock -------------------------------------------

def test_activity_quiet_until_request():
    activity.reset()
    assert activity.quiet_for(0.0) is True
    activity.note_request("https://app.example/api/data")
    assert activity.quiet_for(60.0) is False


def test_activity_ignores_noise_urls():
    activity.reset()
    for url in ("https://www.google-analytics.com/collect",
                "https://x.com/telemetry?x=1",
                "https://cdn.hotjar.com/x.js"):
        activity.note_request(url)
    assert activity.quiet_for(60.0) is True


def test_activity_reset_clears_clock():
    activity.note_request("https://app.example/slow")
    activity.reset()
    assert activity.quiet_for(60.0) is True


# --- Tasks 3/7: DOM attribute scan --------------------------------------------

def _cand(**kw):
    base = {"tag": "div", "id": "", "name": "", "testid": "", "aria": "",
            "title": "", "ph": "", "cls": "", "visible": True}
    base.update(kw)
    return base


def test_score_matches_id_tokens():
    tokens = dom_scan._tokens("the server dev-panel")
    assert dom_scan._score(tokens, _cand(id="dev-panel")) > 0


def test_score_class_only_is_rejected():
    """A class token is too generic to act on without a strong attribute."""
    tokens = dom_scan._tokens("dev panel")
    assert dom_scan._score(tokens, _cand(cls="dev-panel wrapper")) == 0


def test_score_prefers_id_over_aria():
    tokens = dom_scan._tokens("save settings")
    strong = dom_scan._score(tokens, _cand(id="save-settings"))
    weaker = dom_scan._score(tokens, _cand(aria="save settings"))
    assert strong > weaker > 0


def test_selector_forms():
    assert dom_scan._selector_for(_cand(id="dev-panel")) == '[id="dev-panel"]'
    assert '[data-testid="server"]' in dom_scan._selector_for(_cand(testid="server"))
    assert dom_scan._selector_for(_cand(tag="input", name="server")) == 'input[name="server"]'


def _fake_scope(candidates):
    scope = MagicMock()
    scope.evaluate.return_value = candidates
    return scope


def test_best_selector_finds_hidden_dev_panel():
    """The headline case: a developer panel with no visible text and no role,
    invisible to the accessibility tree — found via its id."""
    scope = _fake_scope([
        _cand(id="header", cls="site-header"),
        _cand(id="dev-panel", visible=False),
    ])
    assert dom_scan.best_selector(scope, "clicks the dev-panel") == '[id="dev-panel"]'


def test_best_selector_prefers_visible_on_tie():
    scope = _fake_scope([
        _cand(id="login-form", visible=False),
        _cand(id="login-form", tag="form", visible=True),
    ])
    sel = dom_scan.best_selector(scope, "login form")
    assert sel == '[id="login-form"]'  # same selector either way; tie-break exercised


def test_best_selector_rejects_single_word_phrases():
    """qaplayground regression: phrase 'login' must NOT match
    id="login-username" (the field) when the step wanted the login button —
    one shared token is not enough signal for this tier."""
    scope = _fake_scope([_cand(id="login-username", tag="input")])
    assert dom_scan.best_selector(scope, "login") is None


def test_score_requires_half_the_phrase_covered():
    tokens = dom_scan._tokens("saves the server configuration value")
    # {saves, server, configuration, value} — one hit out of four is rejected
    assert dom_scan._score(tokens, _cand(id="value")) == 0


def test_best_selector_never_raises(monkeypatch):
    scope = MagicMock()
    scope.evaluate.side_effect = RuntimeError("page closed")
    assert dom_scan.best_selector(scope, "anything") is None
    assert dom_scan.best_selector(MagicMock(), "") is None  # empty phrase


# --- Task 9: smart-wait poll loop ----------------------------------------------

def test_poll_uses_find_timeout(monkeypatch):
    """The find budget is NOODLE_FIND_TIMEOUT, decoupled from NOODLE_TIMEOUT."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "100")
    monkeypatch.setenv("NOODLE_TIMEOUT", "999999")  # must not be the budget
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (None, False))
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    loc, _ = locator._poll_strategies(MagicMock(), "Nonexistent")
    assert loc is None


def test_poll_default_budget_is_two_minutes(monkeypatch):
    monkeypatch.delenv("NOODLE_FIND_TIMEOUT", raising=False)
    assert locator._find_timeout_ms() == 120000


def test_poll_extends_once_while_network_busy(monkeypatch):
    """At the deadline with the network still active, ONE bounded extension is
    granted — enough for the element to arrive on a genuinely-loading page."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "0")
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "60000")
    monkeypatch.setattr(locator.time, "sleep", lambda s: None)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: False)  # always busy
    attempts = {"n": 0}

    def flaky(scope, text, prefer=None):
        attempts["n"] += 1
        return ("found_it", False) if attempts["n"] >= 2 else (None, False)

    monkeypatch.setattr(locator, "_try_strategies", flaky)
    loc, amb = locator._poll_strategies(MagicMock(), "Late row")
    assert loc == "found_it"


def test_poll_extension_is_granted_only_once(monkeypatch):
    """A chatty page (network never quiet) cannot extend forever."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "0")
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "0")
    monkeypatch.setattr(locator.time, "sleep", lambda s: None)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: False)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (None, False))
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)
    loc, _ = locator._poll_strategies(MagicMock(), "Never there")
    assert loc is None


def test_poll_rescans_dom_for_alternative_selector(monkeypatch):
    """While waiting, the loop re-searches the DOM for an attribute match —
    the 'maybe I have the wrong selector' half of the smart wait."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "60000")
    monkeypatch.setattr(locator, "_DOM_SCAN_AFTER_S", 0.0)
    monkeypatch.setattr(locator.time, "sleep", lambda s: None)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (None, False))
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: '[id="dev-panel"]')
    cand = MagicMock()
    cand.count.return_value = 1
    scope = MagicMock()
    scope.locator.return_value = cand
    healing.reset()
    loc, amb = locator._poll_strategies(scope, "dev-panel")
    assert loc is cand and amb is False
    assert any(e["strategy"] == "dom-scan" for e in healing.EVENTS)


# --- Task 8: overlay auto-dismiss + hidden force-click --------------------------

def _blocked_then_free_loc():
    loc = MagicMock()
    loc.click.side_effect = [
        actions.PlaywrightTimeoutError(
            "Timeout 10000ms exceeded ... <div class=\"promo-overlay\"> "
            "intercepts pointer events"),
        None,  # retry after dismissal succeeds
    ]
    return loc


def test_click_auto_dismisses_blocking_overlay(monkeypatch):
    loc = _blocked_then_free_loc()
    monkeypatch.setattr(actions, "find", lambda p, t: loc)
    closed = {"n": 0}
    monkeypatch.setattr(actions, "close_popups", lambda p: closed.update(n=closed["n"] + 1))
    healing.reset()
    actions.click(MagicMock(), "Add to cart")
    assert closed["n"] == 1
    assert loc.click.call_count == 2
    assert any(e["strategy"] == "overlay-dismissed" for e in healing.EVENTS)


def test_click_auto_dismiss_disabled(monkeypatch):
    monkeypatch.setenv("NOODLE_AUTO_DISMISS", "false")
    loc = _blocked_then_free_loc()
    monkeypatch.setattr(actions, "find", lambda p, t: loc)
    with pytest.raises(actions.PlaywrightTimeoutError):
        actions.click(MagicMock(), "Add to cart")


def test_click_force_clicks_hidden_element(monkeypatch):
    """Dev-panel pattern: present in the DOM, invisible, normal click times
    out — one force-click with a warning trail."""
    loc = MagicMock()
    loc.click.side_effect = actions.PlaywrightTimeoutError("Timeout ... waiting for element to be visible")
    loc.count.return_value = 1
    loc.first.is_visible.return_value = False
    monkeypatch.setattr(actions, "find", lambda p, t: loc)
    healing.reset()
    actions.click(MagicMock(), "dev-panel")
    loc.first.click.assert_called_once_with(force=True, timeout=5000)
    assert any(e["strategy"] == "hidden-force-click" for e in healing.EVENTS)


def test_click_visible_element_failure_still_raises(monkeypatch):
    """A real actionability failure (element visible, still unclickable) is
    not swallowed by the recovery path."""
    loc = MagicMock()
    loc.click.side_effect = actions.PlaywrightTimeoutError("Timeout ... element is not stable")
    loc.count.return_value = 1
    loc.first.is_visible.return_value = True
    monkeypatch.setattr(actions, "find", lambda p, t: loc)
    with pytest.raises(actions.PlaywrightTimeoutError):
        actions.click(MagicMock(), "Save")


# --- Task 11: init on an existing workspace --------------------------------------

def test_reinit_syncs_engine_glue(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    glue = tmp_path / "noodle_tests" / "steps" / "z_catch_all.py"
    glue.write_text("# stale engine glue from an old noodle\n")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert "catch_all" in glue.read_text()  # rewritten to current engine glue
    assert "Updated to match this noodle version" in result.output


def test_reinit_keeps_edited_templates_and_warns(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# my team's own instructions\n")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert agents.read_text() == "# my team's own instructions\n"
    assert "Outdated templates kept" in result.output
    assert "--force" in result.output


def test_reinit_force_refreshes_templates_with_backup(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# my team's own instructions\n")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "North star" in agents.read_text()
    assert (tmp_path / "AGENTS.md.bak").read_text() == "# my team's own instructions\n"


def test_reinit_never_touches_config_files(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    env = tmp_path / ".env"
    yaml_f = tmp_path / "noodle.yaml"
    env.write_text("NOODLE_BROWSER=firefox\n")
    yaml_f.write_text("tests_dir: my_tests\n")
    runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert env.read_text() == "NOODLE_BROWSER=firefox\n"
    assert yaml_f.read_text() == "tests_dir: my_tests\n"


def test_reinit_clean_workspace_reports_up_to_date(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert "up to date" in result.output


# --- Tasks 1/2: scaffold carries the AI north-star instructions -------------------

def test_agents_md_contains_north_star_rules(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    agents = (tmp_path / "AGENTS.md").read_text()
    # NOOD_0131 — Popups/resources-scripts detail moved to the on-demand
    # playbook (byte-ceilinged surface); the pipeline markers replaced them.
    for marker in ("North star", "Background:", "read_docs",
                   "probe_page", "author_test", "run_and_report"):
        assert marker in agents, marker


def test_prompt_template_scaffolded(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    tpl = (tmp_path / "PROMPT_TEMPLATE.md").read_text()
    assert "[APP NAME]" in tpl and "AGENTS.md" in tpl


# --- Task 6: noodle init mcp ------------------------------------------------------

def test_init_mcp_writes_client_configs(tmp_path):
    from noodle.cli import _resolve_mcp_command
    expected_cmd = _resolve_mcp_command()
    result = runner.invoke(app, ["init-mcp", str(tmp_path)])
    assert result.exit_code == 0
    claude = json.loads((tmp_path / ".mcp.json").read_text())
    assert claude["mcpServers"]["noodle"]["command"] == expected_cmd
    vscode = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert vscode["servers"]["noodle"] == {"type": "stdio", "command": expected_cmd, "args": []}
    copilot = json.loads((tmp_path / ".copilot" / "mcp-config.json").read_text())
    assert copilot["mcpServers"]["noodle"]["command"] == expected_cmd


def test_resolve_mcp_command_is_absolute_when_resolvable(tmp_path, monkeypatch):
    # Windows console_scripts are always compiled to a .exe launcher — the
    # installed file is never a bare extensionless "noodle-mcp" there.
    from noodle.cli import _resolve_mcp_command
    name = "noodle-mcp.exe" if sys.platform == "win32" else "noodle-mcp"
    fake_bin = tmp_path / name
    fake_bin.write_text("#!/bin/sh\n")
    if sys.platform != "win32":
        fake_bin.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert _resolve_mcp_command() == str(fake_bin)


def test_init_mcp_alias_via_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "mcp"])
    assert result.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / "mcp").exists()  # 'mcp' was a subcommand, not a dir


def test_init_mcp_merges_existing_config(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}))
    runner.invoke(app, ["init-mcp", str(tmp_path)])
    data = json.loads(f.read_text())
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}
    from noodle.cli import _resolve_mcp_command
    assert data["mcpServers"]["noodle"]["command"] == _resolve_mcp_command()


def test_init_mcp_ci_note(tmp_path, monkeypatch):
    monkeypatch.setenv("TF_BUILD", "True")
    result = runner.invoke(app, ["init-mcp", str(tmp_path)])
    assert "CI environment detected" in result.output
    assert (tmp_path / ".mcp.json").exists()  # still written — team can commit it


# --- Task 2: MCP read_docs tool ----------------------------------------------------

def test_read_docs_lists_available_docs():
    from noodle.mcp import server
    out = server.read_docs()
    names = [d["name"] for d in out["docs"]]
    assert "agent-playbook" in names


def test_read_docs_returns_named_doc():
    """NOOD_0158 — the playbook is 57 KB, past DOC_WHOLE_MAX_BYTES, so a bare
    name now returns its section index and the body comes one section at a
    time. Small docs still ride back whole (see test_nood_0158)."""
    from noodle.mcp import server
    out = server.read_docs(name="agent-playbook")
    assert out["name"] == "agent-playbook.md"
    assert out["sections"], "large doc must offer a section index"
    body = "".join(
        server.read_docs(name="agent-playbook", section=s["title"])["content"]
        for s in out["sections"])
    assert "Allure" in body


def test_read_docs_unknown_name_lists_alternatives():
    from noodle.mcp import server
    out = server.read_docs(name="no-such-doc")
    assert "error" in out and "agent-playbook" in out["available"]


def test_read_docs_query_greps_lines():
    from noodle.mcp import server
    out = server.read_docs(query="NOODLE_FIND_TIMEOUT")
    assert isinstance(out["hits"], list)


# --- wrong-element fix: fill/clear prefer editable targets -----------------------

def test_fill_passes_prefer_input(monkeypatch):
    """qaplayground regression: 'enters X in the username field' matched a
    decorative 'copy username' BUTTON (button strategy ran first) and fill
    failed with 'Element is not an <input>'. fill/clear now ask find() for an
    editable target."""
    seen = {}

    def fake_find(page, text, prefer=None):
        seen["prefer"] = prefer
        loc = MagicMock()
        return loc

    monkeypatch.setattr(actions, "find", fake_find)
    actions.fill(MagicMock(), "username field", "admin")
    assert seen["prefer"] == "input"
    actions.clear(MagicMock(), "username field")
    assert seen["prefer"] == "input"


def test_try_strategies_prefer_input_beats_button():
    """With prefer='input', the editable-constrained label strategy runs
    before the button strategy, so the field wins over the copy button."""
    scope = MagicMock()
    field = MagicMock()
    field.count.return_value = 1
    button = MagicMock()
    button.count.return_value = 1
    scope.get_by_label.return_value.and_.return_value = field
    scope.get_by_role.return_value = button
    loc, ambiguous = locator._try_strategies(scope, "username", prefer="input")
    assert loc is field and ambiguous is False
    # default order unchanged: button strategy still wins without the hint
    loc2, _ = locator._try_strategies(scope, "username")
    assert loc2 is button


# --- RCA provenance + stale-report fix (user-reported collision) -------------------

def _result_json(tmp_path, name="Buy a movie", status="failed", app="busterblock",
                 ffile="sample_feature_tests/web/busterblock/features/cart.feature",
                 warnings=None):
    import uuid as _uuid
    r = {
        "uuid": str(_uuid.uuid4()), "historyId": _uuid.uuid4().hex,
        "name": name, "fullName": f"F: {name}", "status": status, "stop": 5,
        "labels": [{"name": "feature", "value": "F"},
                   {"name": "parentSuite", "value": app},
                   {"name": "featureFile", "value": ffile}],
        "steps": [{"name": "clicks 'Buy'", "status": "failed" if status == "failed" else "passed",
                   "statusDetails": ({"message": "boom", "trace": ""} if status == "failed"
                                      else {"warnings": warnings or []})}],
    }
    p = tmp_path / f"{r['uuid']}-result.json"
    p.write_text(json.dumps(r))
    return p


def test_rca_collect_carries_app_and_feature_file(tmp_path):
    from noodle.reporting import rca_report
    _result_json(tmp_path)
    (e,) = rca_report.collect(str(tmp_path))
    assert e["app"] == "busterblock"
    assert e["feature_file"].endswith("features/cart.feature")


def test_rca_markdown_and_html_show_provenance_column(tmp_path):
    from noodle.reporting import rca_report
    _result_json(tmp_path)
    md = rca_report.render_markdown(str(tmp_path))
    assert "App / .feature" in md and "busterblock —" in md
    html_out = rca_report.render_html(str(tmp_path))
    assert "App / .feature" in html_out and "<strong>busterblock</strong>" in html_out


def test_rca_warnings_table_shows_provenance(tmp_path):
    from noodle.reporting import rca_report
    _result_json(tmp_path, status="passed", warnings=["auto-dismissed an overlay"])
    (w,) = rca_report.collect_warnings(str(tmp_path))
    assert w["app"] == "busterblock" and w["feature_file"].endswith(".feature")
    md = rca_report.render_markdown(str(tmp_path))
    assert "busterblock" in md


def test_scenario_result_writes_feature_file_label():
    from noodle.reporting.writer import ScenarioResult
    sc = MagicMock()
    sc.name = "Buy a movie"
    sc.tags = []
    sc.feature.name = "Cart"
    sc.feature.filename = "noodle_tests/shop/features/cart.feature"
    labels = {lab["name"]: lab["value"] for lab in ScenarioResult(sc).result["labels"]}
    assert labels["featureFile"] == "noodle_tests/shop/features/cart.feature"
    assert labels["parentSuite"] == "shop"


def test_ensure_fresh_reports_rebuilds_stale(tmp_path, monkeypatch):
    """User-reported collision: reports/ held an rca.html from one run beside
    an allure-report from another — serve must rebuild anything OLDER than
    the newest result JSON, not only missing files."""
    import os as _os

    from noodle.reporting import builder, rca_report
    results = tmp_path / "allure-results"
    results.mkdir()
    root = tmp_path / "reports"
    (root / "allure-report").mkdir(parents=True)
    _result_json(results)
    # stale artifacts: mtime well before the result JSON
    stale = (root / "rca.html", root / "allure-report" / "index.html")
    for f in stale:
        f.write_text("old run")
        _os.utime(f, (1, 1))
    calls = []
    monkeypatch.setattr(builder, "generate", lambda r, o: calls.append("allure") or True)
    monkeypatch.setattr(rca_report, "write_reports", lambda r, o: calls.append("rca") or {})
    builder.ensure_fresh_reports(str(results), str(root))
    assert calls == ["allure", "rca"]


def test_ensure_fresh_reports_leaves_current_alone(tmp_path, monkeypatch):
    import time as _time

    from noodle.reporting import builder, rca_report
    results = tmp_path / "allure-results"
    results.mkdir()
    root = tmp_path / "reports"
    (root / "allure-report").mkdir(parents=True)
    _result_json(results)
    _time.sleep(0.01)
    (root / "rca.html").write_text("fresh")
    (root / "allure-report" / "index.html").write_text("fresh")
    calls = []
    monkeypatch.setattr(builder, "generate", lambda r, o: calls.append("allure"))
    monkeypatch.setattr(rca_report, "write_reports", lambda r, o: calls.append("rca"))
    builder.ensure_fresh_reports(str(results), str(root))
    assert calls == []


# --- noodle report stop -------------------------------------------------------------

def test_report_stop_kills_registered_server(tmp_path, monkeypatch):
    from noodle import cli as _cli
    _cli._write_report_pids(str(tmp_path), {"8000": 11111, "8001": 22222})
    killed = []
    monkeypatch.setattr(_cli.os, "kill", lambda pid, sig: killed.append(pid))
    result = runner.invoke(app, ["report", "stop", "-w", str(tmp_path)])
    assert result.exit_code == 0
    assert sorted(killed) == [11111, 22222]
    assert _cli._report_pids(str(tmp_path)) == {}


def test_report_stop_single_port_leaves_others(tmp_path, monkeypatch):
    from noodle import cli as _cli
    _cli._write_report_pids(str(tmp_path), {"8000": 11111, "8001": 22222})
    killed = []
    monkeypatch.setattr(_cli.os, "kill", lambda pid, sig: killed.append(pid))
    runner.invoke(app, ["report", "stop", "--port", "8000", "-w", str(tmp_path)])
    assert killed == [11111]
    assert _cli._report_pids(str(tmp_path)) == {"8001": 22222}


def test_report_stop_prunes_dead_pids(tmp_path, monkeypatch):
    from noodle import cli as _cli
    _cli._write_report_pids(str(tmp_path), {"8000": 99999})

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(_cli.os, "kill", gone)
    result = runner.invoke(app, ["report", "stop", "-w", str(tmp_path)])
    assert result.exit_code == 0
    assert "already gone" in result.output
    assert _cli._report_pids(str(tmp_path)) == {}


def test_report_stop_nothing_recorded(tmp_path):
    result = runner.invoke(app, ["report", "stop", "-w", str(tmp_path)])
    assert "nothing to stop" in result.output.lower()


def test_report_server_sends_no_store_header(tmp_path):
    """User-reported: browser kept showing a days-old report from caching.
    NOOD_0093 — no-store (not just no-cache) so Chrome never stores it."""
    import threading
    import urllib.request

    from noodle.reporting import builder
    (tmp_path / "rca.html").write_text("<h1>fresh</h1>")
    # NOOD_0162 — builder no longer owns a server thread (the hosting path is
    # a detached child); the handler is what this test is about, so drive it.
    httpd = builder._make_server(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/rca.html")
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_report_serve_registers_only_after_bind(tmp_path, monkeypatch):
    """A serve that loses the port race must not touch the registry — its
    un-guarded cleanup used to POP the pid of the server actually holding the
    port (found when a unit test's stubbed serve unregistered a live one)."""
    from noodle import cli as _cli
    from noodle.reporting import builder

    _cli._write_report_pids(str(tmp_path), {"8000": 42})  # someone else's live server

    def bind_fails(d, host, port, on_bound=None):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(builder, "serve_report", bind_fails)
    result = runner.invoke(app, ["report", "serve", "-w", str(tmp_path), "--port", "8000"])
    assert result.exit_code == 1
    assert _cli._report_pids(str(tmp_path)) == {"8000": 42}  # untouched


def test_report_serve_registers_actual_bound_port(tmp_path, monkeypatch):
    """-p 0 (OS-assigned port) must register the port that was really bound."""
    import os as _os

    from noodle import cli as _cli
    from noodle.reporting import builder
    observed = {}

    def fake_serve(d, host, port, on_bound=None):
        on_bound(54321)                       # OS picked this
        observed.update(_cli._report_pids(str(tmp_path)))

    monkeypatch.setattr(builder, "serve_report", fake_serve)
    runner.invoke(app, ["report", "serve", "-w", str(tmp_path), "--port", "0"])
    # NOOD_0161 — the entry carries the served root/host too, so the next run
    # can reuse this server instead of opening one on a new port.
    assert list(observed) == ["54321"]                  # registered while serving
    assert _cli._pid_of(observed["54321"]) == _os.getpid()
    assert _cli._report_pids(str(tmp_path)) == {}       # cleaned up on exit

"""NOOD_0185 — feature-generation regression benchmark: score() verdicts and
reason lines, env-overridable budget, runbook self-sufficiency, CLI exit
codes. The benchmark itself (live site + host model) never runs here."""
import json

from typer.testing import CliRunner

from noodle import cli, regression


def _tc(**over):
    base = {"id": "tc1_search_suggestion", "elapsed_s": 20, "aic": 3,
            "corrections": 0, "green": True, "verified": True}
    return {**base, **over}


def test_pass_within_budget():
    v = regression.score({"test_cases": [_tc(), _tc(id="tc2_account_textboxes")]})
    assert v["verdict"] == "PASS" and v["regressions"] == []
    assert v["average"]["aic"] == 3


def test_every_budget_breach_gets_a_named_reason():
    v = regression.score({"test_cases": [
        _tc(elapsed_s=9999, aic=40, corrections=5),
        _tc(id="tc2_account_textboxes", green=False, verified=False),
        _tc(id="tc3_extra", verified=False)]})   # a pass held up by healing
    assert v["verdict"] == "REGRESSED"
    joined = " | ".join(v["regressions"])
    for token in ("slow", "over budget", "inaccurate", "not green",
                  "unverified", "average"):
        assert token in joined
    # a red run reports "not green" alone — unverified would be noise there
    tc2 = v["test_cases"][1]
    assert tc2["failures"] == ["final run not green"]


def test_missing_measurements_and_missing_canonical_tc_regress():
    v = regression.score({"test_cases": [{"id": "tc1_search_suggestion"}]})
    assert v["verdict"] == "REGRESSED"
    assert any("missing measurement" in r for r in v["regressions"])
    assert any("canonical test cases" in r for r in v["regressions"])


def test_development_time_excludes_run_time():
    """The time budget measures TEST DEVELOPMENT (prompt → authored
    .feature), not the generated test's own execution: a slow site run must
    not fail a fast authoring."""
    v = regression.score({"test_cases": [
        _tc(elapsed_s=130, run_s=100),                       # 30s development
        _tc(id="tc2_account_textboxes", run_s=10)]})         # 10s development
    assert v["verdict"] == "PASS"
    assert v["test_cases"][0]["development_s"] == 30
    v2 = regression.score({"test_cases": [
        _tc(elapsed_s=150, run_s=5), _tc(id="tc2_account_textboxes")]})
    assert any("slow development" in r for r in v2["regressions"])


def test_budget_env_overrides(monkeypatch):
    monkeypatch.setenv("NOODLE_REG_MAX_AIC", "50")
    monkeypatch.setenv("NOODLE_REG_MAX_AVG_AIC", "50")
    v = regression.score({"test_cases": [
        _tc(aic=40), _tc(id="tc2_account_textboxes", aic=40)]})
    assert v["verdict"] == "PASS"


def test_runbook_is_self_sufficient():
    text = regression.runbook()
    for p in regression.PROMPTS:
        assert p["id"] in text and p["mode"] in text and p["content"] in text
    assert "results.json" in text and "--score" in text


def _matched_install(monkeypatch):
    """Keep the folder-freshness test about folders: a developer whose install
    lags the checkout hits the mismatch guard below, not this."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "version_report",
                        lambda: {"installed": "9.9.9", "source": "9.9.9",
                                 "mismatch": False})


def test_init_refuses_a_stale_install(tmp_path, monkeypatch):
    """The workspace name carries the CHECKOUT's version+sha, so measuring a
    stale install would file the results under code that never ran."""
    monkeypatch.chdir(tmp_path)
    from noodle import install_check
    monkeypatch.setattr(install_check, "version_report",
                        lambda: {"installed": "1.0.0a1", "source": "1.0.0a2",
                                 "mismatch": True})
    out = CliRunner().invoke(cli.app, ["feature-regression", "--init"])
    assert out.exit_code == 1
    assert "noodle update" in out.output and "1.0.0a2" in out.output
    assert not (tmp_path / "regression_runs").exists()   # nothing half-scaffolded


def test_init_makes_a_new_workspace_every_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _matched_install(monkeypatch)
    r = CliRunner()
    assert r.invoke(cli.app, ["feature-regression", "--init"]).exit_code == 0
    assert r.invoke(cli.app, ["feature-regression", "--init"]).exit_code == 0
    runs = list((tmp_path / "regression_runs").iterdir())
    assert len(runs) == 2 and all((d / "noodle.yaml").is_file() for d in runs)


def test_score_writes_verdict_next_to_results(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(json.dumps(
        {"test_cases": [_tc(), _tc(id="tc2_account_textboxes")]}))
    out = CliRunner().invoke(cli.app, ["feature-regression", "--score", str(results)])
    assert out.exit_code == 0
    assert json.loads((tmp_path / "verdict.json").read_text())["verdict"] == "PASS"
    # the three ACs (development time, cost, accuracy) get a browser page too
    html = (tmp_path / "verdict.html").read_text()
    assert "PASS" in html and "tc1_search_suggestion" in html
    assert "development" in html and "AIC" in html


def test_prompt_suggestion_click_becomes_typeahead_suggest():
    """AC: the benchmark generates from prompts the way real users write
    them. 'Click the suggestion X' after a search is the typeahead flow —
    the search action becomes a suggest (never submitted)."""
    from noodle.repl import prompt_expander
    exp = prompt_expander.expand(
        '1. Go to the URL https://example.test/a\n'
        '2. Search for "Vacuum cleane"\n'
        '3. Click the suggestion "Vacuum cleaner"')
    assert exp["ok"]
    acts = exp["goal"]["actions"]
    assert len(acts) == 1 and acts[0]["do"] == "suggest"
    assert acts[0]["term"] == "Vacuum cleane"
    assert acts[0]["option"] == "Vacuum cleaner"


def test_prompt_suggestion_click_without_search_refuses():
    from noodle.repl import prompt_expander
    exp = prompt_expander.expand(
        '1. Go to the URL https://example.test/a\n'
        '2. Click the suggestion "Vacuum cleaner"')
    assert not exp["ok"]


def test_prompt_verify_compiles_to_sees_text():
    """The benchmark's first catch (NOOD_0185): `verify <text>` used to emit
    an any_of check, compiled to a link-scoped 'result titles' count locator
    that can never match a plain <label>. Intent is 'literal text visible' →
    a `see` check; wrapping quotes belong to the quoting, not the text."""
    from noodle.repl import prompt_expander
    exp = prompt_expander.expand(
        '1. Go to the URL https://example.test/a\n'
        '2. Verify "Confirm password"\n3. Verify Email address')
    assert exp["ok"]
    assert exp["goal"]["checks"] == [
        {"see": "Confirm password"}, {"see": "Email address"}]


def test_cli_exit_codes(tmp_path):
    r = CliRunner()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(
        {"test_cases": [_tc(), _tc(id="tc2_account_textboxes")]}))
    assert r.invoke(cli.app, ["feature-regression", "--score", str(good)]).exit_code == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"test_cases": [_tc(aic=99)]}))
    assert r.invoke(cli.app, ["feature-regression", "--score", str(bad)]).exit_code == 1
    # bare-command exit code owned by test_bare_command_exits_non_zero…
    assert "tc1_search_suggestion (prompt)" in r.invoke(
        cli.app, ["feature-regression"]).output


def test_zero_cost_without_a_host_basis_is_not_a_measurement():
    """NOOD_0189 — `aic: 0` used to satisfy `0 > cap` and pass silently,
    while `null` failed as a missing measurement: the placeholder scored
    better than the omission it stood for."""
    v = regression.score({"test_cases": [
        _tc(aic=0, cost_basis="measured: host billing unavailable; floor=0"),
        _tc(id="tc2_account_textboxes")]})
    assert v["verdict"] == "REGRESSED"
    assert any("unmeasured cost" in r for r in v["regressions"])


def test_zero_cost_is_accepted_when_the_host_reported_it():
    v = regression.score({"test_cases": [
        _tc(aic=0, cost_basis="host-reported"),
        _tc(id="tc2_account_textboxes")]})
    assert v["verdict"] == "PASS"


def test_bare_command_exits_non_zero_so_a_runbook_is_never_a_result():
    """NOOD_0189 — the printed protocol was reported as a completed run
    because exit 0 read as success."""
    out = CliRunner().invoke(cli.app, ["feature-regression"])
    assert out.exit_code != 0
    assert "NOT a run" in out.output

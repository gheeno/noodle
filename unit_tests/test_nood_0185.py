"""NOOD_0185/0190 — feature-generation regression benchmark: score() verdicts
and reason lines, env-overridable budget, execute()'s measurement of the
author payload, and the CLI's run/exit codes. The live half (real browser,
real site) never runs here — execute() is faked at the core.author_test seam.
"""
import json

from typer.testing import CliRunner

from noodle import cli, regression


def _tc(**over):
    base = {"id": "tc1_search_suggestion", "elapsed_s": 20, "lines": 9,
            "corrections": 0, "green": True, "verified": True}
    return {**base, **over}


def _full(*over):
    """One healthy measurement per CANONICAL case, so a test about budgets
    never trips the "all cases measured" guard. Sized off PROMPTS, not a
    literal — NOOD_0191 added tc3 and every 2-case fixture regressed."""
    cases = [_tc(id=p["id"]) for p in regression.PROMPTS]
    for i, changes in enumerate(over):
        if changes:
            cases[i] = {**cases[i], **changes}
    return {"test_cases": cases}


def test_pass_within_budget():
    v = regression.score(_full())
    assert v["verdict"] == "PASS" and v["regressions"] == []
    assert v["average"]["lines"] == 9


def test_every_budget_breach_gets_a_named_reason():
    v = regression.score({"test_cases": [
        _tc(elapsed_s=9999, corrections=5),
        _tc(id="tc2_account_textboxes", green=False, verified=False),
        _tc(id="tc3_extra", verified=False)]})   # a pass held up by healing
    assert v["verdict"] == "REGRESSED"
    joined = " | ".join(v["regressions"])
    for token in ("slow", "inaccurate", "not green", "unverified"):
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
    # NOOD_0227 — sized off PROMPTS (tc4 joined), like every other case list
    v = regression.score({"test_cases": [
        _tc(elapsed_s=130, run_s=100),                       # 30s development
        *(_tc(id=p["id"], run_s=10)
          for p in regression.PROMPTS[1:])]})                # 10s development
    assert v["verdict"] == "PASS"
    assert v["test_cases"][0]["development_s"] == 30
    v2 = regression.score({"test_cases": [
        _tc(elapsed_s=150, run_s=5),
        *(_tc(id=p["id"]) for p in regression.PROMPTS[1:])]})
    assert any("slow development" in r for r in v2["regressions"])


def test_budget_env_overrides(monkeypatch):
    monkeypatch.setenv("NOODLE_REG_MAX_CORRECTIONS", "9")
    v = regression.score({"test_cases": [
        _tc(id=p["id"], corrections=5) for p in regression.PROMPTS]})
    assert v["verdict"] == "PASS"


# --- execute(): the benchmark runs itself (NOOD_0190) ------------------------

def _fake_author(monkeypatch, *, ready=True, healing=0, flaky=0, failed=0,
                 ready_on_retry=True):
    """Stand in for core.author_test — the one seam between the benchmark and
    a real browser. Records every call so the re-author path is observable."""
    from noodle.repl import core
    calls = []

    def fake(**kw):
        calls.append(kw)
        is_retry = kw.get("overwrite")
        ok_now = ready or (is_retry and ready_on_retry)
        return {
            "ok": ok_now and not failed,
            "author": {"ready": ok_now, "intent_verified": ok_now,
                       "feature": f"noodle_tests/web/wiki/features/f{len(calls)}.feature",
                       "compiled": {"feature": "line\n" * 8, "pom": "p\n" * 4}},
            "run": {"seconds": 7, "failed": failed, "verified": not failed,
                    "healing_events": [{"locator": "search"}] * healing,
                    "flaky": [{"scenario": "s"}] * flaky}}

    monkeypatch.setattr(core, "author_test", fake)
    monkeypatch.setattr(core, "run_and_report", lambda *a, **k: {
        "ok": True, "served": {"host": "127.0.0.1", "port": 9999,
                               "urls": ["http://127.0.0.1:9999/rca.html"]}})
    return calls


def test_execute_produces_a_scorer_valid_results_file(monkeypatch):
    _fake_author(monkeypatch)
    results = regression.execute("/tmp/ws")
    assert len(results["test_cases"]) == len(regression.PROMPTS)
    assert regression.score(results)["verdict"] == "PASS"
    # the size signal is the generated feature + POM line count — 8 + 4
    assert all(tc["lines"] == 12 for tc in results["test_cases"])
    # verdict.html leads the served URLs
    assert results["report_urls"][0].endswith("/verdict.html")


def test_corrections_come_from_the_engines_own_repair_signals(monkeypatch):
    """NOOD_0190 — a self-reported 0 used to sit beside a run log recording a
    real self-heal. Healing, a flaky retry and a re-author each cost one."""
    _fake_author(monkeypatch, healing=1)
    assert regression.execute("/tmp/ws")["test_cases"][0]["corrections"] == 1
    _fake_author(monkeypatch, flaky=1)
    assert regression.execute("/tmp/ws")["test_cases"][0]["corrections"] == 1
    calls = _fake_author(monkeypatch, ready=False)
    tc = regression.execute("/tmp/ws")["test_cases"][0]
    assert tc["corrections"] == 1 and tc["green"]
    assert calls[1]["overwrite"] is True      # re-authored exactly once


def test_a_case_that_never_becomes_ready_stays_red(monkeypatch):
    _fake_author(monkeypatch, ready=False, ready_on_retry=False)
    results = regression.execute("/tmp/ws")
    assert not results["test_cases"][0]["green"]
    assert regression.score(results)["verdict"] == "REGRESSED"


def test_a_failed_run_regresses(monkeypatch):
    _fake_author(monkeypatch, failed=1)
    v = regression.score(regression.execute("/tmp/ws"))
    assert v["verdict"] == "REGRESSED"
    assert any("not green" in r for r in v["regressions"])


# --- CLI --------------------------------------------------------------------

def _matched_install(monkeypatch, tmp_path):
    """Keep the folder tests about folders: a developer whose install lags the
    checkout hits the mismatch guard below, not these. clone_root is pinned to
    tmp_path so a unit test never writes into the real clone."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "version_report",
                        lambda: {"installed": "9.9.9", "source": "9.9.9",
                                 "mismatch": False})
    monkeypatch.setattr(install_check, "clone_root", lambda: tmp_path)


def test_init_refuses_a_stale_install(tmp_path, monkeypatch):
    """The workspace name carries the CHECKOUT's version+sha, so measuring a
    stale install would file the results under code that never ran."""
    monkeypatch.chdir(tmp_path)
    from noodle import install_check
    monkeypatch.setattr(install_check, "version_report",
                        lambda: {"installed": "1.0.0a1", "source": "1.0.0a2",
                                 "mismatch": True})
    out = CliRunner().invoke(cli.app, ["benchmark", "--init"])
    assert out.exit_code == 1
    assert "noodle update" in out.output and "1.0.0a2" in out.output
    assert not (tmp_path / "regression_runs").exists()   # nothing half-scaffolded


def test_init_makes_a_new_workspace_every_run_inside_the_clone(tmp_path, monkeypatch):
    """NOOD_0190 — the folder is anchored to the CLONE, not the cwd, so
    invoking from a subdirectory can't drop an un-gitignored regression_runs/
    wherever you happen to stand."""
    sub = tmp_path / "some" / "subdir"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    _matched_install(monkeypatch, tmp_path)
    r = CliRunner()
    assert r.invoke(cli.app, ["benchmark", "--init"]).exit_code == 0
    assert r.invoke(cli.app, ["benchmark", "--init"]).exit_code == 0
    runs = list((tmp_path / "regression_runs").iterdir())
    assert len(runs) == 2 and all((d / "noodle.yaml").is_file() for d in runs)
    assert not (sub / "regression_runs").exists()


def test_bare_command_runs_the_benchmark(tmp_path, monkeypatch):
    """NOOD_0190 — the whole point: one call generates, runs, serves and
    prints, instead of printing a protocol for an agent to improvise around."""
    monkeypatch.chdir(tmp_path)
    _matched_install(monkeypatch, tmp_path)
    _fake_author(monkeypatch)
    out = CliRunner().invoke(cli.app, ["benchmark", "--gate"])
    assert out.exit_code == 0
    assert "VERDICT: PASS" in out.output
    for p in regression.PROMPTS:
        assert p["id"] in out.output
    ws = next((tmp_path / "regression_runs").iterdir())
    assert json.loads((ws / "results.json").read_text(encoding="utf-8"))["test_cases"]
    assert json.loads((ws / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    assert "PASS" in (ws / "verdict.html").read_text(encoding="utf-8")


def test_a_regressed_run_exits_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _matched_install(monkeypatch, tmp_path)
    _fake_author(monkeypatch, failed=1)
    out = CliRunner().invoke(cli.app, ["benchmark", "--gate"])
    assert out.exit_code == 1 and "VERDICT: REGRESSED" in out.output


def test_score_writes_verdict_next_to_results(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(json.dumps(
        _full()), encoding="utf-8")
    out = CliRunner().invoke(cli.app, ["benchmark", "--score", str(results)])
    assert out.exit_code == 0
    assert json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    # the ACs (development time, accuracy, size) get a browser page too
    html = (tmp_path / "verdict.html").read_text(encoding="utf-8")
    assert "PASS" in html and "tc1_search_suggestion" in html
    assert "development" in html and "lines" in html


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

"""NOOD_0052 — report cmds take --workspace, safari/edge browser aliases.
NOOD_0093 — runs no longer auto-archive; archiving is on-demand only."""
import json

from noodle.hooks import _ENGINE_ALIASES, _VALID_BROWSERS


def test_run_has_no_auto_archive_helper():
    """NOOD_0093 — a `noodle run` overwrites its artifacts in place (Allure
    trend history carries the past forward); the auto-archive path is gone."""
    import noodle.cli as cli
    assert not hasattr(cli, "_archive_previous")


def test_report_default_resolves_inside_workspace(tmp_path):
    """NOOD_0086 — report cmds default to the workspace's last-run root
    (classic artifacts/ when no pointer exists); explicit paths win."""
    from noodle.reporting import paths as _paths
    assert _paths.last_run_root(str(tmp_path)) == tmp_path / "artifacts"


def test_parallel_exit_code_derived_from_merged_results(tmp_path, monkeypatch):
    """behavex has returned 0 with failed scenarios — the merged results are
    the ground truth for the build's exit code."""
    import subprocess as sp

    import noodle.cli as cli

    results = tmp_path / "artifacts" / "allure-results"
    results.mkdir(parents=True)

    def _fake_merge(_results):
        # simulate a worker having produced a failed scenario — written at
        # merge time because _run_parallel wipes stale *-result.json first
        (results / "a-result.json").write_text(json.dumps(
            {"status": "failed", "labels": [{"name": "tag", "value": "web"}]}))

    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0))
    monkeypatch.setattr("noodle.reporting.builder.generate", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_write_last_run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_merge_worker_results", _fake_merge)

    class _FakeBehavex:  # satisfies the import check
        pass
    import sys
    monkeypatch.setitem(sys.modules, "behavex", _FakeBehavex())

    rc = cli._run_parallel("tests", 2, None, {}, cwd=str(tmp_path))
    assert rc == 1   # behavex said 0; the failed result must win


def test_rule_plan_parses_compound_create_run_report():
    from noodle.repl.repl import _extract_plan_rules
    plan = _extract_plan_rules(
        "create a test for login at example.com, run it and show me the report")
    assert [s["action"] for s in plan] == ["create", "run", "open_report"]
    assert plan[0]["description"] == "login"
    assert plan[0]["url"] == "example.com"   # no trailing comma captured


def test_rule_plan_is_all_or_nothing():
    from noodle.repl.repl import _extract_plan_rules
    # unrecognized clause → no plan (falls through to single-intent grammars)
    assert _extract_plan_rules("create a test for login at x.com, do a barrel roll") == []
    # single clause → not a compound
    assert _extract_plan_rules("create a test for login at x.com") == []
    # compound description with 'and' must not half-execute
    assert _extract_plan_rules("create a test for search and checkout at x.com") == []
    # report/summary-only chatter isn't a plan
    assert _extract_plan_rules("show me the report and the summary") == []


def test_safari_and_edge_are_valid_and_alias_correct_engines():
    assert {"safari", "edge"} <= _VALID_BROWSERS
    assert _ENGINE_ALIASES["safari"] == ("webkit", None)
    assert _ENGINE_ALIASES["edge"] == ("chromium", "msedge")
    # plain engines pass through untouched
    assert _ENGINE_ALIASES.get("chromium", ("chromium", None)) == ("chromium", None)

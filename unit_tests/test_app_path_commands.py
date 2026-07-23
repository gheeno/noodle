"""NOOD_0086 — every noodle command works from inside an app package:
`cd noodle_tests/app1 && noodle run|summary|report|archive ...` re-roots on
the nearest noodle.yaml ancestor and targets/reads that app's report/."""
import json
import subprocess

import pytest
from typer.testing import CliRunner

from noodle.cli import _resolve_run_target, app
from noodle.reporting import paths as _paths

runner = CliRunner()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    runner.invoke(app, ["init", str(tmp_path)])
    return tmp_path


def test_resolve_run_target_reroots_from_app_dir(ws):
    app_dir = ws / "noodle_tests" / "sample_app"
    workspace, path = _resolve_run_target(str(app_dir), None)
    assert workspace == str(app_dir.resolve().parents[1])  # the init'd root
    assert path == str(app_dir.resolve().relative_to(ws.resolve()))


def test_resolve_run_target_untouched_otherwise(ws):
    # workspace root itself: not an app package
    assert _resolve_run_target(str(ws), None) == (str(ws), None)
    # explicit path given: never re-rooted
    app_dir = str(ws / "noodle_tests" / "sample_app")
    assert _resolve_run_target(app_dir, "x.feature") == (app_dir, "x.feature")
    # app dir with no noodle.yaml ancestor: left alone
    orphan = ws.parent / f"{ws.name}_orphan" / "app"
    (orphan / "features").mkdir(parents=True)
    assert _resolve_run_target(str(orphan), None) == (str(orphan), None)


def test_last_run_root_inside_app_dir(ws):
    app_dir = ws / "noodle_tests" / "sample_app"
    assert _paths.last_run_root(str(app_dir)) == app_dir / "report"
    # the workspace root still resolves the classic way
    assert _paths.last_run_root(str(ws)) == ws / "artifacts"


def test_summary_and_report_list_from_app_dir(ws):
    app_dir = ws / "noodle_tests" / "sample_app"
    results = app_dir / "report" / "allure-results"
    results.mkdir(parents=True)
    (results / "a-result.json").write_text(
        '{"name": "S", "status": "passed", "start": 0, "stop": 1}')
    r = runner.invoke(app, ["summary", "--workspace", str(app_dir), "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["passed"] == 1
    r = runner.invoke(app, ["report", "list", "--workspace", str(app_dir), "--json"])
    assert r.exit_code == 0


def test_archive_from_app_dir_zips_app_report(ws):
    app_dir = ws / "noodle_tests" / "sample_app"
    (app_dir / "report" / "allure-results").mkdir(parents=True)
    r = runner.invoke(app, ["archive", "--workspace", str(app_dir)])
    assert r.exit_code == 0
    assert list((app_dir / "archives").glob("artifacts_*.zip"))


def test_mcp_run_test_from_app_dir(ws, monkeypatch):
    from noodle.repl import core
    seen = {}

    def fake_engine(*args, workspace="."):
        seen["args"], seen["workspace"] = args, workspace
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(core, "_engine", fake_engine)
    app_dir = ws / "noodle_tests" / "sample_app"
    result = core.run_test(workspace=str(app_dir), headless=True)
    assert result["ok"]
    # no target passed to the CLI — it re-roots and targets the app itself
    assert seen["args"] == ("run", "--headless")
    assert seen["workspace"] == str(app_dir)

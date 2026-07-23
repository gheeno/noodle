"""NOOD_0147 — session diagnostics: agent-written failure self-reports.

Proves the engine-side guarantees the AGENTS.md prompt text can't enforce
by itself: deterministic write with auto-appended engine facts, secret
scrubbing, the dedupe-per-session update, the NOODLE_DIAG_MAX rotation cap,
the bundle command, and the scaffold wiring (gitignore, AGENTS.md trigger
table, .env knobs, MCP tool + instructions).
"""
import zipfile
from pathlib import Path

import pytest

from noodle import diagnostics


@pytest.fixture()
def ws(tmp_path):
    return str(tmp_path)


def _write(ws, **kw):
    args = dict(app="demo", triggers=["hard-fail"], summary="stayed red")
    args.update(kw)
    return diagnostics.write_diagnostic(ws, **args)


def test_write_creates_md_with_front_matter(ws, tmp_path):
    r = _write(ws, triggers=["hard-fail", "slow-dev"],
               timeline="probe -> author -> 10 red laps",
               suspected_cause="iframe POM gap", fixes_tried="frame step",
               duration_min=27.4, attempts=10, agent="codex 5.3",
               agent_cost="23 AIC")
    assert (tmp_path / "diagnostics").is_dir()
    path = Path(r["path"])
    fm = diagnostics._front_matter(path)
    assert fm["app"] == "demo"
    assert fm["triggers"] == ["hard-fail", "slow-dev"]
    assert fm["duration_min"] == 27.4 and fm["attempts"] == 10
    assert fm["agent"] == "codex 5.3" and fm["agent_cost"] == "23 AIC"
    text = path.read_text()
    for section in ("What went wrong", "Timeline", "Suspected cause", "Fixes tried"):
        assert section in text
    assert r["updated"] is False and r["count"] == 1


def test_unknown_or_empty_trigger_rejected(ws):
    with pytest.raises(ValueError, match="unknown"):
        _write(ws, triggers=["hard-fail", "made-up"])
    with pytest.raises(ValueError):
        _write(ws, triggers=[])
    with pytest.raises(ValueError, match="summary"):
        _write(ws, summary="   ")


def test_same_session_updates_not_duplicates(ws, tmp_path):
    r1 = _write(ws, session="sess1", summary="first take")
    r2 = _write(ws, session="sess1", summary="second take")
    assert r2["updated"] is True
    assert r1["path"] == r2["path"]
    files = list((tmp_path / "diagnostics").glob("*.md"))
    assert len(files) == 1
    assert "second take" in files[0].read_text()


def test_same_app_in_window_updates_without_session(ws, tmp_path):
    _write(ws, summary="first take")
    r2 = _write(ws, summary="second take")
    assert r2["updated"] is True
    assert len(list((tmp_path / "diagnostics").glob("*.md"))) == 1


def test_different_apps_get_separate_files(ws, tmp_path):
    _write(ws, app="app-one")
    r2 = _write(ws, app="app-two")
    assert r2["updated"] is False
    assert len(list((tmp_path / "diagnostics").glob("*.md"))) == 2


def test_cap_rotates_oldest(ws, tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setenv("NOODLE_DIAG_MAX", "3")
    for i in range(5):
        r = _write(ws, app=f"app{i}")
        # distinct mtimes so oldest-first ordering is deterministic
        os.utime(r["path"], (time.time() - 100 + i, time.time() - 100 + i))
    files = list((tmp_path / "diagnostics").glob("*.md"))
    assert len(files) == 3
    assert r["count"] == 3


def test_secret_values_scrubbed(ws):
    from noodle import log
    log.register_secret("Hunter2Pass")
    try:
        r = _write(ws, summary="login with Hunter2Pass failed",
                   fixes_tried="retyped Hunter2Pass by hand")
        text = Path(r["path"]).read_text()
        assert "Hunter2Pass" not in text
        assert "***" in text
    finally:
        log._secret_values.discard("Hunter2Pass")


def test_narrative_fields_truncated(ws):
    r = _write(ws, timeline="x" * 20_000)
    text = Path(r["path"]).read_text()
    assert "truncated by noodle" in text
    assert len(text) < 12_000


def test_list_and_bundle(ws, tmp_path):
    _write(ws, app="alpha")
    _write(ws, app="beta")
    entries = diagnostics.list_diagnostics(ws)
    assert {e["app"] for e in entries} == {"alpha", "beta"}
    b = diagnostics.bundle(ws)
    assert b["count"] == 2
    with zipfile.ZipFile(b["path"]) as z:
        assert len(z.namelist()) == 2
    # a second bundle replaces the first — one current zip, not a pile
    b2 = diagnostics.bundle(ws)
    zips = list((tmp_path / "diagnostics").glob("*.zip"))
    assert [str(z) for z in zips] == [b2["path"]]


def test_bundle_empty_is_error(ws):
    assert "error" in diagnostics.bundle(ws)


def test_write_without_any_run_still_lands(ws, monkeypatch):
    # a session can die before its first run — the diagnostic must not
    # require allure-results to exist
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    r = _write(ws)
    assert r["count"] == 1


def test_engine_facts_appended_when_run_exists(ws, tmp_path, monkeypatch):
    import json
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    results = tmp_path / "artifacts" / "allure-results"
    results.mkdir(parents=True)
    (results / "demo-result.json").write_text(json.dumps({
        "name": "Valid login", "status": "failed", "start": 0, "stop": 1000,
        "statusDetails": {"message": "element not found: login"},
        "labels": [{"name": "feature", "value": "Login"}]}))
    r = _write(ws)
    fm = diagnostics._front_matter(Path(r["path"]))
    assert fm.get("last_run", {}).get("failed") == 1
    assert fm["last_run_failures"][0]["scenario"] == "Valid login"


# --- automatic trigger detection (track_run) --------------------------------

def test_first_red_run_fires_first_attempt(ws):
    assert "first-attempt-fail" in diagnostics.track_run(ws, "t.feature", failed=True)
    # second red lap is no longer a "first attempt"
    assert "first-attempt-fail" not in diagnostics.track_run(ws, "t.feature", failed=True)


def test_hard_fail_fires_at_dev_fix_cap(ws, monkeypatch):
    monkeypatch.setenv("NOODLE_DEV_FIX_ATTEMPTS", "3")
    fired = []
    for _ in range(3):
        fired = diagnostics.track_run(ws, "t.feature", failed=True)
    assert "hard-fail" in fired


def test_green_run_resets_streak(ws, tmp_path):
    import json
    diagnostics.track_run(ws, "t.feature", failed=True)
    diagnostics.track_run(ws, "t.feature", failed=False)
    state = json.loads((tmp_path / ".noodle" / "diag_state.json").read_text())
    assert "t.feature" not in state
    # next red run counts as a fresh first attempt
    assert "first-attempt-fail" in diagnostics.track_run(ws, "t.feature", failed=True)


def test_slow_dev_fires_past_threshold_even_on_green(ws, tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("NOODLE_DIAG_SLOW_MIN", "20")
    diagnostics.track_run(ws, "t.feature", failed=True)
    f = tmp_path / ".noodle" / "diag_state.json"
    state = json.loads(f.read_text())
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat(timespec="seconds")
    state["t.feature"]["first_run_at"] = old
    f.write_text(json.dumps(state))
    assert "slow-dev" in diagnostics.track_run(ws, "t.feature", failed=False)


def test_stale_state_restarts_as_fresh_session(ws, tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    diagnostics.track_run(ws, "t.feature", failed=True)
    f = tmp_path / ".noodle" / "diag_state.json"
    state = json.loads(f.read_text())
    old = (datetime.now(timezone.utc) - timedelta(minutes=300)).isoformat(timespec="seconds")
    state["t.feature"].update(first_run_at=old, last_run_at=old)
    f.write_text(json.dumps(state))
    # a run 5 hours later is a NEW dev session: first-attempt fires again,
    # and the 5-hour gap must not read as slow-dev
    fired = diagnostics.track_run(ws, "t.feature", failed=True)
    assert fired == ["first-attempt-fail"]


def test_track_run_never_raises(tmp_path):
    # unwritable state file must not break the run
    bad = tmp_path / ".noodle"
    bad.write_text("a file where a dir should be")
    assert diagnostics.track_run(str(tmp_path), "t", failed=True) == []


def test_run_result_carries_diagnostic_due(ws, monkeypatch):
    import subprocess

    from noodle.repl import core

    def fake_engine(*args, workspace=None):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
    monkeypatch.setattr(core, "_engine", fake_engine)
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    result = core.run_test(tag="smoke", workspace=ws)
    assert result["ok"] is False
    assert "first-attempt-fail" in result["diagnostic_due"]["triggers"]
    assert "log_diagnostic" in result["diagnostic_due"]["action"]


# --- scaffold + surface wiring ---------------------------------------------

def test_gitignore_and_templates_carry_diagnostics():
    from noodle import cli
    from noodle.mcp import server
    assert "diagnostics/" in cli._GITIGNORE
    assert "Session diagnostics" in cli._AGENTS_MD
    assert "log_diagnostic" in cli._AGENTS_MD
    assert "NOODLE_DIAG_MAX" in cli._ENV_STUB_BASE
    assert "diagnostics/" in cli._WORKSPACE_README
    # the full trigger vocabulary lives in the doc + the MCP tool docstring,
    # NOT in the byte-capped always-on surfaces
    doc = (Path(__file__).resolve().parent.parent / "docs" / "session-diagnostics.md").read_text()
    for trigger in diagnostics.TRIGGERS:
        assert trigger in doc
        assert trigger in server.log_diagnostic.__doc__


def test_init_scaffolds_gitignored_diagnostics(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    result = CliRunner().invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert "diagnostics/" in (tmp_path / ".gitignore").read_text()
    assert "Session diagnostics" in (tmp_path / "AGENTS.md").read_text()


def test_cli_log_and_list(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    runner = CliRunner()
    r = runner.invoke(app, ["diagnostic", "log", "demo", "-t", "manual",
                            "-s", "user asked for a diagnostic",
                            "-w", str(tmp_path)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["diagnostic", "list", "-w", str(tmp_path)])
    assert "app=demo" in r.output
    bad = runner.invoke(app, ["diagnostic", "log", "demo", "-t", "nope",
                              "-s", "x", "-w", str(tmp_path)])
    assert bad.exit_code == 1


def test_guide_command_works_without_mcp():
    # corp/MCP-blocked parity: the contract must be printable from the CLI
    from typer.testing import CliRunner

    from noodle.cli import app
    r = CliRunner().invoke(app, ["diagnostic", "guide"])
    assert r.exit_code == 0, r.output
    for trigger in diagnostics.TRIGGERS:
        assert trigger in r.output


def test_guide_doc_bundled_into_wheel():
    # NOOD_0145 pattern: installed distributions must carry the doc so
    # `noodle diagnostic guide` doesn't depend on a source checkout
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert '"docs/session-diagnostics.md" = "noodle/_docs/session-diagnostics.md"' in pyproject


def test_no_workspace_relative_doc_path_in_hints():
    # NOOD_0145 — hints must never name docs/session-diagnostics.md as a
    # path: agents in an external workspace resolve it as <workspace>/docs/…
    from noodle import cli
    hint = diagnostics.due_hint(["hard-fail"])["action"]
    assert "docs/session-diagnostics" not in hint
    assert "noodle diagnostic guide" in hint
    assert "docs/session-diagnostics" not in cli._AGENTS_MD


def test_mcp_tool_registered_and_writes(tmp_path):
    server = pytest.importorskip("noodle.mcp.server")
    out = server.log_diagnostic(app="demo", triggers=["over-budget"],
                                summary="burned 25 AIC", agent_cost="25 AIC",
                                workspace=str(tmp_path))
    assert (tmp_path / "diagnostics").is_dir()
    assert out["count"] == 1
    # the byte-capped connect-time instructions deliberately do NOT carry the
    # rule — the diagnostic_due run-result nudge and the tool docstring do
    assert "log_diagnostic" not in server._INSTRUCTIONS
    # (byte ceiling moved to noodle/instruction_budget.py — NOOD_0159)

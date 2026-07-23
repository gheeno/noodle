"""NOOD_0138 — context-aware `noodle doctor`.

Pins:
  * context resolution: engine / workspace / install-only from any start dir,
    nearest ancestor wins, engine beats workspace in a same-dir collision,
    forced --scope, exit 2 on bad path/scope
  * launcher provenance: identical duplicates are info (NOT a warning — a
    project .venv + uv tool shim running the same editable build is the normal
    engine-dev setup), conflicting builds fail, unknown provenance warns,
    a single launcher is never probed
  * engine profile never compares workspace templates or recommends init
  * workspace profile: config/layout/template/MCP checks with init remediation
  * CLI contract: --json shape, stable check IDs, exit codes
"""
import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle import doctor, install_check
from noodle.cli import app

REPO = Path(__file__).resolve().parents[1]
runner = CliRunner()


def make_engine(d: Path, name: str = "noodle") -> Path:
    (d / "noodle").mkdir(parents=True)
    (d / "noodle" / "__init__.py").write_text("")
    (d / "noodle" / "cli.py").write_text("")
    (d / "unit_tests").mkdir()
    (d / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0"\n')
    return d


def make_workspace(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "noodle.yaml").write_text("tests_dir: noodle_tests\n")
    return d


@pytest.fixture
def single_launcher(monkeypatch):
    """A healthy install baseline so tests exercise ONE profile at a time."""
    monkeypatch.setattr(install_check, "shims_on_path", lambda: ["one"])
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    # NOOD_0157 — a healthy install has no version drift either. make_engine's
    # fixture pyproject declares version "0", which mismatches whatever
    # importlib.metadata reports for a REAL install, so install.version-sync
    # warned and doctor exited 1. Unstubbed, these tests only passed where
    # noodle wasn't installed at all (version_report suppresses the mismatch
    # when installed == "unknown") — green on a bare checkout, red in CI.
    monkeypatch.setattr(install_check, "version_report",
                        lambda: {"installed": "0", "source": "0", "mismatch": False})


# --- context resolution ------------------------------------------------------

def test_engine_root_detected_from_subdir(tmp_path):
    e = make_engine(tmp_path / "eng")
    ctx = doctor.resolve_context(e / "noodle")
    assert ctx.kind == "engine" and ctx.root == e.resolve()


def test_workspace_nearest_ancestor_wins_from_nested_app(tmp_path):
    ws = make_workspace(tmp_path / "ws")
    appdir = ws / "noodle_tests" / "app1" / "features"
    appdir.mkdir(parents=True)
    ctx = doctor.resolve_context(appdir)
    assert ctx.kind == "workspace" and ctx.root == ws.resolve()


def test_workspace_inside_engine_nearest_wins(tmp_path):
    e = make_engine(tmp_path / "eng")
    ws = make_workspace(e / "tmp_ws")
    assert doctor.resolve_context(ws).kind == "workspace"


def test_same_dir_collision_engine_wins(tmp_path):
    e = make_engine(tmp_path / "eng")
    (e / "noodle.yaml").write_text("tests_dir: sample_feature_tests\n")
    assert doctor.resolve_context(e).kind == "engine"


def test_lookalike_project_name_is_not_engine(tmp_path):
    e = make_engine(tmp_path / "eng", name="noodles")
    assert doctor.resolve_context(e).kind == "install"


def test_unrelated_dir_is_install_only(tmp_path):
    ctx = doctor.resolve_context(tmp_path)
    assert ctx.kind == "install" and ctx.root is None


def test_file_path_starts_from_its_parent(tmp_path):
    ws = make_workspace(tmp_path)
    f = ws / "notes.txt"
    f.write_text("x")
    assert doctor.resolve_context(f).kind == "workspace"


def test_missing_path_is_doctor_error(tmp_path):
    with pytest.raises(doctor.DoctorError):
        doctor.resolve_context(tmp_path / "nope")


def test_forced_scope_without_marker_errors(tmp_path):
    with pytest.raises(doctor.DoctorError):
        doctor.resolve_context(tmp_path, scope="engine")
    with pytest.raises(doctor.DoctorError):
        doctor.resolve_context(tmp_path, scope="workspace")


def test_forced_workspace_at_engine_root(tmp_path):
    e = make_engine(tmp_path / "eng")
    (e / "noodle.yaml").write_text("tests_dir: t\n")
    assert doctor.resolve_context(e, scope="workspace").kind == "workspace"


def test_scope_install_skips_detection(tmp_path):
    ws = make_workspace(tmp_path)
    assert doctor.resolve_context(ws, scope="install").kind == "install"


def test_invalid_scope_errors(tmp_path):
    with pytest.raises(doctor.DoctorError):
        doctor.resolve_context(tmp_path, scope="bogus")


# --- build-line parsing (POSIX + Windows path forms) ---------------------------

def test_parse_build_line_posix_editable_with_sha():
    p = install_check.parse_build_line(
        "noodle 0.1.0 (editable) /Users/x/noodle/noodle @ 1511295\n")
    assert p == {"version": "0.1.0", "kind": "editable",
                 "root": "/Users/x/noodle/noodle", "sha": "1511295"}


def test_parse_build_line_windows_noneditable_no_sha():
    p = install_check.parse_build_line(
        r"noodle 0.2.0 (NON-EDITABLE COPY) C:\Py311\Lib\site-packages\noodle")
    assert p["kind"] == "NON-EDITABLE COPY"
    assert p["root"] == r"C:\Py311\Lib\site-packages\noodle" and p["sha"] is None


def test_parse_build_line_garbage_is_none():
    assert install_check.parse_build_line("Usage: noodle [OPTIONS] COMMAND") is None


def test_probe_launcher_unrecognized_output_is_bounded_error():
    r = install_check.probe_launcher(sys.executable)  # prints "Python 3.x", not a build line
    assert r["error"].startswith("unrecognized") and len(r["error"]) < 300


def test_probe_launcher_nonexistent_is_error():
    r = install_check.probe_launcher(str(Path("nonexistent") / "noodle"))
    assert "error" in r


def test_probe_launcher_timeout_is_error(monkeypatch):
    import subprocess
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 10))
    monkeypatch.setattr(subprocess, "run", boom)
    assert "timed out" in install_check.probe_launcher("x")["error"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/chmod semantics")
def test_shims_dedupe_symlink_and_duplicate_path_entries(tmp_path, monkeypatch):
    real = tmp_path / "bin1" / "noodle"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    d2 = tmp_path / "bin2"
    d2.mkdir()
    (d2 / "noodle").symlink_to(real)
    monkeypatch.setattr(os, "get_exec_path",
                        lambda: [str(real.parent), str(real.parent), str(d2)])
    assert install_check.shims_on_path() == [str(real)]


# --- launcher provenance classification ----------------------------------------

ACTIVE = {"version": "0.1.0", "kind": "editable", "root": "/repo/noodle", "sha": "abc1234"}


def _classify(monkeypatch, probes: dict) -> doctor.Check:
    monkeypatch.setattr(install_check, "dist_version", lambda: ACTIVE["version"])
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    monkeypatch.setattr(install_check, "package_dir", lambda: Path(ACTIVE["root"]))
    monkeypatch.setattr(install_check, "git_sha", lambda: ACTIVE["sha"])
    monkeypatch.setattr(install_check, "shims_on_path", lambda: list(probes))
    monkeypatch.setattr(install_check, "probe_launcher", lambda p, timeout=10.0: probes[p])
    return doctor._launcher_check()


def test_identical_duplicate_launchers_are_info(monkeypatch):
    c = _classify(monkeypatch, {"a": dict(ACTIVE), "b": dict(ACTIVE)})
    assert c.status == "info" and "same build" in c.summary
    assert "Never delete the engine .venv" in c.remediation


def test_different_version_is_fail(monkeypatch):
    c = _classify(monkeypatch, {"a": dict(ACTIVE), "b": {**ACTIVE, "version": "0.2.0"}})
    assert c.status == "fail" and "first on PATH wins" in c.summary


def test_different_source_root_is_fail(monkeypatch):
    c = _classify(monkeypatch, {"a": dict(ACTIVE), "b": {**ACTIVE, "root": "/elsewhere/noodle"}})
    assert c.status == "fail"


def test_noneditable_shadow_copy_is_fail_with_remediation(monkeypatch):
    c = _classify(monkeypatch,
                  {"a": dict(ACTIVE),
                   "b": {**ACTIVE, "kind": "NON-EDITABLE COPY", "sha": None}})
    assert c.status == "fail"
    assert "uv tool" in c.remediation or "pip install -e" in c.remediation


def test_unknown_provenance_is_warn(monkeypatch):
    c = _classify(monkeypatch, {"a": dict(ACTIVE), "b": {"error": "timed out after 10.0s"}})
    assert c.status == "warn" and "timed out" in c.detail


def test_single_launcher_is_pass_and_never_probed(monkeypatch):
    monkeypatch.setattr(install_check, "shims_on_path", lambda: ["only"])
    monkeypatch.setattr(install_check, "probe_launcher",
                        lambda *a, **k: pytest.fail("probed a single launcher"))
    assert doctor._launcher_check().status == "pass"


# --- engine profile -------------------------------------------------------------

def test_engine_profile_never_calls_template_files(tmp_path, monkeypatch, single_launcher):
    import noodle.cli as cli
    monkeypatch.setattr(cli, "_template_files",
                        lambda root: pytest.fail("engine profile compared workspace templates"))
    e = make_engine(tmp_path / "eng")
    monkeypatch.setattr(install_check, "package_dir", lambda: e / "noodle")
    r = runner.invoke(app, ["doctor", str(e)])
    assert "Context: engine" in r.output
    assert "init --force" not in r.output
    assert r.exit_code == 0, r.output


def test_engine_profile_warns_on_workspace_artifacts_but_not_noodle_yaml(
        tmp_path, monkeypatch, single_launcher):
    e = make_engine(tmp_path / "eng")
    (e / "noodle.yaml").write_text("tests_dir: sample_feature_tests\n")  # deliberate, tracked
    (e / "AGENTS.md").write_text("generated")
    (e / "noodle_tests").mkdir()
    monkeypatch.setattr(install_check, "package_dir", lambda: e / "noodle")
    r = runner.invoke(app, ["doctor", str(e)])
    assert r.exit_code == 1
    assert "engine.workspace-artifacts" in r.output
    assert "Review: AGENTS.md, noodle_tests/" in r.output
    assert "doctor never deletes" in r.output


def test_engine_install_link_mismatch_fails(tmp_path, monkeypatch, single_launcher):
    e = make_engine(tmp_path / "eng")
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: tmp_path / "other-venv" / "site-packages" / "noodle")
    r = runner.invoke(app, ["doctor", str(e)])
    assert r.exit_code == 1
    assert "engine.install-link" in r.output and "not this checkout" in r.output


def test_real_engine_checkout_diagnoses_engine(single_launcher):
    r = runner.invoke(app, ["doctor", str(REPO)])
    assert "Context: engine" in r.output
    assert "workspace.templates" not in r.output  # engine docs are not templates
    assert "active noodle package resolves to this checkout" in r.output
    assert r.exit_code == 0, r.output


# --- workspace profile ------------------------------------------------------------

def _init_ws(tmp_path: Path) -> Path:
    runner.invoke(app, ["init", str(tmp_path)])
    return tmp_path


def test_workspace_all_green(tmp_path, single_launcher):
    _init_ws(tmp_path)
    for rel in (".mcp.json", ".vscode/mcp.json", ".copilot/mcp-config.json"):
        (tmp_path / rel).unlink(missing_ok=True)  # command resolution varies by env
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "Context: workspace" in r.output
    for check_id in ("workspace.config", "workspace.layout", "workspace.templates"):
        assert f"PASS  [{check_id}]" in r.output


def test_workspace_broken_yaml_fails_without_traceback(tmp_path, single_launcher):
    make_workspace(tmp_path)
    (tmp_path / "noodle.yaml").write_text("tests_dir: [unclosed")
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 1
    assert "FAIL  [workspace.config]" in r.output and "Traceback" not in r.output


def test_workspace_tests_dir_escape_fails(tmp_path, single_launcher):
    make_workspace(tmp_path)
    (tmp_path / "noodle.yaml").write_text("tests_dir: ../outside\n")
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 1
    assert "escapes the workspace" in r.output


def test_workspace_missing_tests_dir_warns_with_init_fix(tmp_path, single_launcher):
    make_workspace(tmp_path)
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 1
    assert "workspace.layout" in r.output and "noodle init" in r.output


def test_workspace_stale_template_warns_with_force_fix(tmp_path, single_launcher):
    _init_ws(tmp_path)
    (tmp_path / "AGENTS.md").write_text("drifted")
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 1
    assert "workspace.templates" in r.output and "--force" in r.output


def test_mcp_check_stale_command_warns(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"noodle": {"command": "/nonexistent/noodle-mcp", "args": []}}}))
    c = doctor._mcp_check(tmp_path)
    assert c.status == "warn" and "noodle init mcp" in c.remediation


def test_mcp_check_absent_is_info(tmp_path):
    assert doctor._mcp_check(tmp_path).status == "info"


def test_mcp_check_valid_command_passes(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"noodle": {"command": sys.executable, "args": []}}}))
    assert doctor._mcp_check(tmp_path).status == "pass"


# --- CLI contract -------------------------------------------------------------------

def test_json_output_contract(tmp_path, single_launcher):
    _init_ws(tmp_path)
    r = runner.invoke(app, ["doctor", str(tmp_path), "--json"])
    data = json.loads(r.output)
    assert data["context"]["kind"] == "workspace"
    assert data["context"]["root"] == str(tmp_path.resolve())
    assert data["ok"] == (r.exit_code == 0)
    ids = {c["id"] for c in data["checks"]}
    assert {"install.active-build", "install.editable", "install.launchers",
            "workspace.config", "workspace.layout", "workspace.templates",
            "workspace.mcp"} <= ids
    assert all(c["status"] in ("pass", "info", "warn", "fail") for c in data["checks"])
    assert "\x1b" not in r.output  # no ANSI in JSON


def test_exit_2_on_missing_path(tmp_path):
    r = runner.invoke(app, ["doctor", str(tmp_path / "nope")])
    assert r.exit_code == 2


def test_exit_2_on_forced_scope_mismatch(tmp_path):
    r = runner.invoke(app, ["doctor", str(tmp_path), "--scope", "engine"])
    assert r.exit_code == 2


def test_scope_install_runs_install_checks_only(tmp_path, single_launcher):
    _init_ws(tmp_path)
    r = runner.invoke(app, ["doctor", str(tmp_path), "--scope", "install"])
    assert r.exit_code == 0, r.output
    assert "Context: install only" in r.output
    assert "workspace.config" not in r.output


def test_internal_check_error_becomes_fail_record(tmp_path, monkeypatch, single_launcher):
    make_workspace(tmp_path)
    monkeypatch.setattr(doctor, "workspace_checks",
                        lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx, checks = doctor.diagnose(str(tmp_path))
    crash = [c for c in checks if c.id == "workspace.internal-error"]
    assert crash and crash[0].status == "fail" and "boom" in crash[0].summary

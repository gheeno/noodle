"""NOOD_0055 — MCP server + agent-core fixes from the production-readiness review.

Covers the four confirmed bugs and the hardening around them:
  1. validate_feature/write_feature honour the workspace's docs/agent_patterns.yaml
     (previously validated as unmatched what the runtime resolved fine).
  2. `noodle report generate` exits non-zero when no report was built, so
     core.build_report / MCP run_and_report stop reporting phantom success.
  3. noodle-mcp loads the workspace .env at startup (NOODLE_MODEL from
     `noodle init --llm` was invisible to every MCP tool).
  4. agent state persists workspace-relative paths from every writer
     (create_test and the REPL), matching write_feature/resolve_target.
  Plus: _engine's NOODLE_ENGINE_TIMEOUT guard and the REPL's trailing-flag argv.

Complements test_nood_0045.py (tool surface, auth gate, transports) — no
browser, no LLM, no network.
"""
import json
import subprocess
from pathlib import Path

import pytest

from noodle.repl import core

FEATURE_CUSTOM_STEP = (
    "Feature: F\n"
    "  Scenario: S\n"
    "    When User frobnicates the widget\n"
)

AGENT_PATTERNS = (
    "- phrase: 'frobnicates the widget'\n"
    "  action_type: click\n"
    "  params: []\n"
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
    (tmp_path / "tests").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_patterns_dir():
    """validate_feature repoints the process-global agent-patterns dir —
    put it back so test order can't leak a workspace into other tests."""
    from noodle.resolver import patterns as _patterns
    yield
    _patterns.set_agent_patterns_dir(None)


# --- bug 1: workspace agent patterns in validation -------------------------------

def test_validate_feature_uses_workspace_agent_patterns(ws):
    (ws / "docs").mkdir()
    (ws / "docs" / "agent_patterns.yaml").write_text(AGENT_PATTERNS)
    result = core.validate_feature(FEATURE_CUSTOM_STEP, workspace=str(ws))
    assert result["error"] is None
    assert result["unmatched"] == []


def test_validate_feature_without_workspace_patterns_flags_step(ws):
    result = core.validate_feature(FEATURE_CUSTOM_STEP, workspace=str(ws))
    assert result["unmatched"] == ["When User frobnicates the widget"]


def test_write_feature_accepts_workspace_pattern_steps(ws):
    (ws / "docs").mkdir()
    (ws / "docs" / "agent_patterns.yaml").write_text(AGENT_PATTERNS)
    r = core.write_feature("tests/frob.feature", FEATURE_CUSTOM_STEP,
                           workspace=str(ws))
    assert r["ok"] and r["unmatched"] == []
    assert (ws / "tests" / "frob.feature").is_file()


def test_mcp_validate_tool_passes_workspace(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    (ws / "docs").mkdir()
    (ws / "docs" / "agent_patterns.yaml").write_text(AGENT_PATTERNS)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    assert server.validate_feature(FEATURE_CUSTOM_STEP)["unmatched"] == []


def test_cli_validate_resolve_uses_workspace_patterns(ws):
    from typer.testing import CliRunner

    from noodle.cli import app
    (ws / "docs").mkdir()
    (ws / "docs" / "agent_patterns.yaml").write_text(AGENT_PATTERNS)
    (ws / "tests" / "frob.feature").write_text(FEATURE_CUSTOM_STEP)
    r = CliRunner().invoke(app, ["validate", "tests", "--workspace", str(ws),
                                 "--resolve", "--json"])
    assert r.exit_code == 0
    steps = json.loads(r.stdout)[0]["steps"]
    assert all(s["matched"] for s in steps)


# --- bug 2: report generate signals failure --------------------------------------

def test_builder_generate_returns_false_without_allure(ws, monkeypatch):
    from noodle.reporting import builder
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    assert builder.generate(str(ws / "r"), str(ws / "out")) is False


def test_cli_report_generate_exits_nonzero_without_allure(ws, monkeypatch):
    from typer.testing import CliRunner

    import noodle.reporting.builder as builder
    from noodle.cli import app
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    r = CliRunner().invoke(app, ["report", "generate", "--workspace", str(ws)])
    assert r.exit_code == 1


def test_run_and_report_reports_no_phantom_report(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    (ws / "tests" / "any.feature").write_text("Feature: F\n")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    monkeypatch.setattr(core, "run_test",
                        lambda *a, **k: {"ok": True, "passed": 1, "failed": 0})
    # NOOD_0131 — no unconditional rebuild: with no report on disk (allure
    # missing / never ran), the payload must say None, not a phantom path.
    r = server.run_and_report("any")
    assert r["report"] is None


# --- bug 3: MCP server loads the workspace .env -----------------------------------

def test_load_workspace_env_reads_noodle_model(ws, monkeypatch):
    import os
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    (ws / ".env").write_text("NOODLE_MODEL=ollama/testmodel\n")
    try:
        server._load_workspace_env(str(ws))
        assert os.environ.get("NOODLE_MODEL") == "ollama/testmodel"
    finally:
        # plain pop, not monkeypatch.delenv: delenv here would record the
        # loaded value and teardown would *restore* it — leaking NOODLE_MODEL
        # into later tests (which then try to reach a live Ollama).
        os.environ.pop("NOODLE_MODEL", None)


def test_load_workspace_env_never_overrides_host_env(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setenv("NOODLE_MODEL", "host/wins")
    (ws / ".env").write_text("NOODLE_MODEL=ollama/testmodel\n")
    server._load_workspace_env(str(ws))
    import os
    assert os.environ["NOODLE_MODEL"] == "host/wins"


def test_main_stdio_loads_workspace_env(ws, monkeypatch):
    import os
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    (ws / ".env").write_text("NOODLE_MODEL=ollama/fromenvfile\n")
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    try:
        server.main(["--workspace", str(ws)])
        assert os.environ.get("NOODLE_MODEL") == "ollama/fromenvfile"
    finally:
        server._WORKSPACE = "."
        os.environ.pop("NOODLE_MODEL", None)  # see note above — not delenv


# --- bug 4: workspace-relative agent state ----------------------------------------

def _fake_generate(description, url, cfg, workspace=".", overwrite=False):
    app_dir = Path(workspace) / cfg["tests_dir"] / "web" / "example"
    feat = app_dir / "features" / "login.feature"
    pom = app_dir / "resources" / "pageobjects" / "login_pom.yaml"
    feat.parent.mkdir(parents=True, exist_ok=True)
    pom.parent.mkdir(parents=True, exist_ok=True)
    feat.write_text("Feature: F\n  Scenario: S\n    Given User is on 'https://x'\n")
    pom.write_text("# pom\n")
    return feat, pom


def test_create_test_persists_workspace_relative_state(ws, monkeypatch):
    from noodle.repl import generate
    monkeypatch.setattr(generate, "generate", _fake_generate)
    r = core.create_test("login test", "example.com", workspace=str(ws))
    assert r["ok"]
    state = core.load_state(str(ws))
    assert state["last_feature"] == "tests/web/example/features/login.feature"
    assert not Path(state["last_feature"]).is_absolute()
    # resolve_target must find it again by rejoining the workspace
    assert core.resolve_target(None, str(ws)) == {"feature": state["last_feature"]}


def test_repl_remember_created_is_workspace_relative(ws):
    from noodle.repl import repl
    feat = ws / "tests" / "web" / "app" / "features" / "x.feature"
    pom = ws / "tests" / "web" / "app" / "resources" / "pageobjects" / "x_pom.yaml"
    state = {}
    repl._remember_created(state, feat, pom, str(ws))
    assert state["last_feature"] == "tests/web/app/features/x.feature"
    assert state["last_pom"] == "tests/web/app/resources/pageobjects/x_pom.yaml"


# --- hardening: engine timeout + REPL argv ----------------------------------------

def test_engine_timeout_returns_rc_124(monkeypatch):
    monkeypatch.setenv("NOODLE_ENGINE_TIMEOUT", "5")

    def boom(cmd, **kwargs):
        assert kwargs["timeout"] == 5.0
        raise subprocess.TimeoutExpired(cmd, 5, output="partial", stderr="")

    monkeypatch.setattr(subprocess, "run", boom)
    proc = core._engine("run", "x.feature")
    assert proc.returncode == 124
    assert "timed out" in proc.stderr
    assert proc.stdout == "partial"


def test_engine_timeout_zero_disables(monkeypatch):
    monkeypatch.setenv("NOODLE_ENGINE_TIMEOUT", "0")
    seen = {}

    def fake(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    core._engine("summary")
    assert seen["timeout"] is None


def test_repl_trailing_flag_exits_cleanly(capsys):
    from noodle.repl import repl
    with pytest.raises(SystemExit) as e:
        repl.main(["--workspace"])
    assert e.value.code == 2
    assert "--workspace needs a value" in capsys.readouterr().out


# --- NOOD_0057: per-call workspace override + server_info -------------------------

def _second_ws(tmp_path):
    ws2 = tmp_path / "other-ws"
    (ws2 / "tests").mkdir(parents=True)
    (ws2 / "noodle.yaml").write_text("tests_dir: tests\n")
    return ws2


def test_ws_none_returns_startup_workspace(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    assert server._ws(None) == str(ws)
    assert server._ws("") == str(ws)


def test_tool_accepts_per_call_workspace(ws, tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))  # default: no patterns
    ws2 = _second_ws(tmp_path)
    (ws2 / "docs").mkdir()
    (ws2 / "docs" / "agent_patterns.yaml").write_text(AGENT_PATTERNS)
    # the custom step only resolves in ws2 — proves the override is honoured
    assert server.validate_feature(FEATURE_CUSTOM_STEP)["unmatched"] != []
    assert server.validate_feature(FEATURE_CUSTOM_STEP,
                                   workspace=str(ws2))["unmatched"] == []


def test_ws_rejects_nonexistent_dir(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    with pytest.raises(ValueError, match="not a directory"):
        server._ws(str(ws / "nope"))


def test_ws_enforces_allowed_roots(ws, tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws2 = _second_ws(tmp_path)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    monkeypatch.setattr(server, "_ALLOWED_ROOTS", [ws2.resolve()])
    assert server._ws(str(ws2)) == str(ws2)          # inside a root: allowed
    with pytest.raises(ValueError, match="outside the roots"):
        server._ws(str(ws))                          # outside every root


def test_main_stdio_leaves_roots_unrestricted(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    server.main(["--workspace", str(ws)])
    assert server._ALLOWED_ROOTS is None
    server._WORKSPACE = "."


def test_main_workspace_root_sets_allow_list(ws, tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    root = tmp_path / "team-workspaces"
    root.mkdir()
    server.main(["--workspace", str(ws), "--workspace-root", str(root)])
    assert server._ALLOWED_ROOTS == [root.resolve(), ws.resolve()]
    server._WORKSPACE = "."


def test_main_http_locks_roots_to_workspace(ws, monkeypatch):
    import uvicorn
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setenv("NOODLE_MCP_API_KEY", "sekret")
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: None)
    server.main(["--workspace", str(ws), "--transport", "streamable-http",
                 "--host", "0.0.0.0"])
    assert server._ALLOWED_ROOTS == [ws.resolve()]
    server._WORKSPACE = "."


def test_server_info_identifies_process(ws, monkeypatch):
    import os
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    info = server.server_info()
    assert info["pid"] == os.getpid()
    assert info["workspace"] == str(ws.resolve())
    assert info["noodle_version"]
    assert info["started_at"]  # ISO timestamp set at import


def test_main_logs_identity_to_stderr(ws, monkeypatch, capsys):
    import os
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    server.main(["--workspace", str(ws)])
    err = capsys.readouterr().err
    assert "noodle-mcp" in err and f"pid={os.getpid()}" in err
    assert "restart" in err  # the no-hot-reload warning
    server._WORKSPACE = "."


# --- NOOD_0084: run flags, init_workspace, cost_estimate ---------------------------

def _capture_engine(monkeypatch):
    calls = []

    def fake(*args, workspace="."):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(core, "_engine", fake)
    monkeypatch.setattr(core, "last_result", lambda workspace=".": {})
    return calls


def test_run_test_forwards_browser_retries_parallel(ws, monkeypatch):
    calls = _capture_engine(monkeypatch)
    (ws / "tests" / "a.feature").write_text("Feature: A\n")
    core.run_test("a", workspace=str(ws), browser="firefox", retries=2,
                  parallel=3, parallel_scheme="scenario")
    args = calls[0]
    assert args[:2] == ["run", "tests/a.feature"]
    for pair in (["--browser", "firefox"], ["--retries", "2"],
                 ["--parallel", "3"], ["--parallel-scheme", "scenario"]):
        i = args.index(pair[0])
        assert args[i:i + 2] == pair


def test_run_test_defaults_add_no_flags(ws, monkeypatch):
    calls = _capture_engine(monkeypatch)
    (ws / "tests" / "a.feature").write_text("Feature: A\n")
    core.run_test("a", workspace=str(ws))
    assert calls[0] == ["run", "tests/a.feature"]


def test_run_test_rejects_bad_browser(ws, monkeypatch):
    calls = _capture_engine(monkeypatch)
    r = core.run_test("a", workspace=str(ws), browser="netscape")
    assert r["ok"] is False and "netscape" in r["error"]
    assert calls == []  # rejected before the engine ran


def test_run_test_rejects_bad_parallel_scheme(ws, monkeypatch):
    calls = _capture_engine(monkeypatch)
    r = core.run_test("a", workspace=str(ws), parallel=2, parallel_scheme="file")
    assert r["ok"] is False and "file" in r["error"]
    assert calls == []


def test_mcp_run_test_forwards_new_params(ws, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    seen = {}

    def fake(target=None, **kw):
        seen.update(kw, target=target)
        return {"ok": True}
    monkeypatch.setattr(core, "run_test", fake)
    server.run_test("a", browser="webkit", retries=1, parallel=2,
                    parallel_scheme="scenario")
    assert (seen["browser"], seen["retries"], seen["parallel"],
            seen["parallel_scheme"]) == ("webkit", 1, 2, "scenario")


def test_init_workspace_scaffolds_runnable_workspace(tmp_path):
    target = tmp_path / "fresh"
    r = core.init_workspace(str(target))
    assert r["ok"] and "Created" in r["output"]
    assert (target / "noodle.yaml").is_file()
    assert (target / ".env").is_file()
    # end-to-end: the scaffolded workspace validates a vocabulary feature
    v = core.validate_feature(
        'Feature: F\n  Scenario: S\n    When User waits for the page to load\n',
        workspace=str(target))
    assert v["error"] is None


def test_init_workspace_never_overwrites(tmp_path):
    target = tmp_path / "fresh"
    core.init_workspace(str(target))
    (target / ".env").write_text("NOODLE_MODEL=keep/me\n")
    r = core.init_workspace(str(target), llm="claude")
    assert r["ok"]
    assert (target / ".env").read_text() == "NOODLE_MODEL=keep/me\n"


def test_mcp_init_workspace_respects_allowed_roots(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    monkeypatch.setattr(server, "_ALLOWED_ROOTS", [tmp_path / "allowed"])
    with pytest.raises(ValueError, match="outside the roots"):
        server.init_workspace(str(tmp_path / "elsewhere" / "ws"))
    r = server.init_workspace(str(tmp_path / "allowed" / "ws"))
    assert r["ok"]


def test_cost_estimate_matches_llm_cost_module(ws):
    pytest.importorskip("litellm")
    from noodle.llm import cost as _cost
    f = ws / "tests" / "a.feature"
    f.write_text("Feature: A\n  Scenario: S\n    When User waits for the page to load\n")
    r = core.cost_estimate("tests/a.feature", model="ollama/llama3",
                           workspace=str(ws))
    expected = _cost.estimate(f.read_text(), model="ollama/llama3")
    assert r["ok"] is True
    assert {k: r[k] for k in expected} == expected


def test_cost_estimate_missing_file(ws):
    r = core.cost_estimate("nope.feature", workspace=str(ws))
    assert r["ok"] is False and "nope.feature" in r["error"]

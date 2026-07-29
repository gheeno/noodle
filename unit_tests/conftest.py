import pytest


@pytest.fixture(scope="session", autouse=True)
def _guard_repo_not_polluted():
    """NOOD_0176 — a test that runs a cwd-relative command (`noodle init`,
    `init mcp`, generate…) without `monkeypatch.chdir(tmp_path)` first
    scaffolds straight into the engine checkout (that's how stray
    noodle_tests/, AGENTS.md, .vscode/mcp.json turned up in `git status`).
    Snapshot untracked files at the repo root and fail the session if the
    suite added any, so the leak is caught here — with the filenames — instead
    of surfacing later as mystery files. Session-scoped: one `git status` each
    side, so it can't name the offending test, only the files it left behind.
    Skips silently outside a git checkout (e.g. a wheel test env)."""
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent

    def untracked():
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo, capture_output=True, text=True)
        if r.returncode != 0:
            return None  # not a git checkout — nothing to guard against
        return {ln[3:] for ln in r.stdout.splitlines() if ln.startswith("??")}

    before = untracked()
    yield
    after = untracked()
    if before is not None and after is not None:
        leaked = sorted(after - before)
        assert not leaked, (
            "tests scaffolded into the repo checkout — a cwd-relative command "
            "ran without monkeypatch.chdir(tmp_path). Leaked: " + ", ".join(leaked))


@pytest.fixture(autouse=True)
def _no_report_servers(monkeypatch):
    """NOOD_0200 — `noodle run` serves the reports by default now; a unit test
    that drives the CLI must never spawn a detached report server on the
    developer's machine. The env kill-switch beats the default; a test that
    asserts serving behaviour monkeypatches _spawn_report_server anyway."""
    monkeypatch.setenv("NOODLE_SERVE_REPORTS", "0")


@pytest.fixture(autouse=True)
def _reset_workspace_docs_override():
    """NOOD_0027 — step_resolver.set_docs_dir()/patterns.set_agent_patterns_dir()
    are process-global overrides (mirrors pom.py's existing set_context()
    pattern). Without resetting after each test, a workspace pointed at by
    one test (e.g. `noodle step-search --workspace tmp_path`) would leak into
    unrelated tests running later in the same pytest process."""
    yield
    from noodle.resolver import patterns, step_resolver
    step_resolver.set_docs_dir(None)
    patterns.set_agent_patterns_dir(None)


@pytest.fixture(autouse=True)
def _reset_mcp_allowed_roots():
    """NOOD_0057 — server.main() sets the module-global _ALLOWED_ROOTS
    allow-list; a streamable-http test must not lock later tests' per-call
    workspace overrides out of their tmp dirs."""
    yield
    import sys
    server = sys.modules.get("noodle.mcp.server")
    if server is not None:
        server._ALLOWED_ROOTS = None


@pytest.fixture(autouse=True)
def _reset_browser_cache():
    """NOOD_0183 — hooks._browsers caches one browser per launch-option key for
    the life of the process, so a MagicMock browser cached by one test would be
    handed to the next (its is_connected() is a truthy Mock) and that test's
    `launch.assert_called_once()` would see zero launches. Also stops a real
    browser leaking between tests."""
    yield
    import sys
    hooks = sys.modules.get("noodle.hooks")
    if hooks is not None:
        hooks._browsers.clear()


@pytest.fixture(autouse=True)
def _reset_noodle_console_stream():
    """NOOD_0172 — server.main() calls log.route_console_to_stderr(), a
    process-global flip of the console handler; without restoring it every
    later capsys-stdout test sees no output. Restore to the stream the current
    NOODLE_LOG_FORMAT implies (stdout for text, stderr for json)."""
    yield
    from noodle import log
    stream = "stderr" if log._json_mode() else "stdout"
    for h in log.logger.handlers:
        if isinstance(h, log._LiveStreamHandler):
            h._stream_name = stream

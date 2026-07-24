import pytest


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

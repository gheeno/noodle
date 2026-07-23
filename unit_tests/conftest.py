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

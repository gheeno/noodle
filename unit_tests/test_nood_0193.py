"""NOOD_0193 — the REST doorway: same tools, no MCP handshake.

What must hold: a caller with nothing but an HTTP client reaches every tool,
and the API key gates /api/* exactly as it gates /mcp. If the gate ever stops
covering the new routes, the server becomes remote test execution for anyone
who finds the port — hence the 401 case.
"""
import asyncio
import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from noodle.mcp import rest, server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    return TestClient(rest.mount(Starlette()))


def test_call_tool_without_mcp_handshake(client):
    """The whole point: no initialize, no session id, no SSE — just a POST."""
    r = client.post("/api/tools/server_info", json={})
    assert r.status_code == 200, r.text
    assert r.json()["pid"] == server._info()["pid"]


def test_empty_body_means_no_arguments(client):
    assert client.post("/api/tools/server_info").status_code == 200


def test_every_mcp_tool_is_reachable(client):
    """No hand-written route list to drift: /api/tools mirrors the registry."""
    listed = {t["name"] for t in client.get("/api/tools").json()}
    assert "run_and_report" in listed and "probe_page" in listed
    assert len(listed) > 20


def test_unknown_tool_is_404_not_500(client):
    r = client.post("/api/tools/definitely_not_a_tool", json={})
    assert r.status_code == 404
    assert "unknown tool" in r.json()["error"]


def test_bad_body_is_400(client):
    assert client.post("/api/tools/server_info", content=b"{not json").status_code == 400
    assert client.post("/api/tools/server_info", json=["a", "list"]).status_code == 400


def test_api_key_gate_covers_the_rest_routes():
    """/api/* sits behind the same _require_key ASGI gate as /mcp."""
    guarded = TestClient(server._require_key(rest.mount(Starlette()), "s3cret"))
    assert guarded.get("/api/health").status_code == 401
    assert guarded.get("/api/health", headers={"x-api-key": "wrong"}).status_code == 401
    ok = guarded.get("/api/health", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["noodle_version"]


def test_main_wires_the_routes_onto_the_real_app_behind_the_key(monkeypatch):
    """The wiring, not just the routes: `--transport streamable-http` must hand
    uvicorn an app that has /api/* AND has the key gate in front of it. Unit-
    testing rest.mount() alone would still pass if main() stopped calling it."""
    import uvicorn
    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app))
    monkeypatch.setenv("NOODLE_MCP_API_KEY", "k")
    server.main(["--workspace", ".", "--transport", "streamable-http",
                 "--host", "127.0.0.1", "--port", "0"])

    client = TestClient(served["app"])
    assert client.get("/api/health").status_code == 401           # gate is in front
    assert client.get("/api/health", headers={"x-api-key": "k"}).status_code == 200


def test_mounting_leaves_the_mcp_endpoint_intact():
    """/api/* is added to the streamable-http app, never in place of /mcp."""
    app = rest.mount(server.mcp.streamable_http_app())
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/mcp" in paths and {"/api/health", "/api/tools"} <= paths


def test_committed_openapi_matches_the_live_registry():
    """docs/openapi.json is generated (`python -m noodle.mcp.rest`), so a tool
    added without regenerating it must fail here rather than ship a spec that
    lies to whoever generated a client from it.

    NOOD_0194 — `info.version` is excluded. It is the running build's version,
    not part of the API's shape, and comparing it made this guard fail for two
    reasons that have nothing to do with drift: every release bumps the version
    (so CLAUDE.md's mandatory bump would red CI until someone regenerated a
    file no tool had changed), and an interpreter without noodle's metadata
    installed — anyone who runs `python -m pytest` outside the venv — renders
    it "unknown (not installed)". The live server always reports the true
    version at /api/health."""
    def shape(spec):
        return {**spec, "info": {k: v for k, v in spec["info"].items() if k != "version"}}

    committed = json.loads((REPO / "docs" / "openapi.json").read_text(encoding="utf-8"))
    live = asyncio.run(rest.openapi())
    assert shape(committed) == shape(live), (
        "docs/openapi.json is stale — regenerate with:\n"
        "  python -m noodle.mcp.rest > docs/openapi.json")


def test_openapi_covers_the_four_required_capabilities():
    """The bare minimum the API must do (NOOD_0193): author, update, run
    (single + parallel), and produce all three reports."""
    spec = asyncio.run(rest.openapi())
    paths = spec["paths"]

    # 1. generate a new test  2. update an existing one
    assert "/api/tools/author_test" in paths and "/api/tools/generate_test" in paths
    for tool in ("author_test", "write_feature", "generate_test"):
        body = paths[f"/api/tools/{tool}"]["post"]["requestBody"]
        props = body["content"]["application/json"]["schema"]["properties"]
        assert "overwrite" in props or "append_to" in props, tool

    # 3. run one test, or N in parallel
    run = paths["/api/tools/run_and_report"]["post"]["requestBody"]
    run_props = run["content"]["application/json"]["schema"]["properties"]
    assert {"target", "tag", "parallel", "parallel_scheme"} <= set(run_props)

    # 4. the reports — Allure + RCA, built by the run and hostable afterwards
    assert "/api/tools/get_rca" in paths and "/api/tools/serve_report" in paths
    assert "/api/tools/list_reports" in paths
    assert "serve_reports" in run_props    # returns hosted URLs in the run call

    assert spec["openapi"].startswith("3.1")
    assert spec["security"] and spec["components"]["securitySchemes"]


def test_openapi_and_docs_are_served(client):
    spec = client.get("/api/openapi.json")
    assert spec.status_code == 200
    assert spec.json()["info"]["title"] == "Noodle Engine API"
    ui = client.get("/api/docs")
    assert ui.status_code == 200 and "swagger-ui" in ui.text


def test_no_leak_between_the_api_wok_and_the_engine_api():
    """"api" means two things — the wok that TESTS a REST service, and this
    surface other systems call to DRIVE Noodle. The distinction is only ever
    documentation, so it's the kind of thing that rots: assert each side still
    points at the other, and that the guide isn't named to be mistaken for the
    wok's."""
    docs = REPO / "docs"
    assert not (docs / "api-guide.md").exists(), \
        "a doc named api-guide.md gets opened by people looking for the api wok"

    engine_api = (docs / "engine-api-guide.md").read_text(encoding="utf-8")
    assert "woks.md#api" in engine_api          # → the wok, for anyone who wanted it
    assert "glossary.md#api-means-two-different-things" in engine_api

    wok = (docs / "woks.md").read_text(encoding="utf-8")
    assert "engine-api-guide.md" in wok         # → back the other way

    glossary = (docs / "glossary.md").read_text(encoding="utf-8")
    assert "the api wok" in glossary and "the engine API" in glossary

    spec = asyncio.run(rest.openapi())
    assert spec["info"]["title"] == "Noodle Engine API"
    assert "api wok" in spec["info"]["description"]


def test_keyvault_install_folds_in_the_azure_extra():
    """keyVaultUrl without the `azure` extra installs green and then dies at
    before_all ("the Azure SDK is missing"). The template must add the extra
    itself — a project passing the one secrets knob shouldn't also have to know
    which pip extra backs it."""
    install = (REPO / "ci" / "azure" / "noodle-tests.yml").read_text(encoding="utf-8")
    # NOOD_0205 — the literal EXTRAS='[azure]' became the shared add_extra()
    # helper (parallelProcesses needs the same fold). Assert the two branches
    # that were always the point: empty extras BECOME [azure], and an existing
    # group GAINS it instead of losing itself.
    assert 'EXTRAS="[$1]"' in install
    assert '${EXTRAS%]},$1]' in install, "an existing extras group must gain the extra, not lose itself"
    assert "add_extra azure" in install
    # '$(VAR)' left unexpanded means the variable was never set — same rule as
    # hooks._load_keyvault, or an unconfigured project installs azure for nothing.
    assert "''|'$'*)" in install


def test_browser_deps_do_not_assume_root():
    """`playwright install --with-deps` apt-gets system libs. A locked-down
    self-hosted agent has no passwordless sudo and the job died before any test
    ran, which docs/ci-project-repo.md 7 promised wouldn't happen."""
    install = (REPO / "ci" / "azure" / "noodle-tests.yml").read_text(encoding="utf-8")
    assert "sudo -n true" in install, "--with-deps must be guarded by a sudo probe"
    assert "playwright install chromium\n" in install, "needs a no-root fallback"


def test_pinned_engine_examples_match_the_current_version():
    """The copy-paste `ref: refs/tags/<v>` in the template header, the example
    pipeline and the guide. Pinning a stale version is the NOOD_0133 failure in
    a new costume: the build runs an engine older than the docs describe."""
    import re
    version = re.search(r'^version = "([^"]+)"',
                        (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    for rel in ("ci/azure/noodle-tests.yml", "ci/azure/example-project-pipeline.yml",
                "docs/ci-project-repo.md"):
        # NOOD_0205 — [\w.-]+, not \S+: a pin written inside markdown backticks
        # captured the trailing backtick and read as stale. REPLACE_ME is the
        # scaffold's deliberate placeholder, not a version that fell behind.
        pins = set(re.findall(r"refs/tags/([\w.-]+)", (REPO / rel).read_text(encoding="utf-8")))
        stale = {p for p in pins
                 if p != version and not p.startswith("<") and p != "REPLACE_ME"}
        assert not stale, f"{rel} pins {stale}, but this engine is {version}"


def test_tools_run_off_the_event_loop(monkeypatch):
    """A run must not block the server that's serving it. FastMCP calls sync
    tools inline, so /api/health would stop answering for the whole run — and a
    liveness probe would restart the pod mid-run. Assert the tool body executes
    on some other thread than the loop's."""
    import threading
    seen = {}

    async def fake_call_tool(name, args):
        seen["thread"] = threading.get_ident()
        return {"ok": True}

    monkeypatch.setattr(server.mcp, "call_tool", fake_call_tool)
    result = asyncio.run(rest.dispatch("server_info", {}))
    assert result == {"ok": True}
    assert seen["thread"] != threading.get_ident()


def test_unwrap_returns_the_payload_not_the_mcp_envelope():
    class Text:
        type, text = "text", json.dumps({"ok": True, "n": 1})
    assert rest.unwrap([Text()]) == {"ok": True, "n": 1}

    class Bare:
        type, text = "text", "not json at all"
    assert rest.unwrap([Bare()]) == {"result": "not json at all"}


def test_mermaid_edge_labels_quote_gherkin_tags():
    """Mermaid 11 reads a bare `@` in an edge label as an edge-ID token, so
    `-->|@visual|` renders a parse-error box instead of the diagram. Three
    architecture diagrams shipped broken that way until NOOD_0193. Quote the
    label — `-->|"@visual"|` — and it parses."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for md in list(root.glob("docs/*.md")) + [root / "README.md"]:
        for block in re.findall(r"```mermaid\n(.*?)```", md.read_text(encoding="utf-8"), re.S):
            for line in block.splitlines():
                if re.search(r"\|\s*@", line):
                    offenders.append(f"{md.name}: {line.strip()}")
    assert not offenders, (
        "unquoted @tag in a mermaid edge label — will not render:\n"
        + "\n".join(offenders)
    )

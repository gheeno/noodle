"""NOOD_0193 — the ENGINE API: a plain-HTTP door onto the same MCP tools.

Naming, because "api" is overloaded in this repo: this is the **engine API**,
what another system calls to drive Noodle. It is NOT the **api wok**
(noodle/wok.py, `@api`), which is Noodle testing someone else's REST service.
Testing an API vs. being callable as one — docs/glossary.md pins the terms.

`noodle-mcp --transport streamable-http` is already a network API, but it
speaks MCP: a caller must `initialize`, carry the `mcp-session-id` it gets
back, and parse SSE frames. Fine from an MCP SDK; not available to a Java
service on a platform that blocks MCP, a Jenkins `curl` step, or a dashboard's
`fetch`. A bare tools/call to `/mcp` gets `400 Bad Request: Missing session ID`.

So the same tools are also mounted as ordinary JSON endpoints on the same app,
behind the same API-key gate (`server._require_key`) and the same
`--workspace-root` containment:

    GET  /api/health         -> server_info (also the liveness probe)
    GET  /api/tools          -> [{name, description, input_schema}]
    POST /api/tools/<name>   -> the tool's payload; request body = arguments

    curl -X POST http://host:8080/api/tools/run_and_report \\
         -H "Authorization: Bearer $NOODLE_MCP_API_KEY" \\
         -H "content-type: application/json" \\
         -d '{"headless": true, "retries": 0}'

One dispatcher over `mcp.call_tool`, not 23 hand-written routes: a tool added
to server.py shows up here on its own, keeps its payload budget (NOOD_0164) and
audit event (NOOD_0172), and the two surfaces cannot drift apart. This is not a
third API — it is a second doorway onto the one in server.py.
"""
from __future__ import annotations

import asyncio
import json

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

_ONE_AT_A_TIME = anyio.Lock()


async def dispatch(name: str, args: dict):
    """Run a tool OFF the event loop, one at a time.

    FastMCP invokes sync tool functions inline (`func_metadata`:
    `return fn(...)`), so a five-minute browser run would otherwise block the
    whole server — `/api/health` included. That is precisely what a liveness
    probe hits, so an orchestrator would conclude the pod was dead and restart
    it mid-run. A worker thread keeps the loop answering.

    ponytail: the lock keeps behaviour as it was — one tool at a time. Letting
    runs overlap would race on NOODLE_RUN_ID (process-global, see
    server._tool) and on the workspace's single report/ dir; scale by running
    one server per workspace, not by removing this."""
    async with _ONE_AT_A_TIME:
        from noodle.mcp.server import mcp
        # asyncio.run: call_tool is async but does no real awaiting for sync
        # tools, so a throwaway loop in the worker thread is enough.
        return await anyio.to_thread.run_sync(
            lambda: asyncio.run(mcp.call_tool(name, args)))


def unwrap(result) -> dict:
    """FastMCP returns content blocks; every Noodle tool returns a dict. Parse
    the payload back out so a REST caller gets the dict, not an MCP envelope."""
    if isinstance(result, dict):
        return result
    texts = [b.text for b in result if getattr(b, "type", None) == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"result": texts[0]}      # a tool that returns bare text (get_rca)
    return {"result": texts}


async def openapi() -> dict:
    """The OpenAPI 3.1 spec, BUILT from the tool registry — never hand-written.
    Each tool's own JSON Schema becomes its requestBody, so `docs/openapi.json`
    and the live `/api/openapi.json` are the same document the server actually
    serves, and a hand-edited spec can't drift from the code
    (unit_tests/test_nood_0193.py asserts the committed copy is current)."""
    from noodle.mcp.server import _info
    paths = {
        "/api/health": {"get": {
            "summary": "Liveness + which build is running",
            "operationId": "health", "tags": ["server"],
            "responses": {"200": {"description": "version, pid, workspace",
                                  "content": {"application/json": {"schema": {"type": "object"}}}}}}},
        "/api/tools": {"get": {
            "summary": "Every tool with its JSON Schema",
            "operationId": "listTools", "tags": ["server"],
            "responses": {"200": {"description": "tool descriptors",
                                  "content": {"application/json": {"schema": {"type": "array",
                                                                              "items": {"type": "object"}}}}}}}},
    }
    from noodle.mcp.server import mcp
    for t in await mcp.list_tools():
        paths[f"/api/tools/{t.name}"] = {"post": {
            "summary": (t.description or "").strip().split("\n")[0],
            "description": t.description or "",
            "operationId": t.name,
            "tags": ["tools"],
            "requestBody": {"required": False, "content": {
                "application/json": {"schema": t.inputSchema or {"type": "object"}}}},
            "responses": {
                "200": {"description": "the tool's payload",
                        "content": {"application/json": {"schema": {"type": "object"}}}},
                "400": {"description": "malformed JSON body"},
                "401": {"description": "missing or wrong API key"},
                "404": {"description": "no such tool"},
                "500": {"description": "the tool itself failed; `error` names the cause"}},
        }}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Noodle Engine API",
            "version": _info()["noodle_version"],
            "description":
                "Drive the Noodle test engine from another system: every MCP tool "
                "as a plain JSON endpoint (NOOD_0193), for callers that can't "
                "speak MCP.\n\n"
                "This is the ENGINE API — the caller side. It is not the 'api "
                "wok', which is Noodle testing someone else's REST service with "
                "@api feature files; nothing here is a test target.\n\n"
                "Calls are SYNCHRONOUS: a run returns when it finishes, so set "
                "client read timeouts in minutes, not seconds. One call runs at "
                "a time per server. Green means `failed == 0` AND "
                "`verified == true`.",
        },
        "servers": [{"url": "http://localhost:8080",
                     "description": "noodle-mcp --transport streamable-http"}],
        "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
        "components": {"securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
            "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
        "tags": [{"name": "tools", "description": "the Noodle operations"},
                 {"name": "server", "description": "discovery and liveness"}],
        "paths": paths,
    }


_SWAGGER_UI = """<!doctype html>
<html><head><title>Noodle API</title><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url: "/api/openapi.json", dom_id: "#ui"})</script>
</body></html>
"""


async def _openapi(request: Request) -> JSONResponse:
    return JSONResponse(await openapi())


async def _docs(request: Request) -> HTMLResponse:
    # ponytail: Swagger UI from the CDN, so nothing is vendored or built. An
    # air-gapped host serves docs/openapi.json to its own UI instead.
    return HTMLResponse(_SWAGGER_UI)


async def _health(request: Request) -> JSONResponse:
    from noodle.mcp.server import _info
    return JSONResponse(_info())


async def _tools(request: Request) -> JSONResponse:
    from noodle.mcp.server import mcp
    return JSONResponse([{"name": t.name, "description": t.description,
                          "input_schema": t.inputSchema}
                         for t in await mcp.list_tools()])


async def _call(request: Request) -> JSONResponse:
    from noodle.mcp.server import mcp
    name = request.path_params["name"]
    body = await request.body()
    try:
        args = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"body is not valid JSON: {e}"},
                            status_code=400)
    if not isinstance(args, dict):
        return JSONResponse({"ok": False,
                             "error": "body must be a JSON object of tool arguments"},
                            status_code=400)
    if name not in {t.name for t in await mcp.list_tools()}:
        return JSONResponse({"ok": False, "error": f"unknown tool {name!r} — "
                                                   "GET /api/tools lists them"},
                            status_code=404)
    try:
        return JSONResponse(unwrap(await dispatch(name, args)))
    except Exception as e:
        # ponytail: every in-tool failure is a 500 carrying the exception text,
        # which names the cause (bad argument, workspace outside the allowed
        # roots, driver crash). Split the argument-validation case out to 400 if
        # a caller ever needs to distinguish retryable from permanent.
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"},
                            status_code=500)


def mount(app):
    """Add the REST routes to the streamable-http Starlette app. Called before
    the API-key gate wraps it, so /api/* is authenticated exactly like /mcp."""
    app.router.routes.extend([
        Route("/api/health", _health),
        Route("/api/tools", _tools),
        Route("/api/tools/{name}", _call, methods=["POST"]),
        Route("/api/openapi.json", _openapi),
        Route("/api/docs", _docs),
    ])
    return app


if __name__ == "__main__":   # python -m noodle.mcp.rest > docs/openapi.json
    import asyncio
    print(json.dumps(asyncio.run(openapi()), indent=2))

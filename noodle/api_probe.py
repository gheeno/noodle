"""NOOD_0201 — live API discovery: which localhost answers, and what it serves.

Two facts block fully-automated API authoring when the prompt doesn't carry
them: the base URL, and the real endpoint paths. Both are discoverable — a
dev-loop app is already listening on a well-known localhost port, and most
frameworks publish their OpenAPI document on a well-known path (springdoc's
/v3/api-docs, FastAPI's /openapi.json, ...). The motivating failure: a test
authored from a ticket POSTed to /greeting while the app served /greeting/new
— one spec fetch would have said so.

`discover()` sweeps a bounded loopback port list (plus any ports the repo's
own config names) and reports every live HTTP server with an api-shaped
verdict. `probe(base_url)` interrogates one server: liveness, spec, the full
endpoint list, and copy-ready steps with request-body hints.

Loopback only for the sweep; every request goes through check_target like any
other Noodle call. Nothing here guesses: ambiguity comes back as `questions`,
and the caller (an agent, or author_test) decides.

ponytail: a fixed port list and well-known spec paths, not an nmap. The
ceiling is an app on a port nobody uses with no spec route — then `questions`
carries the ask, which is the correct failure.
"""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path

from noodle.agents.web import rest_client
from noodle.repo_scan import _ignored_dirs, _iter_files, _read, endpoints_from_doc

# Where dev servers actually live: node (3000/5173/4200), flask (5000),
# django/uvicorn (8000), spring/tomcat (8080+), misc proxies (8888/9000).
_DEFAULT_PORTS = (3000, 3001, 3333, 4000, 4200, 4444, 5000, 5001, 5173, 5555,
                  8000, 8080, 8081, 8082, 8090, 8888, 9000, 9090)
# The well-known spec routes, most-common-first: FastAPI, springdoc,
# springfox, ASP.NET, generic.
_SPEC_PATHS = ("/openapi.json", "/v3/api-docs", "/v2/api-docs",
               "/swagger/v1/swagger.json", "/swagger.json", "/api-docs",
               "/openapi.yaml")
_HEALTH_PATHS = ("/actuator/health", "/health", "/healthz")
_HTTP_TIMEOUT = 2           # seconds per request — a dev loop, not a WAN
_CAP = 10                   # per-list payload bound (docs/payload budget)


def _get(url: str) -> tuple[int, str, dict] | None:
    """GET through the normal Noodle client (check_target, no redirects);
    None means nothing HTTP-shaped answered."""
    try:
        return rest_client.rest_call("GET", url, None, None, _HTTP_TIMEOUT)
    except Exception:
        return None


def _parse_spec(path: str, body: str):
    try:
        if path.endswith((".yaml", ".yml")):
            import yaml
            return yaml.safe_load(body)
        return json.loads(body)
    except Exception:
        return None


def _fetch_spec(base: str) -> tuple[str, dict] | None:
    """(spec_url, parsed_doc) from the first well-known route that answers
    with a real OpenAPI document."""
    for p in _SPEC_PATHS:
        r = _get(base.rstrip("/") + p)
        if r and r[0] == 200:
            doc = _parse_spec(p, r[1])
            if isinstance(doc, dict) and ("openapi" in doc or "swagger" in doc):
                return base.rstrip("/") + p, doc
    return None


def _deref(doc: dict, node):
    """One $ref hop ('#/components/schemas/X') — enough for a body hint."""
    if isinstance(node, dict) and "$ref" in node:
        cur = doc
        for part in str(node["$ref"]).lstrip("#/").split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return cur
    return node


def _body_hint(doc: dict, op: dict) -> str | None:
    """A pasteable JSON body for an operation: the spec's example, else
    `{"field": "<type>"}` from the schema's properties."""
    content = (op.get("requestBody") or {}).get("content") or {}
    schema = _deref(doc, (content.get("application/json") or {}).get("schema") or {})
    if not isinstance(schema, dict):
        return None
    if "example" in schema:
        return json.dumps(schema["example"])
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        return json.dumps({k: f"<{(_deref(doc, v) or {}).get('type', 'value')}>"
                           for k, v in list(props.items())[:8]})
    return None


def _suggested_steps(base: str, doc: dict) -> list[str]:
    steps = [f"Given sets {{var:REST_BASE_URL}} to '{base}'"]
    for route, ops in list((doc.get("paths") or {}).items())[:_CAP]:
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            m = str(method).upper()
            if m not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            hint = _body_hint(doc, op) if isinstance(op, dict) else None
            body = f" with body '{hint}'" if hint else ""
            steps.append(f"When performs a {m} call at '{route}'{body}")
    return steps[:_CAP + 5]


def probe(base_url: str) -> dict:
    """One server, fully interrogated: liveness, spec, endpoints, copy-ready
    steps. `questions` carries whatever discovery could not settle."""
    base = base_url.rstrip("/")
    root = _get(base + "/")
    if root is None:
        return {"ok": False, "base_url": base,
                "error": f"nothing answered HTTP at {base}"}
    spec = _fetch_spec(base)
    health = next((p for p in _HEALTH_PATHS
                   if (r := _get(base + p)) and r[0] == 200), None)
    out = {"ok": True, "base_url": base, "status": root[0],
           "server": root[2].get("Server", ""), "health": health,
           "title": "", "spec_url": None, "endpoints": [],
           "endpoints_total": 0, "suggested_steps": [], "questions": []}
    if spec:
        spec_url, doc = spec
        eps = endpoints_from_doc(doc)
        out.update(spec_url=spec_url, endpoints=eps[:_CAP * 4],
                   endpoints_total=len(eps),
                   title=str((doc.get("info") or {}).get("title") or ""),
                   suggested_steps=_suggested_steps(base, doc))
    else:
        out["questions"].append(
            f"no OpenAPI document answered on {base} (tried "
            f"{', '.join(_SPEC_PATHS)}) — which endpoints should be covered? "
            "(a list of paths, or the router file to read them from)")
    return out


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# Repo config files that name the port the app serves on.
# NOOD_0201 — the JS entry points earn their own row: `PORT || 3333` /
# `listen(3333)` is how most Node services declare a port, and nothing else in
# the repo records it (our own BusterBlock test app is exactly this case).
_JS_PORT = re.compile(r"PORT\s*\|\|\s*(\d{2,5})|\.listen\(\s*(\d{2,5})")
_PORT_PATTERNS = {
    "application.properties": re.compile(r"^\s*server\.port\s*[=:]\s*(\d{2,5})", re.M),
    "application.yml": re.compile(r"^\s*port:\s*(\d{2,5})", re.M),
    "application.yaml": re.compile(r"^\s*port:\s*(\d{2,5})", re.M),
    ".env": re.compile(r"^\s*PORT\s*=\s*(\d{2,5})", re.M),
    "docker-compose.yml": re.compile(r'-\s*"?(\d{2,5}):\d{2,5}"?'),
    "docker-compose.yaml": re.compile(r'-\s*"?(\d{2,5}):\d{2,5}"?'),
    "package.json": re.compile(r"--port[= ](\d{2,5})|-p (\d{2,5})"),
    "server.js": _JS_PORT, "app.js": _JS_PORT, "index.js": _JS_PORT,
    "main.js": _JS_PORT, "server.ts": _JS_PORT, "main.ts": _JS_PORT,
}


def hint_ports(root: str | Path) -> list[int]:
    """Ports the repo's own config says it serves on — worth checking before
    (and beyond) the well-known list."""
    r = Path(root).resolve()
    if not r.is_dir():
        return []
    out: dict[int, None] = {}
    for f in _iter_files(r, _ignored_dirs(r)):
        rx = _PORT_PATTERNS.get(f.name)
        if rx is None:
            continue
        for m in rx.finditer(_read(f, 100_000)):
            p = int(next(g for g in m.groups() if g))
            if 1023 < p < 65536:
                out[p] = None
    return list(out)[:_CAP]


def discover(ports: list[int] | None = None, repo_root: str | Path | None = None) -> dict:
    """Every live HTTP server on loopback, api-shaped or not. Candidates are
    evidence for a caller's decision — Noodle still never guesses: exactly one
    api-shaped candidate is an answer, anything else is a question."""
    sweep = list(dict.fromkeys(
        (ports or []) + (hint_ports(repo_root) if repo_root else [])
        + list(_DEFAULT_PORTS)))
    candidates = []
    for port in sweep:
        if not _port_open(port):
            continue
        base = f"http://127.0.0.1:{port}"
        r = _get(base + "/")
        if r is None:
            continue
        status, body, headers = r
        server = headers.get("Server", "")
        if "AirTunes" in server:        # macOS AirPlay squats on 5000/7000
            continue
        spec = _fetch_spec(base)
        ctype = headers.get("Content-Type", "")
        api_shaped = bool(spec) or "json" in ctype or any(
            (h := _get(base + p)) and h[0] == 200 for p in _HEALTH_PATHS[:1])
        cand = {"base_url": base, "status": status, "server": server,
                "content_type": ctype.split(";")[0], "api": api_shaped,
                "spec_url": spec[0] if spec else None,
                "endpoints_total": len(endpoints_from_doc(spec[1])) if spec else 0,
                "endpoints": endpoints_from_doc(spec[1])[:_CAP] if spec else []}
        candidates.append(cand)
    api_candidates = [c for c in candidates if c["api"]]
    questions = []
    if not candidates:
        questions.append(
            "no HTTP server is listening on the well-known localhost ports "
            f"({', '.join(map(str, _DEFAULT_PORTS))}) — is the app under test "
            "running, and on which port?")
    elif len(api_candidates) != 1:
        questions.append(
            f"{len(api_candidates)} api-shaped servers found among "
            f"{len(candidates)} live ones — which base URL is the app under "
            "test?")
    return {"ok": True, "candidates": candidates[:_CAP],
            "api_candidates": [c["base_url"] for c in api_candidates][:_CAP],
            "questions": questions,
            "next": ("exactly one api candidate → probe(base_url) for the "
                     "endpoint list, then author against it; otherwise answer "
                     "`questions` first. Noodle never guesses a URL.")}

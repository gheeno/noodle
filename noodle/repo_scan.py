"""NOOD_0192 — "review this repo and write me some tests": what IS this repo?

A developer standing in their own project asks their agent for tests. The
agent knows the code; what it does NOT know is the two facts Noodle needs
before it can author anything — where the app answers (a URL, or the command
that serves it locally) and which wok the request belongs to. Nothing in the
engine could answer either, so the loop stalled on a question nobody asked.

This is a deterministic scan of marker files, nothing more: no LLM, no code
parsing, no execution. It reports the stack, how the repo says it serves
itself, any OpenAPI spec it ships (with the endpoint list — that is the whole
API test plan, written down by the developer already), where tests live, and
the QUESTIONS still open. The caller is an LLM holding the repo; it can read
the code far better than we can. Our job is to be exact about what we need.

ponytail: marker files and package manifests, not a build-system inspector.
The ceiling is a repo whose real entry point is only in a README sentence or
a CI job — then `questions` carries the ask instead of a guess, which is the
correct failure. Reach for deeper detection only when that proves common.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Directories that never hold the answer and always hold thousands of files.
_SKIP = {".git", "node_modules", ".venv", "venv", "env", "__pycache__",
         "dist", "build", "out", "target", "vendor", ".next", ".nuxt",
         ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".idea",
         ".gradle", "bin", "obj", "artifacts", ".terraform"}
_MAX_DEPTH = 3          # a monorepo's packages live one or two levels down
_CAP = 10               # per-list payload bound (docs/payload budget)

# marker filename → stack label. Order is irrelevant; every match is reported,
# because a repo with a python API and a node front end is BOTH, and that is
# exactly the repo where "generate api and web tests" gets asked.
_MARKERS = {
    "package.json": "node",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "Gemfile": "ruby",
    "Cargo.toml": "rust",
    "composer.json": "php",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
}
# dependency name → framework label, per manifest kind. Substring match on the
# manifest text: reading a lockfile properly is a package-manager's job.
_FRAMEWORKS = {
    "node": ("next", "react", "vue", "svelte", "angular", "express",
             "fastify", "koa", "nest", "remix", "astro"),
    "python": ("fastapi", "flask", "django", "starlette", "sanic",
               "aiohttp", "tornado"),
    "java": ("spring-boot", "quarkus", "micronaut"),
    "ruby": ("rails", "sinatra"),
    "php": ("laravel", "symfony"),
    "go": ("gin-gonic", "echo", "fiber", "chi"),
}
_SPEC_NAMES = re.compile(
    r"^(?:openapi|swagger)\.(?:ya?ml|json)$|"
    r"\.(?:openapi|swagger)\.(?:ya?ml|json)$", re.I)
# The scripts a JS project uses to run itself, most-likely-first.
_SERVE_SCRIPTS = ("dev", "start", "serve", "start:dev", "run", "preview")
_MAKE_TARGET = re.compile(r"^(dev|run|serve|start|up)\s*:", re.M)
_TEST_WORDS = {"test", "tests", "spec", "specs", "e2e"}


def _ignored_dirs(root: Path) -> set[str]:
    """Plain directory names the repo's own .gitignore excludes.

    Not a gitignore implementation — just the unambiguous lines (`build/`,
    `regression_runs/`), which is what keeps generated output out of the
    report. A repo that ignores by glob keeps its globs; we skip nothing
    extra rather than guess wrong.
    """
    out = set()
    try:
        lines = (root / ".gitignore").read_text(errors="replace", encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines[:500]:
        ln = ln.strip().rstrip("/")
        if ln and not ln.startswith(("#", "!")) and not set(ln) & set("*?[]/"):
            out.add(ln)
    return out


def _iter_files(root: Path, skip: set[str] = frozenset()):
    """Every file worth looking at, depth-bounded and skip-pruned."""
    _SKIP_ALL = _SKIP | skip
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if depth < _MAX_DEPTH and e.name not in _SKIP_ALL \
                        and not e.name.startswith("."):
                    stack.append((e, depth + 1))
            elif e.is_file():
                yield e


def _read(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(errors="replace", encoding="utf-8")[:limit]
    except OSError:
        return ""


def _node_serve(p: Path) -> list[str]:
    """The npm scripts this package says start it."""
    try:
        scripts = json.loads(_read(p)).get("scripts") or {}
    except (json.JSONDecodeError, AttributeError):
        return []
    return [f"npm run {s}" for s in _SERVE_SCRIPTS if s in scripts]


def endpoints_from_doc(doc) -> list[str]:
    """`METHOD /path` for every operation in a parsed OpenAPI document — the
    API test plan the developer already wrote down. Shared with the live
    probe (api_probe, NOOD_0201)."""
    paths = (doc or {}).get("paths")
    if not isinstance(paths, dict):
        return []
    out = []
    for route, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method in ops:
            if str(method).upper() in ("GET", "POST", "PUT", "PATCH",
                                       "DELETE"):
                out.append(f"{str(method).upper()} {route}")
    return out


def _spec_endpoints(p: Path) -> list[str]:
    text = _read(p)
    try:
        if p.suffix.casefold() == ".json":
            doc = json.loads(text)
        else:
            import yaml
            doc = yaml.safe_load(text)
    except Exception:
        return []
    return endpoints_from_doc(doc)


def scan(root: str = ".") -> dict:
    """{root, stacks, frameworks, api_specs, endpoints, serve, test_dirs,
    ui, questions, next} — a bounded, deterministic picture of the repo.

    `questions` is the load-bearing key: it is what a driving agent must
    settle with the developer (or read out of the code itself) before Noodle
    can author anything. An empty list means nothing is missing.
    """
    r = Path(root).resolve()
    if not r.is_dir():
        return {"ok": False, "error": f"not a directory: {root}"}
    stacks: dict[str, None] = {}
    frameworks: dict[str, None] = {}
    specs, serve, test_dirs = [], [], {}
    endpoints: list[str] = []
    manifests: list[tuple[str, Path]] = []
    for f in _iter_files(r, _ignored_dirs(r)):
        rel = f.relative_to(r)
        name = f.name
        if kind := _MARKERS.get(name):
            stacks[kind] = None
            manifests.append((kind, f))
        if _SPEC_NAMES.match(name):
            # NOOD_0195 — POSIX: a scan payload names files for an agent
            # and a CI config, not for the local filesystem.
            specs.append(rel.as_posix())
            endpoints += _spec_endpoints(f)
        if name == "package.json":
            serve += _node_serve(f)
        elif name in ("docker-compose.yml", "docker-compose.yaml"):
            serve.append(f"docker compose -f {rel.as_posix()} up")
        elif name in ("Makefile", "makefile") and _MAKE_TARGET.search(_read(f)):
            serve += [f"make {m.group(1)}"
                      for m in _MAKE_TARGET.finditer(_read(f))]
        elif name == "Procfile":
            serve.append("foreman start  # Procfile")
        # where the repo already keeps tests — a new suite belongs beside them
        parts = rel.parts
        for i, part in enumerate(parts[:-1]):
            # token match, not substring: "unit_tests" and "__tests__" are
            # test dirs, "latest" is not.
            if set(re.split(r"[\W_]+", part.casefold())) & _TEST_WORDS:
                test_dirs["/".join(parts[:i + 1])] = None
                break
    for kind, path in manifests:
        text = _read(path).casefold()
        for fw in _FRAMEWORKS.get(kind, ()):
            if fw in text:
                frameworks[fw] = None
    # A front end means a web wok candidate; a spec or an api framework means
    # an api wok candidate. Both is the cross-wok case.
    ui = any(f in frameworks for f in
             ("next", "react", "vue", "svelte", "angular", "remix", "astro"))
    api = bool(specs) or any(
        f in frameworks for f in
        ("fastapi", "flask", "django", "express", "fastify", "koa", "nest",
         "spring-boot", "quarkus", "gin-gonic", "echo", "fiber", "chi",
         "rails", "sinatra", "laravel", "symfony"))
    questions = []
    if not serve:
        questions.append(
            "how is this app served for testing — a base URL, or the command "
            "that starts it locally? (no run script, compose file or Makefile "
            "target found)")
    else:
        questions.append(
            f"which base URL does it answer on once served (e.g. "
            f"`{serve[0]}` → http://localhost:PORT)?")
    if ui and api:
        questions.append(
            "web tests, API tests, or both? (this repo has a UI and an API — "
            "Noodle can do either, and cross-wok in one scenario)")
    if not specs and api:
        questions.append(
            "which endpoints should be covered? (no OpenAPI spec found — a "
            "list of paths, or the router file to read them from)")
    questions.append(
        "which change is under test — a branch/diff range, a ticket, or a "
        "list of features? ('the new features' needs a boundary)")
    return {
        "ok": True,
        "root": str(r),
        "stacks": sorted(stacks)[:_CAP],
        "frameworks": sorted(frameworks)[:_CAP],
        "api_specs": specs[:_CAP],
        "endpoints": endpoints[:_CAP * 2],
        "endpoints_total": len(endpoints),
        "serve": list(dict.fromkeys(serve))[:_CAP],
        "test_dirs": sorted(test_dirs)[:_CAP],
        "woks": [w for w, on in (("web", ui), ("api", api)) if on] or ["web"],
        "questions": questions,
        "next": ("answer `questions`, then send the test as numbered steps "
                 "with the URL in step 1 — `noodle task \"write a test: "
                 "1. GET http://localhost:PORT/... 2. Verify the response "
                 "status is 200\"`. Noodle authors from the answers; it never "
                 "guesses a URL."),
    }

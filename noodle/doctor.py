"""NOOD_0138 — context-aware `noodle doctor`.

The old doctor assumed its path argument was a generated workspace, so running
it in the ENGINE checkout compared engine docs (README.md, CLAUDE.md) against
workspace templates and recommended `noodle init --force` against the source
repo. It also warned on launcher COUNT: two launchers that execute the same
editable build (project .venv + uv tool shim) are redundant, not broken — the
health boundary is conflicting provenance (different version/root/SHA/install
type), because then shells, editors and agents run different code.

One public command, three profiles behind one dispatcher:
  * install   — always runs: active build + launcher provenance on PATH
  * engine    — an engine source checkout: editable linkage, stray workspace files
  * workspace — a generated workspace: config, layout, template drift, MCP config

Read-only by design: diagnose and print the exact context-correct remediation,
never write, never touch the network, never launch a browser. Repair stays in
`noodle init` (workspace) and the documented reinstall commands (install).
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from noodle import install_check

SCOPES = ("auto", "engine", "workspace", "install")


class DoctorError(Exception):
    """Usage/context error that prevents a reliable diagnosis → exit 2."""


@dataclass(frozen=True)
class Check:
    id: str
    scope: str
    status: str  # pass | info | warn | fail
    summary: str
    detail: str | None = None
    remediation: str | None = None


@dataclass(frozen=True)
class DoctorContext:
    kind: str  # engine | workspace | install
    root: Path | None
    start: Path


# --- context resolution -----------------------------------------------------

def _is_engine_root(d: Path) -> bool:
    """ALL structural markers, plus [project].name == "noodle" parsed from
    pyproject.toml — a lookalike dir with a noodle/ package but a different
    project name must not be diagnosed as the engine."""
    if not ((d / "noodle" / "__init__.py").is_file()
            and (d / "noodle" / "cli.py").is_file()
            and (d / "unit_tests").is_dir()
            and (d / "pyproject.toml").is_file()):
        return False
    try:
        meta = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))
        return meta.get("project", {}).get("name") == "noodle"
    except (OSError, tomllib.TOMLDecodeError):
        return False


def _is_workspace_root(d: Path) -> bool:
    """Existence, not parseability: a workspace whose noodle.yaml is broken
    should be diagnosed as a workspace with a failing config check, not
    silently degrade to install-only and hide the problem."""
    return (d / "noodle.yaml").is_file()


def resolve_context(path: Path, scope: str = "auto") -> DoctorContext:
    """Walk `path` and its ancestors only — never siblings, home, or mounts.
    Nearest matching ancestor wins; engine beats workspace when both markers
    sit in the same directory (the engine repo is deliberately its own
    workspace, and an accidental `noodle init` there must not flip the
    diagnosis either)."""
    if scope not in SCOPES:
        raise DoctorError(f"invalid --scope '{scope}' (choose from {', '.join(SCOPES)})")
    try:
        start = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise DoctorError(f"path does not exist or is unreadable: {path}")
    if start.is_file():
        start = start.parent
    if scope == "install":
        return DoctorContext("install", None, start)
    for d in (start, *start.parents):
        if scope in ("auto", "engine") and _is_engine_root(d):
            return DoctorContext("engine", d, start)
        if scope in ("auto", "workspace") and _is_workspace_root(d):
            return DoctorContext("workspace", d, start)
    if scope != "auto":
        raise DoctorError(f"--scope {scope}: no {scope} root found from {start}")
    return DoctorContext("install", None, start)


# --- install profile (always runs) ------------------------------------------

def _active_provenance() -> dict:
    return {
        "version": install_check.dist_version(),
        "kind": {True: "editable", False: "NON-EDITABLE COPY",
                 None: "source tree"}[install_check.is_editable()],
        "root": str(install_check.package_dir()),
        "sha": install_check.git_sha(),
    }


def _differs(a: dict, b: dict) -> bool:
    if a["version"] != b["version"] or a["kind"] != b["kind"]:
        return True
    if os.path.normcase(os.path.realpath(a["root"])) != os.path.normcase(os.path.realpath(b["root"])):
        return True
    return bool(a.get("sha") and b.get("sha") and a["sha"] != b["sha"])


_ONE_PER_CONTEXT = ("keep one canonical launcher per context — engine development: "
                    "the clone's activated .venv; general/editor/agent use: the uv "
                    "tool launcher. Never delete the engine .venv.")


def _fmt_provenance(p: dict) -> str:
    return f"{p['version']} ({p['kind']}) {p['root']}" + (f" @ {p['sha']}" if p.get("sha") else "")


def _launcher_check() -> Check:
    shims = install_check.shims_on_path()
    if len(shims) <= 1:
        return Check("install.launchers", "install", "pass",
                     f"{len(shims)} `noodle` launcher on PATH"
                     + ("" if shims else " (running via python -m?)"),
                     detail=shims[0] if shims else None)
    active = _active_provenance()
    probed = {s: install_check.probe_launcher(s) for s in shims}
    detail = "\n".join(
        f"{s}: " + (p["error"] if "error" in p else _fmt_provenance(p))
        for s, p in probed.items())
    conflicting = [s for s, p in probed.items() if "error" not in p and _differs(p, active)]
    unknown = [s for s, p in probed.items() if "error" in p]
    if conflicting:
        return Check("install.launchers", "install", "fail",
                     f"{len(shims)} launchers on PATH execute DIFFERENT builds (first on PATH wins)",
                     detail=f"active: {_fmt_provenance(active)}\n{detail}",
                     remediation=f"{_ONE_PER_CONTEXT} Remove the stale copy, then "
                                 f"`noodle update` (by hand: {install_check.reinstall_cmd()})")
    if unknown:
        return Check("install.launchers", "install", "warn",
                     f"{len(unknown)} of {len(shims)} launchers on PATH could not report provenance",
                     detail=detail,
                     remediation="run `<launcher> --version` by hand to identify the build")
    return Check("install.launchers", "install", "info",
                 f"{len(shims)} launchers execute the same build — safe; "
                 "optional cleanup reduces ambiguity",
                 detail=detail, remediation=_ONE_PER_CONTEXT)


def install_checks() -> list[Check]:
    checks = [Check("install.active-build", "install", "pass", install_check.build_line())]
    ed = install_check.is_editable()
    if ed is False:
        checks.append(Check(
            "install.editable", "install", "fail",
            "running build is a non-editable copy — a git pull/re-clone will NEVER update this CLI",
            remediation=f"noodle update (by hand: {install_check.reinstall_cmd()})"))
    elif ed is None:
        checks.append(Check("install.editable", "install", "info",
                            "install type unknown (running from a source tree, no dist-info)"))
    else:
        checks.append(Check("install.editable", "install", "pass", "running build is editable"))
    # NOOD_0156 — the post-`git pull`/`git checkout` question: even an editable
    # install goes stale in its DEPENDENCIES when a branch changes them, and the
    # recorded-vs-source version is the visible proxy for "this install predates
    # this checkout".
    vr = install_check.version_report()
    if vr["mismatch"]:
        checks.append(Check(
            "install.version-sync", "install", "warn",
            f"this checkout declares noodle {vr['source']} but the install recorded "
            f"{vr['installed']} — the install predates the checkout, so dependencies "
            "may be stale too",
            remediation="noodle update"))
    checks.append(_launcher_check())
    return checks


# --- engine profile ----------------------------------------------------------

# Files an accidental `noodle init` at the engine root would actually create.
# NOT noodle.yaml: the engine repo deliberately tracks its own (it is its own
# workspace for sample_feature_tests/), and init never overwrites config files.
_WORKSPACE_ARTIFACTS = ("AGENTS.md", "PROMPT_TEMPLATE.md", "noodle_tests")


def engine_checks(root: Path) -> list[Check]:
    """Never calls cli._template_files and never recommends `noodle init`
    against the engine root — engine docs are not workspace templates."""
    checks = [Check("engine.source-root", "engine", "pass",
                    f"engine checkout at {root} (pyproject metadata readable)")]
    pkg = install_check.package_dir()
    if os.path.normcase(str(pkg.parent.resolve())) == os.path.normcase(str(root.resolve())):
        checks.append(Check("engine.install-link", "engine", "pass",
                            "active noodle package resolves to this checkout"))
    else:
        checks.append(Check(
            "engine.install-link", "engine", "fail",
            f"active noodle package is {pkg}, not this checkout — source edits here won't run",
            remediation="activate this clone's environment, or reinstall editable "
                        f"from this clone: {install_check.reinstall_cmd()}"))
    found = [a + ("/" if (root / a).is_dir() else "")
             for a in _WORKSPACE_ARTIFACTS if (root / a).exists()]
    if found:
        checks.append(Check(
            "engine.workspace-artifacts", "engine", "warn",
            "workspace-only files exist at the engine root (accidental `noodle init` here?)",
            detail="Review: " + ", ".join(found),
            remediation="remove them by hand after review — similarly named files "
                        "may contain your own work; doctor never deletes"))
    return checks


# --- workspace profile --------------------------------------------------------

_MCP_FILES = (  # (relative path, container key) — mirrors cli.init_mcp
    (Path(".mcp.json"), "mcpServers"),
    (Path(".vscode") / "mcp.json", "servers"),
    (Path(".copilot") / "mcp-config.json", "mcpServers"),
)


def _config_check(root: Path) -> tuple[Check, str | None]:
    """(check, tests_dir) — tests_dir is None when config is unusable."""
    import yaml
    try:
        loaded = yaml.safe_load((root / "noodle.yaml").read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"expected a mapping of keys, got {type(loaded).__name__}")
    except Exception as e:  # yaml errors are user input, not a traceback
        return Check("workspace.config", "workspace", "fail",
                     f"noodle.yaml is invalid — {type(e).__name__}: {str(e)[:200]}",
                     remediation="fix noodle.yaml by hand; doctor never rewrites config"), None
    from noodle import config as _config
    tests_dir = str(loaded.get("tests_dir", _config.DEFAULTS["tests_dir"]))
    resolved = (root / tests_dir).resolve()
    if Path(tests_dir).is_absolute() or not resolved.is_relative_to(root.resolve()):
        return Check("workspace.config", "workspace", "fail",
                     f"tests_dir '{tests_dir}' escapes the workspace — it must be "
                     "a relative path inside it",
                     remediation="edit tests_dir in noodle.yaml"), None
    return Check("workspace.config", "workspace", "pass", "noodle.yaml is valid"), tests_dir


def _layout_check(root: Path, tests_dir: str) -> Check:
    tests = root / tests_dir
    if not tests.is_dir():
        return Check("workspace.layout", "workspace", "warn",
                     f"configured tests_dir '{tests_dir}' does not exist",
                     remediation=f"noodle init {root}")
    missing = [str(g) for g in ("environment.py", Path("steps") / "z_catch_all.py", "pom.yaml")
               if not (tests / g).is_file()]
    if missing:
        return Check("workspace.layout", "workspace", "warn",
                     "scaffold glue missing under " + tests_dir + ": " + ", ".join(missing),
                     remediation=f"noodle init {root}")
    return Check("workspace.layout", "workspace", "pass",
                 f"tests_dir '{tests_dir}' and scaffold glue present")


def _templates_check(root: Path) -> Check:
    from noodle.cli import _template_files
    stale, missing = [], []
    for f, text in _template_files(root).items():
        rel = str(f.relative_to(root))
        if not f.exists():
            missing.append(rel)
        elif f.read_text() != text:
            stale.append(rel)
    if stale or missing:
        parts = ([f"stale (differ from this noodle version): {', '.join(stale)}"] if stale else []) \
              + ([f"missing: {', '.join(missing)}"] if missing else [])
        return Check("workspace.templates", "workspace", "warn",
                     "generated instruction/template files are not current",
                     detail="\n".join(parts),
                     remediation=(f"noodle init {root} --force refreshes stale files "
                                  "(originals saved *.bak)" if stale
                                  else f"noodle init {root} creates missing files"))
    return Check("workspace.templates", "workspace", "pass",
                 "generated instruction/template files match this noodle version")


def _mcp_check(root: Path) -> Check:
    import shutil
    present, problems = [], []
    for rel, key in _MCP_FILES:
        f = root / rel
        if not f.is_file():
            continue
        present.append(str(rel))
        try:
            entry = json.loads(f.read_text(encoding="utf-8") or "{}").get(key, {}).get("noodle")
        except json.JSONDecodeError:
            problems.append(f"{rel}: unparseable JSON")
            continue
        if not entry:
            continue  # file exists for other servers — noodle simply not wired
        cmd = entry.get("command", "")
        if not (Path(cmd).is_file() or shutil.which(cmd)):
            problems.append(f"{rel}: command '{cmd}' does not exist (stale noodle executable)")
    if problems:
        return Check("workspace.mcp", "workspace", "warn",
                     "MCP client config problems: " + "; ".join(problems),
                     remediation="run `noodle init mcp` from the workspace "
                                 "(--force to overwrite a differing entry)")
    if not present:
        return Check("workspace.mcp", "workspace", "info",
                     "no MCP client config found — fine unless an agent should drive this "
                     "workspace; `noodle init mcp` writes it")
    return Check("workspace.mcp", "workspace", "pass",
                 "MCP client config present and its command resolves: " + ", ".join(present))


def workspace_checks(root: Path) -> list[Check]:
    cfg_check, tests_dir = _config_check(root)
    checks = [cfg_check]
    if tests_dir is not None:
        checks.append(_layout_check(root, tests_dir))
    checks.append(_templates_check(root))
    checks.append(_mcp_check(root))
    return checks


# --- orchestration + rendering -------------------------------------------------

def _guarded(scope: str, fn) -> list[Check]:
    """An individual check crash becomes an explicit fail record, never a
    silent skip or a traceback."""
    try:
        return fn()
    except Exception as e:
        return [Check(f"{scope}.internal-error", scope, "fail",
                      f"{scope} checks crashed — {type(e).__name__}: {str(e)[:200]}")]


def diagnose(path: str = ".", scope: str = "auto") -> tuple[DoctorContext, list[Check]]:
    ctx = resolve_context(Path(path), scope)
    checks = _guarded("install", install_checks)
    if ctx.kind == "engine":
        checks += _guarded("engine", lambda: engine_checks(ctx.root))
    elif ctx.kind == "workspace":
        checks += _guarded("workspace", lambda: workspace_checks(ctx.root))
    return ctx, checks


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status in ("warn", "fail") for c in checks) else 0


def _context_line(ctx: DoctorContext) -> str:
    if ctx.kind == "install":
        return f"Context: install only (no engine/workspace found from {ctx.start})"
    return f"Context: {ctx.kind} {ctx.root}"


def render_text(ctx: DoctorContext, checks: list[Check]) -> str:
    lines = [_context_line(ctx)]
    for c in checks:
        lines.append(f"{c.status.upper():<5} [{c.id}] {c.summary}")
        for extra in filter(None, (c.detail, f"Fix: {c.remediation}" if c.remediation else None)):
            lines += [f"      {line}" for line in extra.splitlines()]
    return "\n".join(lines)


def render_json(ctx: DoctorContext, checks: list[Check]) -> str:
    return json.dumps({
        "ok": exit_code(checks) == 0,
        "context": {"kind": ctx.kind,
                    "root": str(ctx.root) if ctx.root else None,
                    "start": str(ctx.start)},
        "checks": [{k: v for k, v in vars(c).items() if v is not None} for c in checks],
    }, indent=2)

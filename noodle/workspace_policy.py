"""NOOD_0223 — workspace strict mode: the engine owns what the engine wrote.

The failure this closes is a *workflow* one, not a code one. When authoring
blocks, an agent's cheapest-looking next move is to open the .feature and fix
the line by hand — and every hand edit is a fact the engine cannot see. The
next `author --overwrite` silently discards it, the next probe re-derives a
POM that contradicts it, and the run that finally goes green proves a file
nobody can regenerate. Manual patch paths are how a deterministic pipeline
turns into a pile of one-off edits.

Strict mode makes that visible and refuses to run on it. The engine records a
sha256 for every file it writes (`.noodle/authored.json`); before a strict run
it re-hashes them and blocks on any that changed underneath it, naming the one
command that re-establishes ownership.

**What this can and cannot do.** It detects and refuses; it cannot prevent.
Nothing here stops a text editor, and it is not a security control — an agent
that wants to hand-edit still can. What it removes is the *silent* version:
the edit that survives to a green report because no one compared the bytes.

Deliberately OFF by default. A blocking gate a user did not ask for is the
opposite of helpful in someone else's project, so this arms three ways, most
specific first: the `--workspace-strict` flag, `NOODLE_WORKSPACE_STRICT`, then
`workspace_strict: true` in noodle.yaml.

Only ENGINE-OWNED artifacts are tracked: the feature, the POM, and the app's
environments.yaml. Secrets files are excluded on purpose — populating a
credential placeholder by hand is the documented workflow, not drift.

ponytail: untracked .feature files are reported but never block. A workspace
scaffolded by `noodle init` ships sample features the engine never authored,
and a mode whose first act is to condemn the scaffold would be turned off
before it ever caught a real edit.
"""
import hashlib
import json
import os
from pathlib import Path

MANIFEST = Path(".noodle") / "authored.json"
_TRUTHY = {"1", "true", "yes", "on"}


def _manifest_path(workspace: str = ".") -> Path:
    return Path(workspace) / MANIFEST


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rel(path, workspace: str) -> str:
    return Path(os.path.relpath(Path(path).resolve(),
                                Path(workspace).resolve())).as_posix()


def load(workspace: str = ".") -> dict:
    """{relpath: sha256} — what the engine believes it wrote. Missing or
    corrupt manifest reads as empty: drift detection degrades to "nothing is
    owned yet", never to a false accusation."""
    f = _manifest_path(workspace)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def record(workspace: str, paths) -> dict:
    """Claim ownership of `paths` at their current bytes. Called once per
    successful author transaction, AFTER the writes land — a rolled-back
    transaction must leave ownership exactly as it was."""
    owned = load(workspace)
    for p in paths or ():
        p = Path(p)
        if not p.is_file():
            owned.pop(_rel(p, workspace), None)
            continue
        if (d := _digest(p)) is not None:
            owned[_rel(p, workspace)] = d
    f = _manifest_path(workspace)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": 1, "files": owned}, indent=1),
                     encoding="utf-8")
    except OSError:
        # A workspace the engine cannot write to still runs; it just has no
        # ownership record. Failing the author over the manifest would be a
        # bookkeeping file breaking a real test.
        pass
    return owned


def agent_driven() -> bool:
    """NOOD_0228 (G3) — is an AGENT driving this run, rather than a human at a
    terminal? True inside the MCP server (which sets NOODLE_AGENT_MODE), or
    when a caller passes it explicitly.

    The distinction matters because the two audiences want opposite defaults.
    A blocking gate a *tester* did not ask for is the opposite of helpful in
    their own project — Noodle ships enforcement opt-in. But an agent driving
    through the MCP door is the party the rule governs, not the party it
    inconveniences, and for it the gate is the whole point."""
    return os.getenv("NOODLE_AGENT_MODE", "").strip().lower() in _TRUTHY


def enabled(workspace: str = ".", override: bool | None = None) -> bool:
    """Most specific wins: explicit flag, then env, then noodle.yaml, then —
    NOOD_0228 — on by default for agent-driven runs. A `workspace_strict:
    false` in noodle.yaml still wins over that default: a workspace that has
    said no keeps saying no."""
    if override is not None:
        return bool(override)
    env = os.getenv("NOODLE_WORKSPACE_STRICT")
    if env is not None and env.strip():
        return env.strip().lower() in _TRUTHY
    from noodle import config
    configured = config.load(workspace).get("workspace_strict")
    if configured is not None:
        return bool(configured)
    return agent_driven()


def drift(workspace: str = ".") -> list[dict]:
    """Every engine-owned file whose bytes no longer match what the engine
    wrote. `kind` is 'modified' or 'deleted'."""
    out = []
    for rel, want in sorted(load(workspace).items()):
        p = Path(workspace) / rel
        if not p.is_file():
            out.append({"path": rel, "kind": "deleted"})
        elif _digest(p) != want:
            out.append({"path": rel, "kind": "modified"})
    return out


# NOOD_0227 (A5) — the FOOTPRINT of shell improvisation. The engine cannot
# see an agent's shell, but a heredoc'd spec, a scratch script, or a
# hand-copied POM all leave bytes inside the engine-managed tree. Suffixes
# only: a data fixture an app test legitimately reads is none of our
# business, but nothing engine-authored is ever a .py/.sh/.tmp/.bak, and a
# .feature/.yaml the manifest doesn't know arrived from outside the engine.
_FOREIGN_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".ps1", ".tmp", ".bak"}
_TRACKED_SUFFIXES = {".feature", ".yaml", ".yml"}
_FOREIGN_CAP = 20


# NOOD_0228 (G4) — the scan reaches the workspace ROOT, not just the tests
# tree. A heredoc'd spec lands wherever the agent's cwd is, and the audited
# session's went to /tmp — outside any scan by construction. Widening to the
# workspace root catches the far commoner case (a scratch file beside
# noodle.yaml) without pretending to catch the rest.
_SKIP_DIRS = {"sample_app", "sample_api", "report", "artifacts",
              "__pycache__", ".git", ".venv", "venv", "node_modules",
              ".noodle", "allure-results", "allure-report", "diagnostics"}


def foreign_artifacts(workspace: str = ".") -> list[str]:
    """NOOD_0227 (A5) / NOOD_0228 (G4) — files the engine did not write, in
    the tests tree AND at the workspace root: scripts/temp files anywhere, and
    .feature/.yaml files the ownership manifest doesn't know.

    **What this can and cannot see.** It sees a FOOTPRINT. `ls`, `cat`,
    `grep` and `head` leave none — a read-only shell command is invisible to
    filesystem scanning by construction, not by omission. So this is evidence
    of write-shaped improvisation only; the layers that actually remove the
    shell reflex are the commands that made it unnecessary (`noodle workspace
    inspect`, `noodle pom`, `--spec-text`) and a host-level tool allowlist.
    Do not read a clean scan as "no shell was used".

    The scaffold's sample packages are skipped, absence of a manifest reports
    nothing (a hand-authored workspace is a legitimate workflow), and the
    list is capped."""
    from noodle import config
    owned = load(workspace)
    if not owned:
        return []
    ws = Path(workspace)
    roots = [ws / config.load(workspace).get("tests_dir", "noodle_tests")]
    out = []
    try:
        # workspace root: top level only — a deep walk of someone's project
        # would report their application source as "foreign", which it is not.
        for p in sorted(ws.iterdir()):
            if p.is_file() and p.suffix.lower() in _FOREIGN_SUFFIXES:
                out.append(_rel(p, workspace))
        for root in roots:
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                if len(out) >= _FOREIGN_CAP:
                    break
                if not p.is_file():
                    continue
                rel = _rel(p, workspace)
                if _SKIP_DIRS & set(p.parts):
                    continue
                suffix = p.suffix.lower()
                if suffix in _FOREIGN_SUFFIXES or (
                        suffix in _TRACKED_SUFFIXES and rel not in owned):
                    out.append(rel)
    except OSError:
        return out
    return out[:_FOREIGN_CAP]


def gate(workspace: str = ".", override: bool | None = None) -> dict:
    """{'ok': True} when the run may proceed. Under strict mode a drifted
    file blocks with the one command that re-establishes ownership — the
    point is to send the caller back through the engine, not to leave them
    guessing which of the two copies is real.

    NOOD_0227 (A5) — a passing strict gate still reports `foreign` files
    (the shell-improvisation footprint), advisory only."""
    if not enabled(workspace, override):
        return {"ok": True, "strict": False}
    found = drift(workspace)
    if not found:
        out = {"ok": True, "strict": True, "agent_mode": agent_driven()}
        if foreign := foreign_artifacts(workspace):
            # NOOD_0228 (G4) — the COUNT rides the payload whether or not the
            # list is read, so the class self-reports in the diagnostic log.
            out["foreign"] = foreign
            out["foreign_count"] = len(foreign)
            out["foreign_note"] = (
                f"{len(foreign)} file(s) in the workspace that no author "
                "transaction wrote — engine-foreign artifacts (hand-written "
                "or shell-created); they run, but nothing can regenerate "
                "them. Advisory: a read-only shell command leaves no "
                "footprint, so a clean scan is not proof none was used.")
        return out
    lines = ", ".join(f'{d["path"]} ({d["kind"]})' for d in found[:5])
    more = f", +{len(found) - 5} more" if len(found) > 5 else ""
    return {
        "ok": False, "strict": True, "drift": found,
        "error": (
            f"workspace-strict: {len(found)} engine-authored file(s) changed "
            f"outside the engine — {lines}{more}. Strict mode runs only what "
            "the engine wrote: re-author the change through `noodle author` "
            "(prompt/goal + --overwrite), or drop --workspace-strict / "
            "`workspace_strict: false` to run the hand-edited copy. A hand "
            "edit is discarded by the next author lap, so a green run on one "
            "proves a file the engine cannot regenerate."),
    }

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


def enabled(workspace: str = ".", override: bool | None = None) -> bool:
    """Most specific wins: explicit flag, then env, then noodle.yaml."""
    if override is not None:
        return bool(override)
    env = os.getenv("NOODLE_WORKSPACE_STRICT")
    if env is not None and env.strip():
        return env.strip().lower() in _TRUTHY
    from noodle import config
    return bool(config.load(workspace).get("workspace_strict", False))


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


def gate(workspace: str = ".", override: bool | None = None) -> dict:
    """{'ok': True} when the run may proceed. Under strict mode a drifted
    file blocks with the one command that re-establishes ownership — the
    point is to send the caller back through the engine, not to leave them
    guessing which of the two copies is real."""
    if not enabled(workspace, override):
        return {"ok": True, "strict": False}
    found = drift(workspace)
    if not found:
        return {"ok": True, "strict": True}
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

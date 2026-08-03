"""NOOD_0228 (C1) — resolve an ambiguous locator with a command, not a heredoc.

Before this module the engine's own ambiguity warning ended in a YAML snippet
and a file path. That is a hand-edit prescription with no command behind it,
and an agent that follows it leaves bytes the engine cannot regenerate (the
exact class NOOD_0223 workspace-strict exists to refuse).

So the ambiguity becomes addressable. `locator._on_ambiguous` records what it
saw — the phrase, the live URL, and one record per candidate with a scoped
CSS selector — under a STABLE id derived from the phrase, and the remediation
it emits is `noodle pom resolve <id> --choose <candidate>`. The engine picks
the file, writes the key, and keeps ownership: the caller never constructs CSS
and never learns the layout.

Why the app's flat `resources/pom.yaml` and not `pageobjects/<page>_pom.yaml`:
the per-page file is scoped to URLs matching its own filename (NOOD_0212), so
a control clicked to REACH a page silently never resolves from it. The flat
file always applies. `set` may target a page file explicitly, and writes
`match: {}` into it when it does, which is the same trap closed by code
instead of by a parenthetical.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_STORE = Path(".noodle") / "ambiguities.json"
_CAP = 50          # a run that hits 50 distinct ambiguities has a bigger problem
_SELECTOR_KINDS = ("css", "xpath", "id", "testid", "text", "role",
                   "label", "placeholder", "title", "alt_text")


def _store_path(workspace: str = ".") -> Path:
    return Path(workspace) / _STORE


def ambiguity_id(phrase: str) -> str:
    """Stable across runs: the same phrase on the same page is the same
    ambiguity, so an id printed by yesterday's run still resolves today. An
    id that changed every run would force the caller to re-run just to learn
    the argument."""
    return "A" + hashlib.sha256(
        phrase.strip().lower().encode("utf-8")).hexdigest()[:6]


def load(workspace: str = ".") -> dict:
    try:
        data = json.loads(_store_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("ambiguities")
    return items if isinstance(items, dict) else {}


def record(phrase: str, candidates: list[dict], url: str = "",
           app_dir: str | None = None, workspace: str = ".") -> str:
    """Persist one ambiguity; return its id. Best-effort — a workspace the
    engine cannot write to still runs the test, it just cannot offer the
    `pom resolve` shortcut, and the remediation degrades to `pom set`."""
    aid = ambiguity_id(phrase)
    items = load(workspace)
    # Everything is coerced to a plain string on the way in. A candidate's
    # fields come from page.evaluate, so a stubbed or exotic page can hand
    # back anything; a bookkeeping file must never be the thing that breaks
    # a run (the ambiguity warning itself still has to reach the caller).
    items[aid] = {
        "id": aid, "phrase": str(phrase), "url": str(url or ""),
        "app_dir": str(app_dir) if app_dir else None,
        "candidates": [
            {"id": f"C{i}", "used": i == 0,
             "tag": str(c.get("tag", "")), "text": str(c.get("text", "")),
             "css": str(c.get("css", ""))}
            for i, c in enumerate(candidates) if isinstance(c, dict)],
    }
    if len(items) > _CAP:                     # keep the newest _CAP entries
        items = dict(list(items.items())[-_CAP:])
    p = _store_path(workspace)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "ambiguities": items}, indent=1),
                     encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
    return aid


# ── workspace layout ────────────────────────────────────────────────────────
def app_dirs(workspace: str = ".") -> dict[str, Path]:
    """{app_name: app package dir} for every package that has a features/ dir.

    Both layouts are live in one workspace: `noodle init` scaffolds
    <tests_dir>/<app>/features, while an authored package lands under its wok
    at <tests_dir>/<wok>/<app>/features (NOOD_0192). A discovery command that
    only knew one of them would send the caller straight back to `ls`."""
    from noodle import config
    root = Path(workspace) / config.load(workspace).get("tests_dir",
                                                        "noodle_tests")
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for pattern in ("*/features", "*/*/features"):
        for feat in sorted(root.glob(pattern)):
            app_dir = feat.parent
            name = app_dir.name
            if name in out:
                name = f"{app_dir.parent.name}/{name}"
            out[name] = app_dir
    return out


def resolve_app_dir(app: str | None, workspace: str = ".") -> tuple[Path | None, str]:
    """(app_dir, error). No `app` and exactly one package → that one; more
    than one → name them rather than guess."""
    dirs = app_dirs(workspace)
    if not dirs:
        return None, ("no test package in this workspace yet — author one "
                      "first (`noodle author --prompt ...`)")
    if app:
        for name, d in dirs.items():
            if app in (name, d.name):
                return d, ""
        return None, (f"no package named {app!r} — this workspace has: "
                      + ", ".join(sorted(dirs)))
    if len(dirs) == 1:
        return next(iter(dirs.values())), ""
    return None, ("more than one package here — pass --app: "
                  + ", ".join(sorted(dirs)))


def _flat_pom(app_dir: Path) -> Path:
    return app_dir / "resources" / "pom.yaml"


def _read_yaml(path: Path) -> dict:
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                       default_flow_style=False),
        encoding="utf-8")


# ── the three operations ────────────────────────────────────────────────────
def set_entry(phrase: str, *, app: str | None = None, workspace: str = ".",
              page: str | None = None, **selector) -> dict:
    """Pin `phrase` to a selector. `selector` is exactly one of the POM kinds
    (css=…, xpath=…, id=…, …). Writes the app's flat resources/pom.yaml unless
    `page` names a per-page file, which then gets `match: {}` so it is not
    silently scoped to its own filename (NOOD_0212)."""
    kinds = {k: v for k, v in selector.items()
             if k in _SELECTOR_KINDS and v not in (None, "")}
    if len(kinds) != 1:
        return {"ok": False, "error": (
            "pass exactly one selector: "
            + ", ".join(f"--{k}" for k in _SELECTOR_KINDS))}
    phrase = (phrase or "").strip()
    if not phrase:
        return {"ok": False, "error": "phrase is required"}
    app_dir, err = resolve_app_dir(app, workspace)
    if err:
        return {"ok": False, "error": err}
    if page:
        path = app_dir / "resources" / "pageobjects" / f"{page}_pom.yaml"
        data = _read_yaml(path)
        # An unscoped shared file is the only kind that always applies; the
        # engine writes the scope rather than warning about it.
        data.setdefault("match", {})
    else:
        path = _flat_pom(app_dir)
        data = _read_yaml(path)
    key = phrase.lower()
    previous = data.get(key)
    data[key] = dict(kinds)
    try:
        _write_yaml(path, data)
    except OSError as e:
        return {"ok": False, "error": f"cannot write {path}: {e}"}
    _own(workspace, path)
    return {"ok": True, "pom": str(path), "key": key,
            "selector": dict(kinds), "replaced": previous,
            "app": app_dir.name}


def resolve_ambiguity(ambiguity_id_: str, candidate_id: str,
                      workspace: str = ".", app: str | None = None) -> dict:
    """Pin the ambiguity's chosen candidate. The engine already holds the
    selector — the caller supplies only which element it meant."""
    items = load(workspace)
    rec = items.get(ambiguity_id_)
    if rec is None:
        known = ", ".join(sorted(items)) or "none recorded yet"
        return {"ok": False, "error": (
            f"unknown ambiguity {ambiguity_id_!r} — recorded ids: {known}. "
            "Ambiguities are recorded by the run that hit them; run the test "
            "first, or pin the phrase directly with `noodle pom set`.")}
    want = (candidate_id or "").strip().upper()
    match = next((c for c in rec["candidates"] if c["id"] == want), None)
    if match is None:
        listing = ", ".join(
            f"{c['id']}={c.get('text') or c.get('tag') or '?'}"
            for c in rec["candidates"])
        return {"ok": False, "error": (
            f"{candidate_id!r} is not a candidate of {ambiguity_id_} — "
            f"choose one of: {listing}")}
    css = match.get("css")
    if not css:
        return {"ok": False, "error": (
            f"candidate {want} has no selector — pin the phrase directly "
            "with `noodle pom set`")}
    if app is None and rec.get("app_dir"):
        app = Path(rec["app_dir"]).name
    out = set_entry(rec["phrase"], app=app, workspace=workspace, css=css)
    if out.get("ok"):
        out["ambiguity"] = ambiguity_id_
        out["chose"] = want
        out["candidate"] = {k: match.get(k) for k in ("tag", "text", "css")}
    return out


def _entry_keys(data: dict) -> list[str]:
    """Every phrase pinned in a POM file — flat keys plus the ones nested
    under `pages:`/`shared:` (docs/pom formats). Reporting only the flat tier
    would say "no pins" about a page-scoped file that has plenty."""
    keys = {k for k in data if k not in ("match", "pages", "shared")}
    shared = data.get("shared")
    if isinstance(shared, dict):
        keys |= set(shared)
    pages = data.get("pages")
    if isinstance(pages, dict):
        for block in pages.values():
            if isinstance(block, dict):
                keys |= {k for k in block if k != "match"}
    return sorted(keys)


def list_entries(app: str | None = None, workspace: str = ".") -> dict:
    """Every POM pin the workspace holds, per file, with each file's scope.
    The read-only half of `ls resources/pageobjects/`."""
    dirs = app_dirs(workspace)
    if app:
        app_dir, err = resolve_app_dir(app, workspace)
        if err:
            return {"ok": False, "error": err}
        dirs = {app_dir.name: app_dir}
    files = []
    for name, app_dir in sorted(dirs.items()):
        res = app_dir / "resources"
        for path in [_flat_pom(app_dir),
                     *sorted((res / "pageobjects").glob("*_pom.yaml"))]:
            if not path.is_file():
                continue
            data = _read_yaml(path)
            scope = data.get("match")
            files.append({
                "app": name,
                "path": str(path.relative_to(Path(workspace))
                            if path.is_relative_to(Path(workspace)) else path),
                # NOOD_0212 — a per-page file with no `match` applies only on
                # URLs containing its own stem. Saying so per file is the
                # difference between a pin that works and one that looks made.
                "scope": ("everywhere" if isinstance(scope, dict) and not scope
                          else scope if scope is not None
                          else "everywhere" if path.name == "pom.yaml"
                          else f"urls containing {path.stem[:-4]!r}"),
                "keys": _entry_keys(data),
            })
    return {"ok": True, "files": files,
            "ambiguities": [
                {"id": r["id"], "phrase": r["phrase"], "url": r.get("url", ""),
                 "candidates": [{"id": c["id"], "text": c.get("text", ""),
                                 "css": c.get("css", "")}
                                for c in r["candidates"]]}
                for r in load(workspace).values()]}


def _own(workspace: str, path: Path) -> None:
    """A POM the engine wrote is a POM the engine owns — otherwise the next
    workspace-strict run reports its own write as drift."""
    try:
        from noodle import workspace_policy
        workspace_policy.record(workspace, [path])
    except Exception:
        pass

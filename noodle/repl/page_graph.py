"""NOOD_0227 (E1) — the persistent, app-scoped page graph.

Every goal probe pays for real page evidence and then, before this module,
threw it away: the next author call re-probed from zero and a blocked target
three pages away was reported as "the app doesn't have it". The graph keeps
what the probe SAW — URL → control names/headings, plus the transitions the
transaction walked — in `artifacts/page_graph/<app>.json`.

Deliberately narrow read side, because the engine never answers with page
state it has not just looked at (the probe-cache rule, core._probe_cache_ttl):

  * blocker annotation — an unrouted "no probed control matches" blocker gains
    a DATED pointer when the graph knows the name from another URL ("last seen
    on <url>, 4m ago — add that page to goal.navigation"). A hint with
    provenance, never compiled-from evidence.
  * route-repair ranking — core._route_repair light-probes candidate pages;
    graph pages known to hold the missed targets go first, so the one bounded
    repair probe aims where the evidence points.

Writes are best-effort and bounded; a graph that cannot be read or written
means no hints, never an error.
"""
import json
import re
import time
from pathlib import Path

from noodle.repl.goal_evidence import _norm
from noodle.reporting import paths as _paths

_MAX_PAGES = 50
_MAX_NAMES = 120
_MAX_TRANSITIONS = 200


def _path(workspace: str, app: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(app or "app"))
    return (Path(workspace) / _paths.artifacts_root() / "page_graph"
            / f"{safe}.json")


def _load(workspace: str, app: str) -> dict:
    try:
        g = json.loads(_path(workspace, app).read_text(encoding="utf-8"))
        if isinstance(g, dict):
            g.setdefault("pages", {})
            g.setdefault("transitions", [])
            return g
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"pages": {}, "transitions": []}


def _names(blk: dict) -> list[str]:
    out = [str(c.get("name")) for c in blk.get("controls") or []
           if c.get("name")]
    out += [str(h) for h in blk.get("headings") or [] if str(h).strip()]
    return out


def record(workspace: str, app: str, probe_result: dict) -> None:
    """Fold one probe result into the app's graph. Best-effort, bounded."""
    pages = (probe_result or {}).get("pages") or []
    if not pages:
        return
    try:
        g = _load(workspace, app)
        now = time.time()
        for pg in pages:
            blocks = [pg, *(pg.get("revealed") or [])]
            for blk in blocks:
                url = str(blk.get("url") or pg.get("url") or "").split("#")[0]
                if not url:
                    continue
                entry = g["pages"].setdefault(
                    url, {"names": [], "title": "", "seen_at": 0})
                merged = list(dict.fromkeys(
                    [*entry.get("names", []), *_names(blk)]))
                entry["names"] = merged[-_MAX_NAMES:]
                entry["title"] = str(blk.get("title")
                                     or entry.get("title") or "")
                entry["seen_at"] = now
                via = blk.get("revealed_by")
                frm = str(pg.get("url") or "").split("#")[0]
                if via and frm and url != frm:
                    t = {"from": frm, "via": str(via), "to": url,
                         "seen_at": now}
                    g["transitions"] = ([x for x in g["transitions"]
                                         if not (x.get("from") == frm
                                                 and x.get("to") == url)]
                                        + [t])[-_MAX_TRANSITIONS:]
        if len(g["pages"]) > _MAX_PAGES:
            keep = sorted(g["pages"].items(),
                          key=lambda kv: kv[1].get("seen_at", 0))[-_MAX_PAGES:]
            g["pages"] = dict(keep)
        p = _path(workspace, app)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(g, indent=1), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def _age(seen_at: float) -> str:
    d = max(0, time.time() - float(seen_at or 0))
    if d < 90:
        return f"{int(d)}s"
    if d < 5400:
        return f"{int(d / 60)}m"
    if d < 129600:
        return f"{int(d / 3600)}h"
    return f"{int(d / 86400)}d"


def lookup(workspace: str, app: str, target: str,
           exclude_urls=()) -> dict | None:
    """The newest OTHER page whose recorded names contain `target`
    (word-boundary containment, both directions — the goal_evidence rule)."""
    t = _norm(target)
    if len(t) < 3:
        return None
    skip = {str(u).split("#")[0].rstrip("/") for u in exclude_urls or ()}
    best = None
    for url, entry in _load(workspace, app)["pages"].items():
        if url.rstrip("/") in skip:
            continue
        for n in entry.get("names", []):
            nn = _norm(n)
            if t == nn or re.search(rf"\b{re.escape(t)}\b", nn):
                if best is None or entry.get("seen_at", 0) > best["seen_at"]:
                    best = {"url": url, "name": n,
                            "seen_at": entry.get("seen_at", 0)}
                break
    return best


# The two blocker families a graph pointer can aim: an action target or a
# see-check text the probed pages don't show (same families as
# goal.unrouted_targets — another page may hold them).
_TARGET_RE = re.compile(r'^(?:\w+|check) "(.+?)":')


def annotate_blocking(workspace: str, app: str, blocking: list[str],
                      current_urls=()) -> list[str]:
    """Append a dated graph pointer to each unrouted blocker whose target the
    graph knows from another URL. Suffix-only, so the repair/route parsers
    (prefix-anchored) keep matching."""
    out = []
    for b in blocking or []:
        hint = ""
        if ("no probed control matches that name" in b
                or "no probed heading or control shows" in b):
            m = _TARGET_RE.match(b)
            hit = m and lookup(workspace, app, m.group(1),
                               exclude_urls=current_urls)
            if hit:
                hint = (f' — page graph: last seen on {hit["url"]} '
                        f'({_age(hit["seen_at"])} ago); add that page to '
                        "goal.navigation, or let repair.goal take it")
        out.append(b + hint)
    return out


def rank_candidates(workspace: str, app: str, cands: list[str],
                    targets: list[str]) -> list[str]:
    """Order route-repair candidates so graph pages known to hold the missed
    targets are probed first. Never adds or drops a candidate — ranking only,
    the light probe stays the evidence."""
    g = _load(workspace, app)["pages"]

    def score(u: str) -> int:
        names = [_norm(n) for n in (g.get(str(u).split("#")[0]) or {})
                 .get("names", [])]
        return -sum(1 for t in targets
                    if any(_norm(t) == n
                           or re.search(rf"\b{re.escape(_norm(t))}\b", n)
                           for n in names))

    return sorted(cands, key=score)

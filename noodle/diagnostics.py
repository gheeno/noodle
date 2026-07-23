"""NOOD_0147 — session diagnostics: agent-written failure self-reports.

The problem: Noodle workspaces are driven by whatever LLM agent a tester
happens to use (Claude Code, Copilot, codex, …). When a test-development
session goes wrong — the dev-fix loop exhausts its cap, the first run is
red, authoring drags past 20 minutes, the agent burns its token budget —
the only record of *why* lives in that agent's session memory, and it
evaporates when the tester closes the chat. The maintainers never see it.

The mechanism: at session end, if a trigger fired (the trigger table lives
in the scaffolded AGENTS.md + docs/session-diagnostics.md — the *agent*
evaluates them, because only it knows its wall clock and its own spend),
the agent makes ONE `log_diagnostic` MCP call / `noodle diagnostic log`
invocation with a short narrative from memory. This module does the rest
deterministically — zero model calls:

- writes ONE Markdown file with YAML front matter into the workspace's
  gitignored `diagnostics/` folder;
- auto-appends the facts the engine already has on disk (last-run counts +
  failures, compact RCA verdict, engine llm_cost, noodle version) so the
  agent never re-reads logs/reports just to compose the diagnostic;
- scrubs registered secret values (NOOD_0118) from everything it writes;
- enforces the anti-spam guarantees prompt text can't: narrative fields
  are truncated, a repeat write for the same session/app updates the
  existing file instead of adding another, and the folder is capped at
  NOODLE_DIAG_MAX files (oldest rotate out).

`bundle()` zips the folder so a tester can send it back in one command.
"""
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from noodle import config, log

DIR_NAME = "diagnostics"

# The canonical trigger vocabulary. Definitions/thresholds live in
# docs/session-diagnostics.md; this set only gates the `triggers` input so a
# typo'd or invented trigger fails loudly instead of polluting the corpus.
TRIGGERS = ("hard-fail", "first-attempt-fail", "slow-dev", "over-budget", "manual")

# Per-field ceiling on agent-supplied narrative. Diagnostics are meant to be
# a cheap summary from session memory, not a transcript dump.
_FIELD_MAX_CHARS = 4000

_MARKER = "noodle_diagnostic"


def diag_dir(workspace: str = ".") -> Path:
    return Path(workspace) / DIR_NAME


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text).strip()).strip("-").lower()
    return s or "unknown"


def _clip(text) -> str | None:
    if text is None:
        return None
    t = str(text).strip()
    if len(t) > _FIELD_MAX_CHARS:
        t = t[:_FIELD_MAX_CHARS] + "\n… [truncated by noodle — keep diagnostics summary-sized]"
    return t or None


def _front_matter(path: Path) -> dict:
    """Parse a diagnostic file's YAML front matter; {} on anything unexpected
    (hand-edited file, foreign .md dropped into the folder)."""
    try:
        text = path.read_text(encoding="utf-8")
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            return {}
        data = yaml.safe_load(m.group(1)) or {}
        return data if isinstance(data, dict) and _MARKER in data else {}
    except (OSError, yaml.YAMLError):
        return {}


def _engine_facts(workspace: str) -> dict:
    """The facts already on disk from the last run — collected best-effort so
    a diagnostic still lands from a session that never got to a run at all."""
    facts: dict = {}
    try:
        from noodle.repl import core
        result = core.last_result(workspace=workspace)
        # all-zero counts = no run results on disk (a session can die before
        # its first run) — write no facts rather than a misleading 0/0 block
        if (isinstance(result, dict) and not result.get("error")
                and (result.get("passed") or result.get("failed"))):
            for key in ("passed", "failed", "seconds"):
                if key in result:
                    facts.setdefault("last_run", {})[key] = result[key]
            failures = result.get("failures") or []
            if failures:
                facts["last_run_failures"] = failures[:5]
            if result.get("llm_cost"):
                facts["llm_cost"] = result["llm_cost"]
    except Exception:
        pass
    try:
        from noodle.repl import core
        rca = core.rca(workspace=workspace, compact=True)
        if rca and isinstance(rca, str) and "no fail" not in rca.lower():
            facts["rca_compact"] = _clip(rca)
    except Exception:
        pass
    try:
        # NOOD_0156 — one version derivation (install_check) and an explicit
        # mismatch flag when the installed metadata lags the checkout.
        from noodle import install_check
        vr = install_check.version_report()
        facts["noodle_version"] = vr["installed"]
        if vr["mismatch"]:
            facts["noodle_source_version"] = vr["source"]
            facts["version_mismatch"] = True
    except Exception:
        pass
    return facts


def run_attempts(workspace: str = ".") -> dict | None:
    """NOOD_0156 — artifact-derived attempt count: the engine's own
    rca-history.jsonl records one line per failed scenario per run stop-time,
    so the trailing dev-session cluster (runs closer together than
    NOODLE_DIAG_SESSION_GAP_MIN) yields {attempts, failure_sequence} without
    trusting the agent's memory. None when no history exists (the
    caller-supplied count then stands). Failed runs only — a floor, never an
    overcount."""
    from noodle.reporting import paths as _paths
    path = _paths.last_run_root(workspace) / "reports" / "rca-history.jsonl"
    records = []
    try:
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and isinstance(r.get("stop"), (int, float)) \
                    and r.get("stop"):
                records.append(r)
    except OSError:
        return None
    if not records:
        return None
    records.sort(key=lambda r: r["stop"])
    try:
        gap = max(1, int(os.getenv("NOODLE_DIAG_SESSION_GAP_MIN", "120")))
    except ValueError:
        gap = 120
    gap_ms = gap * 60_000
    cluster = [records[-1]]
    for r in reversed(records[:-1]):
        if cluster[0]["stop"] - r["stop"] <= gap_ms:
            cluster.insert(0, r)
        else:
            break
    stops, sequence = [], []
    for r in cluster:
        if r["stop"] not in stops:
            stops.append(r["stop"])
            sequence.append(f'run {len(stops)}: '
                            f'{r.get("category") or "?"}'
                            + (f' — {r.get("scenario")}' if r.get("scenario")
                               else ""))
    return {"attempts": len(stops), "failure_sequence": sequence}


def _existing(folder: Path) -> list[Path]:
    """Diagnostic .md files, oldest first. Bundles (*.zip) and foreign files
    never count toward the cap and are never rotated out."""
    files = [p for p in sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime)
             if _front_matter(p)]
    return files


def _dedupe_target(folder: Path, app_slug: str, session: str | None) -> Path | None:
    """The existing file a repeat write should update instead of duplicating:
    same explicit session id, or — with no session id — a diagnostic for the
    same app written within the NOODLE_DIAG_DEDUPE_MIN window (default 30
    minutes; an agent finishing one session rarely re-logs later than that,
    and two genuinely distinct sessions further apart deserve two files)."""
    if not folder.is_dir():
        return None
    try:
        window = max(1, int(os.getenv("NOODLE_DIAG_DEDUPE_MIN", "30")))
    except ValueError:
        window = 30
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window)
    for path in reversed(_existing(folder)):
        fm = _front_matter(path)
        if session and fm.get("session") == session:
            return path
        if not session and fm.get("app") == app_slug:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                return path
    return None


# --- automatic trigger detection --------------------------------------------
#
# The agent-side triggers (over-budget, manual) need the agent's own memory,
# but hard-fail / first-attempt-fail / slow-dev are all visible from the run
# stream — so the engine detects them itself and folds a `diagnostic_due`
# nudge into the run result the driving agent already reads. That makes the
# mechanism automatic even for an agent that never loaded AGENTS.md.

_STATE = Path(".noodle") / "diag_state.json"


def _slow_minutes() -> int:
    try:
        return max(1, int(os.getenv("NOODLE_DIAG_SLOW_MIN", "20")))
    except ValueError:
        return 20


def track_run(workspace: str, target: str, failed: bool) -> list[str]:
    """Record one run of `target` and return the engine-detectable triggers it
    fired. Per-target state lives in .noodle/diag_state.json; a green run
    clears the target's streak, and state idle past NOODLE_DIAG_SESSION_GAP_MIN
    (default 120 minutes) restarts as a fresh dev session — so a workspace
    that's been red-green for weeks can't misreport 'first-attempt-fail' or
    accumulate a phantom slow-dev clock. Never raises: a diagnostics hiccup
    must not break a run."""
    try:
        now = datetime.now(timezone.utc)
        f = Path(workspace) / _STATE
        try:
            state = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            state = {}
        key = str(target or "<workspace>")
        entry = state.get(key)
        try:
            gap = max(1, int(os.getenv("NOODLE_DIAG_SESSION_GAP_MIN", "120")))
        except ValueError:
            gap = 120
        if entry:
            last = datetime.fromisoformat(entry.get("last_run_at", ""))
            if now - last > timedelta(minutes=gap):
                entry = None
        if entry is None:
            entry = {"first_run_at": now.isoformat(timespec="seconds"),
                     "runs": 0, "reds": 0}
        entry["runs"] += 1
        entry["last_run_at"] = now.isoformat(timespec="seconds")
        fired = []
        elapsed_min = (now - datetime.fromisoformat(entry["first_run_at"])
                       ).total_seconds() / 60
        if failed:
            entry["reds"] += 1
            if entry["runs"] == 1:
                fired.append("first-attempt-fail")
            if entry["reds"] >= config.dev_fix_attempts():
                fired.append("hard-fail")
            if elapsed_min > _slow_minutes():
                fired.append("slow-dev")
            state[key] = entry
        else:
            # green closes the loop: if getting here was slow, that's still a
            # slow-dev story worth capturing; either way the streak resets.
            if entry["reds"] and elapsed_min > _slow_minutes():
                fired.append("slow-dev")
            state.pop(key, None)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state) + "\n")
        return fired
    except Exception:
        return []


def due_hint(fired: list[str]) -> dict:
    """The run-result nudge for fired triggers — points at the one call to
    make and where the full contract lives. Both references are portable
    (NOOD_0145): the CLI form works with MCP blocked, and no repo-relative
    doc path appears (an agent in a test workspace would resolve it against
    the workspace and conclude the doc is missing)."""
    return {"triggers": fired,
            "action": "at session end, ONE log_diagnostic call (`noodle "
                      "diagnostic log`) from session memory — fields: "
                      "read_docs('session-diagnostics') / "
                      "`noodle diagnostic guide`"}


def guide_text() -> str | None:
    """The session-diagnostics contract (docs/session-diagnostics.md) — from
    the source/editable checkout when present, else the copy bundled into
    wheels (pyproject force-include, NOOD_0145 pattern: installed
    distributions must not depend on a source checkout, and the bundled path
    is an implementation detail — never printed)."""
    pkg = Path(__file__).resolve().parent
    for candidate in (pkg.parent / "docs" / "session-diagnostics.md",
                      pkg / "_docs" / "session-diagnostics.md"):
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def write_diagnostic(workspace: str = ".", *, app: str, triggers: list[str],
                     summary: str, timeline: str | None = None,
                     suspected_cause: str | None = None,
                     fixes_tried: str | None = None,
                     duration_min: float | None = None,
                     attempts: int | None = None,
                     agent: str | None = None,
                     agent_cost: str | None = None,
                     session: str | None = None) -> dict:
    """Write (or update) this session's diagnostic file. Returns
    {path, updated, rotated_out, count}."""
    trig = sorted({str(t).strip().lower() for t in (triggers or []) if str(t).strip()})
    unknown = [t for t in trig if t not in TRIGGERS]
    if not trig or unknown:
        raise ValueError(
            f"triggers must be a non-empty subset of {list(TRIGGERS)}"
            + (f" — unknown: {unknown}" if unknown else ""))
    if not str(summary or "").strip():
        raise ValueError("summary is required — one short paragraph on what went wrong")

    folder = diag_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    app_slug = _slug(app)
    now = datetime.now(timezone.utc)

    meta = {
        _MARKER: 1,
        "at": now.isoformat(timespec="seconds"),
        "app": app_slug,
        "triggers": trig,
    }
    if session:
        meta["session"] = _slug(session)
    if duration_min is not None:
        meta["duration_min"] = round(float(duration_min), 1)
    # NOOD_0156 — attempts come from persisted run history when it exists;
    # the agent's remembered count is kept alongside only when it disagrees
    # (the reviewed session remembered 4 of 6 recorded runs).
    hist = run_attempts(workspace)
    if hist:
        meta["attempts"] = hist["attempts"]
        meta["attempts_source"] = "run-history"
        if attempts is not None and int(attempts) != hist["attempts"]:
            meta["attempts_reported_by_agent"] = int(attempts)
        if hist["failure_sequence"]:
            meta["failure_sequence"] = hist["failure_sequence"]
    elif attempts is not None:
        meta["attempts"] = int(attempts)
    if agent:
        meta["agent"] = str(agent)[:120]
    # NOOD_0156 — cost is either a measured value or explicitly "unreported";
    # an "n/a" placeholder hid the reviewed session's real 57-AIC spend.
    cost = str(agent_cost).strip() if agent_cost is not None else ""
    meta["agent_cost"] = (cost[:120] if cost and cost.lower() not in
                          ("n/a", "na", "none", "unknown", "-", "?")
                          else "unreported")
    meta.update(_engine_facts(workspace))

    sections = [("What went wrong (summary)", _clip(summary)),
                ("Timeline / steps taken", _clip(timeline)),
                ("Suspected cause", _clip(suspected_cause)),
                ("Fixes tried", _clip(fixes_tried))]
    body = "\n".join(f"## {title}\n\n{text}\n"
                     for title, text in sections if text)
    content = ("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
               + "---\n\n"
               + f"# Session diagnostic — {app_slug}\n\n" + body)
    content = log.redact(content)  # NOOD_0118 — same value-scrub as run output

    target = _dedupe_target(folder, app_slug, _slug(session) if session else None)
    updated = target is not None
    if target is None:
        target = folder / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{app_slug}.md"
    target.write_text(content, encoding="utf-8")

    # Cap — oldest rotate out; the file just written is never a candidate.
    try:
        cap = max(1, int(os.getenv("NOODLE_DIAG_MAX", "25")))
    except ValueError:
        cap = 25
    files = _existing(folder)
    rotated = []
    for old in files[:max(0, len(files) - cap)]:
        if old != target:
            try:
                old.unlink()
                rotated.append(old.name)
            except OSError:
                pass
    return {"path": str(target), "updated": updated,
            "rotated_out": rotated, "count": len(_existing(folder))}


def list_diagnostics(workspace: str = ".") -> list[dict]:
    """Newest first: the front-matter facts of every diagnostic on disk."""
    out = []
    folder = diag_dir(workspace)
    if not folder.is_dir():
        return out
    for path in reversed(_existing(folder)):
        fm = _front_matter(path)
        fm.pop(_MARKER, None)
        out.append({"file": path.name, **fm})
    return out


def bundle(workspace: str = ".") -> dict:
    """Zip every diagnostic into diagnostics/noodle_diagnostics_<stamp>.zip —
    the one file a tester sends back. Earlier bundles are excluded (and a new
    bundle replaces them: one current zip, not an accumulating pile)."""
    folder = diag_dir(workspace)
    files = _existing(folder)
    if not files:
        return {"error": f"no diagnostics in {folder} — nothing to bundle"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = folder / f"noodle_diagnostics_{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)
    for stale in folder.glob("noodle_diagnostics_*.zip"):
        if stale != out:
            try:
                stale.unlink()
            except OSError:
                pass
    return {"path": str(out), "count": len(files)}

"""Which results belong to THIS run, and which prior results a run replaces.

NOOD_0229. Until now every run began by deleting every `*-result.json` and
`*-attachment.*` under `allure-results/`. That made incremental authoring
destructive: authoring feature B into an app whose feature A had just run
green deleted A's result AND A's evidence image, then rebuilt the report from
the single surviving result. The served report presented itself as the app's
report while covering one feature, and nothing in the payload said so — the
`passed: 1` summary reads as "all green".

The fix splits one overloaded meaning in two:

  * **the results directory accumulates** — a run replaces only the results of
    the scenarios it actually re-ran (matched by historyId/fullName, or by
    feature file when the whole file is re-run), so the Allure report is the
    app package's cumulative state;
  * **the run payload stays run-scoped** — exit code, `last_run.json`,
    `--failed`, the quarantine scan and the RCA must speak for the run that
    just happened, never for a result some earlier run left behind.

A run marks itself with `.noodle-run.json` (run id + wall-clock start). Every
result carries `start` already, so "did this run write it?" is
`result["start"] >= marker["started_ms"]` — no schema change to the Allure
JSON, and no sidecar to keep in sync. **No marker means no filtering**: a
directory produced before this change, or by a bare `behave` invocation,
reads exactly as it did before.
"""
import json
import os
import time
from pathlib import Path

from noodle.reporting import paths as _paths

MARKER = ".noodle-run.json"
# How many feature names report_scope() names before it stops and lets the
# counts speak (NOOD_0164 — the payload is bounded).
_NAME_SAMPLE = 10


def shared_results_dir(results_dir=None) -> Path:
    """The FLAT results dir, even inside a behavex worker.

    A worker writes into `allure-results/p<pid>/` (NOOD_0183) but the stale
    results it replaces live in the shared parent — the merge flattens into
    it at the end of the run. Retention must therefore always act on the
    parent, or a parallel run would purge nothing.
    """
    if results_dir is not None:
        d = Path(results_dir)
    else:
        d = Path(_paths.results_dir())
    if os.getenv("NOODLE_PARALLEL_WORKER") == "1" and d.name.startswith("p") \
            and d.name[1:].isdigit():
        return d.parent
    return d


def begin_run(results_dir=None, run_id: str | None = None) -> int:
    """Stamp the results dir with this run's identity. Returns started_ms.

    Idempotent per run id: the CLI marks a parallel run before spawning
    workers and `hooks.before_all` marks a plain one, and a re-entrant call
    must not move the boundary forward (results already written by this run
    would fall outside it and vanish from the payload).
    """
    d = shared_results_dir(results_dir)
    run_id = run_id or os.getenv("NOODLE_RUN_ID") or ""
    existing = read_marker(d)
    if existing and run_id and existing.get("run_id") == run_id:
        return int(existing.get("started_ms") or 0)
    started = int(time.time() * 1000)
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / MARKER).write_text(
            json.dumps({"run_id": run_id, "started_ms": started}) + "\n",
            encoding="utf-8")
    except OSError:
        pass          # retention is a nicety — never fail a run over it
    return started


def read_marker(results_dir=None) -> dict | None:
    d = shared_results_dir(results_dir)
    try:
        m = json.loads((d / MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return m if isinstance(m, dict) and m.get("started_ms") else None


def run_started_ms(results_dir=None) -> int | None:
    m = read_marker(results_dir)
    return int(m["started_ms"]) if m else None


def in_run(result: dict, started_ms: int | None) -> bool:
    """Did THIS run write `result`?

    Undatable inputs answer YES, deliberately, in both directions: no marker
    (a directory written before this change, or by a bare `behave`) and no
    usable `start` on the result. Dropping a result a run may well have
    written would understate the run — the exact class of dishonesty
    NOOD_0229 exists to remove — where keeping an extra one is visible in the
    counts. Every result the writer produces carries `start`.
    """
    if started_ms is None:
        return True
    start = result.get("start")
    if start is None:
        return True
    try:
        return int(start) >= started_ms
    except (TypeError, ValueError):
        return True


def filter_run(results: list[dict], results_dir=None,
               scope: str = "run") -> list[dict]:
    """`scope="run"` keeps only what this run wrote; "all" keeps everything
    (what the Allure report is built from)."""
    if scope != "run":
        return list(results)
    started = run_started_ms(results_dir)
    if started is None:
        return list(results)
    return [r for r in results if in_run(r, started)]


def _load(path: Path) -> dict | None:
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return r if isinstance(r, dict) else None


def _label(result: dict, name: str) -> str:
    for lab in result.get("labels") or []:
        if lab.get("name") == name:
            return str(lab.get("value") or "")
    return ""


def result_key(result: dict, fallback: str = "") -> str:
    return str(result.get("historyId") or result.get("fullName") or fallback)


def _attachment_sources(result: dict) -> set[str]:
    out = set()
    for a in result.get("attachments") or []:
        if src := a.get("source"):
            out.add(str(src))
    for s in result.get("steps") or []:
        for a in s.get("attachments") or []:
            if src := a.get("source"):
                out.add(str(src))
    return out


def _feature_files(result: dict) -> set[str]:
    """Both spellings of the feature file this result came from — the
    workspace-relative path behave reports and its bare filename — so a
    purge matches whichever the caller has in hand."""
    f = _label(result, "featureFile")
    if not f:
        return set()
    p = Path(f)
    return {p.as_posix(), p.name}


def purge(results_dir=None, *, keys=(), feature_files=(),
          keep_since_ms: int | None = None) -> int:
    """Delete the results this run is about to replace, and only those.

    `keys` — historyId/fullName values (one re-run scenario each).
    `feature_files` — feature file paths (the whole file is being re-run).
    `keep_since_ms` — never delete a result written at/after this instant;
    the caller passes its own run start so retry attempts of the CURRENT run
    survive (mark_flaky needs every attempt) even when a purge fires late.

    A deleted result takes its own attachment copies with it — those files
    are uuid-named and referenced by exactly one result, and leaving them
    behind was how a green run's evidence image outlived the result that
    explained it. Returns the number of result files removed.
    """
    d = shared_results_dir(results_dir)
    if not d.is_dir():
        return 0
    keys = {k for k in keys if k}
    files = {Path(f).as_posix() for f in feature_files if f}
    files |= {Path(f).name for f in feature_files if f}
    if not keys and not files:
        return 0
    doomed, sources = [], set()
    for p in sorted(d.glob("*-result.json")):
        r = _load(p)
        if r is None:
            continue
        if keep_since_ms is not None and in_run(r, keep_since_ms):
            continue
        if result_key(r, p.name) in keys or (_feature_files(r) & files):
            doomed.append(p)
            sources |= _attachment_sources(r)
    if not doomed:
        return 0
    # Never orphan an attachment a SURVIVING result still points at. Sources
    # are uuid-named so this is belt-and-braces, but a shared source would
    # otherwise turn one purge into another report's broken image link.
    keep = set()
    doomed_names = {p.name for p in doomed}
    for p in sorted(d.glob("*-result.json")):
        if p.name in doomed_names:
            continue
        if r := _load(p):
            keep |= _attachment_sources(r)
    for p in doomed:
        p.unlink(missing_ok=True)
    for src in sources - keep:
        (d / src).unlink(missing_ok=True)
    return len(doomed)


def clean_run_ledgers(results_dir=None) -> None:
    """Delete the per-run bookkeeping that must NOT accumulate: junit slices
    (allure would ingest every scenario twice), LLM cost ledgers (load_total
    rglobs them, so a stale one inflates the next run's spend) and healing
    reports. Scenario results and their attachments are deliberately left
    alone — that is the whole point of NOOD_0229."""
    import shutil
    d = shared_results_dir(results_dir)
    if not d.is_dir():
        return
    (d / "junit.xml").unlink(missing_ok=True)
    for f in (*d.glob("junit.*.xml"), *d.glob("llm_cost*.json"),
              *d.glob("healing-report*.txt")):
        f.unlink(missing_ok=True)
    for wd in d.glob("p[0-9]*"):
        if wd.is_dir():
            shutil.rmtree(wd, ignore_errors=True)


def clean_all(results_dir=None) -> None:
    """The pre-NOOD_0229 wipe, kept as the explicit opt-out (`run --fresh`,
    `noodle report reset`): start this app package's report from nothing."""
    d = shared_results_dir(results_dir)
    if not d.is_dir():
        return
    for f in (*d.glob("*-result.json"), *d.glob("*-attachment.*")):
        f.unlink(missing_ok=True)
    clean_run_ledgers(d)


# --- what the report covers vs what the run covered (NOOD_0229, fix 2.2) ----

def covered_features_root(artifacts_root, fallback=None) -> Path | None:
    """The .feature tree this artifacts root's report is meant to cover.

    A single-app run writes into `<app>/report/` (NOOD_0086), so the package's
    features live one level up; a suite-wide run has no such pairing and takes
    the caller's fallback (the workspace tests dir)."""
    app = Path(artifacts_root).parent / "features"
    if app.is_dir():
        return app
    p = Path(fallback) if fallback else None
    return p if p is not None and p.is_dir() else None


def _feature_names(results) -> list[str]:
    """One stable name per feature file: the workspace-relative path behave
    recorded, falling back to the Allure feature label when a result carries
    no featureFile (hand-written fixtures, pre-NOOD_0089 results)."""
    out = set()
    for r in results:
        f = _label(r, "featureFile")
        out.add(Path(f).as_posix() if f else _label(r, "feature"))
    return sorted(f for f in out if f)


def report_scope(results_dir=None, features_root=None) -> dict:
    """The honesty field. A run payload that says `passed: 1` is true about
    the run and silent about the report beside it; a reader with no way to
    tell them apart reported "2 of 2 green" off a report holding one test.

      features_run / scenarios_run        — what THIS run executed
      features_in_report / scenarios_in_report — what the served report holds
      features_in_app                     — .feature files in the package
      note                                — set only when they disagree

    `features_root` is the app package (or tests dir) whose .feature files
    the report is supposed to cover; omit it to skip the third count.
    """
    d = shared_results_dir(results_dir)
    started = run_started_ms(d)
    latest, ran = {}, {}
    for p in sorted(d.glob("*-result.json")) if d.is_dir() else []:
        r = _load(p)
        if r is None:
            continue
        key = result_key(r, p.name)
        latest[key] = r
        if in_run(r, started):
            ran[key] = r

    run_features = _feature_names(ran.values())
    all_features = _feature_names(latest.values())
    # NOOD_0164 — the payload is bounded, and a 1000-feature suite must not
    # spend that budget on a file list nobody reads. The counts are the point;
    # the names are a sample.
    out = {"features_run": run_features[:_NAME_SAMPLE],
           "features_run_count": len(run_features),
           "scenarios_run": len(ran),
           "features_in_report": len(all_features),
           "scenarios_in_report": len(latest)}
    if features_root:
        root = Path(features_root)
        if root.is_dir():
            out["features_in_app"] = len(list(root.rglob("*.feature")))
    notes = []
    if len(run_features) < out["features_in_report"] and run_features:
        notes.append(
            f"this run executed {len(run_features)} of the "
            f"{out['features_in_report']} feature(s) the report holds — the "
            "rest are earlier runs' results, kept, not re-verified")
    if "features_in_app" in out and out["features_in_app"] > out["features_in_report"]:
        notes.append(
            f"the report covers {out['features_in_report']} of "
            f"{out['features_in_app']} feature file(s) in the package — run "
            "the package for a combined report")
    if notes:
        out["note"] = "; ".join(notes)
    return out

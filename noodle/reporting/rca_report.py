"""NOOD_0018 — RCA report generator.

Two independent classifiers feed the same report:

  1. Heuristic (this module, `classify()`) — pure pattern matching over the
     structured failure data already written to allure-results/*-result.json
     (assertion message, traceback, and the console ⚠️ WARNING lines captured
     per-step since NOOD_0018 — see hooks.after_step / log.py). Free, instant,
     deterministic, no model required.

  2. Agentic (noodle/rca.py) — a vision model looking at the failure
     screenshot, when NOODLE_RCA + a vision-capable NOODLE_MODEL are set. Its
     verdict (if any) is already attached as rca_category/rca_reason/rca_fix
     labels on the same result JSON.

`render_markdown` merges both into one table — the heuristic verdict is always
shown (it's free); the agentic verdict is shown alongside it when present, so
you can see where they agree and where the model caught something the rules
can't (a real visual regression, layout shift, wrong color, etc).

`render_markdown_llm` (opt-in, `--llm`) hands the same structured JSON to
`noodle.llm.client.ask` (text-only — no image needed) for a short prose
narrative, in the same spirit as `reporting/summary.py:summarize_llm`.
"""
import html
import json
import os
import re
from pathlib import Path

from noodle.reporting import paths as _paths

# Shared with noodle/rca.py's CATEGORY_LABELS, plus "config-gap" — a category
# the agentic classifier can't name (it's about *absence* of a working model,
# so the vision call that would name it never runs in the first place) — and
# "known-quirk" — a failure a human already diagnosed once and recorded in
# known-quirks.yaml (NOOD_0018 Phase 3 / NOOD_0030).
CATEGORIES = (
    "app-regression", "locator-rot", "environment-flap",
    "test-data", "test-script", "config-gap", "known-quirk",
    "navigation-mismatch", "blocked-by-overlay", "wrong-action-target",
    "mutation-failed", "app-rejected-action", "unknown",
    # NOOD_0207 — the two verdicts that stop RCA blaming the part that worked.
    "assertion-wording", "ambiguous-item-click",
)


def _load_quirks(results_dir: Path) -> list[dict]:
    """known-quirks.yaml (NOOD_0018 Phase 3) — a human-maintained ledger of
    failures already diagnosed once ("qaplayground multi-select is the site's
    own React bug") so classify() never re-derives them. Each entry:

        - match: "regex against scenario name + failure message"
          reason: "what a human concluded"
          fix: "what to do (often: nothing, it's the app/site's bug)"

    ponytail: lives at the workspace root, resolved as results_dir/../.. —
    correct for the default artifacts/allure-results layout; pass an explicit
    file via NOODLE_QUIRKS if NOODLE_ARTIFACTS_DIR points elsewhere."""
    import yaml
    path = Path(os.getenv("NOODLE_QUIRKS", "") or
                results_dir.parent.parent / "known-quirks.yaml")
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [q for q in data if isinstance(q, dict) and q.get("match")]


def _match_quirk(entry: dict, quirks: list[dict]) -> dict | None:
    hay = f"{entry['scenario']}\n{entry['message']}"
    for q in quirks:
        try:
            hit = re.search(str(q["match"]), hay, re.I)
        except re.error:
            continue
        if hit:
            return {
                "category": "known-quirk",
                "confidence": "high",
                "reason": q.get("reason", "Recorded in known-quirks.yaml."),
                "fix": q.get("fix", "See known-quirks.yaml."),
            }
    return None


def _history_path(results_dir: Path) -> Path:
    return results_dir.parent / "reports" / "rca-history.jsonl"


def _update_history(results_dir: Path, entries: list[dict]) -> None:
    """Append-only failure log (NOOD_0018 Phase 2): one JSONL line per
    (historyId, run stop-time, heuristic category). Buys flake-vs-regression
    signal across runs — a scenario failing the same way for the Nth time is
    evidence, not a guess — so repetition promotes confidence to high and the
    report can say "seen before" instead of judging every failure in
    isolation. Deduped on (historyId, stop), so re-rendering the same run's
    report never double-counts.

    ponytail: failures only — pass-tracking (to spot "failed once, then
    recovered" flakes explicitly) can be added when this log has consumers
    that need it."""
    path = _history_path(results_dir)
    prior_records: list[dict] = []
    seen: set = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            prior_records.append(r)
            seen.add((r.get("historyId"), r.get("stop")))

    new_lines = []
    for e in entries:
        key = (e["history_id"], e["stop"])
        if not e["history_id"] or key in seen:
            continue
        seen.add(key)
        new_lines.append(json.dumps({
            "historyId": e["history_id"], "stop": e["stop"],
            "scenario": e["scenario"],
            "category": e["heuristic"]["category"],
            "ai_category": e["ai_category"],
        }))
    if new_lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")

    for e in entries:
        prior = [r for r in prior_records
                 if r.get("historyId") == e["history_id"] and r.get("stop") != e["stop"]]
        same = sum(1 for r in prior if r.get("category") == e["heuristic"]["category"])
        e["prior_failures"] = len(prior)
        e["prior_same_category"] = same
        if same >= 2 and e["heuristic"]["confidence"] != "high":
            e["heuristic"]["confidence"] = "high"


def _history_note(entry: dict) -> str:
    n, same = entry.get("prior_failures", 0), entry.get("prior_same_category", 0)
    if not n:
        return "first recorded failure"
    return f"failed {n} previous run(s), {same} with this same category"


def _latest_results(results_dir: str = None) -> list[dict]:
    """This run's scenario results from allure-results, deduplicated by
    historyId (auto-retry writes one result per attempt — keep the last).

    NOOD_0229 — run-scoped, the single choke point every collect_* below
    reads through. The results directory accumulates across runs so the
    Allure report can cover the whole app package; the RCA is the diagnosis
    of the run that just happened and must not narrate a failure some earlier
    run recorded and this one never re-ran. A dir with no run marker reads
    whole, exactly as before."""
    from noodle.reporting import summary as _summary
    return _summary.latest_results(results_dir)


def collect(results_dir: str = None) -> list[dict]:
    """Failed/errored scenarios from the latest deduplicated results."""
    d = Path(results_dir or _paths.results_dir())
    quirks = _load_quirks(d)
    out = []
    for r in _latest_results(results_dir):
        if r.get("status") not in ("failed", "broken"):
            continue
        labels = {lab["name"]: lab["value"] for lab in r.get("labels", [])
                  if lab["name"] not in ("tag",)}
        tags = [lab["value"] for lab in r.get("labels", []) if lab["name"] == "tag"]
        step = next((s for s in r.get("steps", []) if s.get("status") == "failed"), None)
        details = (step or {}).get("statusDetails", {})
        entry = {
            # NOOD_0089 — provenance: app package + .feature file (labels
            # written by ScenarioResult; empty on pre-0089 result JSON).
            "app": labels.get("parentSuite", ""),
            "feature_file": labels.get("featureFile", ""),
            "feature": labels.get("feature", ""),
            "scenario": r.get("name", ""),
            "step": (step or {}).get("name", ""),
            "message": details.get("message", ""),
            "trace": details.get("trace", ""),
            "warnings": details.get("warnings", []),
            "tags": tags,
            "history_id": r.get("historyId", ""),
            "stop": r.get("stop", 0),
            "ai_category": labels.get("rca_category"),
            "ai_confidence": labels.get("rca_confidence"),
            "ai_reason": labels.get("rca_reason"),
            "ai_fix": labels.get("rca_fix"),
            # NOOD_0141 — warnings from EVERY step, not just the failing one:
            # a no-effect click warns on the (passing) click step while the
            # failure surfaces at a later assertion; classify() needs both.
            "scenario_warnings": [
                w for s in r.get("steps", [])
                for w in (s.get("statusDetails", {}).get("warnings") or [])],
        }
        # A human-recorded quirk beats any heuristic guess (Phase 3).
        entry["heuristic"] = _match_quirk(entry, quirks) or classify(entry)
        # NOOD_0156 — a failed first-party mutation outranks the GENERIC
        # assertion-mismatch verdicts only: the specific engine-stamped ones
        # (navigation-mismatch, blocked-by-overlay, …) and human quirks keep
        # priority, and the request-succeeded correlation upgrades nothing
        # stronger than a low-confidence guess.
        if entry["heuristic"]["category"] in ("app-regression", "test-data",
                                              "unknown"):
            # NOOD_0197 — the correlation is gated on the scenario's own
            # steps: no mutation-shaped step, no mutation verdict. Background
            # app XHR is not the scenario's mutation.
            steps_text = " ".join(s.get("name", "") for s in r.get("steps", []))
            net = (_load_network(d, entry["scenario"])
                   if _MUTATION_STEP_RE.search(steps_text) else None)
            mv = mutation_verdict(entry, net) if net else None
            if mv and (mv["category"] == "mutation-failed"
                       or entry["heuristic"]["confidence"] == "low"):
                entry["heuristic"] = mv
        # NOOD_0197 — cite the goal node behind the failing step when the
        # author's intent trace is reachable; assertion failures from a
        # multi-term check name the compilation as suspect #1.
        hint = goal_node_hint(entry, _load_intent_trace(d))
        if hint:
            entry["goal_node_hint"] = hint
        out.append(entry)
    _update_history(d, out)
    return out


def collect_flaky(results_dir: str = None) -> list[dict]:
    """NOOD_0187 — scenarios that failed and then passed via auto-retry. The
    only prior signal was behave's stdout line, which --quiet (the CI/agent
    default) diverts into run.log — so a flaky pass was indistinguishable
    from a clean one everywhere the run is actually read."""
    d = Path(results_dir or _paths.results_dir())
    files = sorted(d.glob("*-result.json")) if d.is_dir() else []
    groups: dict = {}
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = r.get("historyId") or r.get("fullName") or f.name
        groups.setdefault(key, []).append(r)
    out = []
    for rs in groups.values():
        if len(rs) < 2:
            continue
        final = max(rs, key=lambda r: r.get("stop", 0))
        if final.get("status") != "passed":
            continue
        labels = {lab["name"]: lab["value"] for lab in final.get("labels", [])}
        out.append({"app": labels.get("parentSuite", ""),
                    "feature_file": labels.get("featureFile", ""),
                    "feature": labels.get("feature", ""),
                    "scenario": final.get("name", ""),
                    "attempts": len(rs)})
    return out


def _provenance(results_dir: str = None) -> dict:
    """NOOD_0187 — who/when/what this report is about: run id (from the
    environment.properties the run wrote), generated-at, and the scenario
    counts. A served rca.html with no provenance could not be told apart
    from a different run's — that is how a stale green gets believed."""
    from datetime import datetime
    d = Path(results_dir or _paths.results_dir())
    latest = _latest_results(results_dir)
    counts: dict = {}
    for r in latest:
        s = r.get("status", "")
        counts[s] = counts.get(s, 0) + 1
    run_id = ""
    env_file = d / "environment.properties"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("noodle.run.id="):
                    run_id = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    return {"run_id": run_id, "total": len(latest),
            "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0) + counts.get("broken", 0),
            "skipped": counts.get("skipped", 0),
            "generated": datetime.now().astimezone().isoformat(timespec="seconds")}


def _provenance_line(p: dict) -> str:
    rid = f"run {p['run_id']} — " if p["run_id"] else ""
    return (f"{rid}generated {p['generated']} — {p['total']} scenario(s): "
            f"{p['passed']} passed, {p['failed']} failed, {p['skipped']} skipped")


_EMPTY_RUN_NOTE = ("0 scenarios ran — this report proves nothing. Check the "
                   "path/tag/name filter and the run log.")


def collect_warnings(results_dir: str = None) -> list[dict]:
    """Scenarios that PASSED but a step still logged a ⚠️ warning (ambiguous
    locator, self-heal, vision-locate failure). Lenient mode never fails the
    build on these, so they're invisible once the console output scrolls away
    — this is the same historyId-deduped read as collect(), just for
    status == "passed" instead of failed/broken."""
    out = []
    for r in _latest_results(results_dir):
        if r.get("status") != "passed":
            continue
        labels = {lab["name"]: lab["value"] for lab in r.get("labels", [])
                  if lab["name"] not in ("tag",)}
        for step in r.get("steps", []):
            for w in step.get("statusDetails", {}).get("warnings") or []:
                out.append({
                    "app": labels.get("parentSuite", ""),
                    "feature_file": labels.get("featureFile", ""),
                    "feature": labels.get("feature", ""),
                    "scenario": r.get("name", ""),
                    "step": step.get("name", ""),
                    "warning": w,
                })
    return out


def collect_healing(results_dir: str = None) -> list[dict]:
    """NOOD_0156 — healing events on steps of PASSED scenarios (per-step
    provenance written by hooks.after_step). A green scenario whose click or
    assertion resolved through dom-scan/partial-text/vision is the anatomy of
    a false pass — it must be readable from the compact result, not only from
    healing-report.txt on disk."""
    out = []
    for r in _latest_results(results_dir):
        if r.get("status") != "passed":
            continue
        labels = {lab["name"]: lab["value"] for lab in r.get("labels", [])
                  if lab["name"] not in ("tag",)}
        for step in r.get("steps", []):
            for h in step.get("statusDetails", {}).get("healing") or []:
                out.append({
                    "app": labels.get("parentSuite", ""),
                    "feature_file": labels.get("featureFile", ""),
                    "feature": labels.get("feature", ""),
                    "scenario": r.get("name", ""),
                    "step": step.get("name", ""),
                    **h,
                })
    return out


def write_authoring_blocked(reports_dir, info: dict) -> None:
    """NOOD_0200 — a blocked author produces the report pair anyway.

    A blocked author never launches a browser, so there is no run and there
    were no Allure/RCA artifacts — the engine went silent at the exact moment
    the operator most needed a written explanation. Renders rca.md + rca.html
    (verdict: authoring-blocked) from the blocking list, the typed
    next_action and any failed intent-trace rows. Milliseconds, no browser;
    overwritten by the next real run's reports."""
    d = Path(reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    feature = info.get("feature") or ""
    blocking = [str(b) for b in info.get("blocking") or []]
    nxt = info.get("next_action")
    failed_trace = [t for t in info.get("intent_trace") or []
                    if isinstance(t, dict) and not t.get("ok")]
    md = ["# RCA — authoring blocked", ""]
    if feature:
        md += [f"Feature: `{feature}`", ""]
    md += ["No run happened: authoring did not reach ready, so no browser "
           "was launched and no Allure results exist for it (an Allure "
           "report next to this file is from an earlier run). The blockers "
           "below are the whole story — fix them and re-author.",
           "", "## Blocking"]
    md += [f"- {b}" for b in blocking] or ["- (none recorded)"]
    if nxt:
        md += ["", f"**next_action:** `{nxt}`"]
    if failed_trace:
        md += ["", "## Failed intent-trace rows"]
        md += [f"- {json.dumps(t, default=str)}" for t in failed_trace]
    (d / "rca.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    items = "".join(f"<li>{html.escape(b)}</li>" for b in blocking) \
        or "<li>(none recorded)</li>"
    trace_html = "".join(
        f"<li><code>{html.escape(json.dumps(t, default=str))}</code></li>"
        for t in failed_trace)
    (d / "rca.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>RCA — authoring blocked</title>
<style>body{{font:15px/1.5 system-ui;margin:2rem auto;max-width:64rem;padding:0 1rem}}
.v{{color:#fff;background:#cf222e;display:inline-block;padding:.2rem .8rem;border-radius:.4rem}}</style>
<h1>RCA — <span class="v">authoring blocked</span></h1>
{f"<p>Feature: <code>{html.escape(feature)}</code></p>" if feature else ""}
<p>No run happened: authoring did not reach ready, so no browser was launched
and no Allure results exist for it (an Allure report next to this file is
from an earlier run). The blockers below are the whole story — fix them and
re-author.</p>
<h2>Blocking</h2><ul>{items}</ul>
{f"<p><b>next_action:</b> <code>{html.escape(str(nxt))}</code></p>" if nxt else ""}
{f"<h2>Failed intent-trace rows</h2><ul>{trace_html}</ul>" if trace_html else ""}
""", encoding="utf-8")


def collect_evidence(results_dir: str = None) -> list[dict]:
    """NOOD_0153 — every image attached to a step across the latest results:
    evidence shots on passed steps, explicit "takes a screenshot" shots, and
    failure screenshots — so the RCA reports can show the proof a test did
    what it claims next to the verdicts. Ordered by scenario stop time."""
    d = Path(results_dir or _paths.results_dir())
    out = []
    for r in sorted(_latest_results(results_dir), key=lambda r: r.get("stop", 0)):
        labels = {lab["name"]: lab["value"] for lab in r.get("labels", [])
                  if lab["name"] != "tag"}
        for step in r.get("steps", []):
            for att in step.get("attachments") or []:
                if not str(att.get("type", "")).startswith("image/"):
                    continue
                src = att.get("source", "")
                out.append({
                    "app": labels.get("parentSuite", ""),
                    "feature_file": labels.get("featureFile", ""),
                    "feature": labels.get("feature", ""),
                    "scenario": r.get("name", ""),
                    "step": step.get("name", ""),
                    "status": step.get("status", ""),
                    "kind": att.get("name", "evidence"),
                    "path": str(d / src) if src else "",
                    "source": src,
                })
    return out


# --- NOOD_0156 — mutation-aware RCA -----------------------------------------
#
# The NOOD_0156 regression: the network capture already contained the
# aborted add-to-cart request, but the report reduced the failure to a generic
# assertion mismatch. These helpers correlate a failed postcondition with the
# scenario's own first-party mutation traffic — from the capture hooks already
# write, no extra probe/inspect/screenshot read.

_ANALYTICS_RE = re.compile(
    r"analytics|doubleclick|googletag|google-analytics|gtag|gtm\.|adobedtm"
    r"|omtrdc|demdex|criteo|facebook|hotjar|clarity|segment|optimizely"
    r"|newrelic|nr-data|datadog|sentry|tiktok|snapchat|branch\.io|braze"
    r"|amplitude|mixpanel|quantummetric|/pixel|beacon", re.I)

_ASSERTION_RE = re.compile(
    r"Expected |Comparison failed|should (contain|equal)|not found"
    r"|does not (contain|equal)|AssertionError")

# NOOD_0197 — mutation_verdict may only speak when the scenario itself did
# something mutation-shaped. A read-only flow (navigate → search → assert)
# was classified mutation-failed off a BACKGROUND app XHR aborted by
# navigation: same registrable domain, non-GET, so the correlation fired and
# sent the agent to "fix the action" on a scenario with no mutating action.
_MUTATION_STEP_RE = re.compile(
    r"sav\w+|submit|creat\w+|delet\w+|remov\w+|updat\w+|upload|register"
    r"|add(?:s|ed|ing)? |send|approv\w+|pay\w*|purchas\w+|order|book"
    r"|check(?:s|ed)? ?out|buy|log(?:s)? ?in|sign(?:s)? ?(?:in|up)"
    r"|\bPOST\b|\bPUT\b|\bPATCH\b|\bDELETE\b", re.I)


def _load_intent_trace(results_dir: Path) -> dict:
    """NOOD_0197 — the author-persisted goal trace (scenario → node entries).
    Author-time and run-time artifact roots usually coincide (the atomic
    author→run flow); when a per-app reroute separates them the classic
    workspace root is the second candidate, and absence just means no
    citation."""
    for p in (results_dir.parent / "intent_trace.json",
              Path("artifacts") / "intent_trace.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def goal_node_hint(entry: dict, traces: dict) -> str | None:
    """NOOD_0197 — name the goal node that produced the failing step, so RCA
    points at the compiler's input instead of an unrelated request. A
    multi-term (any_of) node makes the COMPILATION suspect #1: the session
    this fixes watched a correct disjunction compiled as a conjunction while
    RCA blamed a background POST."""
    rec = traces.get(entry.get("scenario") or "")
    if not isinstance(rec, dict):
        return None
    quoted = re.findall(r'["\']([^"\']+)["\']', entry.get("step") or "")
    if not quoted:
        return None
    for t in rec.get("trace") or []:
        terms = [str(x) for x in (t.get("terms") or [])]
        req = str(t.get("requirement") or "")
        if any(q in terms or q in req for q in quoted):
            node = str(t.get("node") or "?")
            hint = f"This step was compiled from goal node {node} ({req})."
            if len(terms) > 1:
                hint += (" That node is a multi-term check — verify its "
                         "compilation (one disjunctive step, every member "
                         "present) before touching any action.")
            return hint
    return None


def _url_parts(url: str) -> tuple[str, str]:
    from urllib.parse import urlsplit
    try:
        s = urlsplit(url)
        return (s.hostname or "").lower(), s.path or "/"
    except ValueError:
        return "", ""


def _same_site(h1: str, h2: str) -> bool:
    return bool(h1) and bool(h2) and \
        h1.split(".")[-2:] == h2.split(".")[-2:]


def _load_network(results_dir: Path, scenario: str) -> dict | None:
    """The scenario's network capture (hooks.after_scenario writes one JSON
    per scenario under <artifacts>/network/, sibling of allure-results)."""
    safe = scenario.replace(" ", "_").replace("/", "_")[:80]
    path = results_dir.parent / "network" / f"{safe}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def mutation_verdict(entry: dict, net: dict) -> dict | None:
    """Pure — correlate a failed assertion with the scenario's first-party
    mutation traffic. Three distinct stories (never conflated):

      * the mutation request FAILED at the network layer (aborted) — the
        state change never reached the server;
      * the mutation request completed with a NON-SUCCESS status — the server
        refused it;
      * the mutation completed successfully but the asserted state did not
        update — an application-level rejection or a wrong observation point.

    ("the click itself did nothing" is already the wrong-action-target /
    no-observable-effect verdict upstream.) Endpoint detail is REDACTED to
    method + path: no host, query, payload, cookies, or headers ever reach
    the report. Third-party/analytics failures are ignored. None when the
    failure isn't assertion-shaped or no first-party mutation exists."""
    if not isinstance(net, dict) or not _ASSERTION_RE.search(_text(entry)):
        return None
    first_host = next(
        (h for h in (_url_parts(u)[0] for u in net.get("requests") or []) if h),
        "")

    def _first_party(url: str) -> bool:
        host, _ = _url_parts(url)
        return _same_site(first_host, host) and not _ANALYTICS_RE.search(url)

    for f in net.get("failed_requests") or []:
        m = re.match(r"^(\S+)\s+(\S+)\s+—\s+(.*)$", str(f))
        if not m:
            continue
        method, url, failure = m.groups()
        if method.upper() in ("GET", "HEAD", "OPTIONS") or not _first_party(url):
            continue
        _, path = _url_parts(url)
        return {
            "category": "mutation-failed",
            "confidence": "high",
            "reason": f"The state-changing request '{method} {path}' "
                      f"failed at the network layer ({failure or 'aborted'}) "
                      "— the mutation never reached the server, so the "
                      "asserted postcondition structurally cannot hold. "
                      "(endpoint redacted to method + path)",
            "fix": "Fix the action that issues this request — the click may "
                   "fire while the request dies (overlay, consent gate, "
                   "anti-automation). Do not weaken the assertion: it is "
                   "correctly reporting that the state never changed.",
        }
    for f in net.get("failed_responses") or []:
        m = re.match(r"^(\d{3})\s+(\S+)\s+(\S+)$", str(f))
        if not m:
            continue
        status, method, url = m.groups()
        if method.upper() in ("GET", "HEAD", "OPTIONS") or not _first_party(url):
            continue
        _, path = _url_parts(url)
        return {
            "category": "mutation-failed",
            "confidence": "high",
            "reason": f"The state-changing request '{method} {path}' "
                      f"returned HTTP {status} — the server refused the "
                      "mutation, so the asserted postcondition cannot hold. "
                      "(endpoint redacted to method + path)",
            "fix": "Inspect why the server rejects this call (stale session, "
                   "missing precondition, bad payload from the flow) before "
                   "touching locators or assertions.",
        }
    muts = [m for m in net.get("mutations") or []
            if " " in str(m) and _first_party(str(m).split(" ", 1)[1])]
    if muts:
        method, url = str(muts[-1]).split(" ", 1)
        _, path = _url_parts(url)
        return {
            "category": "app-regression",
            "confidence": "medium",
            "reason": f"The state-changing request '{method} {path}' "
                      "completed without a network error, yet the asserted "
                      "state did not update — an application-level rejection, "
                      "or the assertion observes the wrong destination. "
                      "(endpoint redacted to method + path)",
            "fix": "Verify the destination the assertion reads (the result "
                   "may land elsewhere) and the app's handling of the "
                   "mutation; the request itself went through.",
        }
    return None


def _has_warning(entry: dict, pattern: str) -> bool:
    return any(re.search(pattern, w) for w in entry["warnings"])


def _text(entry: dict) -> str:
    return f"{entry['message']}\n{entry['trace']}"


_EXC_RE = re.compile(r"^(\w+(?:Error|Exception)):", re.MULTILINE)

# NOOD_0207 — the near-miss stamp actions._rendered_near_miss writes at failure
# time. NOOD_0223 lifted it to module level so classify() and the auto-fix lap
# read the SAME pattern: a second copy would drift, and the drift would show up
# as an auto-fix that rewrites an assertion the report says is fine.
_NEAR_MISS_RE = re.compile(r"Expected to see '([^']*)' on page — not found\. "
                           r"\[near-miss\] the page renders: (.+?)(?:\n|$)")


def assertion_rewrite(entry: dict) -> dict | None:
    """NOOD_0223 — {'expected': ..., 'rendered': [...]} when this failure is a
    pure wording mismatch the page itself already answered, else None.

    This is the one runtime failure whose fix is fully determined by evidence
    the engine collected at failure time: the assertion reached its page, and
    the page renders a near twin of the expected string. `classify` reports
    that; the auto-fix lap ACTS on it, which is why the extraction lives here
    next to the pattern rather than in the caller.

    ponytail: the rendered alternatives were joined with '; ' by repr(), so a
    rendered line containing that exact separator splits wrong. Harmless — a
    bad split yields a candidate that simply does not match any goal check,
    and the lap declines rather than rewriting something at random."""
    m = _NEAR_MISS_RE.search(_text(entry))
    if not m:
        return None
    import ast
    rendered = []
    for part in m.group(2).split("; "):
        part = part.strip()
        try:
            value = ast.literal_eval(part)
        except (ValueError, SyntaxError):
            value = part.strip("'\"")
        if isinstance(value, str) and value.strip():
            rendered.append(value.strip())
    return {"expected": m.group(1), "rendered": rendered} if rendered else None


def classify(entry: dict) -> dict:
    """Pure, no I/O — pattern-match the structured failure into a category +
    plain-English reason + suggested fix. Unit-testable without a run."""
    text = _text(entry)

    # NOOD_0228 — the refusal the engine issues itself. Without a rule it fell
    # to unknown/low with "inspect the trace manually", which is the same
    # defect as the ambiguity warning below: the engine knows precisely what
    # is wrong and hands back homework. A run only reaches here on a feature
    # written against an older engine, so the fix is the migration.
    if "not accepted inside step text" in text:
        return {
            "category": "test-script",
            "confidence": "high",
            "reason": "This step carries an evidence request inside its text. "
                      "Run configuration rides as scenario tag metadata "
                      "(@evidence:steps=<n>, 1-based), never in the sentence "
                      "the tester reads — the engine refuses to run it.",
            "fix": "noodle migrate evidence-markers --write  (converts this "
                   "workspace's features to the tag form in one pass)",
        }

    # NOOD_0135 — wrong page beats every locator verdict: when navigation
    # landed off the requested path, the "missing" element cannot exist and
    # any POM/inspect work is wasted. The engine stamps this verdict at
    # failure time (actions.nav_mismatch → statusDetails warnings), so this
    # is a pure pattern-match, checked FIRST.
    m = re.search(r"\[navigation-mismatch\] expected (\S+), current (\S+)",
                  text + "\n" + "\n".join(entry["warnings"]))
    if m:
        return {
            "category": "navigation-mismatch",
            "confidence": "high",
            "reason": f"The browser is on the wrong page — expected path "
                      f"'{m.group(1)}' but it is on '{m.group(2)}'; the target "
                      "element cannot exist here.",
            "fix": "Fix the app URL in the app's environments.yaml / the "
                   "navigation step (re-run author_test with the full URL and "
                   "overwrite=true) before touching POM entries.",
        }

    # NOOD_0167 — the app ANSWERED an earlier click with an announcement
    # (ARIA alert/status/live region or toast), stamped at failure time by
    # actions.page_response. Outranks every click/locator verdict below:
    # when the page itself said "Out of stock at <store>" or "Email is
    # required", advice to re-point locators or find another submit control
    # is wrong — the app refused the action for an app-state reason.
    m = re.search(r"\[page-response\] after clicking '([^']*)' the page "
                  r"announced: \"(.*?)\"",
                  text + "\n" + "\n".join(entry["warnings"]), re.DOTALL)
    if m:
        return {
            "category": "app-rejected-action",
            "confidence": "medium",
            "reason": f"After the click on '{m.group(1)}' the application "
                      f"announced: \"{m.group(2)}\" — the failing step is "
                      "downstream of that response; the app refused or "
                      "qualified the action, not a locator problem.",
            "fix": "Satisfy the precondition the announcement names in the "
                   "scenario's setup (test data, availability, account or "
                   "store state) — or assert the announcement itself if the "
                   "rejection is the expected behaviour. Locator/POM work "
                   "is wasted until the app accepts the action.",
        }

    # NOOD_0207 — the destination WAS reached and only the wording differs.
    # Ranked above every click verdict below: when the expected text's near
    # twin is on screen, click/POM advice is not merely unhelpful, it is
    # actively false ("the click left the page unchanged" about a page that
    # demonstrably re-rendered). Stamped at failure time by
    # actions._rendered_near_miss.
    m = _NEAR_MISS_RE.search(text)
    if m:
        return {
            "category": "assertion-wording",
            "confidence": "high",
            "reason": f"The page DOES render {m.group(2)} — the step reached "
                      f"its destination and the assertion is worded "
                      f"differently from what the app shows "
                      f"(expected '{m.group(1)}').",
            "fix": "Change the expected text to the string the page renders "
                   "(re-author with overwrite=true). Locator/POM/navigation "
                   "work is wasted — that part of the flow succeeded.",
        }

    # NOOD_0207 — a `sees` failure downstream of a click the engine resolved
    # to a control the probe marked `unique: False`: the run acted on
    # instance 1 of a repeated control, not the item the assertion names.
    # Above the click verdicts for the same reason — the submit control they
    # blame is fine; the earlier ambiguous click is the cause.
    # (Keyed off the warning locator._on_ambiguous already emits in lenient
    # mode — no new stamp needed; only the ranking was wrong.)
    m = re.search(r"Ambiguous locator '([^']*)' — matched multiple elements",
                  "\n".join(entry["warnings"]
                            + entry.get("scenario_warnings", []))) \
        if re.search(r"Expected (?:to see|NOT to see) '", text) else None
    if m:
        return {
            "category": "ambiguous-item-click",
            "confidence": "high",
            "reason": f"An earlier step resolved '{m.group(1)}' to several "
                      "elements and acted on the first — not necessarily the "
                      "item this step asserts.",
            "fix": 'Scope the action to one instance: within: "<text unique '
                   'to the intended row/card>" in the goal (or pin the POM '
                   "entry to that instance).",
        }

    # NOOD_0145 — a submit-like click that produced no page change beats the
    # locator/regression verdicts below: the destination the failing step
    # expects was never reached, so the "missing" element/text structurally
    # cannot appear and POM work on it is wasted. The engine stamps this at
    # failure time (actions.stuck_click → statusDetails warnings); the broad
    # "app-regression" this used to fall into sent authors debugging the app
    # instead of the wrong click target.
    m = re.search(r"\[no-navigation\] clicking '([^']*)'",
                  text + "\n" + "\n".join(entry["warnings"]))
    if m:
        return {
            "category": "wrong-action-target",
            "confidence": "high",
            "reason": f"The submit/navigation click on '{m.group(1)}' left the "
                      "page unchanged — the expected destination was never "
                      "reached, so the element/text this step looks for cannot "
                      "exist yet.",
            "fix": "Point the click at the control that actually submits (the "
                   "probe marks it '(submit)'); locator/POM fixes on the "
                   "destination page are wasted until the navigation happens.",
        }

    # NOOD_0141 — a click earlier in THIS scenario changed nothing (no
    # navigation, DOM mutation, or network request): the failing step is
    # downstream collateral of a click that never advanced the page — the
    # click's locator points at a decorative/no-op element (the healed
    # typeahead-icon case). Checked before the visibility/locator rules so
    # the root cause outranks its symptoms.
    all_warnings = entry["warnings"] + entry.get("scenario_warnings", [])
    if any("had no observable effect" in w for w in all_warnings):
        m = re.search(r"Click on '([^']+)' had no observable effect",
                      "\n".join(all_warnings))
        who = f" ('{m.group(1)}')" if m else ""
        return {
            "category": "locator-rot",
            "confidence": "medium",
            "reason": f"An earlier click{who} had no observable effect — no "
                      "navigation, DOM change, or network request — so this "
                      "step failed on a page that click never advanced; its "
                      "locator likely points at a decorative element.",
            "fix": "Re-point that click's POM entry/locator at the element "
                   "that actually navigates (for typeaheads: probe --suggest, "
                   "or the composite `selects the \"...\" suggestion` step).",
        }

    # NOOD_0144 — another element COVERS the target: Playwright names the
    # blocker in its "<sel> intercepts pointer events" error. Must outrank the
    # NOOD_0123 hidden/duplicate rule below — the same call log also carries
    # "waiting for element to be visible", and classifying an intercepting
    # modal as a hidden twin sent a reviewed session chasing locators while
    # the real fix was dismissing the dialog the flow itself opened.
    if "intercepts pointer events" in text:
        m = re.search(r"(<[^<>\n]{1,60}>)[^<\n]*intercepts pointer events",
                      text)
        who = f" ({m.group(1)})" if m else ""
        return {
            "category": "blocked-by-overlay",
            "confidence": "high",
            "reason": "The target resolved and is visible, but another "
                      f"element{who} sits on top and swallowed the click — "
                      "an open modal/overlay/toast, not a locator problem.",
            "fix": "Handle the covering element first: author the step the "
                   "flow itself implies (close/confirm the dialog that just "
                   "opened), or set NOODLE_AUTO_DISMISS=true for generic "
                   "popups. Do NOT re-guess this step's locator.",
        }

    # NOOD_0123 — a Playwright action timed out because its target resolved but
    # is hidden (a responsive/duplicate twin, or a box a trigger must open first).
    # Checked before the traceback rule below: the message often arrives wrapped
    # in a TimeoutError traceback, which would otherwise be misread as a
    # framework bug. Distinct from "element missing": the locator matched, the
    # element just isn't visible, so the fix is a visibility/trigger one.
    if re.search(r"element is not visible|waiting for.*to be visible", text, re.I):
        return {
            "category": "locator-rot",
            "confidence": "high",
            "reason": "The locator matched an element that is present but hidden "
                      "(a responsive/duplicate twin, or a box a trigger must open "
                      "first) — the action waited for it to become visible and "
                      "timed out.",
            "fix": "Target the visible candidate, run `noodle inspect` to see "
                   "which duplicate is hidden, or — when the step promises trigger "
                   "handling (e.g. 'User searches for') — fix the composite action "
                   "rather than adding a manual trigger step.",
        }

    if "Traceback (most recent call last)" in text:
        m = _EXC_RE.search(text)
        exc = m.group(1) if m else "exception"
        if exc != "AssertionError":
            return {
                "category": "test-script",
                "confidence": "high",
                "reason": f"Unhandled {exc} inside Noodle's own code, not a "
                          "real assertion — a framework/step-definition bug is "
                          "masking the actual failure.",
                "fix": "Fix or guard the raised exception's call site; re-run once "
                       "fixed to see the real underlying failure, if any.",
            }

    if re.search(r"daily request limit|rate limit|too many requests|\b429\b", text, re.I):
        return {
            "category": "environment-flap",
            "confidence": "high",
            "reason": "A third-party API's rate limit/quota was exhausted.",
            "fix": "Self-host a mock for CI, get an API key, or reduce call volume.",
        }

    if _has_warning(entry, r"vision-locate failed") or "Multimodal data provided" in text:
        return {
            "category": "config-gap",
            "confidence": "high",
            "reason": "NOODLE_MODEL is not vision-capable — the vision fallback "
                      "(locator or RCA) errored and silently degraded.",
            "fix": "Configure a vision-capable NOODLE_MODEL, e.g. "
                   "ollama/qwen2.5vl:7b, anthropic/claude-haiku-4-5, or openai/gpt-4o.",
        }

    if _has_warning(entry, r"Ambiguous locator"):
        return {
            "category": "locator-rot",
            "confidence": "medium",
            "reason": "Accessibility matched more than one element and no POM "
                      "entry disambiguated it — lenient mode used the first "
                      "(possibly wrong) match.",
            # NOOD_0228 — this used to read "add a scoped pom.yaml entry",
            # which is a hand-edit prescription with no command behind it. The
            # run's own warning prints the ambiguity id and candidate ids.
            "fix": "noodle pom resolve <id> --choose <C#>  (the id and the "
                   "candidates are in the run's ambiguity warning; "
                   "`noodle pom list` reprints them)",
        }

    if _has_warning(entry, r"Healed: matched .* via partial text"):
        return {
            "category": "locator-rot",
            "confidence": "medium",
            "reason": "Self-heal partial-text match likely grabbed the wrong "
                      "element (or the resulting text didn't match expectations).",
            "fix": "Add an explicit POM entry instead of relying on partial-text "
                   "self-heal for this locator.",
        }

    if re.search(r"has no browser, but this step needs one", text):
        return {
            "category": "test-script",
            "confidence": "high",
            "reason": "This step's action type isn't allow-listed for browserless "
                      "(@api/@appium) scenarios.",
            "fix": "If the step genuinely needs a browser, remove the tag. If it's "
                   "pure I/O (like this one), add its action type to "
                   "runner.py's _BROWSERLESS_TYPES.",
        }

    # NOOD_0126 — the POM key EXISTS but its file is scoped out on this URL (a
    # per-page <stem>_pom.yaml whose match: doesn't fit the live URL).
    # pom.explain_miss already spells this out in the miss message; surface it
    # as its own verdict so compact RCA names the missing `match:` scope — "add
    # a POM entry" is the wrong advice, the entry is there, it just doesn't apply.
    # Checked before the generic "Could not find" below (which the same message
    # also trips).
    if re.search(r"IS defined in .* but only in", text, re.I) or \
            _has_warning(entry, r"IS defined in .* but only in"):
        return {
            "category": "locator-rot",
            "confidence": "high",
            "reason": "The POM key exists but its file is scoped out on this URL "
                      "— a per-page <stem>_pom.yaml with no `match:` only applies "
                      "to URLs containing its filename stem, so it silently never "
                      "activates here.",
            "fix": "Add `match: {}` to that POM file (applies on every page), or a "
                   "`match: {url_contains: ...}` fragment of the real URL.",
        }

    if re.search(r"Could not find (element|dropdown|checkbox)", text):
        return {
            "category": "locator-rot",
            "confidence": "medium",
            "reason": "No matching element via accessibility, POM, or self-heal.",
            "fix": "noodle inspect <url> \"<phrase>\" to see what the page "
                   "offers, then noodle pom set \"<phrase>\" --css \"<sel>\" "
                   "to pin it (NOOD_0228 — the engine writes the file).",
        }

    if re.search(r"Expected status \d+, got \d+", text):
        code = re.search(r"got (\d+)", text)
        code = int(code.group(1)) if code else 0
        if code >= 500 or code == 0:
            return {
                "category": "environment-flap",
                "confidence": "medium",
                "reason": f"API returned an unexpected status ({code}) suggesting "
                          "a server/infra problem rather than a bad request.",
                "fix": "Check the API's health/logs; retry once it recovers.",
            }
        return {
            "category": "test-data",
            "confidence": "medium",
            "reason": f"API returned status {code} instead of the expected one — "
                      "likely wrong/stale request data or an API contract change.",
            "fix": "Check the request payload/fixture and the API's current contract.",
        }

    # NOOD_0022 — three failure shapes that used to fall through to the coarse
    # catch-all below, each now with the specific mechanism + fix spelled out.
    url_m = re.search(r"^(?:Actual )?URL: (\S+)$", text, re.MULTILINE)
    page_url = url_m.group(1) if url_m else ""

    if re.search(r"Expected to see .* not found", text) and page_url.endswith("?"):
        return {
            "category": "test-script",
            "confidence": "medium",
            "reason": "The failure URL ends in a bare '?' — the signature of an "
                      "implicit GET form submit. A preceding step (typically "
                      "pressing Enter in a form's lone input) submitted the form "
                      "and reloaded the page before this assertion could see the "
                      "expected text.",
            "fix": "Assert the real post-submit behaviour (the URL change) or use "
                   "a non-submitting interaction — the expected text structurally "
                   "cannot survive the reload on this markup.",
        }

    if re.search(r"Expected URL to (contain|end with|be)", text):
        return {
            "category": "app-regression",
            "confidence": "medium",
            "reason": "The page never reached the expected URL within the timeout "
                      "— the navigation/redirect didn't fire or went elsewhere. "
                      "If the Actual URL is still the pre-action page, the "
                      "preceding action didn't trigger navigation at all.",
            "fix": "Compare the Actual URL in the message against the app's "
                   "current redirect behaviour; if the action should navigate, "
                   "investigate why it no longer does.",
        }

    if re.search(r"Timed out waiting for", text):
        return {
            "category": "app-regression",
            "confidence": "low",
            "reason": "An explicit wait expired — the element/text never reached "
                      "the awaited state within the window. Either the app is "
                      "slower than the timeout or the condition is genuinely "
                      "never met anymore.",
            "fix": "If it's latency, raise the per-step timeout ('for up to N "
                   "seconds') or NOODLE_TIMEOUT; if the state never occurs, "
                   "treat it as a regression.",
        }

    if re.search(r"'(undefined|null|NaN)'", text):
        return {
            "category": "app-regression",
            "confidence": "medium",
            "reason": "The literal string 'undefined'/'null'/'NaN' is visible on "
                      "the page — a strong signal of an unguarded JS value (a "
                      "missing field, a failed fetch, a thrown-then-swallowed "
                      "exception) rendered straight into the DOM instead of "
                      "being handled.",
            "fix": "An app-side bug, not a test one: the field that rendered "
                   "the value needs a null/undefined guard (or a clean error "
                   "state) before it is assigned. Report it to whoever owns "
                   "the app — do not re-author the test around it.",
        }

    if re.search(r"Comparison failed|should (contain|equal)|^Expected |does not (contain|equal)",
                 text, re.MULTILINE):
        return {
            "category": "app-regression",
            "confidence": "low",
            "reason": "Actual text/value/state differs from expected — either the "
                      "app/site changed, the expected value is stale, or the page "
                      "hadn't finished updating when the assertion ran.",
            "fix": "Re-verify the expected value against the current app; add an "
                   "explicit wait if it's a timing issue, else investigate the "
                   "regression.",
        }

    return {
        "category": "unknown",
        "confidence": "low",
        "reason": "No heuristic rule matched this failure.",
        "fix": "Inspect the trace/screenshot manually, or configure a "
               "vision-capable NOODLE_MODEL + NOODLE_RCA=true for an AI verdict.",
    }


def _provenance_md(e: dict) -> str:
    """NOOD_0089 — 'app — features/x.feature' cell; empty for pre-0089 results."""
    app, f = e.get("app", ""), e.get("feature_file", "")
    return f"{app} — `{f}`" if app and f else (app or (f"`{f}`" if f else ""))


def _provenance_html(e: dict) -> str:
    app, f = e.get("app", ""), e.get("feature_file", "")
    parts = []
    if app:
        parts.append(f"<strong>{html.escape(app)}</strong>")
    if f:
        parts.append(f'<span class="step">{html.escape(f)}</span>')
    return "<br>".join(parts)


def render_compact(results_dir: str = None) -> str:
    """NOOD_0117 cheap-evidence-first: verdict + failing step + suggested fix
    per failure — nothing else, size-bounded. The read an agent does BEFORE
    reaching for the screenshot (vision tokens) or the network capture.
    NOOD_0156 — passed-step healing rides along: a green step that resolved
    via dom-scan/partial-text/vision is exactly the signal that turns a false
    pass into a caught one, so the compact read includes it too."""
    entries = collect(results_dir)
    healed = collect_healing(results_dir)
    lines = []
    for e in entries:
        h = e["heuristic"]
        fix = e["ai_fix"] or h["fix"]
        cat = e["ai_category"] or h["category"]
        conf = e["ai_confidence"] or h["confidence"]
        reason = e["ai_reason"] or h["reason"]
        lines.append(f"[{cat}/{conf}] {e['scenario']} — failing step: {e['step']}")
        lines.append(f"  why: {' '.join(reason.split())[:300]}")
        lines.append(f"  fix: {' '.join(fix.split())[:300]}")
        # NOOD_0222 — quote what the failed page rendered (hooks captures it
        # as a [page-text] warning): the cheap read answers "what state was
        # the app actually in?" without the screenshot or OCR.
        shown = next((w for w in e.get("warnings") or []
                      if str(w).startswith("[page-text]")), None)
        if shown:
            lines.append(f"  {shown}")
        if e.get("goal_node_hint"):
            lines.append(f"  goal: {e['goal_node_hint']}")
    for h in healed:
        detail = f" ({h['detail']})" if h.get("detail") else ""
        lines.append(f"[passed-with-healing] {h['scenario']} — step: {h['step']}"
                     f" — '{h.get('locator', '')}' via {h.get('strategy', '')}"
                     f"{detail}")
    if not lines:
        return "All green — no failures to explain."
    return "\n".join(lines)


def _evidence_md(results_dir: str = None) -> list[str]:
    """NOOD_0153 — markdown Evidence section: file paths ONLY, no image data.
    Agents read rca.md — inlining pixels there would burn tokens for nothing;
    a reader that wants the picture opens the path (or rca.html)."""
    shots = collect_evidence(results_dir)
    if not shots:
        return []
    d = Path(results_dir or _paths.results_dir())
    lines = ["", f"## Evidence screenshots ({len(shots)})", "",
             f"Step-level screenshots from this run (files under `{d}/`): "
             "green-boxed viewport evidence on passed steps, plus failure "
             "screenshots. Paths only — open rca.html for thumbnails.", "",
             "| App / .feature | Scenario | Step | Status | File |",
             "|---|---|---|---|---|"]
    for s in shots:
        row = [_provenance_md(s), s["scenario"], s["step"],
               f"{s['status']} ({s['kind']})", f"`{s['source']}`"]
        lines.append("| " + " | ".join(
            c.replace("\n", " ").replace("|", "\\|") for c in row) + " |")
    return lines


def render_markdown(results_dir: str = None) -> str:
    entries = collect(results_dir)
    warned = collect_warnings(results_dir)
    flaky = collect_flaky(results_dir)
    prov = _provenance(results_dir)
    lines = ["# RCA Report", "", f"_{_provenance_line(prov)}_", ""]
    if prov["total"] == 0:
        # NOOD_0187 — an empty run must never render the green tick: that is
        # exactly the page a typo'd --tag used to serve.
        lines.append(f"⚠️ {_EMPTY_RUN_NOTE}")
        return "\n".join(lines + _cost_footer(results_dir))
    if not entries and not warned:
        lines.append("No failed or errored scenarios in the last run. ✅")
        return "\n".join(lines + _flaky_md(flaky) + _evidence_md(results_dir)
                         + _cost_footer(results_dir))

    if entries:
        lines.append(f"{len(entries)} failed/errored scenario(s).\n")
        lines.append("| App / .feature | Feature | Scenario | Step | Heuristic verdict | Agentic (AI) verdict | Recommendation |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in entries:
            h = e["heuristic"]
            heur_cell = (f"**{h['category']}** ({h['confidence']}): {h['reason']} "
                         f"_[{_history_note(e)}]_")
            if e["ai_category"]:
                ai_cell = f"**{e['ai_category']}** ({e['ai_confidence']}): {e['ai_reason']}"
                fix = e["ai_fix"] or h["fix"]
            else:
                ai_cell = "_(no vision-capable NOODLE_MODEL configured)_"
                fix = h["fix"]
            row = [_provenance_md(e), e["feature"], e["scenario"], e["step"], heur_cell, ai_cell, fix]
            lines.append("| " + " | ".join(c.replace("\n", " ").replace("|", "\\|") for c in row) + " |")
    else:
        lines.append("No failed or errored scenarios in the last run. ✅\n")

    if warned:
        lines.append("")
        lines.append(f"## Passed with warnings ({len(warned)})")
        lines.append("")
        lines.append("These scenarios **passed**, but a step logged a ⚠️ warning "
                      "(ambiguous locator, self-heal match, ...). Lenient mode "
                      "never fails the build on these — worth a look before they "
                      "become a real failure.")
        lines.append("")
        lines.append("| App / .feature | Feature | Scenario | Step | Warning |")
        lines.append("|---|---|---|---|---|")
        for w in warned:
            row = [_provenance_md(w), w["feature"], w["scenario"], w["step"], w["warning"]]
            cells = [re.sub(r"\s+", " ", c).strip().replace("|", "\\|") for c in row]
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines + _flaky_md(flaky) + _traces_md(entries)
                     + _evidence_md(results_dir) + _cost_footer(results_dir))


def _flaky_md(flaky: list[dict]) -> list[str]:
    if not flaky:
        return []
    out = ["", f"## Passed after retry ({len(flaky)})", "",
           "These scenarios **failed at least once** and only went green on an "
           "auto-retry. A pass that needs a retry is a flake to fix, not a pass "
           "to trust.", "",
           "| App / .feature | Feature | Scenario | Attempts |", "|---|---|---|---|"]
    for fk in flaky:
        row = [_provenance_md(fk), fk["feature"], fk["scenario"], str(fk["attempts"])]
        out.append("| " + " | ".join(c.replace("\n", " ").replace("|", "\\|")
                                     for c in row) + " |")
    return out


def _traces_md(entries: list[dict]) -> list[str]:
    """NOOD_0183 — every failed web scenario already saves a full Playwright
    trace (DOM snapshots + network + sources, the headline debugging edge over
    Selenium). Until now the only pointer to it was one `logger.info` line in
    the live console — which `--quiet`, the default for agents and CI, throws
    away. So the single most useful artifact a failure produces was being
    written and never mentioned again. Name it where failures are actually
    read."""
    from noodle.reporting import paths as _paths
    found = []
    for e in entries:
        p = _paths.traces_dir() / f"{e['scenario'].replace(' ', '_').replace('/', '_')[:80]}.zip"
        if p.is_file():
            found.append((e["scenario"], p))
    if not found:
        return []
    out = ["", f"## Playwright traces ({len(found)})", "",
           "Step-by-step replay of the failure — DOM snapshot, network and "
           "console at every action. Open one with:", ""]
    for scenario, p in found:
        out += [f"- **{scenario}** — `playwright show-trace {p}`"]
    return out


def _cost_footer(results_dir: str = None) -> list[str]:
    """NOOD_0080 — what this run's own LLM calls cost (llm_cost*.json is
    written by hooks.after_all). Empty when the run made no model calls."""
    from noodle.llm import cost as _cost
    total = _cost.load_total(results_dir or str(_paths.results_dir()))
    return ["", f"_{_cost.format_line(total)}_"] if total else []


_CONFIDENCE_COLOR = {"high": "#1a7f37", "medium": "#9a6700", "low": "#57606a"}

_HTML_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 1100px; color: #1f2328; background: #fff; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; }
p.count { color: #57606a; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; font-size: 0.9rem; }
th, td { border: 1px solid #d0d7de; padding: 0.5rem 0.7rem; text-align: left; vertical-align: top; }
th { background: #f6f8fa; }
tr:nth-child(even) td { background: #fafbfc; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
         font-size: 0.78rem; font-weight: 600; color: #fff; margin-right: 0.4rem; }
.step { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
.muted { color: #8b949e; font-style: italic; }
.ok { color: #1a7f37; font-weight: 600; }
.evidence { display: flex; flex-wrap: wrap; gap: 1rem; }
.evidence figure { margin: 0; max-width: 320px; border: 1px solid #d0d7de;
                   border-radius: 6px; padding: 0.5rem; background: #f6f8fa; }
.evidence img { max-width: 100%; height: auto; border-radius: 4px; }
.evidence figcaption { font-size: 0.78rem; color: #57606a; margin-top: 0.4rem;
                       word-break: break-word; }
"""


def _badge(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{html.escape(text)}</span>'


# NOOD_0153 — bounds that keep rca.html a single self-contained file of sane
# size: thumbnails are downscaled + recompressed before base64-inlining, and
# the gallery is capped (the markdown section still lists every file path).
_EVIDENCE_THUMB_PX = 480
_EVIDENCE_HTML_CAP = 48


def _thumb_data_uri(path: str) -> str | None:
    """Downscaled JPEG thumbnail of an image file as a data: URI, or None
    when the file is missing/unreadable (the caption still renders)."""
    import base64
    import io
    try:
        from PIL import Image
        img = Image.open(path)
        img.thumbnail((_EVIDENCE_THUMB_PX, _EVIDENCE_THUMB_PX))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _evidence_html(results_dir: str = None) -> list[str]:
    """NOOD_0153 — thumbnail gallery of every step screenshot in the run.
    Inlined as data: URIs so rca.html keeps its documented property of being
    fully self-contained (file:// works; `report serve` hosts only reports/,
    so a relative link into allure-results/ would 404)."""
    shots = collect_evidence(results_dir)
    if not shots:
        return []
    body = [f"<h2>Evidence screenshots ({len(shots)})</h2>",
            "<p>Step-level proof from this run: passed steps show the "
            "viewport with the asserted element boxed in green; failed steps "
            "show their failure screenshot. Full-size files live in "
            "the run's <code>allure-results/</code> and <code>screenshots/</code> "
            "folders.</p>",
            '<div class="evidence">']
    for i, s in enumerate(shots):
        if i >= _EVIDENCE_HTML_CAP:
            body.append(f'</div><p class="muted">…{len(shots) - i} more not '
                        "inlined — see the table in rca.md for every file.</p>")
            return body
        color = "#1a7f37" if s["status"] == "passed" else "#cf222e"
        caption = (_badge(f"{s['status']} · {s['kind']}", color)
                   + f"<br>{html.escape(s['scenario'])}<br>"
                   f'<span class="step">{html.escape(s["step"])}</span>')
        uri = _thumb_data_uri(s["path"])
        img_tag = (f'<img src="{uri}" alt="{html.escape(s["step"])}" '
                   'loading="lazy">' if uri else
                   f'<span class="muted">image missing: {html.escape(s["source"])}</span>')
        body.append(f"<figure>{img_tag}<figcaption>{caption}</figcaption></figure>")
    body.append("</div>")
    return body


def render_html(results_dir: str = None) -> str:
    """Same data as render_markdown, laid out as a styled HTML table instead
    of a raw markdown dump — meant to be served/opened directly in a browser
    (see noodle/repl/repl.py:_serve_rca), not just read as source text."""
    entries = collect(results_dir)
    warned = collect_warnings(results_dir)
    flaky = collect_flaky(results_dir)
    prov = _provenance(results_dir)
    body = ["<h1>RCA Report</h1>",
            f'<p class="count">{html.escape(_provenance_line(prov))}</p>']

    if prov["total"] == 0:
        # NOOD_0187 — same honesty as the markdown: never a green tick for a
        # run that executed nothing.
        body.append(f"<p>⚠️ {html.escape(_EMPTY_RUN_NOTE)}</p>")
        return f"<!doctype html><meta charset=utf-8><title>RCA Report</title><style>{_HTML_STYLE}</style>{''.join(body)}"

    if not entries and not warned:
        body.append('<p class="ok">No failed or errored scenarios in the last run. ✅</p>')
        body.extend(_flaky_html(flaky))
        body.extend(_evidence_html(results_dir))
        return f"<!doctype html><meta charset=utf-8><title>RCA Report</title><style>{_HTML_STYLE}</style>{''.join(body)}"

    if entries:
        body.append(f'<p class="count">{len(entries)} failed/errored scenario(s).</p>')
        body.append("<table><tr><th>App / .feature</th><th>Feature</th><th>Scenario</th><th>Step</th>"
                     "<th>Heuristic verdict</th><th>Agentic (AI) verdict</th><th>Recommendation</th></tr>")
        for e in entries:
            h = e["heuristic"]
            heur_cell = (_badge(h["category"], _CONFIDENCE_COLOR.get(h["confidence"], "#57606a"))
                          + html.escape(h["reason"])
                          + f' <span class="muted">{html.escape(_history_note(e))}</span>')
            if e["ai_category"]:
                ai_cell = _badge(e["ai_category"], _CONFIDENCE_COLOR.get(e["ai_confidence"], "#57606a")) + html.escape(e["ai_reason"])
                fix = e["ai_fix"] or h["fix"]
            else:
                ai_cell = '<span class="muted">no vision-capable NOODLE_MODEL configured</span>'
                fix = h["fix"]
            body.append(
                "<tr>"
                f"<td>{_provenance_html(e)}</td>"
                f"<td>{html.escape(e['feature'])}</td>"
                f"<td>{html.escape(e['scenario'])}</td>"
                f"<td class=\"step\">{html.escape(e['step'])}</td>"
                f"<td>{heur_cell}</td>"
                f"<td>{ai_cell}</td>"
                f"<td>{html.escape(fix)}</td>"
                "</tr>"
            )
        body.append("</table>")
    else:
        body.append('<p class="ok">No failed or errored scenarios in the last run. ✅</p>')

    if warned:
        body.append(f"<h2>Passed with warnings ({len(warned)})</h2>")
        body.append("<p>These scenarios <strong>passed</strong>, but a step logged a ⚠️ warning "
                     "(ambiguous locator, self-heal match, ...). Lenient mode never fails the "
                     "build on these — worth a look before they become a real failure.</p>")
        body.append("<table><tr><th>App / .feature</th><th>Feature</th><th>Scenario</th><th>Step</th><th>Warning</th></tr>")
        for w in warned:
            warning = re.sub(r"\s+", " ", w["warning"]).strip()
            body.append(
                "<tr>"
                f"<td>{_provenance_html(w)}</td>"
                f"<td>{html.escape(w['feature'])}</td>"
                f"<td>{html.escape(w['scenario'])}</td>"
                f"<td class=\"step\">{html.escape(w['step'])}</td>"
                f"<td>{html.escape(warning)}</td>"
                "</tr>"
            )
        body.append("</table>")

    body.extend(_flaky_html(flaky))
    body.extend(_evidence_html(results_dir))
    return f"<!doctype html><meta charset=utf-8><title>RCA Report</title><style>{_HTML_STYLE}</style>{''.join(body)}"


def _flaky_html(flaky: list[dict]) -> list[str]:
    if not flaky:
        return []
    body = [f"<h2>Passed after retry ({len(flaky)})</h2>",
            "<p>These scenarios <strong>failed at least once</strong> and only "
            "went green on an auto-retry. A pass that needs a retry is a flake "
            "to fix, not a pass to trust.</p>",
            "<table><tr><th>App / .feature</th><th>Feature</th>"
            "<th>Scenario</th><th>Attempts</th></tr>"]
    for fk in flaky:
        body.append(
            "<tr>"
            f"<td>{_provenance_html(fk)}</td>"
            f"<td>{html.escape(fk['feature'])}</td>"
            f"<td>{html.escape(fk['scenario'])}</td>"
            f"<td>{fk['attempts']}</td>"
            "</tr>")
    body.append("</table>")
    return body


def write_reports(results_dir: str = None, out_dir: str = None) -> dict:
    """NOOD_0082 — every run ships BOTH RCA renderings (rca.md + rca.html)
    alongside the Allure report, pass or fail: a passing run renders the
    "no failures ✅" page, so `noodle report serve` always has an rca.html
    to host without a separate `noodle rca-report` invocation. Heuristic
    only — free, no LLM call. Returns the written paths."""
    out = Path(out_dir) if out_dir else _paths.reports_dir()
    out.mkdir(parents=True, exist_ok=True)
    md, html_ = out / "rca.md", out / "rca.html"
    md.write_text(render_markdown(results_dir), encoding="utf-8")
    html_.write_text(render_html(results_dir), encoding="utf-8")
    return {"rca_md": str(md), "rca_html": str(html_)}


def open_html(results_dir: str = None, out_path: str = None) -> str:
    """Render rca.html and open it directly in the browser. Unlike the Allure
    report, this page is fully self-contained (inline CSS, no fetch/XHR), so
    a plain file:// URL works fine — no HTTP server needed. Returns the path
    written, for the caller to report back to the user."""
    import webbrowser

    from . import paths as _paths
    out_path = out_path or str(_paths.reports_dir() / "rca.html")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(render_html(results_dir), encoding="utf-8")
    webbrowser.open(Path(out_path).resolve().as_uri())
    return out_path


def render_markdown_llm(results_dir: str = None) -> str:
    """Opt-in prose narrative on top of the same structured data — text-only,
    no image needed, so it works with any configured NOODLE_MODEL (see
    reporting/summary.py:summarize_llm for the same pattern)."""
    from noodle.llm.client import ask
    entries = collect(results_dir)
    if not entries:
        return "No failed or errored scenarios in the last run."
    payload = [{k: v for k, v in e.items() if k not in ("trace",)} for e in entries]
    narrative = ask(
        "You are triaging a BDD test run for a developer. Each entry below is one "
        "failed scenario with a rule-based ('heuristic') root-cause guess already "
        "attached, and an AI vision verdict when one was available. Write a short "
        "prose summary (bullet list, one line per DISTINCT root cause — group "
        "scenarios that share a cause) a developer can act on. Be specific about "
        "which file/step to fix. Do not repeat the raw JSON back.\n\n"
        f"{json.dumps(payload, indent=2)}"
    ).strip()
    return "# RCA Report (AI narrative)\n\n" + narrative + "\n\n---\n\n" + render_markdown(results_dir)


def _find_feature_file(tests_root: Path, scenario: str) -> Path | None:
    """The result JSON carries the scenario name but not its source path —
    substring-scan the workspace's .feature files for it."""
    if not scenario or not tests_root.is_dir():
        return None
    for f in sorted(tests_root.rglob("*.feature")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"Scenario: {scenario}" in text or f"Scenario Outline: {scenario}" in text:
            return f
    return None


def propose_fixes(results_dir: str = None, workspace: str = ".",
                   tests_dir: str = "tests") -> str:
    """NOOD_0030 (§4.4) — opt-in fix proposals via `rca-report --propose-fix`.
    For each failed scenario: feed classify()'s verdict + the feature file
    (+ its package's POM files) to the text-only ask(), request a unified
    diff. Never writes anything — output is for human review."""
    from noodle.llm.client import ask
    entries = collect(results_dir)
    if not entries:
        return "No failed or errored scenarios in the last run."
    tests_root = Path(workspace) / tests_dir
    out = ["# RCA fix proposals",
           "",
           "Generated by the configured NOODLE_MODEL. Review before applying — "
           "nothing on disk has been changed.",
           ""]
    for e in entries:
        h = e["heuristic"]
        feat = _find_feature_file(tests_root, e["scenario"])
        if feat is None:
            out.append(f"## {e['scenario']}\n\n_(no .feature file under "
                        f"{tests_root} contains this scenario — skipped)_\n")
            continue
        poms = sorted((feat.parent.parent / "resources" / "pageobjects").glob("*.yaml"))
        pom_blocks = "\n".join(f'--- {p.name} ---\n{p.read_text(encoding="utf-8")}' for p in poms)
        diff = ask(
            "A BDD scenario failed and was root-caused as "
            f"category '{h['category']}': {h['reason']}\n"
            f"Suggested direction: {h['fix']}\n"
            f"Failing step: {e['step']}\nError message: {e['message']}\n\n"
            f'Feature file ({feat.name}):\n{feat.read_text(encoding="utf-8")}\n\n'
            f"Page-object YAML for this package:\n{pom_blocks or '(none)'}\n\n"
            "Propose the smallest edit that fixes this failure. Reply with ONLY "
            "a unified diff (--- / +++ / @@ hunks) against the file(s) above — "
            "no commentary, no markdown fence."
        ).strip()
        out.append(f"## {e['scenario']} — {h['category']}\n\n"
                    f"`{feat}`\n\n```diff\n{diff}\n```\n")
    return "\n".join(out)

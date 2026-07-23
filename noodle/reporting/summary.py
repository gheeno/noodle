"""Plain-English run summary from allure-results/*.json. No LLM needed.

Reads the same per-scenario JSON the Allure report is built from (see
reporting/writer.py) and prints a human glance: pass/fail counts, which
scenario failed at which step, total time.
"""
import json
from datetime import date
from pathlib import Path

from noodle.reporting import paths as _paths


def collect(results_dir: str = None) -> dict:
    """Aggregate result JSON into counts, failures, and total wall time."""
    from noodle import counters
    counters.bump("result_scan")
    results_dir = results_dir or str(_paths.results_dir())
    d = Path(results_dir)
    files = sorted(d.glob("*-result.json")) if d.is_dir() else []
    passed = failed = 0
    failures = []
    starts, stops = [], []
    # Auto-retry writes one result per ATTEMPT — de-duplicate by historyId
    # (fullName/filename fallback), keeping the last attempt, so counts match
    # Behave and Allure instead of double-counting retried failures.
    latest: dict = {}
    for f in files:
        try:
            r = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "start" in r:
            starts.append(r["start"])
        if "stop" in r:
            stops.append(r["stop"])
        key = r.get("historyId") or r.get("fullName") or f.name
        prev = latest.get(key)
        if prev is None or r.get("stop", 0) >= prev.get("stop", 0):
            latest[key] = r
    # NOOD_0156 — verification confidence: `passed` alone hid two healed
    # locators behind a green exit code (the NOOD_0156 false pass). The
    # payload now carries every warning/healing event/evidence record from the
    # step results, and `verified` — false whenever any step of a green run
    # resolved through a fuzzy tier (dom-scan/partial-text/vision/OCR), passed
    # only via lenient ambiguity, or shipped invalid evidence. Agents must
    # require BOTH failed == 0 AND verified == true before claiming the
    # requested behavior passed.
    from noodle import healing as _healing
    warnings_out, healing_events, evidence_out, reasons = [], [], [], []
    for r in latest.values():
        scenario = r.get("name", "")
        for s in r.get("steps", []):
            sd = s.get("statusDetails", {}) or {}
            for w in sd.get("warnings") or []:
                warnings_out.append({"scenario": scenario,
                                     "step": s.get("name", ""), "warning": w})
                if "Ambiguous locator" in str(w):
                    reasons.append(f"{scenario} — {s.get('name', '')}: passed "
                                   "via lenient ambiguous-locator .first")
            for h in sd.get("healing") or []:
                healing_events.append({"scenario": scenario,
                                       "step": s.get("name", ""), **h})
                if h.get("strategy") in _healing.FUZZY_STRATEGIES:
                    reasons.append(
                        f"{scenario} — {s.get('name', '')}: '{h.get('locator')}'"
                        f" healed via {h.get('strategy')}"
                        + (f" ({h.get('detail')})" if h.get("detail") else ""))
            ev = sd.get("evidence")
            if ev:
                evidence_out.append({"scenario": scenario,
                                     "step": s.get("name", ""), **ev})
                if ev.get("valid") is False:
                    reasons.append(f"{scenario} — {s.get('name', '')}: evidence "
                                   "shot has no fresh exact assertion match")
                # NOOD_0157 — the shot exists but the target element isn't in
                # the captured viewport (center-scroll failed): the image
                # can't prove the step, so the run isn't verified.
                elif ev.get("element_in_view") is False:
                    reasons.append(f"{scenario} — {s.get('name', '')}: evidence "
                                   "element is outside the captured viewport "
                                   "(center-scroll failed)")
        if r.get("status") == "passed":
            passed += 1
        elif r.get("status") == "failed":
            failed += 1
            step = next((s["name"] for s in r.get("steps", [])
                         if s.get("status") == "failed"), "")
            feature = next((lab["value"] for lab in r.get("labels", [])
                            if lab["name"] == "feature"), "")
            message = r.get("statusDetails", {}).get("message", "")
            failures.append({"feature": feature, "scenario": r.get("name", ""),
                             "step": step, "message": message})
    if failed:
        reasons.append(f"{failed} scenario(s) failed")
    secs = round((max(stops) - min(starts)) / 1000) if starts and stops else 0
    return {"passed": passed, "failed": failed, "failures": failures,
            "seconds": secs,
            "verified": not reasons, "unverified_reasons": reasons,
            "warnings": warnings_out, "healing_events": healing_events,
            "evidence": evidence_out}


def render(results_dir: str = None, report_dir: str = None,
           summary: dict = None) -> str:
    """summary (NOOD_0131): a dict collect() already produced — reuse it
    instead of re-scanning the result files."""
    report_dir = report_dir or str(_paths.reports_dir() / "allure-report")
    s = summary if summary is not None else collect(results_dir)
    lines = [f"Run summary — {date.today().isoformat()}",
             f"✅  {s['passed']} passed",
             f"❌  {s['failed']} failed"]
    for fl in s["failures"]:
        at = f"  failed at: {fl['step']}" if fl["step"] else ""
        lines.append(f"   • {fl['feature']} > {fl['scenario']}{at}")
    # NOOD_0156 — a green run that only passed via fuzzy healing/lenient
    # ambiguity is NOT verified; say so where the pass counts are read.
    if s.get("verified") is False and not s["failed"]:
        lines.append("⚠️  UNVERIFIED — passing steps leaned on fuzzy healing "
                     "or lenient ambiguity:")
        for reason in s.get("unverified_reasons", [])[:6]:
            lines.append(f"   • {reason}")
    lines.append(f"⏱️  Total: {s['seconds']}s")
    if Path(report_dir).exists():
        lines.append(f"\nAllure report → {report_dir}/index.html")
    return "\n".join(lines)


def summarize_llm(results_dir: str = None) -> str:
    """Opt-in richer narrative — hand the structured counts to a local/paid model."""
    from noodle.llm.client import ask
    s = collect(results_dir)
    return ask(
        "Summarise this test run for a developer in 3-4 sentences, calling out the "
        f"likely root cause of any failures:\n{json.dumps(s, indent=2)}"
    ).strip()

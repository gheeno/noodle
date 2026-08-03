"""Feature-generation regression benchmark (NOOD_0185).

Runs ONLY when a human asks for it ("run the feature regression"), and
NOOD_0190 makes `noodle feature-regression` actually run it: `execute()`
authors + runs both canonical prompts, one combined run serves the
reports, `score()` returns the verdict. Every measurement comes out of
the payload `author_test(run_after_author=True)` already hands back — an
agent never hand-copies JSON it was given, and never reports a number it
guessed. The prose (what "regressed" means, HIL review, bisecting a
regression to a commit) lives in docs/feature-regression.md.

NOOD_0190 dropped the host AIC/token accounting and the engine LLM
ledger: the first measured how lost the DRIVING AGENT got (and on a host
with no billing API could only be guessed), the second read "none" every
run because the engine takes the deterministic fast path. The benchmark
measures the engine. Generated line count is the size signal that moves.
"""
import json
import os
import time
from pathlib import Path

# The canonical "super easy" test cases, one per authoring mode. A live but
# automation-friendly site (Wikipedia) on purpose: typeahead suggestion,
# popup tolerance, cross-page navigation and plain-text assertions are the
# capabilities under test, and the benchmark must go green from any machine —
# retail sites bot-gate automated browsers (the original retail-store pair,
# kept in docs/feature-regression.md as a live drill, proved that). Site
# drift = update the content here, never the scoring.
# tc1/tc2 are numbered PROMPTS — the plain-English path a human/agent actually
# sends (the AC: the benchmark must mimic real usage, never hand-built
# specs). tc1's suggestion click is prompt vocabulary since NOOD_0185.
#
# NOOD_0191 — tc3 covers the other real shape: CROSS-WOK. "Fetch or seed over
# the API, then prove it rendered in the UI" is the most common mix in a real
# suite (most UI is an API with a face on it), and nothing guarded it end to
# end. It exercises the REST client, {env:} resolution across both step
# families in one scenario, and the compiler's cross-wok step ORDER (the REST
# preamble ahead of the navigation Given).
#
# NOOD_0192 — it is a PROMPT now. It was feature_content because the prompt
# compiler was web-only, so a cross-wok test genuinely had to be hand-authored
# Gherkin — which meant the benchmark's one cross-wok case measured no
# generation at all and reported LINES as "—". With the api verbs in the
# grammar this is the same flow through the door a user actually uses, so all
# three cases now measure what the engine generates. What was lost with the
# hand-authored version is covered where it belongs: `{var:}` chaining from a
# response field and the feature_content door are unit-tested
# (unit_tests/woks/api/), not benchmarked.
PROMPTS = [
    {"id": "tc1_search_suggestion", "mode": "prompt",
     "content": """1. Go to the URL https://en.wikipedia.org/wiki/Main_Page
2. Close any popup that may appear
3. Search for "Vacuum cleane"
4. Click the suggestion "Vacuum cleaner"
5. Verify "From Wikipedia, the free encyclopedia\""""},
    {"id": "tc2_account_textboxes", "mode": "prompt",
     "content": """1. Go to the URL https://en.wikipedia.org/wiki/Main_Page
2. Close any popup that may appear
3. Click "Create account"
4. Verify "Password"
5. Verify "Confirm password"
6. Verify "Email address\""""},
    {"id": "tc3_api_seeds_ui_verifies", "mode": "prompt",
     "content": """1. GET https://en.wikipedia.org/api/rest_v1/page/summary/Vacuum_cleaner
2. Verify the response status is 200
3. Go to the URL https://en.wikipedia.org/wiki/Vacuum_cleaner
4. Close any popup that may appear
5. Verify "From Wikipedia, the free encyclopedia\""""},
    # NOOD_0227 (SC-3) — the shape the 65.9-AIC blowout shipped on, absent
    # from this suite at the time: a FOUR-PAGE flow with a per-card
    # `within:`-scoped click, a same-URL DOM-mutation cart panel, a
    # three-field commit form, and end-state assertions (phrase + item +
    # amount). Hosted on the engine's own static fixture
    # (regression_fixture.py, served on an ephemeral localhost port by
    # execute()), so it is deterministic from any machine. {FIXTURE} is
    # substituted with the live base URL at execute() time.
    {"id": "tc4_multipage_checkout", "mode": "prompt",
     "content": """1. Go to the URL {FIXTURE}/index.html
2. Verify "Widget Depot" - take an evidence screenshot on this step
3. Click "Shop the catalogue"
4. Click "add to cart" in the card containing "Turbo Widget"
5. Click "proceed to checkout"
6. Enter "Pat Tester" in the "full name" field
7. Enter "12 Main St" in the "street address" field
8. Enter "K1A 0B1" in the "postal code" field
9. Click "place order"
10. Verify "Thanks for your order"
11. Verify "Turbo Widget"
12. Verify "$19.99\""""},
]

# Per-test-case ceilings — the definition of "not regressed": on time and
# accurate. Each is overridable with its env var when a deliberately slower
# machine or site is in play.
_DEFAULTS = {
    "max_elapsed_s": ("NOODLE_REG_MAX_ELAPSED_S", 120),
    "max_corrections": ("NOODLE_REG_MAX_CORRECTIONS", 2),
}


def budget() -> dict:
    return {k: float(os.getenv(env, d)) for k, (env, d) in _DEFAULTS.items()}


def _case(prompt: dict, workspace: str) -> dict:
    """Author + run ONE canonical prompt and measure it from the payload.

    Corrections are the ENGINE's own repair signals, never a self-report:
    a locator that needed self-healing, a scenario that needed a retry, and
    a re-author pass when `ready` came back false. One re-author is allowed
    (that is what a real user does); still not ready → the case stays red.
    """
    from noodle.repl import core
    t0 = time.monotonic()
    if prompt.get("mode") == "feature_content":
        kw = {k: prompt[k] for k in ("app_name", "base_url", "feature_path")}
        kw["feature_content"] = prompt["content"]
        if prompt.get("environment_values"):
            kw["environment_values"] = prompt["environment_values"]
        kw |= {"run_after_author": True, "workspace": workspace}
    else:
        kw = {"prompt": prompt["content"], "run_after_author": True,
              "workspace": workspace}
    res = core.author_test(**kw)
    reauthors = 0
    if not (res.get("author") or {}).get("ready"):
        reauthors = 1
        res = core.author_test(**kw, overwrite=True)
    elapsed = time.monotonic() - t0
    author, run = res.get("author") or {}, res.get("run") or {}
    compiled = author.get("compiled") or {}
    feature, pom = compiled.get("feature") or "", compiled.get("pom") or ""
    # LINES measures what the ENGINE generated. feature_content supplies its
    # own Gherkin, so nothing was generated and there is nothing to count —
    # None (rendered "—", skipped by the average), never 0. A zero here would
    # read as "the engine produced an empty test" and would drag the mean
    # down with a number that was never measured.
    lines = (len(feature.splitlines()) + len(pom.splitlines())
             if compiled else None)
    return {"id": prompt["id"], "elapsed_s": round(elapsed, 1),
            "run_s": run.get("seconds"),
            "corrections": reauthors + len(run.get("healing_events") or [])
                           + len(run.get("flaky") or []),
            "lines": lines,
            "green": bool(res.get("ok")) and not run.get("failed"),
            # `intent_verified` answers "did the COMPILED goal match probe
            # evidence" — it is `goal is not None and not blocking`, so it is
            # structurally False for feature_content and demanding it there
            # would score tc3 red forever. Hand-authored Gherkin states its
            # intent literally; there is nothing inferred to verify against,
            # so the run's own `verified` (no fuzzy healing behind the pass)
            # IS the whole bar. Not a weaker AC — the right one per mode.
            "verified": run.get("verified") is True
                        and (prompt.get("mode") == "feature_content"
                             or author.get("intent_verified") is True),
            "feature": author.get("feature"),
            # NOOD_0208 — the FIRST blocker, ahead of the generic skip reason.
            # A blocked author reports `run.skipped: "authoring not ready — no
            # run browser launched"`, which says nothing about why; the
            # actionable line ("run: playwright install chromium", or the
            # control that wouldn't resolve) sat unread in author.blocking.
            # A reader of this table saw REGRESSED and started bisecting the
            # engine — the same diagnose-without-repair defect NOOD_0207 fixed
            # everywhere else, still live in the tool that measures it.
            "error": (res.get("error") or next(iter(author.get("blocking")
                                                    or []), None)
                      or run.get("skipped"))}


def execute(workspace: str) -> dict:
    """Run the whole benchmark in this (freshly scaffolded) workspace:
    each canonical prompt authored + run and measured, then ONE combined run
    so both test cases land on the same served Allure + RCA report. Returns
    the results dict score() takes — nothing to fill in by hand.

    NOOD_0227 — a prompt carrying {FIXTURE} runs against the engine's own
    static multi-page shop (regression_fixture.py), served on an ephemeral
    localhost port for the benchmark's whole lifetime — including the
    combined run — and always torn down."""
    from noodle.repl import core
    fixture_srv = base = None
    try:
        if any("{FIXTURE}" in p["content"] for p in PROMPTS):
            from noodle import regression_fixture
            fixture_srv, base = regression_fixture.serve(workspace)
        prompts = [dict(p, content=p["content"].replace("{FIXTURE}", base))
                   if "{FIXTURE}" in p["content"] else p for p in PROMPTS]
        cases = [_case(p, workspace) for p in prompts]
        feats = [c.pop("feature") for c in cases]
        urls = []
        if paths := [f for f in feats if f]:
            combined = core.run_and_report(
                os.path.commonpath(paths) if len(paths) > 1 else paths[0],
                workspace=workspace, headless=True, retries=0,
                serve_reports=True)
            served = combined.get("served") or {}
            urls = served.get("urls") or []
            if served.get("port"):
                # The scorecard leads: the server hosts the whole reports
                # root, and the CLI drops verdict.html in there before
                # printing these.
                urls = [f"http://{served.get('host', '127.0.0.1')}:"
                        f"{served['port']}/verdict.html"] + urls
    finally:
        if fixture_srv is not None:
            fixture_srv.shutdown()
    return {"workspace": workspace, "report_urls": urls, "test_cases": cases}


def audit(results: dict, workspace: str) -> list[str]:
    """NOOD_0188 — cross-check the agent's self-reported pass claims against
    the artifacts the ENGINE wrote.

    `green` and `verified` are typed into results.json by the driving agent;
    nothing read the run's own `last_run.json`, so the headline verdict of the
    "is the product still good" benchmark was unfalsifiable self-report — the
    one place in the repo where a claim outranked evidence. Returns discrepancy
    lines (empty when the artifacts agree or aren't present to judge).

    Deliberately advisory about ABSENCE (a fresh workspace or a moved
    artifacts root is not a regression) but hard about CONTRADICTION: a tc
    claiming green while last_run.json recorded failures is a false report.
    """
    from noodle.reporting import paths as _paths
    out = []
    claims_green = [tc for tc in results.get("test_cases") or []
                    if tc.get("green") is True]
    if not claims_green:
        return out
    # The run's own pointer (.noodle/last_run_root) — never a search: an
    # rglob would happily grab an unrelated run's artifacts from an enclosing
    # tree and audit this benchmark against someone else's numbers.
    root = _paths.last_run_root(workspace)
    path = root / "last_run.json"
    if not path.is_file():
        path = Path(workspace) / root / "last_run.json"
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        out.append("unverifiable: no last_run.json found to cross-check the "
                   "green/verified claims against (reported numbers are "
                   "self-reported only)")
        return out
    if last.get("failed"):
        out.append(f"self-report contradicts the run: {len(claims_green)} test "
                   f"case(s) claim green but last_run.json records "
                   f"{last['failed']} failed scenario(s)")
    if last.get("verified") is False and any(
            tc.get("verified") is True for tc in claims_green):
        out.append("self-report contradicts the run: a test case claims "
                   "verified but last_run.json records verified: false "
                   f"({'; '.join(last.get('unverified_reasons') or [])[:200]})")
    if (last.get("passed") or 0) == 0:
        out.append("self-report contradicts the run: test cases claim green "
                   "but last_run.json records 0 passed scenarios")
    return out


def score(results: dict, workspace: str | None = None) -> dict:
    """Pure verdict over the measurements execute() produced. PASS = every
    canonical test case measured, green AND verified, within the per-case
    budget; anything else is REGRESSED, with one reason line per breach.

    NOOD_0188 — pass `workspace` (the CLI passes the results file's own
    directory) to also cross-check the green/verified claims against the
    run's `last_run.json`: a benchmark that takes its subject's word for
    the headline result isn't a measurement. Omitted → pure, no disk read."""
    b = budget()
    tcs, regressions = [], []
    for i, tc in enumerate(results.get("test_cases") or []):
        fails = []
        for field in ("elapsed_s", "corrections", "green", "verified"):
            if tc.get(field) is None:
                fails.append(f"missing measurement: {field}")
        # TEST DEVELOPMENT TIME — what the budget measures: how long the
        # agent+engine spent DEVELOPING the test (prompt → authored .feature),
        # i.e. total wall clock minus the generated test's own execution time.
        dev = (tc["elapsed_s"] - (tc.get("run_s") or 0)
               if tc.get("elapsed_s") is not None else None)
        if dev is not None and dev > b["max_elapsed_s"]:
            fails.append(f"slow development: {dev:.0f}s > "
                         f"{b['max_elapsed_s']:.0f}s (run time excluded)")
        if tc.get("corrections") is not None and tc["corrections"] > b["max_corrections"]:
            fails.append(f"inaccurate: {tc['corrections']} corrections > {b['max_corrections']:.0f}")
        if tc.get("green") is False:
            # NOOD_0208 — carry the cause, not just the verdict. "final run
            # not green" is a restatement of the column the reader is already
            # looking at; the engine knew why and this dropped it on the floor.
            why = str(tc.get("error") or "").strip().splitlines()
            fails.append("final run not green"
                         + (f" — {why[0][:200]}" if why else ""))
        elif tc.get("verified") is False:
            fails.append("passed but unverified (healing/lenient matches behind the pass)")
        tcs.append({"id": tc.get("id", f"tc{i + 1}"), "pass": not fails, "failures": fails,
                    "development_s": dev,
                    **{k: tc.get(k) for k in ("elapsed_s", "run_s", "corrections",
                                              "lines", "green", "verified",
                                              "error")}})
        regressions += [f"{tcs[-1]['id']}: {f}" for f in fails]
    if len(tcs) < len(PROMPTS):
        regressions.append(f"only {len(tcs)} of {len(PROMPTS)} canonical test cases measured")

    def _avg(key):
        vals = [t[key] for t in tcs if isinstance(t.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    average = {"development_s": _avg("development_s"), "run_s": _avg("run_s"),
               "elapsed_s": _avg("elapsed_s"), "corrections": _avg("corrections"),
               "lines": _avg("lines")}
    # NOOD_0188 — the artifacts get the last word on the pass claims.
    audit_notes = audit(results, workspace) if workspace is not None else []
    regressions += [n for n in audit_notes if n.startswith("self-report")]
    return {"verdict": "PASS" if not regressions else "REGRESSED",
            "budget": b, "engine": results.get("engine"),
            "workspace": results.get("workspace"),
            "report_urls": results.get("report_urls") or [],
            "test_cases": tcs, "average": average,
            "audit": audit_notes, "regressions": regressions,
            "next": ("no regression — ship it" if not regressions else
                     "confirm the culprit: `git checkout <last-known-good>` (or main) + "
                     "`noodle update`, rerun this benchmark, compare verdicts — "
                     "docs/feature-regression.md § Bisecting a regression")}


def render_html(verdict: dict) -> str:
    """verdict.json as a one-page scorecard, served next to allure-report/
    and rca.html so the three acceptance criteria — time, cost, accuracy —
    are reviewable per build in a browser, not just as raw JSON."""
    v = verdict
    color = "#1a7f37" if v["verdict"] == "PASS" else "#cf222e"
    rows = ""
    for t in v["test_cases"]:
        rows += (
            "<tr><td>{id}</td><td>{ok}</td><td><b>{dev}</b></td><td>{run}</td>"
            "<td>{corr}</td><td>{lines}</td><td>{fails}</td></tr>".format(
                id=t["id"], ok="✅" if t["pass"] else "❌",
                dev=f"{t['development_s']}s" if t.get("development_s") is not None else "—",
                run=f"{t['run_s']}s" if t.get("run_s") is not None else "—",
                corr=t.get("corrections", "—"), lines=t.get("lines", "—"),
                fails="; ".join(t["failures"]) or "—"))
    b = v["budget"]
    return f"""<!doctype html><meta charset="utf-8">
<title>feature-regression verdict</title>
<style>body{{font:15px/1.5 system-ui;margin:2rem auto;max-width:64rem;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left}}
.v{{color:#fff;background:{color};display:inline-block;padding:.2rem .8rem;border-radius:.4rem}}</style>
<h1>Feature-generation regression — <span class="v">{v["verdict"]}</span></h1>
<p>Engine: <b>{v.get("engine") or "unknown"}</b>. Acceptance criteria per test
case — <b>development time</b> (how long generation took: total wall clock
minus the generated test's own run time), <b>accuracy</b> (engine corrections
needed + green&amp;verified run), <b>size</b> (generated .feature + POM
lines — is the engine still generating simple tests):</p>
<table><tr><th>test case</th><th>pass</th><th>development</th><th>run</th>
<th>corrections</th><th>lines</th><th>why not</th></tr>{rows}</table>
<p><b>Averages:</b> {v["average"]["development_s"]}s development,
{v["average"]["run_s"]}s run, {v["average"]["corrections"]} corrections,
{v["average"]["lines"]} generated lines per test case.
<b>Budget:</b> ≤{b["max_elapsed_s"]:.0f}s development,
≤{b["max_corrections"]:.0f} corrections per TC.</p>
{"".join(f"<p>⚠ {r}</p>" for r in v["regressions"])}
{"".join(f'<p style="color:#57606a">🔎 audit: {a}</p>' for a in v.get("audit") or [])}
<p><a href="allure-report/index.html">Allure report</a> · <a href="rca.html">RCA report</a></p>
<p style="color:#57606a">{v["next"]}</p>"""


def render_table(verdict: dict) -> str:
    """The benchmark as a terminal table — what `noodle feature-regression`
    prints. Plain f-strings on purpose: no rich, no tabulate, no dependency
    for six columns."""
    v = verdict
    def _s(x):
        return "—" if x is None else f"{x:g}s"

    def _n(x):
        return "—" if x is None else f"{x:g}"
    rows = [f"🧪 feature-regression — noodle {v.get('engine') or 'unknown'}",
            f"   workspace: {v.get('workspace') or '—'}", "",
            f"   {'TEST CASE':<26}{'GENERATE':>9}{'RUN':>7}{'CORR':>7}"
            f"{'LINES':>7}  {'GREEN':<7}{'VERIFIED'}"]
    for t in v["test_cases"]:
        rows.append(
            f"   {t['id']:<26}{_s(t.get('development_s')):>9}"
            f"{_s(t.get('run_s')):>7}{_n(t.get('corrections')):>7}"
            f"{_n(t.get('lines')):>7}"
            f"  {'✅' if t.get('green') else '❌':<6}{'✅' if t.get('verified') else '❌'}")
    a = v["average"]
    rows += ["   " + "─" * 72,
             f"   {'average':<26}{_s(a['development_s']):>9}{_s(a['run_s']):>7}"
             f"{_n(a['corrections']):>7}{_n(a['lines']):>7}",
             "", f"   VERDICT: {v['verdict']}"]
    rows += [f"   ⚠ {r}" for r in v["regressions"]]
    rows += [f"   🔎 audit: {n}" for n in v.get("audit") or []]
    if urls := v.get("report_urls"):
        rows += [""] + [f"   {'📊 ' if i == 0 else '   '}{u}"
                        for i, u in enumerate(urls)]
    return "\n".join(rows)


if __name__ == "__main__":  # ponytail: the one runnable check — no test framework
    good = {"test_cases": [{"id": p["id"], "elapsed_s": 20, "run_s": 14, "lines": 9,
                            "corrections": 0, "green": True, "verified": True}
                           for p in PROMPTS]}
    assert score(good)["verdict"] == "PASS"
    bad = {"test_cases": [dict(good["test_cases"][0], corrections=5, green=False)]}
    v = score(bad)
    assert v["verdict"] == "REGRESSED" and len(v["regressions"]) >= 3
    assert "VERDICT: REGRESSED" in render_table(v)
    print("regression.score self-check OK")

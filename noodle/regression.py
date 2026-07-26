"""Feature-generation regression benchmark (NOOD_0185).

Runs ONLY when a human asks for it ("run the feature regression"). The
benchmark itself is executed by whatever agent drives noodle — Claude,
Copilot, a human at a terminal — on any OS; this module only holds the
fixed prompts, the budget, and the pure scoring function, so every host
measures against the same yardstick. The prose (what "regressed" means,
HIL review, bisecting a regression to a commit) lives in
docs/feature-regression.md.
"""
import os

# The canonical "super easy" test cases, one per authoring mode. A live but
# automation-friendly site (Wikipedia) on purpose: typeahead suggestion,
# popup tolerance, cross-page navigation and plain-text assertions are the
# capabilities under test, and the benchmark must go green from any machine —
# retail sites bot-gate automated browsers (the original Canadian Tire pair,
# kept in docs/feature-regression.md as a live drill, proved that). Site
# drift = update the content here, never the scoring.
# Both are numbered PROMPTS — the plain-English path a human/agent actually
# sends (the AC: the benchmark must mimic real usage, never hand-built
# specs). tc1's suggestion click is prompt vocabulary since NOOD_0185.
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
]

# Per-test-case ceilings + the cross-case average — the definition of "not
# regressed": on time, on budget, accurate. Each is overridable with its env
# var when a deliberately slower/cheaper host model is driving (absolute AIC
# is not portable across hosts — docs/llm-performance.md §7).
_DEFAULTS = {
    "max_elapsed_s": ("NOODLE_REG_MAX_ELAPSED_S", 120),
    "max_aic": ("NOODLE_REG_MAX_AIC", 10),
    "max_avg_aic": ("NOODLE_REG_MAX_AVG_AIC", 10),
    "max_corrections": ("NOODLE_REG_MAX_CORRECTIONS", 2),
}

_SCHEMA_EXAMPLE = """\
{
  "host": "<driving agent + model, e.g. claude-sonnet-5>",
  "engine": "<the `noodle --version` line>",
  "report_urls": ["<served Allure URL>", "<served RCA URL>"],
  "test_cases": [
    {"id": "tc1_search_suggestion", "elapsed_s": 20, "run_s": 14, "aic": 3,
     "corrections": 0, "green": true, "verified": true,
     "engine_cost": {"input_tokens": 0, "output_tokens": 0, "usd": null}}
  ]
}"""


def budget() -> dict:
    return {k: float(os.getenv(env, d)) for k, (env, d) in _DEFAULTS.items()}


def score(results: dict) -> dict:
    """Pure verdict over agent-reported measurements (schema: runbook()).
    PASS = every canonical test case measured, green AND verified, within
    the per-case budget, and the cross-case AIC average holds; anything
    else is REGRESSED, with one reason line per breach."""
    b = budget()
    tcs, regressions = [], []
    for i, tc in enumerate(results.get("test_cases") or []):
        fails = []
        for field in ("elapsed_s", "aic", "corrections", "green", "verified"):
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
        if tc.get("aic") is not None and tc["aic"] > b["max_aic"]:
            fails.append(f"over budget: {tc['aic']} AIC > {b['max_aic']:.0f}")
        if tc.get("corrections") is not None and tc["corrections"] > b["max_corrections"]:
            fails.append(f"inaccurate: {tc['corrections']} corrections > {b['max_corrections']:.0f}")
        if tc.get("green") is False:
            fails.append("final run not green")
        elif tc.get("verified") is False:
            fails.append("passed but unverified (healing/lenient matches behind the pass)")
        tcs.append({"id": tc.get("id", f"tc{i + 1}"), "pass": not fails, "failures": fails,
                    "development_s": dev,
                    **{k: tc.get(k) for k in ("elapsed_s", "run_s", "aic", "corrections",
                                              "green", "verified", "engine_cost")}})
        regressions += [f"{tcs[-1]['id']}: {f}" for f in fails]
    if len(tcs) < len(PROMPTS):
        regressions.append(f"only {len(tcs)} of {len(PROMPTS)} canonical test cases measured")

    def _avg(key):
        vals = [t[key] for t in tcs if isinstance(t.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    usds = [t["engine_cost"]["usd"] for t in tcs
            if isinstance(t.get("engine_cost"), dict)
            and isinstance(t["engine_cost"].get("usd"), (int, float))]
    average = {"aic": _avg("aic"), "development_s": _avg("development_s"),
               "run_s": _avg("run_s"), "elapsed_s": _avg("elapsed_s"),
               "engine_usd": round(sum(usds) / len(usds), 4) if usds else None}
    if average["aic"] is not None and average["aic"] > b["max_avg_aic"]:
        regressions.append(f"average {average['aic']} AIC per test case > {b['max_avg_aic']:.0f}")
    return {"verdict": "PASS" if not regressions else "REGRESSED",
            "budget": b, "test_cases": tcs, "average": average, "regressions": regressions,
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
        cost = t.get("engine_cost") or {}
        rows += (
            "<tr><td>{id}</td><td>{ok}</td><td><b>{dev}</b></td><td>{run}</td>"
            "<td>{aic}</td><td>{tok}</td><td>{corr}</td><td>{fails}</td></tr>".format(
                id=t["id"], ok="✅" if t["pass"] else "❌",
                dev=f"{t['development_s']}s" if t.get("development_s") is not None else "—",
                run=f"{t['run_s']}s" if t.get("run_s") is not None else "—",
                aic=t.get("aic", "—"),
                tok=(f"{cost.get('input_tokens', 0)}/{cost.get('output_tokens', 0)} tok"
                     + (f" ${cost['usd']}" if cost.get("usd") else "")) if cost else "—",
                corr=t.get("corrections", "—"),
                fails="; ".join(t["failures"]) or "—"))
    b = v["budget"]
    return f"""<!doctype html><meta charset="utf-8">
<title>feature-regression verdict</title>
<style>body{{font:15px/1.5 system-ui;margin:2rem auto;max-width:64rem;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left}}
.v{{color:#fff;background:{color};display:inline-block;padding:.2rem .8rem;border-radius:.4rem}}</style>
<h1>Feature-generation regression — <span class="v">{v["verdict"]}</span></h1>
<p>Acceptance criteria per test case — <b>development time</b> (how long the
LLM/agent took to develop the TC: total wall clock minus the generated
test's own run time), <b>cost</b> (driving-agent AIC + engine tokens/$),
<b>accuracy</b> (corrections needed + green&amp;verified run):</p>
<table><tr><th>test case</th><th>pass</th><th>development</th><th>run</th>
<th>AIC</th><th>engine cost</th><th>corrections</th><th>why not</th></tr>{rows}</table>
<p><b>Averages:</b> {v["average"]["development_s"]}s development,
{v["average"]["run_s"]}s run, {v["average"]["aic"]} AIC per test case,
engine ${v["average"]["engine_usd"] or 0}.
<b>Budget:</b> ≤{b["max_elapsed_s"]:.0f}s development, ≤{b["max_aic"]:.0f} AIC,
≤{b["max_corrections"]:.0f} corrections per TC; ≤{b["max_avg_aic"]:.0f} AIC avg.</p>
{"".join(f"<p>⚠ {r}</p>" for r in v["regressions"])}
<p><a href="allure-report/index.html">Allure report</a> · <a href="rca.html">RCA report</a></p>
<p style="color:#57606a">{v["next"]}</p>"""


def runbook() -> str:
    """The protocol an agent follows, printed by `noodle feature-regression`.
    Self-sufficient on any host — no MCP, no skill file needed."""
    b = budget()
    lines = [
        "Noodle feature-generation regression benchmark (NOOD_0185)",
        "Proves prompt → .feature generation is still fast, cheap and accurate",
        "after engine changes. Prose + triage: `noodle docs feature-regression`.",
        "",
        "Setup",
        "  1. noodle update                    # sync the install with the checkout under test",
        "     — not optional: step 2 refuses to scaffold while the install lags,",
        "     since the folder is named after the checkout's version",
        "  2. noodle feature-regression --init # fresh regression_runs/<stamp>_<build>/ workspace",
        "     — runs the REAL `noodle init` into a NEW folder every run (gitignored),",
        "     so workspace scaffolding is part of the flow under test; everything for",
        "     this build (features, Allure + RCA reports, results.json, verdict.json)",
        "     lives there",
        "  3. cd <the printed workspace path>",
        "",
        "Per test case — measure each SEPARATELY, never combined:",
        "  4. Record wall-clock start.",
        "  5. ONE call, by the test case's mode (content below):",
        '     prompt:  noodle author --prompt "<content>" --run --json -w .',
        "     spec:    save content as tcN_spec.yaml, then",
        "              noodle author --spec tcN_spec.yaml --run --json -w .",
        "     (authors the .feature, runs it headless retries=0, serves reports)",
        "  6. corrections = every re-probe / re-author / re-run you needed after",
        "     that first call. 0 is the expectation.",
        "  7. Record wall-clock end → elapsed_s (whole TC), and run_s = the",
        "     `run.seconds` field of that call's JSON (the generated test's own",
        "     execution time). The scorer derives TEST DEVELOPMENT TIME =",
        "     elapsed_s − run_s — that is what the time budget applies to.",
        "     Note the AIC/credits YOUR host billed for this test case",
        "     (host-reported; the engine cannot see it).",
        "  8. noodle cost --json -w .          # engine-side spend → engine_cost",
        "",
        "Combined report — both test cases on ONE Allure + RCA:",
        "  9. noodle run noodle_tests/web/en_wikipedia_org -w . --headless --retries 0 --json --serve",
        "     Keep the served URLs for the results file.",
        "",
        "Score:",
        " 10. Fill results.json in the workspace root (schema below), then:",
        "     noodle feature-regression --score results.json    # exit 1 = REGRESSED",
        "     Writes verdict.json + verdict.html next to it AND into the served",
        "     reports dir — the ACs (time, cost, accuracy) live at /verdict.html",
        "     beside the Allure and RCA reports.",
        "",
        f"Budget (each overridable, see docs): ≤{b['max_elapsed_s']:.0f}s development time, "
        f"≤{b['max_aic']:.0f} AIC and ≤{b['max_corrections']:.0f} corrections per test case; "
        f"≤{b['max_avg_aic']:.0f} AIC average; every run green AND verified.",
        "",
        "results.json schema:",
        _SCHEMA_EXAMPLE,
    ]
    for p in PROMPTS:
        lines += ["", f"--- {p['id']} ({p['mode']}) ---", p["content"]]
    return "\n".join(lines)


if __name__ == "__main__":  # ponytail: the one runnable check — no test framework
    good = {"test_cases": [{"id": p["id"], "elapsed_s": 20, "run_s": 14, "aic": 3,
                            "corrections": 0, "green": True, "verified": True}
                           for p in PROMPTS]}
    assert score(good)["verdict"] == "PASS"
    bad = {"test_cases": [dict(good["test_cases"][0], aic=40, corrections=5)]}
    v = score(bad)
    assert v["verdict"] == "REGRESSED" and len(v["regressions"]) >= 4
    print("regression.score self-check OK")

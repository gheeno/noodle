"""Feature-generation regression benchmark (NOOD_0185).

Runs ONLY when a human asks for it ("run the feature regression"). The
benchmark itself is executed by whatever agent drives noodle — Claude,
Copilot, a human at a terminal — on any OS; this module only holds the
fixed prompts, the budget, and the pure scoring function, so every host
measures against the same yardstick. The prose (what "regressed" means,
HIL review, bisecting a regression to a commit) lives in
docs/feature-regression.md.
"""
import json
import os
from pathlib import Path

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
    # NOOD_0188 — Claude bills TOKENS, Copilot bills premium requests (AIC).
    # Scoring one benchmark in the other's unit made the cost half of the
    # verdict meaningless on whichever host you weren't using.
    "max_tokens": ("NOODLE_REG_MAX_TOKENS", 120_000),
    "max_avg_tokens": ("NOODLE_REG_MAX_AVG_TOKENS", 120_000),
}

# The two supported driving hosts and the unit each actually charges in.
# `host` in results.json selects one; anything else falls back to AIC with a
# note, since an unknown host's billing unit is exactly what we can't assume.
_HOST_UNITS = {"claude": "tokens", "copilot": "aic"}


def host_unit(host: str | None) -> str:
    """Which cost unit this run is measured in. Substring match so
    'claude-sonnet-5' / 'Copilot CLI (GPT-5)' both resolve."""
    h = (host or "").casefold()
    for name, unit in _HOST_UNITS.items():
        if name in h:
            return unit
    return "aic"

_SCHEMA_EXAMPLE = """\
{
  "host": "claude-sonnet-5",          // 'claude…' -> tokens | 'copilot…' -> aic
  "engine": "<the `noodle --version` line>",
  "report_urls": ["<served Allure URL>", "<served RCA URL>"],
  "test_cases": [
    {"id": "tc1_search_suggestion", "elapsed_s": 20, "run_s": 14,
     "tokens": 18000,                 // Claude hosts: input+output for THIS TC
     "aic": null,                     // Copilot hosts: premium requests instead
     "cost_basis": "host-reported",   // or "measured: <what>" — say which, so a
                                      // floor is never read as full spend
     "corrections": 0, "green": true, "verified": true,
     "engine_cost": {"input_tokens": 0, "output_tokens": 0, "usd": null}}
  ]
}"""


def budget() -> dict:
    return {k: float(os.getenv(env, d)) for k, (env, d) in _DEFAULTS.items()}


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
        last = json.loads(path.read_text())
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
    """Pure verdict over agent-reported measurements (schema: runbook()).
    PASS = every canonical test case measured, green AND verified, within
    the per-case budget, and the cross-case AIC average holds; anything
    else is REGRESSED, with one reason line per breach.

    NOOD_0188 — pass `workspace` (the CLI passes the results file's own
    directory) to also cross-check the green/verified claims against the
    run's `last_run.json`: a benchmark that takes its subject's word for
    the headline result isn't a measurement. Omitted → pure, no disk read."""
    b = budget()
    # NOOD_0188 — cost is measured in the unit the HOST actually bills:
    # tokens on Claude, premium requests (AIC) on Copilot.
    unit = host_unit(results.get("host"))
    cost_field = "tokens" if unit == "tokens" else "aic"
    cost_cap = b["max_tokens"] if unit == "tokens" else b["max_aic"]
    avg_cap = b["max_avg_tokens"] if unit == "tokens" else b["max_avg_aic"]
    tcs, regressions = [], []
    for i, tc in enumerate(results.get("test_cases") or []):
        fails = []
        for field in ("elapsed_s", cost_field, "corrections", "green", "verified"):
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
        if tc.get(cost_field) is not None and tc[cost_field] > cost_cap:
            label = "tokens" if unit == "tokens" else "AIC"
            fails.append(f"over budget: {tc[cost_field]:,} {label} > "
                         f"{cost_cap:,.0f}")
        if tc.get("corrections") is not None and tc["corrections"] > b["max_corrections"]:
            fails.append(f"inaccurate: {tc['corrections']} corrections > {b['max_corrections']:.0f}")
        if tc.get("green") is False:
            fails.append("final run not green")
        elif tc.get("verified") is False:
            fails.append("passed but unverified (healing/lenient matches behind the pass)")
        tcs.append({"id": tc.get("id", f"tc{i + 1}"), "pass": not fails, "failures": fails,
                    "development_s": dev,
                    **{k: tc.get(k) for k in ("elapsed_s", "run_s", "aic", "tokens",
                                              "cost_basis", "corrections", "green",
                                              "verified", "engine_cost")}})
        regressions += [f"{tcs[-1]['id']}: {f}" for f in fails]
    if len(tcs) < len(PROMPTS):
        regressions.append(f"only {len(tcs)} of {len(PROMPTS)} canonical test cases measured")

    def _avg(key):
        vals = [t[key] for t in tcs if isinstance(t.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    usds = [t["engine_cost"]["usd"] for t in tcs
            if isinstance(t.get("engine_cost"), dict)
            and isinstance(t["engine_cost"].get("usd"), (int, float))]
    average = {"aic": _avg("aic"), "tokens": _avg("tokens"),
               "development_s": _avg("development_s"),
               "run_s": _avg("run_s"), "elapsed_s": _avg("elapsed_s"),
               "engine_usd": round(sum(usds) / len(usds), 4) if usds else None}
    if average[cost_field] is not None and average[cost_field] > avg_cap:
        label = "tokens" if unit == "tokens" else "AIC"
        regressions.append(f"average {average[cost_field]:,} {label} per test "
                           f"case > {avg_cap:,.0f}")
    # NOOD_0188 — the artifacts get the last word on the pass claims.
    audit_notes = audit(results, workspace) if workspace is not None else []
    regressions += [n for n in audit_notes if n.startswith("self-report")]
    return {"verdict": "PASS" if not regressions else "REGRESSED",
            "budget": b, "host": results.get("host"), "cost_unit": unit,
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
    # NOOD_0188 — the host cost column shows the unit that host actually bills.
    unit = v.get("cost_unit", "aic")
    host_col = "tokens (Claude)" if unit == "tokens" else "AIC (Copilot)"
    rows = ""
    for t in v["test_cases"]:
        cost = t.get("engine_cost") or {}
        host_val = t.get("tokens") if unit == "tokens" else t.get("aic")
        basis = t.get("cost_basis")
        rows += (
            "<tr><td>{id}</td><td>{ok}</td><td><b>{dev}</b></td><td>{run}</td>"
            "<td>{host}</td><td>{tok}</td><td>{corr}</td><td>{fails}</td></tr>".format(
                id=t["id"], ok="✅" if t["pass"] else "❌",
                dev=f"{t['development_s']}s" if t.get("development_s") is not None else "—",
                run=f"{t['run_s']}s" if t.get("run_s") is not None else "—",
                host=(f"{host_val:,}" if isinstance(host_val, (int, float)) else "—")
                     + (f'<br><span style="color:#57606a;font-size:.85em">{basis}</span>'
                        if basis else ""),
                tok=(f"{cost.get('input_tokens', 0)}/{cost.get('output_tokens', 0)} tok"
                     + (f" ${cost['usd']}" if cost.get("usd") else "")) if cost else "—",
                corr=t.get("corrections", "—"),
                fails="; ".join(t["failures"]) or "—"))
    b = v["budget"]
    host_cap = b["max_tokens"] if unit == "tokens" else b["max_aic"]
    return f"""<!doctype html><meta charset="utf-8">
<title>feature-regression verdict</title>
<style>body{{font:15px/1.5 system-ui;margin:2rem auto;max-width:64rem;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left}}
.v{{color:#fff;background:{color};display:inline-block;padding:.2rem .8rem;border-radius:.4rem}}</style>
<h1>Feature-generation regression — <span class="v">{v["verdict"]}</span></h1>
<p>Host: <b>{v.get("host") or "unknown"}</b> — billed in <b>{host_col}</b>.
Acceptance criteria per test case — <b>development time</b> (how long the
LLM/agent took to develop the TC: total wall clock minus the generated
test's own run time), <b>cost</b> (the driving host's own unit + engine
tokens/$), <b>accuracy</b> (corrections needed + green&amp;verified run):</p>
<table><tr><th>test case</th><th>pass</th><th>development</th><th>run</th>
<th>{host_col}</th><th>engine cost</th><th>corrections</th><th>why not</th></tr>{rows}</table>
<p><b>Averages:</b> {v["average"]["development_s"]}s development,
{v["average"]["run_s"]}s run,
{v["average"][ "tokens" if unit == "tokens" else "aic"]} {host_col} per test case,
engine ${v["average"]["engine_usd"] or 0}.
<b>Budget:</b> ≤{b["max_elapsed_s"]:.0f}s development, ≤{host_cap:,.0f} {"tokens" if unit == "tokens" else "AIC"},
≤{b["max_corrections"]:.0f} corrections per TC.</p>
{"".join(f"<p>⚠ {r}</p>" for r in v["regressions"])}
{"".join(f'<p style="color:#57606a">🔎 audit: {a}</p>' for a in v.get("audit") or [])}
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
        "     Record what YOUR host billed for this test case, in ITS unit",
        "     (host-reported — the engine cannot see the driving agent):",
        "       Claude  → `tokens`: input+output for THIS test case. Claude",
        "                 Code reports session usage with /cost; take the",
        "                 delta across the TC (or the API usage totals).",
        "       Copilot → `aic`: premium requests consumed by THIS test case.",
        "     The scorer picks the unit from `host`, so fill the one that",
        "     matches and leave the other null.",
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
        f"Budget (each overridable, see docs): ≤{b['max_elapsed_s']:.0f}s development time "
        f"and ≤{b['max_corrections']:.0f} corrections per test case; every run green AND "
        "verified. Cost ceiling depends on the host's unit — "
        f"Claude ≤{b['max_tokens']:,.0f} tokens, Copilot ≤{b['max_aic']:.0f} AIC per "
        "test case (absolute cost is not portable across hosts; compare like "
        "for like — docs/llm-performance.md §7).",
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

# Feature-generation regression benchmark (NOOD_0185)

Not a unit test. An end-to-end "is the core product still good" check:
two fixed plain-English prompts → authored `.feature` files → green verified
runs — measured on **time**, **accuracy** and **size**, per test case and on
average. It exists so that after new engine capabilities land, one command
tells you whether the thing Noodle is actually for — fast, correct test
generation — regressed.

It runs **only when a human asks for it** ("run feature-regression").
Nothing schedules it; CI does not run it (it drives a live site).

Works on any OS and under any driving agent (Claude, Copilot, a plain
terminal) as long as `noodle` is installed — it is **one command** that does
the whole benchmark itself, so there is nothing for an agent to improvise.

## The flow

```
noodle feature-regression            # RUNS it: generate → run → serve → table
noodle feature-regression --json     # one bounded payload instead of the table
noodle feature-regression --init     # only scaffold the fresh workspace, don't run
noodle feature-regression --score results.json   # re-score an existing run
```

Exit **0 = PASS, 1 = REGRESSED**. One call from anywhere — a bare terminal,
Claude Code, Copilot CLI — does the whole thing: scaffolds a fresh workspace,
authors and runs both canonical prompts, runs them again combined onto one
Allure + RCA, scores, serves, prints the table.

```
🧪 feature-regression — noodle 1.0.0a6
   workspace: /…/regression_runs/20260727-101500_1.0.0a6_6859c75

   TEST CASE                  GENERATE    RUN   CORR  LINES  GREEN  VERIFIED
   tc1_search_suggestion             9s     7s      1      8  ✅     ✅
   tc2_account_textboxes            11s    12s      0     12  ✅     ✅
   ────────────────────────────────────────────────────────────────────────
   average                          10s   9.5s    0.5     10

   VERDICT: PASS

   📊 http://127.0.0.1:PORT/verdict.html
      http://127.0.0.1:PORT/allure-report/index.html
      http://127.0.0.1:PORT/rca.html
```

**Nothing is hand-measured.** Before NOOD_0190 this command printed a 10-step
protocol and exited 2, so every driving agent improvised it: read docs, guessed
flags, hand-wrote `results.json`, and — on a host with no billing API — went
digging through session telemetry to invent a cost number. That improvisation
was the entire cost of running the benchmark, and it produced verdicts whose
deciding number was a guess. Now the engine writes `results.json` from the
payload `author_test(run_after_author=True)` already returns.

## One folder per build

The command scaffolds `<clone>/regression_runs/<YYYYmmdd-HHMMSS>_<version>_<gitsha>/` —
a **new** workspace every run, never reused (a reused workspace inherits the
previous build's features and POMs and stops measuring generation). It runs
the **real `noodle init`** into that folder — the same scaffold every user
gets — so the benchmark covers the full end-to-end flow:
`init → prompt → authored .feature/POM → headless run → served Allure + RCA +
verdict`. A broken scaffold fails the very next step, so init regressions
surface immediately. The folder is anchored to the engine **clone** (where
`regression_runs/` is gitignored), not the cwd, so invoking from a
subdirectory can't scatter un-gitignored run folders; it is self-contained —
generated features, Allure and RCA reports, `results.json`, `verdict.json`
and `verdict.html` all live inside it, so comparing this build against the
last one is just opening two sibling folders.

A stale install still refuses up front (`run \`noodle update\` first`): the
folder is stamped with the **checkout's** version, so measuring a lagging
install would file the results under code that never ran.

The test cases live in `noodle/regression.py` (`PROMPTS`): **both are
numbered plain-English prompts** — the benchmark must generate the way a
human or agent actually asks (an AC, not a convenience). tc1 exercises the
typeahead flow (`click the suggestion <option>` after a search — prompt
vocabulary since NOOD_0185), tc2 click-navigation + plain-text verifies,
both against Wikipedia — live but automation-friendly, so the benchmark can
go green from any machine. If the site drifts, update the content there;
never bend the scoring. The scoring doesn't care what the prompts say, only
what they cost — any pair works for a demo.

## What each measurement means

Every one is read out of the author/run payload — none is reported by the
driving agent.

| Field | Where it comes from | Meaning |
|---|---|---|
| `elapsed_s` | wall clock around the `author_test` call | The whole test case: generate + run. |
| `run_s` | `run.seconds` | The generated test's own execution time. |
| `development_s` | **derived:** `elapsed_s − run_s` | How long *generation* took. This is what the time budget applies to. |
| `corrections` | `run.healing_events` + `run.flaky` + re-author passes | The engine's own repair signals: a locator that needed self-healing, a scenario that needed a retry, a re-author because `ready` came back false. Never a self-report — last session tc1 claimed `corrections: 0` while the run log recorded a real heal (`locator 'search', strategy visible-filter, multiple matches, exactly one visible`). The engine knew; the old protocol never asked. |
| `lines` | `author.compiled.feature` + `author.compiled.pom` line count | The simplicity signal: *are we still generating simple `.feature` files*. Reported, not gated — if generation starts padding features with extra steps or POM entries, this moves. Stdlib `splitlines()`, zero dependencies, always present. |
| `green` / `verified` | `run.failed`, `run.verified`, `author.intent_verified` | The run contract: `failed == 0` **and** `verified: true` **and** the intent contract held. A pass held up by fuzzy healing or lenient matching is not a pass. |

Accuracy has a human half too: the HIL (or a reviewing agent) reads the
generated `.feature` against the prompt — steps match the intent, assertions
assert what was asked, nothing invented.

### One re-author, then red

`execute()` re-authors a case **once** (with `overwrite=True`) when
`author.ready` comes back false, and counts that as one correction — that is
what a real user does. Still not ready → the case is red and the verdict is
REGRESSED. The correction budget (2) bounds it either way.

### No cost column

NOOD_0190 removed the host AIC/token accounting **and** the engine LLM
ledger. The host figure measured how lost the *driving agent* got, not
whether the engine regressed, and on a host with no billing API it could only
be guessed. The engine figure read `none` every run — the deterministic fast
path makes zero model calls (`translation_mode: deterministic-fast-path`,
`interpretation_model_calls: 0`). Generated line count is the size signal
that actually moves. If you want to compare *agent* cost across hosts, that
is [llm-performance.md §7](llm-performance.md), not this benchmark.

## Budget

| Ceiling | Default | Env override |
|---|---|---|
| Per-TC **development time** (`elapsed_s − run_s`) | 120 s | `NOODLE_REG_MAX_ELAPSED_S` |
| Per-TC corrections | 2 | `NOODLE_REG_MAX_CORRECTIONS` |

Overrides exist for a deliberately slower machine or site — set them in the
shell, not in code.

## Reading the verdict

The table above is the whole report: per-TC pass/fail, the averages, and
`VERDICT: PASS | REGRESSED`, with one reason line per breach ("slow
development: …", "inaccurate: …", "final run not green", "passed but
unverified"). Exit code 1 on REGRESSED, so it slots into any script.
`--json` gives the same thing as one bounded payload.

The same scorecard renders as **`verdict.html`**, written into the build
folder *and* into the run's served reports directory — so the acceptance
criteria are reviewable in the browser at `/verdict.html`, right beside
`/allure-report/index.html` and `/rca.html`, and stay in the build folder for
build-vs-build comparison.

## Bisecting a regression

A REGRESSED verdict says the *current checkout* is worse — not which commit
did it. Confirm before blaming:

1. `git checkout main` (or the last known-good SHA) && `noodle update`
2. `noodle feature-regression` again — it scaffolds its own fresh workspace.
3. Baseline also REGRESSED → the site changed, not the engine. Baseline PASS
   → walk the suspect commits (`git checkout <sha>` + `noodle update` each
   time) until the verdict flips.

If numbers look absurd or the behavior looks old, first suspect a stale
install shadowing the checkout — `noodle doctor`.

## Live drill — the original retail-site pair

The benchmark was born from two Canadian Tire prompts. They remain the
harder, site-specific drill — run them the same way when you want realism
over repeatability (their search API bot-gates automated browsers from
ordinary machines: `net::ERR_ABORTED` on the results XHR, headed and
headless alike, so expect red for site reasons):

```
1. Go to the URL https://www.canadiantire.ca/en.html
2. Close any popup that may appear
3. Use the search bar, search for "Vaccu" (needs to be incomplete)
4. Then a suggestion bar appears below the search bar
5. Click the suggestion "Vaccum cleaner"
6. Then the results page appears with these products:
   "BISSELL® PowerLifter® FurFinder™ Cordless Self-Standing Stick Vacuum" and
   "Hoover WindTunnel 2 Bagless Upright Vacuum"
```

```
1. Go to the URL https://www.canadiantire.ca/en.html
2. Close all the popups that may appear, including geolocation
3. Click the order status
4. On the next page verify you see the two textboxes with
   'Please enter your email address' and 'please enter an order number'
```

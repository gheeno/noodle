# The benchmark (NOOD_0185, NOOD_0232)

`noodle benchmark` measures this build on two axes, and which one you want
decides the flag:

| | `noodle benchmark --gate` | `noodle benchmark` |
|---|---|---|
| Question | can the engine still generate a good test | can it still take a request phrased the way people phrase them |
| Varies | the **flow** — 5 fixed flows, one fixed phrasing | the **shape** — 6 phrasings, one fixed app |
| Target | Wikipedia + a static fixture | BusterBlock, behind its login gate |
| Role | **required** before any engine-code PR | on demand, and before a release |
| Cost | ~90s | ~2-3 min |
| Documented | this page | [benchmark-specs.md](benchmark-specs.md) |

The gate holds the phrasing still so generation itself can be measured; the
default holds the app still so the phrasing can be. A build can pass one and
fail the other, and which one it fails tells you something different each
time. **The rest of this page is the gate.**

## Every flag, and when you want it

`noodle benchmark` with no flag runs the SPEC-SHAPE benchmark headless — no
agent; no model when `NOODLE_MODEL` is unset — and the table's
`interpretation:` line records which path was measured. Everything else is a
variation on that or on the gate.

| Flag | Mode | What it does |
|---|---|---|
| *(none)* | shape | Headless run of all six specs — no agent; no model when `NOODLE_MODEL` is unset (the table's `interpretation:` line says which). Reproducible and free, so it is the right instrument for comparing two builds. A spec the compiler cannot take is a real finding about the grammar. |
| `--session` | shape | Opens an **agent-driven** run: starts BusterBlock, scaffolds the workspace with the real `noodle init`, arms the ledger and prints the runbook for THIS LLM session to follow. The agent is the interpreter, so no spec is blocked by phrasing — what is measured is how much work the loop took. |
| `--table` | shape | Prints the table for the open `--session` run **from the ledger the engine wrote**, never from what the agent remembers doing, then stops the app. This ends the session. |
| `--json` | both | One bounded JSON payload instead of the table. |
| `--specs <file>` | shape | Read the specs from this markdown file instead of `docs/benchmark-specs.md` — for trying a spec set without editing the source of truth. |
| `--gate` | gate | The **PR gate**: the same 5 canonical flows every time, ~90s. Required before any PR that changes engine code. |
| `--gate --init` | gate | Only scaffold the fresh workspace under `regression_runs/<stamp>_<build>_<sha>/` and stop. |
| `--gate --score <results.json>` | gate | Re-score an existing run instead of running it again; writes `verdict.json`/`verdict.html` next to it. |

Exit **0 = PASS, 1 = REGRESSED**, in both modes; **2 = the benchmark could
not run** (a setup fault, `--table` with no open session, a second
`--session` while one is open) — never a REGRESSED verdict.

### The two shape modes are not interchangeable

```bash
noodle update                # the install must match this checkout, or it refuses
noodle benchmark             # headless: the engine's FLOOR, no agent in the loop
noodle benchmark --session   # agent-driven: the workflow the product ships as
```

Headless answers *"what can the compiler take on its own?"* — a blocked spec
there is a grammar gap worth filing. `--session` answers *"what does a user
actually experience?"* — there is always an agent in front of the engine, so
nothing is blocked by phrasing and the honest measurement is the **cost of the
loop**. Reporting one as the other is the mistake both modes exist to prevent.

`--session` spans many turns: it opens, your session authors the specs one at
a time, then `--table` closes it. Do not tally anything by hand in between —
`author_test` appends a line per attempt to `.noodle/benchmark_ledger.jsonl`,
and the table is built from that.

### Both doors count

The runbook prints the spec for **either** door, and the ledger records which
one was used:

```bash
# MCP
author_test(prompt=<the spec, verbatim>, feature_path="spec_<id>.feature",
            workspace="<ws>", run_after_author=True, overwrite=True)

# CLI (NOOD_0236)
noodle author --prompt <the spec, verbatim> \
    --feature-path spec_<id>.feature -w <ws> --run --overwrite
```

`--feature-path` is not cosmetic. The ledger keys on the filename, so a spec
authored under any other name is scored **"not attempted"** — which is how a
session that ran every spec green can still print `REGRESSED`.

Not a unit test. An end-to-end "is the core product still good" check:
fixed test cases → authored `.feature` files → green verified runs —
measured on **time**, **accuracy** and **size**, per test case and on
average. It exists so that after new engine capabilities land, one command
tells you whether the thing Noodle is actually for — fast, correct test
generation — regressed.

It runs on demand ("run the benchmark") and, since NOOD_0197, as the
**pre-PR gate for every engine branch**: after the branch's single squashed
commit, before opening the PR, exit 0 required (see CONTRIBUTING.md).
Nothing schedules it; CI does not run it (it drives a live site) — the
local run is the only gate.

Works on any OS and under any driving agent (Claude, Copilot, a plain
terminal) as long as `noodle` is installed — it is **one command** that does
the whole benchmark itself, so there is nothing for an agent to improvise.

## The flow

```
noodle benchmark --gate            # RUNS it: generate → run → serve → table
noodle benchmark --gate --json     # one bounded payload instead of the table
noodle benchmark --gate --init     # only scaffold the fresh workspace, don't run
noodle benchmark --gate --score results.json   # re-score an existing run
```

Exit **0 = PASS, 1 = REGRESSED**; **2 = the benchmark could not run** (a
setup fault — never a REGRESSED verdict). One call from anywhere — a bare
terminal, Claude Code, Copilot CLI — does the whole thing: scaffolds a fresh
workspace, authors and runs all five canonical cases, runs them again
combined onto one Allure + RCA, scores, serves, prints the table.

```
🧪 benchmark — noodle 1.0.0a54
   workspace: /…/regression_runs/20260807-101500_1.0.0a54_258eda3

   TEST CASE                  GENERATE    RUN   CORR  LINES  GREEN  VERIFIED
   tc1_search_suggestion          12.1s    14s      1     10  ✅     ✅
   tc2_account_textboxes           5.3s    13s      0     16  ✅     ✅
   tc3_api_seeds_ui_verifies       3.4s    11s      0     10  ✅     ✅
   tc4_multipage_checkout          3.9s    11s      0     33  ✅     ✅
   tc5_search_pick_add             3.6s    14s      2     22  ✅     ✅
   ────────────────────────────────────────────────────────────────────────
   average                        5.66s  12.6s    0.6   18.2

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

The test cases live in `noodle/regression.py` (`PROMPTS`) — tc1-tc3 against
Wikipedia (live but automation-friendly), tc4-tc5 against the engine's own
static fixture (`noodle/regression_fixture.py`, served on an ephemeral
localhost port), so the benchmark can go green from any machine. If the live
site drifts, update the content there; never bend the scoring.

## The canonical cases

| # | Mode | What it covers |
|---|---|---|
| `tc1_search_suggestion` | numbered prompt | typeahead suggestion, popup tolerance, plain-text assertion |
| `tc2_account_textboxes` | numbered prompt | click-navigation across pages, multiple field assertions |
| `tc3_api_seeds_ui_verifies` | numbered prompt | **cross-wok** — fetch over REST, assert the status, then prove the UI rendered it |
| `tc4_multipage_checkout` | numbered prompt | NOOD_0227 — four-page flow: per-card `within:`-scoped click, same-URL DOM-mutation cart panel, three-field commit form, end-state assertions |
| `tc5_search_pick_add` | numbered prompt | NOOD_0230 — the loose search→pick→add→verify shape on a deterministic grid: ordinal pick binding, `add_to` lowering, single deduplicated destination click |

All five are the plain-English path a human or agent actually sends. tc3
(NOOD_0191) is the other real shape: *"get the data from the API, then prove
the UI shows it"* — the most common mix in a real suite, because most UI is
an API with a face on it. It exercises the REST client, `{env:}` across both
step families in one scenario, and the compiler's cross-wok step **order**
(the REST preamble ahead of the navigation `Given`).

**It was `feature_content` until NOOD_0192**, because the prompt compiler was
web-only and a cross-wok test genuinely had to be hand-written Gherkin — which
meant the one cross-wok case in a *generation* benchmark measured no
generation at all and printed `—` for LINES. Now the api verbs are in the
grammar, so all five cases measure what the engine writes, and the risk and
work effort of a cross-wok test is a number like any other. What the swap gave
up is covered where it belongs, in `unit_tests/woks/api/`: `{var:}` chaining
from a response field, and the `feature_content` door.

The generated scenario is tagged `@web`, **not** `@api`: `@api` means "start
no browser", which would kill the UI half. The compiler decides this itself —
api-only goals get `@api`, anything with a web step gets `@web`
([woks.md](woks.md#the-api-wok-is-a-lifecycle-not-a-gate)).

## What each measurement means

Every one is read out of the author/run payload — none is reported by the
driving agent.

| Field | Where it comes from | Meaning |
|---|---|---|
| `elapsed_s` | wall clock around the `author_test` call | The whole test case: generate + run. |
| `run_s` | `run.seconds` | The generated test's own execution time. |
| `development_s` | **derived:** `elapsed_s − run_s` | How long *generation* took. This is what the time budget applies to. |
| `corrections` | `run.healing_events` + `run.flaky` + re-author passes | The engine's own repair signals: a locator that needed self-healing, a scenario that needed a retry, a re-author because `ready` came back false. Never a self-report — last session tc1 claimed `corrections: 0` while the run log recorded a real heal (`locator 'search', strategy visible-filter, multiple matches, exactly one visible`). The engine knew; the old protocol never asked. |
| `lines` | `author.compiled.feature` + `author.compiled.pom` line count | The simplicity signal: *are we still generating simple `.feature` files*. Reported, not gated — if generation starts padding features with extra steps or POM entries, this moves. `—` for a `feature_content` case: nothing was generated, so there is nothing to count, and it is excluded from the average rather than counted as zero. Since NOOD_0192 all five cases are prompts, so all five report a number. |
| `green` / `verified` | `run.failed`, `run.verified`, `author.intent_verified` | The run contract: `failed == 0` **and** `verified: true` **and** the intent contract held. A pass held up by fuzzy healing or lenient matching is not a pass. `intent_verified` asks "did the *compiled* goal match probe evidence", so it applies to the prompt cases — which, since NOOD_0192, is all five. (A `feature_content` case states its intent literally, with nothing inferred to verify against; there `run.verified` alone is the bar.) |

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
that actually moves here. If you want to compare *agent* cost across hosts,
that is [llm-performance.md §7](llm-performance.md), not this benchmark.

**Cost is measured in the drill instead** (NOOD_0232), and door-agnostic:
the collapsed + bounded payload the engine hands back for one authoring lap,
serialized bytes ÷ 4 — identical whichever door drove the lap, because both
doors apply the same two transforms before the payload crosses the wire
(`benchmark.py _tokens`). That survives
where the host figure did not, because it is deterministic, needs no billing
API, is identical on every machine, moves the moment a payload grows, and
measures the only part of an agent's bill the engine controls. It is also
**build-agnostic**, which is the property that matters for the job the old
column kept failing: one drill can measure two engine versions whose
internals differ, which is what a "did this release get more expensive"
question actually requires.

## Budget

| Ceiling | Default | Env override |
|---|---|---|
| Per-TC **development time** (`elapsed_s − run_s`) | 120 s | `NOODLE_REG_MAX_ELAPSED_S` |
| Per-TC corrections | 2 | `NOODLE_REG_MAX_CORRECTIONS` |

The spec-shape benchmark (`noodle benchmark` / `--session`) has its own pair,
and shares the third with the gate — a spec is a test case like any other and
is not graded on a softer curve:

| Ceiling | Default | Env override |
|---|---|---|
| Per-spec **development time** | 120 s | `NOODLE_BENCH_MAX_DEV_S` |
| Per-spec **payload tokens** (scored only on green specs) | 2048 | `NOODLE_BENCH_MAX_TOKENS` |
| Per-spec corrections | 2 | shared with the gate: `NOODLE_REG_MAX_CORRECTIONS` |

Overrides exist for a deliberately slower machine or site — set them in the
shell, not in code.

`NOODLE_REG_KEEP_ATTEMPTS=1` (NOOD_0230) preserves a failed attempt's
authoring payload and probe snapshot under `<workspace>/attempts/` before the
re-author lap overwrites them — the artifacts a postmortem needs, kept
instead of reconstructed from a transcript.

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
2. `noodle benchmark --gate` again — it scaffolds its own fresh workspace.
3. Baseline also REGRESSED → the site changed, not the engine. Baseline PASS
   → walk the suspect commits (`git checkout <sha>` + `noodle update` each
   time) until the verdict flips.

If numbers look absurd or the behavior looks old, first suspect a stale
install shadowing the checkout — `noodle doctor`.

## The other axis — `noodle benchmark` (NOOD_0232)

```
noodle benchmark
```

The gate above varies the **flow** and holds the phrasing pinned at exactly
one value: every one of its cases is a numbered imperative list. So it cannot
answer the question a user asks before adopting Noodle — *does it still work
when I don't phrase my request the way your benchmark does.* A build that
generates perfect tests from perfectly-shaped prompts scores PASS here
whatever it does with a paragraph, a one-liner, or a half-specified ticket.

`noodle benchmark` is that axis. It holds the **app** constant — the repo's
own bundled **BusterBlock** site (`test-apps/busterblock`), behind its login
gate — and varies only how the request is phrased: a paragraph, a numbered
list, a single sentence, a short ambiguous spec, **one spec whose assertion is
deliberately wrong**, and (NOOD_0236) **one whose step needs a helper the
grammar has no verb for**. Because the app is fixed, any difference between
rows is attributable to the phrasing and nothing else.

It is not a PR gate. Run it when touching the prompt compiler, the agent
doors or payload sizes, and before a release.

| | `noodle benchmark --gate` | `noodle benchmark` |
|---|---|---|
| Varies | the flow (5 flows, one shape) | the shape (6 shapes, one app) |
| Target | Wikipedia + a static fixture | BusterBlock, behind its login gate |
| Role | **required** before any engine-code PR | on demand, before a release |
| Cost | ~90s | ~2-3 min |

The specs, what each column means, and what a change to any of them means
live in **[benchmark-specs.md](benchmark-specs.md)** — which is also the file
the benchmark parses its prompts out of, so the block a human pastes into a
session is byte-for-byte the one measured.

## Live drill — a real site

The benchmark grew out of a pair of prompts against a live retail store
front. Templates below: substitute your own site and its real copy. This is
the harder, site-specific drill — run it the same way when you want realism
over repeatability. Expect red for *site* reasons rather than engine ones:
most retail search APIs bot-gate automated browsers from ordinary machines
(`net::ERR_ABORTED` on the results XHR, headed and headless alike).

```
1. Go to the URL https://<your-retail-site>/en.html
2. Close any popup that may appear
3. Use the search bar, search for "Vaccu" (needs to be incomplete)
4. Then a suggestion bar appears below the search bar
5. Click the suggestion "Vaccum cleaner"
6. Then the results page appears with these products:
   "<exact product title 1>" and "<exact product title 2>"
```

```
1. Go to the URL https://<your-retail-site>/en.html
2. Close all the popups that may appear, including geolocation
3. Click the order status
4. On the next page verify you see the two textboxes with
   'Please enter your email address' and 'please enter an order number'
```

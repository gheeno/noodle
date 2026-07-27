# Feature-generation regression benchmark (NOOD_0185)

Not a unit test. An end-to-end "is the core product still good" check:
two fixed plain-English prompts → `noodle author --prompt … --run` →
authored `.feature` files → green verified runs — measured on **time**,
**cost**, and **accuracy**, per test case and on average. It exists so that
after new engine capabilities land, one command tells you whether the thing
Noodle is actually for — cheap, fast, correct test generation — regressed.

It runs **only when a human asks for it** ("run the feature regression").
Nothing schedules it; CI does not run it (it drives a live site and bills a
host model).

Works on any OS and under any driving agent (Claude, Copilot, a plain
terminal) as long as `noodle` is installed: the whole protocol is printed by
one command, no MCP or skill file required.

## The flow

```
noodle feature-regression            # prints the runbook, exits 2 — it is NOT a run
noodle feature-regression --init     # fresh per-build workspace (see below)
noodle feature-regression --score results.json   # verdict, exit 1 = REGRESSED
```

## One folder per build

`--init` scaffolds `regression_runs/<YYYYmmdd-HHMMSS>_<version>_<gitsha>/` —
a **new** workspace every run, never reused (a reused workspace inherits the
previous build's features and POMs and stops measuring generation). It runs
the **real `noodle init`** into that folder — the same scaffold every user
gets — so the benchmark covers the full end-to-end flow:
`update → init → prompt → authored .feature/POM → headless run → served
Allure + RCA + verdict`. A broken scaffold fails the very next step, so
init regressions surface immediately. The
folder is gitignored and self-contained: the generated features, the Allure
and RCA reports, `results.json` and the `verdict.json` written by `--score`
all live inside it, so comparing this build against the last one is just
opening two sibling folders.

The runbook in short: `noodle update`, `noodle init` a **fresh** directory,
then per test case — timed and reported **separately, never combined** — one
`noodle author … --run --json -w .` call (each TC states its mode: `--prompt`
for the numbered plain-English case, `--spec` for the goal case), count every
correction after it, capture the host's own AIC for the TC and
`noodle cost --json` for the engine side. Then one combined
`noodle run … --headless --retries 0 --json --serve` so **both** test cases
land on the same served Allure + RCA report. Fill `results.json`, score it.

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

| Field | Meaning |
|---|---|
| `elapsed_s` | Wall clock from starting the TC to its served green report. |
| `run_s` | The generated test's own execution time — the `run.seconds` field of the author call's JSON. |
| `development_s` | **Derived by the scorer:** `elapsed_s − run_s` — how long the LLM/agent spent *developing* the test case (prompt → authored `.feature`). This is what the time budget applies to. |
| `tokens` / `aic` | The **driving agent's own** cost for that TC, **in the unit its host actually bills** (NOOD_0188) — host-reported; the engine cannot see the driving agent. `host` selects it: a `claude…` host is scored on `tokens` (input+output for that TC — Claude Code reports session usage via `/cost`, take the delta across the TC), a `copilot…` host on `aic` (premium requests). Fill the one that matches, leave the other `null`; the scorer flags the missing one as an unmeasured field. Scoring a Claude run in Copilot's unit (or vice-versa) made the cost half of the verdict meaningless on whichever host you weren't using. Absolute cost is not portable across hosts ([llm-performance.md §7](llm-performance.md)) — compare same host to same host. |
| `cost_basis` | How the host cost was obtained: `"host-reported"` or `"measured: <what>"`. NOOD_0189: a cost of **0** is rejected as "unmeasured cost" unless `cost_basis` starts with `host-reported` — a driving agent always bills something, so a zero placeholder is not a measurement (it used to slip past the `> cap` check while `null` failed loudly). |
| `corrections` | Accuracy proxy: every re-probe / re-author / re-run needed **after** the first `author --prompt --run` call. 0 is the expectation; a couple is tolerable; more means the engine sent the agent chasing. |
| `green` / `verified` | The run contract: `failed == 0` **and** `verified: true`. A pass held up by fuzzy healing or lenient matching is not a pass. |
| `engine_cost` | Noodle's own `NOODLE_MODEL` spend (`noodle cost --json`), separate from the host's AIC. |

Accuracy has a human half too: the HIL (or a reviewing agent) reads the
generated `.feature` against the prompt — steps match the intent, assertions
assert what was asked, nothing invented.

## Budget

| Ceiling | Default | Env override |
|---|---|---|
| Per-TC **development time** (`elapsed_s − run_s`) | 120 s | `NOODLE_REG_MAX_ELAPSED_S` |
| Per-TC host cost — **Copilot** (AIC) | 10 | `NOODLE_REG_MAX_AIC` |
| Cross-TC AIC average — **Copilot** | 10 | `NOODLE_REG_MAX_AVG_AIC` |
| Per-TC host cost — **Claude** (tokens) | 120 000 | `NOODLE_REG_MAX_TOKENS` |
| Cross-TC token average — **Claude** | 120 000 | `NOODLE_REG_MAX_AVG_TOKENS` |
| Per-TC corrections | 2 | `NOODLE_REG_MAX_CORRECTIONS` |

Only the ceiling for **this run's host unit** is enforced (NOOD_0188) — the
other is ignored, so a Claude run is never judged against a premium-request
budget it doesn't spend.

Rationale: a super-easy test case should cost about one turn's worth of
context end to end — ~10 AIC on Copilot (NOOD_0156's acceptance line), or a
low-six-figure token count on Claude, where a single tool round-trip re-sends
the conversation. Overrides exist for deliberately slower/cheaper host models
— set them in the shell, not in code.

## Reading the verdict

`--score` prints per-TC pass/fail with one reason line per breach
("slow: …", "over budget: …", "inaccurate: …", "final run not green",
"passed but unverified"), the averages (AIC, seconds, engine USD), and
`verdict: PASS | REGRESSED`. Exit code 1 on REGRESSED, so it slots into any
script.

It also renders the same scorecard as **`verdict.html`** — written next to
`results.json` *and* into the run's served reports directory, so the three
acceptance criteria (time per TC, cost per TC, accuracy) are reviewable in
the browser at `/verdict.html`, right beside `/allure-report/index.html` and
`/rca.html`, and stay in the build folder for build-vs-build comparison.

## Bisecting a regression

A REGRESSED verdict says the *current checkout* is worse — not which commit
did it. Confirm before blaming:

1. `git checkout main` (or the last known-good SHA) && `noodle update`
2. Rerun the benchmark in a **new** fresh workspace, same host model.
3. Baseline also REGRESSED → the site or the host changed, not the engine.
   Baseline PASS → walk the suspect commits (`git checkout <sha>` +
   `noodle update` each time) until the verdict flips.

If numbers look absurd (huge AIC, old behavior), first suspect a stale
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

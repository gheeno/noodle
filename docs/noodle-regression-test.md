# Agent regression drill — runbook

**You are an LLM agent executing this file.** It answers one question after a
major commit: *is Noodle still fast and cheap to develop a test with?*

Execute phases 0→5 in order. Each phase has a STOP condition — obey it.
Everything you write goes in `tmp/`, which is gitignored.

## Contract — read before phase 0

| Rule | |
|---|---|
| **Never edit engine code** | Not to fix a failure, not to make a case pass. Observe only. |
| **Never commit, stage, or push** | All output lands in `tmp/`. |
| **Never hand-author Gherkin** | Paste the prompt; let the engine generate. Hand-written `feature_content` measures nothing. |
| **One fresh workspace per case** | A reused workspace inherits the last case's POM and stops measuring generation. |
| **Never bend a budget to pass** | Record the breach. A breach ends in a plan, not a patch. |
| **Never estimate a cost number** | Report your own counter or leave it blank. |

### Why this exists next to `noodle feature-regression`

Keep both — neither replaces the other.

| | `noodle feature-regression` | this drill |
|---|---|---|
| Driven by | the engine, one command | you, by hand |
| Measures | generation time, corrections, lines | **dev time + agent AIC per case** |
| Cost | zero tokens | real tokens — that is the point |
| Deterministic | yes | no (a model drives it) |
| Role | pre-PR gate, exit 0 required | post-commit health check |

The engine gate has no cost column on purpose. This drill is that missing
half. It cannot gate a PR: an LLM in the loop means no stable exit code.

---

## Phase 0 — engine gate (free, do this first)

```bash
noodle update
noodle feature-regression
```

- **Exit 0** → continue to phase 1.
- **Exit 1, or "run `noodle update` first"** → **STOP.** Report the engine
  gate failed and do nothing else. A regression the free gate already caught
  is not worth tokens to re-find.

## Phase 1 — setup

```bash
export NOODLE_REG_RETAIL_URL="https://<your-retail-site>/en.html"
export RUN="tmp/agent-regression/$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
```

`NOODLE_REG_RETAIL_URL` is unset and the user did not supply a site → **run
TC3 only** and record TC1/TC2/TC4 as `skipped: no retail URL`. Do not
substitute a site yourself, and do not hardcode a brand or client name
anywhere in this repo.

## Phase 2 — run each case

For **N = 1, 2, 3, 4**, in order, one at a time:

```bash
noodle init "$RUN/tc<N>"
```

Then work inside `$RUN/tc<N>` and paste the case's prompt **verbatim** from
[The four cases](#the-four-cases) below. Behave exactly as you would for a
stranger's prompt — no extra probing you wouldn't normally do, no carrying
over knowledge from the previous case.

Start a wall clock when you paste. Stop it at the first green run.

Then append one row to `$RUN/results.md`:

```markdown
| case | dev_s | run_s | aic | tool_calls | green | verified | evidence_ok | notes |
|------|-------|-------|-----|------------|-------|----------|-------------|-------|
| tc1  |       |       |     |            |       |          |             |       |
```

| Field | Where it comes from |
|---|---|
| `dev_s` | your wall clock: prompt pasted → first green run. Excludes `run_s`. |
| `run_s` | `run.seconds` in the run payload |
| `aic` | **your own** session counter (`/cost` in Claude Code). The engine cannot see this. Blank if unavailable — never estimate. |
| `tool_calls` | count of Noodle tool/CLI calls you made for this case |
| `green` | `failed == 0` |
| `verified` | `verified == true`. A pass propped up by fuzzy healing or lenient ambiguity is **not** a pass — read `unverified_reasons` / `healing_events`. |
| `evidence_ok` | screenshots landed where [Budgets](#budgets) says for this case |
| `notes` | site-red, re-author lap, anything that cost time |

**Do not** move to case N+1 until case N's row is written.

## Phase 3 — score

A case **passes** when every one of these holds:

1. `dev_s` ≤ 300 (5 min)
2. `aic` < 12, or blank
3. `green` **and** `verified` are both true
4. `evidence_ok` is true

Any case fails → phase 4. All pass → phase 5.

## Phase 4 — breach handling (only if a case failed)

Do these three steps in order. Nothing else.

**Before any retry, preserve the failed attempt** (NOOD_0230): copy the
case workspace's `artifacts/probe_goal.json`, the authored feature/POM and
`.noodle/last_payload.json` to `$RUN/tc<N>-attempt<K>/` — or retry in a
fresh `noodle init` folder. A re-run overwrites all three, and a postmortem
reconstructed from a session transcript is guesswork; the TC4 flake
investigation needed exactly these files and did not have them.

**4a. Triage the tier.** Which case failed?

- **TC3 (tier A)** — Wikipedia is automation-friendly. Red here is an engine
  regression. Go to 4b.
- **TC1 / TC2 / TC4 (tier B)** — live retail. Most retail search and cart
  APIs bot-gate automated browsers (`net::ERR_ABORTED` on the results XHR,
  headed and headless alike). Rule the site out first:
  1. Ask the user to do the steps by hand in a normal browser. Blocked there
     too → record `site-red` in `notes`, treat the case as **not a
     regression**, and stop triaging it.
  2. Otherwise re-run just that case on `main` (`git checkout main && noodle
     update`). Green on main, red on the branch → engine regression, and you
     have the two commits that bracket it. Go to 4b.

**4b. Session scan** — write down what the session actually did, from your
own memory:

```bash
noodle diagnostic log <app> \
  --trigger slow-dev --trigger over-budget \
  --summary "…" --timeline "…" --cause "…" --fixes "…" \
  --duration-min <n> --attempts <n> --agent "<your model>" --agent-cost "<n> AIC"
cp -r <workspace>/diagnostics "$RUN/tc<N>-diagnostics"
```

**4c. Write two files into `$RUN/`, then STOP:**

- **`findings.md`** — the results table, which budget broke, and the engine
  weakness behind it. Name the mechanism (an extra probe pass, a re-author
  lap, a step that stopped matching, a locator that needed healing) — not a
  feeling, not a guess.
- **`plan.md`** — how to fix it, **written against `main`**, not against the
  current branch. A plan pinned to a branch that never merges is wasted.

No engine edits. No commits. No PR. Hand both files to the user.

## Phase 5 — report

Reply with, and only with:

1. The `results.md` table.
2. `VERDICT: PASS` or `VERDICT: REGRESSED — <one line per failed case>`.
3. Paths to `findings.md` / `plan.md` if phase 4 ran.

---

## The four cases

Paste each block verbatim, substituting only `$NOODLE_REG_RETAIL_URL` and
the two `<…>` product titles in TC1.

### TC1 — typeahead suggestion (tier B, live retail)

```
Generate a Noodle test in this workspace.

App under test: the retail store front at $NOODLE_REG_RETAIL_URL

User goal: search for a product by picking a typeahead suggestion.

Steps a human would take:
1. Go to $NOODLE_REG_RETAIL_URL
2. Close any popup that may appear
3. In the search bar, type "Vaccu" — deliberately incomplete
4. A suggestion list appears below the search bar
5. Click the vacuum-cleaner suggestion, worded exactly as the site renders it
6. The results page lists these products:
   | <exact product title 1 from your site> |
   | <exact product title 2 from your site> |

Verify: picking a suggestion runs the search.

Follow AGENTS.md — prerequisites in Background:, step-dictionary sentences
only, validate before running, finish with the Allure + RCA report links and
nothing else.
```

Covers: typeahead, popup tolerance, plain-text assertion on a results grid.

### TC2 — click-navigation across pages (tier B, live retail)

```
1. Go to $NOODLE_REG_RETAIL_URL
2. Close every popup that may appear, including the geolocation prompt
3. Click "Order Status"
4. On the next page, verify you see the two textboxes
   'Please enter your email address' and 'Please enter an order number'
```

Covers: multi-popup dismissal, navigation by link text, two field assertions.

### TC3 — assertion depth with per-assertion evidence (tier A, the gate)

```
Web test.
UI: https://en.wikipedia.org/wiki/Main_Page

Acceptance criteria:
1. Go to the UI
2. Assert the UI page returns 200
3. Assert the page contains the "Welcome to Wikipedia" banner
4. Assert the Main Page contains "From today's featured article"
5. Using the search bar, search for "Steve Jobs"
6. Assert the page contains "Steve Jobs", his born and died dates, and that
   his signature is visible

Note: every assertion must carry its own evidence screenshot.
```

Covers: a status assertion beside UI assertions, search, image assertion,
and the **evidence override** — the one case that asks for a screenshot per
assertion instead of the default.

### TC4 — deliberately underspecified (tier B, live retail)

```
Base URL: $NOODLE_REG_RETAIL_URL
1. Open the website and close all pop-ups on the home page
2. Search for a toy
3. Add it to the cart
Verify: the toy is in the cart, with a screenshot as evidence.
```

"Search for a toy" is vague on purpose. This is the only case measuring
whether the engine copes with a loose prompt instead of a spec. If it starts
demanding exact product names, that is a regression — record it.

---

## Budgets

| Ceiling | Bar | Goal |
|---|---|---|
| Dev time per case (`dev_s`, excludes the run) | **≤ 300 s** | **< 60 s** |
| Agent cost per case | **< 12 AIC** ([llm-performance.md](llm-performance.md) §7) | lower |
| Green | `failed == 0` **and** `verified == true` | — |
| Evidence — TC1, TC2, TC4 | **final assertion only** (the default) | — |
| Evidence — TC3 | **every assertion** (the prompt overrode the default) | — |
| Reports | Allure **and** RCA generated and served automatically, every case | — |

The engine gate's 120 s ceiling measures the engine alone. The 300 s here is
the whole agent-in-the-loop lap.

## What the run folder holds afterwards

```
tmp/agent-regression/<UTC-stamp>/
├── tc1/ … tc4/            fresh workspaces (init + generated feature/POM + reports)
├── results.md             one row per case
├── findings.md            only if a budget broke
├── plan.md                only if a budget broke — against main
└── tc<N>-diagnostics/     only if a session scan ran
```

Two run folders side by side is the whole build-vs-build comparison — same
as `regression_runs/` for the engine gate.

## See also

- [feature-regression.md](feature-regression.md) — the engine gate phase 0 runs
- [agent-playbook.md](agent-playbook.md) — the conventions you are being measured against
- [session-diagnostics.md](session-diagnostics.md) — the session scan and its triggers
- [llm-performance.md](llm-performance.md) — where the AIC bar comes from

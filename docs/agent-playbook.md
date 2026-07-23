# Agent Playbook (robust agentic entry flow)
<!-- Branch: NOOD_0008 -->

**Who this is for:** an AI coding agent (Claude Code, GitHub Copilot, or any
other LLM-driven tool) that gets asked, in plain English, to build and/or run
a Noodle test — *not* a human reading the README, and not `noodle repl`'s
own REPL. This playbook drives the `noodle` CLI directly — no REPL involved.
(`noodle repl`'s former fixed-trigger gap was closed; with `--llm` it now
accepts free-form requests too — see
[design-history.md § Phase 17](design-history.md#phase-17--web-gap-closure-nood_0008).)

> **NOOD_0037 update:** added the mandatory RCA+Allure step (§5), the full
> Gherkin annotation/tag vocabulary and steps-dictionary/POM resolution
> pipeline (§3–4), and an edge-cases section (§8) — closing gaps found
> reviewing this playbook against how the framework actually behaves.
> `.github/copilot-instructions.md` and `CLAUDE.md` both point here as the
> canonical, detailed guide — this is the one file to keep current.

Trigger phrases this covers: "write/generate a test for X", "add test
coverage for the login page", "make sure Y works end to end", "run the
tests", "run the smoke suite" — while a `noodle` install is available (or
installable) in the current project.

Follow these steps in order. Every step maps to something already in the
engine — this doc adds no new code, only a decision procedure.

---

## 0 — North star (NOOD_0089)

A successful Noodle test is, in this order: **deterministic** (same result
every run), **readable as plain English**, **token-cheap** to generate and
maintain, and **honest** about what it verified.

**Terminology — the three nouns (NOOD_0155).** Canonical definitions:
[glossary.md § The three nouns](glossary.md#the-three-nouns--engine-workspace-wok).
The **noodle engine** is the framework itself (this repo / the installed
package); a **noodle workspace** is the test project `noodle init`
scaffolds (templates refreshed by `noodle init --force`); a **noodle wok**
is a capability work area — web, mobile, desktop, performance
([woks.md](woks.md)) — selected per scenario by tags. Route requests by the
noun used: "update the noodle engine" → framework code; "update our noodle
workspace" → tests/config in the workspace; "update our noodle wok mobile"
→ that capability in the engine plus its `unit_tests/woks/mobile/` tests.

**Never silently drop the asked-for check (NOOD_0127).** If the north-star
verification is hard to express, `probe`/`inspect` for its selector and add
a POM entry — do not ship a green test that quietly omits what the user asked
to prove. If it genuinely can't be verified, say so explicitly rather than
delivering a green run that never checked it.

**Output discipline — spend fewer tokens.** Progress updates are max 2
sentences and always state your current intent — what you are doing right
now, so the user knows where things stand ("I'll locate the app package,
then add the feature + env/secrets, validate, run and serve the reports",
"Now running the Noodle validate+run loop", "Run did not go through: URL
is blocked", "Run successful after 3 attempts", "Serving the reports
now"). One update per phase change — don't restate tool output, don't
give per-step progress commentary; quote only failing steps and errors.
If the user says not to output shell commands ("do not output the shell
command"), echo no command line of any kind (no `$ noodle run …`, no
shell/tool invocation text) — the intent update replaces it.
Batch work (validate a whole .feature once, not per line). When you
need framework detail, call the MCP `read_docs` tool (list / by name / grep
query across these docs) instead of guessing, asking, or pasting docs into
your context. Generating or updating a test case specifically:
[llm-performance.md](llm-performance.md) ranks the paths (rule templates →
author+validate over MCP → engine LLM) and the habits that keep generation
fast and token-lean — read it before reaching for `use_llm=True`.

**Steps read like a human using the page.** English grammar order:
navigate → see → act → verify. Prefer the plain interaction verbs the
dictionary already has (click, enter, hover, double-click, wait, see)
before anything clever. Site search is ONE step — `searches for "..."`
resolves the box editable-first and, when it's hidden behind an icon or a
desktop trigger, clicks a visible trigger to reveal it before filling
(NOOD_0106, NOOD_0123). If the one-step form fails, that's a framework bug to
fix or report — never a cue to decompose it into a manual trigger + click +
enter. (A `probe --search` that succeeds while the run fails is that bug's
fingerprint: the two took different visible-vs-hidden boxes.) Pre-requisites
are not test steps:

```gherkin
# WRONG — setup impersonating the test
Given we login to a URL
And we set the resolution to 1920x1080
And then turn off cert-error

# RIGHT — prerequisites live in Background / tags / config
Background:
  Given User is on '{env:BASE_URL}'
```

Resolution → `@viewport:1920x1080` tag or `NOODLE_VIEWPORT`; cert errors are
already ignored by default (`NOODLE_IGNORE_HTTPS_ERRORS`, `@secure_certs` to
re-enable validation for a scenario).

**Popups/overlays** — three cases, in order of preference:
1. Irrelevant to the test → write nothing. The engine auto-dismisses an
   overlay that blocks a click, retries once, and logs a ⚠️ warning that the
   RCA report surfaces even on green runs (`NOODLE_AUTO_DISMISS=false` to
   turn off). After a green run, check those warnings — an auto-dismissed
   popup may itself be the bug, or the thing the test was supposed to touch.
2. Expected but optional → one conditional step: `closes the popup if it
   appears`, or `if 'Loyalty offer' appears, clicks 'No thanks'`. A popup
   that arrives late (seconds after load) gets a window, not a sleep:
   `closes the popup if it appears within 10 seconds` (NOOD_0106).
3. The popup IS the test → assert on it like any other element.

Native browser permission prompts (geolocation, camera, mic, notifications)
are **not** page popups — they are browser chrome, so `closes the popup if it
appears` can never see them. Dismiss one with `the user closes the location
prompt` (denies it), or grant it up front with the `@permissions:…` tag / grant
step when the test needs the capability.

**Custom python scripts** for an app-under-test (data seeding, helpers)
live in that app's `resources/scripts/` and run via the
`runs the script '...'` step — never in the framework repo or workspace root.

**Waiting is the engine's job, not the test's.** Element finds poll up to
`NOODLE_FIND_TIMEOUT` (default 2 min — a ceiling, not a wait: the step
proceeds the instant the element appears). While waiting, the engine
re-scans the DOM for attribute matches (wrong-selector self-heal, hidden
dev-panel elements included) and grants one bounded extension when the
network shows the page is still loading. The ceiling also cuts the other
way: once the page has demonstrably finished (network quiet, DOM stable
past `NOODLE_SETTLE_TIMEOUT`, default 15s), a find that still hasn't
matched stops polling early and moves on to the self-heal chain — a label
that can never resolve on an already-loaded page costs seconds, not the
full budget (`NOODLE_SETTLE_TIMEOUT=0` restores unconditional full-budget
polling). Don't add `waits for N seconds` steps to paper over slow pages.
**`the user sees 'X'` shares this same budget** (NOOD_0116) — a plain
assertion right after a state-changing click (e.g. an SPA full-reload)
polls exactly like a click/fill target would, so it doesn't need a
hand-added `waits until 'X' is visible` in front of it just to survive the
reload.

---

## 1 — Decide where the test lives

| You're working in... | Write tests to |
|---|---|
| This framework's own repo (`noodle/` checkout, contributing to its bundled suites) | `sample_feature_tests/<wok>/<app>/` — the folder level is the **wok** (capability work area: web, mobile, desktop, performance — see [woks.md](woks.md)); see [feature-packages.md](feature-packages.md) |
| Any other project (the common case for agentic entry) | `<workspace>/noodle_tests/<app>/` |

**Default to the external-workspace route.** If you were not explicitly asked
to add to *this* repo's own bundled example suites, a new test case belongs
outside the repo it's testing, in a sibling/dedicated test workspace — not
mixed into the application's own source tree. The `noodle_tests` name is
deliberate for the outside-repo case: a host project may already have its own
`tests/` (pytest, another test framework) — `noodle_tests` can't collide with
it, and it's grep-able as "this is what Noodle owns."

**Scaffolding outside-repo**:

```bash
noodle init <workspace>   # writes noodle.yaml, .env, AGENTS.md, noodle_tests/
```

`noodle_tests/` is the scaffolded tests root (the `tests_dir` key in
`<workspace>/noodle.yaml`; every `noodle` command reads it, so renaming it
there is all it takes). Each app-under-test is one self-contained package —
`noodle_tests/<app>/{features,resources,report}/` — copy the scaffolded
`noodle_tests/sample_app/` template. A single-app run
(`noodle run noodle_tests/<app>`) writes its whole artifacts tree (results,
Allure + RCA reports) into that app's own `report/` folder, and follow-up
commands (`summary`, `rca-report`, `report serve`, MCP `get_last_result`)
find the last run there automatically. Every noodle command can also be
invoked from inside the app dir itself (`cd noodle_tests/<app>` — or an
in-repo suite dir — then `noodle run`/`summary`/`report serve`/`archive`);
over MCP, pass the app dir as `workspace`. If `<workspace>/noodle_tests/` already exists (a second
app being added to an existing workspace), skip straight to Step 2.

### Reuse before you author (NOOD_0163)

One glob — `<workspace>/noodle_tests/**/features/*.feature`, or
`list_tests(query="<app>")` / `noodle list --query <app>` — decides the
cheaper of two routes:

**The workspace already has features** (anything beyond the scaffolded
`sample_app/`) → reuse, don't re-author.

- A feature already covering this app, with a green `report/last_run.json`?
  Copy it, retarget the `{env:}` key, run. No probe, no authoring, no goal.
- No same-app feature, but shared POMs/`functions/` exist? Probe once, then
  author only the POM keys the shared files don't already resolve
  (`noodle validate --resolve` names them).
- Either way, phrase steps the way the sibling features do — `noodle steps
  <kw>` confirms a phrasing is dictionary-valid — so the new test reads like
  the rest of the suite.

**Fresh or scaffold-only workspace** → author from a `goal` (§2), and anchor
each check to the page it was observed on: `after: start` for the landing
page, `after: <action id>` for the page an action lands on, unanchored for
the end state. A multi-page goal with no anchors asserts everything against
the last page and goes red on the first run.

Both routes, search hygiene: scope every `grep`/`rg` to the app package and
exclude `artifacts/`, `archives/`, `report/`, `.noodle/` — a single run's
artifacts outweigh every feature file in the workspace, and a search that can
return >200 KB is mis-scoped. The workspace `AGENTS.md` and the skill card
are already in context; re-reading them buys nothing, `noodle.yaml` is the
one file worth opening. Confirm a path with a glob *before* opening it, and
never batch the open with the glob that would have told you it isn't there.

Two topologies, same engine, same config/secrets/POM resolution either way
— see [feature-packages.md § Two topologies](feature-packages.md#two-topologies).
The one thing that's workspace-sensitive: `noodle step-search`/`noodle repl`'s
suggestion-acceptance flow, which must be pointed at the same `--workspace`
you'll `run` against, or an accepted phrasing silently won't resolve at run
time (see [design-history.md § Phase 23](design-history.md#phase-23--external-workspace-verdict--nood_0030-gap-analysis-closed-out)).

---

## 2 — Scaffold the app package's supporting files

**Fastest path (NOOD_0128): `author_test` / `noodle author --spec`** writes the
whole package in one transaction — app folder, `environments.yaml` (base URL),
POM, feature, and empty placeholders for the secret keys you name — validates
the Gherkin, and rolls back every file it wrote if anything fails. If the
original prompt supplied credential *values* (NOOD_0130), pass them once as
`secret_values={KEY: value}`: they are written ONLY into the gitignored
`<app>_secrets.env`, never echoed back or returned, and never placed in a
feature/POM/`environments.yaml`. Any referenced key left without a value stays
in `missing_secret_keys` and marks the package not-ready (fill it locally).
Prefer it over the manual copy/rename/edit sequence below; the layout it
produces is exactly the contract described here.

### Prompt mode — raw numbered steps (NOOD_0169)

A plain numbered user prompt needs NO translation by the calling agent —
pass it through raw and the engine expands it deterministically (a fixed
verb grammar, no LLM):

```
noodle author --prompt "1. go to <url> 2. search for <term>
3. add to cart 4. verify cart has <term>" --run
```

(MCP: `author_test(prompt=..., run_after_author=True)`.) A URL in the
prompt derives `app_name`/`base_url`/`feature_path`, so prompt + `--run`
is the whole call: expand → probe → compile → run → both reports.

Compilation is three deterministic passes: clause normalization
(bullets/backticks stripped, `Open url`/`Then url`/naked URLs,
parenthetical `(and close all pop ups)` compounds, `Verify:` labels,
screenshot-evidence suffixes), literal translation of complete steps
(stable ids in source order — `search1`, `pick1`, `add1`), then typed
dataflow ONLY for steps incomplete on their own: "add to cart" with no
item consumes the nearest unconsumed search's result set within a
two-step window; "check the cart" bare observes the added item there.
A fully explicit prompt is the `deterministic-fast-path` (zero
inference); every inference carries provenance + supporting clause ids
under `prompt_expansion` — read the assumptions back to the user next
to the evidence. Conflicting context (verify item vs search term, two
equally compatible searches, an unrelated destination) BLOCKS by name
under `conflicts` — the engine never guesses past a contradiction, and
no model is consulted for one. Steps outside the grammar (supported
verbs: go to / open url / search for / click / enter…in / select…from
/ add…to / verify / close popups / screenshot) go to ONE
`NOODLE_MODEL` interpretation call when configured — its output passes
the same review + goal validation, never writes Gherkin — else the
payload returns `needs_interpretation` naming the unresolved clauses.
`planner.state` is the typed terminal verdict (VERIFIED / READY /
EVIDENCE_MISSING / EXTERNAL_FAILURE / RUN_FAILED /
NEEDS_INTERPRETATION / CONTRACT_BLOCKED); on a block, repair the one
`next_action` gap and re-author — never hand-edit the compiled Gherkin
(write_feature refuses it under an intent contract).

### The goal object (NOOD_0137/0161)

`goal` is an **object** — a mapping, never a YAML/JSON string (that's
`pom_content`'s shape) and never `{}`. The minimal valid one, which every
`invalid goal` rejection returns verbatim as `example`:

```yaml
goal:
  scenario: Search returns matching results
  dismissals: [location_prompt, popups]
  actions: [{do: search, term: "<term>"}]
  checks: [{count: results, min: 1}, {any_of: ["<text>", "<alt text>"]}]
```

The `<…>` values are placeholders — the example teaches the shape, never a
site, a product, or a vocabulary. Any app, any domain, any action mix.

Every key: `scenario` (required), `navigation` (every requested URL, in
order — one `{env:}` Given each), `actions`
(`search|suggest|pick|add_to|click|enter|select`, each with `id?` +
`term`/`target`/`value`/`option`), `checks`
(`see|count|any_of|field|item_in_destination`, plus `min?`, `value?`,
`expected_from?`, `evidence: screenshot?`, `after?`), `dismissals`
(`popups|location_prompt|notifications_prompt`), `probe: {discover}`, and
`allow_no_assertion`. Loose dismissal wording is canonicalized for you and
echoed back as `goal_normalized`; anything else that can't be proven blocks
rather than compiling — see §5's verification contract.

**`after` is the page binding** (NOOD_0163). A check with no `after` asserts
the END state, after every action (NOOD_0158); `after: <action id>` asserts on
the page that action lands on; `after: start` asserts on the landing page,
before anything is clicked — the one page no action id can name. Bind every
check in a goal that spans pages, or a landing-page string gets asserted
against the page the flow ended on. Two `any_of` checks no longer share a
POM key either: each distinct locator gets its own (`result titles`,
`result titles 2`, …), so the second check can't inherit the first's
selector.

Every app-under-test is a self-contained folder — the full contract is in
[feature-packages.md](feature-packages.md#the-package-contract). Create what
the scenario actually needs, nothing more:

```
<tests_dir>/<app>/
  features/
    *.feature                       # always
  resources/
    <app>_secrets.env               # gitignored working file — credentials the scenarios reference; scaffold it, fill in real values (NOOD_0118: no committed .example — that's an init-only convention)
    environments.yaml               # base URL for {env:APP} style refs — add if the .feature uses one
                                    # (<app>_environments.yaml also accepted; never the workspace-root .env)
    payloads/ data/                 # test data / payloads — only if a step needs a fixture
    pageobjects/
      *_pom.yaml                    # selector overrides — only for elements text/role can't find
```

Prefix every credential/config key with the app name (`BB_USER`, not
`USERNAME`) — `os.environ` is one flat namespace across every package in a
run; a generic key collides silently with another app's ([the collision
rule](feature-packages.md#the-collision-rule)).

A `*_pom.yaml` file with no `match:` block is scoped by default to its own
filename stem, not applied everywhere — if you're writing one meant to apply
across every page (e.g. `shared_pom.yaml`), add an explicit `match: {}` or
its keys will silently never resolve. See
[feature-packages.md § Per-page POM files need `match:`](feature-packages.md#per-page-pom-files-need-match).
`noodle validate --resolve` lints for this trap (warn-only).

---

## 3 — Write the `.feature` file: Gherkin shape and annotations

### Structure

```gherkin
@web @smoke
Feature: Login
  Covers: valid login, invalid username, invalid password.

  Background:
    Given User is on "{env:BUSTERBLOCK}"

  @smoke
  Scenario: Valid user logs in successfully
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  Scenario Outline: Multiple users log in -- @1.<n>
    When User enters <username> in the username field
    And User enters <password> in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

    Examples:
      | username     | password    |
      | tape_tanya   | Rewind2#    |
      | vhs_victor   | VCR_3way    |
```

Conventions actually enforced/expected by this codebase:

- **Feature-level tags** (top of file, above `Feature:`) set defaults for
  every scenario in the file — the platform (`@web`/`@api`/`@appium`) and any
  org-wide marker (`@smoke`, `@capability`). All tests run headless by
  default; add `@headed` (feature- or scenario-level, or `--headed` on the
  CLI) to force a visible browser. **Scenario-level tags** narrow or override
  for just that scenario.
- A one-line `Covers:` comment (or a `#` comment block) under `Feature:`
  describing what the file demonstrates is this repo's own convention, not
  enforced by the engine — but keep it, it's what a human or another agent
  reads first to decide if the file already covers what they're about to add.
- Quoting: literal strings are double-quoted (`"VHS Catalog"`); parameter
  refs (`{env:X}`, `{var:X}`, `{pom:X}`) can appear bare or inside quotes —
  both resolve. Scenario Outline placeholders (`<username>`) are never
  quoted.
- **Never hardcode a real credential in a `.feature` file.** Base URLs come
  from `{env:APP}` (→ `environments.yaml`), credentials from `{env:APP_USER}`
  style refs (→ `secrets.env` — gitignored). `environments.yaml` keys
  uppercase automatically — `myapp:` → `{env:MYAPP}`, no separate declaration
  needed.

### Parameter syntax (NOOD_0033 — unified, current)

| Form | Source | Example |
|---|---|---|
| `{env:NAME}` | Real OS env → `.env` → `secrets.env` → per-app `resources/[<app>_]secrets.env`/`[<app>_]environments.yaml` → `environments.yaml` | `{env:BUSTERBLOCK}`, `{env:BB_USER}` |
| `{var:NAME}` | Captured during this run only (set/store/extract/function-result) — never config | `{var:DEVICE_ID}` |
| `{pom:NAME}` | Explicit POM-key resolver — forces a `pom.yaml` lookup even when the plain text would otherwise resolve some other way | `{pom:login button}` |
| `[NAME]`, `` `NAME` `` | **Legacy** — still resolve (env / var respectively) with a one-time deprecation warning per ref. Don't use in new tests. | — |

### Tag vocabulary

Functional tags — each one changes engine behavior, confirmed by reading
`noodle/hooks.py`/`noodle/preconditions.py`/`noodle/steps/catch_all.py`, not
just this list:

| Tag | Effect |
|---|---|
| `@web` | Playwright browser (default engine) |
| `@api` | Pure REST scenario — no browser launched at all |
| `@appium` | Drive a device/emulator via Appium (needs the `[mobile]` extra + a running Appium server) |
| `@android` / `@ios` / `@windows` / `@mac` | Appium **platform** tags — imply `@appium` and fill in default capabilities for that platform (`NOODLE_<PLATFORM>_APP` names the app) |
| `@mobile` (+ `@iphone` / `@android`) | Browser-level **device emulation** (viewport/UA), distinct from the Appium platform tags above — no real device needed |
| `@visual` | Route through the OpenCV/desktop agent instead of Playwright |
| `@perf` | Performance wok (NOOD_0155) — browserless like `@api`; the built-in load generator runs `runs a load test on ...` steps and latency/error/throughput assertions ([woks.md](woks.md)) |

> **Wok tag on generated tests (NOOD_0155):** engine write paths
> (`create_test`, `author_test`, `write_feature`) auto-add the routing tag —
> explicit request wins, then step signals, then task wording, else `@web`
> ([woks.md § Automatic wok tagging](woks.md#automatic-wok-tagging-on-generation)).
> If you author `feature_content` yourself, tag it yourself — a tag you set
> is never overridden.
| `@headed` | Force browser visible for this scenario (headless is the default; `--headed` does the same from the CLI) |
| `@firefox` / `@webkit` / `@safari` / `@edge` | Browser engine (default: chromium). `@safari` is Playwright's WebKit engine under its friendlier name; `@edge` launches Chromium through the locally-installed Microsoft Edge (NOOD_0052) |
| `@strict` | Ambiguous locators **fail** instead of silently using the first DOM match (lenient default) |
| `@viewport:1920x1080` | Set browser viewport for this scenario |
| `@geo:51.5,-0.12` + `@permissions:geolocation` | Geolocation emulation + the browser permission grant it needs |
| `@locale:fr-FR` / `@timezone:America/New_York` / `@color_scheme:dark` | Browser locale / timezone / prefers-color-scheme emulation |
| `@offline` | Start the browser context offline |
| `@ocr_fallback` | Opt in to coordinate/OCR locator fallback (closed shadow DOM / canvas) |
| `@terminal` | Needs the OCR engine (tesseract) — **auto-skips** (not fails) if it isn't installed |
| `@live` | Hits a real external site — **auto-skips** unless `NOODLE_RUN_LIVE=1` |
| `@no_retry` | Opt this scenario out of the default auto-retry-on-failure |
| `@retry_step` | Retry each failed *step* once in place (for flaky individual steps, not the whole scenario) |
| `@soft` | Collect assertion failures through the whole scenario instead of stopping at the first; fails at the end if any were collected |
| `@quarantine` | Failure is still reported, but doesn't fail the build — for a *confirmed, understood* external bug, not a shrug |
| `@slow` | 500ms delay between actions (debugging) |
| `@record_video` | Record a `.webm`, saved to `artifacts/videos/` |
| `@precondition:NAME` | Runs `NAME`'s `setup:` HTTP calls (from `preconditions.yaml`) before the scenario, `teardown:` after — even on failure |
| `@page:NAME` | Pin the active POM page context when the URL alone can't identify which page's `pageobjects/*_pom.yaml` applies |
| `@audit` | Extra audit log line per scenario (this repo's own `sample_feature_tests/steps/custom_hooks.py` convention, not core engine) |

Organizational-only tags (no runtime effect, just categorization/CI-sharding
signals — don't assume they gate anything):

- `@smoke` — a filtering convention (`--tag smoke`), not auto-checked by hooks.
- `@capability` — this repo's own "this scenario demonstrates a framework
  capability" marker on its bundled example suites.
- `@manual` — tells `scripts/list_features.py` to exclude a file from the CI
  sharding matrix. Unlike `@live`, there's **no engine-level skip** — if you
  run it directly it runs.

---

## 4 — Steps dictionary fundamentals: annotation → POM → step

Every step line, whatever its exact wording, is handed to one regex
catch-all (`noodle/steps/catch_all.py` → `execute_step()`). There's no
per-step registration to keep in sync, which also means the resolution order
matters — this is the actual pipeline, in order:

1. **Pattern match** (`noodle/resolver/patterns.py`) — a step's plain-English
   shape ("User clicks X", "User enters X in the Y field", ...) is matched
   against a curated regex table. A match extracts an **action type** plus
   a **locator string** (e.g. `clicks "Login"` → `{type: click, locator:
   "Login"}`). This is free and deterministic — no model call.
2. **Locator resolution** (`noodle/agents/web/locator.py`) — the extracted
   locator string is resolved to a real element, in this order:
   - Accessible name / role / visible text (no config needed — most buttons
     and links resolve here for free).
   - **`pom.yaml` lookup** — the locator string, lowercased, is looked up as
     a key in the nearest applicable `pageobjects/*_pom.yaml` (scoped by
     `match:`), then `resources/pom.yaml`, then the ancestor `<tests_dir>/pom.yaml` (scaffolded as `noodle_tests/pom.yaml`).
     This is *why* a step written as `User clicks "Sweet Alert"` only needs a
     POM entry when the plain text is ambiguous or has no accessible name —
     the key you add is exactly the string the step uses.
     **The key is the extracted locator string, not the full step text** —
     the step patterns strip trailing nouns (field/box/input/button/link)
     before the lookup. `User enters '{env:ASSET_TAG}' in the asset
     tag field` looks up `asset tag`, so a key authored as
     `asset tag field:` silently never matches; author it as
     `asset tag:`. (`noodle validate --resolve` warns on this exact
     shape — NOOD_0109.)
   - Self-heal / OCR / vision fallback, in that order, only if the above
     misses and the relevant opt-in (`@ocr_fallback`, a vision-capable
     `NOODLE_MODEL`) is configured. Two heals worth knowing when generating
     tests (NOOD_0109): class tokens prefixed `e2e_`/`e2e-`, `qa-`, `test-`,
     `cy-`, `pw-`, `automation-`, `hook-`, `tid-` or `sel-` count as strong
     identity signals in the DOM scan — an app whose only automation hook is
     `class="e2e_dev-panel_device-type_dropdown"` needs no POM entry for
     that element — and the auth verbs login/logout/register self-heal to
     their common real-world labels (Sign in / Log in / Sign out / Sign up)
     when the literal step text matches nothing.
3. **`{pom:name}` explicit syntax** bypasses step 2's implicit lowercase-key
   lookup and forces a direct `pom.yaml` key lookup — use it when a step's
   own wording shouldn't double as the POM key (e.g. the step reads naturally
   but the element needs a different, more specific key).
4. **`@page:NAME`** solves a different problem than `{pom:name}` — it picks
   *which page's* `pageobjects/*_pom.yaml` file is in scope, for cases where
   the current URL alone doesn't tell the engine which page's POM file
   should apply (e.g. a modal/overlay that doesn't change the URL).
5. **LLM fallback** (`noodle/resolver/step_resolver.py`) — only reached if
   pattern matching in step 1 found nothing at all, and only if `NOODLE_MODEL`
   is set. Converts the step's plain English into the same `{type, locator}`
   JSON shape, then re-enters step 2 for locator resolution. **Nothing
   auto-detects a local model** — a running Ollama server does *not* get
   picked up unless `NOODLE_MODEL=ollama/<tag>` is set explicitly (see
   [llm-setup.md](llm-setup.md)'s addendum).

**Before inventing new step phrasing, check what already exists:**

- Full list with examples: [steps_dictionary.md](steps_dictionary.md)
- Curated cheat-sheet (same list the engine's own `--llm` generator is
  prompted with): `noodle/repl/prompts.py` → `STEP_VOCABULARY`
- Fast lookup: `noodle steps "<keyword>" ["<keyword>" …]` — several
  keywords in ONE call (union of hits, NOOD_0169; a reviewed session paid
  ten calls for ten words) — or `noodle step-search "<plain
  English description>" --workspace <workspace>` — finds the closest existing
  step, or drafts a new one if there's truly no match (`--accept` to save it
  non-interactively).

Before trusting a hand-written or generated file, validate it against the
pattern table without touching a browser:

```bash
noodle validate <tests_dir>/<app>/features --workspace <workspace> --resolve
```

Any step reported as unmatched needs rephrasing to a listed shape, or the run
will either fail loudly (no LLM configured — the default) or silently cost a
model call (LLM configured).

### SPA field notes (NOOD_0110) — the five ways agents stall on Angular/React sites

Observed driving a weaker model against a real Angular app; each has a
one-shot recipe, so apply them up front instead of rediscovering them over
ten red runs:

0. **Probe before you author (NOOD_0113).** `probe_page(url)` (`noodle
   probe <url>`) opens the page once headless and returns every actionable
   control — hidden trigger zones included — with a ready selector, which
   ones need a POM entry (paste-ready `pom_yaml`), a suggested step each,
   and the exact heading texts to copy into assertions. On any SPA or
   unfamiliar page, do this before writing the feature: it turns items 1–5
   below from failure-time discoveries into authoring-time inputs, and
   replaces the author-blind → run → RCA → fix-POM → re-run lap entirely.
   The template exemption is NARROW (NOOD_0136): skip the probe only when
   every control the test needs is a standard visible one (a plain
   login/search form). Any mention of a dev/hidden panel, configuration
   step, custom control, or an Angular/Flutter/SPA shell forces one
   bundled probe — with every reveal click, `open_native_controls=True`,
   and the search term folded in — before any selector is written. A
   login-shaped page with a config gate behind it is NOT template-shaped.
   The probe also closes popups the way the run-time engine would and
   records permission prompts (geolocation/notifications) the page raised,
   reporting each as a ready-made step, and ends with a paste-ready
   **scenario skeleton** (navigation → permission/popup closes → search →
   results floor) — start from that skeleton and add goal assertions from
   the exact-texts list instead of composing the opening by hand
   (NOOD_0137). `next_pages` lists same-origin links — probe the page a
   scenario navigates to in the same call (`probe_page("url1 url2")`). Controls
   flagged `⚠ caption is attribute-only` (NOOD_0115) carry their label
   only in alt/aria-label/title — paste the pre-emitted POM entry before
   writing any "should see"-style assertion on them.
0.1. **Content hidden behind a click? Reveal it in the same probe
   (NOOD_0116).** A settings panel, tab, dropdown, or modal that only
   renders after a trigger click is invisible to a single-load probe. Pass
   `probe_page(url, click=["trigger name"])` (`noodle probe <url> --click
   "trigger name"`) and the probe clicks that control for real, settles,
   and appends what it revealed under a separate `revealed` section — no
   hand-written Playwright script needed to see real selectors for gated
   controls. Repeatable, executed in order; a target that can't be
   resolved lands in a `⚠` warning without failing the probe. Only name
   reveal controls (panels/tabs/dropdown triggers), never a
   state-mutating button — the click is real, not simulated.
0.2. **Search test? Probe the results page in the same call (NOOD_0117).**
   `probe_page(url, search="term")` (`noodle probe <url> --search "term"`)
   performs the site search (editable-first box detection, icon-opened
   boxes included) and summarizes the results page: new controls, the
   "NN results" summary element with a ready POM entry, and the
   summary-count assertion. Prefer that assertion — `the number in
   'results summary' should be at least N` — over counting rendered cards
   (`should see at least N '<x>' items`): rendered counts are lazy-load-
   and headless-dependent (a real grid rendered 52 headless vs 92 headed
   for the same query). Token diet for any probe: `--compact` (MCP
   default) returns only needs-POM controls + POM YAML + steps +
   headings, caps each list at 25 by default (a facet-heavy results page
   won't flood you), drops consent-widget noise, and groups image-tile
   alt/title captions into their own author-ready slice; pass
   `--max-controls N` to widen the cap, `--section pom|controls|steps|
   headings` for one slice. Need ONE control the cap hid (a card's
   "Add to cart", a buried field)? `--find "<text>"` (MCP
   `probe_page(find=...)`, NOOD_0169) returns every control, result item,
   and card action matching the text — pre-cap, case/space-insensitive —
   with selector, step, and POM line each. That replaces grepping
   `.noodle/last_payload.json` or any other spill file: noodle output is
   already payload-bounded; never pipe it through `grep`/`head`/`jq`.
0.2b. **Suggestion/typeahead flow? Capture it in the same probe
   (NOOD_0141).** `probe_page(url, suggest="partial term")` (`noodle probe
   <url> --suggest "partial term"`) types the term per-character into the
   search box and returns the typeahead's EXACT suggestion strings in
   order, the navigating selector per row, a flag on no-op icon
   sub-elements, and copy-ready steps. Author with those two steps only —
   `When User selects the "<suggestion>" suggestion for "<partial>"` and
   `Then the search suggestions for "<partial>" include "<fragment>"` —
   never a hand-rolled click on a suggestion selector, and keep the
   partial/misspelled term exactly as the prompt spells it (the
   misspelling IS the test). Need the results page too? Add
   `follow="<expected suggestion>"` (`--follow`, NOOD_0142): the probe
   clicks the captured row matching that text — containment first, then
   fuzzy, so your correctly-spelled ask still finds a site's misspelled
   row — and summarizes the landed page exactly like `--search`, results
   summary included; the emitted steps carry the row's EXACT text.
   Verifying products/content on the landed page? `expect=["text", …]`
   (`--expect "text"`, repeatable) prints one FOUND/NOT-FOUND verdict per
   text at the TOP of the output and turns hits into ready
   `User should see` steps — never dump controls just to confirm a
   product name. One probe = type → suggestions → pick → results →
   verified texts; probing per stage, or re-running a probe to grep its
   output, pays a full browser launch each time for nothing (task blocks
   print first as of NOOD_0142). Combine with `search="term"` only for a
   submit flow; `--suggest` runs first (the submit navigates away).
0.3. **Page behind an auth/config gate or a multi-step transaction? Probe
   through it with `--do` (NOOD_0144).** `--do "enter <value> in <field>"
   / "select <option> from <dropdown>" / "click <name>"` (repeatable,
   ordered, run after any `--click` reveals) executes the REAL
   transaction — fill the config field, pick the option, press Save — and
   snapshots the delta after every action, so the post-save state
   ("Save → login appears") is discovered in the same single probe.
   `{env:KEY}` inside a value resolves engine-side from the workspace env
   chain, so credentials cross the gate without ever appearing in the
   transcript or the payload. Only when even `--do` can't reach the state
   (e.g. an external OAuth redirect, a hardware prompt): budget ONE
   exploratory run (NOOD_0127) — reach the page once, read its
   screenshot/DOM once, and author every post-gate assertion from the real
   strings in a single pass. One probe (or one slow run) replaces several.
0.4. **Trigger names unknown, frames, shadow DOM, Flutter? One probe still
   covers it (NOOD_0136).** `probe_page(url, discover=True)` (`noodle probe
   <url> --discover`) clicks bounded generic disclosure candidates —
   hidden trigger zones, `aria-expanded=false`, tabs/menus,
   panel/settings/config-named buttons, never a state-mutating name —
   records each delta under `revealed` (`discovered: true`), reverts
   between branches, and returns a `discovery` trace naming every skipped
   candidate. In compact output a discovered block is a bounded summary
   (first 8 controls + `+N more`), not a catalog — need one of those
   panels for the goal? Re-probe with `--click "<its trigger>"` for the
   full block (NOOD_0137). Open shadow roots are walked automatically (selectors still
   work — Playwright pierces them; `scope` names the host chain). Every
   iframe becomes its own `frames` block carrying the
   `switches to the "<name>" frame` step to precede its controls — POM
   entries are page-global and can NEVER reach into a frame, so in-frame
   controls resolve by readable name only. Custom listbox options are
   scrolled until stable (virtualized lists), suggested selectors are
   proven unique in their scope (`unique: false` + a ⚠ means narrow it
   first), and a canvas-only or Flutter page without semantics returns
   `coverage: visual_only` with POM output suppressed — author visual/OCR
   steps, never selectors, from such a page (Flutter's accessibility
   placeholder is activated automatically when present). Trust
   `author_ready`: false means fix the named ⚠ before pasting anything.
   Native apps: `probe_app(platform)` (`noodle probe-app <platform>`)
   snapshots the Appium accessibility tree once — same contract, nothing
   tapped (see docs/native-apps.md).
0.5. **One locator still mystifying you? Inspect it (NOOD_0115).**
   `inspect_locator(url, text)` (`noodle inspect <url> "<text>"`) runs
   `find()`'s exact resolution against the live page and lists every
   candidate by source (text node / alt / aria-label / title / POM / DOM
   scan) with visibility, plus which element `find()` picks. One command
   instead of a throwaway Playwright script when a step times out on an
   element that is clearly there, or resolves to the wrong one.
1. **Dictionary-valid ≠ resolvable.** A step can validate perfectly and
   still name a label the page never renders. Step nouns must be the exact
   visible label or a POM key; unstable/duplicated labels get pinned once
   with `{pom:key}`.
2. **Custom widgets stay one step.** `selects 'X' from the Y dropdown`
   handles non-native dropdowns itself (non-`<select>` trigger → engine
   clicks it open and picks the option by role/text). Don't decompose into
   click-chains; only an unnamed custom *trigger* needs a POM entry.
3. **In-DOM ≠ interactable.** `find()` polls for existence and every action
   auto-waits for actionability (visible, enabled, stable) — sleeps are
   never the fix. `the user sees 'X'` already polls the full find budget
   (NOOD_0116), so it survives a state-changing reload on its own; reach for
   an explicit `waits until 'X' appears` / `disappears` only when the step
   itself needs to name the transition, not to work around a race.
4. **Pointer interception is handled.** A visible control blocked by a
   cookie bar/spinner/overlay is auto-dismissed and retried once
   (`NOODLE_AUTO_DISMISS`), with a ⚠️ RCA warning — write no step for it.
5. **Harden selectors in one pass, not iteratively.** After the first green
   run, read the RCA healing warnings and promote every healed or
   visible-disambiguated locator into a POM entry; the next run should
   resolve with zero heals. That single pass replaces the slow
   "works-once → tighten → rerun" loop.

---

## 5 — Run it, then **always** generate and serve both reports

```bash
noodle run --workspace <workspace> <tests_dir>/<app>/features/<name>.feature --headless
noodle run --workspace <workspace> --tag smoke
noodle list     --workspace <workspace>     # sanity-check scenario discovery first
```

`--workspace` (`-w`) points the engine at the right `noodle.yaml`/`.env` and
writes `artifacts/` there; omit it only when already `cd`'d into the
workspace. `noodle run` on a single file only runs that file — full path
matching, not filename matching. **`--headless` is the expected default for
any unattended/CI-style invocation** — only drop it for a human explicitly
watching the browser.

**Quiet runs (NOOD_0117).** Agent/CI runs never benefit from the live
behave stream — it's the heaviest blob a driving agent re-bills on every
later call. `noodle run --quiet` writes it to `<artifacts>/run.log` and
prints only the summary; it's automatic when stdout isn't a TTY, and
`NOODLE_QUIET=0` forces the stream back for a human watching.

### Green is not automatically verified (NOOD_0156)

A run result (CLI `--json`, `run_and_report`, `get_last_result`,
`artifacts/last_run.json`) now carries **`verified`**, `unverified_reasons`,
`warnings`, `healing_events`, and `evidence` alongside the pass/fail counts.
`verified: false` on a green run means a passing step leaned on a fuzzy
resolution — DOM-attribute-scan or partial-text healing, a vision/OCR
fallback, a lenient ambiguous-locator `.first`, or an evidence screenshot
with no fresh exact match. That is how a scenario once reported 8 passing
steps against a provably empty cart.

The success contract is therefore **`failed == 0` AND `verified: true`** —
an exit code alone is neither. Before reporting success:

1. Read `unverified_reasons` / `healing_events` / `warnings`; a healed or
   ambiguous green is an anomaly to report, not a pass.
2. **Open the evidence/requested screenshot and check its contents** — a
   filename never proves what the image shows.
3. Never author assertion text the probe didn't observe ("Added to cart"):
   assert durable state — a count delta, the same item in the destination —
   not a transient toast. Transient messages may supplement a durable check,
   never replace it.
4. `author_ready: false` (probe) and `ready: false` (author/goal) are
   STOPs: fix the named gap and re-probe/re-author. Never route around a
   blocked goal by hand-writing `feature_content` — that bypass is exactly
   how unproven flows get authored. For an unfamiliar transactional flow,
   make the one probe a full-flow `--do` transaction (search → pick →
   add/save) so every later step is evidence-backed.
5. **Generic search-select-mutate-verify intents (NOOD_0156)** compile
   from a goal, not from hand-picked steps: `{do: pick}` (after the
   search) binds "any matching result" to ONE probe-observed caption —
   a *bound target* with provenance, not a new step — and the check
   `{item_in_destination: "<destination>", expected_from: <pick id>,
   evidence: screenshot}` asserts that SAME caption in the destination
   — any collection the app moves items into (cart, wishlist, queue,
   folder) — with the
   `( take a screenshot )` marker on the verification step. A
   destination count can never satisfy item identity; an extra action without probe
   provenance blocks instead of compiling; a standalone screenshot step
   is never authored. `author_test` returns `intent_verified: true` only
   on this goal path — manual `feature_content` is always
   `intent_verified: false` (its `ready: true` is syntax-only).
6. After a red run whose failing step is a postcondition of an add/save
   action, read the compact RCA first: a `mutation-failed` verdict means
   the engine correlated the run's own network capture (aborted request,
   or a non-success status, redacted to method + path) — fix the action;
   never weaken the assertion or guess a repair step.
7. A green-with-warnings/healing outcome belongs in the session diagnostic
   (§7.5) — log the anomaly, don't bury it.

Assertions themselves are hardened engine-side: literal `should see` checks
never resolve through the DOM-attribute scan (exact text/accessible caption
or an explicit `{pom:...}` only), and a state-changing action whose target
only fuzzy-matches a navigation control fails instead of "healing" into it.
Human-authored lenient suites keep their exit-code behavior; `verified`
is the honest layer on top.

### Iterate to green while developing (NOOD_0094)

A freshly generated step or scenario rarely runs clean first try — a selector
misses, a locator is ambiguous, the find budget times out. While *developing*
the test, close that loop yourself instead of handing back a red run:

```
generate → validate → run → (mechanical fail?) → fix cause → run → …
```

- **Fix the cause, re-run.** Element-not-found / ambiguous-locator /
  find-timeout are authoring problems: tighten the POM selector, fix the step
  phrasing, correct the `{env:}`/`{var:}` value — §7 maps failure kinds to
  fixes. Element-not-found on a page you never probed? `probe_page` it now
  (NOOD_0113) — one probe names the real selector and every other control
  on the page, instead of guessing one fix per red run.
- **First locator/state failure → reproduce, don't re-guess (NOOD_0144).**
  When the failing step sits mid-flow (after fills/saves/navigation),
  re-running with one guessed fix per lap is the expensive anti-pattern:
  reproduce the EXACT failing state once — `noodle probe <url> --do "<each
  action up to the failure>"` — and re-author every downstream step from
  that snapshot's real controls in a single pass. One probe, one
  re-author, one verification run.
- **Pass `--retries 0` (`retries=0` over MCP) for every run in this loop.**
  The engine's own default retry (1) silently re-runs a failing scenario
  before returning, doubling wall-clock time on every failed fix→rerun cycle
  for no benefit until the test is stable — turn retries back up (or drop
  the flag) once it's green.
- **Cap the loop** at `NOODLE_DEV_FIX_ATTEMPTS` (default 10, `config.dev_fix_attempts()`).
  The cap is a ceiling, not a budget to spend: every re-run must carry a
  cause-backed fix (RCA verdict → repair, or a `--do` reproduction →
  re-author) — ten blind laps is ten times the cost of one reproduction.
  If it's still red after the cap, **stop** and tell the user the test is
  flaky — what failed on each attempt, what you changed, and the RCA
  verdict — rather than burning more attempts.
- **This is not green-forcing.** A genuine app-regression, a confirmed
  external-site bug, or a real assertion mismatch is root-caused (§7) or
  `@quarantine`d — never looped on. Looping to hide a real failure is the
  anti-pattern §8 calls out; this loop only makes the *mechanics* work.

**Whenever a run happens — whether you triggered it or the user asked you
to "run the tests" — always follow up with both of these, not just one:**

```bash
# One server, both reports (NOOD_0082) — hosts the Allure report AND rca.html
# on localhost, rebuilding either from allure-results/ first if missing:
noodle report serve --workspace <workspace>
#   → http://127.0.0.1:8000/allure-report/index.html  +  http://127.0.0.1:8000/rca.html

# Driving the CLI as an agent? Add --background (NOOD_0104): the server
# detaches, the command prints both URLs and EXITS — no hung tool call, no
# curl-probing the port. Stop it later with `noodle report stop`.
noodle report serve --workspace <workspace> --background
```

Without `--background` the command serves in the foreground and blocks until
Ctrl+C — that's for humans in a terminal. Don't background it yourself with
`&`/a shell tool's background mode and then poll the port to see whether it
worked: `--background` (or the `serve_report` MCP tool) already returns the
URLs only after a successful bind, and registers the server so `noodle
report stop` can find it.

Both reports carry per-step **evidence screenshots** (NOOD_0153): by default
the final step of every passing web scenario ships a viewport-only JPEG with
the asserted element boxed in green — proof the test did what it claims. A
tester requests more with a trailing `( take a screenshot )` marker on any
step, the `@evidence`/`@no_evidence` tags, or `NOODLE_EVIDENCE=all|last|off`
(see docs/steps_dictionary.md § Evidence screenshots). In rca.md the
Evidence section lists file paths only (token-lean — never inline pixels);
rca.html inlines bounded thumbnails. Don't screenshot the page yourself to
prove a run worked — point at the reports' evidence instead.

Every run (single-process *and* `--parallel`, pass or fail) auto-writes all
three — the Allure report, `rca.md` and `rca.html` — into the run's own
`reports/` folder: `<app>/report/reports/` for a single-app run,
`artifacts/reports/` for a workspace-wide one (a green run renders the "no
failures" confirmation page). Follow-up commands and MCP tools find the
last run's root automatically (`.noodle/last_run_root`). So after a run you only need to *host* them — `noodle
report serve`, or the `serve_report` MCP tool, which returns the URLs
without blocking. `noodle report generate` rebuilds both from existing
results if you need to regenerate after the fact. The `--llm` RCA narrative
and `--propose-fix` remain opt-in via `noodle rca-report`.

Older runs: `noodle report list` shows the live report plus the timestamped
`archives/artifacts_<stamp>.zip` snapshots `noodle archive` writes on demand
(NOOD_0093 — runs no longer auto-archive); `noodle report serve <stamp>`
extracts and re-hosts one (MCP: `list_reports`, then `serve_report` with
`report_dir`).

Never host the reports any other way — not `allure serve`, not `python -m
http.server`, not a raw `file://` open of `index.html`. Only `noodle report
serve` (or the `serve_report` MCP tool) sets up the real HTTP origin Allure's
SPA needs (it renders blank otherwise) *and* co-hosts `rca.html` on the same
origin. `rca.html` alone is self-contained (works from `file://`), but the
served URL is the consistent thing to hand over.

**Once the run and both reports have been delivered, tear down what you
spawned for it — except the report server, which is the deliverable.**
NOOD_0161: every hosting path is now a detached child that survives your
process, on a port reused run after run, so the link you hand over keeps
working and keeps being the *same* link. Stopping it the moment you finish
the turn is what produces "this site can't be reached" when the user finally
clicks — stop it (`stop_report_server`, `noodle report stop`) only when they
say they're done. Nothing else here is auto-reaped, and none of it is the
deliverable: a `noodle-mcp` process you drove directly (e.g. via a stdio client script
against a worktree's own `.venv/bin/noodle-mcp`, rather than the host's
already-registered server) exits only when its parent process does, and a
local dev server for the app-under-test (started for the run, not by the
user for other work) has no lifecycle tie to the test run at all. Before
ending the session/turn: `ps aux` for any noodle-mcp
process you started, kill it, and stop any app server you started
too — but never kill a process you didn't start this session (an
already-registered MCP server, another session's report server, a
long-lived dev server the user was already running) without confirming
first.

---

## 6 — Report back

Tell the user, concretely:

- Which files you wrote (feature + POM + any env/resources), and their
  paths relative to `<workspace>`.
- The exact `noodle run ...` command that runs just what you added.
- Whether `noodle validate --resolve` found any unmatched steps, and what
  you did about them.
- Pass/fail counts **and the `verified` flag** — success is `failed == 0`
  AND `verified: true`; a green with `verified: false` is reported as
  unverified, with its `unverified_reasons`/`healing_events` (NOOD_0156).
- For any failure: the RCA's heuristic verdict
  (category + confidence), not just "it failed."
- Where the Allure report and RCA both ended up, and how to reopen either
  later.
- **What the run cost (NOOD_0080):** relay the `llm_cost` block from the
  run result (`run_test`/`run_and_report`/`get_last_result` over MCP, or
  `noodle cost --workspace <workspace>` on the CLI) — calls, in/out tokens,
  dollars, split by purpose (steps/@visual vs RCA vision). "LLM cost: none"
  is a valid answer (pattern-matched runs make zero model calls). Note that
  this covers only Noodle's own `NOODLE_MODEL` calls — your *own* token
  spend as the driving agent is billed to your subscription and invisible
  to Noodle (Claude Code: `/cost`; Copilot: seat-based, no per-token API).

---

## 7 — Fixing a failure: root-cause before you patch

When a test fails, resist patching the assertion to match whatever happened.
Take the evidence in cheapest-first order (NOOD_0117): (1) the RCA verdict —
`get_rca()` / `noodle rca-report --compact` returns verdict + failing step +
suggested fix in a few lines of text; (2) the failure message already in the
quiet-run summary; (3) only if still unexplained, the failure screenshot
(vision tokens are ~10× text) or the network capture. Never dump the network
capture to explain a timing/locator failure — the verdict already names it.
Read the RCA verdict first — `app-regression`/`test-data`/`locator-rot`/
`unknown`, each with a confidence and a recommendation — then match the fix
to the actual cause:

- **Stale/rotated content** (a color variant, a shuffled list) → assert the
  stable substring, not the exact volatile string.
- **Shared external resource became read-only/rate-limited** → make the
  scenario create and act on its own data, don't depend on a fixed shared
  ID (see `sample_feature_tests/api/features/rest_write.feature`'s fix history for the
  pattern).
- **Ambiguous locator, lenient mode picked the wrong match** → add a scoped
  `pom.yaml` entry (real `id`/`data-test` attribute if the page under test
  has one) rather than leaving it to first-match luck.
- **`blocked-by-overlay` — the click landed on a covering modal/overlay
  (NOOD_0144)** → the target is fine; handle the covering element first:
  author the close/confirm step the flow itself implies, or
  `NOODLE_AUTO_DISMISS=true` for generic popups. Re-guessing the target's
  locator is exactly the wrong move.
- **`app-rejected-action` — the app itself announced why it refused an
  earlier click (NOOD_0167)** — the verdict quotes the page's own
  announcement (an ARIA alert/status/live region or toast: "out of stock",
  "select a store", "email is required"). Locator/POM work is wasted;
  satisfy the precondition the message names in the scenario's setup, or
  assert the announcement itself if the rejection IS the expected
  behaviour.
- **Confirmed external site bug, reproduced directly, not our bug** →
  `@quarantine`, with a comment explaining what was confirmed and how —
  don't delete the scenario or silently ignore its failures.
- **Environment/hardware genuinely unavailable** (no Appium client, no
  emulator, no display) → the engine should skip cleanly, not fail + retry
  pointlessly. If you find a spot where it still hard-fails instead of
  skipping, that's a real bug in the hook gating, not a test problem — fix
  the hook (see `noodle/hooks.py`'s `_ocr_available()`/`_appium_available()`
  pattern for the shape that fix should take).

---

## 7.5 — Session diagnostics: log the failure story before it evaporates (NOOD_0147)

The engine watches every run for the failure shapes worth reporting back:
first run of a dev session red (`first-attempt-fail`), red-run count
reaching `NOODLE_DEV_FIX_ATTEMPTS` (`hard-fail`), first-run→now wall clock
past `NOODLE_DIAG_SLOW_MIN`, default 20 min (`slow-dev`). A fired trigger
rides the run result as `diagnostic_due` — in the MCP payloads, in `noodle
run --json`, and as a 🩺 line on the plain CLI. Two triggers only you (the
driving agent) can evaluate: `over-budget` — your OWN session spend past
`NOODLE_DIAG_COST_BUDGET` (default 20 AIC/credits) — and `manual` — the
user's prompt contains `--diagnostic` or `skill: diagnostic` (log
regardless of outcome).

If any fired: at session end, after the reports are delivered, make ONE
`log_diagnostic` call (`noodle diagnostic log <app> --trigger <t>
--summary "…"`) per developed test, written from session memory only —
summary, timeline, suspected cause, fixes tried, duration, attempts, agent
name + cost. The engine appends the run facts and RCA verdict itself and
scrubs secrets; never re-read logs/reports to compose the narrative, and
never include credential values. **No trigger → write nothing** — the
mechanism only has value if a diagnostic means something went wrong. Files
land in the workspace's gitignored `diagnostics/` folder (engine-capped and
session-deduped); the tester ships them back with `noodle diagnostic
bundle`. Full contract: [session-diagnostics.md](session-diagnostics.md).

---

## 8 — Edge cases to account for

- **Artifacts are overwritten in place on every `noodle run`** (NOOD_0093):
  the report is rebuilt from scratch, but the Allure trend history
  (`reports/allure-history/`) survives the wipe and carries prior-run trends
  into the new report — so there's nothing to archive for trends. Runs no
  longer auto-zip the previous tree. `noodle archive` still zips a run on
  demand (to keep that run's screenshots/traces); `noodle clean` deletes the
  live tree but preserves the trend history.
- **A model isn't auto-detected.** `NOODLE_MODEL` must be set explicitly —
  a locally running Ollama server, or any other provider, is invisible to
  the framework until you set it. Don't assume an LLM fallback step will
  "just work" because *something* is running locally.
- **GitHub Models is retired (2026-07-30).** If a workspace's LLM setup
  needs a work-account-backed model, see
  [llm-setup.md](llm-setup.md) — Azure AI Foundry
  is the current path GitHub itself points to, not GitHub Models.
- **`@live`/`@terminal`/`@appium` scenarios auto-skip, don't fail, when
  their real dependency (network opt-in / OCR engine / Appium client)
  isn't there.** A skip in the summary for one of these tags is expected,
  not a bug to chase — check the skip *reason* string before assuming
  otherwise.
- **Env var collisions are silent.** Two packages sharing a generic key
  name (`USERNAME`, not `BB_USER`) means whichever runs first in that
  process "wins" — there's no error, just a wrong value. Always prefix.
- **A `*_pom.yaml` with no `match:` silently never resolves** if its
  filename stem isn't a substring of the target URL. `noodle validate
  --resolve` warns on this — don't ignore that warning.
- **Retrying a failure until it's green is not a fix.** The engine already
  auto-retries once by default; if it's still failing after that, that's
  signal, not noise — root-cause it (§7), don't just bump
  `NOODLE_RETRIES`.
- **Don't invent step phrasing the dictionary already has a shape for** —
  `noodle step-search` before writing a new custom step in
  `<tests_dir>/steps/*.py`. A custom step file must not be prefixed `z_` — that's
  reserved for `z_catch_all.py`'s load-order trick.
- **Secrets never go in a `.feature` file or a committed `.env`.**
  `secrets.env`/`<app>_secrets.env` are gitignored — values stay on the
  machine that runs the test, never in git. (Secret values are also scrubbed
  from all run output — console, log file, RCA — NOOD_0118.)
- **App resources never go in the workspace-root `.env`** (NOOD_0108). Base
  URLs, endpoints and credentials for an app-under-test live in that app's
  own `resources/` (`environments.yaml`, `[<app>_]secrets.env`) so each
  package stays self-contained; the root `.env` is run-wide engine settings
  only (browser, timeouts, headless…).
- **An auto-dismissed overlay on a PASSING run still needs a human glance**
  (NOOD_0089): the RCA report's warnings section lists every popup the
  engine closed on the test's behalf. If the popup was the point — or is
  itself a bug — the green run is lying; report it.
- **A hidden element that had to be force-clicked is a smell.** The
  `hidden-force-click` healing event means the DOM-scan tier found the
  target but it was invisible. Fine for dev panels; a bug if users were
  supposed to see it.
- **Environment looks stale or half-set-up? `noodle doctor` first, by hand
  second** (NOOD_0138). It's read-only, detects by itself whether it's in an
  engine checkout, a workspace, or neither (pass a path or `--scope` to
  override), and `--json` gives stable check IDs + `ok` for programmatic
  triage. Exit 0 healthy / 1 findings / 2 bad path-or-scope. It never
  repairs and never reads secrets — apply the remediation command it
  prints (`noodle init …` for workspaces, reinstall for installs) rather
  than improvising. Details: [manual.md → Health
  check](manual.md#health-check--noodle-doctor).
- **Squash/branch/commit conventions live in `CLAUDE.md`, not here** — don't
  duplicate them; if they conflict with anything in this file, `CLAUDE.md`'s
  git-workflow rules win for the git side, this file wins for the
  test-authoring/running side.

---

## Checklist

1. In-repo or outside-repo? → `tests/<category>/<app>/` vs `<workspace>/noodle_tests/<app>/` (default to outside-repo unless told otherwise).
2. Scaffold only the supporting files the scenario needs (`resources/`, `resources/pageobjects/`).
3. Write Gherkin using only vocabulary from `steps_dictionary.md` / `STEP_VOCABULARY`; `{env:...}`/`{var:...}`/`{pom:...}` for anything credential-, capture-, or POM-specific — never a legacy `[X]`/`` `X` `` ref in new tests.
4. `noodle validate --resolve` before the first real run.
5. `noodle run --workspace <workspace> <path> --headless`.
5a. While developing, iterate on mechanical failures (bad selector/locator/find-timeout) with a cause-backed fix per re-run — first mid-flow failure: reproduce the exact state once with `probe --do`, re-author from it (§5) — up to `NOODLE_DEV_FIX_ATTEMPTS` (default 10), then stop and report the test as flaky — never loop to mask a real failure (§5, §8).
6. **Always** deliver both reports, every run, pass or fail: `noodle report serve --workspace <workspace>` hosts the Allure report and rca.html together on localhost (MCP: `serve_report`; driving the CLI yourself, add `--background` so the command returns with the URLs instead of blocking).
7. Root-cause any failure via the RCA verdict before patching (§7); `@quarantine` a confirmed external bug rather than hiding it.
8. Summarize what was written/run, pass/fail counts **plus the `verified` flag** (green + `verified: false` = unverified, say so — §5 "Green is not automatically verified"), and where both reports ended up; inspect any screenshot you cite before claiming what it shows.
9. Session end: if a failure trigger fired (hard-fail / first-attempt-fail / slow-dev / over-budget, or the prompt asked via `--diagnostic`), log ONE session diagnostic from memory — `noodle diagnostic log` / `log_diagnostic` (§7.5); no trigger → log nothing.

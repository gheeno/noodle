# Noodle Test Framework — Design History
<!-- Branch: NOOD_0066 -->

> **For:** maintainers — historical rationale, not a how-to.

The chronological record of how each capability was designed and built, condensed
from the original twelve phase documents (plus the 2026-06-28 peer review /
NOOD_0025 pattern-coverage review / 2026-07-02 web-testing gap review cycle,
phases E–V). For how the framework works *today*, read
**[Architecture](architecture.md)** and the **[Encyclopedia](encyclopedia.md)** — those are kept
current; this page is the rationale trail behind them.

> Early phases (1–2) were written as forward-looking plans and use some names that
> changed during implementation (e.g. an early "LangGraph orchestrator" and a
> `parser/feature_loader.py` became the simpler `orchestrator/runner.py` +
> `resolver/`). Where a phase says "Plan", treat it as intent, not current code.
> The shipped surface is whatever Architecture/Encyclopedia and the `unit_tests/` suite show.

| Phase | Topic | Status |
|-------|-------|--------|
| 1 | Foundation — parse, resolve, route | Done |
| 2 | Web agent — Playwright, intent locators, assertions | Done |
| 3 | CLI & hooks hardening — 6 correctness bugs | Done |
| 4 | Visual / desktop agent — OpenCV, OCR, PyAutoGUI | Done |
| 5 | Reporting — Allure JSON, JUnit XML, annotated shots | Done |
| 6 | CLI, recorder & Azure DevOps | Done |
| 7 | Syntax highlighting & editor (LSP) | Done |
| 8 | Test-development guide — feature/POM authoring | Done (guide) |
| 9 | Element disambiguation — ambiguity + page-scoped POM | Done (9.1–9.3; 9.4 deferred) |
| 10 | Foundry Local — local model on a locked-down network | Plan / research |
| 11 | Step-coverage expansion — keyboard, tables, asserts | Done (11.1–11.3; 11.4 deferred) |
| 12 | Step dependencies & shared state | Done (12.1–12.2; 12.3 deferred) |
| D | Network mocking · API setup/teardown · test-data fixtures | Done |
| 13 | Bundled test app (BusterBlock) + data preconditions/teardowns | Done |
| 14 | Run external scripts/commands as steps | Done |
| 15 | Pattern coverage — tabs, history, clicks, submit | Done (NOOD_0025) |
| 16 | Test folder restructure — `features/` → `tests/`, `features/`+`resources/` split | Done (NOOD_0013) |
| 17 | Web gap closure — POM-first resolution, dialogs, uploads, multi-select, Allure de-dup | Done (NOOD_0008) |
| 18 | `noodle-agent` LLM persistence — `noodle init --llm`, `.env` auto-load | Done (NOOD_0017) |
| 19 | `noodle-agent` seamless-conversation gaps — persona, memory, compound requests, self-repair | Done (NOOD_0012) |
| 20 | Live-usage report follow-ups — POM auto-scope lint, Allure metadata, `assert_url` waits, sharper RCA | Done (NOOD_0022) |
| 21 | Deterministic POM pinning — `@page:<name>` tag, `{key}` explicit resolver bypass | Done |
| 22 | Declarative resolution step + target-architecture gap closures | Done |
| 23 | External workspace verdict + NOOD_0030 gap analysis closed out | Done |
| 24 | RCA engine hardening + heuristics-first design | Done (NOOD_0018) |
| E | Parallelism ceiling — per-file Azure matrix sharding, local `--parallel` | Done (NOOD_0021/0022) |
| F | Appium / native mobile — `@appium`, `agents/mobile/` | Done (code; device validation pending, NOOD_0016) |
| G | Desktop automation — `focus_region`, multi-word OCR, window management, native UIA driver (partial) | Done (NOOD_0016/0024) |
| H | Remote browser execution — `NOODLE_REMOTE_URL` (BrowserStack/Sauce Labs/grid) | Done (NOOD_0016) |
| I | LLM cost cap — `NOODLE_LLM_MAX_CALLS` / `NOODLE_RCA_MAX_CALLS` | Done (NOOD_0016) |
| J | Multi-user / multi-context flows — named browser contexts | Done (NOOD_0016) |
| K | Step-level retry — `NOODLE_STEP_RETRIES` / `@retry_step` | Done (NOOD_0016) |
| L | Advanced interaction patterns — drag & drop, soft assertions, network assertions | Done (NOOD_0009/0016) |
| M | Console & network error visibility | Done (NOOD_0016) |
| N | Browser context emulation — geolocation, permissions, locale, timezone, color-scheme | Done (NOOD_0016) |
| O | Offline mode & network throttling | Done (NOOD_0016) |
| P | Accessibility auditing — vendored axe-core | Done (NOOD_0016) |
| Q | Clipboard read/write | Done (NOOD_0016) |
| R | WebSocket observation | Done (NOOD_0016) |
| S | Print / PDF | Done (NOOD_0016) |
| T | Closed shadow-DOM via coordinate/OCR fallback | Done (NOOD_0016) |
| U | Developer / agent / manual-tester experience — LSP hover, VS Code snippets, `noodle steps` | Done (NOOD_0016) |
| V | Agentic depth — persistent memory & re-planning | Open / owner decision |

---

## Phase 1 — Foundation

**Goal:** read a `.feature` file, understand each step, route it to the right agent.

- **behave** parses Gherkin; Noodle Test Framework drives the steps through one catch-all (no per-step glue).
- **Variable substitution** — `[my email]` → `os.getenv("MY_EMAIL")`, before the step resolves. Missing vars stay literal + warn (handy for exploratory runs).
- **Two-tier resolver** — Tier 1 is regex pattern matching (most steps, no cost); Tier 2 hands the sentence to the LLM only on a miss.
- **LiteLLM** is the single model gateway — swap models with one env var.

The orchestrator was originally sketched as a LangGraph state machine; it shipped
as the straightforward `orchestrator/runner.py`. The two-tier resolver and
LiteLLM gateway carried through unchanged.

## Phase 2 — Web Agent

**Goal:** run web steps with Playwright; find elements by *what they are*, not by selector.

- **Intent locator chain** (`agents/web/locator.py`): `getByRole` → `getByLabel` → `getByText` → `getByPlaceholder` → `getByTitle`, stopping at the first hit. Step 6 (vision LLM) only fires when all five miss.
- **Assertions** split into **structural** (DOM text/url/title — never an LLM) and **semantic** (vision LLM judges a screenshot, and stores its reasoning as evidence).
- **Visual baseline** — `the screen should look the same as before` stores a *semantic description*, not pixels, so timestamps/avatars don't cause false diffs.
- **Self-healing** — re-scan, scroll, partial-text retry, then LLM as last resort; healing events are logged.
- Browser/engine/emulation selected by tags (`@firefox`, `@webkit`, `@mobile @iphone`, `@slow`, `@record_video`).

## Phase 3 — CLI & Hooks Hardening

**Goal:** close six correctness bugs found reviewing Phase 2, each with a concrete failure scenario.

1. **`NOODLE_HEADLESS` passthrough** — normalise any truthy value (`1`/`yes`/`on`) to canonical `true`/`false` so headless CI isn't silently downgraded to headed.
2. **`--headed` + `--headless` together** — now a hard error instead of a silent winner.
3. **`@headed` + `@headless` on one scenario** — emits a warning (priority `@headed` wins, but the conflict is surfaced).
4. **`NOODLE_BROWSER` validation** — bad values (`chrome`, `safari`) give a clear "unsupported browser" error, not a cryptic `AttributeError`.
5. **Hardcoded `features/` base** — the behave root is now derived from the passed path (nearest ancestor with `steps/` or `environment.py`), so non-standard layouts work.
6. **Cleanup leak** — per-resource `try/except` with guards in `after_scenario`, so a failed close no longer orphans Playwright processes.

Covered by `unit_tests/test_cli_hardening.py` and `unit_tests/test_hooks_hardening.py`.

## Phase 4 — Visual / Desktop Agent

**Goal:** automate anything on screen that isn't a browser DOM — desktop, Electron, Citrix, legacy.

- Tag `@visual` routes to `agents/visual/`. Three locator types:
  1. **Image match** — OpenCV `matchTemplate` against a reference PNG (a path relative to the run dir, e.g. `assets/`), with DPI-scale variants (0.8×–1.2×).
  2. **OCR** — `pytesseract` reads on-screen text (grayscale + contrast preprocessing).
  3. **Description** — vision LLM coordinate fallback, gated on `NOODLE_VISION_MODEL`.
- **PyAutoGUI** performs click/type/key/drag/scroll; **mss** captures the screen.
- Failed image matches produce an annotated screenshot (searched region, best candidate + score). Web and visual steps can mix in one scenario.

## Phase 5 — Reporting

**Goal:** after every run, a report that shows what happened, where it failed, and a screenshot with the failure circled — in a format Azure DevOps reads natively.

- `reporting/writer.py` emits **Allure JSON** per step as it runs.
- `reporting/annotate.py` draws failure annotations with **Pillow** (red box for not-found, highlight + ✗ for assertion failures).
- `reporting/junit.py` writes **JUnit XML** (stdlib `xml.etree`) — Azure DevOps shows pass/fail counts with no plugin.
- `reporting/builder.py` shells out to the **Allure CLI** to render the HTML; `noodle report open` / `generate` wrap it. Semantic-assertion reasoning is attached as evidence.

## Phase 6 — CLI, Recorder & Azure DevOps

**Goal:** one command to run, one to record, drop-in pipeline YAML.

- **Recorder** (`recorder/recorder.py`) — `noodle record` opens a visible browser, watches navigate/click/fill events via Playwright, and writes human-readable steps (not raw selectors). `recorder/sensitives.py` auto-redacts emails/cards/passwords to `{env:VARIABLE}`.
- **CLI** (`cli.py`, Typer): `run` (`--headless`/`--headed`/`--tag`/`--browser`), `validate`, `list`, `record`, `report open`/`generate`.
- **Azure pipelines** — Linux (`azure-pipelines.yml`, Xvfb for headed/visual) and Windows (`azure-pipelines-windows.yml`, native GUI) templates that publish JUnit + Allure.

## Phase 7 — Syntax Highlighting & Editor

**Goal:** great `.feature` editing in VS Code with minimal new code.

- A **TextMate grammar** (`vscode-extension/syntaxes/noodle.tmLanguage.json`) colours Gherkin keywords, `@tags`, and Noodle Test Framework-specific `[variables]` (gold).
- A **pygls** language server (`lsp/server.py`) reads `resolver/patterns.py` directly, so step validation always reflects the real patterns. Unknown steps are **warnings, not errors** (the LLM may resolve them at runtime).
- `@tag` autocomplete and `[variable]` completion sourced from the project `.env`.
- The standard Cucumber extension conflicts (both bind `.feature`); disable it per workspace.

## Phase 8 — Test-Development Guide

**Goal:** explain how a QA writes feature files and when to write `pom.yaml`, especially across multi-page flows.

The key insight: the framework is **stateless about pages** — it acts on whatever
is in the browser when each step runs, so a multi-page journey needs no special
config. `pom.yaml` is needed *only* when an element can't be found by its natural
label. This content now lives in the **[Encyclopedia](encyclopedia.md)**.

## Phase 9 — Element Disambiguation

**Goal:** guarantee the framework acts on the *intended* element, not just the first match.

Two failure modes were fixed:

- **9.1 Ambiguity detection** — the accessibility path no longer blindly returns `.first`. On 2+ matches it consults the POM for a scoped entry first; with none, **lenient** mode (default) warns + uses first, **strict** mode (`NOODLE_STRICT_LOCATOR` / `@strict`) fails with the full candidate list. *This is the linchpin* — most wrong-element bugs were "ambiguous but found" cases that never reached the POM before.
- **9.2 URL page-scoped POM** — optional `pages:` / `shared:` blocks; `pom.locate` reads `page.url` and consults the matching block, so the same key resolves to different selectors per page. Flat files still work unchanged.
- **9.3 Page pinning** — `Given User is on the "X" page` for SPAs where the URL never changes.
- **9.4** (per-page POM files) deferred — YAGNI until one file actually hurts.

Rejected: container-language-in-Gherkin (heavy parser, duplicates a one-line POM
entry) and mandatory page names in every step (punishes the 95% unambiguous case).

## Phase 10 — Foundry Local

**Goal:** run a local model on a corporate network where Hugging Face *and* Ollama are blocked. **Status: plan / research.**

**Verdict:** it works, *because* Foundry Local avoids both blocks — it pulls
models from the Azure Foundry Catalog (not HF), is a separate runtime (not
Ollama), and its runtime installs via `winget`/`brew` (not pip). It exposes an
**OpenAI-compatible** endpoint.

The lazy finding for Noodle Test Framework itself: its LLM fallback already runs on LiteLLM,
which already speaks OpenAI-compatible endpoints — so unblocking it needs **no new
framework, only `.env`**:

```bash
NOODLE_MODEL=openai/qwen2.5-7b-instruct-generic-cpu   # exact id from `foundry model list`
NOODLE_LLM_URL=http://localhost:<port>/v1
OPENAI_API_KEY=not-needed                               # local service ignores it
```

The heavier `agent-framework` + `MCPStdioTool` work (a genuinely agentic
MCP-driven browser agent) is reserved for when one-shot `ask()` isn't enough —
kept in a separate `uv` subproject so it never bloats Noodle Test Framework's core install.

## Phase 11 — Step-Coverage Expansion

**Goal:** grow from a happy-path engine into a full-interaction engine without breaking "sentences over syntax".

The constraint: a new capability is **three edits** — a regex (`patterns.py`), an
action (`actions.py` + `runner.py`), and the action name added to the LLM prompt's
valid-action list (`step_resolver.py`); first-person verbs also need `_FIRST_TO_THIRD`.

- **11.1 Tier A** — keyboard keys, hover, wait-disappears, element value/state/**attribute** asserts (attribute covers SVG), count asserts, and `store_text` (capture element text into a run-scoped var).
- **11.2 Tier B** — D365 tables: row/cell scoping (`click "Edit" in the row containing "X"`), bounded container scoping (`the "Save" button in the "Payment" section`), and iframe `switch_frame`. All accessibility-first, no XPath in the sentence.
- **11.3 Docs** — shadow-DOM (`css`/`role` pierce, `xpath` doesn't; closed → vision), SVG authoring, container scoping. Folded into the Encyclopedia.
- **11.4** (video, canvas-click) deferred — YAGNI; semantic vision already answers "what does the chart show".

## Phase 12 — Step Dependencies & Shared State

**Goal:** let a value produced in one step be used by a later step, in plain Gherkin, with no DI container or expression engine.

`context._vars` (per-scenario, reset each scenario) **is** the scenario-scoped
bean; `[VAR]`/`` `var` `` substitution **is** the injection. Two delimiters by
design: `` `name` `` reads the **run store** (values captured during the test),
`[name]` reads **`.env`** (secrets/config).

- **12.1** — `set_var` (seed a literal), `store_attribute` (capture an attribute); `store_text` already existed from 11.1.
- **12.2** — `assert_compare` (`greater than` / `less than` / `equal` / `contain` / `not equal`); numeric if both sides parse, else string. This was the actual gap — storing + substitution already carried the dependency.
- **12.3** (computed expected values) deferred *and discouraged* — computing the expected value with the app's own logic re-implements the app and proves nothing. The app computes; the test observes.

Rejected throughout: a Spring-style DI container (nothing to inject into), an
expression mini-language (`${A+B}` turns the feature file into code), and
cross-scenario globals (makes tests order-dependent).

## Phase D — Network mocking, API setup/teardown & test data

**Goal:** decouple a UI test from a flaky/slow/absent backend, and seed/clean data
without driving the browser.

- **Network mocking** (`mock_route`/`block_route`) — Playwright routing fulfils or
  aborts matched requests, so a test can run against a stubbed API or silence
  third-party noise.
- **API setup/teardown** (`api_call`) — hit an endpoint directly via Playwright's
  request context (shares browser cookies); fails on a non-2xx.
- **Test-data fixtures** (`load_data`) — load a YAML/JSON mapping into the
  run-scoped store, referenced later as `` `backtick` `` captures.

Three edits each (regex + action + LLM action-list), same as Phase 11.

## Phase 13 — Bundled test app & data preconditions

**Goal:** a self-contained app the examples run against, and a way to seed its
data before a scenario — the JDBC-fixture pattern, in Gherkin.

- **BusterBlock** (`test-apps/busterblock/`) — a Node/Express VHS-rental site with
  in-memory data and test-only `/api/test/*` endpoints (reset / set-stock /
  seed-cart) gated behind `BB_TEST_API`. The in-memory store *is* the "database".
- **Tag-driven preconditions** (`noodle/preconditions.py`) — `@precondition:NAME`
  runs a fixture's `setup:` HTTP calls in `before_scenario` and its `teardown:`
  calls in `after_scenario` (**even on failure**), from a per-folder
  `preconditions.yaml`. stdlib `urllib`, no new dependency.

Chosen over a `Background:`-only approach and a real SQLite DB. Rationale:
`Background:` + the existing `api_call`/`load_data` steps already covers
*setup*, but `Background` runs only before a scenario — there is no native
teardown, which the requirement demands. A real SQLite DB would be a truer
JDBC analog but is a large rewrite of the test app for no test-coverage gain;
the in-memory store already models the domain. Teardown guarantees: every
fixture's teardown is `POST /api/test/reset`, so state never leaks between
scenarios even if a setup half-completed; `before_all` wipes per-run
artifacts, so preconditions add per-scenario data isolation on top.

## Phase 14 — Run external scripts/commands as steps

**Goal:** invoke anything a test needs that the browser can't do — seed a DB with
Python, run a Java jar, call a shell tool — as a plain Gherkin step.

- `run_script` / `run_command` (`noodle/orchestrator/script_runner.py`) — the
  interpreter is inferred from the file extension (`.py`/`.js`/`.jar`/`.sh`/…); a
  non-zero exit **fails the step**; stdout is captured into `` `SCRIPT_OUTPUT` ``
  (plus an optional named var) for downstream assertions. Same three-edit shape as
  the other action families.

Trust boundary noted: feature files are trusted code (like step definitions), so
`run the command` uses a shell — not for untrusted input.

## Phase 15 — Pattern coverage expansion (NOOD_0025)

**Goal:** eliminate the LLM-dependency for steps that 117 existing test files already used, turning four LLM-only paths into deterministic local patterns.

A coverage review found 113 of 117 unique steps in the repo matched patterns; four did not — all from `features/busterblock/new_tab.feature`. Two problems drove the whole phase: (1) no tab/window handling existed at all, (2) the `select` pattern only accepted `from the X`, not `in the X`.

- **Tab / window management** — `a new tab should open` (asserts second page, focuses it), `switches to the new|previous|original|first tab`, `closes the tab`, and an `… in the new tab` suffix that routes any step to the newest page. Wired into `execute_step` which owns `context.pages`.
- `submits the (…) form` → clicks the form's submit control via `form.requestSubmit()`.
- `selects "X" in the Y (dropdown|filter|menu|list|select)` — widened the `select` pattern to accept both `from` and `in`.
- Browser navigation verbs: `goes back`, `goes forward`, `reloads|refreshes the page`.
- Click variants: `double-clicks X`, `right-clicks X`.

The web pixel/OCR bridge (`agents/web/screen.py`, delivered in NOOD_0024) was also incorporated: canvas and terminal UIs within `@web` scenarios now have a dedicated path (`type_text`, `click_at`, `click_text`, `assert screen text`, `focuses on region`) that converts OCR device-pixel coordinates to Playwright CSS-pixel mouse inputs.

Result: 117/117 steps in the repo resolve to a local pattern without an LLM call.

## Phase 16 — Test folder restructure: `features/` → `tests/` (NOOD_0013)

**Goal:** the top-level `features/` folder had drifted into holding more than
features — environment config, page objects, scripts, fixtures — which read
as confusing to a newcomer skimming the tree. Reorganize so the name matches
the contents at every level.

- Root rename: `features/` → `tests/`. Config key renamed to match:
  `features_dir` → `tests_dir` (`noodle.yaml`, `noodle/config.py`).
- Per-app split: each app-under-test now has `features/` (its `.feature`
  files only) and `resources/` (everything else — page objects, env,
  secrets, fixtures, functions, preconditions) as siblings, instead of
  mixing them all directly in the app folder.
- `environment/` folded into `resources/` — one folder for "things this
  app's tests need", not two. `environments.yaml` and `secrets.env`(`.example`)
  inside it gained an app-name prefix (`<app>_environments.yaml`,
  `<app>_secrets.env`) since they now sit alongside pageobjects/, functions/,
  payloads/, and data/ in the same folder rather than being visually set
  apart in their own `environment/` subfolder.
- `scripts/` → `functions/` (per-app, under `resources/`) — kept distinct
  from the repo-root `scripts/`, which is unrelated framework dev tooling
  (CI feature discovery, etc.) and was never part of this rename.
- `pageobjects/` and the local (non-global) `pom.yaml` moved under
  `resources/`; `preconditions.yaml` likewise. The tree-wide `pom.yaml` and
  the behave-required `environment.py`/`steps/` stay at the `tests/` root —
  behave itself requires that exact file name and folder name at the root
  it's pointed at, so they're a framework contract, not a per-app or
  Noodle-specific convention, and can't be folded into `resources/` or
  renamed to something like `helpers/`.
- `payloads/` (JSON) and `data/` (CSV) are conventions inside `resources/`,
  not enforced — the resolvers (`orchestrator/runner.py`'s `load_resource`,
  the CSV-login custom step) just join whatever relative path a `.feature`
  file writes onto `resources/`. A fixture dropped straight in `resources/`
  instead of its category folder still resolves; it just reads
  inconsistently to the next person.
- Every path-resolution touch point that used to compute a package root as
  "the .feature file's own directory" now computes it as that directory's
  *parent*, since `.feature` files moved one level deeper into `features/`:
  `hooks.py` (`_load_package_env`, the `environments.yaml` glob),
  `agents/web/pom.py` (`_load_pom_chain`, `_global_pom_path`),
  `orchestrator/runner.py` (`load_resource`), `preconditions.py`
  (`_fixtures_for`), `tests/steps/custom_hooks.py` (the CSV-login step). One
  real bug surfaced by this: `reporting/writer.py` derived the Allure
  `parentSuite` label from the immediate parent folder name, which would
  have become the literal string `"features"` for every app once `.feature`
  files moved into a `features/` subfolder — fixed to use the grandparent
  (the actual app name) when the immediate parent is named `features`.
- `noodle/agent/generate.py`'s scaffolding also gained a `web/` type layer
  it didn't have before (it used to write straight to
  `<tests_dir>/<app>/`, inconsistent with the hand-authored suites which
  were already nested under `web/`) — the agent only ever generates
  browser-based tests, so this closes that inconsistency rather than adding
  a new one.

## Phase 17 — Web gap closure (NOOD_0008)

**Goal:** stress-test the framework against a real, content-heavy site
(qaplayground.com) end to end, both entry points (agentic external workspace
and classic in-repo), and close whatever broke. 34 scenarios, 10 features,
every widget class on the site. Result before closure: 28 ✅ / 6 ❌, all 6
were deliberate `@gap` probes; after: 34 ✅ / 0 ❌.

- **Allure double-ingest** — `write_junit()` defaulted into `allure-results/`,
  and `allure generate` reads both native JSON and JUnit XML from that same
  dir, so every scenario was counted twice (suite-less duplicate nodes,
  12 "failed" for 6 real failures). Fixed: JUnit now writes to `reports/junit.xml`
  (outside the ingest dir); `writer.py` adds `historyId` (md5 of `fullName`,
  folds retries) and `parentSuite`/`suite` labels; stale JSON cleared at
  `before_all`; failure screenshots copied into `results_dir()` so Allure
  actually renders them.
- **POM-first locator resolution** — `find()` consulted the POM only when the
  accessibility scan was ambiguous or empty, so a *unique but wrong*
  accessible match (a prose `<button>` containing the target's label text)
  silently won over an explicit `pom.yaml` entry — 19 of the 34 scenarios
  failed this way on the first run. `find()` now tries `pom.locate` first,
  matching `wait_for`/`wait_hidden`.
- **`noodle summary` retry de-dup** — `summary.collect()` now folds retries
  by `historyId` instead of counting each attempt as a separate failure.
- **JS dialog handling** (`agents/web/actions.py`) — arm-before-trigger:
  `User accepts the alert` / `dismisses the confirm` / `answers the prompt
  with "x"` / `the alert should say "x"`, via `page.once("dialog")`. The arm
  step must precede the click that triggers the dialog.
- **File upload + download assert** — `uploads '<path>' to the <element>` →
  `set_input_files()`; `a file "<name>" should be downloaded` wraps the
  triggering click in `page.expect_download()`, same arm-before-trigger shape
  as dialogs.
- **Multi-value + non-native select** — `selects 'A', 'B' from the X` →
  `select_option(["A","B"])`; on `not a <select> element` (Radix/headless-UI
  dropdowns), falls back to click-then-click-option-by-role/text. Tightened
  the single-select and `types X into Y` regexes to reject quotes inside the
  captured value — this had also been producing validator false positives
  (a step reported resolvable that actually wasn't).
- **POM file scoping** — sibling per-page POM files with no `match:` block
  were folder-global and silently shadowed each other (first file
  alphabetically won). A `match:`-less file now defaults its scope to
  `url_contains: <filename-stem>`; the old folder-global behaviour requires
  an explicit `match: {}`.
- **Polish** — empty-value assert (`should have value ""`), `pom.locate`
  gained a short retry instead of an immediate `count()`, failure messages
  in `find()` now name the element actually matched (role + accessible name).
- **Element-level failure indicators** — failure screenshots get in-page CSS
  outlines (red solid on what the locator actually matched, green dashed on
  where the POM says it should be) instead of a whole-image border; survives
  `full_page=True` and scrolling, unlike drawing rectangles from
  `bounding_box()`.
- **Two smaller fixes found in a follow-on ease-of-use pass**: `noodle run
  <file>.feature` was matching same-named files in *other* packages too (the
  `--include` regex wasn't anchored to the full relative path — fixed in
  `cli.py`); `python -m pytest unit_tests/` wasn't reachable from a clean
  install because `pytest` was never declared in any extra — added a `dev`
  extra and folded it into `all`.
- **Agentic trigger gap** (a separate finding from the same review pass) —
  `noodle-agent`'s dispatcher gated *all* test generation behind one fixed
  regex (`create (?:a )?test (?:for )?(.+?)(?: at | on )(https?://\S+)`), so
  free-form asks like "generate a test that goes to youtube.com and searches
  for X" failed with "Don't understand" even with `--llm` set — the regex
  gate ran before the model was ever invoked. Closed by `_extract_intent()`
  (LLM-backed `{description, url}` extraction as the dispatch fallback) and
  `_normalize_url()` (bare host → `https://`), both in `repl.py`; and
  `generate.py` gained `_scaffold_environment()` so a generated app also gets
  its `environment/` files, not just the `.feature` + POM.

## Phase 18 — `noodle-agent` LLM persistence

**Goal:** close the gap between the intended flow (clone repo → `noodle
init` → talk to `noodle-agent` in plain English from then on) and what
actually happened — `noodle init` never touched LLM config, and
`noodle-agent` only ever saw a model if `--llm`/`--model` was passed again on
every single invocation, because its process never loaded `.env` at all
(that only happens inside behave's `before_all`, which the REPL never runs).
So even hand-editing `NOODLE_MODEL` into `.env` had zero effect on the agent.

- `noodle init` gained `--llm ollama|claude|gemini` (+ `--model` override).
  When set, the generated `.env` gets an active `NOODLE_MODEL=...` line (and
  `NOODLE_LLM_URL=http://localhost:11434` for `ollama`) instead of the
  commented-out placeholder. If `.env` already exists (re-running init),
  `--llm` is a no-op with an explicit note — never silently rewrites a
  workspace's config.
- `noodle-agent`'s `main()` now loads the workspace's `.env` itself
  (`noodle/agent/repl.py:_configure_llm`) before deciding whether free-form
  mode is on. An explicit `--llm`/`--model` still wins outright; otherwise, if
  `NOODLE_MODEL` is already set (persisted by `noodle init --llm`, or
  exported in the shell), free-form mode turns on automatically — no flags
  needed on a second, third, or hundredth terminal session in that workspace.
- The `--llm` name → model-string preset table moved from `repl.py` to
  `config.LLM_PRESETS` so `noodle init` and `noodle-agent` share one
  definition instead of two copies drifting apart.
- Test generation itself (`generate_llm()` mapping free-form sentences to
  vocabulary-checked Gherkin) and the file-layout playbook (POM/environment/
  secrets scaffolded per app under `<tests_dir>/web/<app>/`) were already
  solid before this phase — see Phase 17 and `docs/agent-playbook.md`
  — this phase closes the *session bootstrapping* gap only.

## Phase 19 — `noodle-agent` seamless-conversation gaps (NOOD_0012)

**Goal:** close the gap between "the two spec-required capabilities work"
(configurable LLM provider; guardrailed free-form `.feature` generation — both
already done, see Phases 2 and 18) and "conversation with the agent feels
seamless across multiple turns," which the reviewed four-layer agent
architecture (brain / memory / tools / orchestration) framed as missing
memory and orchestration.

- **Persona** — `noodle/agent/prompts.py` gained a `SYSTEM` string
  ("you write Gherkin for Noodle only, never suggest shell commands outside
  `noodle ...`") threaded as a `role: system` message in `client.ask()`, so a
  smaller/cheaper model is less likely to wander off-task.
- **Overwrite guard** — `generate.generate()`/`generate_llm()` now warn and
  refuse to silently clobber an existing feature file; asking twice for "the
  youtube search test" requires an explicit "overwrite" instead of losing the
  first draft (mirrors the pre-existing `environments.yaml` guard).
- **Turn-level memory** — `repl.dispatch()` takes a `state` dict (which file
  the agent itself last created/ran this session) so "run that" / "add a
  scenario to that one" resolves a pronoun to the prior turn's file. Working
  memory only — no DB, no vector store.
- **Compound requests** — `_extract_intent` (single `{description, url}`)
  became `_extract_plan` (`prompts.PLAN`), an ordered list of
  `{action: create|run|summary, ...}` steps, so "create a test for X, run it,
  and show me the report" resolves in one turn instead of three separate
  REPL lines. This is the minimum viable agent loop — `dispatch()` already
  was one, it just accepts more than one step per call now.
- **Run-failure self-repair** — new `noodle/agent/reflect.py`: after a
  `noodle run` the agent itself just triggered comes back failed, it feeds
  the first `summary.collect()` failure back to the model with the original
  `.feature`/POM, asks for a targeted diff, re-runs once, and keeps the fix
  only if it reduced the failure count — same keep-only-if-it-helped
  discipline as `generate_llm`'s step-vocabulary repair. Scoped to files the
  agent created and ran itself this session — never a hand-authored test
  someone asked to run by name.

## Phase 20 — Live-usage report follow-ups (NOOD_0022)

**Goal:** close the four actionable findings from the the-internet.herokuapp.com
regression-suite report (a real outside-in usage session: 15 features /
31 scenarios written against the framework as shipped).

- **POM auto-scope lint** — `noodle validate` (both plain and `--resolve`)
  now warns when a `pageobjects/<page>_pom.yaml` with no explicit `match:`
  has a filename stem that appears in no URL-ish string in its sibling
  features — the "silently never applies" trap Phase 17 documented and the
  report's author hit anyway (`file_upload_pom.yaml` vs `/upload`). Warn-only:
  a stem can match a runtime redirect the static scan can't see.
  Lives in `noodle/agent/validate.py::lint_pom_scopes`.
- **Allure metadata** — `noodle/reporting/allure_meta.py` writes
  `environment.properties` (browser/headless/timeout/retries + app base URLs,
  mirroring `hooks._load_environments`'s load order) and `categories.json`
  (four-bucket failure taxonomy keyed to the engine's actual message
  prefixes: `Could not find`/`Ambiguous locator`, `Timed out`,
  `No pattern matched`, `Expected`) into allure-results in
  `hooks.after_all`, before the report build. The report's Environment and
  Categories widgets were shipping empty.
- **Two more rule-based templates** — `generate.pick_template` adds
  `_CHECKBOX` (check/uncheck + state assert) and `_DROPDOWN` (select +
  value assert). Same validated-skeleton guarantee as login/search: every
  step resolves via the pattern table with placeholders intact
  (pinned by `unit_tests/test_nood_0022.py`).
- **Bundled-suite fix** — `sample_feature_tests/web/the_internet/features/key_presses.feature`
  no longer asserts the keydown echo for Enter: the lone-input bare `<form>`
  makes Enter fire the browser's implicit submit and reload the page before
  any assertion runs (the report's one legitimate failure). Enter now asserts
  its real behaviour (the redirect); Tab covers the echo path.
- **`assert_url` now waits** — fixing the suite exposed that the URL
  assertions were the only instant-read asserts in the engine: a keypress/
  click that triggers navigation returns before the navigation commits, so
  the assert raced the very redirect it checked. It now polls up to
  NOODLE_TIMEOUT like every other assertion (Playwright's own
  `expect(page).to_have_url` waits for the same reason). Gotcha for future
  polling code: the loop must pump via `page.wait_for_timeout`, not
  `time.sleep` — `page.url` is a client-side cached value that only
  refreshes while the sync event loop runs, so a plain sleep polls a stale
  URL forever.
- **Sharper RCA heuristics** — three failure shapes that fell through to the
  coarse "app-regression (low)" catch-all now get specific verdicts in
  `rca_report.classify()`: the bare-`?` implicit-form-submit signature
  (the exact diagnosis the report's author had to make by hand), URL-assert
  failures (navigation never happened / went elsewhere), and expired
  explicit waits. The classifier still guesses — but it now automates the
  three diagnoses this live session actually required.

## Phase 21 — Deterministic POM pinning

**Goal:** URL-based POM scoping (Phase 9) ties a `pages:` block to
`match.url_contains` — rename the route and the block stops matching even
though the selector inside it is still valid, breaking tests for a reason
that has nothing to do with the page itself. Two additive escape hatches,
neither changing the existing URL-matching default:

- **`@page:<name>` tag** (`noodle/hooks.py::page_pin`, wired into
  `before_scenario`) — pins the POM active page for the whole scenario
  before any step runs, the tag equivalent of the existing
  `Given User is on the "<name>" page` step
  (`agents/web/actions.set_page` → `pom.set_active_page`). A later step
  still overrides the tag; the tag is only the up-front default. This was
  already half-documented (`pom.py`'s own comment on `_active_page` and a
  README mention both referenced an `@page:<name>` tag that had never
  actually been wired to anything) — this phase makes the docs true.
- **`{key}` explicit locator syntax** (`agents/web/pom.py::is_explicit`,
  checked first thing in `agents/web/locator.py::_find`/`wait_for`/
  `wait_hidden`) — a step wraps an element name in braces
  (`clicks the {burger menu}`) to go straight to the POM YAML chain
  (page → app → global) and nowhere else: no accessibility tree, no
  self-heal, no vision LLM. Not found → the step fails immediately naming
  the chain it checked, rather than silently trying a heuristic that might
  resolve to the wrong element. Plain unbraced names are unaffected and
  keep the existing five-step order (Architecture § the resolution
  hierarchy, level ③).

Both reuse existing machinery end-to-end (`_tag_value`, `pom.set_active_page`,
`pom.locate`) — no new selector types, no new YAML shape, no path-based
"import" (rejected: a file path in the `.feature` is one more thing to keep
in sync with the filesystem; a logical name resolved through the same chain
the framework already walks is not). Pinned by
`unit_tests/test_hooks_hardening.py::TestPagePinTag` and
`unit_tests/test_pom_explicit_syntax.py`.

## Phase 22 — Declarative resolution step + target-architecture gap closures

**Goal:** `docs/target-architecture.md` (a cleaned-up rendering of the original
design sketch, `Noodle Framework.pdf`) was the vision/target picture; this
phase reconciled it with the as-built framework and added the one capability
it named but the codebase didn't yet speak in its own words. The doc's every
open question and gap was closed by this point, so it was retired — its
as-built deviations and open questions live in
[architecture.md](architecture.md) now instead.

- **`browser/screen/system resolution is set to <WxH>`** — a second, more
  declarative phrasing for the existing `set_viewport` action
  (`resolver/patterns.py`), sitting next to `sets the viewport to`. Same
  dispatch (`orchestrator/runner.py`'s `set_viewport` branch), same
  `{env:X}`/`{var:X}` substitution (done in `execute_step` before the
  sentence ever reaches the resolver) — no new runner logic, just a second
  regex mapping to the action the framework already runs.
- **Compressed + deduped failure screenshots** (sketch open question) —
  `reporting/annotate.py`'s PNG writes now pass `optimize=True` (free,
  lossless, stdlib Pillow feature); and `hooks.after_step` deletes the raw
  pre-annotation screenshot once the annotated copy exists, since only the
  annotated file was ever attached to the Allure result — the raw copy was
  pure duplicate storage on every failure before this.
- **`noodle report serve`** (sketch open question: auto-serve/host reports)
  — `reporting/builder.py::serve_report`, stdlib `http.server`, binds
  `0.0.0.0:8000` by default (NOOD_0070 flipped the default to `127.0.0.1`;
  `--host 0.0.0.0` opts back in). `allure open` only ever bound to localhost, so
  a teammate had to download the artifact zip and run `allure open`
  themselves to see a report; this serves the already-built HTML directly
  off whatever machine ran the suite. Deliberately not a permanent-hosting
  story (no GitHub Pages/S3 publish step) — that's a CI/infra decision, not
  a framework one.
- **Docs reconciliation** — the mapping table's "noodle-agent... **the
  gap**" row was stale: `agent/repl.py` + `cli.py init` already exist, and
  NOOD_0030 already closed generation-grounding, run-after-generate,
  negative-case generation, RCA history, and visual-diff baselines. Row
  updated to point at the real modules. The sketch's "Ollama (default)" LLM
  backend is flagged as an intentional as-built deviation (no model
  configured is the actual default — see architecture.md §5) rather than a
  gap to close. The sketch's native-app "custom HTML" report line is
  flagged for retirement — native/mobile runs already share the same
  Allure+JUnit pipeline as web, and building a second renderer for parity
  with an old sketch isn't worth it.

Nothing here touches the still-open items: persistent cross-session agent
memory and a multi-step re-planning loop (NOOD_0030 §4.1/§4.2, punch-list
#8/#9) remain explicit, owner-level decisions, not something this pass
should default into existing.

Pinned by `unit_tests/test_nood_0035.py`.

## Phase 23 — External workspace verdict + NOOD_0030 gap analysis closed out

**NOOD_0027** asked whether teams need a way to own their own test repo
(features/POMs/secrets) separate from the engine repo. Verdict: already
mostly capable — the workspace model (`noodle init`, `--workspace`, per-app
`resources/`) already covers it, documented in
[docs/encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops) and
[feature-packages.md](feature-packages.md#two-topologies). One real fix did
land from this review: `noodle step-search` / `noodle-agent`'s "find a step
for..." suggestion engine was hardcoded to this repo's own `docs/`
regardless of `--workspace` — fixed by threading `--workspace` into
`step_resolver.set_docs_dir()` / `patterns.set_agent_patterns_dir()`, so an
accepted suggestion resolves in the same workspace it was accepted in.

**NOOD_0030** was a point-in-time gap analysis (2026-07-04) comparing
`noodle-agent` against RobotFramework/Selenide/Selenium/Appium/Playwright.
Its punch list items #1–7 all shipped and are covered above (Phase 22): RCA
failure history, `known-quirks.yaml`, generation grounding, fix proposals,
visual-diff baselines, run-after-generate, and negative-case generation.
Items #8–9 (persistent cross-session agent memory, multi-step re-planning)
were deliberately left as open, owner-level decisions rather than built
speculatively — tracked going forward in
[Phase V below](#phase-v--agentic-depth-persistent-memory--re-planning-open-owner-decision).

Both source reviews are condensed here per this doc's own policy — no
standalone point-in-time review docs once shipped.

## Phase 24 — RCA engine hardening + heuristics-first design (NOOD_0018)

**Goal:** a full `.feature` test-run across every suite to find real bugs, and
a heuristic (non-AI) tier for RCA — most root causes are visible in text
signals already printed to the console, not requiring a vision model at all.

- **Heuristic classifier** (`noodle/reporting/rca_report.py::classify()`) —
  pure pattern matching over the assertion message, traceback, and
  now-captured console warnings (`noodle/log.py` captures every ⚠️ WARNING+
  line per step into the Allure result's `statusDetails.warnings` instead of
  just printing it and losing it). Same five categories as `rca.py`'s vision
  verdict, plus `config-gap`. `noodle rca-report [--llm]` renders the
  heuristic and agentic verdicts side by side, with an optional text-only LLM
  prose narrative on top (works with any configured model, vision or not).
- **Five real bugs found and fixed** by running every suite once: per-page POM
  files with no `match:` block claiming page-agnostic resolution, when the
  auto-scope behavior (Phase 17) had already made that need an explicit
  `match: {}` opt-out; `load_resource` wrongly rejected in browserless `@api`
  scenarios (one-line allow-list omission); an unguarded dict access in
  `rest_set_auth` that crashed with a raw `KeyError` instead of a clean
  `AssertionError` when a misconfigured model hallucinated an incomplete
  action; a `hooks.py` ordering bug where the Appium session-start ran before
  user `before_scenario` hooks, so a failed mobile session masked its real
  `ImportError` behind a second, confusing `AttributeError`; a qaplayground.com
  site quirk (its own React state doesn't track `select_option()`'s DOM-level
  multi-select) quarantined rather than chased.
- The **`known-quirks.yaml`** ledger (shipped, see Phase 23) grew directly out
  of this session's qaplayground investigation — a confirmed non-Noodle root
  cause should never need re-deriving by the next person who hits it.
- Deferred (non-AI, still worth doing if it becomes a real pain point):
  failure history (repetition across runs promotes confidence, distinguishes
  flake from regression) and a cheap structural visual-diff (SSIM/pixel-delta
  against a last-known-good screenshot) as a free signal before reaching for
  a vision model.
- Where AI actually earns its keep, in order of leverage: pointing
  `NOODLE_MODEL` at a vision-capable model at all (unblocks RCA, the
  vision-locate fallback, and the `pure_llm`/`llm_fallback` features
  simultaneously — the single highest-leverage change is a config value, not
  code); text-only fix-proposal generation (`classify()`'s verdict plus the
  relevant file's content → a unified diff, human-reviewed, never
  auto-applied); vision reserved for the residual "unknown" bucket once the
  above heuristics are in place.

## Phase E — Parallelism ceiling

**Goal:** shard at the feature-*file* level, not the feature-*folder* level, to eliminate uneven CI agent load.

- **CI**: `scripts/list_features.py` discovers all web `.feature` files; both Azure pipelines (`azure-pipelines.yml`, `azure-pipelines-windows.yml`) consume the list as a dynamic per-file matrix via a `discover` job. Web-only by design — `@appium`/`@desktop`/etc. files are excluded (Phase F/G have their own runners).
- **Local follow-up (NOOD_0022)**: `behavex` added as an opt-in `[parallel]` extra for local multi-process runs (`noodle run --parallel N`, web only) — CI stays on the matrix, the two aren't stacked. Reporting made parallel-safe: per-worker `artifacts/allure-results/p<pid>/` dirs, merged on completion.
- `pytest-bdd` migration considered and skipped — no benefit over behave + behavex.

## Phase F — Appium / native mobile

**Goal:** drive a real device or emulator via Appium, using the same plain-English Gherkin as the web agent.

Shipped (NOOD_0016) as code: `[mobile]` extra, `noodle/agents/mobile/` (`driver.py` session lifecycle, `locator.py` mirroring the web fallback chain — accessibility first, POM YAML second, fail loudly third — `actions.py` for tap/swipe/long-press/back/home/send-keys), `@appium` routing in `hooks.py`, an `APPIUM_SERVER` env key, and a smoke feature (`sample_feature_tests/mobile/features/smoke.feature`) targeting Android Settings. TouchAction was replaced by W3C Actions (client v3 removed it). Not yet validated against a live emulator/device. iOS XCUITest is deferred until the Android path is proven stable; biometric/gesture actions are deferred too.

## Phase G — Desktop automation

Four sub-phases, in priority order:

- **G1 — `focus_region` wire-up** (Done, NOOD_0024, shipped alongside the web OCR bridge): a per-scenario active region crops the OpenCV/OCR search area (`agents/visual/screenshot.py`'s `_active_region`, reset each scenario) instead of always scanning the full screen.
- **G2 — Multi-word OCR phrase matching** (Done, NOOD_0016): `ocr.py` groups Tesseract word boxes by `(block, par, line)` and joins them into a line string before searching, so `"Save As"` matches even when Tesseract returns `"Save"`/`"As"` as separate boxes.
- **G3 — Window management** (Done, NOOD_0016): `agents/visual/window.py` — `focus_window(title)` via `pygetwindow` (Windows/macOS) or `wmctrl` (Linux), plus `list_windows()` for debugging.
- **G4 — Native accessibility driver** (Partial, NOOD_0016) — the strategic desktop bet: give desktop the same "find by role/name" story the web locator has, instead of pixel/OCR matching. Shipped: the app-lifecycle primitive (`launches the app "cmd"` / `the app should be running [on port N]` / auto-kill in `after_scenario`, `noodle/app_lifecycle.py`) and scale-invariant template matching (try 3–5 scales, remember the winning scale per session, in `matcher.py`). Still open: the Windows UIA driver itself (`agents/desktop_native/`, `pywinauto`-based) — needs a Windows 11 host to develop and validate; locked decision is Windows-first, macOS (`atomacos`/`pyobjc`) and Linux (`pyatspi`) stay stubs until then. Deferred indefinitely: multi-monitor support and Win32/COM (no named app to test against yet).

## Phase H — Remote browser execution

**Done (NOOD_0016).** `NOODLE_REMOTE_URL` makes `hooks.before_scenario` call `browser_type.connect(ws_endpoint=url)` instead of `launch(...)`, pointing Playwright at a remote CDP endpoint (BrowserStack, Sauce Labs, a Playwright grid) with the rest of the scenario lifecycle unchanged.

## Phase I — LLM cost cap

**Done (NOOD_0016).** `NOODLE_LLM_MAX_CALLS` (default `0` = unlimited) caps `ask()`/`ask_vision()` calls via a module-level counter reset each run in `noodle/llm/client.py`; past the cap, a clear `AssertionError` fires instead of runaway spend. RCA calls (`rca.review`) are capped separately via `NOODLE_RCA_MAX_CALLS` so a cap on fallback steps doesn't also silence RCA.

## Phase J — Multi-user / multi-context flows

**Done (NOOD_0016).** `context._named_contexts: dict[str, Page]` lets one scenario drive two simultaneous browser sessions: `Given a new browser context as "buyer"` creates a second `BrowserContext`+`Page`; `When acting as "buyer"` swaps `context.page` to that context's page for the step block. All named contexts close before the primary one in `after_scenario`.

## Phase K — Step-level retry

**Done (NOOD_0016).** `NOODLE_STEP_RETRIES` (or a per-scenario `@retry_step` tag) wraps the catch-all step dispatch (`noodle/steps/catch_all.py`) in a retry loop, retrying only on `AssertionError`/Playwright `TimeoutError` — unexpected exceptions propagate immediately. Retries are recorded in `artifacts/reports/healing-report.jsonl` (`strategy: "step-retry"`) alongside locator heals.

## Phase L — Advanced interaction patterns

From the NOOD_0025 pattern-coverage review — all shipped: **drag & drop** (NOOD_0009, `drags "Card" onto "Done"` → `actions.drag` → `locator.drag_to`), **soft assertions** (NOOD_0016, `@soft` collects failures through the scenario, `all soft assertions should pass` reports them at a chosen point), and **network assertions** (NOOD_0016, `a request to "X" should have been made` against a passive `page.on("request")` listener shared with Phase M).

## Phase M — Console & network error visibility

**Done (NOOD_0016).** `hooks.before_scenario` adds `page.on("console"/"pageerror"/"requestfailed")` listeners populating per-scenario lists (`context._console_errors`, `_page_errors`, `_failed_requests`), mirroring the existing `_downloads` lifecycle. `assert_no_console_errors`/`assert_no_page_errors`/`assert_no_failed_requests` turn silent page-under-test failures into first-class assertions with no code change to the page itself.

## Phase N — Browser context emulation

**Done (NOOD_0016).** Mirrors the existing `@viewport:WxH`/`NOODLE_VIEWPORT` pattern for geolocation, permissions, locale, timezone, and color-scheme (`@geo:`, `@locale:`, `@timezone:`, `@color_scheme:`, `@permissions:` tags or their `NOODLE_*` env equivalents, feeding `ctx_opts` before `new_context()`). Locale/timezone/color-scheme are context-creation-time only (no Playwright runtime setter); geolocation and permissions also get imperative runtime steps (`sets geolocation to`, `grants permission`).

## Phase O — Offline mode & network throttling

**Done (NOOD_0016).** Offline uses Playwright's native `new_context(offline=True)` plus a runtime toggle (`goes offline`/`goes back online`). Throttling has no Playwright kwarg — it's CDP-only (`Network.emulateNetworkConditions` via `new_cdp_session`), Chromium-only, with named presets (`slow-3g`, `fast-3g`) from standard Lighthouse values; a clear error fires on firefox/webkit.

## Phase P — Accessibility auditing

**Done (NOOD_0016).** Vendored `axe.min.js` (MIT, no network fetch at test time, no new pip dependency) under `agents/web/vendor/`; `agents/web/a11y.py::run_axe(page)` injects and runs it. `assert_no_a11y_violations` takes an impact threshold (`minor`/`moderate`/`serious`/`critical`) and raises with rule id, impact, and element count.

## Phase Q — Clipboard

**Done (NOOD_0016).** Chromium-only clipboard read/write via `page.evaluate("navigator.clipboard...")`, gated on Phase N's permissions plumbing (`clipboard-read`/`clipboard-write` granted at context level).

## Phase R — WebSocket observation

**Done (NOOD_0016).** `page.on("websocket", ...)` captures frames (`{url, direction, payload}`) into `context._ws_frames`; `assert_websocket_message` checks direction/contains. Mocking/rewriting WebSocket traffic is deferred until a real need shows up — observation only for now.

## Phase S — Print / PDF

**Done (NOOD_0016).** `emulate_print_media` (`page.emulate_media(media="print")`) and `save_as_pdf` (`page.pdf(path=...)`, Chromium-only). Print-layout *visual* verification composes with the existing pixel-baseline assertion rather than a second visual-diff engine.

## Phase T — Closed shadow-DOM via coordinate/OCR fallback

**Done (NOOD_0016).** Closed shadow roots are spec-level unreachable from JS/CSS — no selector crosses the boundary — so the fix routes through the coordinate math `agents/web/screen.py` already has for canvas/terminal UIs, rather than inventing a smarter selector. An opt-in tier (`NOODLE_OCR_FALLBACK=true` / `@ocr_fallback`) makes `locator.py::_find()` return a coordinate sentinel when the vision-LLM CSS attempt fails; `click()`/`assert_visible()` delegate to `screen.click_at`/`screen.assert_text_visible` instead of raising. Deliberately not extended to element-*state* assertions (disabled/checked/attribute) — those need real DOM access a closed root makes impossible without a Chromium-only partial-CDP build-out that isn't worth it for a handful of checks; documented as a hard limitation, not faked coverage.

## Phase U — Developer / agent / manual-tester experience

**Done (NOOD_0016).** Small additions on top of tooling that already worked: **LSP hover** (`noodle/lsp/server.py`) shows a recognized step's canonical example (from `docs/steps_dictionary.md`) in a tooltip; **VS Code snippets** (`vscode-extension/snippets/noodle.code-snippets`) cover the Phase M–T step categories plus existing major ones; **`noodle steps [keyword]`** CLI subcommand searches the step dictionary and prints matching examples to the terminal, reusing `noodle validate --resolve`'s pattern-classification path — faster than reading a 700-line markdown file.

## Phase V — Agentic depth: persistent memory & re-planning (open, owner decision)

Carried forward from NOOD_0030's gap analysis (Phase 22/23 above). Deliberately not built speculatively — needs an explicit yes/no from the project owner before scoping:

1. ~~**Persistent cross-session agent memory** — `noodle-agent` today has no memory across process restarts. A design decision on storage/scope comes before any code.~~ **Done (NOOD_0045).** `artifacts/agent_state.json` persists `last_feature`/`last_pom`/`last_app`/`last_run_target` across processes and sessions (`noodle/repl/core.py:load_state`/`save_state`).
2. **Multi-step tool-use / re-planning loop** — letting the shell replan after a failed step instead of one-shot generation. Multi-week, own subproject — only worth it once the current fallback (LLM + patterns) stops being enough. Superseded by the full analysis in Phase Y below (NOOD_0056), including a recommendation on whether this is worth building at all.

## Phase W — `noodle-agent` renamed to `noodle repl`, folded into `noodle` itself

**Done (NOOD_0056).** A peer-readiness review found the name itself was the biggest source of confusion: `noodle-agent` is a keyword-matched command dispatcher (regex routing, template-based generation) with an optional `--llm` tier that makes a handful of narrow, single-shot model calls — not an autonomous agent that plans and adapts across steps. Renamed the `noodle/agent/` package to `noodle/repl/` throughout code, tests, and docs (README's "Agentic mode" section retitled "Plain-English shell" to match). The unrelated `Agentic (AI) verdict` RCA feature (`noodle/rca.py`, an actual vision-LLM call) keeps its name — it was never the confusing one.

Second step, same ticket: having *two* installed binaries (`noodle` and a separate `noodle-repl`) was itself confusing — "two shells" reads like two products. `noodle/repl/repl.py:main()` was split into `run(workspace, llm, model)` (the loop itself) + a thin argv-parsing `main()` wrapper; `noodle/cli.py` gained a `repl` subcommand that calls `run()` in-process. The standalone `noodle-repl` console script (`pyproject.toml [project.scripts]`) was removed — there is now exactly one installed binary, `noodle`, and the shell is `noodle repl [--workspace] [--llm] [--model]`. No test invoked the old `noodle-repl` binary directly (all exercised `dispatch()`/`repl.main` functions), so this was a zero-regression merge.

See Phase Y below (NOOD_0056) for what, if anything, a *real* AI agent for Noodle would need.

## Phase X — `noodle repl` LLM-mode hardening + README accuracy (NOOD_0038)

**Done (planned NOOD_0038, shipped NOOD_0038 and follow-ups).** A peer-testing readiness review (2026-07-05) found classic mode and rule-based `noodle repl` ready to hand to peers, but `--llm` free-form generation was not, for one concrete reason confirmed against a real local model (Ollama `llama3.1:8b`): a generation that never became parseable Gherkin was written to disk anyway (`generate_llm` in `noodle/repl/generate.py` wrote the feature, POM, and resources unconditionally after a failed repair pass), and running it threw a hard `ParserError` instead of a clean failure. The fix gates the write on `result["error"]` after the one repair attempt — still-broken drafts are not written at all; the function returns `None` (its existing "nothing written" contract) and points the user at the rule-based fallback. Unmatched-but-parseable steps remain acceptable (they fall to the runtime LLM as before). Two README claims were also confirmed wrong against the actual CLI and corrected: `--no-capture` is a `behave` flag the internal subprocess already hardcodes, not a `noodle run` option (examples dropped it), and `llm_fallback.feature`/`pure_llm.feature` *fail* rather than skip without a configured model (reworded; later work under NOOD_0065 added true `@llm` auto-skip).

## Phase Y — Does Noodle need a real AI agent? Answer: no (NOOD_0056)

**Decided (NOOD_0056).** The follow-on question raised by the Phase W rename, analysed and settled. Baseline: every LLM call in the codebase — `generate_llm`, `_extract_plan`, `reflect.try_fix`, `_llm_pick_type`, the RCA vision verdict — is single-shot, wired into a fixed sequence Python already decided; nothing observes a result and chooses the next action. **Recommendation: no general-purpose autonomous agent.** Reasons: (1) `noodle-mcp` already answers "let an AI agent drive Noodle" better than an in-house agent could — any external agent (Claude Code, Copilot, LangChain/MAF) brings its own model, reasoning, and cost tradeoffs while Noodle stays a deterministic, auditable executor; (2) determinism is the product — an agent that "keeps trying things until the test passes" is a flaky-test generator; (3) `reflect.try_fix`'s one-shot keep-only-if-it-helped discipline is a safety rail, not a corner cut; (4) an autonomous loop needs spend budgets, step caps, and escalation paths that duplicate what host agents already do. Instead: harden the existing narrow, bounded, opt-in LLM assists (Phase X). A *bounded* tool-use loop for messy compound requests remains explicitly gated on observed peer demand, not scheduled.

## Phase Z — Production-readiness audit, CLI + MCP (NOOD_0058)

**Done (NOOD_0058, 2026-07-11, audited at `3e20517`, fixes shipped in `9762a6e`).** Every claim exercised live, not inferred from docs; 763/763 unit tests green going in. Verdicts: generation (CLI/REPL and MCP), validation, web execution, reports (CLI and MCP), the noodle skill, security, complex-prompt handling, and both ADO pipelines all **Ready**. Security detail worth keeping: MCP HTTP refuses non-localhost bind without `NOODLE_MCP_API_KEY` (checked via `hmac.compare_digest`); `write_feature` is path-locked (`resolve()` + `is_relative_to(tests_root)` + `.feature` suffix + Gherkin-validate); secrets files gitignored; the `run a command` step is a documented by-design trust boundary (same trust level as the .feature author); per-call workspace `.env` loading is first-wins for process lifetime — multi-tenant HTTP should run one server per workspace. Four gaps found and fixed in the same branch: login template now slot-fills quoted credentials; `search_step` returns `found: true` only on high confidence plus a scored top-3 `candidates` list; feature naming strips URL tokens/quoted values (keeps credentials out of filenames); both pipelines' `discover` job runs the unit suite before the shard matrix fans out (fail fast, and Windows finally executes unit tests on `windows-latest`). Backlog deliberately left unranked: visual-regression baseline management, flake analytics, step-usage telemetry, per-call env isolation, API-mock recording proxy, Key Vault secrets.

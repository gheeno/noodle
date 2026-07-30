# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/). The 1.0.0 alpha series counts up (`1.0.0a1` → `1.0.0a2`, PEP 440 spelling of `1.0.0-alpha.01`); the `0.2.0aN` line below it is pre-1.0 history.

## [Unreleased]

## [1.0.0a18] — 2026-07-30

**NOOD_0202** — feature: a CI job can see what the run is doing, and an LLM
session still doesn't pay for it.

`--quiet` is automatic off a TTY, so every CI job got it — and quiet folded the
behave child's *stderr* into `run.log` alongside its stdout. An Azure job
watched a 20-minute suite in total silence, and the scenario/step events
documented in `docs/logging.md` never reached the log stream at all: a
`NOODLE_LOG_FORMAT=json` run shipped exactly two records (`run.start`,
`run.end`), both from the parent CLI. The KQL in that doc could never have
returned a scenario-level row.

The file log was always complete — this makes the *console* a sink decision
rather than an on/off one.

- `log.progress_mode()` — is a build console watching? `NOODLE_LOG_PROGRESS`
  explicit `0`/`1` wins, else on by default when `CI`/`TF_BUILD` is set (no
  pipeline yaml change needed), else off.
- Progress mode streams a maven-style build log: `run.start`, one line per
  feature, one per scenario start and result (icon, name, elapsed), failures
  inline, and a `run.end` verdict line. `--log-level DEBUG` adds a line per
  step — the `mvn -X` tier that shows where a wedged suite stopped.
- `log.attach_progress_handler()` carries only records with an `event` plus
  every `WARNING+`, so the per-action firehose (81 logger sites in
  `actions.py` alone) stays in `run.log` where it belongs.
- Token guard: the agent doors set `NOODLE_LOG_PROGRESS=0` themselves
  (`repl.core._engine`, `noodle run --json`) rather than trusting `CI` to be
  unset — an AI-SDLC orchestrator runs *inside* the pipeline, and its captured
  subprocess pipes stderr straight into the payload the model reads.
- The run log now rides the agent payload by **path** (`log`), so a model reads
  it with its own file tools when a run goes red instead of streaming it on
  every green one.
- `telemetry()` also fires in text mode under progress mode; the NOOD_0173
  "json only" gating held on the premise that a human already had behave's
  output, which is exactly what `--quiet` takes away.
- The build log is level-prefixed and emoji-free (`[INFO]` / `[WARNING]` /
  `[ERROR]`) — it's a log file, not a terminal, so it has to grep and diff, and
  a failed scenario logs at ERROR so `grep '^\[ERROR\]'` yields exactly the
  failures. Stripping happens at that sink only: the local TTY console keeps
  its breadcrumbs (NOOD_0171's unchanged-console contract).
- **Parallel runs are readable.** N behavex workers all stream to one stderr, so
  a 4-worker run was four features' scenarios shuffled together with no
  attribution — two identical `Step failed` lines in a row belonged to different
  features. Every line now carries its lane (`[w3]`), claimed at worker startup
  and matching `NOODLE_WORKER_INDEX`, so `grep '\[w3\]'` replays one worker in
  order. json records carry `lane` next to `worker`/`feature`. Sequential runs
  get no tag — nothing to disambiguate.
- An **engine** exception is distinguishable from a test failure. A
  non-`AssertionError` names its class and message under the failed step; the
  six behave boundaries (`before_all` … `after_all`) log class, message and
  traceback before re-raising, because behave otherwise reports a hook crash as
  one `ABORTED: HOOK-ERROR` line with the traceback on stdout — which `--quiet`
  had already diverted to run.log. Ordinary engine functions stay unwrapped: a
  blanket try/except swallows failures rather than surfacing them.
- The progress handler attaches as the *first* statement of `before_all`, so a
  crash during run setup still has a console to report itself on.
- A retried scenario is marked `[retry 1/1]`. behave's autoretry re-enters the
  hooks, so the same scenario printed twice with nothing saying why — which
  reads as a duplicate, or as two scenarios sharing a name.
- The run-wide TLS warning (`NOODLE_IGNORE_HTTPS_ERRORS`) is said once per run
  instead of once per browser context. It fired 109 times on a 109-scenario
  suite — a quarter of the whole build log. `@insecure_certs` still warns per
  scenario, where it is a per-scenario decision and the step's RCA should
  carry it.

## [1.0.0a17] — 2026-07-29

**NOOD_0201** — feature: the API wok grows up — localhost discovery, live
OpenAPI probing, batch steps, typed JSON assertions.

An AI-SDLC session authored a seeding test against a Spring Boot service and
produced two defects the web wok would never ship: 21 pasted
`performs a POST call` lines with a single trailing status assertion (which
only ever checked the last call), and a test aimed at `POST /greeting` when
the app served `POST /greeting/new` — the real path was one spec fetch away
the whole time. Root causes: neither deterministic authoring path could emit
a data table or a loop, and nothing in the engine could discover a base URL
or an endpoint list. Five changes:

1. **Batch REST steps.** `performs a POST call at '<path>' with body
   '<template>' repeated N times expecting status 201` (`{i}` = call number)
   and the `for each row` data-table twin (headings name the `{placeholder}`
   tokens, substituted into path and body per row). `expecting status`
   asserts EVERY call and fails naming the row; batches cap at 1000 (volume
   belongs to the perf wok). Docstring bodies
   (`with this body:` + `"""…"""`) for payloads that don't fit on a line.
2. **Typed JSON assertions.** `the response json '<path>' should equal '42'`
   walks the extraction path family and compares numbers as numbers,
   booleans/null by name — a substring check can't tell `"count": 20` from
   `"count": 200`. Plus `should contain` and `should have N items`
   (`'$'` = root).
3. **Goal + prompt grammar reach all of it.** The `api` action gains
   `rows`/`repeat`/`expect_status` (compiled to ONE step; a batch with
   `expect_status` satisfies the assertion-required rule), checks gain the
   `json` kind, and the planner reads `create 20 rows using POST <url> with
   body '<template>' expecting status 201`, `repeated N times` tails, and
   rooted api paths (`POST /api/greeting`). A missing url/body template is
   refused with the exact one-shot phrasing; an unrecognized tail
   (`with headers …`) is refused, never silently dropped.
4. **Live discovery (`noodle api-scan` / MCP `probe_api`).** With no URL:
   sweep the well-known localhost dev ports — plus ports the repo's own
   config names (`server.port`, compose mappings, `PORT=`) — and report every
   live HTTP server, api-shaped or not (macOS AirPlay filtered). With a URL:
   fetch the OpenAPI document from the well-known routes (`/openapi.json`,
   `/v3/api-docs`, …) and return the REAL endpoint list, request-body hints
   derived through `$ref`, and copy-ready steps. `author_test` adopts the
   single unambiguous candidate automatically (payload carries
   `discovered_base_url`; app named from the spec's own `info.title`);
   ambiguity returns candidates + questions — Noodle still never guesses a
   URL. Compiled features with relative api urls now get the
   `REST_BASE_URL` Given bound to the app's stored `{env:}` key (previously
   the join was silently `"" + path`), and an IP-hosted base URL no longer
   derives an illegal `{env:}` key.
5. **Playbook decision rules.** Repetition (one step, N executions — table
   vs `repeat` vs Scenario Outline), custom scripts
   (`resources/functions/*.py` + `calls the function` when the vocabulary
   can't say it), and the no-context API flow (scan repo → api-scan →
   probe → author with relative paths).

Also: the last Canadian Tire reference is gone (a comment in `hooks.py` keeps
its false-pass lesson without the name).

Second pass — the deferred gaps closed, and all of it validated against a live
service instead of only in unit tests:

6. **Polling and response contracts.** `waits until '<path>' returns status
   200 [and the body contains '<x>'] [within N seconds]` retries until the
   condition holds (the REST twin of the web wok's smart wait — an endpoint
   that answers 202 and finishes the write later used to be a race), and
   `the response should match the schema '<file>'` validates the whole body
   against a JSON Schema file in the app's `resources/`
   (type/required/properties/items/enum/bounds; no `$ref`/`oneOf`, so the base
   install stays dependency-free).
7. **Auth and chaining reachable from a goal.** The `api` action takes
   `headers`, `auth: {scheme: bearer|basic, ...}` and
   `store: {VAR: '<json path>'}` — one goal action compiling to several steps,
   which is why login-then-chain previously forced hand-written
   `feature_content`.
8. **`noodle ticket` / MCP `plan_from_ticket`.** A raw JIRA issue payload
   becomes one authorable goal per acceptance criterion, deterministically:
   Atlassian Document Format walked, `SPEC_LINK`/sha256 boilerplate discarded
   instead of read as intent, criteria split into Given/When/Then, and each
   criterion's endpoint **resolved against the routes the service really
   serves** (singular/plural folded, so `POST /api/review` finds
   `POST /api/reviews/new`) with the correction recorded as an assumption. What
   the API cannot show lands in `not_automatable` with the reason; what the
   ticket never said lands in `questions` behind a visible `<value>`
   placeholder.
9. **BusterBlock became the api wok's local target.** A reviews resource
   (`POST /api/reviews/new` validate→persist→return, paginated newest-first
   `GET /api/reviews`, bearer-protected delete), a published
   `/openapi.json`, and a `/reviews.html` page that renders the list so
   cross-wok scenarios are real. The create route is deliberately
   `/api/reviews/new` while tickets say `/api/review` — the correction has to
   come from discovery.
10. **Four new sample features, 19 scenarios, all run green live:**
    `api/features/busterblock_api.feature` (polling, typed JSON, schema,
    docstring body, payload file, bearer auth + id chaining, negative paths),
    `busterblock_batch.feature` (20-in-one-step vs data table vs Scenario
    Outline — the replacement for the 21 pasted calls),
    `busterblock_from_ticket.feature` (authored from the JIRA payload alone),
    and `web/busterblock/features/api_seeds_ui_verifies.feature` (API seeds →
    UI verifies, and UI submits → API confirms).

Fixed while testing it for real: the localhost sweep missed port 3333 and did
not read the ubiquitous Node `PORT || 3333` / `.listen(3333)` idiom, so the
bundled test app — the exact case discovery exists for — was invisible to it.

**NOOD_0200** — feature: sequential authoring serves every run's reports, the
probe survives promo-heavy pages, and the screenshot claim stops costing a
shell approval.

A three-test authoring session on a non-frontier host (MCP blocked, CLI
path) measured ~15–20 AIC and 5–10 minutes per test against a popup-heavy
retail site, burned one human approval, and delivered no served report for
the second and third test. Three root causes, none of them the prompt
grammar — all three of the session's prompt shapes already one-shot through
the compiler (now pinned offline in `unit_tests/test_nood_0200.py`):

1. **The CLI's serve default.** Report *generation* is engine-automatic, but
   *serving* was automatic only on MCP `run_and_report`; plain `noodle run`
   defaulted `--serve` off, and the CLI is the primary door when MCP is
   blocked (NOOD_0199). Runs two and three built reports nobody hosted.
2. **The probe swept popups once, at initial load only.** The overlays a
   promo-heavy site drops on the results page, the picked product page, or a
   prerequisite trial's restore reload polluted the landed-page control set,
   so the mutation proof (add-to-cart) failed, the goal blocked, and the
   repair lap re-launched a browser and re-probed — the minutes and the AIC.
3. **"Open the image" with no path in hand.** The run payload never named
   the evidence screenshot paths, so a shell-driven agent hunted through
   `rca.md` and reached for `open`/`cat` — the human-approval prompt.

### Added
- `noodle run` hosts both reports after every run by default and prints the
  URLs; `--serve/--no-serve` is now a tristate override. Auto-off when
  `CI`/`TF_BUILD` is set (a runner has no human to click a link and would
  leak a detached server per job); `NOODLE_SERVE_REPORTS=0/1` overrides in
  either direction. Unit-test suites are guarded by a conftest fixture
  setting the kill-switch.
- Run payloads (`core.run_and_report`, `noodle run --json`, and the MCP tool
  via core) carry `evidence: [{step, status, path}]` — every image attached
  to a step — so an agent reads the screenshot with its harness file reader
  instead of shelling out to find it. Always-on surfaces now say exactly
  that ("read the image at `evidence[].path`").
- The probe's search-results block, the picked landed page, and every
  mutation-trial restore re-run the popup sweep (same sweep + short
  re-settle a probe-followed tab already got), counted into the existing
  `popups_closed` disclosure.

### Fixed
- HIL-bait instructions scrubbed from agent-facing surfaces: the workspace
  README and both skill cards authored via a spec *file* (a file-write
  approval) — now `noodle author --prompt "<ask>" --run`; the playbook
  recommended a heredoc for `--spec -` (now: inline `--spec '<yaml>'`,
  never a heredoc) and an end-of-session `ps aux`/`kill` sweep (now: stop
  what you started through the harness's own controls); the skill cards'
  "grep `run.log`", the encyclopedia's `python -m http.server` row, and the
  external-site walkthrough's `curl | grep` recon recipe (now the probe)
  are gone. `docs/agent-playbook.md` leads reuse discovery with
  `noodle list --query` instead of `grep`/`rg`.
- `docs/llm-performance.md` §7 now carries the real AIC ladder — target
  < 12 (the NOOD_0195/0199 postmortem bar), ≤ 17 acceptable with a
  cost-delta explanation, 25 hard ceiling — instead of contradicting the
  CHANGELOG's < 12 target.
- Instruction-budget ledger: no cap raised. AGENTS.md 5125 → 5103 bytes
  (headroom 529), claude card 7149 → 7129, copilot card 7294 → 7292;
  `noodle run --help` grew ~26 bytes for the tristate flag (headroom 98).

---

**NOOD_0200** — fix: end-state evidence — stop validating the last page
against the first one.

The evidence pass validated an unanchored check against the page the probe
*started* on while the compiler (NOOD_0158) ran it against the page the flow
*ends* on. On a goal whose actions carry the page past the probe's reach
(fill → save → fill → submit, or search → pick → add-to), that was wrong in
both directions: it hard-blocked assertions that are genuinely true on the
post-submit page, and it "proved" assertions that merely matched landing-page
footer/nav wording — shipping a green, `verified: true` test that verified a
different page than the one it asserted. A three-attempt authoring session
(every attempt either blocked on true assertions or passed on false ones)
traced to this one defect.

### Fixed
- `goal.evidence()` routes an unanchored check to `runtime_asserted` whenever
  the goal's actions go beyond the probe's reach (`_beyond_probe_reach` —
  reads the action grammar only, no domain knowledge): the run, the only
  honest witness for that page, proves it. `after: start` still pins a check
  to the landing page with full pre-run proof; search/suggest/pick-only goals
  keep their probe proof untouched. The probe still never crosses a data gate
  (probing commits mutates state) — by design; the evidence pass now knows it.
- Blocking messages carry their own repair: the missed-check block names the
  three legal moves (fix the text / anchor with `after:` / leave unanchored on
  a gated flow), and the ambiguity block names the candidate it refuses to
  guess. Rule adopted: no `blocking` entry may describe a state without
  naming a legal next input.
- A blocked author writes and (on the `--run` path) serves the RCA pair
  anyway — verdict `authoring-blocked`, the blocking list, `next_action`,
  failed intent-trace rows. No browser, milliseconds; the engine no longer
  goes silent at the exact moment the operator needs a written explanation.
- Compiled POMs are a `pages:` block resolved through the feature's own
  `@page:<slug>` tag instead of folder-global `match: {}` — a second compiled
  feature in the same app no longer shadows the first's same-named keys
  (warning noise today, silently wrong element tomorrow). Same reach within
  the feature (the pin spans every page the scenario visits), invisible to
  siblings.
- Probe `dispatch_event` fallbacks are time-bounded (3 s, matching the click
  budget beside them) — a hidden trigger that never attaches no longer eats
  30 s of the discovery budget per attempt. The fuller SPA settle-heuristic
  from the field report is deliberately deferred behind a wall-clock
  measurement (it is the only piece that can cost time on every probe).

### Added
- `evidence.proven_phase` — which probe phase's page each proven check
  matched on (`initial`/`search`), riding the author payload next to
  `proven`; `probe_summary` now also counts run-proven checks. Probe-proven
  vs run-proven is readable per fact without diffing JSON.
- `unit_tests/test_nood_0200_end_state.py` — synthetic gated-flow fixture
  (generic control names only) pinning: end-state checks route to the run and
  never block; landing-page wording can never prove an end-state check;
  `after: start` keeps landing proof; search/suggest/pick goals keep pre-run
  verification; the pinned-POM resolver isolation; the blocked-author report;
  the bounded dispatch.

---

## [1.0.0a15] — 2026-07-29

**NOOD_0199** — feature: the DOM-attribute heal tier survives a page too large
to walk whole (ERP/CRM SPAs).

The DOM-attribute scan — the tier that resolves elements named by machine
identity (`e2e_grid_row-actions`, a dynamic id, a hidden dev panel) rather
than by anything a human can read — walked at most 3000 elements, hardcoded at
import time. An element counts toward that the moment it carries *any* of the
collected attributes, and `class` is one of them, so on a component-framework
app where every styled `div` has a class the cap is really "the first 3000 DOM
nodes". An ERP screen clears that on its chrome and ribbon alone, and the grid
and form content a step actually targets sits past the end of the walk.

Four things were wrong with how that played out. The cap could not be moved.
Hitting it produced a **silent** miss — `best_selector` returned `None` with no
log line, so "the target was past the cap" and "no such element exists" were
indistinguishable in the log of a step about to fail. Scoping the step to a row
or panel, the obvious mitigation, bought nothing: the collect JS hardcoded
`document`, so a `Locator` scope still walked the whole page and still
truncated at the same point. And on a page that never satisfies the settle
early exit — websocket tiles, a polling notification badge, a live clock, all
routine in this class of app — the poll ran its full budget and repeated that
whole-page walk every 5 seconds throughout, roughly 24 times per failed find.

Unchanged: this is a heal tier, below POM and the accessibility strategies,
which query the full document and have never had a cap. Anything it resolves
is still a fuzzy match that reports `verified: false`.

### Added
- `NOODLE_DOM_SCAN_MAX` (default 3000) sets the walk cap. Unset, unparseable
  and non-positive values all mean the default — a typo must not silently
  disable the tier by collecting zero elements.
- A truncated walk that finds nothing now warns with the cap, the page's real
  element count, a suggested higher value and the `pom.yaml` alternative. A
  walk that completes, or one that truncates but still matches, stays quiet.
- `docs/manual.md` troubleshooting entry for the ERP/SPA case, and the knob in
  the generated `.env` stub and `docs/encyclopedia.md`.

### Fixed
- A `Locator` scope now genuinely narrows the walk. Playwright calls
  `Page.evaluate(fn, arg)` as `fn(arg)` but `Locator.evaluate(fn, arg)` as
  `fn(element, arg)`; the collect function branches on which shape arrived and
  roots itself accordingly, counting the scoped element itself as a candidate.
- DOM re-scans within one `find()` are capped at `_DOM_SCAN_MAX_PASSES` (6).
  The accessibility strategies keep retrying every 100ms and stay uncapped;
  only the expensive whole-page walk is bounded.

---

**NOOD_0199** — fix: the `--prompt` door stops eating prompts, and the
MCP-blocked CLI path stops paying a shell approval to find its own command.

Two pastes that used to one-shot were measured at ~17 AIC (target < 12) on a
Copilot/Codex host with MCP blocked, one of them burning a human approval on
`noodle author --help | sed`. Running both through `expand()` said why: the
first came back **`ok: true` with `actions: []` and `checks: []`** — the
engine reporting success for a test that does nothing. A prose prompt was
never split into sentences, so the whole flow was ONE clause; that clause
mentioned "headed mode", which classifies a WHOLE clause as run-mode
metadata; what survived was dismissals. The agent then paid inferences to
discover the emptiness. This is not a recent regression — the file behaves
identically at every revision back to the clean-history baseline — but it is
the gap between "the prompt door exists" and "the prompt door works", and
it is what the AIC went to.

### Fixed
- **A goal with no action and no check is a blocker, not an empty `ok:
  true`.** It names the rewrite (numbered steps + the grammar) and carries a
  `suggested` line per clause. The silent-empty class is closed at the door.
- **An incidental run-mode mention no longer swallows its sentence** —
  "once on the page on headed mode or even headless the location prompt
  appears" kept exactly one clause's worth of metadata and dropped the flow.
  The aside is lifted out and the sentence keeps parsing; a clause that IS
  only a run note is untouched and still filed as metadata.
- **Prose is split into sentences** before connectors, on a capital OR a
  lowercase grammar verb after the stop (humans type "go to x.com. search
  for kettles"), and never inside a URL.
- **Preamble stripping runs on both halves of a compound** — `_depreamble`
  is factored out and `_recognizable` applies it, so "verify X and then use
  the search bar to search for Y" splits (the verb table is `^`-anchored, so
  the right half looked verbless and the split never happened).
- **Assertion filler is stripped** — "verify that there is at least 1 result
  found with the title A or B" reached the results-count grammar for the
  first time; it used to leak the whole sentence into the `any_of` members.
- **`PROMPT_TEMPLATE.md` parses at the door it feeds.** `Base URL: [ … ]` is
  navigation, `User goal:` wraps the flow, `Credentials/config:` and friends
  are named metadata (with the note that credentials belong in `--spec`
  `secret_values`), and a bracketed placeholder is punctuation. `Verify:`
  stays grammar. Noodle's own template used to hard-fail `noodle author
  --prompt`.
- **The narrative dismissal** — "a few known popups would appear, close
  those too" — is read as a dismissal. The thing and the close land in
  different comma segments, which `_CONDITIONAL` (if/when, one clause) never
  covered, so the sentence compiled into a click on a 200-character target.
- **Prompt ORDER anchors a check to the page it observes.** A check written
  before the first action now compiles with `after: start`, so the evidence
  pass matches it against the landing page. Unanchored it scoped to the
  post-search page (`goal._check_scope`), and a fact the probe had literally
  proven on the landing page blocked authoring. Goals with no actions are
  untouched — there is no later page to confuse them with.
- **A decorated-text check narrows to its probed substring instead of
  blocking** — asked "verify X Y image" against a page rendering "Banner 2
  of 8 View the X Y now.", neither string contains the other, so the check
  blocked on a page that plainly showed it. `goal._shared_phrase` finds the
  longest contiguous run of the ask that a probed text contains (floor: two
  whole words, the same floor `_find_text` uses), narrows the check to it,
  and the payload carries `check narrowed: asked for …, asserting … — the
  probe found it inside …`. This is the "assert the smallest stable
  substring" rule the workspace tells authors to apply, done by the engine
  WITH provenance. A one-word overlap still blocks.

### Added
- **Markdown-brief shapes**: `—`/`–`/`>` bullets; a `Steps a human would
  take:` header (which also makes the `User goal:` line above it a scenario
  title, not a second bogus search); markdown table rows as expected values
  (`| Product A |` → one check per cell); scene-setting narration ("then a
  suggestion bar appears below the search bar") ignored and NAMED in
  `assumptions` — deliberately narrow, so "the cart badge shows 1" is still
  refused rather than silently dropped.
- **`Click the Suggestion : "X"`** resolves to the typeahead `suggest`
  action (spaced colon + quoted option), and a quoted search term ignores
  the aside after it (`search for "<partial>" ( needs to be incomplete )`).

### Changed
- **Scaffolded `AGENTS.md` carries the literal one-shot command** —
  `noodle author --prompt "<the ask, verbatim>" --run --json`, with "ask
  passed RAW, never `--help` first". An MCP-blocked agent reads AGENTS.md
  and nothing else; without the invocation it spends a shell approval on
  `author --help` (5.9 KB, which NOOD_0165 fattened for exactly this
  reader) before authoring. Net +86 bytes inside the ledger cap, paid for by
  trimming the `base_url_key`, `append_to` and result-pick lines — no cap
  raised. Existing workspaces refresh with `noodle init --force`.
- `unit_tests/test_nood_0199.py` — one file, both halves (§1 DOM scan, §2
  prompt door), 36 tests; both measured pastes as fixtures (shapes verbatim,
  site-neutral content).
- **The §1 truncation-warning tests read the output that actually carries
  it.** They asserted on `caplog`, but `noodle/log.py` sets
  `logger.propagate = False` and attaches its own stream handler, so no
  record reaches the root logger pytest captures: `caplog.text` was empty
  even when the warning fired. The warn test failed and both quiet-path
  tests passed for the wrong reason. They read `capsys` now.

Verified live: both pastes, one `noodle author --prompt <file> --run` call
each, `ready: true` → `passed: 1, failed: 0, verified: true`, planner
`VERIFIED` with 1 probe, 1 run and 0 interpretation model calls.

## [1.0.0a14] — 2026-07-28

**NOOD_0198** — fix: a prompt written to a file gets into Noodle without going
through the shell.

The AI-SDLC handoff — an orchestrator finishes a user story, writes the test
ask to a file, invokes Noodle — had exactly one way in for plain-English
steps: `noodle author --prompt "$(cat story.md)"`. Inside double quotes the
shell command-substitutes every backtick, and a machine-written markdown file
is mostly code fences. That both corrupted the prompt and turned the
generator's output into a shell-injection path. `--spec` had already grown a
path/`-`/inline rule in NOOD_0197; `--prompt` and `task TEXT` had not.

### Fixed
- **`noodle author --prompt` and `noodle task TEXT` accept a file path or `-`
  (stdin)**, the same rule `--spec` uses: `-` reads stdin, an existing file
  reads that file, anything else is the text itself. One helper
  (`cli._arg_text`) now serves all three doors, so the `--spec` branch loses
  its duplicated path-resolution. A path-shaped `--spec` that names no file is
  still rejected as a typo, and an inline YAML document still parses.

## [1.0.0a12] — 2026-07-28

**NOOD_0196** — fix: an `--expect` verdict now answers for a goal that never
searches, so the feature-regression benchmark stops blocking a check its own
probe proved.

`noodle feature-regression` on `main` came back REGRESSED on
`tc3_api_seeds_ui_verifies` — while the test it generated ran green and
verified. Authoring blocked on

    check "From Wikipedia, the free encyclopedia": no probed heading or
    control shows that text

against a probe payload recording that exact string as `found: true`.
NOOD_0195 scoped expect verdicts to checks observing the search landing page,
correct when the goal searches — but with no search in the goal the probe
*ends* on the initial page, so the verdict describes the very page the check
observes. Discarding it there blocked every check on body prose (Wikipedia's
`#siteSub`), which a structured capture of headings and controls never
carries. A check anchored before a search still ignores expect, and a
`found: false` verdict still blocks.

### Fixed
- **Every `Path.read_text()` in `noodle/` now passes `encoding="utf-8"`** (34
  files). Python picks the *locale* encoding when it's omitted — cp1252 on a
  default Windows 11 install — so any UTF-8 byte in a `noodle.yaml`, a POM, a
  `.feature`, a steps dictionary, or an agent-patterns file raised
  `UnicodeDecodeError` on Windows while reading fine on macOS/Linux.

## [1.0.0a13] — 2026-07-28

**NOOD_0197** — fix/feature: the retail-search session review
(workspace 195), fixed at the engine. A green that was weaker than the ask,
reached in 4 invocations instead of 1: `any_of` compiled to a conjunction,
the prompt grammar rejected 3 of 6 ordinary-English steps, RCA blamed a
background POST on a read-only scenario, and the CLI forced a heredoc the
moment `--prompt` missed. (Version jumps a11 → a13: a12 is taken by the
in-flight `patch/nood_0196` branch.)

### Fixed
- **`{any_of: [A, B]}` is a real disjunction** — compiles to ONE
  `the user sees any of "A", "B"` step (`at least N of` when `min` > 1),
  resolved by the new `assert_any_visible` action: passes when ≥ min
  alternatives are visible under one shared smart-wait budget, and logs
  WHICH member satisfied it. Replaces both NOOD_0195 shapes — the
  per-member conjunctive expansion (a logic inversion: "A or B" went red on
  a page correctly showing only A) and the union-selector count fallback.
  Dictionary entries added ("any of" / "either" / "one of" /
  "at least N of"), found by `noodle step-search`.
- **RCA never claims `mutation-failed` on a read-only scenario** — the
  correlation is gated on the scenario's own steps carrying something
  mutation-shaped; background app XHR aborted by navigation no longer
  produces "fix the ACTION" advice on a flow with no mutating action.
- **RCA cites the goal node behind the failing step** — authoring persists
  `intent_trace.json` (now carrying each check's terms); the compact RCA
  appends `goal: … checks[i] …`, and a multi-term check names the
  compilation as suspect #1.
- **`failed_requests` capture keeps its failure tail** — `_safe_url` over
  the whole `METHOD url — failure` composite destroyed everything after the
  first `?`, so query-string mutations silently vanished from
  `mutation_verdict`. URL-only redaction now.
- **`feature_path` relocation is declared** — requested vs written paths
  both ride the payload (`feature_path_requested`, a `warnings` entry) when
  the wok layout moves the target, instead of silently echoing only the
  final path.

### Added
- **Prompt grammar: the phrasings that failed** — conditional dismissals
  ("if the location prompt appears, close it", + `consent`), instrument
  preambles ("use the search bar to …" / "using …" / "via …"), narrative
  preambles ("As a user, I would like to…", "On the results page,"),
  result-set assertions ("at least 1 result with title A or B" →
  `any_of`+`min`; untitled → `count`), and "verify A or B" disjunctions.
  Positive-visibility tails ("… is shown") and leading articles are
  stripped from `see` literals.
- **Partial parse instead of a hard stop** — a rejection now returns
  `goal_partial` (built from the clauses that DID parse) plus a concrete
  `suggested` rewrite per unresolved clause; the error names the
  `NOODLE_MODEL` opt-in for the one bounded interpretation call.
- **`noodle author --spec` accepts the document inline** — a non-path
  argument that parses as YAML/JSON is the spec itself: no heredoc, no
  temp file, no shell approval. `--vocabulary` prints the goal schema +
  example + prompt grammar on demand (no more learning the schema by
  triggering rejections).
- **`noodle doctor` checks Playwright browsers** — `install.browsers`
  warns when no browser binaries exist on disk (read-only, never spawns).
- **`probe_summary`** — one scalar line narrating the internal probe
  (facts proven, popups closed, prompts, reuse), so the probe-inside-author
  is auditable instead of looking skipped.
- **Feature-regression gate** — `noodle feature-regression` (exit 0)
  required before every engine-branch PR; wired into CLAUDE.md,
  CONTRIBUTING.md, and the Copilot instructions (which also stop saying
  "both" test cases — there are three).
- **Parity guard** — a unit test pins every `_author_test_impl` parameter
  reachable from BOTH the CLI author door and the MCP `author_test` tool.

### Changed
- AGENTS.md template + skill cards: an OR is ONE `sees any of` step, never
  narrowed to a member to go green; `--spec` inline (never heredocs);
  `noodle author --vocabulary` named next to the no-pipes rule — all within
  the instruction byte budgets.
- Playbook: "Updating an existing test" (overwrite / `append_to` /
  `noodle task "update …"`), and the `mutation-failed` guidance now
  documents the read-only gate + goal-node citation.

## [1.0.0a11] — 2026-07-28

**NOOD_0195** — fix: a `ready: true` goal now compiles to something the
evidence checker can verify, and the goal vocabulary is published before the
rejection that would teach it.

From a postmortem of a 33.04-AIC session against a < 12 AIC target: 15.02 AIC
(45%) was waste, and 10.45 of that was engine/docs defect rather than agent
judgement — the prompt could not have one-shot as shipped.

### Fixed
- **`any_of` no longer compiles to an unverifiable count assertion.** A check
  over two probe-proven product titles compiled to
  `Then should see at least 1 "result titles"` plus a synthesized regex POM
  key. That step resolves no *single* element, so `hooks.after_step` saw no
  fresh match, drew no box, and marked the shot invalid: the run reported
  `passed: 1, failed: 0, verified: false`, which the workspace rules correctly
  refuse to call green — 6.78 AIC of mandatory rework behind an author that
  had said `ready: true`. Where the probe rendered every member IN FULL, the
  compiler now emits one literal `the user sees "<member>"` per member and no
  POM entry (an AND — what a prompt listing expected products asks for),
  carrying the `evidence: screenshot` marker onto each. A member seen only in
  part keeps the count form: a `see` for text the page never renders in full
  would trade a false green for a red run.
- **A genuine `count` assertion now produces valid evidence too.**
  `assert_count` resolves through `pom.locate_all`/`get_by_text` directly,
  never through `find()`, so `match_seq` never moved and *every* count step
  shipped an unverified screenshot — hand-written features included. On a
  passing count it now registers its first counted element
  (`locator.note_match`), which is real and exactly resolved, so the shot
  centres and outlines it. A failing count registers nothing.
- **Near-miss repairs are named instead of re-derived.** An invented
  `do:` gets `did you mean 'suggest'?` beside the valid list
  (`pick_suggestion` → `suggest` — one composite action, not the two the
  author reached for), and a `suggest` option that misses names the closest
  captured spelling (`did you mean 'vaccum cleaner'?`).

Verifying the above against the live site — four re-runs of the postmortem's
own prompt — surfaced five more defects of the same class, all fixed here.
Each was invisible until the layer above it stopped hiding it.

- **A `suggest` goal never probed the page the suggestion lands on.**
  `probe_args` didn't pass `follow` (NOOD_0142, shipped and working), so the
  probe read the typeahead, closed it, and returned only the LANDING page.
  Every check on the results page therefore had no evidence source at all.
- **A one-letter control name could prove anything.** `_find_text` accepted
  reverse containment with no floor, and the live retail landing page
  carries a control literally named `a` — which satisfied both 41-character
  product titles. `evidence()` recorded `proven any_of[0] == ['a']` and the
  goal reached `ready: true` on it. A reverse match now needs at least two
  whole words of the requested text; the truncated-caption case it exists for
  is unaffected.
- **An unanchored check took its evidence from the wrong page.** NOOD_0158
  made it assert the END state and `compile_goal` emits it after every action,
  but the evidence pass still matched it against the landing page — checked
  against one page, asserted against another. It now scopes to the landed
  page, and `suggest` counts alongside `search` in that decision. A
  landing-page check in a search goal needs `after: start`, which is what that
  anchor is for.
- **Search-result captions were invisible to the evidence pass.**
  `_block_texts` read headings and control names only, so a `see`/`any_of`
  naming a real product hard-blocked with "no probed heading or control shows
  that text" while the probe held the caption in structured `result_items`.
- **`suggest` was still in `_runtime_gate`**, whose rationale — "a suggestion
  CLICK-THROUGH never happens" — the `follow` fix above had just made false.
  Every check after a suggest was routed to runtime-asserted: never proven,
  never blocked, never eligible for the literal upgrade. That is a
  `ready: true` that checked nothing.
- **`probe_args` now hands the checks' literals to `--expect`** (NOOD_0142, up
  to 8), an exact full-text search of the page the probe ended on. Structured
  captures are lossy — result captions truncate at ~60 chars, so a 68-char
  product title could never be proven whole from them, and the literal upgrade
  could never fire on the flow it was written for. An expect `FOUND` is
  full-render proof. Scoped to end-state checks: an `after: start` check
  cannot borrow a results-page verdict.

### Fixed — Windows

The CI Windows cell had never once reported on the code: it died at test
COLLECTION on a doc emoji, so 113 real failures sat behind a trivial one. With
collection unblocked, Noodle turned out to be materially broken on a platform
`docs/llm-install.md` ships a runbook for.

- **UTF-8 is explicit on every file read and write** (639 sites across engine
  and tests). Windows encodes text as the ANSI code page, so the engine wrote
  cp1252 and read back UTF-8: an em dash in a scaffolded header became `0x97`
  and every later read of that file failed. `config.write_private` — the
  secrets/env writer — hid behind `os.fdopen` and was the single biggest
  source. `noodle steps` crashed outright reading its own steps dictionary.
- **The console speaks UTF-8 regardless of the OS default** (`cli.py`). Windows
  gives stdout cp1252, which has no `✅ ⚠️ ❌ 📸` — symbols the engine prints on
  every run. A single one raised `UnicodeEncodeError` from `typer.echo` and
  took the command down; worse, it did so while REPORTING another error, so
  the real failure was invisible. `errors="replace"` keeps a genuinely legacy
  console degrading to `?` rather than crashing.
- **Workspace-relative paths are POSIX.** `os.path.relpath`/`relative_to`
  yield backslashes on Windows, but these are cross-process identifiers in
  `agent_state.json` and run payloads — a path written by the MCP server no
  longer matched one written by the REPL.
- **The liveness probe no longer Ctrl+Cs a process group.** `_pid_alive` used
  `os.kill(pid, 0)` — the POSIX idiom, and actively destructive on Windows,
  where signal 0 IS `CTRL_C_EVENT`: Python calls `GenerateConsoleCtrlEvent`, so
  asking "is this server still up?" delivered a console Ctrl+C to that pid's
  group. In CI it interrupted pytest itself mid-run (an async
  `KeyboardInterrupt` that then crashed pytest's own traceback formatter); on a
  tester's machine it is their shell. Every other signal value routes to
  `TerminateProcess`, so no signal is safe as a probe — it now opens a
  read-only process handle. The win32 skip on
  `test_background_server_outlives_the_launcher` is removed with it.
- **A taken port falls back on Windows too.** `http.server` sets
  `allow_reuse_address`, and Windows answers a second bind with `WSAEACCES`,
  not `WSAEADDRINUSE` — so NOOD_0134's fallback never fired there and
  `report serve` dead-ended with the wasted round trip that fix removed.
- **The instruction ledger counts content, not line endings.** git hands
  Windows CRLF, inflating every markdown surface by a byte per line (~180 B on
  a skill card) and failing the ceiling for a reason unrelated to what an agent
  reads.

**Live result** — the postmortem's prompt, unmodified, on the fourth re-run:
one `author --run` call, `ready: true` first attempt, zero standalone probes,
`passed: 1 / failed: 0 / verified: true`, no POM, and the `any_of` compiled to
two literal per-product assertions. Evidence resolved through the
`accessibility` tier on the real product tile (`element_in_view: true`,
`valid: true`) — not the count fallback. The test now proves BOTH requested
products, where the count form proved only that one of them rendered.

### Changed
- **Skill cards publish the goal action vocabulary** (claude 6144 → 7168 B,
  copilot 6304 → 7296 B — accounted in `instruction_budget`). Writing
  `{do: type_partial}` + `{do: pick_suggestion}` where the answer was one
  `suggest` was the session's single most expensive step at 3.67 AIC, and the
  15 real verbs were revealed only by the rejection. The §7 doc-section
  exception applies at its strongest here: the agent had generalised
  reasonably from the card's `{do: search}` example, so there was no round
  trip to intercept — only a wrong answer to pay for.
- Both cards also state that **goal mode probes for you** (a `probe_page`
  before a `goal:` double-bills, and a term one character off returns a
  different, misleading list — that is how a site's genuinely misspelled
  typeahead got "corrected" away), and that **quoted user strings are data**,
  never to be spell-fixed. Full rationale in
  `docs/agent-playbook.md` → The goal object.

## [1.0.0a10] — 2026-07-27

**NOOD_0194** — fix: alpha-readiness pass. Four things an alpha tester would
have hit before we did.

### Fixed
- **`noodle report serve` no longer waits on reverse DNS to bind.**
  `http.server.HTTPServer.server_bind()` calls `socket.getfqdn(host)` purely to
  set `self.server_name`, which nothing in Noodle reads. Where the resolver is
  slow to answer a reverse lookup — corporate VPN, a container with no
  resolver, a self-hosted CI agent — that blocked the socket long enough for
  `_spawn_report_server`'s 30s deadline to fire, so the report-hosting step
  every run is supposed to end with reported "didn't bind within 30s" while
  the server was coming up fine. Measured on one machine, same code, two
  interpreters: **35.1s → 0.1s**.
- **A pass that ran zero steps is now `verified: false`.** `noodle init`'s
  sample ships with its scenario body commented out so a fresh workspace runs
  green — which meant a new tester's first `noodle run` printed a bare ✅
  having launched no browser and asserted nothing. Same silent-green class as
  NOOD_0187's 0-scenarios guard, one level down, and reported through the
  channel that already exists to say "green, but nothing backs it". Not a
  failure: the pre-commented sample is deliberate and a placeholder scenario
  isn't an error.
- **The OpenAPI spec is stable across Python versions.** Python 3.13 strips
  common leading whitespace from docstrings at compile time and 3.11 does not,
  and a tool's docstring *is* its published description — so the same source
  generated two different specs and `docs/openapi.json`'s drift guard could
  only ever be green on one interpreter. Tool descriptions now go through
  `inspect.cleandoc`.
- The drift guard also no longer compares `info.version`: that's the running
  build's version, not the API's shape, and comparing it failed for two
  reasons unrelated to drift — every mandatory version bump reddened CI until
  someone regenerated a file no tool had changed, and running `python -m
  pytest` outside the venv rendered it `unknown (not installed)`.

### Added
- CI now tests the two platforms the project claims and never exercised:
  **Windows** (`windows-latest`, 3.11) and **Python 3.13** (`requires-python =
  ">=3.11"` promised it). Unit suite only — lint, the constraints diff and the
  browser suites are platform-independent and already gated once. Each leg
  varies one thing against the ubuntu-3.11 baseline, so a red leg names its
  own cause.

### Changed
- `CONTRIBUTING.md` said neither `make test` nor `make lint` runs in CI. That
  stopped being true at NOOD_0187; a contributor reading it would skip both
  believing nothing would catch them.
- The UNVERIFIED banner no longer says "leaned on fuzzy healing or lenient
  ambiguity" — it names what the reasons share, now that a zero-step pass is
  one of them.

## [1.0.0a9] — 2026-07-27

**NOOD_0193** — feature: the MCP server's tools are also reachable as plain
HTTP, for callers that can't speak MCP.

### Added
- `noodle-mcp --transport streamable-http` now also serves `GET /api/health`,
  `GET /api/tools` and `POST /api/tools/<name>` alongside `/mcp`, on the same
  port, behind the same `NOODLE_MCP_API_KEY` gate and the same
  `--workspace-root` containment. No new flag, nothing extra to start.
  A Java service, a `curl` step in CI, or a dashboard's `fetch` can call any
  of the 23 tools with one POST — `/mcp` needs an `initialize` handshake, an
  `mcp-session-id` header and SSE parsing, and answers a bare `tools/call`
  with `400 Missing session ID`.
- One dispatcher over the existing tool registry (`noodle/mcp/rest.py`), not a
  hand-written route per tool: a tool added to `server.py` shows up on both
  doorways at once, keeps its payload budget (NOOD_0164) and audit event
  (NOOD_0172), and the surfaces cannot drift apart.

- `GET /api/openapi.json` (OpenAPI 3.1) and `GET /api/docs` (Swagger UI).
  The spec is **generated from the tool registry**, never hand-written, so it
  can't describe an API Noodle doesn't have; a committed copy lives at
  `docs/openapi.json` for generating clients with no server running
  (`python -m noodle.mcp.rest > docs/openapi.json`, and a unit test fails if
  it's stale). Generate a Java/.NET/Node client straight from it.
- `docs/engine-api-guide.md` — setup guide for a developer who doesn't know Noodle:
  install from the clone, start the server, then a worked example per
  operation (author a test, update one, run single or parallel, get the
  reports), plus how to read `failed`/`verified` and the gotchas.
- **README § API mode** — the engine API is now one of the three ways to
  drive Noodle on the front page, next to MCP mode and manual mode, with the
  two commands that stand a server up and call it. Someone who clones this
  repo and asks an agent "how do I use it" now finds the integration path
  without opening the docs table.
- **architecture.md § 2.5, "The engine surfaces"** — a new diagram for what
  *drives* Noodle (CLI · MCP server · engine API · LSP) and how they converge
  on one engine, plus the `Integrate` row in the component map. The doc
  described the inside of a run in eight diagrams and the ways to start one in
  none.

### Fixed
- **Three architecture diagrams didn't render at all.** Mermaid 11 reads a
  bare `@` in an edge label as an edge-ID token, so `-->|@visual|` produced a
  parse-error box on GitHub instead of the resolution-hierarchy and the two
  LLM-layer diagrams. Labels are quoted now; all 15 diagrams across the docs
  were rendered through `mermaid-cli` to confirm, and a unit test fails on the
  next unquoted tag.
- **Docs said "four woks"** — API became its own wok in NOOD_0191, but the
  wok diagram still drew REST inside the web box, and README/woks.md still
  counted four. Corrected, with the "REST steps work in any scenario, `@api`
  only skips the browser" distinction stated where the diagram is.
- Tool calls no longer block the whole server. FastMCP invokes sync tool
  functions inline on the event loop, so a browser run stopped `/api/health`
  from answering for its full duration — a liveness probe would have concluded
  the pod was dead and restarted it mid-run. Tools now run in a worker thread,
  serialized by one lock so behaviour is otherwise unchanged (concurrent runs
  would race on `NOODLE_RUN_ID` and the workspace's single `report/` dir).
  Caught by running a test *through* the API that called back into the same
  server: red before, green after.
- **CI, project-repo pipeline** (`ci/azure/noodle-tests.yml`) — three defects
  found reviewing the two-repo model (engine repo + project repo on one Linux
  agent), which otherwise works: verified end to end by installing the engine
  non-editable from a path and running a workspace at another path — Allure,
  RCA, junit and shard discovery all landed where the publish steps look.
  - `keyVaultUrl` installed green and then died at `before_all` with "the Azure
    SDK is missing": the template took the one secrets knob but never installed
    the `azure` extra behind it. It now folds `[azure]` in itself (merging with
    any existing `extras`), treating an unexpanded `$(VAR)` as "no vault" like
    the engine does.
  - `playwright install --with-deps` apt-gets system libraries and so needs
    root, which a locked-down self-hosted agent doesn't grant — the job died
    before any test ran, contradicting ci-project-repo.md § 7's "no sudo"
    promise. Guarded by a `sudo -n` probe with a browser-only fallback and a
    warning naming the one-time `playwright install-deps` fix.
  - the copy-paste `ref: refs/tags/` examples still pinned `1.0.0a7`, two
    bumps stale — pinning them is the NOOD_0133 failure in a new costume. Now
    current, with a unit test tying them to `pyproject.toml`, and § 3
    documents that the engine repo must actually be **tagged** (an
    unresolvable `ref:` fails at compile time, before there's a log to read).

### Notes
- This is a second doorway, not a second API. When the caller shares the
  machine, `noodle run --json` remains the cheaper path — no server, no key,
  no port. `/api/*` is for remote callers that MCP doesn't reach.
- `verdict.html` is deliberately NOT exposed: it's the engine-wide regression
  benchmark (NOOD_0185, "did this Noodle build regress?"), not a per-run
  report, and stays the `noodle feature-regression` CLI command. A caller's
  per-run verdict is `failed`/`verified` in the run payload.
- **"API" now means two things, so the docs always qualify it**: the **api
  wok** is Noodle testing someone's REST service (`@api`); the **engine API**
  is another system driving Noodle over HTTP. Pinned in
  [glossary.md](docs/glossary.md), cross-referenced from both sides, and
  guarded by a unit test — the guide is named `engine-api-guide.md` rather than
  `api-guide.md` precisely so nobody looking for the wok opens it.
- Docs: [engine-api-guide.md](docs/engine-api-guide.md),
  [mcp-guide.md § 8.1](docs/mcp-guide.md#81-plain-http-for-non-mcp-callers-nood_0193).

## [1.0.0a8] — 2026-07-27

**NOOD_0192** — feature: the api wok authors from a prompt (alone or mixed
with web), Noodle can read the repo it's in, and the install is
shell-agnostic.

Noodle has run REST tests since NOOD_0007 and called API a first-class wok
since NOOD_0191 — but the cheap deterministic authoring path (plain English →
compiled `.feature`) was web-only. So every API test in existence had to be
hand-written Gherkin: never intent-verified, never size-measured, a second
workflow for the same job. That is closed here. Still zero LLM calls on the
authoring path.

- **API verbs in the prompt grammar.** `GET|POST|PUT|PATCH|DELETE <url>`,
  `call the api at <url>`, `go to <url> via rest`, `verify the response
  status is <code>`, `verify the response body contains <text>`. New goal
  action `api` (`method`, `url`, `body`) and check kinds `status` /
  `response_contains`.
- **Pure-API tests need no browser anywhere.** An api-only prompt needs no
  URL step and no `base_url` (it is made of URLs), skips the probe entirely,
  compiles to `@api`, writes no POM, and lands in `noodle_tests/api/<app>/`
  — authoring works on a machine with no Playwright install. `evidence.
  browserless: true` says so, and the planner bills `probes: 0` instead of a
  probe that never ran.
- **Cross-wok in one prompt, in your order.** API calls placed before the
  first web step compile *ahead* of the navigation `Given` — "fetch it, then
  prove the UI shows it" is a sequence, not a set. Mixed scenarios are tagged
  `@web`, never `@api` (which would kill the UI half by starting no browser).
- **feature-regression tc3 is a prompt now**, not `feature_content`. The one
  cross-wok case in a *generation* benchmark generated nothing and printed
  `—` for LINES; it now measures like the other two (10 lines, green,
  verified, PASS). What the swap gave up — `{var:}` response chaining, the
  `feature_content` door — moved to `unit_tests/woks/api/`.
- **`noodle scan` / `scan_repo` / `noodle task "review this repo"`** — what
  is this repo and what can Noodle test in it: stacks, frameworks, how the
  repo says it serves itself, the OpenAPI endpoint list (the API test plan
  the developer already wrote), where tests live, candidate woks — and
  `questions`, the things still missing before a test can be authored.
  Deterministic marker-file scan: no LLM, no code execution, nothing written,
  gitignored dirs skipped. The answer to "review this repo and write tests
  for the new features" is now questions, not a guess.
- **Shell-agnostic install, verified.** `uv tool update-shell` already writes
  bash, zsh, **fish** and PowerShell — there was never a fish-specific
  install path, only no way to tell the shell was missed. `noodle doctor`
  gains `install.login-shell`: it asks `$SHELL` (as a login shell) to find
  `noodle` and warns with that shell's own fix (`fish_add_path (uv tool dir
  --bin)`) when it can't. Advisory when unanswerable (Windows, no `$SHELL`),
  never a fail.
- **Fixes.** `noodle task` intent patterns missed every PLURAL noun
  (`\btest\b` cannot match "tests"), so "generate new API tests" matched
  nothing and fell through to the default. `intent_trace` now reports api
  assertions as `runtime:rest-client` rather than claiming probe evidence.
- No instruction-budget ceiling raised: the api prompt grammar lands in
  `docs/woks.md` and the `noodle task --contract` payload, per the ledger's
  own "surfaces route, docs carry" rule.

## [1.0.0a7] — 2026-07-27

**NOOD_0191** — feature: AI-SDLC ready without MCP — `noodle task`, a
consumable project-repo pipeline template, and public-safety redaction.

Noodle could already do every job an AI SDLC needs; it had two missing
doorways. An agent that has never read the docs had no entry point that
tolerated its phrasing, and a project repo wanting to run its own tests had to
copy 400 lines of hard-won Allure wiring. Both are closed here. No LLM call is
added anywhere — the execution path makes zero by construction, and the
authoring path stays deterministic.

- **`noodle task "<any text>"`** — one door, five intents (generate / update /
  run / report / verdict) chosen by a deterministic keyword scan and
  dispatched to commands that already exist. No recognized verb defaults to
  generate. Adds no authoring logic.
- **The refusal is the feature.** Text that can't be compiled comes back with
  `need`, `next` and the grammar instead of a guess, so the caller — already
  an LLM — fills the gap and re-sends in one round trip. An "already exists"
  refusal says `need: ["overwrite"]`, not "rewrite your prompt". A routing
  sentence in front of a step list (`create a test: 1. Go to …`) is stripped
  before compiling; it reached the grammar as step 1 and was refused.
- **`noodle task --contract`** — intents, prompt grammar, goal vocabulary and
  the envelope schema in one bounded payload, so an agent that isn't Claude
  Code or Copilot (and so can't read the skill card) fetches the contract once
  instead of learning it through rejections.
- **`ci/azure/noodle-tests.yml`** — a jobs template a project repo consumes in
  ~12 lines: it clones the engine pinned by tag, installs it with the engine's
  own `constraints.txt` (base deps, not `.[all,llm]`), shards by feature file,
  runs headless, and publishes the Allure tab, Tests tab, RCA and artifacts.
  Secrets are one knob — `keyVaultUrl` — with `secretEnv` as the escape hatch,
  replacing the per-key list that made the old pipeline unreusable.
- **Self-hosted agent hardening**, shared by the template and this repo's own
  pipeline (`ci/azure/steps-allure-cli.yml`): no `sudo npm -g`, the Allure CLI
  installed by exact version into a workspace dir with its *resolved* binary
  version-verified before it reaches PATH, and a portable Node provisioned
  when the agent's is older than 20. Shipping advice we didn't follow was how
  these kept being rediscovered downstream.
- **CI is headless only.** Nobody can watch a browser in a build agent, so the
  template has no `--headed` knob and a test keeps one from growing back.
  Headed stays the local demo path.
- **Stops the framework denying capabilities it has.** A developer added the
  skill to their work repo, asked for API tests, and the agent refused —
  *"Noodle is a web UI testing framework… use pytest, RestAssured or
  Postman"*. Flatly wrong: Noodle is a universal BDD framework and has
  shipped browserless REST testing since NOOD_0007 (GET/POST/PUT/PATCH/DELETE, bearer/basic/apikey/oauth2,
  status+body+header asserts, JSON extraction and variable chaining, payload
  files, five worked sample features). The agent was reasoning correctly from
  what it had been shown: every always-on surface opened "Playwright+behave
  BDD", and `@api` appeared once inside a ten-tag list. All of them now name
  the wok and say **never refuse a non-web ask** — skill cards (both hosts),
  `AGENTS.md`, and `noodle task`'s contract.
- **API is now its own wok — Noodle is universal, not web-with-extras.** It
  had been filed under the web wok on the grounds that REST "shares the web
  session lifecycle". It never did: `hooks.before_scenario` nulls the page and
  returns before any browser launch for `@api`, so an API suite runs on a CI
  image with no Playwright installed. Five woks now — **web · api · mobile ·
  desktop · performance** — each standing alone, none a sub-mode of another
  (`noodle/wok.py`, `docs/woks.md`, `noodle wok`). `@api` routes to `api` and
  beats `@web` when both are present, matching what the runtime already did.
- **Verified: API and UI steps mix freely in one scenario** — "seed data over
  REST, then assert it rendered" is the most common real pattern, and it works
  today. Proven live: a `@web` scenario doing GET → extract `{var:}` →
  navigate → assert went green. The trap is the tag, now documented in
  `docs/woks.md § Cross-wok composition`: **`@api` does not mean "contains API
  steps", it means "no browser at all"** — tag a mixed scenario `@web`. REST
  steps are browserless by action-type prefix, so they already run in any
  scenario; mistagging removes the browser and the first web step fails with a
  clear message (also verified). `unit_tests/woks/api/` guards the prefix rule
  and the naming convention it depends on.
- **`noodle feature-regression` gains tc3 — the cross-wok case.** The
  benchmark had two prompt cases and nothing covering the shape a real suite
  uses most: fetch over REST, chain `{var:}` into a web step, assert it
  rendered. `tc3_api_seeds_ui_verifies` covers the REST client, `{var:}`
  chaining across step families, `{env:}` in both, and the `feature_content`
  authoring door nothing else here touched. Verified by mutation: breaking
  `rest_extract_json` turns tc3 red and leaves tc1/tc2 green.
  `_case()` now dispatches on `mode` (it declared the key but never read it),
  and two measurements are mode-aware rather than wrong: `intent_verified` is
  `goal is not None and not blocking`, so demanding it for hand-authored
  Gherkin would score tc3 red forever — there `run.verified` is the whole bar;
  and `lines` reports `—` instead of `0`, because nothing was *generated* to
  count and a zero would drag the average with a number never measured.
- **The API wok is a lifecycle, not a gate** — documented explicitly, because
  promoting it to a wok risks re-implying the silo just removed. REST steps
  are plain I/O available in *every* scenario with no tag at all, the same
  class of thing as `run_command` or a JDBC call to seed a fixture: the runner
  refuses a step only when `page is None`, and `rest_*` is exempt there too,
  so REST is never gated anywhere. `@api` is purely an opt-out from starting a
  browser — a CI-image-size and startup-cost decision. Pinned by a test, so a
  future change can't quietly make the tag a precondition. Honest gap recorded
  alongside it: there is no native SQL/DB step family yet — database setup
  goes through `run_command`/`run_script` today.
- `noodle task` routes a non-web ask to its wok instead of refusing it with
  web grammar: an API request returns `wok: "api"` with copy-ready REST step
  syntax and `next: "supported — this is the api wok"`. Same for
  mobile/desktop. Web prompts are unaffected.
- Instruction-budget ledger: claude skill card 5888 → 6144, copilot 6144 →
  6304. The §7 exception applies more strongly here than anywhere it has
  before — an agent must know a capability exists before it will spend a call
  looking it up, and this one never made a second call. A first attempt paid
  for the bytes by deleting the probe narrow-exemption sentence;
  `test_nood_0101`/`test_nood_0136` refused it, correctly — that is the
  131-AIC session's lesson, not spare bytes. `AGENTS.md` absorbed its scope
  line inside its cap, and the NOOD_0160 headroom floor forced three genuine
  content moves rather than a spend (`after:` anchoring and `inspect_locator`
  to the playbook, the `--spec -` heredoc tip added to playbook §2).
- Redacts employer-specific references from the public repo: the live-drill
  prompts in `docs/feature-regression.md` become site-neutral templates, and
  the matching comments in `noodle/regression.py` / this changelog say
  "retail-store pair". No corp agent pools, hostnames or org names remain.
- Release tags are now `1.0.0aN` → `1.0.0bN` → `1.0.0`, no `v` prefix
  (CONTRIBUTING.md); the obsolete `v`-prefixed tag is gone. A consuming
  project pins `ref: refs/tags/<version>`.
- Docs: `docs/ci-project-repo.md` (the walkthrough),
  `ci/azure/example-project-pipeline.yml` (copy-paste consumer),
  `docs/cli-reference.md § noodle task`, and `docs/ai-sdlc-integration.md`
  rewritten around project-repo-owns-its-tests with a CLI-door section for
  networks where MCP isn't allowed. Background and the deferred items:
  `docs/todo/nood-0191-ai-sdlc-cli-plan.md`.
- `unit_tests/test_nood_0191.py` — a routing table of real off-template
  prompts, plus drift guards over the pipelines: no resurrected `sudo npm`, no
  bare `allure` invocation, and the CLI pin identical across all five files
  that name it (both the literal `allure@X.Y.Z` form and the template
  parameter default — reading only the first let the templates drift).

## [1.0.0a6] — 2026-07-27

**NOOD_0190** — feature: `noodle feature-regression` runs the benchmark in one
command.

The command printed a 10-step manual protocol and exited 2. There was no
execute mode, so every driving agent improvised the protocol — read docs,
guessed flags, hand-wrote `results.json`, and on a host with no billing API
went digging through session telemetry to invent a cost number. That
improvisation was the entire cost: ~28 AIC to produce a REGRESSED verdict
whose deciding number was a guessed timestamp, against an engine that made
zero LLM calls and ran both tests in 7s and 12s.

- **The bare command runs it:** fresh workspace → both canonical prompts
  authored + run → one combined Allure + RCA → scored → served → benchmark
  table. Exit codes collapse to **0 = PASS, 1 = REGRESSED**. `--json` for
  machines, `--init` to scaffold only, `--score` to re-score an existing run.
- **No new measurements were needed** — `regression.execute()` writes
  `results.json` from the payload `author_test(run_after_author=True)` already
  hands back (`elapsed − run.seconds`, `run.failed`, `run.verified`,
  `author.intent_verified`).
- **Corrections are measured, not self-reported:** `run.healing_events` +
  `run.flaky` + re-author passes. `execute()` re-authors once on
  `ready: false` and counts it; still not ready → red. The old integer was
  an agent's memory — last session tc1 reported `corrections: 0` beside a run
  log recording a real heal (`locator 'search', strategy visible-filter`).
- **Generated size:** `.feature` + POM line count from `author.compiled`,
  stdlib `splitlines()`. Reported, not gated — the signal for "are we still
  generating simple tests". (Not an estimated token count: that needs
  `litellm`, an optional extra this repo doesn't install, so the column would
  read `null` in practice.)
- **Removed the host-cost machinery and the engine LLM ledger** — `aic`,
  `tokens`, `max_aic`/`max_avg_aic`/`max_tokens`/`max_avg_tokens`, `host`,
  `host_unit()`, `cost_basis`, `engine_cost`, and the NOOD_0188/0189
  unit-selection and zero-guard logic. The host figure measured how lost the
  driving agent got, not whether the engine regressed, and could only be
  guessed on a host with no billing API; the engine figure read `none` every
  run (deterministic fast path, zero model calls). **The benchmark measures
  the engine.**
- **Deleted `runbook()`** — it existed only to tell an agent how to do this by
  hand. Prose for humans stays at `noodle docs feature-regression`.
- `verdict.html` leads the served URLs, beside Allure and RCA.
- `regression_runs/` is anchored to `install_check.clone_root()` instead of
  cwd — invoking from a subdirectory used to drop an un-gitignored folder
  wherever you stood.
- **Unrelated CI fix, found by this branch's e2e run:** the `Token-guarded
  api_call` sample scenario POSTed movie 1 to `/api/cart`, the same movie
  `run_custom_script.feature`'s `seed_out_of_stock.py` zeroes. Carts are
  per-user and parallel-safe; movie **stock is global**, so under `--parallel`
  that seed lands in another worker mid-scenario and the cart answers
  `400 Out of stock`. Now uses movie 2, which nothing seeds.
- Both skill cards + `.github/copilot-instructions.md` learn the one-command
  path, so Copilot CLI, Claude Code and a bare terminal behave identically.
  Instruction-budget ceilings: copilot skill card 5888 → 6144, copilot digest
  7168 → 7424 (+256 each, 189 B used; the Claude card absorbed it inside its
  cap).

## [1.0.0a5] — 2026-07-27

**NOOD_0189** — fix: the regression benchmark's cost AC is now falsifiable.

A 2026-07-27 benchmark session produced a PASS that hid two failures: the
bare `noodle feature-regression` (exit 0) was reported as a completed run,
and a placeholder `aic: 0` satisfied the `> cap` cost check while `null`
would have failed loudly.

- `score()` rejects a cost of 0 as "unmeasured cost" unless `cost_basis`
  starts with `host-reported` — a driving agent always bills something
- bare `noodle feature-regression` exits 2 (1 already means REGRESSED /
  stale install) and the runbook's last line states it is NOT a run
- three pinning tests in `unit_tests/test_nood_0185.py`

## [1.0.0a4] — 2026-07-27

**NOOD_0188** — feature: the NOOD_0187 deferred batch — goal-grammar growth,
probe reuse, richer mocking/HAR, driven websockets, binary downloads, pixel
masks, benchmark self-report audit, Node 24 CI.

The NOOD_0187 audit deferred seven items as "features, not lies". This is
them.

- **Goal grammar doubles: 7 → 14 actions, 5 → 7 check kinds.** The cheap
  deterministic authoring path reached 7 verbs while the runtime had 169
  action types, so any test with a checkbox, an upload, a hover menu, a date
  picker, an Enter press or a back-navigation dropped to hand-written Gherkin
  — which is never intent-verified. Added `check`, `uncheck`, `hover`,
  `upload`, `press_key`, `pick_date`, `go_back` plus the `not_see` (absence —
  the empty-state/removal half of a journey) and `url_contains` (the flow
  landed where it should) checks. The runtime steps already existed since
  NOOD_0186; this is the wiring, reachable from plain English too
  (`Check the "Terms" checkbox`, `Verify "Error" is not visible`).
  Two silent-fallthrough traps are closed with it: `_action_step` used to end
  in an unguarded `select` and `_check_step` in an unguarded `any_of`, so a
  table-only addition compiled a *plausible but wrong* step that still matched
  the pattern table — both now raise. The check-kind tuple, hand-repeated in
  three places, is now one constant.
- **Probe reuse between repair laps** (`NOODLE_PROBE_CACHE_TTL`, **default
  off**). Keyed on the feature + URLs + every probe argument, so only an
  identical question about the same feature is ever reused. Deliberately
  opt-in: the commonest reason to re-author is that someone just CHANGED the
  app, and answering from cache would describe a page the engine hasn't
  looked at. On, it saves a browser launch + page load + scan per lap while
  iterating on a goal against a static site; reuse is always disclosed (log
  line + `probe_reused` in the payload).
- **Richer network mocking**: response headers (via a step table), fixture-file
  bodies (`with the fixture 'orders.json'`, resolved from the app's
  `resources/`), method scoping (`mocks POST '…'` — other verbs fall through),
  injected latency (`after 3 seconds`), and a guessed content type instead of
  a hardcoded `application/json` that served mocked HTML/CSV under a lying
  MIME. Plus **HAR replay/record** (`replays 'checkout.har'` /
  `records 'checkout.har' for '**/api/**'`) — NOOD_0183 declined it on the
  grounds mock_route covers it; it doesn't, because mock_route fulfils one
  glob per step and a HAR pins a whole third-party session in one line.
- **Websockets can be driven, not just watched**: `mocks the websocket` +
  `the server sends '…'` + `the page should have sent '…'`. Capture alone
  meant a live-update UI (ticker, chat, order status) could never be tested,
  because nothing could make the server say anything. Arm-first (routing
  attaches to a socket opened after the mock), same contract as the JS dialog
  steps.
- **Binary downloads are assertable**: xlsx/docx/pptx/pdf are read by the new
  `noodle.docparse` (stdlib `zipfile`/`zlib` — Office files are zipped XML, a
  generated PDF's text is in its content streams). The enterprise export case
  could previously assert only the filename; a refusal message told you so.
  No new dependency; `.xlsx` row counts use real `<row>` elements.
- **Pixel baselines take masks**: `ignoring the clock and the avatar` paints
  those boxes flat black on both sides before diffing. Any moving pixel used
  to make a pixel baseline permanently red, which is how visual tests get
  switched off. Masks are a tolerance, never an assertion — an unresolvable
  one is skipped.
- **The regression benchmark now audits its own subject.** `green`/`verified`
  were typed into `results.json` by the driving agent and nothing read the
  run's `last_run.json`, making the headline verdict of the "is the product
  still good" benchmark unfalsifiable self-report. `score()` stays pure by
  default; given a workspace it cross-checks, and a claim contradicting the
  artifacts is a REGRESSION. Missing artifacts stay advisory (a fresh
  workspace isn't a regression).
- **CI is off Node 20**: checkout v7, setup-python v6, setup-node v6,
  upload-artifact v6, cache v5, github-script v8 — each verified as
  `using: node24` at the pinned major tag rather than assumed.

Three bugs the live run of the new steps caught, fixed here:

- **A local dev server couldn't be authored against** (pre-existing, since the
  prompt path shipped): `_URLISH` required a dot and rejected a port, so every
  `http://localhost:3333` / `http://127.0.0.1:8080` prompt failed with "no URL
  in the prompt" — the cheap authoring path could not target the app you are
  actually developing. An explicit scheme now accepts any host and a port; a
  dotless bare word without a scheme still routes to a CLICK (`go to the
  cart`), which is what that rule was protecting.
- **Control-free actions crashed authoring**: `press_key`/`go_back` name no
  control, but `evidence()`'s generic path keys on `a["target"]` — `KeyError`
  mid-author. They are skipped there now.
- **…and reported themselves unproven**: `intent_trace` keyed them on the same
  missing target, so any goal using one came back `intent_verified: false` — a
  false negative on the signal agents are told to trust. They now trace as
  `deterministic:no-control`, which is what they are.
- `mocks GET '…' with the fixture '…'` (method + fixture together) didn't
  parse, though it's the natural phrasing.

**Benchmark cost is measured in the unit the host actually bills.** AIC was
the only cost metric, so a Claude-driven run was scored in premium requests
it never spends — the cost half of the verdict was meaningless on whichever
host you weren't using. `host` now selects it: `claude…` is scored on
`tokens` (`NOODLE_REG_MAX_TOKENS`, default 120 000), `copilot…` on `aic`
(`NOODLE_REG_MAX_AIC`, unchanged); only that host's ceiling is enforced, and
the host's own unit is a required measurement. An optional `cost_basis`
string rides through to `verdict.html` so a measured FLOOR can never be
misread as full billed spend.

## [1.0.0a3] — 2026-07-27

**NOOD_0187** — fix: the framework never lies — report integrity, parallel
scale, CI/cloud readiness.

A five-lens audit (report trust, parallel scale, pipeline readiness,
generation cost, web coverage) found concrete ways a green could lie and
hard ceilings on running 1000s of tests. This closes them.

**Report integrity — every false-green path found is closed:**

- `ScenarioResult.finish()` now reads behave's own verdict, not just recorded
  steps: a `@soft`-assert-only failure, a setup-hook/precondition error and an
  undefined step all wrote `"passed"` to Allure/junit/`last_run.json` (the
  soft case even exited 0). Soft failures also land as a synthetic failed
  step listing every collected assertion.
- **Exit codes derive from the written results in both modes**: behave's 0 on
  a soft-failed run and behavex's observed lying 0 both red the build now.
- **Zero scenarios ran → exit 3**, not silent green: a typo'd `--tag` used to
  produce a green pipeline that tested nothing while `report serve` hosted
  the PREVIOUS run's report. The RCA renders "0 scenarios ran — this report
  proves nothing" instead of the green tick, junit.xml is always written
  (even empty), and an empty run builds an honest empty report. `--shard`
  legs and `NOODLE_ALLOW_EMPTY=1` downgrade to a warning (a slice can
  legitimately filter to zero; both ADO pipelines set it per shard).
- **Skipped scenarios exist now**: gated skips (@live off, missing
  OCR/Appium/NOODLE_MODEL) write a skipped result — they were invisible in
  every report, so a fully gated-off suite read as a clean run.
- **Retries are honest**: retried-then-green scenarios are stamped
  `flaky` in Allure, counted in `last_run.json` (`flaky: [...]`), and the RCA
  gets a "Passed after retry" section. The quarantine scan and `--failed`
  also dedupe to last-attempt (a retried-green no longer re-runs or blocks
  the all-quarantined override).
- `@quarantine`'s exit-0 rewrite is recorded (`quarantine_overrode_exit`) and
  `NOODLE_STRICT_QUARANTINE=1` disables it. `NOODLE_STRICT_HOOKS=1` makes a
  crashing custom hook fail the run instead of logging.
- **Secrets can no longer become pixels**: the failure/evidence label burned
  into screenshots by annotate.py is value-scrubbed first (NOOD_0177 covered
  the filename, not the drawn caption).
- Provenance everywhere: environment.properties gains noodle version, engine
  git SHA, Playwright version, run id + timestamp; rca.md/rca.html open with
  run id, generated-at and true counts; `--json` returns `null` for report
  paths that don't exist; step durations are real (start = stop − behave's
  measured duration, no more 0 ms timeline); `@record_video`'s .webm is
  attached to the Allure result (it was recorded and never referenced);
  a core-only install warns loudly that no results will be written.

**Parallel scale (1000s of tests, several teams):**

- `noodle run --shard i/N` — deterministic feature-file slices for splitting
  one suite across machines; `--timeout N` kills the wedged run (process
  group) at 124 and still reports the partial results; `--fail-fast` stops at
  first red. `--parallel` now goes through the same reporting tail as
  sequential: `--json`/`--quiet`/`--serve` all work (parallel `--json`
  printed nothing).
- behavex runs `after_all` once per FEATURE, not per worker — that overwrote
  each worker's junit/healing/cost ledger with its last feature (the
  published junit held one testsuite per WORKER) and closed the reused
  browser between files. Parallel outputs now carry unique slice ids and
  merge by glob; browsers/lanes close at process exit (atexit) so the
  NOOD_0183 reuse win survives multi-feature workers.
- The merge is Windows-safe (`shutil.move`, fixed-name metadata skips —
  `Path.rename` onto an existing dst crashes there) and flattens the
  per-worker traces/network leaves, so the RCA's trace section and the
  NOOD_0156 mutation classifier work under `--parallel` (they silently saw
  empty dirs).
- Lock/lane hardening: lane mtime heartbeats every scenario (a feature
  running past NOODLE_LOCK_TTL had its lane stolen — two workers, same
  credentials); stale-lock breaking is rename-atomic (the rmdir+mkdir pair
  could double-grant `@serial`); `reset_control_dir(workspace)` takes the
  root instead of chdir; `--failed` names are `re.escape`d (behave --name is
  a regex); workers clamp to 60 on Windows; stale per-pid artifact leaves and
  cost ledgers are cleaned at run start (the cost rglob summed dead runs).

**Pipeline / cloud / ADO:**

- `constraints.txt` (uv.lock export, `make constraints`) pins every
  transitive dep in all three CI surfaces — an unpinned morning resolve
  could break pipelines nobody touched. The GitHub gate diffs it against the
  lock.
- GitHub Actions grows an **e2e job** (BusterBlock + headless chromium +
  `--parallel 2 --timeout`, junit/Allure/RCA uploaded) and a **docker build
  job** — it was lint+unit only, no `.feature` ever ran on GitHub.
- The Windows ADO pipeline is re-synced with Linux: ruff in the gate,
  tesseract, chromium in discover, the Allure CLI (reports silently never
  built on Windows shards), per-shard trend-history cache,
  generate-if-missing. Both pipelines: Playwright browser cache keyed on
  constraints.txt, quoted `$(tagFlag)` (tags with spaces), and cmd comments
  fixed to `rem`.
- `scripts/list_features.py` exits 1 on zero discovered features (an empty
  matrix made the tests job a green no-op); the non-editable-install warning
  is suppressed off-TTY (wheel installs in CI are deliberate, `noodle
  update` advice was wrong there).

**Authoring accuracy:**

- The intent-contract lock refuses only **blocked** contracts: a cleanly
  goal-authored feature used to freeze its entire app package against any
  manual edit or auto-run — the main autonomous-agent dead end.
- Named browser contexts ('buyer'/'seller') inherit the scenario's context
  options and get the same console/network/websocket/download capture as the
  primary page — the second user debugged blind with half the assertion
  vocabulary silently seeing nothing.
- New step: `no server errors should occur` (`no HTTP errors should have
  occurred`) — fails on any 4xx/5xx in the page's own traffic. The data was
  already captured for RCA; `no network requests should fail` only sees
  transport aborts, so a page full of 500s passed it.

Instruction budget: `noodle run --help` 5120 → 5504 B (three new flags; a
flag's existence is routing — NOOD_0179 rule; rationale lives in
docs/cli-reference.md).

## [1.0.0a2] — 2026-07-26

**NOOD_0186** — feature: the five audited web-coverage gaps closed — HTML5
media, multi-file upload, calendar pickers, hover menus, native-dialog
honesty.

A full audit of the web wok's step patterns against "what can a real website
throw at a tester" found five remaining holes; this closes all of them:

- **HTML5 media** (the one visible gap): `plays/pauses/mutes/unmutes the
  video`, `seeks the video to 1:30`, `sets the volume to 50%`, plus polled
  asserts — `the video should be playing / paused / muted / ended`,
  `should have played at least 5 seconds`, `should be 30 seconds long`
  (±1s). Named form (`the 'promo' video`) resolves through the locator
  engine and walks into the media tag inside player wrappers; the bare form
  takes the page's only/first visible `<video>`/`<audio>`. An
  autoplay-policy block surfaces the browser's own error plus the fix.
- **Multi-file upload**: `uploads 'a.png' and 'b.png' to the 'Photos'
  input` — one `set_input_files` call with the whole list, because repeated
  single uploads REPLACE each other on an `<input multiple>`.
- **Calendar pickers**: `picks 'March 15, 2026' from the 'Departure'
  calendar` / `selects tomorrow from the 'Check-in' calendar` — drives
  click-driven popup calendars (react-datepicker, flatpickr, MUI, jQuery
  UI, ARIA grids): open, navigate months (bounded, honest about min/max
  clamps), click the day cell (skipping disabled/adjacent-month fillers).
  Native `<input type=date>` short-circuits to an ISO fill.
- **Hover menus**: `opens the 'Products' menu and clicks 'Shoes'` — hover
  the trigger, wait for the revealed item, click it via the normal engine
  (POM + self-heal apply); falls back to clicking the trigger for
  click-to-open menus.
- **Native OS dialogs refuse honestly** (same ethos as the email/SMS
  refusals): the print dialog and the OS file picker render outside the
  page, so steps that open/assert/click them fail at resolution naming the
  covering step. `prints the page [as '…']` maps to the intent automation
  CAN cover — `page.pdf` export.

New action types are mirrored in the LLM-fallback `VALID_TYPES`; the steps
dictionary documents every new phrasing (and the corpus test pins them).

Two fixes from running the NOOD_0185 feature-regression benchmark on this
build (TC1 cost 3 corrections; both were pre-existing, this closes them):

- **Typeahead capture debounce race**: per-keystroke suggestion XHRs land
  out of order, so the probe could capture a stale prefix's list (typing
  "Vacuum cleane" captured the suggestions for "Va") and goal authoring
  blocked on evidence that was never really absent. The probe now polls
  (bounded) until a captured row shares a content word with the full term.
- **`noodle author --overwrite`**: the engine-side `overwrite` field was
  reachable only from a spec file or MCP — prompt mode had no way past
  "exists — pass overwrite=true", though blocked authoring deliberately
  leaves its files behind (fix-in-place). The flag now exists on the CLI
  and ORs with the spec key.

## [1.0.0a1] — 2026-07-26

**First 1.0 alpha — the web wok is the released capability.**

Version numbering moves from the pre-1.0 `0.2.0aN` line to the 1.0.0 alpha
series. What earns it is the web wok (`@web`, `@api`, `@terminal`,
`@mobile`-as-device-emulation): accessibility-first locators with POM fallback
and self-healing, goal/prompt authoring, Allure + RCA on every run, tracing,
network capture, parallel runs, MFA, REST budgets, touch gestures — a suite
with zero skips, 2677 unit tests green, and a live end-to-end benchmark
(`noodle feature-regression`, below) that measures the whole product per
release rather than per function.

Alpha, not GA, and deliberately: the other three woks are less proven —
mobile (Appium) and desktop (visual agent) need real devices and hosts to
exercise, performance is young. Nothing about their behaviour changes here.
Pin `1.0.0a*` expecting the web wok to hold and the rest to move.

**NOOD_0185** — feature: `noodle feature-regression` — core-product regression benchmark.

Unit tests prove functions; nothing proved the product. New on-demand
benchmark: two fixed "super easy" test cases against Wikipedia (live but
automation-friendly — typeahead suggest via goal spec, click-nav +
plain-text verifies via numbered prompt; the original retail-store pair
stays in the doc as a live drill, its search API bot-gates automated
browsers) → one `noodle author … --run` each → both on ONE served
Allure + RCA report, measured per test case on time, host AIC, corrections,
and engine-side LLM cost — never combined, plus the cross-case average.

- `noodle feature-regression` prints the whole runbook (setup, prompts,
  measurement protocol, results schema) — self-sufficient on any OS/host
  (Claude, Copilot, plain terminal), no MCP or skill file needed; doubles
  as a no-new-capability demo.
- `noodle feature-regression --score results.json` → per-TC breakdown +
  averages + `PASS`/`REGRESSED` verdict (exit 1), one reason line per
  breach. Budget: ≤600 s, ≤15 AIC, ≤2 corrections per TC; ≤10 AIC average;
  runs must be green AND verified. Each ceiling env-overridable
  (`NOODLE_REG_MAX_*`).
- Prompts + budget + pure `score()` live in `noodle/regression.py`;
  prose (measurement semantics, HIL review, bisecting a regression to a
  commit) in docs/feature-regression.md, reachable via
  `noodle docs feature-regression`.
- `--score` also renders the scorecard as `verdict.html` — next to
  results.json and into the run's served reports dir, so the three ACs
  (time, cost, accuracy per test case + averages) are reviewable at
  `/verdict.html` beside the Allure and RCA reports.
- **Prompt grammar: typeahead suggestion click** — `click the suggestion
  <option>` after a `search for <term>` step now expands to the engine's
  suggest action (type, pick from the dropdown, never submit). The
  benchmark's AC — generate from prompts the way real users ask — exposed
  that the marquee typeahead flow was unreachable by prompt.
- Budget tightened to the stated acceptance numbers: ≤120 s and ≤10 AIC per
  test case (was 600 s / 15).
- The scorecard now calculates **test development time** — `elapsed_s −
  run_s` (the run's own `seconds` comes back in the author call's JSON) —
  and the time budget applies to development, not to the generated test's
  execution: a slow site cannot fail a fast authoring. verdict.html shows
  development and run side by side, per TC and as averages.
- **Engine fix the benchmark caught on its first run:** prompt-mode
  `verify <text>` emitted an `any_of` check, which compiles to a
  link/title/alt-scoped "result titles" count locator — structurally unable
  to match plain page text (a `<label>`, a tagline), and quoted prompt text
  leaked its quotes into the selector regex. Plain verifies now emit `see`
  (compiled to `the user sees "<text>"`, same as goal mode), wrapping quotes
  stripped (noodle/repl/prompt_expander.py).
- `--init` refuses to scaffold when the install lags the checkout (the
  `noodle --version` mismatch): the folder name carries the checkout's version
  and sha, so a stale install would file its results under code that never ran.
  Runbook step 1 (`noodle update`) is named in the error.
- **`noodle report stop` no longer needs `-w`** — the benchmark serves reports
  from a per-build workspace, and a plain `noodle report stop` elsewhere said
  "nothing to stop" while the server stayed up. The lsof fallback only
  recognized `python -m http.server --directory <dir>`; it now treats any
  directory on a listening process's command line as a candidate — including
  the engine's own detached `noodle report serve <dir>`, whose cwd is the
  launching dir, not the report tree. The `-w` registry path is unchanged.

## [0.2.0a37] — 2026-07-26

**NOOD_0184** — feature: MFA one-time codes — TOTP + email inbox.

MFA'd test environments previously had one answer: session reuse
(`NOODLE_STORAGE_STATE`). Now the challenge itself can be solved, stdlib-only
(no new dependencies):

- **TOTP (RFC 6238)** — `enters the one-time code for {env:MFA_SECRET} in
  'verification code'` computes the authenticator-app code from the enrolled
  base32 secret (accepted exactly as apps display it: spaces/dashes/case/
  padding). `stores the one-time code … in {var:OTP}` composes with any wok.
  Secret falls back to `NOODLE_TOTP_SECRET`; an unresolved `{env:X}` ref
  fails naming the missing variable, not with a base32 stack trace. Verified
  against the RFC 6238 Appendix B test vectors.
- **Emailed codes (IMAP)** — `waits for an email code [containing 'Verify']
  and stores it in {var:OTP} [within N seconds]` polls the
  `NOODLE_IMAP_HOST/USER/PASSWORD` inbox (SSL, port 993) for unseen mail,
  newest first, HTML bodies included; prefers a 6-digit code over date-like
  numbers; marks only the consumed message read so a code is never handed
  out twice. Budget: `NOODLE_MAIL_TIMEOUT` (90s) or the step's `within`.
- The NOOD_0152 email refusal now points at the real workaround (the IMAP
  code step) instead of a hand-rolled `call_function` mailbox.
- Still refused, honestly: SMS, Entrust push approvals, hardware tokens —
  un-automatable by design; enroll a TOTP soft token or use session reuse.

## [0.2.0a36] — 2026-07-26

**NOOD_0183** — feature: parallel runs that don't collide, plus four web-wok gaps.

### Parallel

Audited `--parallel` against the "hundreds to thousands of scenarios" case.
The scheme was already right — sharding is per **feature file**, so scenarios
inside one file always run sequentially in one process and a file whose
scenarios share a login is safe by construction. Four things around it were
not.

| Weakness | Fix |
|---|---|
| Evidence files collided **by name** across workers — `FAILED_<step text>.png`, `EVIDENCE_<step text>.jpg`, `<scenario>.zip`, `<scenario>.json` all shared one dir, so two features sharing a step sentence wrote the same file from two processes, and Allure attached whichever write won | each worker gets a `p<pid>/` leaf under `screenshots/`, `traces/`, `videos/`, `network/` |
| Every scenario relaunched the Playwright driver *and* a browser — measured **0.672s** of pure setup per scenario (driver 0.28s, launch 0.23s, first context 0.16s) | one browser per worker, a fresh **context** per scenario — the same isolation (cookies/storage/cache/permissions are context-scoped) for **0.034s**. `NOODLE_REUSE_BROWSER=0` restores the old behaviour |
| No way to say "these two feature files share one account" | `@serial` / `@lock:<name>` — a cross-process mutex (atomic `mkdir`, no dependency), held per scenario, released on pass *or* fail, stale locks broken after `NOODLE_LOCK_TTL` |
| N workers logging in as the *same* user | `NOODLE_WORKER_INDEX` (lane 1…N) + numbered keys: define `SHOP_USER_1..4` and `{env:SHOP_USER}` resolves to this lane's account with no feature-file change. Sequential runs are lane 1, so a lane-aware suite behaves identically either way |

Measured on the 106-scenario BusterBlock sample: 175s → **142s** sequential
(browser reuse alone), 106 passed / 0 failed both ways. Lock verified with
three feature files across three worker processes — critical sections strictly
non-overlapping (4.3s), and overlapping when the tag is removed (1.5s).

New flags: `--sequential` (beats `$NOODLE_PARALLEL_PROCESSES`), `--parallel -1`
(one worker per core), `--failed` (re-run only last run's failures), `--name`
(filter by scenario name). `--parallel-scheme scenario` now warns that it
splits a single feature file across workers, and a headed parallel run warns
about the window pile-up.

### Web wok

Audited the ~200-action vocabulary against how Playwright and Selenide are
actually used. Four real gaps (relative locators, `nth()`, and HAR replay were
checked and deliberately **not** added — `in row`/`in section` scoping,
ambiguity-as-an-error, and `mock_route` already cover them):

- **Traces were written and never mentioned again.** Every failed web scenario
  saves a full Playwright trace, but the only pointer to it was one console
  line that `--quiet` — the default for agents and CI — discards. `rca.md` now
  lists each failure's trace with a copy-pasteable `playwright show-trace`.
- **Clock control** — `sets the clock to '2026-01-01'`, `freezes the clock`,
  `advances the clock by 30 days`. Anything rendered from today's date
  (countdowns, "expires in 3 days", session timeouts, month-end totals) was
  untestable without waiting for real time.
- **Pre-boot script injection** — `runs the script '…' before every page load`.
  `runs the script` fires after load, so it could not stub what an app reads at
  boot: a feature flag, an analytics SDK, a seeded `localStorage` token.

### Also

- `unit_tests/test_nood_0181.py::test_narrow_viewport_warns_that_it_is_not_mobile`
  was red on `main` — it asserted on `caplog`, but Noodle's logger does not
  propagate to root, so it never saw the warning it checks. Switched to
  `capsys`; the engine behaviour was correct all along.

## [0.2.0a35] — 2026-07-25

**NOOD_0182** — fix: REST waits had no budget an author could set.

Reported as *"a GET that takes 20 seconds fails"*. Verified — partly true, and
worse than reported in one place:

| Path | Before | Now |
|------|--------|-----|
| `waits for the response from '/api/x'` | **10s** (`NOODLE_TIMEOUT`, the *element* budget) → a 20s API answer failed | REST budget, 30s default |
| `performs a GET call at '/x'` (all woks) | 30s hardcoded, unreachable; blew up as a raw `URLError(timed out)` | env + per-step, named failure |
| `calls GET '...'` (Playwright request) | Playwright's invisible 30s | same budget |
| `@perf` load generator | 30s hardcoded | same budget |

One knob, `config.rest_timeout()` — `NOODLE_REST_TIMEOUT` (seconds, default
30), deliberately separate from `NOODLE_TIMEOUT`: a report endpoint or a cold
serverless call answers in minutes and a UI timeout has no business capping it.
Per step, `within N seconds` overrides it:

    When User performs a GET call at '/reports/annual' within 180 seconds
    And User waits for the response from '/api/orders' within 90 seconds

Every budget is a **ceiling, not a sleep** — the step continues the instant the
response lands (socket-blocking / Playwright event, never a timer); a unit test
pins that a 2s response under a 10s budget costs 2s.

All woks: `rest_*` steps need no browser, so this is the same budget in `@api`,
`@web`, `@appium`/mobile, desktop and `@perf` scenarios.

A timeout now fails as an assertion that names the budget and both ways to
raise it, instead of a bare `URLError`. Non-timeout `URLError`s (DNS, refused,
TLS) still propagate untouched — those are real failures, not slow ones.

## [0.2.0a34] — 2026-07-25

**NOOD_0181** — feature: mobile-web touch gestures, plus four capability gaps
found reviewing the framework after NOOD_0180.

`@mobile` already built a genuinely touch-capable browser context — Playwright's
device presets carry `maxTouchPoints: 1`, `pointer: coarse`, a mobile
user-agent and the device DPR — but the step language could not reach any of
it. `swipes up` hard-failed with *"tag the scenario @appium"*, i.e. go buy a
device, for something the emulated browser does natively. Measured:

    @mobile (Pixel 5)   maxTouchPoints=1  coarse=True   MobileUA=True   DPR=2.75
    viewport-only       maxTouchPoints=0  coarse=False  MobileUA=False  DPR=1

- **Touch gestures on web.** `swipes left/right/up/down` and `long-presses 'X'`
  now run in any touch context. CDP `Input.dispatchTouchEvent`, not
  `page.mouse` and not a JS-built `TouchEvent`: mouse events carry
  `pointerType: 'mouse'` so touch-only handlers (Swiper, Embla, framer-motion)
  ignore them, and JS-dispatched touch events are untrusted so the browser
  never scrolls. Same geometry and direction convention as the Appium wok
  (finger up ⇒ page scrolls down), so a gesture reads identically in both.
  Chromium-only; raises rather than degrading to a mouse drag, because a
  gesture that reports success while firing the wrong event type is exactly the
  test-that-cannot-fail NOOD_0180 was about.
- **`clicks`/`taps` arrive as touch** in a touch context (`loc.tap()`), so
  `pointerType` is `'touch'` and `touchstart` fires. Plain click handlers keep
  working — the browser synthesises a click from the touch.
- **A narrow viewport now says it isn't mobile.** `sets the viewport to
  390x844` / `switches to mobile view` only ever changed the width; the context
  kept no touch, a desktop UA and DPR 1, so a responsive site served its
  desktop branch and the test quietly proved nothing. Playwright cannot grant
  touch to a live context, so resizing below 500px in a desktop context warns
  once and names `@mobile`.
- **`@device:<preset>`** — the roster was two hardcoded names (iPhone 13,
  Pixel 5); Playwright ships ~130. `@device:pixel_7`, `@device:iphone-15-pro`
  (tags can't hold spaces, so `_`/`-` become one). Unknown names fail loudly
  with suggestions. `@mobile` keeps its historic defaults.
- **`NOODLE_BROWSER_ARGS`** — the popup taxonomy has told testers since
  NOOD_0131 to "add a disable flag" for Chrome's sign-in/save-password bubbles,
  but `launch()` only ever got headless/slow_mo/channel, so there was nowhere
  to put one. shlex-split, so a flag value containing a space survives.
- **`drags 'file.png' to the 'upload area'` was a phantom.** Documented since
  NOOD_0009, but `drag()` resolved `'file.png'` as a DOM element and failed as
  not-found. A source naming a file on disk is now a real file-drop via
  `DataTransfer` — for dropzones that only listen for `drop`. (Ones with a
  hidden `input[type=file]` were already covered by `uploads ... to`.)
- Routing: `swipes`/`long-presses` no longer tag a generated feature `@appium`
  — only gestures with no browser equivalent do. `sends the app to the
  background`, the canonical phrasing, never matched that rule at all; fixed.
- **Naming a window.** Separate browser windows already worked — `window` is a
  synonym for `tab` throughout, and Playwright makes no distinction (a
  `window.open()` popup, a `target="_blank"` link and a new tab are all pages in
  one browser context). But the switching vocabulary was a *two*-page model:
  `new`/`last` → newest, everything else → first. With a shop page, a support
  chat and a payment window open at once, "previous" means nothing. New:
  `switches to the window titled 'Support chat'` / `switches to the 'Payment'
  window`, matching title **or** URL (case-insensitive substring) — URL too
  because a chat popup often opens with an empty title. No match lists every
  open window with its title and URL; two matches is an error rather than a
  guess, since taking the first is how a test asserts against the wrong window
  and passes anyway. Query strings are redacted from those messages (NOOD_0177).
- **The unit suite now has zero skips.** Two tests were permanently skipped and
  neither was pulling its weight. The OCR bridge test needs the `tesseract`
  binary, which no gate installed — so the pixel path behind `@terminal` /
  `@ocr_fallback` had no coverage at all; both gates now install it. That test
  also skipped whenever OCR returned nothing recognisable, which meant a real
  regression in `find_all_text_in_image` read as an environment quirk; it now
  only skips on Pillow < 10.1, whose default bitmap font genuinely cannot be
  OCR'd, and asserts otherwise. The opt-in live acceptance test was deleted: its
  URLs had been scrubbed to `example.com` placeholders that 404, so it could not
  pass even with `NOODLE_LIVE_ACCEPTANCE=1` — a dead skip reading as a working
  opt-in. Its fixture prompt is still exercised by the stub tests around it.
- Both CI gates now run `playwright install chromium` before the unit suite.
  The playwright package is a core dep but the browser download is separate,
  so the new touch tests — which prove a CDP touch event actually scrolls the
  page, something no fake can tell you — would have skipped in CI and let the
  gate go green having proved nothing. They skip cleanly (not error) when the
  binary is absent, so a contributor without browsers isn't blocked.
- Two NOOD_0133 install tests were environment-dependent: they pinned
  `pip install -e` for the non-uv-tool case, but `reinstall_argv()` is a
  three-way branch (uv tool → pip → `uv pip --python`) and a uv-created venv
  ships no pip, so they passed in CI and failed on a dev machine — the check
  that exists to catch a wrong environment was itself environment-dependent.
  `_has_pip` is now pinned per case so all three flavors are exercised, and
  the doctor assertion pins `noodle update` rather than one flavor's spelling.

## [0.2.0a33] — 2026-07-25

**NOOD_0180** — fix: a scroll test could not fail. New `is in view` assertion.

The engine could already *drive* every scroll an element-owned scrollbar needs
— `scrolls to 'X'` (both axes), `scrolls the 'X' panel to the right/bottom`
(`left`/`right` included), `scrolls the grid right`, `scrolls until 'X' is
visible`. What was missing was any way to *verify* one had happened.

`should see 'X'` rides Playwright's `is_visible()`, which reports whether an
element has a box in the layout, not whether the tester can see it. An element
scrolled outside its container's overflow keeps its box. Measured against
`codepen.io/jaemskyle/pen/XWJvRL` — a 500px-wide strip holding 1020px of
sections:

    scrollLeft=0   section six: is_visible=True  intersectionRatio=0.00
    after scroll   section six: is_visible=True  intersectionRatio=1.00

So the natural test — `scrolls right` then `should see 'six'` — passes without
ever scrolling, and keeps passing if the scroll breaks.

- `assert_on_screen` — asks IntersectionObserver, the only native API that
  clips the target against every ancestor's overflow *and* the viewport.
- Steps: `'X' is in view` / `is on screen` / `is in the viewport` /
  `is scrolled into view`, plus `is not` / `is no longer` / `should not be`
  negations. Ordered above the `assert_visible` block; existing
  `should see` / `sees 'X' on the screen` phrasings are unchanged.
- Resolves with `heal=False` — the self-heal chain's first move is to scroll
  the element into view, which would scroll the container under measurement
  and make every `is not in view` a false failure.
- Failure message says the element **is** in the DOM, so the reader looks at
  the scroll, not at the locator.
- Docs: steps_dictionary gains "Asserting a scroll worked" and the container
  forms, incl. the POM-cannot-reach-into-an-iframe caveat.

## [0.2.0a32] — 2026-07-25

**NOOD_0179** — perf: probe wall-clock and payload cost (CLI + MCP parity; chromium primary, engine-selectable).

Four measured costs, one phase each. The probe sits on the hot path of every authoring session, and three of the four were paid on every call for no result.

- **The browser launches once per engine, not once per call.** A long-lived host (MCP server, `noodle repl`) paid ~0.5-1 s of launch overhead per probe; three probes in one process now launch one browser (measured 3.99 s → 3.22 s on a trivial page, and the saving scales with probe count). The pool lives on ONE hand-rolled **daemon** worker thread, which is what makes it safe: Playwright's sync objects are thread-affine and `outside_asyncio` spawned a fresh executor thread per call, so a naively cached browser would be touched from the wrong thread on the next call; and a non-daemon thread holding a live Playwright blocks interpreter shutdown, hanging every one-shot `noodle probe`. A subprocess test pins prompt exit. Each call gets a fresh **context**, and `finally:` closes the context — never the shared browser.
- **The probe honors `NOODLE_BROWSER`** (chromium default; firefox/webkit/safari/edge), which it previously ignored — `p.chromium` was hardcoded. Browsers are keyed by the resolved `(engine, channel)` pair, so flipping the env mid-session cannot serve a chromium browser to a firefox request. A missing engine returns an `errors` entry naming the fix (`playwright install firefox`, install Edge) and **never** silently falls back to chromium — NOOD_0122, decide by engine: a silent substitution would make the probe claim evidence it never gathered. `_ENGINE_ALIASES` moved into `noodle/config.resolve_engine()`; `hooks.py` and `cli.py` now share that one table instead of holding two literal copies.
- **The initial settle watches the DOM instead of the network.** Navigation mode paid a flat `networkidle` 3 s ceiling after every `goto`, and analytics-chatty pages (most retail sites) never go network-idle — 3 s per URL spent proving nothing. A MutationObserver decides: no mutation within 700 ms ⇒ static ⇒ done; otherwise wait for a 250 ms-quiet window, with a 1 s network belt for pages that fetch without touching the DOM. Standards only, so behaviour is identical on chromium/firefox/webkit — no CDP, no engine branches. Any scripting failure falls back to the legacy settle verbatim, and the Angular blank-body wait (NOOD_0109) is untouched.
- **Uniqueness verification is one evaluate, not 60 round trips.** `_verify_unique` ran up to `_UNIQUE_CAP` sequential `locator(sel).count()` calls per block, repeated for every revealed block and every frame. Plain-CSS selectors — the majority — are now counted in a single `evaluate`, summed across the document and every open shadow root: Playwright's locator pierces open roots and `querySelectorAll` does not, so summing is what keeps the counts identical to the loop it replaces. `text=` / `:nth-match(` / `>>` selectors keep the per-selector loop, marking semantics are unchanged, and a blown-up evaluate degrades to the loop.
- **`--brief` / `brief=True`** (CLI, MCP, REPL) sends the three step sentences once as `step_templates` + per-kind `step_names` instead of repeating `clicks "<name>"` on every row. Rows whose wording is load-bearing — needs-POM, machine-named, attribute-caption-only — keep their exact `step`, because "use the suggested step as-is" is what keeps authored steps resolving at run time. **Honest number: ~0.92x, not the 0.75x the ticket estimated** (measured 7470 → 6854 bytes on a real retail homepage). Template overhead is 9 bytes on a click row and 30 on a field row; the control NAMES are the payload, and shedding them — or the exact steps of POM-needing rows — would cost exactly the precision the exception rule protects. The cap ladder, not brief, is what bounds a genuinely oversized payload.
- **Fix: `--compact --section <slice>` silently ignored the compact cap.** `render()` returned from the section branch before the cap was computed, so `--compact --section steps` emitted the whole uncapped inventory — one real call returned ~600 lines from a flag documented as "compact caps at 25". Now 26 lines (25 + the overflow note). Non-compact section renders stay uncapped, as opt-in verbose.
- **Instruction budget:** `hot-tool-docstrings` 6144 → 6400 and `cli-help (noodle probe --help)` 6144 → 6400, +256 each. They sat at 3 and 62 bytes of headroom, so `brief` could not be named on either surface without a raise. Per the §7 rule the substance did move to a doc section (docs/llm-performance.md §4); what each surface buys is one clause naming the flag, because a parameter's EXISTENCE is routing — an agent that never learns `brief` is a flag cannot read the doc that explains it.
- Knobs, all defaulting to the new behaviour: `NOODLE_PROBE_REUSE_BROWSER=0` (launch per call, still engine-aware), `NOODLE_PROBE_SETTLE=legacy`, `NOODLE_UNIQUE_LEGACY=1`, `NOODLE_PROBE_TIMINGS=1` (adds the settle-path debug key; the default payload never grows).
- Caught in review: the new uniqueness script was first named `_COUNT_JS`, shadowing the existing module-level `_COUNT_JS` that finds the "NN results" element. The later binding wins for every caller, so the results-count feature would have broken silently. Renamed `_UNIQUE_COUNT_JS`, with a test pinning that the two stay distinct.
- Follow-ups, deliberately out of scope: parallel multi-URL probing, a session-scoped probe result cache, browser reuse for `repl/ground.py` and `inspect_locator`, and `NOODLE_REMOTE_URL` remote-browser connect for the probe.

## [0.2.0a31] — 2026-07-24

**NOOD_0178** — feature: the probe follows new tabs (open → probe → switch back).

`probe()` drove a single `page` object, so a reveal/`--do` click that opened a tab (`target=_blank`, `window.open`) left the probe evaluating the **original** page: the new tab's controls were never collected, and the click was reported with the false "no observable delta" note the NOOD_0156 follow-up added. The runtime has switched tabs since NOOD_0025 (`runner._switch_tab`); the probe now has the same awareness, so one probe session discovers click → new tab → its controls → switch back → continue.

- **Detection is by page-list growth**, never `page.wait_for_event("popup")` after the click. The popup event fires *during* the click and a listener registered afterwards resolves on the *next* one — the exact bug NOOD_0177 fixed in `runner._switch_tab`, and the reason `_new_tab()` compares `context.pages` snapshots taken before and after each click instead.
- **The tab becomes its own block** under `revealed`, labelled like a frame block and tagged `new_tab` / `tab_url` / `opened_by` / `switch_steps`. `switch_steps` are verbatim runtime vocabulary — `Then a new tab should open`, `When User switches to the new tab` — pinned against the pattern table by unit test, as is the return step `When User switches to the original tab`. The tab gets the same navigation-mode settle, popup sweep and per-scope uniqueness proof the initial load gets; no diff against the opener, because a selector string means nothing across documents.
- **The transaction continues across tabs.** Later `--do` actions act on the newest page (the "act on the page the flow is ON now" rule NOOD_0168 set for search landings), `_reveal`/`_do`/`_pick` return the active page, and `--expect` runs on the page the transaction ended on. Each url of a multi-url probe starts on the original tab again, so `goto` can never navigate a probe-opened one.
- **New fourth `--do` verb: `switch to <new|last|previous|original|first|main> tab`** — same target semantics as `runner._switch_tab` (new/last = `pages[-1]`, the rest = `pages[0]`). A switch brings the tab to front (a background tab's timers are throttled; without the focus + mutation window the diff raced a cart badge that landed 1.5 s after the click and reported nothing), then diff-snapshots what changed while away. The regex alternative is a single `\S+` token anchored by `tab`, so it adds no backtracking surface to the `_MAX_DO_LEN`-bounded parse; a bad target raises before any browser launches and the message points at `click <name>` for a tab *within* the page.
- **Diff sets are now per document.** One shared `seen` set meant a tab's selectors filtered the opener's delta away — exactly the evidence a switch-back exists to capture.
- Edge cases are warnings, never raises (probe stays advisory): a tab that closes immediately (download shim, self-closing popup), one that never leaves `about:blank` within 3 s, and more than one tab at once (newest probed, count reported).
- `_PERM_JS` moved from `page.add_init_script` to `context.add_init_script` — a page-level init script never reaches a tab that page opens, so a followed tab had no permission shim to read. Signals are read per page.
- The compact payload carries the new keys inside the existing block structures, so `payload_budget` trims them like any other list (a two-tab result is size-asserted). The skeleton weaves the tab leg in runtime order: click → assert → switch → tab assertion → switch back.
- Instruction budget: net zero. The `probe_page` MCP docstring and `noodle probe --help` both document the verb by trimming duplicated wording elsewhere in the same surface (three repeats of `each delta under "revealed"`, `no-op icon flags`, two ticket refs, and a shorter `--do`/`--search`/`--pick`/`--compact` help); the substance lives in `docs/agent-playbook.md` §0.3 per the surfaces-route/docs-carry rule.
- `unit_tests/test_nood_0178.py` (34 tests, browser-free fakes) pins detection, the grammar round trip, the pattern-table contract, the cross-tab transaction, payload size and the render/skeleton shape. Verified live against a two-page fixture (`target=_blank` link, `window.open` button, self-closing shim, blank tab) and the emitted skeleton then **ran green** through `noodle run` unchanged.

Out of scope, unchanged: the runtime (`_switch_tab` already works; a >2-tab back-stack stays its documented "add when needed"), cross-origin popup content beyond what Playwright exposes, and tabs a page opens by itself on load rather than from a probe-driven click.

## [0.2.0a30] — 2026-07-24

**NOOD_0177** — fix: security audit remediation — page content could reach a shell, and credentials could reach every shared artifact.

A seven-lens security audit (Trail of Bits methodology) plus a semgrep sweep found ~50 issues that reduce to two root causes. Both are closed here, with `unit_tests/test_nood_0177.py` pinning each invariant.

**Root cause A — a `.feature` file is executable code, and humans no longer write feature files.** `script_runner.py`'s own docstring states the assumption ("feature files are trusted code"), and it was true when a person typed the feature. Three mechanisms broke it.

- Fixed: **an `aria-label` could inject executable Gherkin.** `_COLLECT_JS` captures `aria`/`title`/`placeholder`/`alt` raw, and `_name_and_source` only did `.strip().lower()` — which trims the ends, so an interior newline survived. `compile_goal` builds the feature by `"\n".join()`-ing step bodies, so a crafted accessible name added real `When User runs the command '…'` lines to a compiled feature that `author_test(run_after_author=True)` then ran in the same call. New `probe._clean_name` collapses whitespace and caps at 80 chars; `compile_goal` independently refuses any step body containing a line break.
- Fixed: **captured page text reached `shell=True`.** `runner.substitute()` expands `{var:}` *before* the step is parsed, so a stored element's text was indistinguishable from authored command text. `substitute()` grew `quote_captured=`, and `execute_step` re-resolves `run_command`/`run_script`/`app_launch` from the pre-substitution sentence with captured values `shlex.quote()`d. Workspace config (`{env:}` from `os.environ`) is left unquoted, so URLs and flags keep working.
- Fixed: **the LLM could select an execution action type.** `run_command`, `run_script` and `call_function` were on `VALID_TYPES`, the only gate on model output — and the rejection message printed the full list, advertising them. New `LLM_FORBIDDEN_TYPES` rejects them from the model path (the pattern table still dispatches them from a hand-authored step).
- Fixed: **`reflect.try_fix` let a page rewrite the feature file.** The prompt embeds the failure message, which quotes page content verbatim, and the reply was written to disk and run unattended. It now passes `validate.check_feature` and an execution-step denylist first, matching what `prompt_expander.model_fallback` already did.
- Fixed: **POM key injection** — keys are page-derived text and were emitted unquoted, so a `:` broke authoring and a newline added attacker-chosen entries that silently re-bind a step phrase on every future run. Keys now go through `_yaml_str` like values.
- Fixed: **`_FUNCTION_REF_RE` spanned newlines** (`[^'"]+` is a negated class), and its capture was interpolated into `def {func}(…)` in a file `call_function` later `exec_module()`s. Constrained to the spec alphabet, plus an `isidentifier()` check and workspace containment on the target path.

**Root cause B — redaction was a logging filter, so every shared artifact bypassed it.** `log.redact()` masks by *value* (the right design) but was wired only into the logging pipeline and `diagnostics.py`: the console was scrubbed, the report you email was not.

- Fixed: **Allure `statusDetails` and JUnit XML are redacted.** `assert_value` formats the expected `{env:PASSWORD}` *and* the live field value, so one failing login printed the credential twice into the served report and the CI Tests tab.
- Fixed: **network log** — request URLs keep their path only, WebSocket frames keep url/direction/length instead of raw payloads (an auth handshake carries the bearer token in frame 0), and the whole document is value-scrubbed.
- Fixed: **failure URLs drop their query string** — SSO callbacks and reset links carry their credential there.
- Fixed: **a resolved secret was echoed to the driving agent.** `probe_page` substituted `{env:}` into `do` actions *before* `parse_do` validated them, and `parse_do`'s `ValueError` embeds the offending string — which is returned verbatim over MCP. Parsing now happens first, restoring the "raw credentials never transit the transcript" guarantee both `core.py` and `mcp/server.py` document.
- Fixed: **step names and artifact filenames are scrubbed.** A step written with a literal credential put it in the Allure step title on the passing path, in the screenshot *filename*, and — via `annotate.py` — burned into the PNG itself, none of which any later text redaction reaches. Scrubbing now happens before the filename is built, and the character allowlist also closes a Windows traversal that stripping `/` alone left open (`\` was not stripped). The engine's own sample features no longer hard-code `Popcorn1!` / `secret_sauce` either — they are what an agent copies.
- Fixed: **evidence metadata is redacted.** Found by running the real suite rather than by reading code: `locator` holds the resolved step phrase and `text` the matched element's own text, so `should see "{env:PASSWORD}"` recorded the live credential into *both* Allure `statusDetails` and `last_run.json` — and on the **passing** path, since evidence is captured by default (`NOODLE_EVIDENCE=last`). Redacting the failure message alone was not enough; the scrub now happens in `evidence.capture`, the one place that builds the dict, so every consumer inherits it.
- Fixed: **secret and session files are written 0600** via `config.write_private`, created private rather than chmod'd after. `os.replace` swaps the inode, so hand-hardening a secrets file was silently reverted on every re-author.

**Insecure defaults**

- Changed: **TLS certificates are now VERIFIED by default** (`hooks.ignore_https_errors`), and the scaffolded `.env` writes `NOODLE_IGNORE_HTTPS_ERRORS=false`. Relaxing it is explicit — `@insecure_certs` per scenario or the env var run-wide — and logs a warning naming the risk. `@secure_certs` still forces verification and wins. The perf wok's `_LAX_TLS` was unconditional `CERT_NONE` with a comment claiming a caller honoured the flag; no caller did, so it had no way to verify at all. It now reads the same switch.
- Fixed: **`noodle clean` could delete a home directory.** `Path(workspace) / "/abs"` collapses to `/abs`, so an absolute `NOODLE_ARTIFACTS_DIR` (or a pointer file holding one) made `rmtree` delete it. Containment is checked first; a CI variable typo was enough to trigger this.
- Fixed: **the scaffolded workspace `.gitignore` now covers run output** — `artifacts/`, `reports/`, `archives/`, `output/`, `baselines/`, `session*.json`. Traces record request *and response* headers plus DOM snapshots, so `noodle init` → run → `git add .` published production session cookies.
- Changed: `serve_report`/`_make_server` default to `127.0.0.1` instead of `0.0.0.0`, and **directory listing is disabled** (403). The report server has no authentication by design, so the listing was the whole exposure — and `docs/steps_dictionary.md` used to tell people to save a browser session into exactly that directory. The documented example now writes to `artifacts/session.json`, outside the served root.

**Containment, SSRF and denial of service**

- Added: **`noodle/target_policy.py`** — one chokepoint every outbound target passes through, used by `rest_client`, `preconditions`, `loadgen` and `probe_page`. http/https only (the default urllib opener speaks `file://` and `ftp://`, so `REST_BASE_URL=file:///etc` made the API wok a local file reader), cloud metadata endpoints always refused, optional `NOODLE_TARGET_ALLOWLIST` host globs. `rest_call` also stops following redirects, which used to carry an `Authorization` header across hosts.
- Fixed: **exponential ReDoS in `press_key`** — `[^\s"']+` could swallow the `+` separating chord segments, so a 110-byte `.feature` file cost 39.8s through `check_feature()` and doubled per added pair. One character (`[^\s"'+]+`); verified behaviour-identical and linear.
- Fixed: **cubic ReDoS** in the prompt compiler's clause patterns and probe's `_DO_RE` — `(.+?)` straddling two `\s+` boundaries ran past 120s on a 1200-space prompt, and `parse_do` runs *before* any browser launches. Whitespace is collapsed and clauses capped.
- Fixed: **`assert_matches` compiled an unbounded regex** whose match subject is text read from the site under test. Nested quantifiers are refused and the pattern length capped.
- Fixed: **`probe_page` was a local-file read oracle** — it returns a ±30-char window per `expect` hit, so `file:///…/.aws/credentials` exfiltrated a key a slice at a time. Local fixture pages now need `NOODLE_ALLOW_LOCAL_URLS`.
- Fixed: **`serve_report(report_dir=…)` bypassed `_ALLOWED_ROOTS`**, so it could publish a directory listing of `$HOME` over localhost.
- Fixed: **`environments.yaml` was built with an f-string**, and `environment_values` (MCP-exposed) was unvalidated — so a caller could set `LD_PRELOAD`/`NODE_OPTIONS` and have the next child process load their code. Now `yaml.safe_dump` plus a key check, and `hooks._apply` refuses loader/interpreter variables outright.
- Fixed: **`_app_from_existing_url` returned an unsanitized app key**, letting `author_test` write outside the workspace (including `<app>_secrets.env`) while the containment check passed vacuously.
- Fixed: **`noodle report stop` signalled unvalidated PIDs** from a workspace JSON file. `os.kill(-1, …)` signals every process the user owns.
- Fixed: **screenshot and `save_session` paths are contained** to the workspace/artifacts root, matching what `repl/core.py` already enforced for the same writes over MCP.
- Added: **perf load caps** (`NOODLE_PERF_MAX_USERS`/`_MAX_DURATION_S`/`_MAX_REQUESTS`) — `wok.infer_tag` routes anything mentioning "performance" to `@perf`, so an ambiguous sentence could point an uncapped flood at production.
- Added: **`noodle/safe_xml.py`** — refuses a `DOCTYPE` before parsing Appium `page_source`, `.xlsx` members and merged JUnit files. ElementTree hasn't resolved external entities since 3.7.1, but entity expansion still takes the process down.

**Suite failures found by running the samples (root-caused, fixed test-first — `unit_tests/test_nood_0177_tabs_and_counts.py`)**

- Fixed: **`a new tab should open` could never pass.** `_switch_tab` went straight to `page.wait_for_event("popup")`, on the stated assumption that it "retrieves the queued popup event even if the click already fired". It does not — `wait_for_event` registers a listener and resolves on the *next* occurrence. The assertion is always its own step, so the popup had already fired during the preceding `User clicks "Preview"` and the step burned the full timeout every time. It now checks for an already-open tab first, and only waits when one is genuinely still in flight.
- Fixed: **the acknowledged tab count leaked between scenarios**, which is why `trailer.feature` passed alone and failed in the suite: a scenario following one that opened a tab started life expecting two, so the already-open check missed and it fell back into the doomed wait. `before_scenario` now re-seeds it alongside the fresh browser context.
- Fixed: **a `target="_blank"` click was reported as having "no observable effect".** Opening a tab *is* the effect, but it changes nothing on the current page, so the url/DOM/network checks all came up empty and every Preview-style click logged a false warning plus a healing event — dragging an otherwise green run to `verified: false`. The pre-click probe now snapshots the tab count too.
- Fixed: **`should see N "X" items` counted 1 while 50 were on screen.** Not a bug — `assert_count` counts through a matching POM key by design (NOOD_0115), and the sample POM scopes `add to cart` to `tr:first-child` so click scenarios get one unambiguous target. The sample now has a separate all-rows `add to cart buttons` key and the scenario counts through it, and the failure message names the POM key and selector it counted through, so the next reader does not repeat the investigation.

**Supply chain, container and editor**

- Fixed: **`.dockerignore` excluded caches but not `secrets.env`/`.env`/`*_secrets.env`**, so `COPY . .` baked a developer's live credentials into an image layer pushed to ACR. The container also now runs as `pwuser` instead of root.
- Fixed: **`squad-heartbeat.yml` checked out PR code** on `pull_request: closed` and executed a checked-in script from it. Pinned to the default branch.
- Fixed: **the VS Code extension's `noodle.pythonPath` is now `machine`-scoped** and the extension declares `untrustedWorkspaces: false` — a cloned repo's `.vscode/settings.json` could otherwise point it at an executable of its choosing.
- Fixed: **LSP hover masked by name only** while `log.py` masks by value, so `DB_PASS`, `ADMIN_PIN` and DSNs rendered in cleartext in a tooltip. The name list is widened and any value defined in a sibling secrets file is masked whatever it is called.

## [0.2.0a29] — 2026-07-24

**NOOD_0176** — fix: LSP hover/tooltips dead when the extension launched a Python without noodle (highlighting worked, tooltips didn't).

The extension launched `<python> -m noodle.lsp.server`, guessing `python` as `noodle.pythonPath` → workspace `.venv` → bare `python3`. `pygls`/`lsprotocol` are `[lsp]`-extra-only and imported at the top of `noodle/lsp/server.py`, so on an Option-B (`uv tool install`) machine with no workspace `.venv`, the guessed `python3` couldn't import the server — the process crashed and no hover appeared, while the (declarative) TextMate highlighting kept working.

- Changed: **the extension now prefers the `noodle-lsp` console script** (`pyproject [project.scripts]`), resolved to an **absolute** path — its shebang is the exact interpreter that installed noodle, extras included, so hover works with **zero `noodle.pythonPath` config**. It searches, in order: the sibling of a configured `noodle.pythonPath`, the workspace `.venv` bin, the **dev-clone `.venv`** two levels up from the extension's own realpath (so a `noodle install-extension` symlinked from a clone finds that clone's venv even when it's on nobody's GUI PATH), `~/.local/bin` (uv-tool default shim dir), then every `PATH` dir — searching for the real file rather than trusting a bare `noodle-lsp`, because VS Code launched from Finder/Dock often has a stripped `PATH`. Falls back to `<python> -m noodle.lsp.server` when the script isn't found.
- Note: this is a VS Code **extension** change — `git pull` + `noodle update` refresh the Python side but not the editor. Re-run `noodle install-extension` (or Reload Window if already symlink-installed) to pick it up. After it lands, the `noodle.pythonPath` workaround is no longer needed for a standard install.
- Changed: **the scaffolded `files.associations` glob is now scoped to the workspace's `tests_dir`** — `**/<tests_dir>/**/*.feature` (default `**/noodle_tests/**/*.feature`) instead of the repo-wide `**/*.feature`. So a monorepo that also holds a Selenium/Playwright project under the same opened folder keeps *those* `.feature` files for Cucumber — noodle only claims its own tests subtree. `noodle init` migrates an existing workspace's old broad `**/*.feature` to the scoped glob. The legacy multi-wok `sample_feature_tests/` layout (features at root `web/`, `api/`) and the engine repo's own hand-committed `.vscode/settings.json` keep `**/*.feature` (all-noodle, no foreign framework to protect).
- Tests: `unit_tests/test_nood_0176_lsp_launcher.py` — pins the `noodle-lsp` console-script contract the extension depends on. Plus a session-scoped `conftest.py` guard that fails the suite (naming the files) if any test scaffolds into the engine checkout via a cwd-relative command without `monkeypatch.chdir(tmp_path)` — the class of leak that dropped stray `noodle_tests/`, `AGENTS.md`, `.vscode/mcp.json` into `git status`.

## [0.2.0a28] — 2026-07-24

**NOOD_0175** — fix: the NOOD_0174 workspace association matched nothing, so the LSP stopped attaching (no hover, no param tooltips).

The scaffolded `files.associations` glob was `**/noodle_tests/**/*.feature`, but workspace features live under `<app>/features/` (`web/busterblock/features`, `api/features`, …) and the default `tests_dir` is `tests` — not `noodle_tests`. The glob matched no real feature file, so nothing resolved to the `noodle` language and `noodle/lsp/server.py` never ran: hover over a step or an `{env:}`/`{var:}`/`{pom:}` token showed no tooltip. (Cucumber in other projects was unaffected, since the global `.feature` claim was correctly removed.)

- Fixed: **the scaffolded glob is now `**/*.feature`** — every `.feature` in a noodle workspace is noodle's own, and the association is folder-scoped (only applies when that workspace is the open folder), so a separate Cucumber project is still untouched. Matches the engine repo's own `.vscode/settings.json` (already `**/*.feature`).
- Migration: existing workspaces scaffolded on 0.2.0a27 have the wrong glob — re-run `noodle init` to add the corrected association (it's additive; the stale `**/noodle_tests/**` entry is harmless but can be deleted by hand), then **Developer: Reload Window**. Or add `{ "files.associations": { "**/*.feature": "noodle" } }` to `.vscode/settings.json` directly.
- Tests: `unit_tests/test_nood_0174_feature_association.py` unchanged — it asserts against `_NOODLE_ASSOC`, so it now pins the corrected glob.

## [0.2.0a27] — 2026-07-24

**NOOD_0174** — stop the VS Code extension colliding with real Cucumber `.feature` handling (Option B).

The extension declared `extensions: [".feature"]` for a `noodle` language, claiming the extension globally. With alexkrechik/VSCucumberAutoComplete or cucumber/vscode also installed, VS Code picks one language owner per `.feature` file — so depending on install order, either our highlighting/LSP or Cucumber's silently didn't activate. behave hardcodes `.feature` discovery, so renaming files on disk was never viable; the fix is editor-side scoping instead.

- Changed: **VS Code extension no longer claims `.feature` globally** — dropped `extensions: [".feature"]` (and the `Gherkin` alias) from the `noodle` language contribution. Grammar, snippets and the LSP still key off the `noodle` language id, unchanged.
- Added: **`noodle init` scaffolds `.vscode/settings.json`** with `"files.associations": { "**/noodle_tests/**/*.feature": "noodle" }`, mapping only *this* workspace's feature files to our language. Every other `.feature` on the machine stays Cucumber's; no more collision either way. Merged, never clobbered (preserves existing settings).
- Note: existing workspaces pick up the association on the next `noodle init` (`--force` not required — the write is additive). A `.feature` file opened outside a scaffolded workspace no longer auto-loads the noodle language, which is the intended, safe default.
- Added: **`noodle install-extension`** — links `vscode-extension/` into the editor's extensions dir (default `~/.vscode/extensions`; `--extensions-dir` for Cursor/VSCodium). The extension's `node_modules` are vendored, so it needs no `vsce`, no `.vsix`, and no `code` CLI. It removes any prior noodle install first (a stale sideload was the usual reason highlighting/LSP stopped working), then symlinks so a `git pull` + Reload Window picks up fixes with no reinstall. Replaces the `vsce package` → `.vsix` → `code --install-extension --force` dance as the recommended path (that path still works for a real `.vsix`). Docs updated: README (both OS runbooks), `manual.md` Part 3, `encyclopedia.md` §12, `llm-install.md` §4, `cli-reference.md` — and all now say a Cucumber extension no longer needs disabling.
- Fixed: **the engine repo's own `.vscode/settings.json`** now carries `"files.associations": { "**/*.feature": "noodle" }`. Dropping the global `.feature` claim would otherwise have left the ~71 in-repo `sample_feature_tests/**` features with no noodle highlighting/LSP when browsed inside the clone (the engine repo isn't a `noodle init` workspace). Every `.feature` in the clone is noodle's own, so a repo-wide association is correct and stays scoped to this folder.
- Tests: `unit_tests/test_nood_0174_feature_association.py` — association merge preserves other settings / is idempotent / survives unparseable JSON; install helper lands the extension and replaces a prior install.

## [0.2.0a26] — 2026-07-24

Phase 5 (final) of the NOOD_0171 logging plan, on ticket **NOOD_0173**: the run/scenario/step lifecycle events, the container default, and the docs. Completes the structured-logging + AI-governance work.

- Added: **lifecycle telemetry** — `run.start`/`run.end` (from the CLI, once per run — so a parallel run gets exactly one, with counts, `exit_code`, `duration_ms`, `llm_usd`, and model/engine/`git_sha` provenance the behave child can't see), `scenario.start`/`scenario.end` (status, duration), and `step.fail` (step, error class, redacted error, screenshot). Emitted **json mode only** via new `log.telemetry()` — the text console stays byte-for-byte unchanged (the phase-1 contract); the human already has behave's output and the CLI summary.
- Added: **`ENV NOODLE_LOG_FORMAT=json` in the `Dockerfile`** — a container ships structured logs to its platform store by default (12-factor XI); override to `text` for a human console.
- Added: **`docs/logging.md`** — enabling json, the sinks (console / file log / per-worker), the OTel record shape, the full event reference table, the redaction guarantees, and starter Log Analytics KQL. Linked from `architecture.md`.
- Tests: `unit_tests/test_nood_0173_logging.py` — `log.telemetry()` json-gating and the `run.end` provenance payload; live-run verification of `scenario.*`/`step.fail` in the file log.

## [0.2.0a25] — 2026-07-24

Phases 3–4 of the NOOD_0171 logging plan (`tmp/nood_0171-logging-plan.md`), on ticket **NOOD_0172** now that phases 1–2 have merged: the `noodle-mcp` server audit trail (phase 3) and the AI-governance events (phase 4). Phase 5 (`run.end`/`scenario.*`/`step.fail` attributes, Dockerfile json default, dashboards) remains an independent follow-up.

Phase 3 — MCP server audit trail:

- Added: **`mcp.tool` audit event** — every `noodle-mcp` tool call is timed and logged in one place (the `_tool()` wrapper): `tool`, `workspace`, `duration_ms`, `ok` (a tool's own `{"ok": false}` counts, not just exceptions), `error` class, `payload_bytes`. Each call mints a `run_id` that a triggered run inherits via `NOODLE_RUN_ID`, so "agent called `run_and_report`" ties to "scenario X failed" in one query.
- Added: **`mcp.auth.deny` security event** — the streamable-http key gate logs denied requests at WARNING with `remote_ip` (x-forwarded-for aware, for the Container Apps ingress), `path`, and `reason` (`missing key`/`bad key`) — **never the supplied or configured key**.
- Added: **`log.route_console_to_stderr()`** — the MCP server's own log lines go to stderr, never stdout (its stdio protocol channel); called once at server start, no-op in json mode.

Phase 4 — AI governance (the "did a model change what this test did, and can you show me?" record):

- Added: **`llm.call` event** on every model call (`llm/client.py`) — `model`, `purpose` (`llm`/`rca`), `input_tokens`/`output_tokens`, `usd`, `duration_ms`, `temperature`, and `api_host` (host/provider only, **never** the key-bearing URL). A spend cap firing logs its own `capped` event at WARNING. `cost.record()` now returns the per-call metrics so the event and the run-total ledger share one computation.
- Added: **`locator.resolve` (strategy `vision`)** and **`locator.heal`** — the two places a model or a fuzzy heuristic, not the test author, decides what gets acted on. `locator.heal` fires for every non-primary resolution with `original`/`technique`/`fuzzy` (the `fuzzy` flag marks the tiers that don't prove intent).
- Added: **RCA provenance** — a vision-model root-cause verdict is now marked `ai_authored: true` with its `model`, and emits an `rca.verdict` event, so no reader mistakes a model's guess for an engine fact.
- Added: **`NOODLE_LOG_LLM_PAYLOADS`** (opt-in, off by default) — writes prompt + completion to a separate, gitignored `artifacts/llm/<run_id>.jsonl`, secret-redacted, screenshots omitted whole; **never** the log stream (prompts carry page text, which carries customer data).
- Tests: extends `unit_tests/test_nood_0171_logging.py` with the `mcp.tool`/`mcp.auth.deny`/`llm.call`/`locator.heal`/`rca.verdict` events (no key/secret leaks), the opt-in payload log, and a conftest guard that restores console routing after `server.main()` tests (they flip a process-global handler).

## [0.2.0a24] — 2026-07-24

Structured logging + AI-governance groundwork for the container future state (NOOD_0171, plan in `tmp/nood_0171-logging-plan.md`, phases 1–2). The engine kept stdlib `logging` — no new dependency; ~120 lines in `noodle/log.py`. Phases 3–5 (per-event `mcp.tool`/`llm.call`/`run.end` taxonomy, Dockerfile default, dashboards) are independent follow-ups.

- Added: **`NOODLE_LOG_FORMAT=json`** — one JSON object per line to **stderr** (never stdout: that's the MCP stdio protocol channel), field names following the OpenTelemetry log data model (`timestamp`/`severity_text`/`severity_number`/`body`/`attributes`) so a later OTel adoption is a transport swap, not a schema migration. Default stays `text` — the emoji console is byte-for-byte unchanged, so CI parsing, the `--json` run payload, and agent-facing summaries are untouched. This is the 12-factor-XI shape a container ships to its platform log store (Log Analytics is already wired in the Container Apps terraform).
- Added: **run correlation** — a `run_id` (16 hex) plus `workspace`/`feature`/`scenario` ride on every log record via a `contextvars` filter, so one shared multi-team server's interleaved lines slice back to a single run. `run_id` crosses the CLI→behave subprocess boundary through `NOODLE_RUN_ID` (minted by the CLI/MCP caller, adopted in `hooks.before_all`).
- Added: **`log.event(name, body, **attrs)`** — one structured event; `body` doubles as the human console line so text output is identical and only json mode gains queryable `event`+`attributes`. An un-migrated `logger.info()` still emits a valid, correlated line.
- Fixed (compliance): **env-injected secrets are now redacted.** `register_secret` was fed only by `*secrets.env` files and Key Vault, so container/K8s-injected credentials (`NOODLE_MCP_API_KEY`, `NOODLE_HTTP_PASSWORD`, model API keys) were never in the scrub set. New `register_env_secrets()` sweeps `os.environ` by key name and registers the values — called at run start (`hooks.before_all`) and server start (`mcp.server.main`).
- Added: **structured-attribute redaction** — the redact filter now also scrubs event `attributes` by value and masks any credential-named key (`api_key`, `password`, `authorization`, …), while leaving token *counts*, durations, paths and model ids intact (governance data, not secrets).
- Added: **the per-run file log (`artifacts/logs/noodle.log`) honors json mode** — it's a direct `FileHandler`, immune to behave's per-scenario stdout/stderr capture, so it's the reliable structured sink for a no-MCP / no-streaming deployment (CI archives the `artifacts/` tree). Text in default mode; JSON (correlated + redacted) under `NOODLE_LOG_FORMAT=json`.
- Added: `unit_tests/test_nood_0171_logging.py` — asserts the JSON shape, correlation context, and that a secret from either a `secrets.env` file or an injected env var leaks into none of the sinks (console, captured-warnings buffer, on-disk `log.redact` writer).

## [0.2.0a23] — 2026-07-22

The NOOD_0168 baseline session (same retail prompt, live site, HEAD engine) reached green — but spent 67 agent interactions and three shell-approval prompts doing it: 30 calls re-learning what the engine already ships (help/docs/steps hunts), then a ~28-call hand-authoring fallback after goal mode blocked on "no search box found", plus greps of the payload spill file that each needed human approval. Every fix here removes one class of those round trips; none is site-specific. Target for that prompt class: probe → author(goal)+run, ≤ 10 interactions, single approved binary.

- Fixed: **search-box election is visible-first, never `.first`-only** (NOOD_0169) — responsive headers render a hidden mobile/desktop twin DOM-earlier than the visible box, and both the probe's CSS scan and the runtime resolver judged each selector by its first match: one hidden twin rejected the whole selector (probe) or took the doomed `fill()` (runtime). Both now walk the first few matches per selector and elect the first VISIBLE editable one; the runtime also retries that scan before and after the trigger-open dance, recorded as a `visible-filter` healing event. This was the block that forced the reviewed session out of goal mode.
- Fixed: **multi-URL CLI/MCP probes act on the LAST page only** (NOOD_0169) — several URLs in one probe IS an ordered navigation contract (session priming, then the action page), and the goal path already acted only on the last; the CLI/MCP default (`act_on="each"`) ran `--search`/`--click`/`--do` on every page, so a setup page reported "no search box found" and poisoned `author_ready`.
- Added: **`probe --find "<text>"` / `probe_page(find=...)`** (NOOD_0169) — pre-cap substring filter over everything the probe collected (controls, result items, card actions; name/selector/step/caption, case/space-insensitive), each hit with its selector, suggested step, and POM line. Replaces grepping `.noodle/last_payload.json` — the reviewed session's three payload greps were exactly the shell approvals a controlled environment can't grant.
- Added: **`noodle steps` takes several keywords in one call** (NOOD_0169) — union of hits in dictionary order, unmatched keywords noted inline; the session paid ten CLI round trips for ten words.
- Added: **goal rejections ship the full vocabulary** (NOOD_0169) — `vocabulary()` generated from the exact tables `validate()` enforces (action keys + required, check keys, dismissals, anchoring notes) rides beside `example` on every invalid-goal error; the keys EXAMPLE doesn't show (`add_to`, `item_in_destination`, `expected_from`, `evidence`) cost the session an 8-call docs hunt.
- Changed: **ambiguous-lenient warnings carry a paste-ready scoped selector per candidate** (NOOD_0169) — the `verified: false` ending named the problem but kept the fix as homework; each candidate line now includes a unique-ish CSS selector (id → test-id attrs → aria-label → parent-scoped nth-of-type) and marks which one lenient mode used, so the POM pin is a paste and the re-author reaches `verified: true` in one lap.
- Added: **prompt mode — `noodle author --prompt` / `author_test(prompt=...)`** (NOOD_0169) — plain-English steps compiled into a goal by a three-pass bounded flow planner (`noodle/repl/prompt_expander.py`): clause normalization (backticked/labelled URLs, `Then url`, parenthetical `(and close all pop ups)` compounds, `Verify:` labels, screenshot-evidence suffixes split off), literal translation of complete clauses with stable ids minted in source order (`search1`, `pick1`, `add1` — no global search id), and typed-dataflow context resolution (search→result_set, pick→selected_item, add_to→mutation, item_in_destination←mutation) within a two-flow-sibling window for the steps incomplete on their own. Fully explicit prompts take the `deterministic-fast-path` with zero inference; every inference carries provenance + supporting clause ids under `prompt_expansion` (assumptions stay as prose); conflicting context — verify item vs search term, two equally compatible searches, an unrelated destination, a mutation with no producer — blocks BY NAME, never guesses. A URL in the prompt derives `app_name`/`base_url`/`feature_path`, so prompt + `--run` is one call from raw request to run + reports. The exact NOOD_0168 retail session prompt is the accepted regression fixture, its semantic plan pinned by test.
- Added: **intent-contract review before any browser** (NOOD_0169) — every translation mode passes the same pure `review_contract` gate: full clause coverage, goal schema validity, provenance on every inference (supporting clauses + a typed consumer — orphans never compile), no surface click/enter/select without a source clause, requested screenshot evidence attached to a verification check. Prompt payloads carry `translation_mode`, clause `coverage`, `inferences`, and a `planner` terminal verdict (VERIFIED / READY / EVIDENCE_MISSING / EXTERNAL_FAILURE / RUN_FAILED / NEEDS_INTERPRETATION / CONTRACT_BLOCKED) with its call-budget ledger — every path terminates, nothing loops back to the start.
- Added: **one optional model-interpretation call** (NOOD_0169) — clauses outside the grammar (and ONLY those; typed conflicts never route to a model) get one temperature-zero `ask()` translating the whole prompt into the same typed goal + per-clause coverage (`prompts.PROMPT_TO_GOAL`), gated by the same review — invalid JSON, uncovered clauses, unknown actions, or an invented click are refused with no browser launched. No `NOODLE_MODEL` → `needs_interpretation` names the unresolved clauses. A complete prompt never pays a model call.
- Added: **result readiness + extraction diagnostics** (NOOD_0169) — after the probe's search the engine polls (bounded by the existing timeout, no fixed sleeps) for a structured result item or an explicit zero-results state; a positive summary with no extractable item returns the typed `result_items_warning: positive-summary-without-items` (the live "1163 results"/zero-cards state), a generic pick refuses to bind lexically through the flat-control fallback, and `next_action` names `result_items_missing` as the ROOT gap (checked before the mutation-path cascade). Result items now record why each qualified (`repeated_structure` / `stable_href` / `post-search-diff`).
- Changed: **mutation prerequisites are semantic and same-page-proven** (NOOD_0169) — `_prove_mutation` no longer trials "the first visible non-submit button": candidates must be disclosure/variant/option-shaped (aria-expanded / aria-haspopup / disclosure naming), never landmark chrome or feedback/signup/legal/support controls; a trial that navigates off the product page is invalidated and the original URL restored (bounded at 3 trials), and accepted proofs record `url_before`/`url_after`/`revealed_selector` evidence.
- Added: **navigation health records** (NOOD_0169) — each probed page carries its HTTP status; requested setup URLs are preserved even when broken (warning attached under `evidence.navigation_health` — setup-page controls never enter the action page's vocabulary), while a broken FINAL action page blocks authoring with `fix_navigation_contract`.
- Added: **repair provenance gate on `write_feature`** (NOOD_0169) — a feature under a structured intent contract can no longer be replaced by hand-written Gherkin (the "click here" drift path): the write is refused with `next_action: fix_blocked_goal`, pointing repairs back through normalize → validate → review → probe → compile; `allow_unverified_intent=true` stays the explicit expert override.
- Changed: **always-on surfaces route around shell helpers** (NOOD_0169) — AGENTS.md template, copilot digest, playbook, and cli-reference now pin: output is payload-bounded so never pipe through `grep`/`head`/`sed`/`jq` (use `--find`), specs go inline via `--spec -` heredoc (no temp file to approve), workspace map is `noodle list` (never `find`/`ls` sweeps), and `steps` takes all keywords at once.

## [0.2.0a22] — 2026-07-22

A reviewed session failed the simplest retail prompt there is — "search for a toy, add it to cart, verify the cart" — while the correctly-shaped goal existed from the first minute. Re-running that exact prompt against the live site with the engine at HEAD exposed five universal gaps, each one verb of the flow breaking its own contract; with all five fixed, the one-call goal path (`author --spec … --run`) reaches a `verified: true` green on the real site, and the bare simple-prompt goal (no pick, no ids, no expected_from) authors clean. All fixes are domain-free: ARIA landmarks, CSS attribute anchors, visibility, and the goal's own structure — no site vocabulary anywhere.

- Added: **the goal normalizer expands the simple-prompt shape** (NOOD_0168) — `search → add_to` with no `pick` spelled out inserts the implied pick (any result of the search) and wires `item_from` to it; an `item_in_destination` check without `expected_from` binds to the sole pick. Both rewrites echo in `goal_normalized`. The reviewed session died exactly here — "add_to requires item_from" — and fell back to hand-authoring.
- Changed: **`mutation_control` binds the first visible responsive duplicate** (NOOD_0168) — a PDP renders the same-named buy control twice (buy box + sticky bar, often both visible); after a pick the landed page holds ONE item, so same-named duplicates perform the same mutation. First visible binds; a grid of many distinct instances (one per card) still blocks. A wrong bind cannot survive: the probe clicks it to prove the delta, and the destination check must still find the picked caption.
- Changed: **`search()` verifies the page reacted** (NOOD_0168) — fill + Enter used to PASS unconditionally; a swallowed submit surfaced two steps later as a bogus not-found on a results-page control (the reviewed session's manual run died there, misattributed). The step now passes only on an observable reaction — URL change, the term newly echoed in body text, or a results-sized text delta — and otherwise fails naming the real problem, bounded by `NOODLE_SETTLE_TIMEOUT`.
- Changed: **`User waits for the network to be idle` is best-effort** (NOOD_0168) — ad/analytics-heavy pages are never strictly idle, so the compiled settle step failed an otherwise-green flow at its timeout. Bounded by `NOODLE_SETTLE_TIMEOUT`, then proceeds: the wait exists to let an in-flight mutation land, and that request is done long before the bound.
- Added: **POM attribute-prefix relaxation** (NOOD_0168) — a live app suffixes state into labels (`aria-label="Cart"` → `"Cart, 1 item"`), rotting an exact attribute match at the exact moment the flow succeeds. When a defined POM css matches 0 after the full poll, `[attr="value"]` retries once as `[attr^="value"]` — same attribute, same anchor, recorded as a `pom-attr-prefix` healing event; the loud failure stays when even that matches nothing.
- Added: **landmark chrome is never a result item** (NOOD_0168) — the collector flags controls inside `nav`/`header`/`footer`/`role=navigation|banner|contentinfo`/breadcrumb, and `build_result_items` drops them as candidates, a belt over the persistence heuristic (a breadcrumb rendered WITH the results defeats "existed before the search").
- Changed: **`--do` shares a probe with `--search` correctly** (NOOD_0168) — the do-transaction ran on the START page before the search phase (the reviewed session's `--do "click Add to cart"` fired on the homepage and torched `author_ready`); with `--search` present it now runs after search/`--pick`, on the landed page, and `_do` target resolution prefers the newest snapshot over same-named twins from earlier pages.

## [0.2.0a21] — 2026-07-22

A reviewed retail session went red on an empty-destination assertion three steps after the page announced the real cause ("Out of stock at <store>") in a `role=alert` toast nothing ever read — and its authoring phase dead-ended twice on evidence that was correct but mute. Three fixes make the engine quote the app and its own evidence instead of leaving a generic dead end; all matching is standards-based (ARIA roles, live regions, probe-collected control names), nothing app-specific.

- Added: **page-announcement capture + `app-rejected-action` RCA verdict** (NOOD_0167) — after every click the engine diffs the page's announcement surfaces (`role=alert|alertdialog|status`, `aria-live`, toast/snackbar class conventions; open shadow roots included) against a pre-click baseline, remembers what the app said, and re-checks before the next click and at failure time (toasts ride a network round trip). A failing step now carries `[page-response] after clicking '<target>' the page announced: "<text>"`, and a new RCA verdict quotes it — placed above the click/locator verdicts, because "re-point the locator" is wrong advice when the app itself refused the action. Same-URL guarded so a navigation's own live regions are never misread as a response.
- Added: **the `add_to` goal blocker names what the landed page offers** (NOOD_0167) — "no proven mutation path" now ends with the landed page's control vocabulary (from the probe's own evidence, deduped, capped at 8), so the next move is a rename, not a re-probe.
- Added: **inspect's zero-candidate dead end lists the page's vocabulary** (NOOD_0167) — when no source matches the phrase, `noodle inspect` appends the page's interactive controls (accessible names, top by count, capped at 12): "nothing matches 'add to cart'" becomes "…the page calls it 'Options'".

## [0.2.0a20] — 2026-07-22

Three branches in a row fixed payload spills, and the next reviewed session still paid three HIL prompts outside `noodle` commands: a second probe to reveal the search box, `jq` slices of the probe JSON, and `curl` against both served report URLs. Each had an engine-side cause; this branch removes all three, so the only human-in-the-loop prompt a session needs is the `noodle` command itself. Pre-1.0 alpha — interfaces may still change.

- Fixed: **one probe folds the search reveal** (NOOD_0166) — `--search`/`--suggest` could already click a search trigger open, but only via CSS heuristics; a stem-named trigger those miss (a retail header icon) returned `no search box found` and cost a second probe with `--click "search"`. `_open_search_box` now falls back to the controls the probe itself collected — stem-named, non-editable, visible first, capped at 3 — with the same hidden-hitbox `dispatch_event` mechanics as `--click`.
- Added: **`author_blocking` names the reasons behind `author_ready: false` in the JSON payload** (NOOD_0166) — the text render always said why; the JSON door (the agent path) handed back a naked `false`, and the reviewed session `jq`'d the payload hunting for a reason that was never in it — then misread the budget-trim note as the blocker. The floor `budget_trimmed` note now also says it is presentation only, not an authoring verdict.
- Added: **served report URLs are engine-verified — `http_ok`** (NOOD_0166) — `_spawn_report_server` HEAD-checks every URL before handing it out (localhost, sub-second), so `run --serve` / `run_and_report` / `serve_report` payloads certify their own links and the curl lap adds nothing. Reuse is now gated on the URLs actually answering: a registry entry whose pid is alive but whose socket is dead falls through to a fresh spawn instead of returning dead links.
- Changed: **the surfaces say so** (NOOD_0166) — AGENTS.md gains one rule ("read payloads as returned (no jq); served URLs are pre-checked (no curl)"), the MCP instructions and both skill cards one clause each. Ledger: AGENTS.md 5104/5632 (headroom 528, floor 512 holds), MCP instructions 2395/2432, cards 5659 and 5822/5888.
- Chore: client-identifying keywords (retailer name, domain, program names) swept out of engine comments, unit tests, and the NOOD_0156 follow-up doc — the repo is public.

## [0.2.0a19] — 2026-07-22

The session after NOOD_0164 spilled again, and the log said why twice over. The budget was measured on the compact serialization while the CLI printed at `indent=2` — 7,556 B measured, 10,240 B on the wire — so a "bounded" payload still landed in the harness's temp file and got `jq`'d five times. And the same session spent ten `noodle docs` / `noodle steps` / `step-search` calls recovering the goal spec shape and step phrasings, for a goal, where the engine compiles every step: `noodle author --help` pointed at `author_test`, an MCP tool a CLI-driven agent cannot read. Pre-1.0 alpha — interfaces may still change.

- Fixed: **the budget now measures what gets rendered** (NOOD_0165) — `size()`/`bound()` take the `indent` the payload will print at, and `_json_out` passes `indent=2`. The `noodle list --json` door on this repo went 10,240 B → 7,133 B on the wire; before this, every CLI `--json` payload was ~35% larger than the budget that cleared it.
- Added: **a trimmed CLI payload leaves the whole thing on disk** (NOOD_0165) — `.noodle/last_payload.json`, named in `payload_note`. The agent reads that path with its own file tools instead of hunting the harness's spill file with `jq`; the spill becomes a deliberate retrieval.
- Added: **`noodle author --help` carries the goal spec** (NOOD_0165) — app_name / base_url / feature_path / the `goal` object, with the line that ends the lookup loop: the engine probes, compiles the Gherkin + POM and (with `--run`) runs it, so step phrasings and dismissal wording are never something to look up for a goal. A test pins the example against `goal.EXAMPLE` so a renamed key can't leave the help stale.
- Changed: **both skill cards say goal mode needs no step lookup** (NOOD_0165) — folded into the existing "steps must match the pattern table" bullet rather than added beside it. The copilot card's auto-retry clause goes (the RCA section already says it), keeping that card at 117 B headroom.

## [0.2.0a18] — 2026-07-22

Fourth session in a row to spill a tool payload to a temp file and pay inferences to `jq` it back — this time a probe payload, in a session driven through GitHub Copilot CLI. The three previous fixes were per-tool (`probe --json`, `noodle --help`, `list_tests`, `probe-app`); each time, the next door leaked. This one moves the bound to the boundary. Pre-1.0 alpha — interfaces may still change.

- Added: **`noodle/payload_budget.py` — one 8 KB bound for every agent-facing payload** (NOOD_0164) — `bound()` trims only what still overflows after a tool has compacted itself, cutting the largest list or string by what the payload is actually over (priced at that value's own bytes-per-element, so a 60-feature index doesn't halve to 15 and a 200 KB string doesn't collapse to one character). It never invents, and `payload_note` names what was cut plus where the rest lives. Small keys — `ready`, `blocking`, `author_ready`, verdicts, paths, URLs — are never the largest value, so they survive whole. `NOODLE_PAYLOAD_BUDGET_BYTES` raises it for a host that inlines more.
- Changed: **every MCP tool registers through `_tool()`, not `@mcp.tool()`** (NOOD_0164) — the budget is the decorator, so a new tool cannot leak a 25 KB payload by forgetting a cap. Measured before the change, with all the per-tool fixes already in: `list_tests` over this repo 15,845 B, `read_docs('manual', section='Setup guide')` 25,468 B (15 sections over 8 KB), probe compact up to 24,000 B.
- Changed: **every CLI `--json` print goes through `_json_out()`** (NOOD_0164) — the same budget on the door an agent without MCP walks through; 13 scattered `typer.echo(json.dumps(...))` calls became one helper, and a guard test fails if a fourteenth appears.
- Changed: **the probe's compact budget is the shared one, 24 KB → 8 KB** (NOOD_0164) — its cap ladder already sheds junk-ranked lists before author-critical keys, which is what makes the smaller number survivable; `compact=False` / `--full` still returns everything.
- Tests: **`unit_tests/test_nood_0164.py`** — the trimmer's behaviour (pass-through, honest note, hint, untrimmable payloads, the env knob, and a pathological 200 KB string that must terminate quickly — the first cut of the loop effectively hung), plus boundary guards: no raw `@mcp.tool()`, no second `typer.echo(json.dumps(...))`, and measured fits for `list_tests`, every `read_docs` section of every doc, and a two-page synthetic probe.

## [0.2.0a17] — 2026-07-22

A session review (25 AIC for one green test, and green only on run 2) split into two halves. The first was a compiler bug wearing an authoring-mistake costume: the goal spanned two pages, and the engine had no way to say which page a check belonged to — so a landing-page string was asserted against the page the flow ended on, and both text checks shared one POM key. The second was agent overhead — a repo-wide search, re-read instruction files, speculative opens, and authoring from scratch beside a workspace that already had a working test for the flow. Compilation fixes the first; the second is a routing rule. Pre-1.0 alpha — interfaces may still change.

- Added: **`after: start` — the landing-page check anchor** (NOOD_0163) — NOOD_0158 made an unanchored check observe the END state, which is right for an outcome but left a check on the page the flow *starts* on with nowhere to go: it compiled behind the actions and asserted against the last page (one red run, then a hand-patch). `after: start` emits the check before the first action, so a goal spanning pages binds every check to the page it was observed on. `start` is now reserved as an action id, and an unknown anchor's error names it.
- Fixed: **two `any_of` checks no longer share one locator** (NOOD_0163) — every unnamed `any_of` check compiled to the POM key `result titles`, and `pom_entries.setdefault` keeps the FIRST selector, so the second check silently re-used the first check's locator: the probe proved both texts, the run asserted one of them twice. Distinct selectors now get distinct keys (`result titles`, `result titles 2`, …); identical ones still share, and an explicit `name` still wins.
- Docs: **playbook §1 "Reuse before you author"** (NOOD_0163) — one glob (`list_tests(query=<app>)`) classifies the workspace, and the two routes it picks between: an existing green same-app feature is a copy + `{env:}` retarget + run (no probe, no authoring), a fresh workspace authors from a `goal` with every check anchored to its page. Plus the search hygiene the review spent its tokens on: scope searches to the app package and skip `artifacts/`/`archives/`/`report/`/`.noodle/`, don't re-read the AGENTS.md and skill card already in context, glob before opening a path instead of batching the two. §2 gained the `after` page-binding contract.
- Fixed: **the instruction-budget ledger measured colour, so CI went red on main** (NOOD_0163) — GitHub Actions renders `noodle probe --help` in colour and a laptop pipes it plain: 396 ANSI escapes, ~2 KB, enough for `probe --help` to measure 8013 B in CI against a 6144 B cap that passed locally at 5995 B (`test_nood_0159`/`test_nood_0162`, run 29949521181). `_cli_help` now strips the escapes — colour is paint, not content, and the agent reads the same bytes either way. Width was already pinned; the stripped size is stable across typer/rich versions (5910–5995 B measured), so the caps stand as they were.
- Changed: **AGENTS.md and both skill cards route to it** (NOOD_0163) — the scaffolded AGENTS.md gains "reuse first: `list_tests(query=<app>)`", the per-page anchor rule, and one hygiene line (don't re-read this file; scope every search); the skill cards gain the anchor clause only. Ledger caps unchanged — AGENTS.md 5026/5632, cards 5552 and 5758/5888.

## [0.2.0a16] — 2026-07-22

NOOD_0161 swept for payload spills, fixed the two it could prove, and left the rest measured in a plan: an agent-facing surface whose default hands back everything. This lands that plan. Same two fix shapes as before — *compact by default with a `--full` escape*, or *index first, detail on request* — plus the deletion NOOD_0161's report-hosting fix left behind. Pre-1.0 alpha — interfaces may still change.

- Changed: **`list_tests` is an index, and gained a `query`** (NOOD_0162) — it was the only MCP tool with no bound of any kind: 25,424 B in this repo, every scenario name of every feature, no filter. The unfiltered call now returns `scenario_count` per feature instead of the names (15.8 KB here, −38%; paths and tags are what a caller routes on), and `query` — substring over path / feature / scenario / tag — returns the names for the features that match. **Shape change:** the return is now `{tests: [...], note: ...}`, not a bare list; the note is what teaches the param. `noodle list --json` follows, with a matching `--query`.
- Changed: **`probe-app --json` is capped, and MCP `probe_app` defaults to compact** (NOOD_0162) — the native probe's JSON door was `json.dumps(whole_snapshot)`: every interactive node of an accessibility tree that is hundreds of nodes on a real screen. The node list now caps at 25, visible first, with a `truncated` note; `author_ready`, `coverage`, warnings and every kept node's POM entry pass through whole. `--full` (CLI) / `compact=false` (MCP) opts back into everything, so the two probes stop disagreeing. Deliberately an honest cap, not a port of the web probe's 130-line ranking — a cleverer order is unverifiable without a device.
- Changed: **`probe --help` 12.5 → 6.0 KB, `run --help` 8.4 → 4.5 KB** (NOOD_0162) — the bulk was option help carrying its NOOD_XXXX rationale (`--do` alone was ~600 chars) on the two commands an agent actually reads. Router, not diet: the rationale **moved** into `docs/cli-reference.md` (which gained rows for `--suggest`, `--pick`, `--follow`, `--expect`, `--open-native`, `--max-reveal-depth`, `--discover`, `--preflight`, `--serve`, `--json`), each option help keeps the line that says what the flag *does*, and both docstrings end with `Full flag reference: noodle docs cli-reference`. The `--do` grammar, `--pick` semantics and `--parallel` extras requirement stay inline — they are what the flag *is*.
- Added: **`noodle probe --help` (6144 B) and `noodle run --help` (5120 B) join the instruction budget ledger** (NOOD_0162) — an agent pulls a whole help screen in one call, so they are always-on surfaces; unpinned, they drift straight back. Measured at a fixed 80 columns because rich pads every line to the terminal width — note that ~4 KB of each screen is frame, so these caps sit near the floor for a command with this many flags.
- Removed: **`builder.start_report_server` and `builder.stop_report_servers`** (NOOD_0162) — the in-process daemon-thread server had no caller left in `noodle/` after NOOD_0161 made every hosting path a detached child; only its own tests used it. `core.stop_report_servers` drops the `stopped_ports` key with it, rather than returning `[]` forever — an empty list reads as "nothing was running" when the real answer is in `detached`.

## [0.2.0a15] — 2026-07-22

Session review follow-up (NOOD_0161): a retail search flow that should cost 12–17 AIC cost 28.955, and most of the overrun was schema recovery — `goal` passed as a string, then as `{}`, then 36 KB of CLI help, a failed `rg`, and repeated docs queries before authoring even started. One copy-pasteable example, carried where the decision is made and returned with every rejection, removes that whole branch.

The same review's other half: the agent-facing surfaces that hand back *everything* by default. A driving agent probed a retail homepage with `noodle probe --json`, its harness spilled the raw payload to a temp file, and it then ran `jq` to slice out the author evidence `compact_payload()` already returns whole. The MCP tool has defaulted to compact since NOOD_0117; the CLI's JSON — the door an agent without MCP walks through — defaulted to the dump. Two neighbours had the same shape. Pre-1.0 alpha — interfaces may still change.

- Added: **`goal.EXAMPLE` — the minimal valid goal object** (NOOD_0161) — returned as `example` alongside the errors on every `invalid goal` rejection (no browser launches to get it) and inlined in both skill cards, so the shape never has to be rediscovered. Its values are `<angle-bracket>` placeholders and a test pins them that way: the example teaches the shape, never a site, product, or vocabulary.
- Fixed: **a non-object `goal` now names what arrived** (NOOD_0161) — `goal must be an object, got str — pass the mapping itself, not a YAML/JSON string`, replacing the bare "must be an object". The `author_test` docstring said `goal` was "a YAML STRING like pom_content is"; it is a dict, and that line is what taught the reviewed session to send one.
- Changed: **skill-card ceiling 5376 → 5888 bytes** (NOOD_0161, ledger) — +266 for the goal example on each card, +246 restoring headroom on the copilot card, which sat at 6 bytes. The example cannot be a doc section: the round trip to read that section *is* the cost being removed (the §7 acceptance rule). Cards now 5430/5636.
- Fixed: **hosted report URLs stop dying, and stop moving** (NOOD_0161) — `serve_report` (so `run_and_report(serve_reports=True)`, the workflow's last step) served from a daemon **thread inside the calling process** — an agent's MCP server — on `port=0`. The link died whenever that server restarted, and every run minted a different one, so users kept clicking "this site can't be reached" and getting a fresh URL to re-copy. It now spawns the same detached child `noodle run --serve` has used since NOOD_0134, and a live server for the same reports root is **reused** rather than duplicated: the URL survives the run, the session and the MCP server, and stays the same run after run. `.noodle/report_servers.json` entries carry the served root + host to make that match (old bare-pid entries still read); `stop_report_server` now stops the detached children too, since an in-process-only stop left them running and unreachable from MCP.
- Changed: **`noodle probe --json` returns the compact author-evidence payload** (NOOD_0161) — the same one MCP `probe_page` returns, bounded to 24 KB by NOOD_0158's whole-payload budget. `--full` opts back into the raw dump (every selector, next-pages, hundreds of KB) for the case a capped list actually hid something; the compact payload's `truncated` note says when that happened. `docs/cli-reference.md` sent agents straight at the raw door and no longer does.
- Changed: **`noodle --help` is a scan list again, 14.7 → 5.3 KB** (NOOD_0161) — Typer rendered each command's FULL docstring into the command list, so the top-level help alone was 14.7 KB and reading it plus two command helps was the session's 36 KB. Each command now derives a `short_help` (first sentence, ticket prefix stripped, 110 chars) unless it sets one; `noodle <cmd> --help` is unchanged and still carries every word. Generalizes the fix NOOD_0156 hand-applied to `update`.
- Changed: **bare `noodle steps` prints the section index, 20 → 2.3 KB** (NOOD_0161) — 359 steps for a caller who wanted one. Same shape as `noodle docs` on a large doc: index first (60 sections + counts), `noodle steps <keyword>` for the section, `noodle docs steps_dictionary` for everything. The keyword filter already existed; nothing told the caller to use it, and the post-hit footer pointed at the 20 KB dump as "full reference".
- Docs: **playbook §2 "The goal object"** (NOOD_0161) — the canonical example plus the full key list, so `read_docs`/`noodle docs` has the substance the cards only sample.

## [0.2.0a14] — 2026-07-22

The follow-up NOOD_0159 named: empty AGENTS.md into the bookshelf (NOOD_0160). The scaffolded AGENTS.md sat at 4 bytes of headroom under its 5632-byte ceiling; this is the first content move under the router rule (llm-performance §8). Pre-1.0 alpha — interfaces may still change.

- Changed: **AGENTS.md is now a router, 5628 → 4589 bytes** (NOOD_0160) — the probe flag catalog, author spec-key list, result-pick binding, evidence-screenshot marker, and the failure taxonomy were straight duplication of playbook content and now live only there, behind pointers. Every guard-pinned phrase and the probe→author→execute contract stayed; all 279 content-guard tests pass unmodified. Headroom: 4 → 1043 bytes.
- Added: **`noodle docs` CLI command** (NOOD_0160) — the CLI form of the MCP `read_docs` tool (index with byte costs, `--section`, `--query`), so an MCP-blocked agent still reaches the content the surfaces only point at. Without it, "move substance to the docs" would have stranded CLI-only agents.
- Added: **a 512-byte headroom floor on AGENTS.md** (NOOD_0160, `test_nood_0160.py`) — the ceiling stops bloat, the floor stops the slow creep back to zero: when headroom drops under 512 bytes the test names the fix (move content to the playbook) instead of letting the next ticket spend the last byte.

## [0.2.0a13] — 2026-07-22

Permanent fix for the byte-ceiling woes (NOOD_0159). Six always-on instruction surfaces sat at near-zero headroom (AGENTS.md: 4 bytes), their ceilings pinned by asserts scattered across seven per-ticket test files with duplicate pins at different values — every new piece of guidance was a byte-fight. Reviewed GoogleChrome/modern-web-guidance as prior art: its always-on payload is a ~230-token router card ("search first, retrieve on demand") with all substance in retrievable per-topic guides that surface their token cost. Adopted the architecture, not the machinery (npx package, TF.js embeddings, telemetry — overkill for a docs set `read_docs(query=…)` already greps). Pre-1.0 alpha — interfaces may still change.

- Added: **the instruction budget ledger** (NOOD_0159) — `noodle/instruction_budget.py` holds every always-on surface's byte ceiling in one `CEILINGS` dict, with the per-ticket accounting history moved into its docstring. `ledger()`/`format_ledger()` report used/cap/headroom per surface; one test (`test_nood_0159.py`) enforces it and prints the whole table on failure. The scattered pins in test files 0117/0126/0127/0128/0130/0131/0147 are retired in place with pointers.
- Changed: **bytes are the only ceiling unit** (NOOD_0159) — the line-count ceilings (70/96/120/165) are retired; tokens track bytes, not lines, and two units meant two fights per edit. The `.github/copilot-instructions.md` digest — the one always-on surface with no cap — is now pinned (7168 bytes).
- Changed: **`read_docs` surfaces retrieval cost** (NOOD_0159) — the no-arg doc index now carries `bytes` and `sections` per doc (Google's `tokenCount` idea), and every `query=` hit names its `section`, so a hit is retrievable in one follow-up call with no extra index round trip.
- Docs: **llm-performance §8 — "surfaces route, docs carry"** (NOOD_0159) — the router rule: a surface earns bytes only for the workflow contract, triggers, and pointers; substance lands as a `docs/` section (~free until read); raising a cap requires a ledger edit plus a CHANGELOG byte-delta note. The shingle anti-duplication guard stays: trimmed text moves to a doc, never onto a second surface.

## [0.2.0a12] — 2026-07-22

Why the MCP path cost 2× the CLI on the same 4-line prompt (NOOD_0158). Authoring one search-suggestion test against a live retail site took 6m38s over MCP against ~3m on the CLI. Protocol overhead was not the cause — stdio JSON-RPC is milliseconds, and the engine did ~45s of real browser work. The other five minutes were three engine behaviours, each costing a round trip the CLI never pays. Pre-1.0 alpha — interfaces may still change.

- Fixed: **an unanchored goal check now observes the END state** (NOOD_0158) — `compile_goal` emitted every check whose `after` was unset *before* the action loop, so a goal reading "search for X, then see product Y" compiled to a feature asserting Y on the landing page. The author's only tell was a red run (`not found. URL: <base_url>`) and a re-author with `after` added to each check — a full browser lap to learn a placement rule the goal schema never surfaced. Checks are what prove the goal worked; nothing to prove exists until the actions have run, so unanchored checks are emitted last. Placement *before* an action stays expressible — that is what `after: <id>` has always been for, and anchored checks are untouched.
- Fixed: **the compact probe payload is bounded as a whole, not per list** (NOOD_0158) — the cap governed each list and never their sum, so one `--suggest`/`--follow` probe of a retail homepage returned **82 KB**: a homepage block plus a full second page block for the search results, each with its own capped `needs_pom` (~300 B per control dict), `suggested_steps`, `tile_captions` and rebuilt `pom_yaml`. That exceeded the MCP caller's context cap, spilling the payload to a file and costing **13 recovery greps** to read back — the same failure NOOD_0156 fixed for one list and not for the total. `compact_payload` now walks a cap ladder (40 → 4) until the serialized payload fits `COMPACT_BUDGET_BYTES` (24 KB), and says which cap it settled on in `budget_trimmed`. The ranking work from NOOD_0156 means the ladder sheds chrome first; the author-critical passthroughs (`skeleton`, `suggest`, `expect`, `result_items`, `results_summary`, `author_ready`, `do_failed`, `warnings`) are not cap-governed and survive to the floor.
- Fixed: **`read_docs(name=…)` no longer returns a 57 KB file whole** (NOOD_0158) — `agent-playbook.md` spilled the caller's context the same way, to use one ~4 KB section of it. A doc over `DOC_WHOLE_MAX_BYTES` (8 KB) now returns its `## ` section index with per-section byte counts; `read_docs(name=…, section='<title or #>')` returns one section, matching by 1-based number, exact title, or substring so a loosely-quoted heading doesn't cost a retry. The preamble before the first heading is section 1, so nothing is unreachable, and docs under the threshold still come back whole.



Evidence screenshots that actually show the thing (NOOD_0157). Authoring against a live retail site surfaced four ways a green run still reported `verified: false` or shipped a useless shot — every one of them costing a pointless inspect/re-author/re-run lap. Pre-1.0 alpha — interfaces may still change.

- Fixed: **evidence shots center their target** (NOOD_0157) — `scroll_into_view_if_needed` scrolls the minimum and its actionability wait can time out on a busy results page, silently skipping the scroll, so the asserted card was clipped at the viewport edge. `capture()` now JS-centers the element (`scrollIntoView` `block: center`, no actionability gate), records `element_in_view` in the evidence meta, and the run summary flags a green run unverified when the element isn't inside the captured viewport.
- Fixed: **existence assertions accept any visible match** (NOOD_0157) — "User should see X" on a page rendering X twice (grid *and* list product card) raised the ambiguous-locator warning and flipped `verified` to false. Existence assertions now resolve with `any_match=True`: several *visible* matches take the first with no warning (`source: any-visible`), since any one of them proves the claim. Action targets (click/fill) keep strict ambiguity handling.
- Fixed: **evidence for elementless final steps** (NOOD_0157) — when the last step resolved no element (a wait, a popup sweep, an API teardown) the shot was unfocused and `valid: false` sank the whole run's verification. `capture()` now refocuses on the scenario's last matched element — only after re-verifying the page never navigated (URL recorded at match time) and the element is still visible — boxes it, flags `refocused: true`, and hooks count refocused evidence as valid. A ghost element or changed URL keeps the no-box rule: never outline what the page can't prove.
- Fixed: **evidence for page-killing final steps** (NOOD_0157) — a tail step like "closes the current tab" left nothing to screenshot, so `NOODLE_EVIDENCE=last` silently produced none. `before_scenario` now aims `last` mode at the newest step that can still produce pixels (pattern-table lookup only, never the LLM fallback); the tab close is skipped and the step before it ships the evidence. Mid-scenario shots (the `( take a screenshot )` marker, `@evidence`, `NOODLE_EVIDENCE=all`) are unchanged.
- Fixed: **24 runnable step types were invisible to the LLM fallback** (NOOD_0157) — NOOD_0152 added its waits, mouse primitives, orientation swap and scoped assert/fill steps to the runner's dispatch but never mirrored them into `step_resolver.VALID_TYPES`. Any step the pattern tables didn't match and the LLM resolved to one of those 24 (`wait_response`, `wait_count`, `mouse_drag`, `set_slider`, `assert_number_between`, `landscape`, …) died with "LLM returned an unknown action type" even though the runner could execute it; `step_suggestion_engine` likewise never offered them, since it builds its classifier prompt from the same frozenset. The `test_valid_types_mirrors_the_runner_dispatch` drift guard had been failing since 0152 — the pipeline runs it, but nothing gates the GitHub merge button on that run.
- Added: **`.github/workflows/tests.yml`** (NOOD_0157) — runs `ruff check .` and `python -m pytest unit_tests -q` on every pull request, the same two commands `azure-pipelines.yml` gates its shard matrix with. Closes the gap that let the drift guard above stay red across four merges. Mark the check required in branch protection for it to actually block.

## [0.2.0a10] — 2026-07-21

One command after a pull (NOOD_0156). Testers could not tell whether the `noodle` on their PATH matched the branch they had just checked out: an editable install keeps the *code* current but not its dependencies or recorded version, a non-editable copy silently keeps running months-old code, and a venv install plus a system install give two different answers with no visible sign of which one ran. Pre-1.0 alpha — interfaces may still change.

- Added: **`noodle update`** (NOOD_0156) — the one step after `git pull` / `git checkout <branch>`. Re-links the running build to its engine checkout by running exactly the command `noodle doctor` recommends, in the clone, against `sys.executable` — the interpreter that imported *this* noodle, so it repairs the environment whose `noodle` you invoked (venv or system) without choosing one for you. `install_check.clone_root()` finds the checkout from the editable link target, falling back to an engine root at or above the cwd for a non-editable copy. Single-step (`uv tool install --force` / pip's own replace), so a failed resolve leaves the working install in place. `--dry-run` prints the command and the clone and stops. Never runs git.
- Added: **version-sync warning on every surface** (NOOD_0156) — `install_check.warn_if_stale` (already on `noodle run` and `noodle init`) and a new `install.version-sync` doctor check now flag an *editable* install whose recorded version lags the checkout's `pyproject.toml`: the visible proxy for "this install predates this checkout, so its dependencies may be stale too". Remediation everywhere is `noodle update`.
- Fixed: **editable uv tool installs are detected from the interpreter** (NOOD_0156) — `reinstall_cmd()` tested `package_dir()` for `.../uv/tools/...`, which only holds for a *non-editable* uv tool install; the editable one docs/llm-install.md prescribes leaves the package in the clone, so the check fell through to the pip branch and uv's pip-less venv answered `No module named pip`. `sys.executable` survives both cases and now decides. `reinstall_cmd()` is derived from `reinstall_argv()` so the command doctor prints can't drift from the one `noodle update` runs — that drift is what hid this. A pip-less non-uv-tool venv (a `uv venv` project env) falls back to `uv pip install --python <sys.executable>`, targeting the same environment rather than uv's default.
- Added: **`python -m noodle`** (NOOD_0156) — the same CLI with no console-script shim in the picture, so `noodle update` can replace its own launcher on Windows (which holds the running `.exe` open). The fix-failure message points there.
- Changed: **versioning contract** (NOOD_0156) — every branch that changes engine code bumps `[project] version` in `pyproject.toml` and adds the matching `CHANGELOG.md` section, so `noodle --version` distinguishes builds and the mismatch warning can fire at all. Documented in CLAUDE.md and `.github/copilot-instructions.md`; a unit test asserts the two files agree.

## [0.2.0a9] — 2026-07-21

Context and intent fidelity (NOOD_0156, plan wave 2): the reviewed live-retail session drifted in two material ways — an invented `Choose options` step with no probe provenance, and a cart-count assertion standing in for item identity — while 891 observed search results collapsed to zero product controls and the whole run cost 72.8 driving-agent AIC. This release fixes the broken boundary around the existing deterministic core: the intent contract preserves every explicit prompt requirement, the probe returns real scoped result items, requested mutations resolve only through evidence-proven paths, and a blocked goal can no longer silently become a guessed manual run. Universal by design — no site-specific selectors, no URL allowlists, any language. Pre-1.0 alpha — interfaces may still change.

- Added: **ordered navigation contract** (NOOD_0156) — goal key `navigation: [url, …]` preserves EVERY requested URL in order. The engine probes them in one browser (state carries; interactive phases run only on the final URL — `probe(act_on="last")`), stores each URL in the app environments.yaml under a derived key (`goal.navigation_env`, universal path-stem naming), and compiles one `Given User is on "{env:…}"` per URL — no literal URL in the feature. A URL that fails to load blocks authoring (`fix_navigation_contract`) before any compile.
- Added: **structured search-result evidence** (NOOD_0156) — `probe.build_result_items` builds `search.result_items` (`{caption, selector, href, actions: [{name, selector}]}`) from the RAW document-ordered collection BEFORE any selector dedup, inferring repeated container structure (≥ 2 captioned links sharing a class signature with distinct hrefs — the universal card shape) instead of vendor selectors. Unique anchor `href` beats a repeated class selector; repeated selectors stay addressable per instance via `:nth-match`; buttons between one caption link and the next attach to that card as scoped actions; global chrome/feedback controls stay out of items but remain in the ordinary control list. On a cross-URL search landing the FULL page is snapshotted — the old previous-page selector diff dropped real result controls whose shared retail classes had "already been seen" (the 891-results-zero-products collapse).
- Changed: **result binding is structural, not lexical** (NOOD_0156) — `goal.bind_result` consumes `result_items` when present: membership in the search-result region IS the provenance, so a valid result (a branded doll, game, or truck) binds even when its caption never repeats the generic query word. `pick` gains optional `from` (ties the selection to the search's result set) and `strategy: first_actionable` (DOM order, preferring an item with a proven card action). The legacy flat-control path keeps the lexical match — without region structure it is the only provenance available.
- Added: **semantic `add_to` mutation lowering** (NOOD_0156) — goal action `{do: add_to, item_from: <pick id>, destination}` expresses the user's intent; the ENGINE lowers it to exact observed controls: the landed-page mutation control (`goal.mutation_control` — one rule shared with the probe, requiring the destination named PLUS more, so a bare "cart" opener can never pose as the mutation; repeated per-card instances block), or a probe-PROVEN prerequisite chain. `probe --mutate`/`_prove_mutation` proves (never performs) the path: when the mutation control is absent it trials AT MOST ONE exact observed, non-submit, non-mutating control and accepts it only when the requested mutation control appears in the before/after delta — recorded as `mutation_path` with `required_by: mutation:add_to` provenance in `intent_summary`. No proven chain → block (`mutation_path_missing`); a label like `Choose options` can never compile on a guess. An assertion-free `add_to` synthesizes the identity postcondition (`item_in_destination` on the bound caption — never a count).
- Added: **honest intent verification split + intent trace** (NOOD_0156) — `author_test` payloads now separate `ready` (syntax/static), `goal_verified` (every goal action/check compiled with probe/compiler provenance), and a stricter `intent_verified` (every intent-contract requirement — navigation, dismissals, actions, identity checks, screenshot evidence — traces to a compiled step or provenance-backed prerequisite). `goal.intent_trace` returns the compact requirement → goal node → evidence trace (stable IDs and short references only; raw evidence stays in `artifacts/probe_goal.json`).
- Added: **manual-fallback gate** (NOOD_0156) — goal-mode authoring records the intent contract per feature and app in the persistent agent state; once one exists, `author_test(feature_content=…, run_after_author=true)` REFUSES the automatic run (`next_action: fix_blocked_goal`; files still written) — the exact drift where a blocked goal silently became a hand-authored `Choose options` run. `allow_unverified_intent=true` (core/MCP/CLI spec key) is the explicit expert override; agent-facing docs forbid setting it autonomously.
- Added: **typed `next_action` blockers** (NOOD_0156) — every blocked goal payload carries ONE machine-actionable repair code (`fix_navigation_contract` | `result_items_missing` | `mutation_path_missing` | `destination_missing` | `external_app_failure` | `fix_blocked_goal` | `fix_goal_request`), so the driving agent repairs the named gap instead of choosing an exploration strategy from prose — the 72.8-AIC session's probe-and-grep loop.
- Added: **AIC as an architecture acceptance criterion** (NOOD_0156) — docs/llm-performance.md §7: ≤ 17 AIC target / ≤ 25 hard ceiling for a simple flow on the pinned Codex 5.3 benchmark, ≤ 3 driving-agent inferences, 1 (max 2) Noodle calls, ≤ 2 browser launches, 0 engine-side LLM/vision calls on green, 0 repair runs on green, and the deterministic CI proxies (call/launch counts, payload byte ceilings, raw evidence in artifacts never inline).
- Added: regression tests in `unit_tests/test_nood_0156.py` (browser-free): repeated-class-selector survival and card-scoped `:nth-match` actions, unique-href selectors, structural binding without term-in-caption, named-target filtering, singleton-diff fallback, ordered dual-navigation compilation with `{env:}`-only URLs, dropped-URL blocking, the full add-to-cart contract (one observation click, identity assertion with the screenshot marker, no count), the `Choose options` replay (blocks, typed `next_action`, never compiles), prerequisite-reveal compilation with `mutation:add_to` provenance, `mutation_control` strictness, intent-trace coverage, the manual-fallback gate with explicit override, and compact-payload/artifact boundaries.

## [0.2.0a8] — 2026-07-21

Intent fidelity for simple flows (NOOD_0156 session-review fix): a generic search → select → mutate → verify request now compiles from the structured goal path with every extra step provenance-backed — the review's `buy online` guess, the `Cart (1)` assertion weakening, and the standalone screenshot step each become structurally impossible. Universal by design: no domain vocabulary, no site-specific code, any language. Pre-1.0 alpha — interfaces may still change.

- Added: **evidence-bound result selection** (NOOD_0156) — goal action `{do: pick}` (after a search) binds "any matching result" to ONE concrete probe-observed result caption via pure `goal.bind_result` (term/phrase match, unique stable caption + selector required; ambiguity blocks, never guesses a non-item control). The probe clicks that bound result (read-only navigation, `probe --pick` / `probe(pick=…)`) and snapshots the landed page under `search.picked`, so later requested actions resolve against real landed-page controls — the landed page's single "Add to cart" wins over the results page's repeated per-card twins (scoped resolution, `evidence.resolved_controls` carries the exact control into the compiled POM). A binding is a bound target, not a new intent: the same caption feeds the destination assertion.
- Added: **item-identity checks + screenshot evidence on the verification step** (NOOD_0156) — check kind `item_in_destination` (`expected_from: <pick id>`, destination = any collection the flow moves items into; `''` = current view) compiles to an identity assertion on the bound caption; a bare count can never satisfy it (`expected_from` on count/see checks is a validation error). A named destination compiles ONE observation click on the probed destination control — labelled `observation`, never user intent — and blocks when unprobed. `evidence: screenshot` (any check kind) compiles the existing NOOD_0153 `( take a screenshot )` marker onto the verification `Then` — no second screenshot implementation, no standalone screenshot step. Item checks are always runtime-asserted (the probe never mutates state).
- Added: **intent provenance + honest `intent_verified`** (NOOD_0156) — `goal.intent_summary` returns the three buckets the compiled test is built from (`requested_actions` verbatim, `bound_targets` with probe evidence, `required_prerequisites` each with `required_by` + `evidence`); `author_test` payloads carry it plus `source: goal|manual` and `intent_verified` — true only for an unblocked structured goal; manual `feature_content` is ALWAYS `intent_verified: false` (its `ready: true` is syntax/static readiness — the engine never received the intent, and no semantic analyzer for arbitrary Gherkin is attempted).
- Added: **mutation-aware RCA** (NOOD_0156) — the capture hooks now also record mutation-shaped requests (`method + URL` for non-GET) and non-success responses (status ≥ 400) into the per-scenario network JSON; pure `rca_report.mutation_verdict` correlates a failed assertion with first-party mutation traffic and distinguishes three stories: request aborted at the network layer (new high-confidence `mutation-failed` category), server refused it (HTTP status named), or request succeeded but the asserted state didn't update. Third-party/analytics failures and GETs are ignored; endpoint detail is redacted to method + path (no host, query, payload, cookies, headers). Wired into `collect()` so it outranks only the generic assertion-mismatch verdicts — engine-stamped verdicts (navigation-mismatch, blocked-by-overlay, …) and human quirks keep priority. The live-site aborted cart-add is now named from the run's own capture, zero extra probes/screenshots.
- Added: **artifact-derived diagnostics** (NOOD_0156) — `diagnostics.run_attempts` counts attempts from the engine's persisted `rca-history.jsonl` (trailing dev-session cluster, `NOODLE_DIAG_SESSION_GAP_MIN` window) instead of trusting agent memory: the front matter records `attempts` + `attempts_source: run-history`, keeps a disagreeing agent count as `attempts_reported_by_agent`, and preserves the per-run `failure_sequence`. `agent_cost` is now either a measured value or the literal `unreported` — `n/a`-style placeholders normalize to `unreported` instead of hiding a real budget failure.
- Added: **version single-sourcing + mismatch detection** (NOOD_0156) — the version lives in ONE source place (`pyproject.toml [project] version`); new `install_check.source_version()`/`version_report()` compare it with the installed dist-info metadata, `noodle --version` prints a ⚠️ line naming the cure (`pip install -e .` from the clone) when they disagree, and diagnostics record `noodle_source_version` + `version_mismatch`. docs/manual.md Troubleshooting gains the "old number after git pull" entry: an editable install keeps code current but the recorded version only refreshes on reinstall.
- Changed: **goal matching is language-universal** (NOOD_0156) — goal-side normalization moved from `[^a-z0-9]` (which erased every non-ASCII caption to `""`) to Unicode `[\W_]` + `casefold()`, so binding and control matching work for any script, regression-pinned with non-Latin captions.
- Changed: agent surfaces carry the hot-path rule inside the existing byte ceilings (NOOD_0156) — workspace AGENTS.md, both noodle skill cards, MCP instructions/docstrings, playbook §5–6: new single-flow test = structured goal + run (manual `feature_content` only after a named blocker, syntax-only readiness); one probe + one run is the normal budget; no guessed repair action (no provenance = no step); screenshot verification via the evidence marker, never a standalone step; after a red mutation read the compact RCA's network correlation before changing steps. Wording universalized — transactional flows in any language/domain, no retail vocabulary.
- Added: regression tests in `unit_tests/test_nood_0156.py` (browser-free): binding/uniqueness/language-universality, bound-caption reuse, provenance blocks for unprobed extras and destinations, observation-navigation gating, marker-on-verification compilation, `intent_verified` for manual vs goal authoring, the mutation-verdict matrix (aborted / non-success / succeeded-but-stale / analytics-ignored / redaction / priority), and run-history-derived diagnostics (6 recorded runs beat a remembered 4; `unreported` cost; version-mismatch report).

## [0.2.0a7] — 2026-07-21

False-positive mitigation (NOOD_0156): a scenario once reported 8 passing steps against a provably empty cart — an unscoped "Add to cart" and a "should see 'Added to cart'" assertion both self-healed onto `data-testid="header-cart"`, and the green exit code hid both substitutions. Three independent engine gates now each make that false pass impossible, plus authoring-side postcondition synthesis. Pre-1.0 alpha — interfaces may still change.

- Fixed: **literal assertions stay literal** (NOOD_0156, Gate 1) — `assert_visible`/`_find_probe_visible` resolve with a new `allow_dom_scan=False` switch threaded through `locator.find`/`_find`/`_poll_strategies`, disabling BOTH the in-poll DOM-attribute re-scans and the self-heal chain's DOM-scan tier for visibility assertions. "should see 'Added to cart'" can now only pass on visible text or an exact accessible caption; explicit `{pom:...}` assertions keep their author-pinned selector (resolved before any scan).
- Fixed: **low-confidence action healing fails** (NOOD_0156, Gate 2) — `dom_scan._score` token coverage tightened from half-coverage to near-total: a two-token phrase must match both meaningful tokens, longer phrases may miss at most one. `Add to cart` ≠ `header-cart` (one shared token); the NOOD_0089 `server dev-panel` → `id="dev-panel"` use case is regression-pinned and survives. An unresolvable state-changing target now fails loudly instead of degrading into a loosely related navigation control — POM or an exact probed accessible name is the recovery path.
- Added: **passing is not automatically verified** (NOOD_0156, Gate 3) — run results (CLI `--json`, `last_run.json`, `run_test`/`run_and_report`/`get_last_result` payloads, quiet-run summary) now carry `verified`, `unverified_reasons`, `warnings`, `healing_events`, and `evidence`. Per-step resolution provenance flows from `locator.find` (new `last_match_source()`, provenance set at every resolution tier) through a healing-event snapshot in `runner.execute_step` into each step's Allure result (`statusDetails.healing`), and evidence screenshots record source/selector/text-snippet/URL plus a `valid` flag (false when the shot's match came from fuzzy healing or wasn't freshly resolved). `verified` is false whenever any step of a green run healed via a fuzzy tier (`dom-scan`, `partial-text`, `vision-llm`, `ocr-coordinate` — `healing.FUZZY_STRATEGIES`), passed via lenient ambiguous-locator `.first`, or shipped invalid evidence; confident tiers (scroll, visible-filter, POM disambiguation, auth synonyms) report but don't unverify. Exit codes are unchanged (compat); the agent contract is `failed == 0` AND `verified: true`. `rca_compact` now includes `[passed-with-healing]` lines (new `rca_report.collect_healing`) and rides green-but-unverified payloads too.
- Added: **automatic postcondition synthesis + entity-bound authoring** (NOOD_0156) — pure `goal.infer_postcondition(goal, evidence)`: a goal with actions but no checks gains an explicit generated `Then` anchored after the last meaningful action, derived ONLY from probe-observed evidence (search → results-count ≥ 1 off the captured summary; enter/select → new `field` check kind compiling to `the "<target>" field should contain "<value>"`, always runtime-asserted; suggest → landed canonical option; reveal click → probe-observed revealed heading) and emitted into the `.feature` — never a hidden runtime check; the author payload lists it under `generated_checks`. No derivable postcondition → the goal BLOCKS with suggested checks (save/submit clicks without observed outcome can never compile assertion-free); explicit `allow_no_assertion: true` opts out. User-supplied checks are never replaced or broadened. Goal evidence additionally blocks on **zero search results** (missing evidence never becomes a guess) and on an **unscoped repeated control** — a click/enter/select target whose exact name matches several distinct probed selectors (one "Add to cart" per product card) must be scoped instead of acting on whichever instance resolves first.
- Changed: agent surfaces harden the workflow (NOOD_0156) — scaffolded workspace AGENTS.md, MCP connect-time instructions, and playbook (§5 "Green is not automatically verified", §6, checklist): `author_ready: false`/`ready: false` are STOPs (never hand-author `feature_content` around a failed probe or blocked goal), unfamiliar shopping/form flows probe the full flow with `--do`, assertion text absent from probe evidence is never invented (durable state over transient toasts), screenshots are opened and read before claiming visual verification, and success is reported only on `failed == 0` AND `verified: true` — a healed/warned green is an anomaly for the session diagnostic. Byte ceilings bumped with rationale (AGENTS.md 4736→5632, MCP instructions 2048→2432).
- Added: regression tests `unit_tests/test_nood_0156.py` (37, browser-free) — the header-cart substitutions rejected at dom-scan, find(), and assert_visible level; dev-panel healing preserved; `verified` computation across healed/ambiguous/failed/clean runs; compact-RCA healing lines; synthesis/blocking matrix for search/enter/select/suggest/click goals, zero-results and repeated-control blockers, `allow_no_assertion`, and user-check preservation.

## [0.2.0a6] — 2026-07-21

Woks: Noodle's capability work areas become a formal, tested concept — web, mobile, desktop, performance — with tag-aware step grammar, automatic wok tagging on every engine write path, and the three canonical nouns (engine / workspace / wok) taught to every agent surface. Pre-1.0 alpha — interfaces may still change.

- Added: **woks — formal capability work areas** (NOOD_0155). A wok (pun on "WOrK area") is a self-contained domain Noodle can test in: **web** (Playwright + REST + OCR bridge), **mobile** (Appium UiAutomator2/XCUITest), **desktop** (visual agent + Appium WinAppDriver/Mac2 + a new stdlib `.xlsx` reader), **performance** (a new built-in load generator). The registry is code — `noodle/wok.py`, pure data + pure functions, its `wok_for_tags()` a tested mirror of the real `hooks.py`/`catch_all.py` routing — and `noodle wok [name]` lists the woks with per-machine install status. Every wok honours the same four contracts: Gherkin `.feature` files, a screenshot capability, Allure + RCA on every run, and its own isolated unit-test folder (`unit_tests/woks/<wok>/`, `make test-wok-*`) so capability work on one wok can't cross-contaminate another. New concept doc `docs/woks.md`; architecture.md gains the wok layer + updated component map (the mobile agent gets its first first-class box); README gains a "Woks — and the tags that route them" section.
- Added: **performance wok** (NOOD_0155) — plain-Gherkin load tests with zero new dependencies: `@perf` scenarios run browserless (same lifecycle as `@api`) against `agents/perf/loadgen.py`, a stdlib threaded HTTP generator (duration or request-budget mode, one connection per request — a CI *gate*, with Locust named in docs as the graduate-to for real load farms). Steps: `runs a load test on "<url>" with N users for S seconds` / `with N requests [using M users]`, latency assertions over p50–p99/average/max, error-rate and throughput floors, `stores the <metric> into "<VAR>"` for cross-wok reuse, and `saves the load test report as "<name>"` — a rendered latency-over-time chart PNG (Pillow, green/red per request + p95 line) that rides the NOOD_0153 screenshot pipeline into Allure + RCA as the wok's "screenshot". Samples in `sample_feature_tests/performance/`; the whole wok unit-tests against a local in-process HTTP server.
- Added: **desktop wok spreadsheet reader** (NOOD_0155) — `agents/desktop/spreadsheet.py` reads `.xlsx` cell values (shared strings, inline strings, numbers, booleans, named sheets, formula cached results) via stdlib zipfile+ElementTree, browserless, so `reads cell "B2" from sheet "Catalog" of spreadsheet "inventory.xlsx" into "PRICE"` composes into ANY scenario — the Excel-value-drives-a-web-test flow, demonstrated end-to-end in `sample_feature_tests/desktop/features/excel_to_web.feature` with a committed workbook. Paths resolve against the app package's `resources/` like `load_data`; driving the Excel application UI remains the visual/Appium side of the wok.
- Added: **tag-aware step grammar** (NOOD_0155) — `wok.pattern_priority(tags)`: the scenario's own wok table gets first claim on a sentence, web-first best guess with no tags (the exact pre-wok behavior, so nothing untagged changes meaning). Inside `@perf`, `the throughput should be at least 20 requests per second` is a real throughput assertion; inside `@windows`/`@mac`, `cell "…" of spreadsheet "…" should equal "…"` is a real cell assertion; everywhere else those sentences stay with the web compare catch-all, and the namespaced phrasings (`should exceed`, `expects … to equal`) resolve identically in every context. The same priority applies at runtime (`execute_step` reads effective tags), in `noodle validate` (per-scenario tags via the behave parser), and in the LSP (tag lines tracked per scenario), so grading and execution can never disagree. The NOOD_0152 pattern↔dispatch structural guard now spans the wok tables.
- Added: **automatic wok tagging on generation** (NOOD_0155) — every engine write path lands features with the right routing tag (`wok.infer_tag`/`ensure_tag`/`retag_feature`, deterministic, no LLM). Precedence: an explicitly requested tag or any routing tag already in authored content wins and is never overridden → step signals (a load-test step IS `@perf`; swipe/long-press → `@appium`; image/on-screen → `@visual`; REST-only → `@api`) → task wording ("load test…", "android", "windows app", "by image", "endpoint") → `@web`. Wired into `create_test` (rule templates + engine-LLM generation retag their own `@web` default; the generation prompt teaches the model the tag list; `append_to` never retags), `author_test` (tag ensured before validation so readiness grades with that wok's grammar priority), and `write_feature` (missing tag added, result reports `wok_tag`).
- Added: **the three nouns — engine / workspace / wok** (NOOD_0155): formal terminology so humans and agents route work unambiguously — **noodle engine** (this repo / the installed framework), **noodle workspace** (the `noodle init` project, templates refreshed by `--force`), **noodle wok** (a capability work area). Canonical table in `docs/glossary.md § The three nouns`, propagated to CLAUDE.md, `.github/copilot-instructions.md`, both noodle skill cards, the scaffolded workspace AGENTS.md, agent-playbook, workspace-guide and woks.md. Instruction-surface ceilings bumped with rationale (AGENTS.md 75→80 lines / 4480→4736 bytes, skill cards 5120→5376 bytes — the NOOD_0147 pattern).
- Added: step-vocabulary audit closure (NOOD_0152) — false-green fix, mouse primitives, native waits, and mis-route guards, including the structural pattern↔dispatch mirror test extended above.
- Added: evidence screenshots for passing steps + headed follow mode (NOOD_0153) — `NOODLE_EVIDENCE` per-step evidence shots with match-boxes, the `( take a screenshot )` step marker, and headed runs scrolling matched elements into view so a watcher's viewport tracks the engine.
- Docs: branch-naming normalization rules in CLAUDE.md (NOOD_0154) — canonical `feature/nood_XXXX`/`patch/nood_XXXX` form, numbering, and session-branch handling.

- Changed: cloud-first LLM posture — Claude Sonnet (`anthropic/claude-sonnet-5`) is the recommended engine-side model; local models (Ollama, Foundry Local) are demoted to a documented fallback for restricted networks / zero-cost laptops, NOT removed (NOOD_0151). No local-specific code path existed to remove — LiteLLM makes every provider one `NOODLE_MODEL` string — so this is a defaults-and-docs repositioning: `LLM_PRESETS` reordered claude-first, the scaffolded `.env` example and `--llm` help strings lead with `claude`, `noodle cost` with no model configured now prices against `anthropic/claude-sonnet-5` instead of a $0 local figure, the `@llm` skip message names a cloud example, and `client.ask()` no longer carries a silent `ollama/llama3` default (an unset `NOODLE_MODEL` now fails loudly at the call, matching `ask_vision`'s existing behavior — every caller gates on the var, so the default only ever masked misconfiguration by dialing a phantom localhost). Docs re-led cloud-first: architecture.md §5 config recap (Anthropic Sonnet block first, local under a "restricted networks" heading with the screenshots-never-leave-the-machine caveat), llm-setup.md §1 retitled with the Sonnet recommendation above the Ollama sizing guide, README and stale `claude-sonnet-4-6` ids refreshed to `claude-sonnet-5`. The deterministic no-model engine remains the CI baseline; nothing requires an LLM.

- Fixed: agentic RCA (`NOODLE_RCA`) actually sees the failure screenshot again (NOOD_0150) — `hooks.after_step` deletes the raw `FAILED_*.png` once the annotated copy exists (NOOD_0035 dropped the duplicate), but still passed the deleted raw path to `rca.review()`, whose best-effort `except` swallowed the read error and returned `None` — so on the normal failure path (page present, annotation succeeds) the vision classification silently never ran and no `rca_category` label reached the Allure result. The hook now hands RCA `annotated_path or raw_path`, whichever file survived.
- Fixed: architecture docs re-audited against the code for NOOD_0150 (enterprise-architect review prep) — stale claims corrected rather than left to mislead: pattern count 50+→250+ (architecture, encyclopedia; codebase-spec ~190→~250), unit-test count 251→1,800+, MCP tool inventory 18→22 (`author_test`, `preflight`, `probe_app`, `log_diagnostic` added to the spec), `allure-python-commons` floor 2.13→2.16, the `[all]` extra documented as deliberately excluding `[llm]` (NOOD_0074) with the `parallel`/`mobile`/`desktop`/`mcp`/`dev` extras rows added, CI sharding named file-level (one `.feature` per agent) not feature-folder, network capture documented as written for EVERY scenario and attached to the Allure result (was "failure only"), `rca.md`+`rca.html` documented as written on every run (green renders the "no failures" page), the CLI command table expanded to the real surface, and the README agent-mode diagram redrawn (`author_test`/`generate_test` write their own files; `write_feature` saves one caller-authored `.feature` — it never wrote "POM + resources"; `probe_page` added).
- Changed: the Trigger-1 LLM-fallback demo verb moved from "submits" to "finalizes" (NOOD_0150) — NOOD_0025 promoted `submits the … form` into a real `submit` pattern, so the demo feature, the docs walkthrough, and the `test_llm_openai_endpoint` `__main__` demo all resolved locally and never exercised the model; "finalizes" verified against the live resolver as genuinely unmatched. Also documented `NOODLE_VISION_MODEL` honestly across architecture/encyclopedia/glossary/steps-dictionary: it is an enable flag for the `@visual` vision fallback (which also fires on `NOODLE_MODEL`), and the model string actually called is always `NOODLE_MODEL` — its value is never read.

## [0.2.0a3] — 2026-07-20

- Added: session diagnostics (NOOD_0147) — when an LLM-driven test-development session goes wrong, the failure story now gets written down instead of evaporating with the agent's chat. The engine itself watches the run stream (`noodle/diagnostics.py::track_run`, per-target state in `.noodle/diag_state.json`) and detects the three mechanically-visible failure shapes — `first-attempt-fail` (first run of a dev session red), `hard-fail` (red-run count reaching `NOODLE_DEV_FIX_ATTEMPTS`), `slow-dev` (first-run→now wall clock past `NOODLE_DIAG_SLOW_MIN`, default 20 min) — and folds a `diagnostic_due` nudge into the run result the driving agent already reads (`run_test`/`run_and_report` MCP payloads, `noodle run --json`, a 🩺 line on the plain CLI), so the mechanism works even for an agent that never loaded AGENTS.md; a green run clears the streak and state idle past `NOODLE_DIAG_SESSION_GAP_MIN` (default 120 min) restarts as a fresh session so long-lived workspaces/CI can't misfire. Two agent-side triggers complete the set: `over-budget` (the agent's OWN spend past `NOODLE_DIAG_COST_BUDGET`, default 20 AIC — the number engine-side `llm_cost` can't see) and `manual` (the prompt contains `--diagnostic`/`skill: diagnostic`). The agent then makes ONE `log_diagnostic` MCP call (`noodle diagnostic log`) from session memory — summary, timeline, suspected cause, fixes tried, duration, attempts, agent + cost — and the engine deterministically appends the last-run result, compact RCA verdict, `llm_cost` and version, scrubs secret values (NOOD_0118 redactor, new public `log.redact`), and writes one Markdown file with YAML front matter into the workspace's `diagnostics/` folder (scaffolded into `.gitignore` by `noodle init`). Anti-spam is engine-enforced: no trigger → nothing written, narrative fields clipped at 4 KB, same-session/same-app re-logs update in place (`NOODLE_DIAG_DEDUPE_MIN`, default 30 min), and the folder caps at `NOODLE_DIAG_MAX` (default 25, oldest rotate out). `noodle diagnostic list`/`bundle`/`guide` round it out — `bundle` zips the folder into the one file a tester sends back, and `guide` prints the full contract from a copy bundled into installed distributions (pyproject force-include, NOOD_0145 pattern), so MCP-blocked corporate environments get the whole loop — detection nudge, contract, log, list, bundle — over the plain CLI, with no repo-relative doc path in any hint. The always-on AGENTS.md gained exactly one 5-line rule (its byte/line ceilings in `test_nood_0128/0130/0131` bumped 70→75 lines / 4096→4480 bytes with the rationale documented); the MCP connect-time instructions gained nothing (still ≤2048 bytes) since the run-result nudge + tool docstring carry the contract. New doc: `docs/session-diagnostics.md`; playbook §7.5 + checklist item 9; `noodle diagnostic` section in `docs/cli-reference.md`; tests in `unit_tests/test_nood_0147.py`.

## [0.2.0a2] — 2026-07-20

One-pass stateful discovery and honesty hardening on top of 0.2.0a1: `probe --do` transactions with halt-and-report semantics, typeahead capture end-to-end, value-named-control and expectation-miss authoring blockers, deterministic goal target matching, the `blocked-by-overlay`/`wrong-action-target` RCA verdicts, and a portable step-dictionary reference. Pre-1.0 alpha — interfaces may still change.

- Fixed: `noodle steps <keyword>` no longer points external workspaces at a source-repo path (NOOD_0145) — the footer read `Full reference: docs/steps_dictionary.md`, which an agent in a test workspace resolves as `<workspace>/docs/steps_dictionary.md`, searches a directory that does not exist, and concludes the documentation is missing (wasted tool calls and possible authoring stalls; the dictionary itself always loaded fine from the bundled `noodle/_docs/` copy). The footer now names portable access methods — run `noodle steps` without a keyword, or MCP `read_docs('steps_dictionary')` — and the missing-dictionary error says to reinstall the package instead of the stale "run from a noodle repo checkout"; the internal bundled path is never printed. `docs/cli-reference.md` example output synced; regression test in `unit_tests/test_cli_step_search.py` proves the command works from a directory outside the source repo without suggesting a workspace-relative `docs/` path.
- Fixed: probe naming honesty, transaction honesty, deterministic goal targets, and a no-navigation RCA verdict (NOOD_0145) — the defects a reviewed login-flow session (four red runs before green) pinned on the engine. (1) **Editable values are never visible text**: the DOM collector used `innerText || value`, so an unlabeled editable input was named by whatever the user had typed into it, marked directly resolvable, and skipped its POM entry — a phrase runtime locators (labels/roles/placeholders/visible text, never values) structurally cannot resolve, and one the NOOD_0144 machine-name fix could not catch because the source read as "text". Value now only captions button-like inputs (`button`/`submit`/`reset`); a textarea contributes no text; a pure-Python contract in `summarize()` enforces the same rule whatever a collector sends, so such controls fall back to machine identity and ship a POM entry. (2) **Failed `--do` actions can no longer vanish**: `do_warnings` render in the text output (top, with the halted-at action, its resolved selector, and the not-attempted list) and ride the compact payload; the transaction HALTS at the first failed action instead of running later actions against an invalid state; `--expect` is skipped (with an explaining warning) when the transaction failed; and any failed `--do` action or explicit `--expect` miss forces `author_ready: false` with a top-level "transaction did not reach requested state" blocker in both full and compact verdicts. (3) **One select implementation**: the probe's transaction now selects through the runtime's own `actions.select_on` (native `select_option` + the open-and-click-options custom-dropdown fallback) instead of a second, weaker native-only path — probe and run time can no longer disagree on what is selectable. (4) **Deterministic goal target matching** replaces first-substring-wins in `goal._locate`: exact name → exact runtime auth synonym ("login" → "sign in") → unique visible submit control for login/submit intent → unique substring; several distinct candidates now *block as ambiguous* instead of guessing, and the compiler reuses the evidence pass's resolution so both stages name the same control. The probe renders the submit flag as `(submit)` and ranks visible submit controls first among copy-ready steps. (5) **`wrong-action-target` RCA category**: clicks record their start URL; a submit-like click that leaves the URL unchanged stamps a `[no-navigation]` warning at failure time and classifies as `wrong-action-target`/high instead of the old broad `app-regression` — pointing at the wrong click target, not at the app. Generated workspace guidance updated inside the byte ceilings: AGENTS.md names goal-mode + `--run` as the default for new tests, and the scaffolded README's first step is `noodle author --spec --run` into `noodle_tests/web/<app>` instead of hand-copying `sample_app/`. Browser-free coverage in `unit_tests/test_nood_0145.py`.
- Added: `noodle probe --do` / `probe_page(do=[…])` — one stateful discovery session for a whole transaction (NOOD_0144). A reviewed config-panel flow burned six failed runs because everything past "fill → select → Save" was invisible to a single-load probe, so each post-save locator was a guess resolved by a red run. `do` takes ordered `"enter <value> in <field>"` / `"select <option> from <dropdown>"` / `"click <name>"` actions (tiny fixed grammar, parsed before any browser launches), executes them for REAL after the `--click` reveals — a save/submit is the point — and appends the settle-aware page delta after every action under `revealed`, so "Save → login appears" is discovered in the same probe that saw the form. Targets resolve through the same name matching as `--click` (later actions see what earlier ones revealed); a failing action lands in `do_warnings` advisory-style; action values are never echoed into the payload, and `{env:KEY}` inside a value resolves engine-side from the workspace env chain (`core.probe_page`), so credentials can cross a login gate without transiting the transcript. Wired through CLI (`--do`, repeatable), core, and MCP (`probe_page(do=…, workspace=…)`); `_do` executor + `parse_do` in `noodle/agents/web/probe.py`.
- Fixed: the probe can no longer suggest a "copy-ready" phrase its own resolver cannot resolve (NOOD_0144) — the reviewed session's first defect: a control whose display name was humanized from an `id`/`data-testid`/class token (sources `find()` never consults) but that carried a >40-char text node slipped past `_needs_pom` as "readable", so the probe emitted a bare phrase with no POM entry and the authored step was a guaranteed red run. `_name_and_source` now tracks WHICH handle produced the name; a machine-sourced name (`id`/`testid`/`name`/`cls`/`tag`) forces `needs_pom=true` + a POM entry with the verified CSS selector and a `machine_name` flag, so every suggested step either resolves via a handle find() consults or ships a working selector — the probe contract the postmortem demanded. Machine-named controls move from the copy-ready steps slice to the POM slice in compact mode automatically.
- Added: RCA classifies an intercepted click as its own `blocked-by-overlay` verdict (NOOD_0144) — Playwright's "`<sel>` intercepts pointer events" failure (another element covers the target) also carries "waiting for element to be visible" in its call log, so the NOOD_0123 rule labeled it "present but hidden (responsive/duplicate twin)" and sent the reviewed session chasing locators when the real fix was closing the modal the flow itself opened. A new rule ordered before the hidden/duplicate rule names the covering element and prescribes handling it (author the close/confirm step, or `NOODLE_AUTO_DISMISS=true`) — explicitly NOT re-guessing the target's locator. New category in `CATEGORIES` + playbook §7 row.
- Fixed: `author_test` re-authoring no longer accumulates dead config (NOOD_0144) — the reviewed session's repeated re-authors left stale env keys and an unused generated POM behind, and later laps kept resolving against them. Goal mode (the engine owns the whole package) with `overwrite=true` now PRUNES environments-yaml keys that no feature or POM in the app references (the app URL key and keys other features use always survive) and deletes an orphaned `<stem>_pom.yaml` when the compile produced no POM — both inside the byte-backup transaction, reported as `pruned_env_keys`/`removed_stale_pom`. Feature mode keeps the NOOD_0129 merge contract (caller-owned content, nothing deleted) but reports `stale_env_keys`. Every author result now also carries `unused_pom_keys` (supplied POM keys the feature never mentions) and `ready_means` ("static authoring checks — runtime is proven only by the run") — the honest-naming fix for a session that read `ready: true` as runtime-proven and over-claimed a green.
- Changed: the retry instruction across every always-on surface flipped from "fix and re-run up to 10 times" to reproduce-once-then-fix (NOOD_0144) — the postmortem's core workflow failure was one guessed locator fix per full red run, six times. `_AGENTS_MD`, both skill cards, the MCP `run_and_report` docstring, `.github/copilot-instructions.md`, playbook §0.3/§5/§7 + checklist 5a, llm-performance §4, and the scaffolded `.env` comment now carry the rule: on the first locator/state failure, reproduce the EXACT failing state once (`probe --do "<the actions so far>"`), re-author every downstream step from that snapshot, and re-run only with a cause-backed fix — `NOODLE_DEV_FIX_ATTEMPTS` is a ceiling, not a budget. Playbook §0.3 reframed: `--do` crosses config gates and multi-step flows in the probe itself; the one-exploratory-run budget now applies only to gates `--do` genuinely can't cross (external OAuth, hardware prompts). All byte/line ceilings hold (AGENTS.md ≤70 lines/4096 B, skill cards ≤5120 B, MCP instructions ≤2048 B, hot docstrings ≤6144 B). New `unit_tests/test_nood_0144.py` (18 tests) pins the phrase contract, the `--do` grammar/executor/env-resolution, the overlay verdict ordering, and the package-hygiene paths.

## [0.2.0a1] — 2026-07-19

First alpha. Everything since the 0.1.0 baseline: constrained goal authoring, context-aware `noodle doctor`, atomic `author_test`/`run_and_report`, proactive DOM probing, per-app secrets, and the compact-probe cost cuts. Pre-1.0 alpha — interfaces may still change.

- Fixed: constrained goal authoring (NOOD_0137) no longer marks a goal ready while omitting a prerequisite or accepting evidence that misses the requested minimum (NOOD_0139) — four correctness gaps closed on the pure-Python compile path (`noodle/repl/goal.py`), the compact-probe work kept intact. (1) **Reveal provenance**: a control the probe only saw after opening a panel is reachable *only* when a click that opens its trigger appears earlier in `actions`; a control surfaced by automatic `--discover` alone is never treated as reachable — either blocks with a precise reason naming the control and its trigger, instead of compiling an action against a control that isn't there yet (the observed multi-stage settings-flow failure). (2) **POM every goal action target** with a stable probe selector, no longer gated on `needs_pom` — that flag is about probe *presentation*, not a runtime accessible-name guarantee; a probe-visible field with `needs_pom=false` used to be dropped from the POM and then time out on the full locator budget at run time. (3) **Minima enforced before any browser launches**: a `count` check parses the observed results-summary number and blocks when it is below `min` (or when the summary carries no unambiguous number); an `any_of` check counts *distinct* matching alternatives and never treats one match as proof of `min > 1`. (4) **Honest runtime-only checks**: a check anchored `after` an enter/select (data the probe never types) — or a commit click after data entry — is kept verbatim in the feature but returned under `evidence.runtime_asserted`, never claimed probe-proven; the single execution run proves it, so post-login/post-save assertions are authorable without pretending the probe crossed the gate. The probe now executes reveal clicks but not commit (save/submit) clicks, so probing can't mutate application state. Removed the synchronous provenance-manifest machinery (`manifest`/`verify_manifest` + the write-hash-then-immediately-reread gate in `author_test`): `run_after_author=true` writes and runs in one transaction, so hashing files only to reread them microseconds later protected no boundary — atomic writes, author validation, and the nonzero-scenario execution gate (a run where 0 scenarios passed is still a forced failure) remain. New `unit_tests/test_nood_0139.py` (15 tests) pins each path; `test_nood_0128.py`/`test_nood_0137.py` updated; MCP `author_test` docstring documents the reachability + runtime-asserted contract.
- Changed: `noodle doctor` is context-aware (NOOD_0138) — it no longer assumes its path is a generated workspace. It resolves what it's inspecting by walking the given path's ancestors only: an engine source checkout (pyproject `name = "noodle"` + `noodle/` + `unit_tests/`) gets an **engine** profile (editable-linkage check, warning on stray workspace files at the engine root — it never compares engine docs against workspace templates or recommends `noodle init --force` there); a directory with `noodle.yaml` gets the **workspace** profile (config validity + `tests_dir` containment, scaffold glue, template drift, MCP config command resolution); anywhere else runs install-only checks. Engine wins a same-directory marker collision (the engine repo is deliberately its own workspace). The launcher check now compares **provenance, not count**: every `noodle` on PATH is probed with `--version` (shell=False, short timeout, bounded output) and parsed — identical duplicates (project `.venv` + uv tool shim executing the same editable build) are `info`/exit 0, conflicting version/root/SHA/install-type is `fail`, unprobeable is `warn`. New `--scope auto|engine|workspace|install` and `--json` (one bounded object: `ok`, `context`, `checks` with stable IDs `install.*`/`engine.*`/`workspace.*`). Exit codes pinned: 0 healthy, 1 warn/fail findings, 2 bad path/forced-scope mismatch; check crashes become explicit `*.internal-error` fail records. Still strictly read-only: no writes, network, browser, package changes, or secret access — remediation stays with `noodle init` / the printed reinstall command. Orchestration moved to `noodle/doctor.py`; `install_check.report()` replaced by structured `parse_build_line()`/`probe_launcher()`.
- Changed: probe compact output cut ~34% on real retail pages and made task-aware (NOOD_0137) — driving the template prompt against a live retail site cost ~30 AIC on Copilot-class agents while the framework floor is 3 driving ops; the compact probe payload was the inviter: 29.6 KB of which ~95% was task-irrelevant. Five cuts, all compact-mode only (full/`--json`-raw render unchanged): (1) the OneTrust preference-center internals that leaked past the NOOD_0119 consent denylist (`onetrust`, `ot-switch`/`ot-label`, `select-all-hosts/vendor-*`, `chkbox-id`, `clear-filters-handler`, `filter-cancel-handler`) are consent noise; (2) the tile-caption slice — the one uncapped list left — collapses numbered families ("go to slide 1…9" → one exemplar + count; distinct captions never group), respects the standard cap, and drops the redundant control line for marketing-length (>60-char) captions whose POM entry already carries everything; (3) needs-POM lists rank visible controls first, hidden non-toggles (the hidden-trigger-zone case) next, hidden toggles (facet floods) last, and collapse numbered facet families — so the default cap eats junk, not authoring material; (4) a PROVEN-ambiguous selector is never offered in the paste-ready POM block (its ⚠ "narrow it first" line stays), and `author_ready` in compact output is scoped to the suggestions actually shown — a page-global `false` driven by a control the capped output never surfaces no longer sends agents off fixing irrelevant ⚠ items (page-global verdict unchanged in full render); (5) `probe()` now arms a permission-API shim before navigation and sweeps popups with the run-time engine's own `_sweep_popups` after settle — the snapshot shows the real page, and the observations surface as ready-made steps (`the user closes the location prompt`, `closes the popup if it appears within 10 seconds`) plus a paste-ready **scenario skeleton** (navigation → permission/popup closes → search flow → results-floor assertion) closing every compact render and riding the MCP payload as `skeleton` — the popup-handling guesswork this prompt shape is about becomes copy-paste. Verified against the live site: 29,620 → 19,659 bytes (-34%), signals + skeleton emitted, and the authored test runs green identically on main and the branch (same 8 steps, LLM cost: none). `noodle/agents/web/probe.py`; scaffolded `AGENTS.md` mentions the skeleton (net-zero at its 70-line/4096-byte ceiling); playbook §0 probe item updated; new `unit_tests/test_nood_0137.py` (16 tests).
- Fixed: authoring preserves the FULL supplied URL — path, query, fragment, trailing slash — instead of silently truncating to `scheme://netloc` (NOOD_0135) — the root cause of the reviewed 69-call/3.08M-token session: `author_test` stored only the origin for `https://example.test/application/login`, so the first run opened the host root, the per-page `login_pom.yaml` auto-scope never activated there, and the failure read as locator rot — then got amplified through nine runs and sixteen extra probes/inspections. All three origin-only write sites (`repl/core.author_test`, `generate._stub_environments`, `generate._scaffold_resources`) now store `normalize_url(base_url)` unchanged; origin-only inputs stay byte-identical, and app-package reuse still matches by host/port (package identity and navigation destination are separate concerns). Readiness now verifies URL *fidelity*, not key presence: after the write, the resolved app key is compared with the supplied normalized URL and any mismatch (process-env override, `environment_values` collision, another env file) returns `ready: false` with one blocking reason naming both values. Old origin-only environment files can't be migrated automatically (the dropped path is unrecoverable) — the documented recovery (`docs/manual.md` → Troubleshooting) is re-running authoring with the original complete URL and `overwrite=true`, which corrects the app URL in place without touching unrelated keys. New `unit_tests/test_nood_0135.py` pins the round-trip (author, CLI `--spec`, generate stubs), origin-only/reuse invariants, and the recovery path.
- Added: failures carry the actual page URL and a `navigation-mismatch` RCA verdict classified BEFORE any locator rule (NOOD_0135) — the engine knew "expected `/application/login`, currently `/`" but reported `[locator-rot]`, steering the session into locator debugging. `actions.navigate` now records (requested, landed); on a failed step, `hooks.after_step` prepends `URL: <actual page url>` to the structured failure warnings plus `[navigation-mismatch] expected <path>, current <path>` when the goto landed off the requested path (server-side redirects). Guarded against false positives: origin-only navigations never flag, a current path that merely extends the requested one (redirect to `/login/step2`) passes, and once the scenario legitimately click-navigates off the landing URL the detector stays silent. `rca_report.classify` matches the verdict first — high confidence, fix = correct `environments.yaml`/navigation (re-author with the full URL) before touching POM entries — with `navigation-mismatch` added to `CATEGORIES`; locator-rot remains the fallback when navigation is correct. Proven live: a 302-sabotaged route classified `navigation-mismatch` on the first failed run.
- Changed: probe reveal clicks settle on DOM mutation instead of a fixed 3 s network-idle wait, and known-hidden triggers dispatch directly (NOOD_0135) — `probe._settle` gained a mutation mode (pre-action DOM fingerprint → wait for change, 1 s cap → ~120 ms stable window, existing timeout as ceiling) used after `--click` reveals, custom-combobox opening, and non-navigating search-trigger clicks; navigation mode (body content + bounded network quiet) remains for `goto` and search submits, and mutation mode falls back to it when the click actually changed `page.url`. Measurement exposed the real 3 s: not the network-idle grace but `click(timeout=3000)` timing out on 0-size hidden trigger zones before the `dispatch_event` fallback — `_reveal` now dispatches immediately for a control the probe already saw as `visible: false`. Replay-spa medians: initial 1.193→0.850 s, one reveal 4.180→0.999 s (target ≤ 2.0), reveal+options 4.267→1.205 s (target ≤ 2.5); exact medians live in `docs/benchmark-nood-0131.md` (new NOOD_0135 section), unit tests assert call shape/mode selection only. Also: `author_test` returns an explicit `validated` flag (mirrors `ready`) making it unambiguous that a separate `validate` call after `ready: true` adds nothing.
- Changed: `author_test` accepts prompt-supplied credentials again via a new `secret_values` mapping, and CLI run preflight is on by default (NOOD_0130) — NOOD_0126 had made every agent surface *reject* prompt credentials and leave empty `*_secrets.env` placeholders, and preflight was opt-in on a plain `noodle run`; the combination let `author_test` report a credential missing yet still return `ready: true`, so a plain run launched a browser into a login form it could never complete (the last confirmed-green login flow was the earlier NOOD_0125 behavior, which accepted credentials from the prompt and stored them in the app package's gitignored secrets file). This restores that proven workflow as a **documented, temporary** policy — a general credential path, not app-specific — with the fewest changes: (1) `core.author_test(secret_values={KEY: value})` validates keys against env-var naming and rejects empty values, merges the supplied values into the app-local `<app>_secrets.env` (preserving unrelated keys and comments, quoting values so `python-dotenv` round-trips them byte-for-byte), joins the existing atomic backup/rollback transaction (a later write failure restores the original file byte-for-byte), never returns or echoes a value, recomputes `missing_secret_keys` after the write, and adds any still-unset referenced credential to `blocking` so `ready` is true only when Gherkin, POM, and credential checks all pass; (2) `noodle run` preflight is now default-on (`--no-preflight` is the explicit human escape hatch) so a plain run never launches a browser on missing/placeholder secrets; (3) `noodle author --spec` and MCP `author_test` forward `secret_values` unchanged and never serialize it; (4) the credential-policy text across the generated `AGENTS.md`, `PROMPT_TEMPLATE.md`, and both `.claude`/`.copilot` skill cards now says: use prompt credentials without re-asking, write them ONLY into the app's gitignored `<app>_secrets.env`, never place a value in a feature/POM/`environments.yaml`/tool result/report/reply, and stop before browser launch when a credential is absent. `{env:KEY}` remains the only credential reference in Gherkin; secrets files stay app-scoped and gitignored; runtime redaction, RCA, reports, and rollback are unchanged. **Transcript exposure is a deliberate, accepted risk for NOOD_0130** — a prompt value plus one write-only `author_test` call — to be removed by the deferred masked secret broker (`docs/todo/secret-broker.md`, updated to mark that as the future hardening path rather than current behavior). New `unit_tests/test_nood_0130.py`; NOOD_0126/0128/0129 secret-policy tests updated to match.
- Changed: `author_test` overwrite rollback is now honest, and it reports a `ready` verdict (NOOD_0129) — two gaps the fast/cheap-authoring review left after NOOD_0128. (1) Rollback previously removed only files `author_test` *created*, so a write failure *after* it had overwritten an existing `environments.yaml`/POM left that file clobbered. It now backs up every existing file's bytes before touching it and writes via a sibling temp + `os.replace`, so a forced failure on a later write restores every original byte (a half-written file never lands over the original either). (2) It ran a Gherkin parse check but no readiness contract, so a separate `noodle validate --resolve` call was still needed and an unusable package could reach the browser silently. The result now carries `ready` + `blocking`: a step that matches no deterministic pattern when no `NOODLE_MODEL` is set to fall back to (opt in per-scenario with `@llm`), or a POM that can never scope to the feature's URLs (`validate --resolve`'s own hard-fail, reusing `validate.lint_pom_scopes`), marks the package not-ready — files are still written so the caller fixes them in place. Also rejects a non-mapping POM before any disk write. `core.author_test`; `noodle author` prints the blocking reasons and exits 1; MCP `author_test` returns the same fields. New `unit_tests/test_nood_0129.py`; `docs/llm-performance.md` now leads new-package work with `author_test` and execution with `run_and_report(serve_reports=True)`, and replaces the old ~12-call budget with the measured ≤25-AIC easy-test goal (pinned-host, not a cross-model gate) plus the deterministic CI proxies (≤3 tool calls, ≤2 browser launches, 0 engine LLM calls). Deferred masked-secret-broker design captured in `docs/todo/secret-broker.md` (not started).
- Added: atomic authoring `author_test` / `noodle author --spec <json-or-yaml>` (NOOD_0128) — writes a whole test package in one transaction to replace the copy-`sample_app` → rename → edit×4 → validate round-trips the reviewed session burned model calls on. Locates or creates the app package (`<tests_dir>/web/<app>`, or an existing package already mapped to `base_url`), writes its `environments.yaml` (base URL + any `environment_values`), the POM (`pageobjects/<feature_stem>_pom.yaml`), and the feature — validated as Gherkin *before* anything touches disk, so a parse error writes nothing — and creates ONLY missing `required_secret_keys` as empty placeholders in the gitignored `<app>_secrets.env` (never accepts secret *values*; an existing real value is never overwritten). Any file it created is rolled back if a later write fails. Returns the written paths, `created_secret_keys`, `missing_secret_keys` still needing real values, unmatched steps, and semantic warnings. `core.author_test` + MCP `author_test`; the scaffolded `AGENTS.md` (net-zero on its 120-line ceiling), `docs/mcp-guide.md`, and `docs/agent-playbook.md` now steer authors to it over the manual sequence.
- Added: secret/config preflight `preflight` / `noodle run --preflight` (NOOD_0128) — before launching a browser, checks every `{env:KEY}` the target references resolves to a real value (not missing, not a `CHANGE_ME`/empty/`<marker>` placeholder), loading each involved app package's `resources/` env chain the way `hooks.before_all` does. A missing credential returns `missing_secret_keys` with **zero browser launches** instead of failing ~50s later at login (one of the two doomed runs in the reviewed session). Also carries the redundant-post-navigation-wait warning. `core.preflight` + MCP `preflight`; run automatically inside `run_and_report`. On the CLI it is opt-in — on automatically with `--json`/`--serve` (the agent combined path), `--preflight` to force it on a plain run, `--no-preflight` to skip — so a bare `noodle run` is unchanged.
- Changed: `run_and_report` is now a one-shot run→report→(serve) result (NOOD_0128) — `core.run_and_report` preflights secrets first (no browser on missing creds), runs, rebuilds both reports, folds the compact RCA (verdict + failing step + fix) straight into the payload on red, and optionally serves the reports and returns the URLs — so a driving agent needs one call, not the run + `get_rca` + `report` + `serve_report` chain the reviewed session used. MCP `run_and_report` gained `serve_reports`; the CLI `noodle run` gained `--serve` (host both reports and print the URLs) and `--json` (emit the same bounded payload — pass/fail summary, report paths, compact RCA on red, served URLs — for shell-driven parity with the MCP tool).
- Added: semantic wait warning — an explicit page-load wait immediately after navigation (NOOD_0128) — `validate.redundant_post_nav_waits` flags a `waits for the page to load`/`… to be ready`/`… network to be idle` step directly after a navigate step: the engine already waits for the page on navigation, so the wait only adds wall-time and can time out on a slow SPA (the redundant ~21s the reviewed session's first browser run burned). Surfaced in `validate_feature`'s new `warnings` field, in `author_test`'s result, and printed by `noodle run --preflight`. Warn-only — a legitimate wait the author meant to keep is never deleted.
- Added: bounded-reveal probing — `probe_page(open_native_controls=True, max_reveal_depth=N)` / `noodle probe --open-native [--max-reveal-depth N]` (NOOD_0128) — discovers a gated page's nested dropdown options in ONE browser session instead of a second probe. After the caller's explicit `--click` reveal (panels/tabs — unusual widgets stay on the explicit list), it automatically enumerates native `<select>` options inline (they live in the DOM — no click) and click-opens custom comboboxes on the initial page and each revealed panel, bounded by `max_reveal_depth` and a per-page click budget, and NEVER clicks a state-mutating control (a `_MUTATING_RE` denylist blocks submit/save/delete/login/checkout/pay/… by name, even one that looks like a dropdown trigger). Options surface as `options:` lines in the render and a `dropdown_options` map in the compact/MCP payload, so the author copies a real option value into the `selects "X" from "…"` step instead of guessing. `probe._auto_open`/`_select_options`/`_is_mutating`; threaded through `core.probe_page`, MCP `probe_page`, and the CLI; new safety-path tests in `unit_tests/test_nood_0128.py`.
- Changed: the scaffolded `AGENTS.md` instruction floor cut from 120 to 70 lines (NOOD_0128) — it rides along on every model call, so this is the largest per-call token line item. The layout ASCII, command-variant prose, the "Writing steps" and standalone "Template normalization" sections, and the failure taxonomy detail moved to `docs/agent-playbook.md` (fetched on demand) and the MCP tool schemas; what stays is the 7-step quick path (now leading with `author_test` and the preflight/one-shot run), the POM scoping trap, the credential/transcript-safety rule, and the output-discipline rule. Every guard-pinned token (`probe_page`/`noodle probe`, `NOODLE_DEV_FIX_ATTEMPTS (default 10)`, `--quiet`/`--compact`, `Cheapest evidence first`, `network capture`, `report serve`/`allure serve`/`http.server`, `resources/environments.yaml`, `use_llm=True`/`append_to`/`llm-performance`, `token economy`, `resources/scripts`, the output-discipline phrases) is preserved; a new `test_nood_0128` ceiling locks the 70-line target. Existing workspaces refresh with `noodle init --force`.
- Added: `noodle doctor` (NOOD_0128) — read-only health check that reports generated instruction/template files (`AGENTS.md`, `CLAUDE.md`, `PROMPT_TEMPLATE.md`, `README.md`, sample feature/POM) that have DRIFTED from the installed noodle version, so a stale brief carrying operating rules the framework has since removed is caught before it misleads an agent. Points at `noodle init --force` (originals saved `*.bak`); changes nothing, exits 1 if anything is stale. Shares the template map with `init` via `cli._template_files(root)` so the two can't diverge.
- Changed: slow-site authoring discipline on the scaffolded `AGENTS.md` + a dev-loop timeout floor in the `.env` stub (NOOD_0127) — a multi-iteration authoring session on a gated, slow flow burns runs on avoidable traps that scale with per-run wall-clock AND agent tokens: blind-guessing decorated assertion text across several slow runs (`Branch #12` → `branch #12` → `12`), asserting against a page the probe could never cross an auth/config gate to reach, and silently shipping a green test that dropped the asked-for verification. `_AGENTS_MD` (`noodle/cli.py`) now teaches four rules — assert the smallest stable substring before any decorated form (never brute-force casing across runs); the probe stops at auth/config gates (login, dropdowns, tenant/device config), so for a page behind one budget ONE exploratory run to harvest real assertion text before writing; token hygiene on the RCA loop (`grep` the run.log for the failing line, never `view` the full log/screenshot/network capture unless RCA is inconclusive — vision costs ~10× the tokens of text); and never silently drop the asked-for check (probe/inspect for a hard-to-phrase verify's selector and add a POM entry, or say it can't be verified). Woven in net-zero against the 120-line `AGENTS.md` ceiling by compressing existing prose. Because a pure-MCP agent driving from outside the workspace reads `read_docs('agent-playbook')` rather than the scaffolded `AGENTS.md`, the two rules that weren't already in the playbook — A2 (gated-page exploratory run) and the firm A5 rule — are mirrored into `docs/agent-playbook.md` (§0 north star + a new probe item 0.3); A1 and A4 were already there. The `_ENV_STUB_BASE` gains a commented, CI-safe dev-loop floor (`NOODLE_FIND_TIMEOUT=25000` / `NOODLE_WAIT_EXTENSION=15000`) with a RAISE/REMOVE-for-CI warning, so a missed element on a spinner-heavy site fails fast during authoring instead of eating the full ceiling. Docs/strings only, no logic; new `unit_tests/test_nood_0127.py` pins the four rules, the floor, and the ceiling. Existing workspaces refresh with `noodle init --force`.
- Changed: `noodle probe`'s suggested POM YAML now leads with a `match: {}` block (NOOD_0126) — a probed-and-pasted `<page>_pom.yaml` with no `match:` auto-scopes to URLs containing its filename stem (`agents/web/pom._wrap_page`), so a login page object named `login_pom.yaml` silently stops applying the moment a scenario navigates past `/login`. This was the dominant cost in the reviewed session (six browser runs, the selectors were right but the file never activated). Both the full (`summarize`) and compact (`_compact_pom`) POM suggestions now emit `match: {}` — folder-global, active on every URL the scenario visits — with an inline comment showing how to narrow it (`match: {url_contains: "/path"}`) when the file really is page-specific. An empty page still emits no YAML at all, not a bare header-only stub. New `probe._match_header`; cases in `unit_tests/test_nood_0126.py`.
- Added: `noodle probe --section revealed` (NOOD_0126) — prints ONLY the controls a `--click` opened (their new controls + suggested steps), suppressing everything from the initial page load. The single-control reveal mode the review asked for: `noodle probe <url> --click "dev panel" --section revealed` hands back just the dev-panel's fields to author against, instead of the whole-page dump. With no `--click` it prints a one-line hint pointing at `--click`. Wired through `probe._render_section` + the CLI `--section` validator.
- Changed: `noodle validate --resolve` now HARD-FAILS (exit 1) on the auto-scope POM lint instead of only warning (NOOD_0126) — a `*_pom.yaml` whose filename stem can never appear in any sibling feature's URL has keys that silently never resolve, exactly the "looks fine, does nothing" mistake the dry-run exists to catch. The failure message names the fix (`add match: {}`, applies on every URL, or a real `match:` block). The orphan-key lint (NOOD_0109) and plain `noodle validate` (no `--resolve`) stay warn-only. `_lint_pom_scopes(target, hard=True)` in `noodle/cli.py`.
- Added: RCA classifies a scoped-out POM key as its own verdict (NOOD_0126) — when a key exists in a page-object file whose `match:` doesn't fit the live URL, `pom.explain_miss` already spelled that out in the miss message, but `rca_report.classify()` let it fall through to the generic "add a POM entry" advice (wrong — the entry is already there, it just doesn't apply here). A new rule, checked before the generic "Could not find" rule, returns `locator-rot`/high with the fix "add `match: {}` to that POM file". New case in `unit_tests/test_nood_0126.py`.
- Changed: `noodle inspect` now brands a self-heal resolution "⚠ DIAGNOSTIC ONLY — do NOT author this phrase" (NOOD_0126) — inspecting a phrase that only resolves via partial/fuzzy self-heal (e.g. `"adv panel"` healing onto `ADVANCED PANEL`) previously just noted the heal tier, which the reviewed session mistook for a working authoring contract and encoded, producing another failed run. The render now says plainly that a healed match is diagnostic evidence, not a stable selector — POM the element or use its exact name. A clean (non-healed) resolution carries no such warning. Docs/string only in `inspect_locator.render`.
- Changed: `noodle report serve --background` (the agent path) now defaults to an OS-assigned port (NOOD_0126) — `--port` became a mode-sensitive default: `0` (free port, no conflict retry) for `--background`, `8000` (bookmarkable) for a human's foreground serve. Agents driving the detached server no longer hit a "port 8000 already in use → retry" lap. The MCP `serve_report` tool already defaulted `port=0`; this brings the CLI's agent-oriented flag in line. `noodle/cli.py`.
- Added: transcript-safe secret-handling rule on every always-on agent surface (NOOD_0126) — the reviewed session leaked a password because it wrote the local `*_secrets.env` through a generic edit tool, whose patch payload is rendered in the client transcript *before* Noodle ever loads or scrubs it; gitignore protected the repo, not the transcript. `_AGENTS_MD`, `_PROMPT_TEMPLATE` (both in `noodle/cli.py`), and both `.claude`/`.copilot` `skills/noodle/SKILL.md` now carry the rule: never write a raw credential through a transcript-visible edit/shell tool (`apply_patch`, `echo`) — reference it by `{env:KEY}` and populate the gitignored `<app>_secrets.env` locally, or leave a placeholder. Kept within the 120-line `AGENTS.md` / 165-line skill ceilings by deduping a repeated `.env` sentence. New surface-coverage test in `unit_tests/test_nood_0126.py`.
- Changed: `noodle --help` lists commands alphabetically (NOOD_0126) — Typer's default is definition order, which buried `validate`/`inspect`/`probe`/`rca-report` in a hard-to-scan pile (they were always registered, just not near the top). A one-method `_OrderedGroup(TyperGroup)` override sorts the command list, applied to both the top-level app and the `report` sub-app.
- Fixed: `noodle probe --search` now suggests a stable results-count floor instead of the page's current live count (NOOD_0125) — for any search on any site, `probe._summary_assertion` previously baked the count it happened to observe into the suggested assertion (`Then the number in 'results summary' should be at least <live count>`). Copied verbatim that assertion rots: the next run goes red the moment that site returns fewer results than the snapshot, for no real regression — and the re-run/fix churn is exactly the per-test AIC the framework is tuned to keep under budget. It now emits `should be at least 1` (a genuine "search returned something" floor); the observed count stays visible as context in the render, and the steering line tells the author to raise the floor to match intent (a "more than N items" ask → `at least N+1`), never hardcode the snapshot. This makes any barebones tester prompt of the shape "search for X, make sure results appear" generate a test that stays green across runs regardless of the target site. `docs/steps_dictionary.md` NOOD_0117 note updated; new stable-floor cases in `unit_tests/test_nood_0125.py`, existing probe tests in `test_nood_0117.py`/`test_nood_0119.py` de-snapshotted.
- Changed: the scaffolded `PROMPT_TEMPLATE.md` is now a task brief instead of a second operating manual (NOOD_0125) — its numbered rules 1-8 duplicated the auto-loaded workspace `AGENTS.md` verbatim, riding along on every model call for no benefit (same waste NOOD_0117 removed by killing the `@AGENTS.md` import). `_PROMPT_TEMPLATE` (`noodle/cli.py`) now carries only what an agent can't infer — a one-line pointer to `AGENTS.md`, the task facts (app, base URL, user goal, verify, credentials, shell-output preference), and the both-reports output contract (a red run still includes the compact RCA reason). The procedural `Steps a human would take:` field is gone: the agent owns procedure through `probe_page` + the step dictionary, and demanding hand-written steps only invited guessed selectors. The max-10 dev-loop cap now lives solely in `AGENTS.md` (its canonical home); the drift guard moved with it (`test_nood_0110.py::test_agents_md_caps_dev_loop_at_ten`). Prompt-assertion tests trimmed in `test_nood_0112.py`/`test_agent.py`; new single contract test `unit_tests/test_nood_0125.py`. Docs/strings only, no logic; paste-clean invariants (NOOD_0107) still hold. Existing workspaces refresh with `noodle init --force`.
- Fixed: `When User searches for "..."` now fills a search box only when it is both editable AND visible (NOOD_0123) — on sites that render a unique hidden `<input>` before a visible desktop trigger opens it (Example), `actions.search()` resolved that hidden input editable-first (a unique `find()` match returns without a visibility check, and a hidden input still matches the editable selector), then `fill()` waited out the full Playwright timeout on an element that never became visible. `search()` now treats a hidden-but-editable box as unusable: it clicks a visible trigger — the box itself when it's a visible non-editable icon (the existing NOOD_0106 path), otherwise a generic visible search control resolved without `prefer="input"` — then re-resolves editable-first and requires the revealed box to be visible before filling. Restores the documented one-step search so generated features need no hand-authored trigger step or POM entry. Global `locator.find()` behaviour is unchanged (hidden elements stay valid targets for hidden-state assertions). New regression cases in `unit_tests/test_nood_0106.py`.
- Added: RCA classifies Playwright "element is not visible" action timeouts as a locator-visibility failure (NOOD_0123) — `rca_report.classify()` previously let a `fill()`/`click()` timeout on a hidden target fall through to the `unknown` catch-all, or (when wrapped in a `TimeoutError` traceback) be misread as a framework `test-script` bug. A new rule, checked before the traceback rule, returns `locator-rot` with a fix that names the next action: target the visible candidate, run `noodle inspect`, or — when the step promises trigger handling (`User searches for`) — fix the composite action instead of adding a manual trigger step. New case in `unit_tests/test_rca_report.py`.
- Changed: `noodle probe --compact --search` prints a one-line authoring hint (NOOD_0123) — `author with "When User searches for ..." only; do not add a separate search-trigger step`, so agents stop copying a page's responsive trigger internals into Gherkin. Docs-only string in `probe._search_lines`. The agent playbook's §0 search note is also corrected: a failing one-step search is a framework bug to fix or report, never a cue to decompose it (the old wording implied decomposing was fine "until it fails"). The scaffolded `AGENTS.md` (`cli._AGENTS_MD`, 129→119 lines) and the `noodle` skill card (165→160 lines) were compressed back under their line ceilings to make room without growing what rides along on every model call.
- Added: popup steps now distinguish page DOM overlays, JavaScript dialogs, and browser permission bubbles (NOOD_0122) — three phrasings that previously dead-ended or mis-resolved now work: `closes any and all popups including the geolocation prompt` runs the normal DOM sweep AND denies only that one named permission for the current origin (a bare `close all popups` stays DOM-only and never touches permission state); `accepts`/`allows the <perm> prompt` grants the named permission (opposite of the existing `closes/dismisses the <perm> prompt`); and `types 'X' into the <perm> prompt` is now rejected at resolution with a message pointing to a JavaScript prompt or a page field, instead of silently becoming a DOM fill that hunts for a nonexistent `location prompt` element. Permissions accepted: `location`/`geolocation`, `notifications`, `camera`, `microphone`. `close_popups` gained a `deny_permissions` param (threaded through the runner) that reuses `dismiss_permission_prompt`; the reject path is the resolver extractor raising at match time (no new dispatch branch). `docs/steps_dictionary.md` gains the composite/allow/reject examples and a system-surface table (DOM / JS dialog / permission prompt / new tab / file chooser / download / HTTP auth / Chrome product UI) so authors pick the right step family; Chrome sign-in/password/translate/autofill UI is documented as launch-profile config, not a page step. New `unit_tests/test_nood_0122.py`.
- Fixed: `dismiss_permission_prompt` now decides by browser engine instead of by catching every CDP error (NOOD_0122) — it previously wrapped `new_cdp_session` in a blanket `except Exception` that reported *any* failure (including a genuine Chromium CDP breakage) as a benign "firefox/webkit auto-deny" no-op. It now reads `page.context.browser.browser_type.name`: firefox/webkit stay a logged no-op (their documented auto-deny behaviour), Chromium (or a persistent/None context) drives CDP and lets real failures surface. `grant_permissions` also canonicalizes prompt aliases through the one permission map (`location`→`geolocation`, singular `notification`→`notifications`) so `accepts the location prompt` reaches Playwright as the valid `geolocation` name, while non-prompt names like `clipboard-read` pass through untouched.
- Changed: the scaffolded workspace `AGENTS.md` now forces template-name normalization after the `sample_app/` copy (NOOD_0121) — copying the sample hands an agent working glue AND placeholder names (`login.feature`, sample POM keys/labels), and nothing told it to rename them, so a checkout-flow test could ship green under `login.feature` with sample labels: correct behaviour, misleading artifacts. `_AGENTS_MD` (`noodle/cli.py`, the single source the workspace `AGENTS.md` is generated from) gains a normalize-before-authoring pointer on Quick-path step 1 and a new "Template normalization" section — a rename checklist (feature filename, feature/scenario titles, app-defined `environments.yaml` keys with framework keys like `BASE_URL` explicitly left unchanged, POM file names/keys), a hard-stop line, and a review/PR reject check for leftover generic template names. Docs/strings only, no logic; existing workspaces refresh with `noodle init --force`.
- Changed: `noodle probe` and `noodle inspect` are now surfaced on every agent always-on surface (NOOD_0120) — agents were hand-writing throwaway raw Playwright to look at pages and debug locators because the commands built to replace those scripts weren't on the files every client reads. `noodle probe` (NOOD_0113) was missing from the root `.github/copilot-instructions.md` digest (the only file guaranteed in Copilot CLI / VS Code Copilot / VS Code context); `noodle inspect` (NOOD_0115) was missing from *every* surface — the digest, both noodle skills, and the scaffolded workspace `AGENTS.md`. Fix is docs/strings only, no logic: a "never hand-write Playwright — probe before authoring, inspect to debug a locator" rule + edge-case bullet in `copilot-instructions.md`; an `inspect` line at the locator-resolution section of both `.copilot`/`.claude` `skills/noodle/SKILL.md` (source of truth `_copy_skills` propagates into every workspace); and an `inspect` line in `_AGENTS_MD` step 6 (`noodle/cli.py`), which regenerates the workspace `AGENTS.md` the workspace `CLAUDE.md` points at — covering the Claude CLI too. Existing workspaces refresh with `noodle init --force`.
- Added: secret values are now redacted from all run output (NOOD_0118) — every value loaded from a `*secrets.env` file (or Azure Key Vault) is registered with a logger-level filter that scrubs it from the console, the run's log file, and the captured-warnings buffer that feeds the RCA report, replacing it with `***`. `runner._safe_repr` only masked variables whose *name* looked sensitive (`token`/`password`/…); a secret stored under a bland name (a `DB_CONN` connection string, a script that echoes a password) leaked in full. This masks by *value* — the source is the signal, not the name. Registration lives in `hooks._load_secrets` / `secrets_akv._apply`; the set is cleared per run in `before_all`; placeholder/short values (`CHANGE_ME`, <4 chars) are skipped so real log lines aren't garbled. New `noodle/log.py` redaction machinery + `unit_tests/test_nood_0118.py`.
- Changed: per-app secrets scaffolding writes the gitignored working file, not a committed `.example` (NOOD_0118) — `noodle repl`'s generate (`scaffold_one("secrets")` and the first-test `_scaffold_resources`) previously emitted `<app>_secrets.env.example`, so every "set up <app>" produced a redundant committed template the agent then had to copy. It now writes `<app>_secrets.env` (gitignored, placeholder keys ready to fill in). The `.example` convention is init/bundled-sample only. `noodle init` now also scaffolds a workspace `.gitignore` (`secrets.env`, `**/resources/*_secrets.env`) so the now-real generated file can't be committed by accident — ceiling: an existing hand-written `.gitignore` isn't merged into. Agent-facing docs (agent-playbook package contract + secrets rule, feature-packages, workspace-guide, codebase-spec, cli help, both agent skills) updated to match; bundled `sample_feature_tests` committed `.example` files and their README `cp` bootstraps are unchanged.
- Added: proactive DOM probe `probe_page` / `noodle probe [--json] [--timeout N]` (NOOD_0113) — one headless page-load *before* authoring returns every actionable control (visible and hidden trigger zones like `.trigger-dev-panel` hitboxes) with a ready CSS selector, a `needs_pom` flag with paste-ready POM YAML for controls generic steps can't name, a vocabulary-shaped suggested step per control (unit-enforced to match the pattern table), exact heading texts for verbatim assertions (`Branch #12`, not `branch#12`), and same-origin `next_pages` candidates; space/comma-separate several URLs to probe them in one browser. Kills the author-blind → run → RCA → hand-probe-with-raw-Playwright → fix-POM → re-run lap that burned 100+ agent interactions per simple test on SPA/Angular sites. New `noodle/agents/web/probe.py` (collector JS + pure-Python summarizer; class-only elements get a `[class~=…]` token selector so framework state classes can't break it, attribute-less buttons fall back to `text=`), `core.probe_page`, MCP tool + probe-first step 0 in the server instructions; probe-first taught in scaffolded `AGENTS.md`/`PROMPT_TEMPLATE.md`, agent-playbook (SPA field note 0 + dev-loop), llm-performance, mcp-guide, cli-reference, manual, README, and both agent skills.
- Changed: `NOODLE_DEV_FIX_ATTEMPTS` default raised 5 → 10 and the scaffolded `PROMPT_TEMPLATE.md` rule "run headless with retries=0 until green" now carries an explicit cap (NOOD_0110) — "until green" was unbounded, so an agent that never converged could loop forever; every reference (config default + fallback, `.env` stub, AGENTS.md, agent-playbook, llm-performance, glossary, unit tests) moved to 10 so the two scaffolded files can't quote different caps.
- Added: "SPA field notes" to the scaffolded `AGENTS.md` and `docs/agent-playbook.md` §4 (NOOD_0110) — the five stalls observed driving a weaker model (GPT 5.3-class) against a real Angular site, each with its one-shot recipe: dictionary-valid steps naming labels the page never renders (use the exact visible label or pin with `{pom:key}`), custom non-native dropdowns (keep the one-step `selects … from … dropdown`; the engine's non-`<select>` fallback clicks the option itself), in-DOM-but-not-interactable timing (actions already auto-wait; gate transitions with `waits until … appears`, never sleeps), pointer interception by overlays (auto-dismiss + retry + ⚠️ RCA warning already handles it), and selector-specificity drift (promote healed/visible-disambiguated locators into POM entries in one pass after first green instead of iterative tightening).
- Changed: README slimmed from 2,388 to ~770 lines (NOOD_0110) — the front door keeps what it is, how it works, and the copy/paste quickstarts (macOS / Windows 11 / CI / agent + MCP); everything deeper (setup Parts 1–7, first test, RCA, syntax, LLM augmentation, `noodle repl`, CI, quick reference, troubleshooting) moved verbatim to the new `docs/manual.md`. All intra-repo links and anchors were rewritten and verified (inbound links from mcp-guide, encyclopedia, workspace-guide, external-site-walkthrough retargeted; one pre-existing dead anchor to the visual-steps section fixed).
- Changed: `docs/architecture.md` Level ③ locator hierarchy brought up to date (NOOD_0110) — the diagram and numbered list now reflect the shipped chain: POM before accessibility (NOOD_0008), smart-wait poll with settled-page early exit (NOOD_0103), visible-match narrowing on ambiguity (NOOD_0106), and the full self-heal chain (scroll → POM retry → partial word → auth-verb synonyms → DOM attribute scan, NOOD_0089/0109) ahead of vision LLM and the opt-in OCR coordinate fallback.
- Changed: scaffolded `AGENTS.md` opens with a 6-step "Quick path" checklist and `PROMPT_TEMPLATE.md`'s agent rules are numbered lines instead of one long sentence (NOOD_0110) — smaller/cheaper driving models (GPT-mini/Haiku class) weight the top of an instruction file and drop clauses from long sentences; the full rationale sections remain below as on-failure reference. Paste-clean invariants (NOOD_0107 tests) still pass.
- Fixed: `docs/llm-setup.md` referenced a nonexistent `noodle generate --llm` command (NOOD_0110) — now names the real LLM-generation entry points (`noodle repl --llm` / MCP `generate_test(use_llm=True)`).
- Added: POM orphan-key lint (NOOD_0109) — `noodle validate` (with and without `--resolve`) now warns when a POM key ends in a trailing noun the step patterns strip before lookup (field/box/input/button/link/checkbox/radio): a key authored as `asset tag field:` serves "enters X in the asset tag field", but the fill pattern extracts locator `asset tag`, so the key silently never matched and nothing warned. Keys a sibling `.feature` references quoted (`"number input"`) or explicitly (`{pom:key}`) keep their suffix at lookup time and are skipped, scoped per app package so one app's quoted usage can't mask another's orphan. `dropdown`/`menu` are deliberately not flagged — the select patterns keep them in the locator, so a rename would break those keys. The shipped samples' own orphans (practicetestautomation's `username field:`/`password field:`, busterblock's `search field:`) are fixed, found by the new lint.
- Added: automation-prefixed class tokens are strong DOM-scan signals (NOOD_0109) — plenty of teams tag automation hooks as CSS classes instead of `data-testid`, with a recognizable prefix (`e2e_dev-panel_device-type_dropdown` was an SPA dev panel's only hook: no id/testid/aria/title/placeholder, so `dom_scan._score()`'s class-only-scores-0 rule made the element unfindable). A class token matching `e2e|qa|test|cy|pw|automation|hook|tid|sel` + `-`/`_` now scores like an id, including for the strong-hit gate; the returned selector targets that class alone with `[class~=...]` so framework state classes (ng-pristine → ng-dirty) can't break it between scan and click. Curated per ponytail — `auto-`/`ci-`/`dev-` deliberately excluded as collision-prone. Plain styling classes still score 0 alone.
- Added: auth-verb synonym tier in the self-heal chain (NOOD_0109) — "clicks the login button" failed against a button whose only text is "SIGN IN": zero token overlap, and a single-token phrase like `login` had no heal tier left (dom_scan needs 2+ tokens, partial-text needs a multi-word phrase). A small curated map (login → sign in/log in/signin, logout → sign out/log out, register → sign up) is now tried as alternate text between partial-text and DOM scan, whole-word substituted so "member login" heals too; only a unique match is accepted, and cheap probes (`heal=False`) skip it. Deliberately not a general synonym engine — these few verbs are near-universal step vocabulary mapping to wildly different real labels.
- Changed: workspace `PROMPT_TEMPLATE.md` is now paste-clean (NOOD_0107) — every line flush-left, one field per line, no markdown indentation and no hard-wrapped sentences, so it pastes intact into a code block, a Teams/Slack chat, or any plain-text editor (the old template's indented list items and mid-sentence line breaks arrived mangled). Unit tests pin the invariant so future template edits can't regress it.
- Changed: the scaffolded `CLAUDE.md` pointer now uses Claude Code's `@AGENTS.md` import instead of a plain markdown link (NOOD_0107) — Claude Code auto-loads `CLAUDE.md` on launch and inlines `@`-imports, so an agent started inside the workspace gets the full `AGENTS.md` rules even when the user pastes no prompt at all (a bare link was only a suggestion the model routinely ignored). Copilot CLI and VS Code Copilot already read `AGENTS.md` natively (`chat.useAgentsMdFile`, on by default), so the pair of plain files covers all three hosts on macOS and Windows 11 with no symlinks and zero runtime cost to the engine.
- Added: `noodle report serve --background`/`-b` (NOOD_0104) — starts the report server as a detached process, prints both URLs once the bind has actually succeeded, and exits 0, so an agent's shell tool gets the links from a command that returns instead of a foreground server it has to background itself and then curl-probe. The launcher reuses the NOOD_0089 pidfile as its readiness signal (the child registers `{port: pid}` only after binding, so `-p 0` reports the real port), a bind failure surfaces the child's exit code plus the tail of `.noodle/report_server.log`, and `noodle report stop` tears the detached server down like any other. `docs/agent-playbook.md` §5 + checklist and both agent skills (`.claude`/`.copilot` `skills/noodle/SKILL.md`) now steer CLI-driving agents to it — the Copilot skill's report section was additionally still teaching the pre-NOOD_0082 flow (`report generate` + `report open`, the latter an unflushed foreground server that also expects a desktop browser), which is precisely the hoop-jumping observed in Copilot sessions; it now hosts the pair via `report serve --background` like the Claude skill.
- Fixed: `noodle report serve`'s "Serving … → http://…" lines were invisible to any agent/script that captured its output (NOOD_0104) — with stdout piped rather than a TTY, Python block-buffers `print`, and since `serve_forever()` never returns, the buffer never drained. Coding agents (observed with Copilot) backgrounded the command, saw an empty pipe, and burned a diagnostic loop guessing at stderr/buffering before curl-probing port 8000 to confirm the server was even up. The banner and URL lines now flush immediately.
- Added: settled-page early exit for ALL element finds (NOOD_0103, `NOODLE_SETTLE_TIMEOUT`, default 15s) — the smart-wait poll exists because an element might still be rendering, but it previously kept polling the full `NOODLE_FIND_TIMEOUT` (~2 min) even after the page had demonstrably finished. Now, once the settle timeout has elapsed AND the network has been quiet AND the DOM fingerprint (element count + text length) has stopped changing across consecutive samples, the poll concludes the label is never going to resolve on this page and returns early, handing over to the self-heal chain (scroll, POM, partial text, DOM scan, vision) in seconds instead of minutes. This is the universal fix for "the page is fully loaded, why is the find still waiting?" — it covers every label/selector on every action, with zero configuration. A page still fetching data or still mutating its DOM is never considered settled (a genuinely late element is still caught), a scope that can't page-evaluate (row/section/iframe Locator scopes) conservatively keeps the old full-budget behaviour, and `NOODLE_SETTLE_TIMEOUT=0` disables the exit entirely. Documented in the scaffolded `.env` stub and `docs/agent-playbook.md`.
- Fixed: multi-probe element finds no longer burn the full smart-wait budget on a doomed early probe (NOOD_0103) — an action written as "try key A, else key B" chained two `find()` calls, and each early probe that could never match on a given page (e.g. `search()`'s hardcoded `searchbox` key ahead of the POM's `search`) still polled the entire `NOODLE_FIND_TIMEOUT` (~2 min, plus a network extension) before falling through. `search()` hit this on every run: a page whose POM defines `search` but not `searchbox` spent minutes on the doomed probe before the real key was tried. New engine primitive `locator.find_first(candidates)` reserves the full budget + self-heal chain for the LAST candidate only; every earlier candidate gets one cheap pass (via a new `heal=False` flag on `find()`/`_find()` that also skips vision-first full-LLM mode) and falls straight through on a miss. General fix — covers every multi-probe action, present and future, not just `search()` — so no per-workspace POM alias is needed.
- Added: `docs/agent-playbook.md` and the scaffolded `AGENTS.md` popup sections now distinguish native browser permission prompts (geolocation, camera, mic, notifications) from in-page popups (NOOD_0103) — they are browser chrome, invisible to `closes the popup if it appears`, and are dismissed with `the user closes the location prompt` or granted up front via the `@permissions:…` tag / grant step.
- Fixed: `make install-ext`/`code --install-extension` could silently no-op, leaving `.feature` files uncoloured even after a full VS Code quit and reopen — the sideloaded extension's manifest version (`vscode-extension/package.json`) never changes between releases, so VS Code saw "same version already installed" and skipped the reinstall. Both the Makefile target and the README's manual/Windows install commands now pass `--force`. README also gains a dedicated Part 3 diagnostic ("still plain black-and-white after a full quit and reopen") and a matching Troubleshooting entry, distinguishing this from the separate LSP-squiggle-staleness issue.
- Added: README "Zero to hero — let your agent install it" — a pair of paste-able prompts for Claude Code/Copilot/Cursor: an install-only one (checks what's already on the machine first, installs prerequisites + the framework + the VS Code extension via Setup guide Parts 1-3, verifies syntax highlighting actually renders, and explicitly does not run or generate any test) and the existing full install-plus-run one, kept side by side. Surfaced in the Contents TOC and the Quick answers/Quick links FAQ.
- Changed: `noodle init` now scaffolds `.env` with `NOODLE_IGNORE_HTTPS_ERRORS=true` set explicitly (NOOD_0094) instead of commented out — sites under test usually live in dev/sandbox environments, so TLS + self-signed/invalid cert errors are ignored out of the box in every browser (this was already the engine default since NOOD_0089; the change just surfaces it as an editable line in a fresh workspace). `@secure_certs` per scenario or `NOODLE_IGNORE_HTTPS_ERRORS=false` run-wide still turns validation back on.
- Added: `NOODLE_DEV_FIX_ATTEMPTS` (default 5, `config.dev_fix_attempts()`) — a token-cost ceiling on the agent's test-development loop (NOOD_0094). When a just-generated step/scenario fails on mechanics (element not found, ambiguous locator, find-timeout), the driving agent fixes the cause and re-runs, up to this many attempts, then stops and reports *why the test is flaky* rather than burning tokens. Documented in the scaffolded `AGENTS.md`, `docs/agent-playbook.md` §5 + checklist, and the glossary env-var table. It is explicitly a mechanical-failure loop, not a green-forcing retry — a real app/assertion failure is still root-caused, never masked.
- Changed: `noodle run` no longer auto-archives the previous run (NOOD_0093) — the `--archive`/`--no-archive` flag and the per-run zip into `archives/` are gone; runs simply overwrite `artifacts/` in place. The Allure trend history (`reports/allure-history/`) already survives the wipe and carries prior-run trends into the new report, so the zip was redundant for reporting. `noodle archive` (manual) and `noodle report serve <stamp>` remain for keeping a specific run's full tree (screenshots/traces) on demand.
- Fixed: served/opened reports could still be cached by Chrome (NOOD_0093) — the report server now sends `Cache-Control: no-store` instead of `no-cache` (`no-cache` still lets the browser *store* the response and reuse the Allure SPA's data JSON across regenerated reports on the same port), and `noodle report open` now serves the report over that same no-store http.server + browser instead of `allure open`, whose bundled Node server sent cacheable headers we couldn't control. One serving path, one cache policy. Supersedes the NOOD_0089 `no-cache` change below.

- Fixed: report server now sends `Cache-Control: no-cache` (NOOD_0089) — it previously sent only Last-Modified, so browsers applied heuristic caching (~10% of the file's age) and kept rendering a days-old rca.html/Allure page for hours after the files were rebuilt, without ever revalidating.
- Added: `noodle report stop [--port N]` (NOOD_0089) — kills hosted Allure/RCA report servers from any terminal (Windows included — `os.kill(pid, SIGTERM)` maps to TerminateProcess there). `report serve` registers its pid in the workspace's `.noodle/report_servers.json` only after a successful bind (via `serve_report`'s new `on_bound` callback, so `-p 0` records the real port), and unregisters only its own entry — a serve losing the port race can't pop the live server's pid. `stop` SIGTERMs the recorded pids (all, or one `--port`) and prunes entries whose process is already gone.
- Fixed: served reports could mix two different runs (NOOD_0089) — `report serve` (CLI and MCP `serve_report`) only rebuilt *missing* report files, so a stale `rca.html` from an old workspace-wide run (other apps' failures included) could be hosted next to a newer, all-green Allure report. Both now rebuild whenever they are older than the newest result JSON (`builder.ensure_fresh_reports`).
- Added: RCA provenance column (NOOD_0089) — every RCA table (failures and passed-with-warnings, markdown and HTML) now has an "App / .feature" column naming the app package and the .feature file each row came from; result JSON carries a new `featureFile` label (older results render the column empty).
- Added: smart element waiting (NOOD_0089) — element finds now poll a dedicated `NOODLE_FIND_TIMEOUT` budget (default 2 min; a ceiling, not a wait — steps proceed the instant the element appears), decoupled from `NOODLE_TIMEOUT` (per-action, 10s). While waiting, the engine periodically re-scans the DOM for attribute-token matches (the "maybe I have the wrong selector" self-heal), and at the deadline grants ONE bounded `NOODLE_WAIT_EXTENSION` (default 30s) when network traffic shows the page genuinely still loading — analytics/telemetry/heartbeat URLs are filtered so chatty pages can't wait forever (`noodle/agents/web/activity.py`).
- Added: DOM attribute scanner (NOOD_0089, `noodle/agents/web/dom_scan.py`) — a locator tier between the accessibility tree and the vision LLM that walks the real DOM (hidden elements INCLUDED), scores id/name/data-testid/aria/title/placeholder/class tokens against the step phrase, and returns a CSS selector. Finds machine-identity targets like `<div id="dev-panel">` that have no role, label or visible text; a present-but-invisible target found this way gets one force-click with a ⚠️ warning + `hidden-force-click` healing event.
- Added: smart overlay handling (NOOD_0089) — when a click times out because an overlay "intercepts pointer events", the engine auto-dismisses popups/cookie banners/promo modals and retries once, recording an `overlay-dismissed` healing event and a ⚠️ warning that the RCA report surfaces even on green runs ("a popup was closed for you — verify it wasn't the point of the test"). `NOODLE_AUTO_DISMISS=false` turns it off; `close_popups` learned more real-world dismiss shapes (role=dialog close buttons, "No thanks"/"Not now"/"Maybe later"/"Got it").
- Added: certificate errors ignored by default in ALL browsers (NOOD_0089) — `ignore_https_errors` is now set on every browser context (chromium/firefox/webkit + safari/edge aliases), since most sites under test live in uncertified dev sandboxes. Opt back into validation with `NOODLE_IGNORE_HTTPS_ERRORS=false` run-wide or the `@secure_certs` tag per scenario.
- Added: `noodle init mcp` / `noodle init-mcp` (NOOD_0089) — writes/merges the noodle server into `.mcp.json` (Claude Code) and `.vscode/mcp.json` (VS Code Copilot); existing JSON is merged, never clobbered. In Azure DevOps (`TF_BUILD`)/CI the files are still written (committable for the team) with a note that pipelines call the CLI directly.
- Changed: `noodle init` on an EXISTING workspace is now the upgrade path (NOOD_0089) — engine-glue files (`environment.py`, `z_catch_all.py`, report README) auto-sync to the installed engine; drifted template files (AGENTS.md, README, samples, PROMPT_TEMPLATE.md) are reported and refreshable with `--force` (originals → `*.bak`); config files (`.env`, `noodle.yaml`, `pom.yaml`) are never touched, keeping team-owned workspaces backwards compatible.
- Added: MCP `read_docs` tool (NOOD_0089) — token-lean framework lookup for driving agents: list docs with one-line summaries, fetch one by name, or grep a query across `docs/*.md`, instead of pasting docs into prompts or guessing.
- Added: workspace `PROMPT_TEMPLATE.md` (NOOD_0089) — a fill-in-the-brackets test-generation prompt (app, user goal, human steps, verification, popup policy) users copy into Claude Code / Copilot / any MCP host; scaffolded `AGENTS.md` gains the north-star sections (definition of success, token-economy output discipline, prerequisites-in-`Background:` rule with the wrong/right example, popup policy, custom scripts in `resources/scripts/`), mirrored in `docs/agent-playbook.md` §0 and `.github/copilot-instructions.md` (rules 7–8, targeting Copilot's per-step narration verbosity).

- Added: README gains a "Quick answers — for testers & managers" FAQ (LLM mode meaning/default, which tool to actually use day to day, how step resolution works, running a test from a separate workspace) and two new "How it works" Mermaid diagrams tracing MCP mode vs. manual-CLI mode start to finish — new test, edit a test, custom-script step, variable/step resolution, running, and RCA+Allure reporting — replacing the two smaller diagrams they made redundant. Setup guide Part 2 gains an up-front decision table so the permanent-PATH install (`uv tool install`, already identical on macOS/Windows 11) isn't buried under the per-terminal `.venv activate` path (NOOD_0078).
- Changed: the "Zero to hero" copy/paste path and Setup guide Part 7 now generate and serve the RCA report (`noodle rca-report --out artifacts/reports/rca.md --serve`) alongside the Allure report — both were previously Allure-only, contradicting the mandatory "always generate + open both reports" rule in `docs/agent-playbook.md` (NOOD_0076).
- Added: the "Zero to hero" copy/paste path now installs the VS Code LSP extension (`make install-ext`, per Setup guide Part 3) — without it, `.feature` files opened in VS Code show no step-validation squiggles at all (not even the `llm-fallback` warning), which read as "steps not found" with no indication the extension was never installed (NOOD_0076).
- Fixed: `make install-ext`/`make vsix` crashed on every machine except the original author's with ` ERROR  currentLevel is undefined for <user> in .../vscode-extension/vscode-extension` — `vscode-extension/vscode-extension` was a symlink accidentally committed back in NOOD_0026, pointing at a dead absolute path on the original author's laptop (`/Users/gheeno/Projects/bddframe/vscode-extension`, predating the repo's rename to "noodle"). `vsce`'s file-size walker resolves it as a folder named after itself and throws. Removed the symlink; also pinned the `vsix` Makefile target to `npx @vscode/vsce` (not bare `vsce`, which can resolve an old abandoned unscoped package) with `--allow-missing-repository --skip-license` so it no longer stalls on interactive prompts (NOOD_0076).
- Added: README now documents putting `noodle` permanently on `PATH` via `uv tool install --editable ".[all]" ... && uv tool update-shell` (Setup guide Part 2), plus the `noodle: command not found` failure mode when the tool bin dir isn't on `PATH` yet or the terminal predates the shell-profile edit — previously only the per-terminal `.venv` activate path was documented (NOOD_0076).
- Fixed: the `@visual` agent could not run its own documented steps. `visual_patterns.py` was authored in bare infinitive (`^click image`) while the rest of the engine canonicalizes every step to third person via `normalize_subject`, and `execute_visual_step` received raw step text — so `When I click image "x.png"` (the form every doc showed) never matched; only the subjectless `When click image "x.png"` did. All 19 visual patterns moved to the canonical third-person form, with the trailing `s`/`es` optional so the bare form still resolves (NOOD_0067).
- Fixed: `{env:}`/`{var:}` references were never substituted in `@visual` steps — `visual_runner.py` never called `runner.substitute()`, so `{env:FILE_PATH}` reached the OS keyboard as eleven literal characters, while `steps_dictionary.md` documented substitution as universal. Visual steps now get the same prep as web: substitution, then subject stripping (NOOD_0067).
- Fixed: `noodle validate`, the MCP `validate_feature` tool and the LSP graded `@visual` features against the *web* pattern table — real visual steps (`press key "enter"`, `scroll down 3 times`) were flagged "no pattern matched, LLM will resolve at runtime", while others (`click image "x.png"`) were silently passed as a **web** action they would never execute as. Added `noodle.resolver.match_step(text, visual=)` as the single entry point for both tables; validate, the LSP (diagnostics *and* hover) and the runtime now all route through it, and the LSP tracks tags per scenario to mirror the runtime's routing (NOOD_0067).
- Added: a "Visual / Desktop Steps — @visual" section in `docs/steps_dictionary.md`, which billed itself as "the full built-in step reference" while documenting none of the 19 visual steps (NOOD_0067).
- Fixed: the `@visual` example in `docs/encyclopedia.md` had 3 of its 4 steps unable to resolve, and claimed web and visual steps "can mix in one scenario; the orchestrator switches agents per step" — they cannot: `steps/catch_all.py` reads the tag once per scenario and routes the whole scenario to one agent (NOOD_0067).
- Fixed: `_example_corpus()` (which feeds the web alias-regression scan, `example_index` and the "did you mean" suggestions) now skips `@visual` gherkin blocks — it resolves every example against the web table, so the newly documented visual vocabulary would otherwise read as unresolvable web steps (NOOD_0067).
- Added: 84 tests covering every visual step in every subject form (`""`/`I `/`User `/`The user `), the substitution path, and visual-awareness of validate/LSP — `test_visual_patterns.py` previously only asserted the subject-stripped form, so the tests encoded the bug and 867 of them passed over a feature that did not work as documented (NOOD_0067).
- Added: directory run targets for the MCP/REPL resolver — `run_test("tests/web/busterblock")` (and bare dir names like `busterblock`) now resolve; `find_feature` only accepted `.feature` files, so the MCP tools couldn't run a suite folder the way `noodle run <dir>` always could (NOOD_0065).
- Added: `@llm`-tagged scenarios auto-skip when `NOODLE_MODEL` is unset — the README always promised this, but the two LLM demo features actually *failed* on every no-LLM run; skip message points at the Ollama setup docs (NOOD_0065).
- Changed: LLM-mode docs now name the two AIs explicitly — the coding agent driving via MCP (Claude Code CLI, Copilot CLI) vs the engine-side `NOODLE_MODEL` the resolver calls mid-run — and the modes are labelled Assisted (`auto`) / Pure (`full`); README gains a tester MCP quickstart, a copy-the-bundled-samples-into-your-own-workspace walkthrough, and a "Pure LLM mode with a coding agent" generate → run → harvest-suggestions loop; mcp-guide §5 documents the same loop; Ollama recommendations updated to `qwen2.5vl:7b` / `qwen2.5-coder:7b` per docs/llm-setup.md (NOOD_0065).
- Fixed: the LSP's `llm-fallback` diagnostic only ran step text through `normalize_subject` before matching, not `normalize_phrasing` like the real runtime resolver (`step_resolver.py`) — 35 steps across the sample `.feature` files were flagged as needing the LLM when they actually resolve fine at runtime. Also taught the diagnostic to substitute Scenario Outline `<name>` placeholders from the first `Examples` row before matching, since Behave only substitutes them at runtime (NOOD_0063).
- Fixed: `# llm-ok` used as a *trailing* comment on a step matched by a custom `@given`/`@when`/`@then` decorator broke the match — Gherkin doesn't strip end-of-line comments, so the comment text became part of what Behave matched against. The marker now also works as a standalone comment line directly above the step, which is always Gherkin-safe (NOOD_0063).
- Fixed: `noodle record`'s `--output` default was a hardcoded `"tests/recorded.feature"`, ignoring the workspace's configured `tests_dir`, and the command had no `--workspace` option unlike every other CLI command — surfaced by this repo's own `tests/` → `sample_feature_tests/` rename (NOOD_0062), where it would have silently created a stray, undiscoverable `tests/` folder (NOOD_0063).
- Changed: `assert_compare`'s failure message no longer echoes the raw operator ("Comparison failed: 'X' contains 'Y' is not true (compared as text)" reads as if X were the assertion, when X is actually the value under test) — now states the relation and which side failed explicitly, e.g. `Expected 'X' to contain 'Y', but it did not (compared as text)` (NOOD_0063).
- Added: `server_info` MCP tool + a startup identity line on stderr (`noodle-mcp <version> pid=… workspace=… started=…`) — a long-lived server never hot-reloads, and until now nothing revealed that a running process predated the last deploy (NOOD_0057).
- Added: optional `workspace` parameter on every MCP tool — one `noodle-mcp` server can now drive several test repos per call instead of one process per workspace. stdio accepts any path (the spawning host is already trusted); streamable-http requires overrides to fall under a `--workspace-root` directory (new repeatable flag), else remote callers stay locked to the startup workspace (NOOD_0057).
- Added: `noodle run` auto-archives the previous run's `artifacts/` to `archives/artifacts_<stamp>.zip` before overwriting it (`--no-archive` to skip) — a run's evidence is never silently lost (NOOD_0052).
- Added: `--workspace`/`-w` on `noodle report generate`/`open`/`serve` — no more `cd` into the workspace first (NOOD_0052).
- Added: `safari` and `edge` browsers (`--browser`, `NOODLE_BROWSER`, `@safari`/`@edge` tags) — Safari maps to Playwright's WebKit engine, Edge launches Chromium through the installed `msedge` channel (NOOD_0052).
- Added: agent skill at `.claude/skills/noodle/SKILL.md` + `.copilot/skills/noodle/SKILL.md` — the agent playbook condensed into an installable skill for Claude Code and Copilot CLI, with install instructions inside (NOOD_0052).
- Added: `noodle repl` rule-based compound-request splitter — "create a test for X at Y, run it and show me the report" now works with no LLM configured; also stops the single-create regex from capturing a trailing comma in the URL (NOOD_0052).
- Renamed: `noodle-agent` → `noodle repl` (package `noodle/agent/` → `noodle/repl/`) — it's a keyword-matched command shell, not an autonomous AI agent, and the old name misled readers into expecting one; see `docs/design-history.md` Phase Y (NOOD_0056).
- Changed: the standalone `noodle-repl` console script is gone — the interactive shell is now `noodle repl` (a subcommand of `noodle`, same as `noodle run`/`noodle validate`), so there's exactly one installed binary instead of two (NOOD_0056).
- Fixed: `test_cli_hardening` asserted `safari` was an invalid browser — once safari became valid the test launched a real 110-second `noodle run` from the repo root during the unit suite, clobbering `artifacts/`; browser-validation tests now use a still-invalid name and pass `--no-archive` (NOOD_0052).
- Fixed: `noodle run --parallel` exit code now derives from the merged results — behavex was observed returning 0 with failed scenarios, which would let CI go green on a red run (NOOD_0052).
- Fixed: `allure` binary resolved via `shutil.which` — `report generate`/`open` now work on Windows (npm installs `allure.cmd`, which `subprocess` won't launch by bare name) and skip with a note instead of crashing when allure isn't installed (NOOD_0052).
- Fixed: `tests/environment.py` engine glue restored (was empty — behave never wired the framework hooks for in-repo runs) (NOOD_0052).
- Fixed: `noodle.log.attach_file_handler` could leave a duplicate `FileHandler` on the logger if one was ever attached outside its own replace path — it now clears every `FileHandler`, not just the one it's tracking.
- Added: `ruff` lint gate (`make lint`, `pyproject.toml [tool.ruff]`).
- Added: `LICENSE` (MIT), `CONTRIBUTING.md`.
- Fixed: `execute_step` in `noodle/orchestrator/runner.py` used `getattr(context, "_vars", None)` directly instead of the local `ctx_get` helper, inconsistent with the rest of the module.

## [0.1.0] — 2026-07-10

Baseline: Gherkin-only test authoring, Playwright web agent, step resolver + optional LLM fallback, Allure 3 + RCA reporting, `noodle-agent` REPL, `noodle-mcp` server, VS Code extension, mobile (Appium) and visual (OpenCV/OCR) agents.

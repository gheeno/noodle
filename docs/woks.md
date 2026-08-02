# Woks — Noodle's capability work areas
<!-- Branch: NOOD_0155 -->

> **For:** everyone — the concept doc for Noodle's five testing domains.
> Architecture context: [architecture.md § 2](architecture.md#2-the-component-map).

A **wok** is a self-contained capability domain — an area where Noodle can
perform testing. The name is a pun on **WO**r**K** area that fits the noodle
kitchen: each wok is one cooking station with its own heat source (engine),
its own vocabulary of verbs, its own optional dependencies, samples and unit
tests — and a dish can be cooked across several woks at once
([cross-wok composition](#cross-wok-composition)).

"Wok" is one of Noodle's three canonical nouns — **engine** (the framework,
this repo), **workspace** (the test project `noodle init` scaffolds), **wok**
(a capability work area cutting across both) — defined once in
[glossary.md § The three nouns](glossary.md#the-three-nouns--engine-workspace-wok).
"Update our noodle wok mobile" means: extend the mobile capability in the
engine and its per-wok tests, not a workspace's test suites.

Noodle is a **universal** BDD test framework, not a web-UI one. There
are five woks, each standing alone:

| Wok | Tests | Engine(s) | Routing tags | Extras |
|-----|-------|-----------|--------------|--------|
| **Web** | browser UI, canvas/terminal-style UIs | Playwright · OCR pixel bridge | `@web` (default), `@terminal` | none — core install |
| **API** | REST services, browserless | stdlib REST client — no browser, no driver | `@api` | none — core install |
| **Mobile** | native Android/iOS apps on device/emulator | Appium (UiAutomator2 / XCUITest) | `@appium`, `@android`, `@ios` | `noodle[mobile]` |
| **Desktop** | native Windows/macOS apps, terminal UIs, spreadsheets — "complex UIs" | Visual agent (OpenCV + OCR + PyAutoGUI) · Appium (WinAppDriver / Mac2) · stdlib `.xlsx` reader | `@visual`, `@windows`, `@mac` | `noodle[visual,desktop,mobile]` |
| **Performance** | HTTP load, latency/error/throughput gates | built-in threaded load generator (stdlib) | `@perf` | none — core install |

Run `noodle wok` to list them (with per-machine install status), or
`noodle wok desktop` for one wok's detail. The registry is code —
`noodle/wok.py` — and `unit_tests/woks/test_wok_registry.py` pins its routing
to what `hooks.py`/`catch_all.py` actually do.

**Every wok honours the same four contracts:**

1. **Gherkin** — tests are plain `.feature` files, same parser, same
   catch-all step, same tag conventions ([agent-playbook.md §3](agent-playbook.md)).
2. **Screenshots** — web/mobile/desktop capture the system under test; the
   performance wok renders a latency-over-time chart PNG through the same
   evidence pipeline (`saves the load test report as "..."`).
3. **Reporting** — Allure + RCA on every run, pass or fail, no exceptions
   ([agent-playbook.md §5](agent-playbook.md)).
4. **Isolated unit tests** — each wok's framework tests live in
   `unit_tests/woks/<wok>/`, so capability work on one wok is
   regression-checked alone (`pytest unit_tests/woks/desktop`) without
   cross-contaminating the others. (The broad pre-wok web suite stays in
   `unit_tests/*.py` — the web wok's mature regression net, NOOD_0154.)

---

## The five woks in detail

### Web — the mature wok

What Noodle grew up on: Playwright-driven browser automation with
accessibility-first locators, POM fallback, self-healing, tracing, network
capture, clock control and pre-boot script injection. The pixel/OCR bridge
for canvas and browser-embedded terminal UIs (`@terminal`) rides in this wok
because it genuinely does share the web session lifecycle.

- Samples: `sample_feature_tests/web/` (8 app packages), `terminal/`
- Unit tests: `unit_tests/woks/web/` (boundary guards) + the whole legacy suite

### API

> **The api wok is Noodle testing *someone else's* API.** Don't confuse it with
> the **engine API** — the `/api/*` HTTP surface another system uses to drive
> Noodle itself ([engine-api-guide.md](engine-api-guide.md)). Testing an API vs.
> being called as one; see
> [glossary.md](glossary.md#api-means-two-different-things--say-which-nood_0193).

REST services, **browserless**. `@api` is the one tag that means "no browser
at all": `hooks.before_scenario` nulls the page and returns before any launch,
so an API-only suite runs in a CI image with no Playwright install and no
browser download. Verbs (GET/POST/PUT/PATCH/DELETE), auth (bearer, basic, API
key, OAuth2 client-credentials with one refresh-and-retry on 401), status /
body / header assertions, JSON extraction with variable chaining, payload
files, and per-step timeouts — full reference:
[steps_dictionary.md § REST API Testing](steps_dictionary.md#rest-api-testing).

Until NOOD_0191 this was filed under the web wok, on the grounds that REST
"shares the web session lifecycle". It never did, and the mislabelling had a
real cost: every always-on instruction surface described Noodle as a browser
framework, so an agent asked to write API tests told the user to go use
pytest or Postman instead.

**Authored from a prompt, like any other test (NOOD_0192).** The cheap
deterministic path — plain-English numbered steps → compiled `.feature` —
used to be web-only, so an API test could only be hand-written Gherkin: never
intent-verified, never size-measured, and a different workflow to remember
for the same job.

```
1. GET https://api.example.com/orders
2. Verify the response status is 200
3. Verify the response body contains "total"
```

```gherkin
@api
Feature: GET /orders, status 200

  Scenario: GET /orders, status 200
    When performs a GET call at 'https://api.example.com/orders'
    Then the response status should be 200
    And the response body should contain 'total'
```

An **api-only** prompt (every step an API step) needs no URL step and no
`base_url`: the package is named after the endpoint, `@api` is applied
automatically, **no probe runs and no browser is ever launched** — authoring
an API test works on a machine with no Playwright install. Mix web steps into
the same prompt and you get a cross-wok test instead
([below](#cross-wok-composition)).

Grammar: `GET|POST|PUT|PATCH|DELETE <url>` · `call the api at <url>` ·
`go to <url> via rest` · `create <N> <things> using POST <url> with body
'<template>'` (NOOD_0201 — `{i}` = call number) · `verify the response status
is <code>` · `verify the response body contains <text>`. Headers, auth,
`{var:}` chaining and payload files stay `feature_content` territory — the
full step table is one `read_docs('steps_dictionary', query='REST')` away.

### Discovery: base URL and endpoints without being told (NOOD_0201)

The two facts an API request most often arrives without are the base URL and
the real endpoint paths — and both are discoverable when the dev loop already
hosts the app locally:

- **`noodle api-scan`** (CLI) / **`probe_api`** (MCP) with no URL sweeps the
  well-known localhost ports (plus any the repo's own config names —
  `server.port`, compose port mappings, `PORT=`) and reports every live HTTP
  server with an api-shaped verdict. **Exactly one api candidate is an
  answer**: `author_test` adopts it automatically when a pure-API goal or
  api-only prompt arrives without `base_url`. Anything else comes back as
  `questions` — Noodle still never guesses a URL.
- **`noodle api-scan <base_url>`** / **`probe_api(base_url=...)`** fetches the
  app's OpenAPI document from the well-known routes (`/openapi.json`,
  `/v3/api-docs`, `/swagger.json`, ...) and returns the REAL endpoint list
  with request-body hints and copy-ready steps. This is the fix for authoring
  `POST /greeting` when the app actually serves `POST /greeting/new` — one
  spec fetch settles the path before the test is written.
- **`scan_repo`** stays the static half: OpenAPI files on disk, serve
  commands, stacks. Repo scan says what the app *is*; api-scan says where it
  *answers* and what it *serves* right now.
- **A spec IS an answer (NOOD_0216).** `noodle api-scan` / `probe_api` also
  accept an OpenAPI document directly — a local `openapi.yaml`/`.json` path
  or a spec URL a developer handed over — no live server needed: same
  report, endpoints and copy-ready steps straight off the document (base URL
  from the spec's `servers`, or a question when it names none). Add
  `--suite` / `suite=True` (or `--out <file>` to write it) and the engine
  generates a **runnable @api feature** — one scenario per documented
  operation, expected status from the spec's responses, body hints from its
  schemas; path params and unexemplified bodies come back as visible
  `<placeholders>` plus `questions`, never guesses.

With a discovered (or given) base URL, api steps may use **relative paths**
(`POST /api/greeting`): the compiler emits the `REST_BASE_URL` Given bound to
the app's stored `{env:}` key.

### Batches and typed JSON checks (NOOD_0201)

Seeding N records is ONE step, never N pasted calls — and `expecting status`
asserts **every** call (a trailing status assertion after N pasted calls only
ever checked the last one):

```gherkin
When performs a POST call at '/api/greeting' with body '{"name":"Row {i}"}' repeated 20 times expecting status 201
```

Heterogeneous data rides a Gherkin table — headings name the `{placeholder}`
tokens substituted into the path and body per row:

```gherkin
When performs a POST call at '/api/greeting' with body '{"name":"{name}"}' for each row expecting status 201:
  | name   |
  | Ada    |
  | Grace  |
```

Big payloads read as a docstring (`performs a POST call at '/g' with this
body:` + `"""..."""`), and JSON assertions are typed — `the response json
'data.items[0].id' should equal '42'` compares numbers as numbers, booleans
as booleans (`should contain`, `should have N items` too). Goal grammar:
`{do: api, url, body, rows|repeat, expect_status}` and
`{json: <path>, equals|contains|items: ...}` checks.

### Every message shape, and the evidence to prove it (NOOD_0216)

- **Form, multipart, GraphQL, cookies.** `with form body 'a=1&b=2'` sends
  that one call form-encoded; `uploading the file 'f.png' as 'photo'` is a
  real multipart/form-data upload (browserless twin of the web wok's file
  chooser); a GraphQL query rides a docstring (`performs a graphql query at
  '/graphql':`) with `the response should have no graphql errors` as the
  gate a 200-with-errors response walks straight past; and Set-Cookie
  responses fill a per-scenario, per-host cookie jar automatically —
  Postman behaviour, `clears the rest cookies` to reset. Docstring bodies
  can store the response now, which is also the escape hatch for payloads
  containing a single quote.
- **The api log.** Every scenario's REST traffic — method, URL, status,
  request/response bodies (capped, secrets redacted) — lands on its Allure
  result as the `api log` attachment, the api wok's twin of the web wok's
  network log. Before NOOD_0216 the registry *claimed* this evidence and no
  code wrote it.
- **Goal parity.** The goal `api` action takes `timeout` (per-call budget)
  and `wait_until: {status, contains?}` (compiles to the polling step — the
  wait IS the assertion), and checks take `schema: 'schemas/x.json'` — so
  async endpoints and contract assertions no longer force hand-written
  `feature_content`.

### Waiting, contracts, auth and chaining (NOOD_0201)

- **Polling, not sleeping.** `waits until '/jobs/1' returns status 200 and the
  body contains 'done' within 60 seconds` retries the call until the condition
  holds — the REST twin of the web wok's smart wait. It returns on the first
  satisfying response, and fails naming the budget and the last status. This is
  the answer to an endpoint that answers `202` and finishes the write later.
- **Shape, not substrings.** `the response should match the schema
  'schemas/review.json'` validates the whole body against a JSON Schema file in
  the app's `resources/` (type/required/properties/items/enum/bounds — no
  `$ref`/`oneOf`, deliberately). A field that changed type or vanished passes
  every substring check ever written.
- **Auth and chaining from a goal.** The `api` action takes
  `headers: {...}`, `auth: {scheme: bearer, token: '{env:KEY}'}` and
  `store: {REVIEW_ID: 'id'}` — so a goal can log in, chain the created id into
  the next call's URL, and never drop to hand-written `feature_content`. One
  goal action can compile to several steps; that is why these were previously
  out of reach.

### Authoring from a ticket payload (NOOD_0201)

`noodle ticket <issue.json>` / MCP `plan_from_ticket` turns the JIRA payload an
SDLC agent already holds into one authorable goal per acceptance criterion —
deterministically, no LLM. Atlassian Document Format is walked, `SPEC_LINK` and
sha256 boilerplate discarded rather than read as intent, criteria split into
Given/When/Then, and **each criterion's endpoint resolved against the routes
the service really serves** (discovery supplies them), so a ticket saying
`POST /greeting` authors against `POST /greeting/new`. Criteria the API cannot
show land in `not_automatable` with the reason; what the ticket never said
(the request body) comes back in `questions` behind a visible `<value>`
placeholder. Worked end to end in
[`busterblock_from_ticket.feature`](../sample_feature_tests/api/features/busterblock_from_ticket.feature).

### The API wok is a lifecycle, not a gate

Two facts, and conflating them is what caused that mess:

1. **REST steps are plain I/O, available in every scenario.** No tag, no
   setup, no "API mode" — the same class of thing as `run_command`, a
   spreadsheet read, or a JDBC call to seed a fixture. Read the runner: a
   step is only ever refused when `page is None`, and `rest_*` is exempt
   there too, so REST is never gated at all. Reach for it wherever you'd
   reach for a shell command.
2. **The API wok is the browserless lifecycle**, for suites whose subject
   *is* the API. `@api` means "start no browser" — a CI-image-size and
   startup-cost decision (no Playwright install, no browser download, no
   trace) — **not** permission to use REST steps.

So: writing API tests needs no tag. Tag `@api` when you want the browser
skipped, and only then.

> **Honest gap:** there is no native SQL/DB step family today — the JDBC
> comparison holds conceptually, but database setup currently goes through
> `run_command`/`run_script` (both browserless, so they compose the same
> way). A first-class DB step family is a candidate wok, not a shipped one.

- Samples: `sample_feature_tests/api/` — 5 against a public API, plus 3 against
  the local BusterBlock service (NOOD_0201): `busterblock_api.feature`
  (polling, typed JSON, schema, docstring, payload file, bearer auth, negative
  paths), `busterblock_batch.feature` (batch vs table vs Scenario Outline), and
  `busterblock_from_ticket.feature` (authored from a JIRA payload alone). The
  cross-wok pair lives with the web package:
  `web/busterblock/features/api_seeds_ui_verifies.feature`. All of them need
  BusterBlock running (`cd test-apps/busterblock && npm start`).
- Unit tests: `unit_tests/woks/api/`, `unit_tests/test_nood_0201*.py`

### Mobile

Native apps on a real device or emulator via **Appium** — the de-facto
standard, already wired in since NOOD_0032: `@android`/`@ios` imply `@appium`
and pick default capabilities (`NOODLE_ANDROID_APP` / `NOODLE_IOS_APP`).
The common step family (tap/fill/swipe/long-press/hide-keyboard/
background/screenshot/assert-visible…) routes through
`agents/mobile/`. Setup: [native-apps.md](native-apps.md).

Note the deliberate distinction: `@mobile` (without Appium tags) stays in the
**web** wok — it's Playwright *device emulation* (viewport/UA), no device
needed.

- Samples: `sample_feature_tests/mobile/` (built-in Settings apps — no
  app-under-test required)
- Unit tests: `unit_tests/woks/mobile/` + engine tests in `test_nood_0032.py`

### Desktop

The wok for "complex UIs" — native desktop apps, terminal-style interfaces,
Excel — on **both Windows and macOS** (the requirement SikuliX can't meet:
its OCR/screen layer is effectively dead on modern macOS). Three engines:

1. **The visual agent** (`@visual`) — OpenCV template matching + Tesseract
   OCR + PyAutoGUI. SikuliX-style "look at pixels, click what you see", but
   cross-platform and maintained here. Drives *anything* that renders —
   terminal windows, legacy Win32 apps, Citrix, Excel's grid.
2. **Appium native drivers** (`@windows` via WinAppDriver, `@mac` via Mac2)
   — element-level automation where the app exposes an accessibility tree.
   Same Appium client as the mobile wok, so it shares `noodle[mobile]`.
3. **The spreadsheet reader** — stdlib `.xlsx` cell access
   (`agents/desktop/spreadsheet.py`), browserless, zero extra deps. Reads
   *saved values* (including a formula's last-calculated result); driving
   the Excel *application* itself is engines 1–2's job.

```gherkin
Given User reads cell "B2" from sheet "Catalog" of spreadsheet "inventory.xlsx" into "TITLE"
Then User expects cell "A1" of spreadsheet "inventory.xlsx" to equal "Movie"
```

(Files resolve against the app package's `resources/` folder, like
`load_data`. Phrasing: `expects … to equal` works in any scenario; the
natural `cell … should equal "…"` also works *inside a desktop-wok scenario*
(`@windows`/`@mac`), where the desktop table gets grammar priority — in
untagged/web scenarios that sentence stays a generic web compare. See
[Tag-aware step grammar](#tag-aware-step-grammar).)

- Samples: `sample_feature_tests/desktop/` — Windows Calculator (`@windows`),
  Excel→web composition (`excel_to_web.feature`)
- Unit tests: `unit_tests/woks/desktop/` + visual-engine tests in
  `test_visual_*.py`

### Performance

Load testing from plain Gherkin, answering "did this build get slower?" as a
CI gate. The engine is a **built-in stdlib load generator**
(`agents/perf/loadgen.py`): N worker threads hammer a URL for a duration or
request budget, and assertions grade p50–p99 latency, error rate and
throughput. Zero extra dependencies, runs anywhere the core install runs.

```gherkin
@perf
Scenario: Home page latency budget
  When User runs a load test on "{env:APP}" with 10 users for 30 seconds
  Then the p95 response time should be under 800 ms
  And the error rate should be under 1 %
  And the throughput should exceed 20 requests per second
  And User saves the load test report as "home baseline"
  And User stores the p95 response time into "HOME_P95"
```

`@perf` scenarios are browserless (same lifecycle as `@api`). The report
step renders a latency-over-time PNG (green/red dots per request, p95 line)
into the screenshots dir and attaches it to Allure/RCA — the wok's
"screenshot".

**Why not JMeter?** XML test plans, a GUI-first workflow and a JVM don't fit
a Gherkin-native, agent-authorable framework. The modern answer to JMeter is
**Locust** (Python, code-based, distributed): when you outgrow the built-in
generator — sustained heavy load, distributed workers, non-HTTP protocols —
run Locust for generation and keep these Gherkin assertions as the contract.
The built-in engine is deliberately a *gate*, not a stress farm: one
process, one connection per request, polite user counts.

- Samples: `sample_feature_tests/performance/`
- Unit tests: `unit_tests/woks/performance/` (local-server driven, CI-safe)

---

## Routing — how a scenario finds its wok

Per **scenario**, tags pick the session (`hooks.before_scenario`):
`@perf`/`@api` → browserless; `@appium`/`@android`/`@ios`/`@windows`/`@mac`
→ Appium session; `@visual` → visual agent (`catch_all.py`); anything else →
Playwright browser. Precedence quirk to remember: `@mobile` beats platform
tags (`@mobile @android` = Pixel-5 *emulation* in the web wok, pre-NOOD_0032
meaning).

Per **step**, browserless step families dispatch by action type regardless
of the scenario's wok — REST (`rest_*`), spreadsheet (`desktop_*`), load
test (`perf_*`) — which is what makes composition work.

## Tag-aware step grammar

The scenario's tags don't just pick the session — they also pick which
wok's pattern table gets **first claim on a sentence**
(`wok.pattern_priority`, used by the runtime, `noodle validate`, and the
LSP alike):

| Scenario tags | Table order |
|---|---|
| `@perf` | performance → web → desktop |
| `@windows` / `@mac` | desktop → web → performance |
| anything else, or no tags | **best guess:** web → performance → desktop |

So inside `@perf`, `the throughput should be at least 20 requests per
second` is a real throughput assertion; in an untagged or `@web` scenario
the same sentence falls to the web compare catch-all (best guess = the
dominant web vocabulary, exactly the pre-wok behavior). Phrasings that are
namespaced enough not to collide (`runs a load test on …`, `reads cell …
into …`, `throughput should exceed …`, `expects cell … to equal …`) resolve
identically everywhere — prefer them in cross-wok scenarios. `@visual` is
separate: those scenarios resolve against the visual table only, as before.

## Automatic wok tagging on generation

When the **engine** writes or updates a `.feature` in a workspace, it makes
sure the file lands with a routing tag (`wok.infer_tag`/`ensure_tag` —
deterministic, no LLM). Precedence:

1. **Explicit wins.** A tag named in the request ("tag it `@perf`", an
   `explicit` tag passed by a caller) is used verbatim — routing tag or not.
   Content that already carries any routing tag is author intent and is
   never changed.
2. **Steps prove intent.** A load-test step makes it `@perf`; swipe/long-press
   gestures make it `@appium`; image/on-screen steps `@visual`; REST-only
   steps `@api`.
3. **Task wording.** "load test…" → `@perf`, "android"/"iPhone" →
   `@android`/`@ios`, "windows app"/"mac app" → `@windows`/`@mac`,
   "by image" → `@visual`, "REST/endpoint" → `@api`.
4. Otherwise `@web`.

Wired into every generation path: `noodle repl` templates and engine-LLM
generation (`create_test` retags its own `@web` default; the generation
prompt teaches the model the tag list), `author_test` (tag ensured before
validation, so readiness grades with that wok's grammar priority), and
`write_feature` (missing tag added; result reports `wok_tag`). `append_to`
and caller-tagged content are never retagged.

## Cross-wok composition

Woks are stations in one kitchen, not silos. The shared `{var:...}` store is
the pass-through: any wok's "store" step feeds any other wok's steps.

**Excel value drives a web test** (desktop + web —
`sample_feature_tests/desktop/features/excel_to_web.feature`):

```gherkin
@web
Scenario: Search the catalog for the movie named in the spreadsheet
  Given User reads cell "B2" from sheet "Catalog" of spreadsheet "inventory.xlsx" into "TITLE"
  And User is on "{env:APP}"
  When User searches for "{var:TITLE}"
  Then User should see "{var:TITLE}"
```

**API seeds, UI verifies** (api + web) — the most common mix, because most
UI is an API with a face on it. Since NOOD_0192 this comes straight out of a
prompt, REST steps and web steps in one list:

```
1. GET https://en.wikipedia.org/api/rest_v1/page/summary/Vacuum_cleaner
2. Verify the response status is 200
3. Go to the URL https://en.wikipedia.org/wiki/Vacuum_cleaner
4. Close any popup that may appear
5. Verify "From Wikipedia, the free encyclopedia"
```

The compiler keeps **your order**: API calls you put before the first web
step compile ahead of the navigation `Given`, because "fetch it, then prove
the UI shows it" is a sequence, not a set. The scenario is tagged `@web`
automatically — see the box below for why that matters.

Hand-authored, the same shape reaches further (`{var:}` chaining from a
response field into a later web step):

```gherkin
@web
Scenario: A value fetched over REST is verified in the browser
  When performs a GET call at 'https://en.wikipedia.org/api/rest_v1/page/summary/Vacuum_cleaner'
  Then the response status should be 200
  And extracts 'title' from response storing in {var:TITLE}
  Given User is on 'https://en.wikipedia.org/wiki/{var:TITLE}'
  Then User should see "{var:TITLE}"
```

> **Tag it `@web`, never `@api`.** `@api` does not mean "this scenario has
> API steps in it" — it means **no browser at all**
> (`hooks.before_scenario` returns before any launch), which is what makes a
> pure-API suite runnable on a browser-free CI image. REST steps are
> browserless, so they already run in *any* scenario; adding `@api` to a
> mixed one takes the browser away and the first web step fails with
> `This step's action type isn't allow-listed for browserless (@api/@appium)
> scenarios` — fix by removing the tag, not by rewriting the step.

**Web seeds, perf gates** (web sets up state via REST/browser in one
scenario; a `@perf` scenario in the same feature then load-tests the
endpoint and stores `{var:HOME_P95}` for a later comparison step).

**Current composition boundary:** one scenario has one *session* — you
cannot drive a Playwright browser and an Appium device in the same scenario
yet. Browserless families (REST, spreadsheet, perf) compose everywhere;
UI-driving engines compose across *scenarios* in a feature, not within one.
Lifting this (multi-session scenarios) is future work.

## Adding capability to a wok

1. Steps: add patterns to the wok's table (`resolver/<wok>_patterns.py>`;
   web: `patterns.py`; visual: `visual_patterns.py`) — namespaced phrasing
   that can't shadow the web table, which always matches first.
2. Action types: register in `step_resolver.VALID_TYPES` and dispatch in
   `orchestrator/runner.py` (the NOOD_0152 structural guard enforces the
   pattern↔dispatch mirror).
3. Engine code: `noodle/agents/<wok>/`.
4. Tests: `unit_tests/woks/<wok>/` — and only there. Cross-wok effects get a
   boundary test in the other wok's folder (e.g. "web verbs still resolve
   to web actions").
5. Docs: the step examples go in `steps_dictionary.md`; heavy deps go in a
   pip extra declared on the wok in `noodle/wok.py`.

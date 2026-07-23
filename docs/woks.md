# Woks — Noodle's capability work areas
<!-- Branch: NOOD_0155 -->

> **For:** everyone — the concept doc for Noodle's four testing domains.
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

There are four woks:

| Wok | Tests | Engine(s) | Routing tags | Extras |
|-----|-------|-----------|--------------|--------|
| **Web** | browser apps, REST APIs, canvas/terminal-style UIs | Playwright · stdlib REST client · OCR pixel bridge | `@web` (default), `@api`, `@terminal` | none — core install |
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

## The four woks in detail

### Web — the mature wok

What Noodle grew up on: Playwright-driven browser automation with
accessibility-first locators, POM fallback, self-healing, tracing, network
capture. REST testing (`@api`) and the pixel/OCR bridge for canvas and
browser-embedded terminal UIs (`@terminal`) ride in this wok because they
share the web session lifecycle. Nothing about it changed in NOOD_0155 —
the wok concept formalizes the boundary around it.

- Samples: `sample_feature_tests/web/` (8 app packages), `api/`, `terminal/`
- Unit tests: `unit_tests/woks/web/` (boundary guards) + the whole legacy suite

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

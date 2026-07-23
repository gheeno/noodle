# Noodle Test Framework — The Encyclopedia
<!-- Branch: NOOD_0066 -->

> **For:** testers — the complete how-to manual, human-facing.

Everything a tester needs, in the order you'll need it: install, write your first
test, run it, read the output, then the harder stuff — problematic locators
(`pom.yaml`), shared state, tags, recording, reports, CI, and the editor.

New to *how* it works under the hood? Read **[Architecture](architecture.md)**.
Just want the elevator pitch and copy-paste commands? The
**{env:README}(../README.md)** has them.

---

## Contents

1. [Install](#1-install)
2. [Configure](#2-configure)
3. [Write your first test](#3-write-your-first-test)
4. [Run it & read the output](#4-run-it--read-the-output)
5. [`pom.yaml` — when natural naming fails](#5-pomyaml--when-natural-naming-fails)
6. [Built-in step reference](#6-built-in-step-reference)
7. [Variables & shared state](#7-variables--shared-state)
8. [Reports](#8-reports)
9. [Recording a test](#9-recording-a-test)
10. [The visual / desktop agent](#10-the-visual--desktop-agent)
11. [CI — Azure DevOps](#11-ci--azure-devops)
12. [VS Code extension](#12-vs-code-extension)
13. [Testing the framework itself](#13-testing-the-framework-itself)
14. [Custom hooks](#14-custom-hooks)
15. [Writing a custom step](#15-writing-a-custom-step)
16. [Using an LLM — setup, providers, and modes](#16-using-an-llm--setup-providers-and-modes)

---

## 1. Install

**Prerequisites:** Python 3.11+, and [uv](https://docs.astral.sh/uv/) (the
project ships `uv.lock`; plain `pip` also works everywhere below).

```bash
git clone https://github.com/gheeno/noodle.git
cd noodle

# Core only (no LLM, no OpenCV)
uv pip install -e .            # or: pip install -e .
playwright install chromium

# OR everything except LLM support at once (MCP mode + no-LLM manual mode need nothing else)
uv pip install -e ".[all]"
playwright install chromium

# LiteLLM-backed manual mode (cloud provider or Ollama) — opt in separately
uv pip install -e ".[llm]"
```

`llm` is deliberately excluded from `all` (NOOD_0074): `pip`/`uv pip install`
resolve and build the whole requested extras set as one transaction, so one
failing build blocks everything, even unrelated packages — and `llm` is the
one extra with a real chance of a from-source build (a transitive dependency
with no prebuilt wheel for some platforms, pulling in a Rust toolchain via
maturin). Install only the extras you need:

| Extra | Adds | Command |
|-------|------|---------|
| `llm` | LLM step fallback + semantic assertions (not in `all` — see above) | `uv pip install -e ".[llm]"` |
| `reporting` | Allure reports + JUnit XML | `uv pip install -e ".[reporting]"` |
| `visual` | Desktop agent (OpenCV, Tesseract, PyAutoGUI) | `uv pip install -e ".[visual]"` |
| `lsp` | VS Code language server | `uv pip install -e ".[lsp]"` |
| `azure` | Azure Key Vault secret loader | `uv pip install -e ".[azure]"` |
| `all` | Everything except `llm` | `uv pip install -e ".[all]"` |

For reports you also need the **Allure 3 CLI**: `npm i -g allure`
(the npm package `allure`, v3.x — not the legacy Java `allure-commandline` 2.x).

### Docker (reproducible runner)

A `Dockerfile` based on the official Playwright image (browsers + system deps
preinstalled) runs the whole suite with no local Python setup:

```bash
docker build -t noodle .
docker run --rm noodle                       # default: noodle run --headless (whole bundled suite)
docker run --rm noodle run sample_feature_tests/web/busterblock/ --headless
```

`.devcontainer/` opens the same image in VS Code ("Reopen in Container").

### Project layout

```
noodle/             ← the package (cli, hooks, agents, resolver, reporting, llm, lsp)
noodle_tests/       ← your tests live here (scaffolded by `noodle init`)
  web/
    saucedemo/
      features/
        checkout.feature
      resources/
        pom.yaml          ← element aliases for this app (optional)
    busterblock/        ← example suite for the bundled test app (needs it running)
      features/
      resources/
        busterblock_environments.yaml   ← package-scoped base URL (optional)
        busterblock_secrets.env         ← package-scoped credentials (optional)
        preconditions.yaml ← @precondition data fixtures (optional)
        functions/        ← scripts invoked by "run the script ..." steps
  pom.yaml            ← global element aliases (optional)
  steps/              ← auto-wired catch-all — do not edit
  environment.py      ← hooks entry point — do not edit
test-apps/busterblock/   ← bundled local test app (BusterBlock.ca) — see §4
environments.yaml     ← base URLs per environment ({env:SAUCEDEMO}, {env:BUSTERBLOCK}) — committed
.env                  ← browser/run settings, NO secrets — committed
secrets.env           ← credentials (gitignored); or use Azure Key Vault
```

---

## 2. Configure

Settings live in **three files by purpose** — base URLs, secrets, and run
settings are kept apart so secrets never sit next to URLs and CI can swap each
independently.

| File | Holds | Committed? |
|------|-------|------------|
| `environments.yaml` | base URLs per environment | ✅ yes (no secrets) |
| `secrets.env` | credentials / tokens — or [Azure Key Vault](#secrets--azure-key-vault) | ❌ gitignored |
| `.env` | browser & run settings | ✅ yes (no secrets) |

```bash
cp secrets.env.example secrets.env   # credentials (then edit)
```

**Base URLs — `environments.yaml`.** Top-level keys become `{env:KEY}` references:

```yaml
saucedemo: https://www.saucedemo.com
staging:   https://staging.example.com
```

```gherkin
Given User is on "{env:SAUCEDEMO}"      # → https://www.saucedemo.com
```

**Secrets — `secrets.env`** (gitignored; for CI, prefer
[Key Vault](#secrets--azure-key-vault) or a pipeline variable group):

```bash
SAUCE_USERNAME=standard_user
SAUCE_PASSWORD=secret_sauce
```

**Run settings — `.env`** (no secrets):

```bash
NOODLE_BROWSER=chromium        # chromium | firefox | webkit
NOODLE_HEADLESS=false          # true = no visible window
NOODLE_TIMEOUT=10000           # ms per Playwright action (clicks, page loads)
NOODLE_FIND_TIMEOUT=120000     # element-find budget, ms — a CEILING, not a wait:
                               # steps proceed the instant the element appears (NOOD_0089)
NOODLE_WAIT_EXTENSION=30000    # one extra wait at the find deadline while the network
                               # shows the page still loading (chatty analytics filtered out)
NOODLE_IGNORE_HTTPS_ERRORS=true # dev/sandbox certs: TLS errors ignored by default in ALL
                               # browsers; false (or @secure_certs tag) restores validation
NOODLE_AUTO_DISMISS=true       # auto-close overlays that block a click + RCA warning;
                               # false = fail the click instead
NOODLE_STRICT_LOCATOR=false    # true = ambiguous locators FAIL (recommended in CI)
NOODLE_RETRIES=1               # re-run a failed scenario N extra times (flaky guard)
NOODLE_PIXEL_THRESHOLD=0.01    # max fraction of changed pixels for "match the baseline"
NOODLE_LOG_LEVEL=INFO          # DEBUG | INFO | WARNING | ERROR
```

**LLM (optional)** — Noodle Test Framework works fully without one. By default no LLM is
called and no AI costs are incurred. To enable, see
**[§16 Using an LLM](#16-using-an-llm--setup-providers-and-modes)** — it covers
every provider, step-by-step setup, and which file each setting goes in.

The short version of what goes in `.env`:

```bash
NOODLE_MODEL=gemini/gemini-1.5-flash   # which LLM to use (free Gemini shown)
NOODLE_LLM_MODE=auto                   # auto (default) or full — see §16
# NOODLE_LLM_URL=...                   # only for Ollama / self-hosted endpoints
# NOODLE_VISION_MODEL=1                # enable flag: @visual vision fallback (model used is NOODLE_MODEL)
```

API keys (never in `.env`) → `secrets.env`.

Any `[variable]` in a `.feature` maps to the matching key, uppercased with spaces
→ underscores: `[sauce username]` → `SAUCE_USERNAME`. **Resolution order, highest
wins:** Key Vault (if configured) → shell / CI variables → `.env` → `secrets.env`
→ `environments.yaml`.

**Per-app overrides.** Any app folder (e.g. `sample_feature_tests/web/busterblock/`) can
carry its own `resources/.env`, `resources/<app>_secrets.env` and
`resources/<app>_environments.yaml` instead of adding keys to the root files —
see **[docs/feature-packages.md](feature-packages.md)** for the full
resolution order and the package layout.

### Secrets — Azure Key Vault

For enterprise CI, pull secrets from a vault instead of `secrets.env`:

```bash
uv pip install -e ".[azure]"
export NOODLE_KEYVAULT_URL=https://my-vault.vault.azure.net/
```

On set, `before_all` authenticates with `DefaultAzureCredential` (a managed
identity on Azure agents; `az login` or env locally), loads **every** secret in
the vault into the environment, and these override other sources. Vault names map
to env keys by dash→underscore + uppercase (`sauce-password` → `SAUCE_PASSWORD`),
since Key Vault names can't contain underscores. Unset the URL → `secrets.env` is
used (the local-dev fallback). Grant the agent identity `get` + `list` on the
vault's secrets.

---

## 3. Write your first test

Feature files live in `noodle_tests/`, one subfolder per app or domain (with a
nested `features/`+`resources/` split — see below). Here's a
complete, real test against the public demo site — **no Python, no selectors, no
page objects.**

`noodle_tests/login/features/login.feature`:

```gherkin
@web
Feature: Login

  Scenario: Standard user logs in
    Given User is on "https://www.saucedemo.com"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Products"
```

Why each step resolves with zero config:

| Step | Resolves via |
|------|--------------|
| `... username field` | input placeholder "Username" |
| `... password field` | input placeholder "Password" |
| `clicks the login button` | button accessible name "Login" |
| `should see "Products"` | plain DOM text |

The subject (`User`, `I`, `The user`, `As a user`) is stripped automatically, so
`User clicks…`, `I click…`, and `clicks…` are equivalent.

The bundled `sample_feature_tests/web/busterblock/` suite is a full worked example organised
by capability — see the README for how to start BusterBlock and run it.

---

## 4. Run it & read the output

```bash
noodle run                                              # all features
noodle run sample_feature_tests/web/busterblock/features/login.feature       # one file
noodle run sample_feature_tests/web/busterblock/                    # one folder
noodle run --tag smoke                        # only @smoke scenarios
noodle run --headless                         # no visible browser
noodle run --headed                           # force visible (overrides .env)
noodle run --browser firefox                  # firefox | webkit
noodle run --retries 2                        # re-run a failed scenario up to 2x
noodle run --log-level WARNING                 # quieter output
noodle list                                   # discovered scenarios, no browser
noodle validate                               # parse + check [variables], no browser
```

### Bundled example suites

The repo ships ready-to-run examples under `sample_feature_tests/`. One of them drives a
**local** test app, so know what each needs before `noodle run` (no arg) runs
them all:

| Suite | Hits | Needs |
|-------|------|-------|
| `sample_feature_tests/web/busterblock/` | the bundled **BusterBlock** app (primary example) | the local app running (below) |
| `sample_feature_tests/api/` | `api.restful-api.dev` public REST sandbox | internet |
| `sample_feature_tests/terminal/` | canvas terminal (OCR bridge) | `pip install -e ".[visual]"` + tesseract |

**BusterBlock** (`test-apps/busterblock/`) is a self-contained Node/Express VHS-rental
site. The `sample_feature_tests/web/busterblock/` suite is organised by framework capability
(one file per capability, tagged for `--tag` filtering). Start it first:

```bash
cd test-apps/busterblock && npm install && npm start   # serves http://localhost:3333
```

Then run all BusterBlock tests or a single capability file:

```bash
noodle run sample_feature_tests/web/busterblock/ --headless
noodle run sample_feature_tests/web/busterblock/features/login.feature --headless
noodle run sample_feature_tests/web/busterblock/ --tag @smoke --headless
```

Full capability map and credential setup: **[Manual → Part 5](manual.md#part-5--start-busterblock-the-bundled-test-app)**.

**What to expect:**

- Pass/fail printed per scenario.
- On failure: `artifacts/screenshots/FAILED_<step>.png` (annotated) **+ `artifacts/traces/<scenario>.zip`** (full Playwright trace — `playwright show-trace artifacts/traces/<scenario>.zip`) **+ `artifacts/network/<scenario>.json`** (console errors, failed requests, websocket frames).
- If a locator self-healed: `artifacts/reports/healing-report.jsonl` + `artifacts/reports/healing-report.txt` with `pom.yaml` suggestions.
- If any scenario failed: `artifacts/reports/rca.md`, a heuristic root-cause table.
- With `[reporting]` installed: Allure JSON written to `artifacts/allure-results/` automatically.
- `noodle artifacts` / `noodle clean` / `noodle archive` list, wipe, or zip the whole `artifacts/` tree.

### Flaky tests — retries & quarantine

Failed scenarios are retried once by default (`NOODLE_RETRIES`, or `--retries`).
Retries fire **only on failure**, so green scenarios cost nothing.

| Tag | Effect |
|-----|--------|
| `@no_retry` | Never retry this scenario (e.g. a known-failing assertion you're asserting *does* fail) |
| `@quarantine` | Still runs, but its failure is **non-blocking** — the build stays green. `noodle run` exits 0 if every failure this run is quarantined. |

Use `@quarantine` to keep a newly-flaky test visible in reports without blocking
the pipeline while you fix it.

**Log lines that tell you which resolution path fired:**

| Log line | Means |
|----------|-------|
| *(a step that's neither logged nor errored)* | resolved by the accessibility tree — free |
| `📋 POM: resolved '<key>' via pom.yaml` | accessibility missed → POM fallback hit |
| `🔧 Healed: found '<text>' via vision LLM` | both missed → vision LLM (Trigger 2) hit |
| `⚠️  Ambiguous locator '<text>' — matched multiple elements` | label matched 2+ elements (warns, or fails under `@strict`) |

Capture is off by default, so everything streams live.

### Browser & display tags

Add tags to a `Scenario` or `Feature` (feature-level applies to every scenario):

| Tag | Effect |
|-----|--------|
| `@web` | Chromium (default) |
| `@headed` | Force browser visible for this scenario (default is headless; overrides `--headless`/`.env`) |
| `@firefox` / `@webkit` | Switch engine |
| `@mobile @iphone` / `@mobile @android` | iPhone 13 / Pixel 5 emulation |
| `@slow` | 500 ms delay between actions (debugging) |
| `@record_video` | Record `.webm` to `artifacts/videos/` |
| `@strict` | Ambiguous locators **fail** instead of using the first match |
| `@visual` | Route to the desktop/OpenCV agent |
| `@viewport:1920x1080` | Viewport size (or `NOODLE_VIEWPORT` run-wide) |
| `@geo:51.5,-0.12` | Geolocation (or `NOODLE_GEOLOCATION`) — grant with `@permissions:geolocation` |
| `@permissions:geolocation,notifications` | Grant browser permissions (or `NOODLE_PERMISSIONS`) |
| `@locale:fr-FR` | Browser locale — `Intl`, `Accept-Language` (or `NOODLE_LOCALE`) |
| `@timezone:America/New_York` | Browser timezone — `Date`, `Intl` (or `NOODLE_TIMEZONE`) |
| `@color_scheme:dark` | `prefers-color-scheme` emulation (or `NOODLE_COLOR_SCHEME`) |
| `@offline` | Start the context with no network (or `NOODLE_OFFLINE=true`) |
| `@ocr_fallback` | Coordinate/OCR locator fallback for **closed** shadow DOM (or `NOODLE_OCR_FALLBACK=true`; needs `[visual]`) |
| `@retry_step` | Retry each failed step once in place (or `NOODLE_STEP_RETRIES=N` run-wide) |
| `@soft` | Collect assertion failures; fail once at scenario end |
| `@appium` | Drive a device/emulator via Appium instead of a browser (`[mobile]` extra) |

Locale, timezone and color-scheme apply at browser-context creation —
Playwright has no runtime setter for them. Geolocation and permissions also
have runtime steps (`User sets geolocation to '…'`, `User grants permission '…'`).

All tests run headless by default. **Priority (highest wins):** `@headed` > `--headed` > `--headless` > `.env`/workspace config

**Remote browsers (Phase H):** set `NOODLE_REMOTE_URL=wss://…` and every
scenario connects to that Playwright/grid endpoint (BrowserStack, Sauce Labs)
instead of launching locally — no code or feature changes. Vendors that need
capabilities take them URL-encoded in the WS URL query string.

```gherkin
@web @smoke
Feature: Regression Suite        ← all scenarios headless (default)

  Scenario: Standard login        ← headless (default)

  @headed
  Scenario: Debug this one        ← headed, overrides the default
```

---

## 5. `pom.yaml` — when natural naming fails

Write the step in plain sentences first and **run it**. Only reach for `pom.yaml`
when a step actually fails or warns — the message prints the exact key to use.
These are the three problems you'll actually hit.

### Problem A — element has no readable label (icon-only button)

Saucedemo's burger menu is `<button id="react-burger-menu-btn">Open Menu</button>`
— the text is visually hidden, so `clicks the burger menu` finds nothing.

```
Assertion Failed: Could not find element to click: 'burger menu'
```

**Fix:** add a `pom.yaml` next to the feature. The key is the step label minus the
subject, `the`, and the type suffix (`button`/`field`/`input`/`box`):

```yaml
# noodle_tests/<type>/<app>/resources/pom.yaml
burger menu:
  id: react-burger-menu-btn
```

Re-run and you'll see `📋 POM: resolved 'burger menu' via pom.yaml`.

Supported selector types: `css`, `xpath`, `id`, `testid`, `text`, `label`,
`placeholder`, `title`, `alt_text`, `role`. `role` additionally accepts a
compound form for an accessible-name filter: `role: { type: button, name:
"Login" }` (bare `role: button` still matches on role alone).

> **Key mapping:** `clicks the search button` → key `search`; `enters X in the
> username field` → key `username`; `clicks "Add to Cart"` → key `Add to Cart`
> (quoted = exact, nothing stripped). Keys are case- and whitespace-insensitive.

### Problem B — the label matches many elements (ambiguous)

Six identical "Add to cart" buttons → `clicks "Add to cart"` matches all six.
Default (lenient): warns and clicks the first. Two ways to handle it:

1. **Make CI strict** — `@strict` tag or `NOODLE_STRICT_LOCATOR=true`. The step
   then fails with the candidate list, forcing you to disambiguate.
2. **Scope it in `pom.yaml`** — a POM entry is always used *before* blind
   first-match:
   ```yaml
   add to cart:
     xpath: "(//button[contains(.,'Add to cart')])[1]"   # or a container scope
   ```

Prefer container scoping (`//header//input[@type='search']`) over positional
`[1]` — it survives DOM reordering.

### Problem C — same name, different element per page

`search` means the home bar on `/` but the results filter on `/search`. Scope by
URL — the framework reads the live URL and picks the matching block:

```yaml
pages:
  home:
    match: { url_contains: "saucedemo.com/$" }   # regex on page.url
    search: { css: "input.home-search" }
  results:
    match: { url_contains: "/inventory" }
    search: { css: "input.results-filter" }
shared:                                           # checked after the active page
  cookie accept: { id: onetrust-accept-btn-handler }
```

For single-page apps where the URL never changes, pin the page explicitly —
either mid-scenario with a step, or up front with a tag so it's set before
any navigation happens:

```gherkin
Given User is on the "results" page          # pins for the rest of this scenario
```
```gherkin
@page:results                                # pins before the scenario starts
Scenario: Search returns matches
```

The step, if one runs later, still overrides the tag — the tag is just the
default. Both set the same pin (`noodle/agents/web/pom.set_active_page`), so
a `pages:` block written for URL matching works unchanged once pinned.

### Problem D — a route rename breaks a still-valid selector

URL-based scoping (Problem C) ties a `pages:` block to `match.url_contains`.
Rename the route (`/inventory` → `/catalog`) and the block stops matching —
even though `search: { css: "input.results-filter" }` inside it is still a
perfectly valid selector. Two fixes, from least to most surgical:

1. **Update the `match:`** — the normal fix, one line.
2. **Pin the page** (above) — decouples the block from the URL entirely, so
   a route rename can't break it again.
3. **Force one element with `{pom:key}`** — when you don't want *any* URL
   dependency for a single step, write the name as a `{pom:...}` ref:

   ```gherkin
   When User clicks the {pom:burger menu}
   ```

   `{pom:burger menu}` looks up `burger menu` directly in the POM chain (page →
   app → global) and **only** there — no accessibility tree, no self-heal,
   no vision LLM. It fails immediately, naming the chain it checked, if the
   key isn't found anywhere — it never silently falls back to a heuristic
   match that might be the wrong element. Plain `the burger menu` (no
   braces) is unaffected and keeps the full five-step order below.

### Scope: local vs global

| File | Applies to | Use for |
|------|-----------|---------|
| `noodle_tests/<type>/<app>/resources/pom.yaml` | that app only | site-specific elements |
| `noodle_tests/pom.yaml` | all feature files | shared elements (cookie banners, nav) |

Local wins when the same key exists in both. A flat `pom.yaml` (no `pages:` /
`shared:`) is fully supported — page-scoping is opt-in.

Page *names* (the thing `@page:<name>` and `is on the "<name>" page` pin) are
app-scoped too, and for the same reason: `pom.set_context()` is set from the
currently-running `.feature` file's own directory
(`hooks.before_feature`), and `_load_pom_chain` globs
`resources/pageobjects/*_pom.yaml` relative to *that* app only. Two different
apps can both ship a `results_pom.yaml` and never collide — a feature file
under `noodle_tests/web/appA/` never even lists `noodle_tests/web/appB/resources/`, let
alone reads it. The only place a name collision is possible is *within* one
app, if that app's own `pom.yaml` or the global `noodle_tests/pom.yaml` also defines
a `results` page block — the same local-beats-global rule above applies,
pinned or not. This only holds if the folder layout is the standard one
(`noodle_tests/<type>/<app>/resources/pageobjects/<page>_pom.yaml`) — see
[workspace-guide.md § 5](workspace-guide.md#5-naming-whats-free-whats-fixed).

### Lookup order (what the framework tries)

```
0. {pom:key} explicit — ONLY if the step writes the name as {pom:...}: POM
   yaml or nothing. Never falls through to 1-5.               (Problem D)
1. Accessibility tree — role / label / placeholder / text   (most steps stop here)
2. If MANY match → ambiguity: POM scoped entry, else warn/fail   (Problem B)
3. Self-heal: scroll, then partial-text retry
4. POM yaml — page-scoped block → shared → flat keys   (Problems A & C)
5. Vision LLM (only if NOODLE_MODEL is set; else the step fails)
```

Full picture, including the LLM boundary: [Architecture → Resolution hierarchy](architecture.md#4-the-resolution-hierarchy).

### Shadow DOM, SVG & containers

- **Shadow DOM** — Playwright's `css`/`role`/`text`/`id`/`testid` engines pierce *open* shadow DOM automatically, so web-component pages mostly "just work." **Avoid `xpath` POM selectors on shadow-DOM pages — XPath does not cross shadow boundaries.**
- **Closed shadow DOM (Phase T)** — spec-level unreachable from JS/CSS: no selector (not even a vision-LLM-generated one) crosses a closed boundary. The fix isn't a smarter selector, it's the coordinate path that doesn't need one: opt in with `@ocr_fallback` (or `NOODLE_OCR_FALLBACK=true`, needs the `[visual]` extra) and, when the whole locator chain misses, `clicks the 'X' button` / `should see 'X'` fall back to OCR screen coordinates (the same DPR-correct math as the terminal bridge, `agents/web/screen.py`). **Covers:** click, type-after-click, visibility. **Does NOT cover (hard limitation):** element-*state* asserts (disabled/checked/attribute) — those need real DOM access, which closed mode makes impossible from outside a CDP session. Opt-in because each fallback costs a screenshot decode + Tesseract pass; without the flag, behaviour is unchanged.
- **SVG** — real DOM, so it's targetable. An SVG with `<title>` or `role="img"` + `aria-label` resolves by name; otherwise treat it like an icon-only button (a `css`/`testid` POM entry).
- **Containers** — scope with a row/section step (below) or a scoped `pom.yaml` page-block; don't bake container paths into the sentence.

---

## 6. Built-in step reference

250+ patterns work out of the box. Subject is stripped automatically. This is
a condensed quick reference — for the complete list with every phrasing
variant, see [docs/steps_dictionary.md](steps_dictionary.md).

### Navigation
```gherkin
Given User is on "https://example.com"
When User navigates to "https://example.com/cart"
When User goes to "https://example.com/checkout"
When User opens "https://example.com"
Given User is on the "results" page          # pin a POM page (SPAs)
When User goes back                           # browser history
When User goes forward
When User reloads the page                    # or: refreshes the page
```

### Forms
```gherkin
When User enters "value" in the email field
When User enters {env:MY_EMAIL} in the email field
When User fills in the username with "admin"
When User types "hello" into the search box
When User clears the search field
When User selects "Medium" from the size dropdown
When User checks the "Remember me" checkbox
When User unchecks the newsletter checkbox
When User selects "Action" in the genre filter  # "in" or "from"
When User submits the login form                 # clicks the form's submit control
```

### Clicks, keyboard & hover
```gherkin
When User clicks the login button
When User clicks "Submit"
When User clicks the "Proceed to Checkout" link
When User presses the confirm button          # a click
When User taps "Menu"
When User double-clicks "Jaws"                 # dblclick
When User right-clicks "File"                  # context-menu click
When User presses Enter                        # a keyboard key
When User hovers over the "Account" menu
```

### Tabs & windows
```gherkin
When User clicks "Preview"                     # opens a new tab
Then a new tab should open                      # asserts + focuses the new tab
And User should see "Details" in the new tab    # any step + " in the new tab"
When User switches to the previous tab          # new / previous / original / first
When User closes the tab
```

### Waiting & scrolling
```gherkin
And User waits for the page to load
And User waits until "Order confirmed" appears
And User waits until "Spinner" disappears
And User waits 2 seconds
When User scrolls down
When User scrolls to "Footer"
```

### Tables & containers (D365-style grids)
```gherkin
When User clicks "Edit" in the row containing "Contoso"
When User clicks the "Save" button in the "Payment" section
Then the cell in row "Contoso" column "Status" should be "Active"
And the grid should have 5 rows
Given User switches to the "main" frame        # iframe
```

### Assertions
```gherkin
Then User should see "Products"
Then User should not see "Error"
Then User should have url containing "dashboard"
And the page title should contain "Swag Labs"
Then the "Email" field should contain "a@b.com"      # element value
Then the "Submit" button should be disabled          # enabled/disabled/checked
And the chart line should have attribute "stroke" equal to "green"
And User should see 3 "result" items                 # count (visible only)
```

> Count assertions count **visible** occurrences — sr-only/aria duplicates and
> tooltip text are excluded, so "should see 3 X" reflects what a user actually
> sees on screen.

### Visual regression — deterministic (no LLM)
Pixel diff against a stored baseline. First run captures `baselines/<name>.png`;
later runs fail if more than `NOODLE_PIXEL_THRESHOLD` (default 1%) of pixels
changed, saving `artifacts/screenshots/DIFF_<name>.png` as evidence.
```gherkin
Then the screen should match the baseline
Then the "checkout" screen should match the baseline
```

### Semantic / visual — LLM (requires `NOODLE_MODEL`)
```gherkin
Then the checkout form should show a success state
And the screen should look the same as before
And the "header" screen should look the same as before ignoring the navigation
```

### Network mocking
Intercept requests via Playwright routing — decouple a test from a flaky/slow/
absent backend, or silence third-party noise.
```gherkin
When User mocks "**/api/cart" with status 200 and body '{"items":[]}'
When User mocks "**/api/checkout" with status 500
When User blocks requests to "**/analytics/**"
```

### API setup / teardown
Hit an endpoint directly (Playwright's request context — shares browser cookies),
e.g. to seed or clean data without driving the UI. Fails on a non-2xx response.
```gherkin
Given User calls POST "https://api.test/seed" with body '{"user":"bob"}'
And   User calls GET "https://api.test/reset"
```

### Test-data fixtures
Load a YAML/JSON mapping into the run-scoped variable store, then reference the
keys as `{var:...}` captures.
```gherkin
Given User loads test data from "fixtures/users.yaml"
When  User enters "{var:username}" in the username field
```

### Running scripts & commands
Invoke any external script (py/js/jar/sh/…) or shell command as a step — seed a
DB, run a jar, call a CLI tool. Interpreter inferred from the extension; a
non-zero exit **fails the step**. stdout is captured into `{var:SCRIPT_OUTPUT}`
(and any var you name), so a later step can assert on it. `{env:X}`/`{var:X}` refs in the
path/args/command are substituted first. Timeout:
`NOODLE_SCRIPT_TIMEOUT` (default 60s).
```gherkin
Given the script "resources/functions/seed_db.py" runs
And   {var:SCRIPT_OUTPUT} should contain "seeded 42 rows"
Given User runs the script "tool.jar" with "--env staging" storing the output as {var:RESULT}
Given User runs the command "java -jar tool.jar {env:BUSTERBLOCK}"
```
> Feature files are trusted code (like step definitions) — `run the command` uses
> a shell. Don't drive these steps from untrusted input. Full guide: README →
> "Run a script from a step".

### Screenshots
```gherkin
And User takes a screenshot "after-login"
```

---

## 7. Variables & shared state

**Config & secrets** use `{env:...}` and come from `.env`:

```gherkin
When User enters {env:MY_EMAIL} in the email field      # reads MY_EMAIL
```

**Values captured during the test** use `{var:...}` and come from a
per-scenario run store — never `.env`. The two prefixes keep a captured value
visually distinct from a secret:

```gherkin
When User stores the order number as {var:order}         # capture → run store
And  User enters "{var:order}" in the reference field     # reuse it later
Then {var:order} should equal {var:confirmation}              # compare two captures
```

Other state steps:

```gherkin
When User sets {env:TAX_RATE} to "0.13"                          # seed a literal
When User stores attribute "data-id" of the row as {var:id}      # capture an attribute
Then {var:total} should be greater than "0"                      # numeric/string compare
And  "abc" should contain "b"
```

The principle: **the app computes, the test observes.** Noodle Test Framework stores the app's
output and asserts on it — it never re-implements the app's arithmetic. Variables
reset between scenarios (tests stay independent).

---

## 8. Reports

Requires `[reporting]` installed and `allure` on your PATH.

```bash
noodle run noodle_tests/             # 1. produces artifacts/allure-results/
noodle report generate           # 2. artifacts/allure-results/ → artifacts/reports/allure-report/ (HTML)
noodle report open               # 3. build + open in a browser
```

> ⚠️ You can't double-click `artifacts/reports/allure-report/index.html` — it loads data over XHR,
> which browsers block on `file://`. It must be served over HTTP (the commands
> above do that).

| Goal | Command |
|------|---------|
| Build static HTML only (CI artifact) | `noodle report generate` → `artifacts/reports/allure-report/` |
| Build + open on a local server | `noodle report open` |
| One-shot from results (no saved dir) | `allure generate artifacts/allure-results --open` |
| Host an already-built report (no Allure CLI) | `python -m http.server 8000 --directory artifacts/reports/allure-report` |

Trends across runs are automatic: `noodle report generate` appends each run to
`artifacts/reports/allure-history/history.jsonl` (Allure 3 `historyPath`) and
reads it back on the next build — just run again and the History/Trend widgets
fill in. In CI the pipeline caches that file between builds (see
`azure-pipelines.yml`).

**What you see:** overview (pass/fail/skip + trend), suites (feature → scenario →
step), each failed step with error + annotated screenshot, timeline. For *how*
the report is built, see [Architecture → Where the report comes from](architecture.md#6-where-the-report-comes-from).

### Failure traces (Playwright)

Every **failed** scenario also captures `artifacts/traces/<scenario>.zip` — a full
Playwright trace with DOM snapshots, network log, console, and a frame-by-frame
timeline. It's discarded on pass (green runs cost no disk). Open it:

```bash
playwright show-trace artifacts/traces/<scenario>.zip
```

In CI it's published as part of the `TestArtifacts-*` pipeline artifact (whole
`artifacts/` tree, every run). This is the headline debugging edge over
Selenium/Selenide — time-travel through the run instead of guessing from a log.

### Healing telemetry

When the locator layer resolves something by a non-primary path (scroll/partial-
text self-heal, POM disambiguation, vision-LLM locate), it's recorded. At end of
run, if anything healed, Noodle Test Framework writes `artifacts/reports/healing-report.jsonl` (one event per line) and
`artifacts/reports/healing-report.txt` with a suggested `pom.yaml` entry per healed locator — turn a
flaky-by-naming locator into a one-line deterministic fix.

### Agentic RCA — automatic failure root-cause

Telemetry tells you *what* healed; RCA tells you *why a failure happened*. Enable
it with a vision model plus the opt-in flag:

```bash
# .env
NOODLE_MODEL=openai/gpt-4o     # vision-capable
NOODLE_RCA=true
```

On **each failed step**, Noodle Test Framework sends the failure screenshot + step text +
error to the model and gets back a structured verdict. It's logged to the console
and attached to the Allure result as the `rca_category` label, so you can filter
the report by root cause:

| `rca_category` | Meaning |
|----------------|---------|
| `app-regression` | The UI changed or a feature is broken |
| `locator-rot` | The element's label or structure changed |
| `environment-flap` | Network, timeout, or infra issue |
| `test-data` | Missing, stale, or wrong seed data |
| `test-script` | The step or assertion itself is wrong |

```
🔍 RCA [environment-flap] (medium): the page never finished loading before the assertion
💡 Suggested fix: add a "wait until ... is visible" step or raise NOODLE_TIMEOUT
```

RCA is **best-effort**: it never changes a test's pass/fail and never raises, and
it fires only on failure (one model call per failed step — green runs cost
nothing). Off unless both `NOODLE_MODEL` and `NOODLE_RCA` are set. It pairs
with [failure traces](#failure-traces-playwright): the trace shows the *what*, RCA
suggests the *why*.

---

## 9. Recording a test

Rather click through your app than write Gherkin?

```bash
noodle record --output noodle_tests/web/myapp/features/login.feature --name "Login Flow"
```

A browser opens. Perform the flow. Close it. Noodle Test Framework writes the `.feature` file.
Sensitive values (emails, card numbers, passwords) are auto-detected and replaced
with `{env:VARIABLE}` placeholders — the real values go in `.env`.

---

## 10. The visual / desktop agent

For UIs with no accessible DOM (desktop apps, Electron, Citrix, legacy web):

```bash
uv pip install -e ".[visual]"
brew install tesseract        # macOS  (apt install tesseract-ocr on Linux)
```

Tag the scenario `@visual` and store reference images where the run can reach
them (e.g. an `assets/` folder; the path in the step is relative to the run dir):

```gherkin
@visual
Scenario: Upload via file picker
  When I click image "upload_button.png"
  Then I should see text "File picker" on screen
  And I type "{env:FILE_PATH}"
  And I press key "enter"
```

It finds targets by OpenCV template match (with DPI-scale variants) → Tesseract
OCR → optional vision LLM (only if `NOODLE_VISION_MODEL` or `NOODLE_MODEL` is set; the call itself uses `NOODLE_MODEL`).

`@visual` routes the **whole scenario** to the desktop agent — the tag is read
once, per scenario, in `noodle/steps/catch_all.py`. Web and visual steps cannot
be mixed in one scenario; split them into separate scenarios (a `@visual` one and
a plain `@web` one). The full visual vocabulary is in
[steps_dictionary.md](steps_dictionary.md#visual--desktop-steps--visual-nood_0067).

---

## 11. CI — Azure DevOps

Drop-in pipeline files are in the project root: `azure-pipelines.yml` (Linux) and
`azure-pipelines-windows.yml` (Windows).

1. Create a variable group `noodle-secrets` with your credentials (`BASE_URL`, `MY_EMAIL`, …).
2. Link the pipeline YAML.

Recommended CI defaults: `NOODLE_HEADLESS=true` and `NOODLE_STRICT_LOCATOR=true`.

What you get:

| Pipeline step | Shows up as |
|---------------|-------------|
| `PublishTestResults@2` (JUnit `artifacts/reports/junit.xml`) | **Run → Tests tab** — native pass/fail dashboard, trends, per-test history |
| `PublishPipelineArtifact@1` (`artifacts/`) | **Run → Artifacts → TestArtifacts** — the whole run: Allure report, junit, screenshots, traces, videos, healing report |
| `PublishAllureReport@2` (suite job, after all shards finish) | **Run → Allure Report tab** — one merged, hosted report across every shard, with cross-run trend history |

The Tests tab is a real dashboard for free. The Allure Report tab is a
*hosted, browsable* report (not just a downloadable artifact) — it needs the
free **Allure Report** marketplace extension (`qameta.allure-azure-pipelines`)
installed once at the Azure DevOps organization level; after that it's
automatic on every run (NOOD_0039/NOOD_0040).

### Parallel execution (sharding)

behave is single-process, so Noodle Test Framework parallelizes by **sharding feature folders
across agents**. The pipeline uses a matrix — one agent per folder — and each shard
publishes its own `junit.xml`; the Tests tab aggregates them into one run:

```yaml
jobs:
  - job: tests
    strategy:
      maxParallel: 4
      matrix:
        busterblock:  { featurePath: 'sample_feature_tests/web/busterblock/' }
        api:          { featurePath: 'sample_feature_tests/api/' }
    steps:
      - script: noodle run $(featurePath) --headless
      # ... PublishTestResults / artifacts per shard
```

Add a matrix row per feature folder to scale out. Because a run rewrites
`artifacts/allure-results/`, each shard must have its own workspace — which separate agents
do automatically. (No in-process worker pool; add agents, not threads.)

#### Data isolation across shards

Separate agents get separate *workspaces*, **not** separate *backends*. Two
shards that seed the same test server race: if both call
`POST {env:BUSTERBLOCK}/api/test/reset` ([preconditions](architecture.md#2-the-component-map)),
one shard's reset wipes the other's state mid-run. Two ways to keep shards
independent:

1. **Backend per shard** (cleanest) — give each shard its own server instance /
   database via the variable group, e.g. set `BUSTERBLOCK` to a per-shard URL in
   the matrix:

   ```yaml
   matrix:
     busterblock_1: { featurePath: 'sample_feature_tests/web/busterblock/', BUSTERBLOCK: 'http://bb-1:3333' }
     busterblock_2: { featurePath: 'sample_feature_tests/web/busterblock/', BUSTERBLOCK: 'http://bb-2:3333' }
   ```

2. **Namespaced fixtures** — if the backend supports it, seed into a per-shard
   slot instead of a global reset (e.g. key test data by the shard's job name) so
   no two shards touch the same records. This needs the app to support scoped
   resets; the bundled BusterBlock uses a single global store, so prefer option 1
   for it.

The safe default: shard so that no two folders hit the same backend, or run each
against its own instance.

### Secrets via Key Vault

Instead of putting credentials in the variable group, set `NOODLE_KEYVAULT_URL`
and grant the pipeline's service connection / managed identity `get` + `list` on
the vault. Install the extra (`pip install -e ".[azure]"`, included in `[all]`)
and Noodle Test Framework loads the vault at startup. See [Configure → Secrets](#secrets--azure-key-vault).

### Running against an external tests repo

A team can own its own test repo, separate from this engine repo (e.g.
`/Projects/noodle` for the engine, `/Projects/tests` for a team's own
`.feature` files/POMs/secrets), and have the pipeline clone both and run
against the external one. Set these at "Run pipeline" time (they're
template parameters, so they show up as UI fields, not just queue-time
variables):

| Parameter | Default | What it does |
|---|---|---|
| `useExternalTestsRepo` | `false` | `true` clones a second repo (`testsRepo`) alongside this one and runs against it instead of the bundled `sample_feature_tests/` |
| `testsRepoType` | `git` | `git` (Azure Repos, same org) or `github` (needs a service connection) |
| `testsRepoName` | *(empty)* | e.g. `MyProject/noodle_tests` (Azure Repos) or `my-org/noodle_tests` (GitHub) |
| `testsRepoRef` | `refs/heads/main` | Branch/tag/ref to check out |
| `testsRepoGithubEndpoint` | *(empty)* | GitHub service connection name — only when `testsRepoType` is `github` |
| `testsRepoDir` | `tests` | The external repo's `noodle.yaml` `tests_dir` value (`tests` or `noodle_tests` — see [feature-packages.md § Two topologies](feature-packages.md#two-topologies)) |
| `testTag` | *(empty)* | Only run scenarios with this tag (e.g. `smoke`) — blank runs everything discovered |
| `headless` | `true` | Browsers run headless by default; only flip to debug a specific queued run |

What a team needs to do on their side: own a repo with the same shape
`noodle init` scaffolds (`noodle.yaml`, `.env`, `tests_dir`, `resources/`) —
nothing engine-specific. The pipeline clones it as a sibling checkout under
`$(Pipeline.Workspace)/testsRepo`, installs `noodle` from *this* repo's
source (the engine repo, checked out as `self`), and runs
`noodle run --workspace $(Pipeline.Workspace)/testsRepo <feature> --headless`
against it — the same `--workspace` model documented in the
[manual](manual.md#plain-english-shell--noodle-repl-optional), just wired into
CI instead of a laptop.

RCA is generated explicitly every run (pass or fail) as a pipeline step —
`noodle run` only auto-writes a bare markdown RCA when there's a failure and
the run isn't `--parallel`, so the pipeline doesn't rely on that; it always
calls `rca-report` and renders the HTML view too, so both are in the
published artifact regardless of mode or outcome.

---

## 12. VS Code extension

Syntax highlighting, `[variable]` colouring, step-validation squiggles,
`@tag` autocomplete — plus param-token discoverability (NOOD_0069):

- **Hover** any `{env:X}` / `{var:X}` / `{pom:X}` token or `"file.py:fn"`
  spec to see where its value comes from — source file, line number, and
  the value itself (masked for anything secret-looking or living in a
  `*secrets*` file). Hovering `{var:X}` shows the step in the same file
  that writes it; engine-set vars (`FUNCTION_RESULT`, `SCRIPT_OUTPUT`,
  `PAYLOAD`, `REST_STATUS`/`BODY`/`HEADERS`) explain themselves.
- **Cmd/Ctrl+click (go to definition)** on the same tokens jumps to the
  `.env`/`environments.yaml` line, the POM YAML entry, the `def` of the
  function, or the step that first writes the `{var:X}`.
- **Autocomplete** after typing `{env:`, `{var:`, or `{pom:` lists every
  known key with its source file as the detail.

### How new files, keys, and steps become discoverable

There is no registry and nothing to map. Every hover/definition/completion
request re-scans the workspace live, following the exact conventions the
runtime uses:

| You add… | Discovered… |
|---|---|
| a key in `.env`, `secrets.env`, `environments.yaml`, or a new `resources/.env` / `*_environments.yaml` / `*_secrets.env` file | immediately — env sources are globbed per request, in runtime precedence order |
| a new `resources/pom.yaml` or `resources/pageobjects/*_pom.yaml` file (or key) | immediately — the POM chain (per-page → app → global `noodle_tests/pom.yaml`) is re-read per request |
| a helper function in any `.py` reachable from the workspace root | immediately — `"path/to/file.py:fn"` resolves relative to the workspace root, same as the runner |
| a `{var:X}` write in a feature file | immediately — hover/definition scan the open document for `… as {var:X}` / `sets {var:X} to` |
| a new built-in step in `PATTERNS` or the steps dictionary | after **Developer: Reload Window** — the LSP is a long-lived Python process and caches both on import (see "Resetting the language server" below) |

**AI-generated steps**: a step no built-in pattern matches gets the yellow
`llm-fallback` squiggle and a hover saying the LLM will resolve it at
runtime — that's the signal it's AI-interpreted, not vocabulary. Any
`{env:}`/`{var:}`/`{pom:}` tokens inside it still hover/click like normal,
since token lookup doesn't care whether the step matched. When a generated
step proves itself and you promote it to `PATTERNS` (§15) or the steps
dictionary, reload the window and it graduates to a first-class step with
hover examples and no squiggle.

```bash
uv pip install -e ".[lsp]"
cd vscode-extension && npm install && cd ..
ln -s $(pwd)/vscode-extension ~/.vscode/extensions/noodle-0.1.0
```

Fully quit VS Code (`Cmd+Q`, not just close the window), then reopen.

**Which Python runs the server?** `noodle.pythonPath` if set; otherwise the
extension auto-detects the workspace `.venv` (`.venv/bin/python`, or
`.venv\Scripts\python.exe` on Windows), falling back to `python3` (`python`
on Windows). No hover/squiggles on a machine where noodle lives outside the
workspace `.venv` — e.g. installed via `uv tool install` — means the fallback
Python can't import noodle; set the path explicitly:

```json
{ "noodle.pythonPath": "/absolute/path/to/python" }
```

**Disable the Cucumber extension for this workspace** — both activate on
`.feature` files and conflict: `Cmd+Shift+X` → search "Cucumber" → right-click
`alexkrechik.cucumberautocomplete` → **Disable (Workspace)** → reload window.

Unknown steps get a yellow squiggle (the LLM may handle them at runtime). Tune it
in `.vscode/settings.json`:

```json
{ "noodle.unknownStepSeverity": "none" }   // "warning" (default) | "information" | "none"
```

### Resetting the language server

`vscode-extension/client/extension.js` spawns `python -m noodle.lsp.server`
once, when the extension activates, and keeps it alive as a stdio
subprocess for the rest of the window's life. It only re-validates
`.feature` files on open/edit — it never re-imports its own Python code.
So if a step still shows a stale `llm-fallback` squiggle after:

- pulling a Noodle update that touches `noodle/lsp/server.py` or
  `noodle/resolver/patterns.py`,
- adding a new entry to `PATTERNS` yourself (§15, Option A),
- or switching branches/worktrees on the code the server imports from,

the running server is just out of date — Python already imported the old
version and won't notice the file changed. Fix: `Cmd+Shift+P` → **Developer:
Reload Window**. That restarts the extension host, which respawns the LSP
process fresh. There's no dedicated "restart Noodle LSP" command today;
Reload Window is the whole trick.

---

## 13. Testing the framework itself

Noodle Test Framework's own suite runs with **no browser, no LLM, and no display**.

```bash
make test                               # == python -m pytest unit_tests/ -v
python -m pytest unit_tests/test_lsp.py -v   # a single file
```

**Expected: 314 passed, 0 failed.** Coverage spans CLI hardening, hooks
lifecycle, step patterns (incl. tables and shared-state), visual patterns,
OpenCV matcher (mocked), Allure writer, JUnit output, screenshot annotation,
recorder + sensitive redaction, LSP validation, page-scoped POM lookup, locator
ambiguity detection, and the enterprise additions — deterministic pixel diff,
quarantine exit-code scan, healing telemetry, Key Vault merge, the
mock/API/test-data steps, **data preconditions/teardowns, the script/command
runner, and the custom hook registry**.

---

## 14. Custom hooks

Custom hooks let you inject cross-cutting behaviour — timing, session tracking,
extra logging, tag-conditional setup — without touching your `.feature` files or
the framework internals. They mirror Cucumber's `Before`/`After` hooks.

### How to register a hook

Create any `*.py` file in `noodle_tests/steps/` and use the `@hook` decorator:

```python
# noodle_tests/steps/custom_hooks.py
import time, uuid
from noodle.hooks import hook
from noodle.log import logger

@hook("before_scenario")
def assign_session(context, scenario):
    context.session_id = str(uuid.uuid4())[:8]
    context._start = time.monotonic()

@hook("after_scenario")
def log_timing(context, scenario):
    elapsed = time.monotonic() - getattr(context, "_start", 0)
    status = "PASSED" if "passed" in str(scenario.status) else "FAILED"
    logger.info(f"\n  🪝 [{context.session_id}] {scenario.name} — {status} ({elapsed:.1f}s)")
    if "audit" in scenario.effective_tags:
        logger.info(f"\n  📋 AUDIT: {scenario.feature.name} / {scenario.name}")
```

behave auto-loads every `*.py` in `noodle_tests/steps/`, so the hooks register
before any scenario runs. The `@hook` decorator is the only API you need.

### Supported events

| Event | Fires | Arguments |
|---|---|---|
| `before_all` | once, before the suite | `(context,)` |
| `before_feature` | once per feature file | `(context, feature)` |
| `before_scenario` | before each scenario | `(context, scenario)` |
| `after_step` | after each step | `(context, step)` |
| `after_scenario` | after each scenario | `(context, scenario)` |
| `after_all` | once, after the suite | `(context,)` |

Alternatively, call `register(event, fn)` directly (no decorator):

```python
from noodle.hooks import register
register("after_all", lambda ctx: print("suite done"))
```

### Execution order within each event

- **`before_*`** — framework setup runs first (browser is already open), then your hook. `context.page` is available.
- **`after_scenario`** — your hook runs first (page is still open), then data teardown, then browser close.
- **`after_all`** — your hook runs first, then the Allure/JUnit report is generated.
- Multiple hooks for the same event fire in registration order (first registered, first called).

### `before_all` — one timing constraint

`before_all` fires before behave loads step files, so a `@hook("before_all")`
placed in a file under `noodle_tests/steps/` will **not** run — the file hasn't
been imported yet. Register `before_all` hooks in `noodle_tests/environment.py`
instead:

```python
# noodle_tests/environment.py
from noodle.hooks import before_all, ..., register

def my_before_all(context):
    context.suite_start = time.monotonic()

register("before_all", my_before_all)
```

All other events are safe to register from step files.

### Demo

`sample_feature_tests/web/busterblock/features/hooks.feature` shows hooks in action against
BusterBlock. The `@audit` tag triggers an extra log line from the
`after_scenario` hook — no change to the feature file required:

```gherkin
@smoke @audit
Scenario: Catalog is visible and the run is audit-logged
  Then User should see "VHS Catalog"
  And User should see "Jaws"
```

Terminal output when `custom_hooks.py` is loaded:

```
  🪝 [a3f1bc2e] Catalog is visible and the run is audit-logged — PASSED (1.2s)
  📋 AUDIT: Hooks demo — cross-cutting behaviour via custom hooks / Catalog is visible and the run is audit-logged
```

### Tag-conditional hooks

Hooks receive the full `scenario` object, so any tag-based branching is plain
Python — no special syntax:

```python
@hook("before_scenario")
def maybe_seed(context, scenario):
    if "needs_admin" in scenario.effective_tags:
        context.admin_token = fetch_admin_token()
```

---

## 15. Writing a custom step

> Before writing a step definition, check whether the built-in
> `calls the function 'file.py:func' … and saves the result as {var:X}`
> step already covers it — arbitrary in-process Python with return-value
> capture and variable-based dependency injection, no framework changes.
> See *Custom Python functions* in `docs/steps_dictionary.md`.

### How Noodle Test Framework resolves a step

Every step goes through two tiers:

1. **Pattern match** — `noodle/resolver/patterns.py` is tried first. A regex
   match returns an action dict immediately; no model is invoked.
2. **LLM fallback** — if no pattern matches *and* `NOODLE_MODEL` is set, the
   step text is sent to the configured model. Without `NOODLE_MODEL` the run
   fails with a clear "add a pattern" message.

The VS Code extension (LSP) shows an inline warning on any step that would fall
through to tier 2:

```
No built-in pattern matched — LLM will resolve at runtime.  [llm-fallback]
```

### What to do when you see that warning

**Option A — add a pattern (preferred)**

Open `noodle/resolver/patterns.py` and append an entry to `PATTERNS`:

```python
# My new verb
(r'^verifies? (?:that )?(.+?) (?:is|are) displayed?$',
                                               'assert_visible', lambda m: {'text': _q(m.group(1))}),
```

The tuple is `(regex, action_type, param_extractor)`. Patterns are matched
top-to-bottom; first match wins. Regex is anchored (`^…$`) and
case-insensitive.

Pick the closest existing `action_type` — you rarely need a new one. The full
list is in `noodle/resolver/step_resolver.py::VALID_TYPES`.

After adding the pattern, **restart the language server** — see
[§12](#12-vs-code-extension) — then reopen or re-save the `.feature` file.
`patterns.py` is imported once when the server process starts, so a running
server never sees an edit to it; only the `.feature` file itself is
re-validated live (on every keystroke, no restart needed for that part).

**Option B — accept LLM fallback**

If the step is intentionally vague (e.g. exploratory tests, legacy steps you
haven't cleaned up yet), you can silence the warning by adding `# llm-ok` at
the end of the step line:

```gherkin
When User authenticates on the sample application  # llm-ok
```

The LSP skips validation for lines marked `# llm-ok`. The step still falls
through to the LLM at runtime — the comment is only a suppression directive for
the editor warning.

> Only use `# llm-ok` for steps that you have consciously decided to leave
> LLM-resolved. A pattern in `patterns.py` is always faster (no model round
> trip) and more deterministic.

### Pattern authoring tips

| Goal | Technique |
|------|-----------|
| Optional words ("the", "a") | `(?:the\s+)?` |
| Singular or plural verb | `verifies?` |
| Capture a quoted string | `'([^']+)'` or `["\'](.+?)["\']` |
| Strip surrounding quotes | wrap with `_q(m.group(n))` |
| Accept a variable write-target | patterns match the legacy `` `X` ``/`[X]` delimiters (see `set_var` pattern); a `{var:X}`/`{env:X}` ref that survives substitution is canonicalized to those by `normalize_phrasing` before matching, so one pattern covers both |
| Action targets a variable already substituted | variables are expanded *before* `resolve()` is called, so the pattern sees the final value |

### Testing your pattern

```bash
python3 -c "
from noodle.resolver.patterns import match, normalize_subject
step = 'verifies that the cart is displayed'
print(match(normalize_subject(step)))
"
```

A `None` result means the pattern didn't match. Check anchoring and quoting.

### Option C — let the agent find or draft it for you

Before doing any of the above by hand, ask:

```bash
noodle step-search "verify that the cart is displayed"
```

This is two components (NOOD_0026), both in the resolver/agent layers, not a
new resolution tier of their own:

- **The step-search-engine** (`noodle/resolver/step_search_engine.py`) —
  deterministic token-overlap + string-similarity ranking over
  `step_resolver.example_index()` (the same corpus `noodle steps` and the
  LSP hover already use — nothing re-parses the dictionary a second way).
  An LLM (`NOODLE_MODEL` — e.g. Claude Sonnet, or local Ollama) is consulted only when the
  ranking is genuinely ambiguous, purely as a tie-breaker — never required,
  never the primary mechanism.
- **The step-suggestion-engine** (`noodle/repl/step_suggestion_engine.py`)
  — when nothing matches well, drafts a phrasing + regex + the closest
  existing `action_type` (falling back to the LLM only to classify into
  `VALID_TYPES` if the nearest neighbor has none). It will only ever draft a
  new *phrasing* for a capability that already exists at runtime; if no
  `action_type` fits, it says so and writes nothing — that's still Option A/B
  territory, done by hand.

Accepting a suggestion (`y` at the `noodle repl` prompt, or
`noodle step-search "..." --accept`) does **not** touch `patterns.py`
directly — it stages the entry in `docs/agent_patterns.yaml`, checked as a
tier *after* `PATTERNS` in `match()` so it can never shadow a hand-written
pattern, plus an example in `docs/steps_dictionary.md`'s "Agent-Suggested
Steps (staging)" section. Splicing an automatically-drafted tuple into the
*right* spot in this order-sensitive file automatically would be fragile —
see the top of `patterns.py`'s own comment on this. Review staged entries
periodically; promoting one into `patterns.py` proper is still Option A
above, just with the regex/type/params already drafted for you.

### Checklist before you push

- [ ] Pattern added to `noodle/resolver/patterns.py`
- [ ] `VALID_TYPES` in `step_resolver.py` updated if you added a new `action_type`
- [ ] Runner (`orchestrator/runner.py`) handles the new action type in `execute_step`
- [ ] LSP warning gone in VS Code
- [ ] Quick smoke: `python3 -c "from noodle.resolver.patterns import match, normalize_subject; print(match(normalize_subject('your step text')))"` returns the expected action
- [ ] If this pattern started as a `noodle step-search` suggestion, delete its entry from `docs/agent_patterns.yaml` and the "Agent-Suggested Steps" section of `docs/steps_dictionary.md` now that it's promoted

---

## 16. Using an LLM — setup, providers, and modes

This section is written for someone who has never used an AI model or agent before.
No prior knowledge assumed. It covers the *mechanics* — which file to edit, which
variable to set, per-provider steps. For *choosing* a provider (cost tradeoffs,
local vs cloud, locked-down corporate accounts), the decision guide is
**[llm-setup.md](llm-setup.md)** — that doc owns provider selection; this one
owns setup.

### What is an LLM and why would I use one here?

An **LLM** (Large Language Model) is the same technology behind ChatGPT and Claude.
It can read plain English and interpret it.

Noodle Test Framework uses an LLM in two specific situations:

1. **A step phrase has no matching pattern.** Noodle Test Framework has 50+ built-in step
   patterns (`clicks the X button`, `enters Y in the Z field`, etc.). If you write
   a step that doesn't match any of them, the LLM can read your sentence and figure
   out what action to run. Without an LLM, the test would simply fail with a "no
   pattern matched" error.

2. **An element can't be found on the page.** If Noodle Test Framework can't locate a button or
   field by its label, the LLM can look at a screenshot and find it visually.
   Without an LLM, the test would fail with a "could not find element" error.

**You do not need an LLM to use Noodle Test Framework.** The default setup is fully local and
deterministic — no AI calls, no cost, no internet. Most test suites work perfectly
without it.

---

### Default behaviour — no LLM

Out of the box, with no configuration, Noodle Test Framework:

- Uses regex patterns to understand steps (fast, free, deterministic)
- Uses Playwright's accessibility tree to find elements on the page
- **Never makes any AI or LLM calls**
- Fails loudly (with a screenshot) if a step or element can't be resolved locally

This is the recommended default for CI pipelines and regression suites.

---

### How to enable an LLM — the two things you set

You need to set exactly two things:

| What | Which file | Variable |
|------|-----------|---------|
| Which LLM to use | `.env` | `NOODLE_MODEL` |
| Your API key (for cloud providers) | `secrets.env` | Provider-specific (e.g. `ANTHROPIC_API_KEY`) |

That's it. No code changes. No restarts.

> **Why two different files?**
> `.env` is committed to git — it's safe for settings but not secrets.
> `secrets.env` is gitignored — it's where passwords and API keys go.
> Putting your API key in `.env` would commit it to version control, which is a
> security risk. Always put keys in `secrets.env`.

---

### Step-by-step: pick a provider and turn it on

#### Option A — Free: Google Gemini (zero-cost way to try it)

Gemini has a free tier that requires no credit card. It is vision-capable, meaning
it can both interpret steps AND find elements on screen by looking at screenshots.

1. Go to [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and create a free API key.

2. Open `secrets.env` and add:
   ```bash
   GEMINI_API_KEY=your-key-here
   ```

3. Open `.env` and add:
   ```bash
   NOODLE_MODEL=gemini/gemini-1.5-flash
   ```

4. Install the LLM extra (once):
   ```bash
   uv pip install -e ".[llm]"
   ```

5. Run your tests as normal. Noodle Test Framework will now use Gemini as a fallback for
   steps and elements it can't resolve locally.

---

#### Option B — Free and fast: Groq (text only — no screenshots)

Groq is a free hosted service that runs open-source models at high speed.
It is **text-only** — it can interpret step phrases but cannot look at screenshots
to find elements. Good for step fallback, not for visual location.

1. Create a free account at [https://console.groq.com](https://console.groq.com) and generate an API key.

2. Open `secrets.env` and add:
   ```bash
   GROQ_API_KEY=your-key-here
   ```

3. Open `.env` and add:
   ```bash
   NOODLE_MODEL=groq/llama-3.1-8b-instant
   ```

4. Install the LLM extra (once):
   ```bash
   uv pip install -e ".[llm]"
   ```

---

#### Option C — Paid: Anthropic Claude (recommended default — best quality, vision-capable)

Claude is a paid service but has low per-call cost and is vision-capable.
As of NOOD_0151 `anthropic/claude-sonnet-5` is the framework's recommended
default (`anthropic/claude-haiku-4-5` for a cheaper tier).

1. Create an account at [https://console.anthropic.com](https://console.anthropic.com), add a payment method, and create an API key.

2. Open `secrets.env` and add:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   ```

3. Open `.env` and add:
   ```bash
   NOODLE_MODEL=anthropic/claude-sonnet-5
   ```

4. Install the LLM extra (once):
   ```bash
   uv pip install -e ".[llm]"
   ```

---

#### Option D2 — GitHub Copilot (uses your existing Copilot seat, no API key)

If you already have a GitHub Copilot subscription (work or personal), you can
use it as Noodle's LLM without a separate API key. `litellm` authenticates via
GitHub's device-flow login and caches the token locally.

1. Open `.env` and add (no `secrets.env` change needed):
   ```bash
   NOODLE_MODEL=github_copilot/claude-sonnet-4.5
   ```
2. Run your tests. The first call opens a device-code login in your terminal —
   follow the printed URL/code once in a browser. The token is then cached at
   `~/.config/litellm/github_copilot/`, so this is a one-time step.
3. Other model strings available through the same provider:
   `github_copilot/gpt-4o`, `github_copilot/gemini-2.5-pro`,
   `github_copilot/claude-opus-4.5` — run
   `python -c "import litellm; print(litellm.models_by_provider['github_copilot'])"`
   to see the full list for your installed `litellm` version.

> This is different from Copilot Chat in your IDE — that surface has no
> callable API. `github_copilot/<model>` goes through GitHub's own Copilot
> completions endpoint, which `litellm` added first-class support for.
> Requires internet and an active Copilot seat.

#### Option D — Local: Ollama (no internet, no account, no cost)

Ollama runs a model on your own machine. Nothing leaves your computer.
Requires a machine with a reasonable amount of RAM (8 GB+ recommended).

1. Install Ollama from [https://ollama.com](https://ollama.com).

2. Download a model (run this once in a terminal):
   ```bash
   ollama pull llama3          # text only
   ollama pull llava           # vision-capable (also installs llama3)
   ```

3. Open `.env` and add (no API key needed, no `secrets.env` change):
   ```bash
   NOODLE_MODEL=ollama/llava        # vision-capable
   NOODLE_LLM_URL=http://localhost:11434
   ```

4. Make sure Ollama is running before you run tests (`ollama serve` or the desktop app).

---

### Which provider should I pick?

Cost comparisons, local-vs-cloud tradeoffs, accuracy notes from live testing,
and the locked-down-corporate-account path (work GitHub/Azure only, no personal
API key) live in the decision guide: **[llm-setup.md](llm-setup.md)**. Short
version: Claude Sonnet as the recommended default, Gemini free tier to try
it at $0, Ollama for air-gapped/private,
Copilot seat if your company already pays for one.

One term you'll see there: **"vision-capable"** means the model can look at a
screenshot. Noodle Test Framework uses this when an element can't be found by
its label — it takes a screenshot, sends it to the model, and asks "where is
the Login button?". Without vision capability, only step-text fallback works
(the model reads words but not images).

---

### The mode toggle — `auto` vs `full`

`NOODLE_LLM_MODE` controls when the LLM is called. Edit this in `.env`.

#### `auto` (default — LLM as backup only)

```bash
# .env
NOODLE_LLM_MODE=auto     # this is the default; you can leave this line out entirely
```

Noodle Test Framework tries to resolve everything locally first:
- Step text → matched against 50+ built-in patterns (fast, free)
- If no pattern matches → asks the LLM
- Element location → scanned by Playwright's accessibility tree (fast, free)
- If element not found → asks the LLM (vision)

Most steps never touch the LLM at all. The LLM is only the last resort.
**Recommended for CI and regression suites.**

#### `full` (LLM resolves every single step)

```bash
# .env
NOODLE_LLM_MODE=full
```

Noodle Test Framework skips all pattern matching and accessibility scanning. Every step and
every element location goes directly to the LLM. This is slower and costs more
per test run, but it lets you write completely free-form test steps without
worrying about whether they match a pattern.

**Requires `NOODLE_MODEL` to be set.** `full` mode with no model is an error.

**Requires a vision-capable model** for element location (Claude Sonnet,
Google Gemini, OpenAI gpt-4o, Ollama llava). With a text-only model (Groq, llama3) in `full`
mode, Noodle Test Framework will warn you and fall back to the accessibility tree for elements.

**Recommended for:** exploratory testing, legacy app automation, writing tests
without learning the step vocabulary first.

---

### Quick reference — what goes where

```
.env                          ← edit this for model and mode settings (committed to git)
  NOODLE_MODEL=...
  NOODLE_LLM_MODE=...
  NOODLE_LLM_URL=...        ← only for Ollama / Foundry Local / self-hosted

secrets.env                   ← edit this for API keys (gitignored — never committed)
  ANTHROPIC_API_KEY=...
  GEMINI_API_KEY=...
  GROQ_API_KEY=...
  OPENAI_API_KEY=...
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `No pattern matched` error with no model set | LLM not enabled | Add `NOODLE_MODEL` to `.env` |
| `NOODLE_LLM_MODE=full but NOODLE_MODEL is not set` | Full mode needs a model | Add `NOODLE_MODEL` to `.env` |
| `LLM support requires: pip install noodle[llm]` | Extra not installed | Run `uv pip install -e ".[llm]"` |
| `AuthenticationError` or `401` | Wrong or missing API key | Check `secrets.env` for the right key name |
| Vision-locate warning: `is NOODLE_MODEL vision-capable?` | Text-only model used with full mode | Switch to a vision-capable model (see [llm-setup.md](llm-setup.md)) or use `auto` mode |
| Ollama: `ConnectionRefused` | Ollama not running | Run `ollama serve` in a terminal first |
</content>

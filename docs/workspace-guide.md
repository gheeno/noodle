# Workspace guide — for manual testers
<!-- Branch: NOOD_0062 -->

Full walkthrough for setting up your own test workspace outside the
`noodle` engine repo: scaffolding, writing tests, mapping page objects
(POM), custom scripts, naming rules, and reports. Linked from the main
[README](../README.md).

This is the walkthrough for a manual tester (or a QE lead) who wants their
own test folder — call it `noodle_tests`, `my_tests`, `qa_home`, whatever —
living **outside** the `noodle` engine repo, owned entirely by their own
team. The mental model: `noodle` is a tool you install, like `git` or
`node`. Your workspace is the project it operates on — your home. Nothing
about your tests, secrets, page objects, custom scripts, or reports ever
needs to live inside — or touch — the `noodle` repo itself.

**Terminology (NOOD_0155).** "Noodle workspace" is a formal term: the
self-contained folder scaffolded by `noodle init <path>` (templates
refreshed by `noodle init --force`). It is one of the three canonical
nouns — **engine** (the framework), **workspace** (this folder), **wok**
(a capability work area: web/mobile/desktop/performance) — defined once in
[glossary.md § The three nouns](glossary.md#the-three-nouns--engine-workspace-wok).

That workspace is meant to be **fully self-contained**: config, secrets,
base URLs, page objects, custom scripts, test data, and every report a run
produces (including the pass/fail trend history) all live under one root
you own and can back up, zip, or hand to a teammate as-is.

# 1. Set up the workspace

```bash
pip install noodle              # once — installs the engine as a CLI tool
noodle init ~/my-tests           # scaffolds a fresh, runnable workspace
cd ~/my-tests
```

`noodle init <path>` writes everything below into `<path>` — and re-running
it on an existing workspace is the **upgrade path** (NOOD_0089): engine-glue
files are auto-synced to the installed engine, drifted template files are
reported (refresh with `--force`, originals backed up to `*.bak`), and your
config files (`.env`, `noodle.yaml`, `pom.yaml`) are never touched — the
workspace stays yours even across noodle upgrades:

```
my-tests/                     ← your workspace — name it anything
├── noodle.yaml                  config: tests_dir, browser, headless
├── README.md                    auto-written next-steps cheat sheet
├── AGENTS.md                    instructions for AI coding agents — auto-read (see below)
├── PROMPT_TEMPLATE.md           copy-edit-paste prompt for any agent (NOOD_0089)
├── .env                         run-wide settings — safe to commit
├── secrets.env                  credentials — YOU create this, gitignore it
├── environments.yaml             base URLs — YOU create this
└── noodle_tests/                ← the test folder (rename via tests_dir)
    ├── environment.py            engine glue — never edit
    ├── steps/z_catch_all.py      engine glue — never edit
    ├── pom.yaml                  global page objects, shared by every app
    └── sample_app/               a template app package to copy
        ├── features/login.feature
        ├── resources/pageobjects/login_pom.yaml
        └── report/               this app's run output lands here
```

Every app-under-test gets its own package like `sample_app/` — features,
resources and report side by side. Running a single app
(`noodle run noodle_tests/<app>`) writes that run's whole artifacts tree
(results, Allure + RCA reports, screenshots, trend history) into its
`report/` folder, so multiple apps in one workspace keep their results
fully isolated. `noodle summary` / `noodle report serve` follow the last
run automatically; workspace-wide runs use `artifacts/` as before. Every
noodle command also works from inside the app folder itself — `cd
noodle_tests/<app>` then `noodle run`, `noodle summary`, `noodle report
serve`, `noodle archive`.

**Generating tests with an agent?** `PROMPT_TEMPLATE.md` is a fill-in-the-
brackets prompt (app, base URL, user goal, human steps, what to verify,
credentials) that you paste into Claude Code / Copilot / any MCP host. It is
paste-clean by construction (NOOD_0107): every line flush-left, no markdown
indentation, no hard-wrapped sentences — it survives a code block, a
Teams/Slack chat, or any plain-text editor intact.

**Skip the prompt entirely and the rules still apply** (NOOD_0107): agents
launched inside the workspace pick up `AGENTS.md` automatically —
Claude Code auto-loads `CLAUDE.md`, whose `@AGENTS.md` import inlines the
full instructions; Copilot CLI and VS Code Copilot read `AGENTS.md`
natively (VS Code setting `chat.useAgentsMdFile`, on by default). Plain
files on every OS — no symlinks, so Windows 11 checkouts behave the same
as macOS.
`noodle init` already wired the MCP server config for both clients
(NOOD_0095); re-run `noodle init mcp --force` only if that config drifted.

Add `--llm ollama|claude|gemini` (+ `--model`) if you want `noodle repl`
to understand free-form chat instead of the fixed `create test for ... at
...` phrasing from the very first command — see
[LLM augmentation](manual.md#llm-augmentation-optional).

Nothing here is committed to the `noodle` repo, and nothing in `noodle`
needs to know this workspace exists. `git init` this folder separately if
your team wants its own history/PR review for test changes — that repo is
now yours to branch, review and release on your own schedule.

# 2. Generate test cases & supporting files

Two ways to write tests, both entirely inside your workspace:

**A. Copy the template, hand-write Gherkin.** Copy `noodle_tests/sample_app/`
to `noodle_tests/<your-app>/` (keep its `features/`, `resources/` and
`report/` subfolders), then write steps in plain English against the vocabulary in
[steps_dictionary.md](steps_dictionary.md) — e.g.:

```gherkin
Feature: Login

  Scenario: Valid user logs in
    Given User is on '{env:BASE_URL}'
    When User enters 'standard_user' in the username field
    And User enters '{env:MY_PASSWORD}' in the password field
    And User clicks the 'Login' button
    Then User should see 'Products'
```

Check any step resolves without opening a browser:
`noodle validate noodle_tests/ --resolve`.

**B. Let `noodle repl` write it for you.** Chat in plain English —
useful for testers who'd rather describe a scenario than hand-write
Gherkin:

```bash
noodle repl --workspace ~/my-tests
noodle> create a test for the login page at https://example.com/login, log in with standard_user, and I should see Products
→ Wrote noodle_tests/web/example/features/login.feature
→ Wrote noodle_tests/web/example/resources/pageobjects/login_pom.yaml
→ Wrote noodle_tests/web/example/resources/example_environments.yaml
→ Wrote noodle_tests/web/example/resources/example_secrets.env
```

**Supporting files** — each app-under-test's `resources/` folder is
self-contained, so one app's tests never leak into another's:

| File | Holds | Referenced in a feature as |
|---|---|---|
| `resources/<app>_environments.yaml` | base URL(s) | `{env:BASE_URL}` |
| `resources/<app>_secrets.env` (gitignored) | credentials | `{env:MY_PASSWORD}` |
| `resources/pageobjects/*_pom.yaml` | locators for one page | element names, see §3 |
| `resources/pom.yaml` | locators shared across this app's pages | element names, see §3 |
| `resources/functions/*.py` | custom in-process Python (§4) | `calls the function '...'` |
| `resources/payloads/*.json` | REST request bodies | `load_data` step |
| `resources/data/*.csv` | table-driven test data | `fill_form_table` etc. |
| `resources/preconditions.yaml` | setup steps run before a scenario | `@precondition:<name>` tag |

Full contract: [feature-packages.md](feature-packages.md).

# 3. Map page objects (POM) back to your feature files

A feature step never hard-codes a CSS selector — it names an element, and
Noodle resolves that name to a real locator through a fixed lookup chain,
nearest file first:

1. `resources/pageobjects/<page>_pom.yaml` — the file whose name (minus
   `_pom.yaml`) matches the page you're on, e.g. `login_pom.yaml` for the
   `login` page.
2. `resources/pom.yaml` — shared across every page in this one app.
3. `noodle_tests/pom.yaml` — the global file, shared across every app in the
   workspace (nav bars, cookie banners, anything truly universal).

```yaml
# noodle_tests/web/example/resources/pageobjects/login_pom.yaml
username field:
  id: "user-name"
password field:
  id: "password"
login button:
  css: "input[type='submit']"
```

```gherkin
When User clicks the 'login button'      # resolves via login_pom.yaml
And User clicks the 'cookie accept'      # falls through to noodle_tests/pom.yaml
```

Selector types: `css | xpath | id | testid | text | label | placeholder |
title | alt_text | role`. `text`/`label`/`placeholder`/`title`/`alt_text`
accept an optional `exact` flag (`{ placeholder: "Username", exact: true }`);
`role` accepts an optional accessible-name `name` (`{ role: { type: button,
name: "Login" } }`), or stays a bare string (`role: navigation`) for a
role-only match. You never wire
up *which* POM file applies to *which* feature by hand — Noodle resolves it
automatically from the feature file's own location, and, for multi-page
apps, from the current page's `match: { url_contains: ... }` block — one
`_pom.yaml` per page is the entire contract.

**Same key name, many pages** — two per-page files can both define
`search field`; each file's *filename* auto-scopes it (no `match:` needed):

```yaml
# resources/pageobjects/home_pom.yaml   — auto-scopes to any URL containing "home"
search field: { css: "input.home-search" }
```
```yaml
# resources/pageobjects/results_pom.yaml — auto-scopes to any URL containing "results"
search field: { css: "input.results-filter" }
```

Only the file whose scope matches the *live* URL is even consulted, so
identical key names in hundreds of page files never collide. The same thing,
written explicitly in one file (equivalent, useful when several pages share
a file):

```yaml
pages:
  home:    { match: { url_contains: "example.com/$" }, search field: { css: "input.home-search" } }
  results: { match: { url_contains: "/results" },       search field: { css: "input.results-filter" } }
shared:
  cookie accept: { id: onetrust-accept-btn-handler }   # checked on every page
```

**Pin the page instead of relying on the URL** — URL-based scoping means a
route rename (`/results` → `/search-results`) breaks the match even though
the selector itself (`input.results-filter`) is still perfectly valid. Two
ways to bind a page explicitly, so a URL rename can't touch it:

```gherkin
Given User is on the "results" page       # pins for the rest of this scenario
```
```gherkin
# @page:results pins BEFORE the scenario starts — useful when a scenario
# deep-links straight to a page instead of navigating there from another one,
# so there's no earlier step to hang the pin on.
@web @page:results
Scenario: Deep-linked results page resolves via the pinned page, not the URL
  Given User is on "https://example.com/en/search-results.html?q=office chair"
  Then User should see "Office Chair"
  When User clicks the firstresult
```

The tag wins as the up-front default; a later `is on the "<name>" page` step
(if present) can still override it mid-scenario — useful for a scenario that
*starts* pinned to one page and later moves to another. Both call the exact
same pin used by `pages:` blocks above — no new selector format, just a
different way to pick the active one. Real, runnable version of the example
above (the actual page objects and live selectors): see the second scenario
in
[`sample_feature_tests/web/example/features/example.feature`](../sample_feature_tests/web/example/features/example.feature).

**The page name comes from the filename, and that only works if the folder
structure is the fixed one**
([§5's table](#5-naming-whats-free-whats-fixed)): `resources/pageobjects/<page>_pom.yaml`
under the app the running feature belongs to. `@page:results` means "the
`results` block *from this app's own `resources/pageobjects/` folder*" —
Noodle finds that folder from the currently-running `.feature` file's own
location, never from anywhere else. Two different apps can both ship a
`results_pom.yaml` and never collide, because each app's feature files only
ever see their own app's `resources/`, not a sibling app's. The only place a
name collision is possible is *within* one app, if that app's own `pom.yaml`
or the workspace's global `noodle_tests/pom.yaml` also defines a `results` page
block — ordinary local-beats-global rules apply there, same as unpinned
lookups.

**Force one exact element: `{pom:key}`** — a bare phrase like `search field`
still tries the accessibility tree *before* pom.yaml, which is normally what
you want (it self-heals through markup changes). When you don't want that —
a known icon-only element, or right after a redesign when you don't yet
trust what the accessibility tree will match — write `{pom:name}` to go
straight to the pom.yaml chain and nowhere else:

```gherkin
When User clicks the {pom:burger menu}
```

`{pom:burger menu}` skips accessibility, self-heal, and the vision LLM entirely.
If no pom.yaml entry named `burger menu` exists in the chain (page → app →
global), the step fails immediately with that chain spelled out — it never
silently guesses. Plain, unbraced names are unaffected and keep the full
five-step resolution order.

# 4. Custom steps: your own scripts, any language

Three step verbs let a scenario shell out to a script/binary in **any**
language, or call an in-process Python function and use its real return
value in a later step — the "step dependency injection" a Java/Cucumber
team would recognize as a step-class method call:

```gherkin
# Run any script — interpreter inferred from the extension
# (.py, .js/.mjs, .jar, .sh, .rb, .pl — anything else must be executable)
When User runs the script 'scripts/seed_db.py' with args '--env staging'
When User runs the command 'curl -s https://example.com/health'
  storing output in {var:HEALTH}

# Call a Python function in-process — get its actual return value, not
# just stdout. Spec is 'path/to/file.py:function' or 'importable.module:function'.
Given User calls the function 'resources/functions/helpers.py:make_username'
  and saves the result as {var:USERNAME}
When User calls the function 'resources/functions/helpers.py:greet'
  with args '{var:USERNAME}'
Then {var:FUNCTION_RESULT} should contain 'Hello'
```

That last example is the dependency-injection pattern: `{var:USERNAME}`
is a value captured at run time from an earlier step — any
step that stores a variable (`call_function`, `run_script`, `store text`,
a REST `rest_extract_json`, ...) feeds any later one. This is different
from `{env:NAME}`, which always comes from `.env`/`environments.yaml` —
config set before the run, not produced during it.

Your custom `.py` files live under your own `resources/functions/` — pure
project code, versioned with your tests, never touching the `noodle` repo.
Full reference (more examples, the interpreter table, exception handling):
[steps_dictionary.md § Scripts & Shell Commands](steps_dictionary.md#scripts--shell-commands).

Need a brand-new **English phrasing** rather than a new script? See
["Adding custom steps from your own test repo"](manual.md#adding-custom-steps-from-your-own-test-repo)
above — `noodle step-search "<description>" --accept --workspace <path>`
stages it into your workspace's own `docs/`, never the engine's.

# 5. Naming: what's free, what's fixed

| Thing | Free to rename? | How |
|---|---|---|
| The workspace folder itself | **Yes** — any name, anywhere on disk | just pick the path for `noodle init` |
| The test folder (default `noodle_tests/`) | **Yes** | `noodle.yaml`'s `tests_dir:` key |
| Each app-under-test's folder name (`noodle_tests/<type>/<app>/`) | **Yes** — `<type>` and `<app>` are free-form; convention: `<type>` = the **wok** name (web/mobile/desktop/performance — [woks.md](woks.md)) | create whatever folder you like under `tests_dir` |
| `<app>/features/`, `<app>/resources/`, `<app>/report/` | **No** — fixed subfolder names | this is the one structural contract (§2's table) |
| `noodle_tests/environment.py`, `noodle_tests/steps/` (name + location) | **No** | `behave` itself requires these exact names/positions — not a Noodle convention |
| `resources/pageobjects/<page>_pom.yaml` | Page name is free; `_pom.yaml` suffix is fixed | lets Noodle infer the page name from the filename |

`noodle init` already names the tests root `noodle_tests` precisely so it
never collides with a host project's own `tests/` folder — no rename needed
when pointing a workspace inside someone else's project.

# 6. Generate & serve reports (with history)

Everything a run produces lands under one root inside your workspace —
`<app>/report/` when the run targets a single app, `artifacts/` for a
workspace-wide run (override with `NOODLE_ARTIFACTS_DIR`) — nothing is
written outside the workspace, ever. Follow-up commands find the last
run's root automatically:

```bash
noodle run --workspace ~/my-tests --headless   # whole workspace → artifacts/
noodle run -w ~/my-tests/noodle_tests/app1 --headless   # one app → app1/report/

noodle report generate --workspace ~/my-tests   # NOOD_0052 — no cd needed
noodle report open --workspace ~/my-tests
noodle report serve --workspace ~/my-tests      # share on the network
```

From `noodle repl` (which always runs *inside* the workspace it was
started with, so this doesn't apply): `generate the report` isn't a chat
phrase yet — run `report generate` from the CLI first, then say **"open
the report"** in chat to open it (same `allure open` under the hood, no
flags to remember). ("what failed" / "summary" gives a quick plain-English
pass/fail instead, without a browser.)

**Trend history** (the Allure report's History/Duration/Retry graphs across
runs) accumulates automatically: every `report generate` seeds this run's
results from the *previous* report's `history/` folder before building, so
consecutive runs plot a real trend instead of resetting every time.
`noodle clean` (wipes `artifacts/` before the next run) preserves this
history by default — pass `--purge-history` for a true full wipe:

```bash
noodle clean --workspace ~/my-tests                  # keeps trend history
noodle clean --workspace ~/my-tests --purge-history   # wipes it too
noodle archive --workspace ~/my-tests                 # zip artifacts/ first, if you want a snapshot
```

Other report/artifact commands, all `--workspace`-aware:

```bash
noodle summary    --workspace ~/my-tests             # plain pass/fail
noodle rca-report --workspace ~/my-tests --llm        # why each failure happened
noodle artifacts  --workspace ~/my-tests              # what's in artifacts/, by category, with sizes
```

Or, from `noodle repl`, say **"serve the rca report"** — regenerates the
RCA and serves a styled HTML table over `localhost` in one step, no
`--out`/`generate`/`open` sequence to remember.

Full location table (screenshots, traces, videos, logs, RCA):
[Where results go after a run](manual.md#where-results-go-after-a-run).


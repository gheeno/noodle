# Noodle manual — setup, tests, reports, reference

> The complete manual, moved out of the README front door (NOOD_0110): full
> setup guide, writing and running tests, RCA, syntax, LLM augmentation,
> `noodle repl`, CI, quick reference, and troubleshooting.
> New here? Start with the [README](../README.md) — quickstarts live there.

## Setup guide

This is the detailed, manual version of the
[Zero to hero](../README.md#zero-to-hero--copypaste-path) block above — read it when you
want to understand (or control) each step instead of pasting one block.
Follow Parts 1–7 top to bottom, in order. Every command below is shown for
**macOS** and **Windows 11** — pick your column and copy/paste as you go.

### Part 1 — Install prerequisites (Windows 11 / macOS)

**On a brand-new Mac**, two things ship missing that everything below
depends on — do these first:

```bash
xcode-select --install    # Command Line Tools; Homebrew (and even bare `git`) needs these.
                           # Opens a GUI installer — let it finish (5-15 min) before continuing.
                           # Already have them? This just errors harmlessly — skip and move on.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                           # Installs Homebrew itself. Skip if `brew --version` already works.
                           # Follow the "Next steps" it prints to add brew to your PATH, then
                           # close and reopen your terminal.
```

**On a brand-new Windows 11 machine**, `winget` is preinstalled (via App
Installer) so no separate bootstrap is needed — but watch for the
Microsoft Store Python alias trap below the table.

| Tool | Why | macOS | Windows 11 (PowerShell) |
|---|---|---|---|
| Python 3.11+ | runs the framework | `brew install python@3.11` | `winget install Python.Python.3.11` |
| Git | clones the repo | usually preinstalled — else `brew install git` | `winget install Git.Git` |
| [uv](https://docs.astral.sh/uv/) | fast Python package manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Node.js 18+ | runs the bundled BusterBlock test app | `brew install node` | `winget install OpenJS.NodeJS.LTS` |
| Allure 3 CLI | generates HTML test reports | `npm install -g allure` *(after Node.js above)* | `npm install -g allure` *(after Node.js above)* |
| VS Code *(optional, recommended)* | editing `.feature` files with syntax colour | `brew install --cask visual-studio-code` | `winget install Microsoft.VisualStudioCode` |
| Ollama *(optional — local AI only)* | runs AI models on your own machine, free | `brew install ollama` | `winget install Ollama.Ollama` |

Close and reopen your terminal after installing so the new tools are on
your `PATH`, then verify everything:

```bash
# macOS
python3 --version      # should say 3.11 or higher
git --version
node --version          # should say 18 or higher
allure --version        # should say 3.x
ollama --version        # only if you installed it
```

```powershell
# Windows 11 (PowerShell)
python --version        # should say 3.11 or higher
git --version
node --version           # should say 18 or higher
allure --version         # should say 3.x
ollama --version         # only if you installed it
```

> **Windows: `python --version` opens the Microsoft Store instead of
> printing a version?** Fresh Windows 11 installs ship "App execution
> aliases" for `python`/`python3` that launch the Store instead of running
> a real interpreter — it can still shadow the real one after
> `winget install Python.Python.3.11` if PATH ordering doesn't win. Fix:
> Settings → Apps → Advanced app settings → App execution aliases → turn
> off the `python.exe`/`python3.exe` entries, then open a new terminal and
> retry.

> **Windows: about to run `.venv\Scripts\Activate.ps1` in Part 2 below and
> worried it'll fail?** Default PowerShell security blocks activation
> scripts with `cannot be loaded because running scripts is disabled on
> this system`. Fix once, no admin needed: `Set-ExecutionPolicy -Scope
> CurrentUser -ExecutionPolicy RemoteSigned`, then retry.

### Part 2 — Get the framework

Two ways to end up with a working `noodle` command — same on macOS and
Windows 11, pick one before you start:

| | Option B — permanent PATH (default below) | Option A — per-terminal |
|---|---|---|
| Command | `uv tool install --editable ".[all]" …` | `source .venv/bin/activate` / `.venv\Scripts\Activate.ps1` |
| You run it | once, ever | once per new terminal window, every time |
| Best for | just using Noodle to run tests | hacking on Noodle itself |
| Jump to | right below | [Option A further down this Part](#option-a--per-terminal-venv-hacking-on-noodle-itself) |

```bash
git clone https://github.com/gheeno/noodle.git
cd noodle

uv tool install --editable ".[all]" --with-executables-from playwright
playwright install chromium     # installs the browser Noodle drives
uv tool update-shell            # adds uv's tool bin dir to PATH, if it isn't already
```

`noodle` (with its `repl` subcommand), `noodle-mcp`, and `playwright` are now
on your `PATH` everywhere, backed by an isolated env `uv` manages for you —
no `cd`, no `source`, no `.venv` to remember. `--editable` means `git pull`
in this repo still updates the global commands.

> **`noodle: command not found` right after `uv tool install`?** The tool
> bin dir (`~/.local/bin` on macOS/Linux, `%USERPROFILE%\.local\bin` on
> Windows — check either with `uv tool dir --bin`) isn't on `PATH` yet —
> `uv tool update-shell` (above) edits your shell profile (`.zshrc`/`.bashrc`,
> or adds it to the Windows user `PATH` registry key) to add it. **Open a
> new terminal window afterward** — the edit only
> takes effect in shells started after it ran.

Want LiteLLM-backed manual mode too (a cloud provider or Ollama, for
`noodle repl`/`noodle run` without an AI coding agent driving it)? It's
deliberately left out of the command above — `llm` is the one extra with a
real chance of a from-source build (see the certificate callout below), so
keeping it separate means it can never block the base install. Add it once
the base install above works:

```bash
uv tool install --editable ".[all,llm]" --with-executables-from playwright --force
```

> **That fails deep in a build with `CERTIFICATE_VERIFY_FAILED`, on a
> corporate/managed ("controlled") network?** See
> [Troubleshooting](#troubleshooting) — it's almost always a TLS-inspecting
> proxy your OS trusts but the Python build tooling doesn't. This can't
> take down the base install above — `llm` installs separately, and you
> don't need it at all for MCP mode or no-LLM manual mode.

#### Option A — per-terminal venv, hacking on Noodle itself

Only worth it if you're also editing Noodle's own code (a project-local
`.venv` means a global upgrade can't break a different project). Just
using Noodle to run tests? Use Option B above instead.

```bash
git clone https://github.com/gheeno/noodle.git
cd noodle

uv venv                          # creates .venv — required before `uv pip install` below
```

`uv pip install` is uv's pip-compatible interface — unlike `uv sync`/`uv run`,
it does **not** create a `.venv` for you. Run `uv venv` first (above), then
activate it, before anything else — or `uv pip install` will fail with
`error: No virtual environment found`, and `playwright install chromium`
below (and every `noodle` command after it) will silently run against your
system Python instead of this project's:

```bash
# macOS
source .venv/bin/activate
```

```powershell
# Windows 11 (PowerShell)
.venv\Scripts\Activate.ps1
```

**Every new terminal window needs that activate line again** before
`noodle`/`playwright` commands work — that's the #1 "it worked yesterday"
gotcha. Command not found, or an old/mismatched Playwright browser error?
You forgot to activate. See [Troubleshooting](#troubleshooting).

```bash
uv pip install -e ".[all]"      # every extra except `llm` — MCP mode and no-LLM manual mode need nothing else
playwright install chromium     # installs the browser Noodle drives
uv pip install -e ".[llm]"      # optional — LiteLLM-backed manual mode (cloud provider or Ollama)
```

`llm` is deliberately a separate command, same reasoning as Option B above
— see the certificate callout there if it fails on a corporate network.

### Part 3 — VS Code + syntax highlighting (optional, recommended)

Gives `.feature` files Gherkin colouring, `{env:...}`/`{var:...}` highlighting,
step-validation squiggles, and `@tag` autocomplete. Same steps on both OS —
this uses the `Makefile` target already in the repo, not a manual symlink.

```bash
npm install -g @vscode/vsce          # one-time — packages the extension
cd vscode-extension && npm install && cd ..
make install-ext                     # builds and installs into VS Code
```

> No `make` on Windows? Run the two commands inside `make install-ext`
> directly: `cd vscode-extension && npx @vscode/vsce package --allow-missing-repository --skip-license --out ../noodle.vsix`,
> then `code --install-extension ../noodle.vsix --force`. The two package
> flags skip two interactive `[y/N]` prompts (`vsce` refuses to package
> without an answer) — safe either way since this build never publishes to
> the Marketplace. `--force` on the install matters even on a first-ever
> install of this repo: the extension's own manifest version never changes
> between Noodle releases, so if *anything* ever sideloaded a `noodle`
> extension on this machine before (an earlier attempt, a different clone,
> an agent that already tried), VS Code sees "same version already
> installed" and silently skips the reinstall without `--force` — the
> extension shows as installed but keeps running whatever old build got
> there first, which is why `.feature` files can stay uncoloured even
> after a full quit and reopen.

This needs the `code` command on your `PATH`. The Windows installer adds it
automatically (leave "Add to PATH" checked). On macOS, open VS Code, open
the Command Palette (`Cmd+Shift+P`), and run **Shell Command: Install
'code' command in PATH**.

Fully quit VS Code (`Cmd+Q` on macOS, close all windows on Windows — not
just close the window) and reopen. If you have the **Cucumber** extension
installed, disable it for this workspace first — both extensions bind
`.feature` files and conflict. Full details:
[docs/encyclopedia.md § 12](encyclopedia.md#12-vs-code-extension).

**`.feature` files still plain black-and-white after a full quit and
reopen — no colour at all, not even keywords?** This is different from the
squiggles/hover issue below (that's the language server; this is the
syntax grammar not being applied at all) and almost always means the
install itself didn't take, not that it needs another reload:
1. Confirm it's actually installed: `code --list-extensions | grep noodle`.
   Nothing? The install command failed silently — re-run it and read the
   output this time.
2. Something there, but still no colour? Re-run the install with
   `--force` (`make install-ext` already does this; the manual command is
   `code --install-extension <path-to-vsix> --force`) — a same-version
   reinstall is silently skipped without it, see the callout above. Then
   fully quit and reopen again.
3. Still nothing? Open any `.feature` file, `Cmd+Shift+P` → **Developer:
   Inspect Editor Tokens and Scopes**, click into the text. It should say
   `source.noodle` somewhere in the scope list — `source.plaintext` or no
   scope at all means a different extension (Cucumber, or another Gherkin
   highlighter) is claiming `.feature` files instead; disable it for this
   workspace and reopen.

**No squiggles or hover tooltips at all on a fresh machine?** The extension
starts the language server with the workspace `.venv` Python it auto-detects
(falling back to `python3`, or `python` on Windows). If noodle is installed
somewhere else — Option B's `uv tool install`, or a different venv — point
the extension at that interpreter in `.vscode/settings.json`:

```json
{ "noodle.pythonPath": "/absolute/path/to/python" }
```

**Squiggles look stale after a `git pull` or a `patterns.py` edit?** The
language server is a long-lived process — it doesn't reload its own code.
`Cmd+Shift+P` → **Developer: Reload Window** restarts it. See
[docs/encyclopedia.md § 12 — Resetting the language server](encyclopedia.md#resetting-the-language-server).

### Part 4 — Configure

```bash
cp secrets.env.example secrets.env   # fill in your credentials
```

Three config files, each with a clear role:

| File | What goes here | Committed? |
|------|---------------|------------|
| `environments.yaml` | Base URLs (`{env:SAUCEDEMO}`, `{env:STAGING}`) | ✅ yes |
| `.env` | Browser and run settings — no secrets | ✅ yes |
| `secrets.env` | Credentials, API keys | ❌ gitignored |

The defaults already work for the tests in this guide — you don't need to
edit anything yet. See **[docs/glossary.md](glossary.md)** for a full
map of every env var and where it lives.

Each app-under-test (e.g. `sample_feature_tests/web/busterblock/`) can keep its own
`resources/` folder with a package-scoped, app-prefixed `.env`/
`<app>_secrets.env`/`<app>_environments.yaml` instead of using the root
files — see **[docs/feature-packages.md](feature-packages.md)**.

### Part 5 — Start BusterBlock, the bundled test app

`test-apps/busterblock/` is **BusterBlock.ca**, a self-contained VHS-rental site
(Node/Express, in-memory data) that ships with the framework so you have
something real to test immediately — no internet, no account, no external
site needed.

**Terminal A — keep this running the whole time you're testing:**

```bash
cd test-apps/busterblock
npm install          # first time only
npm start            # → http://localhost:3333
```

You should see `Server running at http://localhost:3333`. Open that URL in
a browser to confirm — login is `reel_ryan` / `Popcorn1!`. (The Noodle-side
credentials file for testing it — `busterblock_secrets.env` — gets copied in
Part 6 below, once your test workspace exists.)

**To stop it:** `Ctrl+C` in Terminal A, or from another terminal:

```bash
lsof -ti:3333 | xargs kill        # macOS/Linux
```

`test-apps/erp/` is **BeanCounter ERP**, a self-contained Flask + SQLite ERP
test app — the enterprise sibling of BusterBlock, for grid/CRUD/dashboard
coverage. Optional; only start it if a test needs it.

**Terminal C — keep this running for the duration of any ERP test:**

```bash
cd test-apps/erp
pip install -r requirements.txt   # or: uv run --with flask app.py
python app.py                     # → http://localhost:4444
```

Login is `bean_barry` / `Lentils1!`. See `test-apps/erp/README.md` for the
full API/test surface.

**To stop it:** `Ctrl+C` in Terminal C, or from another terminal:

```bash
lsof -ti:4444 | xargs kill        # macOS/Linux
```

### Part 6 — Run your first tests

Tests don't live inside this repo — `noodle` is a tool you install once,
like `git`. Scaffold a real workspace for them, **next to** (not inside)
your `noodle` clone, by copying the bundled sample suites out and
initializing that copy:

```bash
# macOS/Linux — from inside the noodle/ clone
cd ..                                                       # up to noodle's parent folder
cp -R noodle/sample_feature_tests ./sample_feature_tests    # ⚠️ destination must NOT already exist —
                                                              # cp nests the folder inside it otherwise
noodle init sample_feature_tests --force                    # scaffolds noodle.yaml, .env, MCP config, agent skills
cd sample_feature_tests
cp web/busterblock/resources/busterblock_secrets.env.example \
   web/busterblock/resources/busterblock_secrets.env
```

```powershell
# Windows 11 (PowerShell) — from inside the noodle\ clone
cd ..
Copy-Item -Recurse noodle\sample_feature_tests .\sample_feature_tests   # ⚠️ destination must NOT already
                                                                          # exist — Copy-Item nests the
                                                                          # folder inside it otherwise
noodle init sample_feature_tests --force
cd sample_feature_tests
Copy-Item web\busterblock\resources\busterblock_secrets.env.example web\busterblock\resources\busterblock_secrets.env
```

**Edge cases — pathing & OS:**

- **Pick a destination that doesn't exist yet.** Both `cp -R src dst` and
  `Copy-Item -Recurse src dst` *nest* `src` inside `dst` instead of becoming
  it when `dst` already exists (you'd end up with
  `sample_feature_tests/sample_feature_tests/...`) — if you're re-running
  this, delete or rename the old copy first, or pick a new folder name
  (`my-tests`, `qa-tests`, …).
- **Any folder name works.** `sample_feature_tests` above is just what the
  bundled suites are already called — rename the copy to whatever you like
  (`my-tests`) and everything below still applies; only the `cd` and path
  arguments change to match.
- **`noodle` path *arguments* always use forward slashes**, even on
  Windows — `web/busterblock`, not `web\busterblock` — Python accepts `/` in
  paths on Windows too and PowerShell/`cmd.exe` don't rewrite it. Plain
  filesystem operations (`Copy-Item`, `cd`) use native Windows paths as
  shown above.
- **Re-running `noodle init --force` is safe** any time you refresh your
  copy from a newer `noodle` clone — it re-syncs engine glue and template
  files (backing up any you've since edited to `*.bak`) without touching
  your `noodle.yaml`, `.env`, or POM files.

`noodle init --force` writes `noodle.yaml`, `.env`, `README.md`, `AGENTS.md`,
MCP client config, and the `/noodle` agent skill into the copied folder —
everything needed to treat it as a real, agent-ready workspace. The engine
glue your copied `web/`, `api/`, etc. suites need (`environment.py`,
`steps/z_catch_all.py`) already travelled with them in the copy — behave
finds it automatically by walking up from whichever suite you run, no
extra wiring needed. `init` also scaffolds a fresh `noodle_tests/sample_app/`
alongside — that's the starting point for tests you write yourself from
scratch (see [Write your first test](#write-your-first-test) below), kept
separate from the bundled samples you just copied in.

**Terminal B — everything below runs here, from inside your new workspace**
(leave Terminal A running BusterBlock):

```bash
noodle run web/busterblock --headless
```

This is a **single-app run**, so its entire output tree — allure-results,
screenshots, reports, trend history — lands in
`web/busterblock/report/` (inside your workspace, not the noodle repo),
keeping every app-under-test self-contained. (A workspace-wide `noodle run`
with no app path still uses `artifacts/`.) You can also `cd web/busterblock`
and just type `noodle run --headless` — same result.

Expected: all non-LLM tests pass. The two LLM-only feature files
(`llm_fallback.feature`, `pure_llm.feature`) are skipped unless a model is
configured — see [LLM augmentation](#llm-augmentation-optional) below.

Run just one file, or filter by tag:

```bash
noodle run web/busterblock/features/login.feature --headless
noodle run web/busterblock --tag @smoke --headless
```

Still hacking on Noodle itself and want to run the bundled samples in place,
without copying them out? They still work directly inside the engine clone
— `noodle run sample_feature_tests/web/busterblock/ --headless` — this repo
is its own workspace (see its `noodle.yaml`). A real test workspace should
live outside it, as above.

### Part 7 — Generate & view the report

Every run already writes both reports (Allure HTML + `rca.md`/`rca.html`)
into `<run root>/reports/` — the app's own `report/reports/` for a
single-app run, `artifacts/reports/` for a workspace-wide one. Follow-up
commands find the last run automatically (via `.noodle/last_run_root`);
these re-generate and re-host it:

```bash
noodle report generate      # rebuilds BOTH from the last run's results (Allure + RCA)
noodle report open          # serves the Allure report locally and opens your browser
noodle report serve         # hosts BOTH on 127.0.0.1:8000 — /allure-report/index.html + /rca.html
noodle report serve --host 0.0.0.0   # share the link with a teammate on your network

noodle report list                   # the live report + archived runs you can re-host
noodle report serve 20260713_101112  # serve an OLDER run from archives/ (stamp from `report list`)

noodle report stop                   # kill EVERY hosted report server (Allure + RCA), from any terminal
noodle report stop --port 8000       # kill just the one on :8000

noodle rca-report --serve            # root-causes every failure, with LLM opt-ins
noodle archive                       # zip the last run's tree on demand (runs overwrite in place)
```

`report serve` records its pid — with the report root it serves — in
`.noodle/report_servers.json`, so `noodle report stop` works from a
different terminal than the one serving; no more hunting with
`lsof -ti:8000 | xargs kill`. `Ctrl+C` in the serving terminal still works
too. NOOD_0161: that registry is also what makes a URL **stable**. Every
hosting path (`run --serve`, `report serve --background`, the `serve_report`
MCP tool) reuses the server already hosting that root instead of opening a
second one, and all of them are detached processes — so the link survives
the run, the agent session, and the MCP server that handed it over, and it
is the same link next run. Stop it with `noodle report stop` or the
`stop_report_server` MCP tool when you're done with it. `report stop` also
catches ad-hoc servers
an agent started by hand with `python -m http.server` on a report dir — it
wasn't in the registry, but `noodle report stop` finds and kills it anyway.

Run these from the workspace root, or `cd` into the app folder
(`web/busterblock`) to scope everything — report, archive, clean — to that
one app.

`noodle report serve` is safe to run from a fresh shell: if the report files
are missing but the last run's `allure-results/` exists, it rebuilds both first.
Each run overwrites `artifacts/` in place — the Allure **trend history** is
preserved across the wipe, so the report always shows the latest run with the
prior runs' trend line intact. Nothing is auto-archived. If you want to keep a
specific run's full tree (screenshots/traces), `noodle archive` zips it on
demand and `noodle report serve <stamp>` re-hosts it later.

You don't have to `cd` into the workspace — like every noodle command,
`report serve` takes `--workspace`/`-w` (without it, the workspace defaults
to the directory you're in):

```bash
# from any directory:
noodle report serve -w ~/projects/my-tests                    # live reports
noodle report serve 20260704_000638 -w ~/projects/my-tests    # archived run (stamp needs -w to find archives/)
noodle report list -w ~/projects/my-tests                     # what's available

# or point at a path directly — no -w needed:
noodle report serve ~/projects/my-tests/artifacts/reports                       # workspace-wide run
noodle report serve ~/projects/my-tests/noodle_tests/app1/report/reports        # a single app's run
noodle report serve ~/projects/my-tests/archives/artifacts_20260704_000638.zip
```

> Don't open `artifacts/reports/allure-report/index.html` directly — it needs HTTP. Always
> use `noodle report open` (just you) or `noodle report serve` (with `--host 0.0.0.0`, anyone
> on the network — no download, no local `allure` install needed on their end). Same applies
> to `rca-report` — pass `--serve` rather than opening `rca.html` via `file://`.
>
> **Treat `artifacts/` as secret-bearing.** Failure screenshots, traces and videos capture
> whatever was on screen — including credentials a test typed into a login form. Noodle masks
> sensitive-looking values in its *logs*, but a screenshot is a screenshot. Only serve or
> publish reports of credentialed runs on networks/CI systems you trust.

Every run also writes `environment.properties` (browser, headless, timeout,
app base URLs) and `categories.json` (noodle's failure taxonomy: locator
problems, timeouts, unresolved steps, assertion failures) into
the run's `allure-results/` (NOOD_0022) — so the report's **Environment** and
**Categories** widgets are populated and a failure's *kind* is filterable
without reading every message.

**You now have a fully working local install.** Everything below is
either "how do I write my own test" or "optional things you can turn on."

---

## Write your first test

In your workspace (created in [Part 6](#part-6--run-your-first-tests) above
— everything below assumes you're `cd`'d into it), create a new file:
`web/busterblock/features/my_first_test.feature`

```gherkin
@web @headless
Feature: My first test

  @smoke
  Scenario: I can log in and see the catalog
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"
    And User should see "Jaws"
```

**What each part means:**

- `@web @headless` — run in a browser (Chromium), no visible window.
- `{env:BUSTERBLOCK}` — resolves to `http://localhost:3333` from
  `web/busterblock/resources/busterblock_environments.yaml`.
- `{env:BB_USER}` / `{env:BB_PASS}` — resolve from
  `web/busterblock/resources/busterblock_secrets.env` (`reel_ryan` / `Popcorn1!`).
- `Then User should see "VHS Catalog"` — asserts that text appears on the page.

**How parameters resolve, in general — one delimiter, the prefix names the source:**

Every parameter reference is `{source:name}`. Three sources exist:

| Syntax | Source | Example |
|---|---|---|
| `{env:NAME}` | config, checked in this order: real OS env var → app package `resources/.env`/`secrets.env`/`environments.yaml` → workspace root `.env`/`secrets.env`/`environments.yaml`. App-package files override root files (NOOD_0133); a real env var beats everything — full table in [feature-packages.md](feature-packages.md) | `When User enters {env:BB_USER} in the username field` |
| `{var:NAME}` | a value captured earlier in *this run* (`store`/`extract`/`calls the function` steps) — never a file. Also names the write target in those capture steps. | `When User stores the "Total" text as {var:TOTAL}` … `Then {var:TOTAL} should equal "$9.99"` |
| `{pom:name}` | forces the **POM YAML resolver** for this one element, skipping accessibility/self-heal/vision — see §3 below | `When User clicks the {pom:burger menu}` |

Everything else stays plain — no wrapper syntax:

| Value kind | How to write it | Example |
|---|---|---|
| string literal | quote it — `"double"` or `'single'`, both work identically | `When User enters "hello world" in the comment field` |
| number literal | bare, no quotes | `Then the response status should be 200` |
| URL literal | quote it — `"double"` or `'single'` | `Given User is on "https://example.com"` |
| Scenario Outline param | Gherkin's own `<name>` | `When User enters <username> in the username field` |
| data table cell | plain text in the table; `{env:X}`/`{var:X}` work inside cells too | `\| username \| {env:BB_USER} \|` |
| JSON body | plain — literal `{`/`}` are never touched, only `{env:…}`/`{var:…}` inside it are expanded | `with body '{"id": "{var:DEVICE_ID}"}'` |

Unknown references are left as-is (so the failure message shows exactly what
didn't resolve). The legacy delimiters `{env:NAME}` (env) and `` `NAME` ``
(captured var) and bare `{name}` (POM) still work but log a deprecation
warning — migrate to `{env:…}`/`{var:…}`/`{pom:…}`.

**Scenario Outlines & Data Tables** are fully supported (NOOD_0062):
`<placeholders>` work in any step — quoted or bare, several per step — and
`noodle validate --resolve` dry-runs outline steps with the first Examples
row substituted. Data tables handle labelled headers (`| field | value |`),
headerless tables (the first row is kept as data, not silently dropped), and
case-insensitive `| Key | Value |` headers for REST asserts. Steps written
with sloppy grammar also still resolve — past tense ("the user clicked …"),
bare infinitives, smart quotes, doubled spaces, trailing periods, and
"verify that … / makes sure …" wrappers. See
[docs/steps_dictionary.md](steps_dictionary.md) — "Scenario Outlines &
Data Tables" and "Grammar tolerance" sections.

Root files (`./environments.yaml`, `./secrets.env`) apply to every app; per-app
files (`noodle_tests/<type>/<app>/resources/<app>_environments.yaml`) only add keys
the root hasn't already set — see [Part 4 — Configure](#part-4--configure).

A bare phrase with no braces (`the login button`) isn't a config lookup at
all — it's a locator: accessibility tree → self-heal → **POM YAML resolver**
→ vision LLM (if configured). Write it as `{pom:login button}` to skip
straight to the POM YAML resolver and nowhere else (§3 covers when that's
worth doing).

The POM YAML resolver (`noodle/agents/web/pom.py`) checks, in this order,
first match wins:

1. Local `resources/pageobjects/<page>_pom.yaml` — page-scoped
2. Local `resources/pom.yaml` — shared across this app's pages
3. Global `noodle_tests/pom.yaml` — shared across every app

Full walkthrough, including page-scoped `pages:`/`shared:` blocks, page
pinning, `{pom:...}`, and ambiguous/multi-match elements:
[docs/encyclopedia.md § pom.yaml](encyclopedia.md#5-pomyaml--when-natural-naming-fails)
and [docs/workspace-guide.md § 3](workspace-guide.md#3-map-page-objects-pom-back-to-your-feature-files).

Run it:

```bash
noodle run web/busterblock/features/my_first_test.feature
```

You should see a Chromium window open, log in, and the test pass. For the
full vocabulary of built-in sentences (clicks, forms, waits, assertions,
tables, API calls, and more), see
**[docs/steps_dictionary.md](steps_dictionary.md)**.

The same sentences also drive **native apps** *(beta)* — Android/iOS
emulators and **Windows 11 / macOS desktop applications** — by tagging the
scenario `@android`/`@ios`/`@windows`/`@mac`. Setup per platform:
**[docs/native-apps.md](native-apps.md)**. Web testing is the
production-hardened path; native-app, desktop-visual, and visual-baseline
testing are functional but younger — expect rougher edges.

Before trusting a hand-written or generated file, you can also dry-run
every step against the pattern table with no browser at all:

```bash
noodle validate web/ --resolve
```

`validate` also lints POM auto-scoping (NOOD_0022): a
`pageobjects/<page>_pom.yaml` with no explicit `match:` only applies to URLs
containing its filename stem — if that stem never appears in any URL its
sibling features navigate to, the file silently never resolves. The lint
flags exactly that (warn-only, exit code unchanged):

```
POM auto-scope lint — 1 warning(s):
  ⚠️  noodle_tests/web/myapp/resources/pageobjects/file_upload_pom.yaml: auto-scopes
      to URLs containing 'file_upload', but no URL in myapp/features/ contains
      it — its keys will silently never apply. ...
```

---

## Run more tests

The framework ships with several bundled suites so you can see every
capability working before you write your own. Commands below assume you're
`cd`'d into your test workspace (Part 6) — the copy of `sample_feature_tests`
you made, not the noodle repo:

```bash
# BusterBlock — bundled app, covers every framework capability
noodle run web/busterblock --headless

# SauceDemo — public site, no local server needed
noodle run web/saucedemo --headless

# Example — real public site, uses POM files for hard-to-find elements
noodle run web/example --headless

# API — REST tests against a public API, no browser
noodle run api --headless

# Performance — plain-Gherkin load-test gates (NOOD_0155 woks), no browser;
# point APP at an endpoint you own first (e.g. the local BusterBlock app)
noodle run performance --headless

# Everything at once — "." runs every .feature under the workspace root;
# dropping the path entirely instead runs just tests_dir (noodle_tests/ by
# default), which is where tests you write yourself land, not these samples
noodle run . --headless
```

Hacking on Noodle itself and want to run its own internal test suite
instead? That one's engine-repo-only — from inside the `noodle/` clone:

```bash
python -m pytest unit_tests/ -v      # the framework's own internal logic, no browser, no LLM
```

**Watch the browser instead of running invisibly** — swap `--headless` for
`--headed` to debug a test locally (mutually exclusive; `--headed` always
wins if both are passed):

```bash
noodle run web/busterblock --headed
```

**Test lives in a different folder or repo, not inside this one?** Point
`--workspace`/`-w` at it — every `noodle` command takes this flag, not just
`run`. Where you run it from doesn't matter — `--workspace` is a path, not
tied to your current directory — as long as the `noodle` command itself
resolves in that terminal (activated the venv per [Part 2](#part-2--get-the-framework)
Option A, or installed with Option B so it's always on `PATH`):

```bash
noodle run --workspace ~/projects/my-tests --headless
```

Prefer not typing `--workspace` every time? `cd ~/projects/my-tests` first
and drop the flag — it defaults to `.`, your current directory:

```bash
cd ~/projects/my-tests && noodle run --headless
```

**When to add `--parallel`:** once a suite is big enough that a serial
`--headless` run is your bottleneck (large web suites are the common case),
run N feature files at once via [BehaveX](https://github.com/hrcorval/behavex):

```bash
pip install -e ".[parallel]"                        # one-time — already in if you installed "[all]"
noodle run web/ --headless --parallel 4        # 4 feature files at a time
```

Falls back to `$NOODLE_PARALLEL_PROCESSES` if `--parallel` is unset. Keep
runs serial (the default) for small suites or when debugging a single
scenario — parallel output interleaves and the auto-written
`rca.md` is skipped (see [Diagnosing failures](#diagnosing-failures--rca)
below), so run `rca-report` explicitly afterward.

Native apps (Android/iOS emulators, Windows 11/macOS desktop apps) need
`pip install noodle[mobile]` + a running Appium server first — see
[docs/native-apps.md](native-apps.md) for per-platform setup, then:

```bash
noodle run mobile/ --tag android    # or --tag ios
noodle run desktop/ --tag windows   # or --tag mac
```

SauceDemo, Example, and the API suite need internet; BusterBlock needs
Terminal A running (Part 5); unit tests need neither.

---

## Diagnosing failures — RCA

When a run has failures, `noodle rca-report` reads the last run's
`allure-results/` (wherever that run wrote them — the app's `report/` or
`artifacts/`) and writes a Markdown root-cause table — one row per failed/errored scenario, with
a category, a plain-English reason, and a suggested fix. It runs in two tiers:

- **Heuristic** (always on, free, instant) — pattern-matches the assertion
  message, traceback, and the console ⚠️ warnings captured during the run
  (ambiguous locator, vision-fallback failure, self-heal match). No model, no
  network call.
- **Agentic** (opt-in, needs a vision-capable model) — `noodle/rca.py` looks
  at the failure screenshot and adds its own verdict alongside the heuristic
  one. See [Local models with Ollama](#local-models-with-ollama-free-no-account-nothing-leaves-your-machine)
  below to set one up.

`rca-report` also prints a **"Passed with warnings"** section (NOOD_0021)
below the failures table: scenarios that *passed* but still logged a ⚠️
warning (ambiguous locator, self-heal match) along the way. Lenient mode never
fails the build on these, so without this section they're only ever visible
in console output that scrolls away — pass rate alone won't tell you a step
quietly clicked the first of five identical buttons.

### Basic usage

```bash
noodle run web/busterblock --headless   # results → web/busterblock/report/
noodle rca-report                              # prints the table to stdout (finds that run automatically)
```

A run with any failures also auto-writes the heuristic table to
`<run root>/reports/rca.md` (no `--out` needed) — but only when the run
wasn't `--parallel`, and never the HTML view or the `--llm` narrative. Run
`rca-report` explicitly (see [agent-playbook.md](agent-playbook.md)
for why this should be a habit, not an afterthought) for stdout, a fresh
re-run against changed results, the `--llm` narrative, or `--serve` for a
styled HTML page opened directly in the browser — no HTTP server needed,
unlike the Allure report.

```
# RCA Report

2 failed/errored scenario(s).

| Feature | Scenario | Step | Heuristic verdict | Agentic (AI) verdict | Recommendation |
|---|---|---|---|---|---|
| Sauce Demo Checkout | User completes a purchase end to end | When User clicks the shopping cart | **locator-rot** (medium): Accessibility matched more than one element and no POM entry disambiguated it — lenient mode used the first (possibly wrong) match. | _(no vision-capable NOODLE_MODEL configured)_ | Add a scoped pom.yaml entry for this locator. |
| REST Write | POST — create a new object | Then the response status should be 200 | **environment-flap** (high): A third-party API's rate limit/quota was exhausted. | _(no vision-capable NOODLE_MODEL configured)_ | Self-host a mock for CI, get an API key, or reduce call volume. |
```

### Save it to a file

```bash
noodle rca-report --out rca.md
```

### Open the styled HTML view

```bash
noodle rca-report --serve
```

Renders `rca.html` next to the Allure report and opens it directly — self-contained
page (inline CSS, no fetch/XHR), so a plain `file://` open works fine, no
local server needed.

### With a narrative on top (any configured model, vision or text-only)

```bash
noodle rca-report --llm
```

```
# RCA Report (AI narrative)

- Both saucedemo checkout failures are the same cause: the "shopping cart"
  icon has no accessible label and no matching pom.yaml entry — add one in
  web/saucedemo/resources/pageobjects/shared_pom.yaml.
- All 5 REST write failures are the shared sandbox API's daily quota — not a
  code issue, retry tomorrow or self-host a mock.

---

# RCA Report
...(same table as above)
```

### Turn on the agentic (vision) verdict too

```bash
# .env
NOODLE_MODEL=anthropic/claude-sonnet-5   # any vision-capable model (local: ollama/qwen2.5vl:7b)
NOODLE_RCA=true
```

```bash
noodle run web/busterblock --headless --tag @smoke
noodle rca-report
```

Now the "Agentic (AI) verdict" column fills in with the model's own category,
confidence, reason, and fix for each failure it looked at — shown next to the
heuristic one so you can see where they agree.

> A text-only model (Groq, local `llama3.1`) can't produce an agentic verdict
> — `ask_vision()` will error and that column stays empty — but it still
> works fine for `--llm`'s narrative, which only needs text.

**Design rationale for the heuristics-first RCA approach (and where AI
actually helps) → [docs/design-history.md](design-history.md#phase-24--rca-engine-hardening--heuristics-first-design-nood_0018)**

---

## MCP server — `noodle-mcp` (AI SDLC)

Noodle can sit inside an AI SDLC as an MCP server: an external agent (Claude
Code, an orchestrator, any MCP host) calls Noodle's tools instead of typing
into the REPL. The calling agent brings the language skills; Noodle stays
deterministic. **Full setup / usage / MAF + Foundry guide, design rationale,
and tool reference: [docs/mcp-guide.md](mcp-guide.md).**

Looking for the tester-facing quickstarts (connect a host, smoke tests from
your agent's terminal, copying the bundled samples)? They moved up:
[Connect an AI coding agent](../README.md#connect-an-ai-coding-agent-for-testers--pms).
What remains here is the engineer-facing part of the MCP story.

### Pure LLM mode with a coding agent

Assisted (`auto`) and Pure (`full`) LLM modes exist for steps too ambiguous
for the pattern table. When a coding agent — Claude Code CLI, GitHub
Copilot CLI — is the driver, the division of labour is:

1. **Generate** — the agent authors the Gherkin (author loop:
   `noodle://vocabulary` → `validate_feature` → `write_feature`) or calls
   `generate_test(...)`. Steps it can't phrase in the vocabulary stay
   natural-language — set `NOODLE_LLM_MODE=auto` (or `full`) plus
   `NOODLE_MODEL` (local Ollama is fine) in the workspace `.env` so the
   engine can resolve them at run time.
2. **Run** — `run_and_report(...)` → `get_last_result()` → `get_rca()`.
3. **Harvest** — every step the engine-side model resolved was appended to
   `<workspace>/docs/steps_dictionary_suggestions.md` (step text + resolved
   action JSON). Ask your agent to read that ledger and promote recurring
   entries: `noodle step-search "<step text>" --accept` stages the step
   into the workspace's `docs/agent_patterns.yaml`, which every future run
   loads — that step never hits the LLM again. The ledger doubles as the
   plan file a Noodle developer can later turn into a permanent
   `patterns.py` PR.

The engine-side `NOODLE_MODEL` never has to be smart here — the host agent
does the thinking up front; the model only fills run-time gaps, and every
gap it fills is captured for promotion. Loop details:
[docs/mcp-guide.md § 5](mcp-guide.md#5-the-authoring-loops).

### Reference — generic install, restarts, MAF/Foundry

`noodle init` writes host config for you (see [Zero to hero — connect an
MCP host](../README.md#zero-to-hero--connect-an-mcp-host)) — for a bare `mcp` extra
install, a host not covered there, restarting/killing a stuck
`noodle-mcp` process, or the Microsoft Agent Framework / Azure AI Foundry
remote (streamable-HTTP + API key) setup, see the full reference:
**[docs/mcp-guide.md § 3](mcp-guide.md#3-connecting-a-host)** and
**[§ Starting, restarting, and killing the server](mcp-guide.md#starting-restarting-and-killing-the-server)**.

| Tool | Does |
|---|---|
| `generate_test(url, description, use_llm?, overwrite?, append_to?)` | scaffold a feature + POM (templates + slot filling; `use_llm` routes via `NOODLE_MODEL`). A bare `@tag` in `description` (e.g. "...add gherkin tags @hello.com") is added to the generated scenario(s). `append_to` (an existing feature's file stem) adds this test case's scenario(s) to that file instead of writing a new one — same app/topic, one more scenario; omit it (the default) to get a new `.feature` file, e.g. for a different suite/topic (rule-based only, ignored with `use_llm`) |
| `run_test(target?, tag?)` | run a feature — omit `target` to run "the test" (persisted last created/run, else newest `.feature`) |
| `get_last_result()` | structured last-run result: counts, failures, wall time |
| `run_and_report(target?, tag?)` | run + rebuild the Allure HTML report in one call |
| `list_tests(query?)` | feature/tag inventory with `scenario_count`; `query` (substring over path/feature/scenario/tag) is what returns scenario names |
| `validate_feature(content)` | dry-run Gherkin against the pattern table — per-step matched/unmatched |
| `write_feature(path, content, overwrite?)` | store caller-authored Gherkin (validated; path locked to the tests dir) |
| `search_step(query)` | nearest existing step for a plain-English action |
| `get_rca()` | root-cause markdown for the last run's failures |

Plus the `noodle://vocabulary` resource — the canonical step sentences. The
fully-LLM-free loop for a calling agent: read the vocabulary → author Gherkin
→ `validate_feature` → `write_feature` → `run_test` → `get_last_result`.

Session memory (`artifacts/agent_state.json`) is shared with `noodle repl`,
so "the test" means the same thing in both.

## Reference (engineers / CI authors)

Everything from here to [Running in CI](#running-in-ci--azure-devops) is
reference material for engineers and CI authors — the full step grammar,
LLM-provider internals, and the `noodle repl` shell. Testers and PMs don't
need any of it to run tests via their agent (see
[Connect an AI coding agent](../README.md#connect-an-ai-coding-agent-for-testers--pms)).

## Syntax

Every distinct syntax the framework recognizes, in one place — not just the
step vocabulary (that's [docs/steps_dictionary.md](steps_dictionary.md)).
Each entry below is a pointer to its canonical doc, not a re-explanation —
if you came here looking for the full detail on one of them, follow the link.

**Parameters — `{source:name}`.** `{env:NAME}` / `{var:NAME}` / `{pom:name}`,
plus how they behave inside data tables, Scenario Outlines, and JSON bodies:
already covered in full, with a table, in
[Write your first test § How parameters resolve](#write-your-first-test)
above. Don't duplicate that table here — go there.

**Locators — quoted vs unquoted.** `clicks the login button` (no quotes)
strips the trailing `button`/`link` word before the POM lookup, so the key
you need is `login`, not `login button`. Quote it — `clicks the 'login
button'` — and the lookup is verbatim: key `login button`. Same rule for
`double-clicks`, `right-clicks`, `presses`, and `long-presses`. This is
almost never worth thinking about (accessibility-tree matching covers most
elements before POM lookup even runs) — it only bites when you're writing
an explicit POM override and the key silently never matches.
[docs/steps_dictionary.md](steps_dictionary.md) has the full step list;
[Map page objects (POM) back to your feature files](workspace-guide.md#3-map-page-objects-pom-back-to-your-feature-files)
has the POM YAML format itself.

**Variable write target — `` `NAME` `` or `[NAME]`.** Both delimiters are
accepted and mean the same thing (backtick is the preferred style; `[...]`
is the older form, still supported, no deprecation warning). Used only on
the *write* side of a capture step — read the value back with `{var:NAME}`:
```gherkin
When User stores the "Total" text as `TOTAL`
Then {var:TOTAL} should equal "$9.99"
```

**Tags.** Two families. *Bare* tags flip a behavior on for the scenario or
feature; *`@tag:value`* tags carry a value, `@tag:value` always wins over
the matching `NOODLE_*` env var.

| Bare tag | Effect |
|---|---|
| `@web` | Playwright browser session (the default for anything not `@api`/Appium-platformed) |
| `@api` | No browser — pure REST scenario; a web step inside one fails loudly |
| `@perf` | No browser — performance-wok scenario: built-in load generator + latency/error/throughput assertions ([docs/woks.md](woks.md)) |
| `@android` / `@ios` / `@windows` / `@mac` | Appium session instead of Playwright, with that platform's default capabilities — [docs/native-apps.md](native-apps.md) |
| `@mobile` | Playwright *device emulation* (stays web) — wins over a platform tag if both are present on the same scenario |
| `@headless` / `@headed` | Force the browser visible/invisible for just this scenario, overriding `--headless`/`.env`; `@headed` wins if both are present |
| `@slow` | 500ms `slow_mo` between actions — for watching a run, not CI |
| `@firefox` / `@webkit` / `@safari` / `@edge` | Pick the browser engine for this scenario (default: Chromium, or `--browser`/`NOODLE_BROWSER`) |
| `@quarantine` | A failure here doesn't fail the build, as long as every *other* failure this run is also quarantined |
| `@soft` | Assertion failures are collected instead of stopping the scenario immediately — [docs/steps_dictionary.md § Soft Assertions](steps_dictionary.md#soft-assertions-phase-l) |
| `@strict` | Locator resolution fails loudly on an ambiguous match instead of warning and taking the first hit — same as `NOODLE_STRICT_LOCATOR=true` |
| `@secure_certs` | Surface TLS/cert errors for this scenario. TLS + self-signed/invalid certs are ignored by default (`NOODLE_IGNORE_HTTPS_ERRORS=true`) since sites under test are usually dev/sandbox; this tag (or `NOODLE_IGNORE_HTTPS_ERRORS=false`) turns that off |
| `@terminal` | Scenario requires OCR availability (desktop/terminal apps) |
| `@ocr_fallback` | Enables the OCR locator fallback tier for elements nothing else can find (closed shadow roots) |

| `@tag:value` | Effect | Env var fallback |
|---|---|---|
| `@page:name` | Pins the POM active page for the whole scenario — same effect as `User is on the "name" page`, but visible up front and survives even if no step ever navigates | — |
| `@viewport:1920x1080` | Browser viewport size (`WIDTHxHEIGHT`) | `NOODLE_VIEWPORT` |
| `@geo:51.5,-0.12` | Geolocation (`lat,lon`) | `NOODLE_GEOLOCATION` |
| `@permissions:geolocation,camera` | Comma-separated browser permissions to grant | `NOODLE_PERMISSIONS` |
| `@locale:fr-FR` | Browser locale | `NOODLE_LOCALE` |
| `@timezone:America/Toronto` | Browser timezone | `NOODLE_TIMEZONE` |
| `@color_scheme:dark` | Browser color-scheme preference | `NOODLE_COLOR_SCHEME` |
| `@offline` (bare, no value) | Starts the browser context offline | `NOODLE_OFFLINE` |

**Conditional steps and waits.** `if 'X' appears, clicks 'Y'` /
`clicks 'Y' if 'X' appears` / hard sleeps (`waits 5 seconds`) — full syntax
and phrasing variants: [docs/steps_dictionary.md § Conditional Steps](steps_dictionary.md#conditional-steps-nood_0044).

**Page objects (POM) YAML.** Selector types (`css | xpath | id | testid |
text | role`), the `pages:`/`shared:`/`match:` block structure for
page-scoped overrides, and the `<page>_pom.yaml` naming/auto-scope
convention: [docs/workspace-guide.md § 3](workspace-guide.md#3-map-page-objects-pom-back-to-your-feature-files)
and [docs/encyclopedia.md § pom.yaml](encyclopedia.md#5-pomyaml--when-natural-naming-fails).

**Config files.** `noodle.yaml` (engine config), `.env`/`secrets.env`
(`KEY=value`), `environments.yaml` (base URLs) — format and resolution
cascade: [Part 4 — Configure](#part-4--configure), full per-app cascade in
[docs/glossary.md](glossary.md).

---

## LLM augmentation (optional)

Noodle Test Framework is **model-agnostic** via
[LiteLLM](https://github.com/BerriAI/litellm) — point it at any provider
with two lines of config. This unlocks: steps that don't match a built-in
pattern (instead of failing), and — with the agent (below) — free-form
English test generation.

### Cloud providers (one API key required)

```bash
# .env — committed, no secrets
NOODLE_MODEL=anthropic/claude-sonnet-5   # recommended default
```

```bash
# secrets.env — gitignored
ANTHROPIC_API_KEY=your-key-here
```

Swap the model string for any supported provider:

| Provider | Model string | Key variable |
|----------|-------------|-------------|
| Anthropic Claude *(recommended)* | `anthropic/claude-sonnet-5` (cheap tier: `anthropic/claude-haiku-4-5`) | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-flash` | `GEMINI_API_KEY` |
| OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.1-8b-instant` | `GROQ_API_KEY` |

### Local models with Ollama (free, no account, nothing leaves your machine)

```bash
# 1. Start the server (keep this running in a separate terminal)
ollama serve

# 2. Pull a vision-capable model (can see screenshots to locate elements)
ollama pull qwen2.5vl:7b
```

```bash
# .env
NOODLE_MODEL=ollama/qwen2.5vl:7b
NOODLE_LLM_URL=http://localhost:11434
```

No key needed — the model runs on your machine. A 7B model wants ~8GB of
VRAM/unified memory; `ollama pull llava` is the lighter fallback if
`qwen2.5vl` won't fit.

> **Vision vs text-only models.** Vision-capable models (qwen2.5vl, llava,
> claude, gpt-4o, gemini) can look at a screenshot to locate elements.
> Text-only models (Groq, llama3, qwen2.5-coder) interpret step phrases but
> fall back to the accessibility tree for element location. If your suite
> only ever needs step resolution (no vision), `ollama/qwen2.5-coder:7b`
> produces the most reliable step-JSON. Model choice, VRAM sizing, and
> known gaps: [docs/llm-setup.md](llm-setup.md).

On a locked-down work laptop with only a work GitHub/Copilot account (no
personal API key, no Ollama)? See
[docs/llm-setup.md](llm-setup.md) first.

**Full provider guide, Foundry Local setup, demo tests, and mode reference →
[docs/encyclopedia.md §16](encyclopedia.md#16-using-an-llm--setup-providers-and-modes)**

---

## Plain-English shell — `noodle repl` (optional)

Driving Noodle from plain-English chat instead of hand-writing `.feature`
files. Useful for keeping your tests in their own folder, outside this
repo, using `noodle` as an installed tool.

**The mental model:**

- **The engine** (`noodle`) is a package you install. It never holds your tests.
- **Your workspace** is any folder you own — `.feature` files, page objects,
  `.env`, and a `noodle.yaml` config live there.
- **The REPL** (`noodle repl`) is a terminal chat on top of the engine — a
  convenience. It's a keyword-matched command dispatcher (an optional `--llm`
  tier adds narrow, single-shot model calls for generation/planning/repair),
  not an autonomous AI agent — see
  [docs/design-history.md](design-history.md) Phase Y (NOOD_0056). CI/CD
  uses the engine directly and never needs it.

```
~/my-tests/                 ← your workspace (lives anywhere)
├── noodle.yaml           ← config
├── README.md               ← what's here + next steps (auto-created)
├── AGENTS.md               ← AI-agent instructions (auto-created; auto-read by Copilot natively and by Claude Code via CLAUDE.md's @AGENTS.md import)
├── .env                    ← settings (no secrets; hidden — `ls -a`)  [optional]
├── secrets.env             ← credentials (gitignored)                [optional]
├── environments.yaml       ← base URLs                               [optional]
└── noodle_tests/
    ├── environment.py      ← engine glue (auto-created)
    ├── steps/
    │   └── z_catch_all.py  ← engine glue (auto-created)
    ├── pom.yaml             ← global page objects, shared by every app
    └── <type>/<app>/        ← one folder per app-under-test (type: web, api, ...)
        ├── features/        ← your *.feature tests
        ├── resources/       ← everything the tests need
        └── report/          ← this app's run output — results + reports (single-app runs write here)
```

See [docs/feature-packages.md](feature-packages.md) for the full
per-app packaging model.

> **Are you an AI coding agent asked to write a test, rather than a human
> following this guide?** Use
> [docs/agent-playbook.md](agent-playbook.md)
> instead — it drives the `noodle` CLI directly without depending on this
> REPL.

### Create a workspace

```bash
noodle init ~/my-tests
cd ~/my-tests
```

Writes a **runnable** workspace with a sample login test whose steps are
commented out (so it runs green out of the box) pointing at the vocabulary
in [docs/steps_dictionary.md](steps_dictionary.md).

Add `--llm ollama|claude|gemini` (+ `--model` to override the default for
that provider) to have `noodle repl` talk to a model from the very next
command, with no flags to remember:

```bash
noodle init ~/my-tests --llm ollama       # writes NOODLE_MODEL into .env
cd ~/my-tests
noodle repl                              # picks up NOODLE_MODEL automatically
```

This only writes to a fresh `.env` — if the workspace already has one,
`--llm` is ignored with a note (edit `NOODLE_MODEL` there yourself, or delete
`.env` and re-run `init`).

### Chat with the agent

```bash
noodle repl --workspace ~/my-tests
# or just `noodle repl` if you're already in the workspace
```

```
noodle repl — workspace: /Users/you/projects/my-tests  (rule-based, no LLM)
Type 'help' for commands, 'quit' to exit.

noodle> create test for login at https://www.saucedemo.com
→ Wrote sample_feature_tests/web/saucedemo/features/login.feature
→ Wrote sample_feature_tests/web/saucedemo/resources/pageobjects/login_pom.yaml
→ Wrote sample_feature_tests/web/saucedemo/resources/saucedemo_environments.yaml
→ Wrote sample_feature_tests/web/saucedemo/resources/saucedemo_secrets.env.example
→ Run: noodle run sample_feature_tests/web/saucedemo/features/login.feature

noodle> run login
noodle> list
noodle> summary
noodle> quit
```

| You type | It does |
|---|---|
| `run` / `run all` | run every feature |
| `run <name>` | run the matching `.feature` file |
| `run <tag>` | no file matches → run that tag (e.g. `run smoke`) |
| `run that` / `run it` / `run the test` | re-run the last created/run feature — remembered across sessions in `artifacts/agent_state.json` (NOOD_0045); falls back to the most recently modified `.feature` |
| `list` / `what tests` | list all scenarios |
| `create test for <desc> at <url>` | scaffold a feature + skeleton POM + `resources/` |
| ... add `overwrite` | replace an existing feature of the same name |
| `generate the secrets/environments/pom/precondition/payload/function/data file for <app>` | scaffold just that one supporting file for an already-existing app package — see [docs/feature-packages.md](feature-packages.md) |
| `summary` / `what failed` | plain-English summary of the last run |
| `find a step for <description>` / `step-search <description>` | find the closest existing step for a plain-English description; no good match → offers to draft + save a new one (y/N) — see [docs/steps_dictionary.md](steps_dictionary.md#finding-a-step--suggesting-a-new-one) |
| `help` | command list |
| `quit` / `exit` | leave |

Without `--llm`, this is keyword matching — no API key, no cost, works
offline. The `create test for ... at ...` phrase is the precise form, but a
free-prose create request also works as a last-resort match (NOOD_0045): any
sentence with a create-ish verb, the word "test", and a URL-looking token —
e.g. `Generate a new test case targeting "example.com", ... searches for
"office chair" ...` — scaffolds the nearest template, with quoted values
filled into the search/enter/assert slots. Anything the templates can't
express stays a **skeleton by design** — open the files, replace the
`<placeholders>` and `<css selector>` stubs, then run.

### With an LLM: free-form requests

```bash
ollama serve                    # its own terminal — keep it running
ollama pull llama3.2            # one-time download
noodle init ~/my-tests --llm ollama    # once — persists NOODLE_MODEL into .env
cd ~/my-tests
noodle repl                    # no --llm/--model needed — reads it from .env
```

Already initialised a workspace without `--llm`? Pass `--llm`/`--model` on
`noodle repl` itself for that one session — it works exactly as before,
it just isn't required anymore once `init` has persisted a model:

```bash
noodle repl --workspace ~/my-tests --llm ollama --model llama3.2
```

Now any free-form sentence works, including compound requests:

```
noodle> Generate a test, that a user would go to youtube.com, search for MKBHD, and I would see results with MKBHD
→ Wrote noodle_tests/web/youtube/features/search_mkbhd.feature
→ Wrote noodle_tests/web/youtube/resources/pageobjects/search_mkbhd_pom.yaml
→ Wrote noodle_tests/web/youtube/resources/youtube_environments.yaml
→ Wrote noodle_tests/web/youtube/resources/youtube_secrets.env.example
→ Run: noodle run noodle_tests/web/youtube/features/search_mkbhd.feature

noodle> create a test for the youtube search above, run it, and show me the report
```

The model extracts the intent (description + target URL), writes full
Gherkin, and — with `--llm` — a failed run you (or the agent) just created
also gets one automatic repair attempt: the model sees the failing step,
proposes a fix, and it's kept only if a retry has fewer failures. A
"show me the report" at the end of a compound request (or `open the
report` / `serve the report` on their own) rebuilds the Allure HTML
report from the latest results before opening or serving it.

Or with a paid provider:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # Windows (PowerShell): $env:ANTHROPIC_API_KEY = "sk-ant-..."
noodle repl --workspace ~/my-tests --llm claude --model anthropic/claude-sonnet-5
```

### The engine directly (CI/CD)

Everything the agent does maps to a plain command — this is what CI uses:

```bash
noodle run      --workspace ~/my-tests --headless
noodle run      --workspace ~/my-tests --tag smoke
noodle list     --workspace ~/my-tests
noodle list     --workspace ~/my-tests --json            # feature/scenario/tag inventory as JSON
noodle validate --workspace ~/my-tests                   # parse-only, no browser
noodle validate --workspace ~/my-tests --resolve --json  # per-step pattern/LLM classification as JSON
noodle summary  --workspace ~/my-tests                   # after a run
noodle summary  --workspace ~/my-tests --json            # counts + failures as JSON; a run also writes last_run.json in its output root
noodle step-search "<description>" --accept --workspace ~/my-tests   # non-interactive step search/draft
```

CI/CD never needs `noodle repl` — see
[Running in CI — Azure DevOps](#running-in-ci--azure-devops) below for the
pipeline setup, including running against a team's own external test repo.

### Adding custom steps from your own test repo

Full walkthrough (workspace setup, POM mapping, custom scripts, reports):
[docs/workspace-guide.md](workspace-guide.md). Short version — two
mechanisms, both workspace-scoped, neither requires touching the `noodle`
repo itself:

- **Hand-written behave steps** — add a `.py` file under
  `<workspace>/noodle_tests/steps/`; behave auto-imports it like any other step
  file. Don't prefix your own files with `z_` — that's reserved for
  `noodle_tests/steps/z_catch_all.py`'s load-order trick (it must load last so a
  project's own steps take priority).
- **Plain-English vocabulary phrasing** — `noodle step-search "<description>"
  --accept --workspace <path>` (or the `noodle repl` y/N prompt) stages a
  new phrasing into `<workspace>/docs/agent_patterns.yaml` +
  `<workspace>/docs/steps_dictionary.md`. This is per-workspace: each
  project keeps its own staged vocabulary, and `noodle run --workspace
  <path>` picks up exactly what that workspace has staged. Promoting a
  staged entry into `noodle`'s own curated `noodle/resolver/patterns.py` is
  a deliberate, manual step by whoever maintains the engine — by design,
  not something a project team needs (or is able) to do themselves.

Edge cases: gitignore `.env`/`secrets.env` in the test repo the same as you
would here; if several testers share one `pip install noodle`, each passes
their own `--workspace` — it's never implied from cwd alone unless you `cd`
into it first.

### Cost model

| Mode | Cost | What you get |
|---|---|---|
| Rule-based agent (default) | $0 | run, list, template scaffold, template summary |
| `--llm ollama` | $0 | natural-language generation, richer summaries (local) |
| `--llm claude` / `gemini` | your API key | complex generation, full prose output |

---

## Running in CI — Azure DevOps

`azure-pipelines.yml` (Linux agent) is the drop-in pipeline: it shards by
web `.feature` file (one per agent, auto-discovered — adding a file needs
no YAML edit), runs `noodle` the same way a laptop would, and publishes
everything a run produces (Allure report, RCA, JUnit, screenshots, traces,
healing report) as a pipeline artifact per shard, regardless of pass/fail.

**One-time setup:**

1. Create a variable group `noodle-secrets` with your credentials, and link
   it in the pipeline YAML.
2. Install the free **Allure Report** marketplace extension
   (`qameta.allure-azure-pipelines`) at the Azure DevOps organization level
   — needed once for the hosted "Allure Report" tab (below); everything
   else in the pipeline works without it.

**Run it:** push to `main`/`develop`, or "Run pipeline" with defaults — no
parameters needed for this repo's own bundled `noodle_tests/`.

**What you get, per run:**

| Pipeline step | Shows up as |
|---|---|
| `PublishTestResults@2` (JUnit) | **Run → Tests tab** — pass/fail dashboard, trends, per-test history |
| `PublishPipelineArtifact@1` | **Run → Artifacts** — the whole run: Allure report, RCA, screenshots, traces, videos |
| `PublishAllureReport@2` (suite job, Allure 3) | **Run → Allure Report tab** — one merged, hosted report across every shard, with cross-run trend history |

`azure-pipelines-windows.yml` mirrors this for Windows agents — JUnit, RCA
(including the run-summary section), the per-shard artifacts, and the hosted
Allure Report tab are all published the same way as on Linux.

**Which YAML file, and who owns the CI — the multi-repo topology.** Two
directions exist; the shipped pipeline files implement the first:

- **Engine repo owns the CI** (`azure-pipelines.yml` on Linux,
  `azure-pipelines-windows.yml` on Windows): the pipeline lives in this
  repo and by default runs the bundled `sample_feature_tests/`. To run
  another team's tests through it, queue it with `useExternalTestsRepo=true`
  plus the `testsRepoType`/`testsRepoName`/`testsRepoRef`/`testsRepoDir`
  parameters — it then checks out **both** repos (engine at
  `$(Build.SourcesDirectory)`, tests at `$(Pipeline.Workspace)/testsRepo`)
  and runs theirs.
- **Tests repo owns the CI**: the mirror image of the above — a pipeline
  YAML kept in the tests repo that declares the engine as a
  `resources.repositories` entry, checks both repos out, installs the
  engine editable from its checkout, and runs `noodle run --workspace`
  against the tests repo. Use this when the testing team wants their
  pipeline, triggers, and run history in *their* project.

Cross-project gotcha (both directions): when the other repo lives in a
different Azure DevOps **project** of the same organization, the
`repository:` resource's `name:` must be `OtherProject/repo-name` — the
bare repo name only works within the same project — and the pipeline's
build service account needs read access to that other project's repos.

Sharding, per-shard data isolation, Key Vault secrets, and running against a
team's own external tests repo (with the full parameter table) →
**[docs/encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops)**

Wiring an AI SDLC agent (LangChain today, MAF/Azure AI Foundry later) to
generate a test via `noodle-mcp`, land it in this pipeline, and show the
result on the Allure Report tab above →
**[docs/ai-sdlc-integration.md](ai-sdlc-integration.md)**

---

## Your own test workspace — a manual tester's guide

Full walkthrough for a manual tester or QE lead setting up their own test
workspace outside this repo — scaffolding, writing tests, mapping page
objects (POM), custom scripts, naming rules, and reports:

**→ [docs/workspace-guide.md](workspace-guide.md)**

## Tech stack

| Library / Tool | Version | Role |
|----------------|---------|------|
| Python | ≥ 3.11 | Runtime |
| behave | ≥ 1.2.6 | BDD runner |
| Playwright | ≥ 1.40.0 | Browser automation |
| Pillow | ≥ 10.0.0 | Screenshot annotation |
| PyYAML | ≥ 6.0 | Config and POM parsing |
| python-dotenv | ≥ 1.0.0 | `.env` / `secrets.env` loading |
| Typer | ≥ 0.9.0 | `noodle` CLI |
| LiteLLM *(optional)* | ≥ 1.0.0 | LLM provider abstraction (Claude, Gemini, OpenAI, Ollama…) |
| allure-python-commons *(optional)* | ≥ 2.16.0 | Allure report generation |
| OpenCV *(optional)* | ≥ 4.8.0 | Visual / canvas testing |
| pytesseract *(optional)* | ≥ 0.3.10 | OCR for terminal / canvas scenarios |
| PyAutoGUI *(optional)* | ≥ 0.9.54 | Visual agent mouse / keyboard |
| BehaveX *(optional)* | ≥ 4.0.0 | Local parallel execution |
| mss *(optional)* | ≥ 9.0.1 | Screen capture for OCR/visual fallback |
| Appium-Python-Client *(optional)* | ≥ 3.0.0 | Native apps — Android/iOS/Windows 11/macOS (see [docs/native-apps.md](native-apps.md)) |
| pygetwindow *(optional)* | ≥ 0.0.9 | Window focus for the desktop visual agent |
| pytest *(dev)* | ≥ 7.4.0 | `unit_tests/` |
| azure-identity *(optional)* | ≥ 1.15.0 | Azure Key Vault auth |
| azure-keyvault-secrets *(optional)* | ≥ 4.7.0 | Azure Key Vault secret fetch |
| pygls / lsprotocol *(optional)* | ≥ 1.3.0 / ≥ 2023.0.0 | `noodle-lsp` — the VS Code extension's language server |
| Allure | 3.x (npm package `allure`) | `noodle report generate/open`; not the legacy Java `allure-commandline` 2.x |
| Node.js | ≥ 18 | BusterBlock test app, Allure 3 CLI |

---

## Quick reference

### All commands

One binary, `noodle`, every capability a subcommand — run `noodle --help`
for this list, or `noodle <command> --help` for one command. For every flag,
its purpose, when to use it, and sample output, see
**[docs/cli-reference.md](cli-reference.md)**.

| Command | Purpose | Example |
|---|---|---|
| `run` | Run `.feature` files | `noodle run web/busterblock --headless --tag @smoke` |
| `init` | Scaffold a workspace (`noodle.yaml`, `.env`, `noodle_tests/`) | `noodle init ~/my-tests --llm ollama` |
| `doctor` | Context-aware, read-only health check: install + launcher provenance always, plus engine-checkout or workspace checks by context ([reference](#health-check--noodle-doctor)) | `noodle doctor --json` |
| `validate` | Parse features + check step/var resolution, no browser | `noodle validate . --resolve` |
| `list` | List discovered scenarios without running them | `noodle list --workspace ~/my-tests --json` |
| `steps` | Search the step dictionary for example phrasing | `noodle steps clipboard` |
| `step-search` | Find (or draft) the closest step for a plain-English action | `noodle step-search "clear the cart" --accept` |
| `probe` | Pre-authoring DOM probe: controls + selectors + POM suggestions, no test run | `noodle probe https://example.com/login` |
| `inspect` | Debug a locator phrase: every candidate (text/alt/aria/POM/DOM-scan source, visibility) + what `find()` picks | `noodle inspect https://example.com "Weekly Flyer"` |
| `summary` | Plain-English pass/fail summary of the last run | `noodle summary --json` |
| `rca-report` | Root-cause every failed/errored scenario as Markdown | `noodle rca-report --serve` |
| `report generate` \| `open` \| `serve` | Build / open / network-serve the Allure HTML report | `noodle report generate && noodle report open` |
| `repl` | Interactive plain-English shell (rule-based; `--llm` for free-form) | `noodle repl --workspace ~/my-tests --llm ollama` |
| `record` | Record a new test by acting in a browser | `noodle record --workspace ~/my-tests` |
| `clean` | Delete the last run's artifacts tree — everything a run regenerates | `noodle clean --workspace ~/my-tests` |
| `archive` | Zip the last run's tree to `archives/` on demand | `noodle archive --workspace ~/my-tests` |
| `artifacts` | List the last run's tree, by category | `noodle artifacts --workspace ~/my-tests` |

Driving these via an AI agent instead of typing them? Same tools, plain
English — see [More sample prompts](../README.md#zero-to-hero--connect-an-mcp-host)
above.

### Run commands

```bash
noodle run web/busterblock --headless                                          # all BusterBlock tests
noodle run web/busterblock/features/login.feature --headless                   # one feature file
noodle run web/busterblock --tag @smoke --headless                             # by tag
noodle run web/saucedemo --headless                                            # SauceDemo
noodle run web/example --headless                                         # Example
noodle run api --headless                                                       # API tests
noodle run . --headless                                                         # everything in the workspace
noodle run web/busterblock --headed                                            # watch the browser instead of headless
noodle run --workspace ~/projects/my-tests --headless                          # from anywhere, pointed at a workspace
noodle run -w ~/projects/my-tests/noodle_tests/app1 --headless                 # ONE app, from anywhere
cd web/busterblock && noodle run --headless                                    # or just cd into the app
python -m pytest unit_tests/ -v            # the framework's own unit tests — from inside the noodle/ clone
```

Single-app runs (an app dir, its `features/`, or one `.feature`) write their
whole output tree to that app's `report/` folder; suite-wide runs use
`artifacts/`. Follow-up commands (`summary`, `rca-report`, `report …`,
`archive`, `clean`) find the last run automatically.

### RCA commands

```bash
noodle rca-report                          # heuristic + agentic table to stdout
noodle rca-report --out rca.md             # write to a file instead
noodle rca-report --llm                    # add an AI narrative on top
noodle summary                             # plain pass/fail, no root-causing
noodle summary --llm ollama                # plain-English summary via a model
```

Config files (`.env` / `secrets.env` / `environments.yaml`) →
[Part 4 — Configure](#part-4--configure). LLM mode toggle
(`NOODLE_LLM_MODE=auto|full`) → [How it works](../README.md#how-it-works) above.

### Where results go after a run

Everything a run produces lands under one root — Java's `target/`
equivalent, so one folder holds every category and CI can archive/ship the
whole tree in a single step. That root is **`<app>/report/` for a
single-app run** (per-app isolation, NOOD_0086) and **`artifacts/`** for a
workspace-wide run; `NOODLE_ARTIFACTS_DIR` overrides both. The table below
shows the layout inside whichever root the run used (shown for
`artifacts/`):

| Output | Location |
|---|---|
| Allure raw results (input to `rca-report`) | `artifacts/allure-results/` |
| Allure HTML report (Allure 3) | `artifacts/reports/allure-report/` |
| Allure trend history (JSONL, survives `noodle clean`; in Azure carried by a 7-day pipeline cache) | `artifacts/reports/allure-history/history.jsonl` |
| JUnit XML | `artifacts/reports/junit.xml` |
| RCA report | `artifacts/reports/rca.md` (auto, when a run has failures) |
| Locator heal suggestions | `artifacts/reports/healing-report.txt` |
| Failure screenshots | `artifacts/screenshots/FAILED_<step>.png` |
| Failure traces (Playwright) | `artifacts/traces/<scenario>.zip` |
| `@record_video` recordings | `artifacts/videos/` |
| Network/console capture (failed scenarios) | `artifacts/network/<scenario>.json` |
| Noodle's own run log (sys log) | `artifacts/logs/noodle.log` |

### Managing the artifacts/ tree

```bash
noodle artifacts    # list what's there, by category, with file counts + size
noodle clean        # delete the last run's tree — everything a run regenerates
noodle archive      # zip the last run's tree to archives/artifacts_<timestamp>.zip
```

All three follow the last run's root (the app's `report/` or `artifacts/`),
take `--workspace`/`-w` like the other commands, and can be run from inside
an app folder to scope to that app alone. `noodle run` overwrites its target
tree in place (NOOD_0093) — the Allure trend history is preserved across the
wipe, so the report keeps its trend line without any archiving. `noodle
archive` takes a snapshot on demand when you want to keep a specific run's
full tree (screenshots/traces); `noodle report serve <stamp>` re-hosts it.

---

### Health check — `noodle doctor`

```bash
noodle doctor [PATH] [--scope auto|engine|workspace|install] [--json]
```

Read-only, fast, and safe to run from anywhere: no writes, no network, no
browser, no package changes, no secret values read or printed. It diagnoses
and prints the exact remediation command — repair itself stays with
`noodle init` (workspaces) and the documented reinstall commands (installs).

**Context detection (NOOD_0138).** Doctor resolves what you pointed it at by
walking `PATH` (default: the current directory) and its **ancestors only** —
never siblings, your home directory, or the wider filesystem:

1. a directory containing the engine source markers (`pyproject.toml` with
   `name = "noodle"`, `noodle/__init__.py`, `noodle/cli.py`, `unit_tests/`)
   → **engine** profile;
2. a directory containing `noodle.yaml` → **workspace** profile;
3. neither found → **install-only** checks.

The nearest matching ancestor wins; when one directory carries both marker
sets (the engine repo is deliberately its own workspace), **engine wins** —
so an accidental `noodle init` in the engine checkout can't flip the
diagnosis. Force a profile with `--scope` for unusual layouts; a forced
scope whose marker is absent exits 2. The resolved context is always the
first output line, e.g. `Context: workspace /work/shop-tests`.

**What each profile checks** (stable IDs, same in text and `--json`):

| ID | Profile | Checks |
|---|---|---|
| `install.active-build` | always | the running build: version, editable/copy, path, git SHA |
| `install.editable` | always | non-editable copy → **fail** (a `git pull` can never update it) |
| `install.launchers` | always | every `noodle` on PATH is probed for **provenance** (`--version`, short timeout). Identical builds → **info** (a project `.venv` + `uv tool` shim running the same editable source is normal, not broken). Different version/root/SHA/install type → **fail**; unprobeable → **warn** |
| `engine.source-root` | engine | engine markers + parseable `pyproject.toml` |
| `engine.install-link` | engine | the active package resolves to *this* checkout (else your source edits don't run) |
| `engine.workspace-artifacts` | engine | warns on `AGENTS.md`/`PROMPT_TEMPLATE.md`/`noodle_tests/` at the engine root (accidental `noodle init`); listed for manual review, never deleted |
| `workspace.config` | workspace | `noodle.yaml` parses; `tests_dir` is relative and stays inside the workspace |
| `workspace.layout` | workspace | tests dir + scaffold glue (`environment.py`, `steps/z_catch_all.py`, `pom.yaml`) exist |
| `workspace.templates` | workspace | generated instruction/template drift → `noodle init <root> --force` (originals saved `*.bak`) |
| `workspace.mcp` | workspace | MCP client configs parse and their command exists; absent configs are **info**, a dead command → `noodle init mcp` |

The engine profile never compares engine docs (`README.md`, `CLAUDE.md`)
against workspace templates and never recommends `noodle init --force` for
engine files.

**Severity and exit codes.** `pass`/`info` are healthy; `warn`/`fail` are
findings. Exit `0` = only pass/info; `1` = at least one warn/fail; `2` =
bad arguments, missing path, or a forced `--scope` with no matching root.
An individual check crash becomes an explicit `*.internal-error` fail
record, never a silent skip.

**JSON.** `--json` emits one bounded object — no ANSI, no secret values:

```json
{"ok": true,
 "context": {"kind": "workspace", "root": "/work/shop-tests", "start": "/work/shop-tests/web/app1"},
 "checks": [{"id": "install.active-build", "scope": "install", "status": "pass",
             "summary": "noodle 0.1.0 (editable) …"}]}
```

## Troubleshooting

**Windows: `Activate.ps1 cannot be loaded because running scripts is
disabled on this system`**
PowerShell's default execution policy blocks activation scripts. Run once
per user (no admin needed): `Set-ExecutionPolicy -Scope CurrentUser
-ExecutionPolicy RemoteSigned`, then retry `.venv\Scripts\Activate.ps1`.

**`noodle: command not found`**
Using the project-local `.venv`? Activate it first — macOS:
`source .venv/bin/activate`; Windows: `.venv\Scripts\Activate.ps1`. Used
`uv tool install` instead for a permanent, no-activate `noodle` on `PATH`?
Run `uv tool update-shell`, then open a **new** terminal — the PATH edit
doesn't apply to already-open shells. See [Part 2](#part-2--get-the-framework).

**`noodle` behaves like an old version — a `git pull`/re-clone changes
nothing, config overrides don't take effect, or fixes visible in the source
don't run**
The `noodle` on PATH is a **non-editable copy** in some Python's
`site-packages`, shadowing your clone — a copy is a snapshot and never
updates on pull. Diagnose: `noodle doctor` prints the resolved build path,
whether it's editable, its git SHA, and probes every `noodle` launcher on
PATH for the build it actually executes — **two launchers running the same
editable build (project `.venv` + `uv tool` shim) are `INFO`, not a
problem**; a launcher reporting a *different* version/path/SHA is the
failure, with the exact reinstall command. `which -a noodle` (macOS/Linux) /
`Get-Command noodle -All` (Windows 11) shows what's shadowing what.
`noodle --version` and the first line of every `noodle run` also name the
build that's actually executing. Cure — remove every prior copy, then
reinstall editable from the clone:
```bash
pip uninstall -y noodle        # run both; ignore "not installed"
uv tool uninstall noodle
cd <your-clone>
uv tool install --editable ".[all]" --with-executables-from playwright
uv tool update-shell           # then open a NEW terminal
which -a noodle                # Windows 11: Get-Command noodle -All
                               # must resolve into uv's tool dir, NOT a site-packages copy
```
Prefer the editable install ([Part 2](#part-2--get-the-framework), Option
B) so it can't recur — a non-editable install is "advanced: you accept
manual reinstalls on every update".

**`noodle --version` shows an old number (e.g. `0.2.0a3`) after a
`git pull`, even on an editable install**
The version lives in **one place in the source**: `pyproject.toml`
(`[project] version`). `--version` reads the `.dist-info` metadata written
at *install time* — an editable install keeps the **code** current across
pulls, but that recorded number only refreshes on reinstall. NOOD_0156:
`noodle --version`, `noodle run` and `noodle doctor` all compare the two and
print a ⚠️ mismatch line naming the cure:
```bash
noodle update
```
(If the numbers *still* disagree afterwards, you're back in the shadowed
stale-copy case above — run `noodle doctor`.)

**After every `git pull` or `git checkout <branch>` — run `noodle update`**
This is the whole answer to "am I on the right build?". An editable install
keeps the **code** current across pulls, but not the **dependencies** a
branch may have changed, nor the recorded version; a non-editable copy keeps
running the code it was installed with. `noodle update` re-links the running
`noodle` to its clone:
```bash
git pull                       # or: git checkout feature/nood_0156
noodle update                  # ~10s, idempotent, safe to run every time
noodle --version               # confirms: version + path + git SHA
```
What it does: runs exactly the reinstall `noodle doctor` recommends
(`uv tool install --force` or `pip install -e`, matched to how *this* copy
was installed), from the clone, against the interpreter that is running the
`noodle` you just typed — so a venv install stays in the venv and a system
install stays system-wide. `--dry-run` prints the command and the clone
without running it. It never touches git, and never installs Playwright
browsers (Playwright's own error tells you when those need refreshing).

**Run it from anywhere — including a test workspace.** An editable install
points at the clone, so `noodle update` finds it and reinstalls *there* no
matter what directory you're standing in; you never need to `cd` to the
clone first. (The exception is a **non-editable** install, which has no link
back to a clone: from a workspace it exits 2 and asks you to `cd` there. That
setup is a `FAIL` in `noodle doctor` regardless — fix it rather than work
around it.)

Two caveats:
* It repairs **the launcher first on your PATH**, and says so when others
  exist. `noodle doctor` reports whether those run a different build.
* On Windows the running `noodle.exe` can be locked; retry as
  `python -m noodle update`, which has no shim to lock.

**A test opens the site's root (or wrong page) instead of the page you
authored against — RCA says `navigation-mismatch`**
Environments files written before NOOD_0135 stored only the URL's origin
(`https://host`), silently dropping the path — the run then opened `/`
instead of e.g. `/application/login`. The dropped path is not recoverable
from the file, so there is no automatic migration: re-run authoring
(`author_test` / `noodle author`) with the original **complete** URL and
`overwrite=true` — the app's URL key is corrected in place and unrelated
keys are preserved. Don't hand-guess paths across a workspace; fix each app
from its real URL.

**Navigation times out on a slow internal site
(`Could not load page …`)**
Raise the page-load/element-find budget: `NOODLE_FIND_TIMEOUT=300000` in
`.env` gives 5-minute loads (default 120000). TLS/self-signed-cert errors
are already ignored by default (`NOODLE_IGNORE_HTTPS_ERRORS=true`) — if you
opted back into strict certs with `@secure_certs` or
`NOODLE_IGNORE_HTTPS_ERRORS=false`, a self-signed cert stalls the load and
looks identical to slowness.

**Windows: `python --version` opens the Microsoft Store instead of printing
a version**
An App execution alias is shadowing the real interpreter. Settings → Apps →
Advanced app settings → App execution aliases → turn off `python.exe` /
`python3.exe`, then open a new terminal.

**VS Code extension installed, `.feature` files still show no colour at
all after a full quit and reopen**
Almost always a same-version reinstall that got silently skipped, not a
reload problem — VS Code's local-vsix install treats "same version already
here" as a no-op unless told otherwise, and the extension's manifest
version never changes between Noodle releases. Reinstall with `--force`
(`make install-ext` already passes it; manually: `code --install-extension
<path-to-vsix> --force`), then fully quit and reopen VS Code again — not
just **Developer: Reload Window**. Full diagnostic steps (including telling
this apart from a Cucumber-extension conflict) in
[Part 3](#part-3--vs-code--syntax-highlighting-optional-recommended).

**`uv pip install -e ".[llm]"` (or `".[all,llm]"`) fails deep in a build with
`[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed`, often
mentioning `rustup`/`download_rustup`/`setup_rust`**
Almost always a corporate/managed ("controlled") network with a
TLS-inspecting proxy (Zscaler, Netskope, a corporate VPN) — your OS already
trusts its root CA (that's why your browser is fine), but the Python build
tooling doesn't. It shows up here specifically because `litellm` (or one of
*its* dependencies) has no prebuilt wheel for your exact Python/platform, so
it falls back to building from source — and that source build tries to
bootstrap a Rust toolchain over HTTPS using its own bundled certificate
list, which the proxy's re-signed certificate doesn't match. This can't take
down anything else — `llm` installs separately from `all` (NOOD_0074) for
exactly this reason. Three ways out, easiest first:
1. Don't need LiteLLM-backed manual mode right now? Just skip `.[llm]"` —
   MCP mode and no-LLM manual mode (the default `uv pip install -e ".[all]"`
   from Part 2) don't need it at all. Add it later once your certs are sorted.
2. Install `pip-system-certs` into the venv *before* retrying — it patches
   Python's ssl/httpx/requests to trust your OS certificate store instead
   of a bundled list, so the already-trusted corporate CA just works:
   ```bash
   uv pip install pip-system-certs
   uv pip install -e ".[llm]"
   ```
3. If IT gives you the corporate root CA as a `.pem`/`.crt` file, point
   both Python and `uv` at it directly, then retry:
   ```bash
   export SSL_CERT_FILE=/path/to/corp-ca-bundle.pem      # Windows: $env:SSL_CERT_FILE = "C:\path\corp-ca-bundle.pem"
   export REQUESTS_CA_BUNDLE=/path/to/corp-ca-bundle.pem  # Windows: $env:REQUESTS_CA_BUNDLE = "C:\path\corp-ca-bundle.pem"
   export UV_NATIVE_TLS=1                                  # Windows: $env:UV_NATIVE_TLS = "1"
   uv pip install -e ".[llm]"
   ```

**`litellm` not found / LLM import error**
```bash
uv pip install -e ".[llm]"
```

**`ConnectionRefused` when running LLM tests / with `--llm ollama`**
Ollama is not running. Start it: `ollama serve` (separate terminal, keep it open).

**Model is slow on first call**
Normal — the model loads into memory on first use; later calls are faster.

**BusterBlock tests fail with "connection refused"**
Terminal A is not running the app. `cd test-apps/busterblock && npm start`.

**`No pattern matched and no LLM configured`**
You wrote a step that doesn't match a built-in pattern, and no model is
set. Either use a built-in phrase (see
[docs/steps_dictionary.md](steps_dictionary.md)) or add
`NOODLE_MODEL=anthropic/claude-sonnet-5` (or local `ollama/llava`) to `.env`.

**Allure report is blank when I open `index.html` directly**
Use `noodle report open` instead — the report needs HTTP, not `file://`.

**`noodle rca-report` says "No failed or errored scenarios in the last run"**
Either everything passed, or `artifacts/allure-results/` is from an older/different
run — `noodle run` rewrites that directory on every invocation (the previous
run is auto-zipped to `archives/` first), so run the suite you want
root-caused right before `rca-report`, not a while before.

**The "Agentic (AI) verdict" column always says "no vision-capable NOODLE_MODEL configured"**
Expected unless `NOODLE_RCA=true` and `NOODLE_MODEL` point at a
vision-capable model (`anthropic/claude-sonnet-5`, `openai/gpt-4o`, local
`ollama/qwen2.5vl:7b`). A text-only model (Groq, local `llama3.1`) can't produce
this column — the heuristic column still works either way.

**A generated agent test fails immediately**
It's a skeleton. Replace the `<placeholders>` in the `.feature` and the
`<css selector>` stubs in `resources/pageobjects/<name>_pom.yaml`.

**Typed a natural-language request into `noodle repl` and got "Don't understand"**
Free-form requests need a model configured — either `noodle init --llm ...`
persisted one into this workspace's `.env`, or pass `--llm`/`--model` on
`noodle repl` itself. Without either, use the literal phrase
`create test for <desc> at <url>`.

**Ran `noodle init --llm ollama` but `noodle repl` still says `(rule-based, no LLM)`**
`.env` already existed (init never overwrites it — `--llm` was ignored, and
`init` printed a note saying so). Add `NOODLE_MODEL=ollama/llama3.2` to that
`.env` by hand, or delete it and re-run `init --llm`.

**MCP `run_test`/`run_and_report` fails with `Executable doesn't exist at
/root/.cache/ms-playwright/...` even though the identical `noodle run`
command works in the same environment**
Your MCP host spawned `noodle-mcp` with a stripped-down environment (most
hosts default to a minimal env for a stdio server, not a full inherited
one), so container-level vars like `PLAYWRIGHT_BROWSERS_PATH` never reached
the process — the engine subprocess then looked for browsers in the
default cache path instead of wherever they were actually installed. Add
an `env` block to your host's MCP server config with whatever vars your
deployment needs, e.g.:
```json
{
  "command": "/path/to/noodle/.venv/bin/noodle-mcp",
  "args": ["--workspace", "/path/to/my-tests"],
  "env": { "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright" }
}
```

**MCP-driven test run fails with `Looks like you launched a headed browser
without having a XServer running`**
`noodle init` scaffolds `.env` with `NOODLE_HEADLESS=false`, and most MCP
hosts (CI, containers, sandboxes) have no display. Pass `headless: true` on
the `run_test`/`run_and_report` tool call to override it for that run —
no need to edit the workspace `.env`.


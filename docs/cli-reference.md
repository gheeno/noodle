# CLI reference
<!-- Branch: NOOD_0057 -->

Every `noodle` subcommand and flag, what it's for, and when to reach for it.
For a one-line command list see README.md § All commands; for narrative
walkthroughs see `docs/agent-playbook.md` and
`docs/workspace-guide.md`. Run `noodle <command> --help` any time for the
live version of this — that's the source of truth if this drifts.

Flags shared by nearly every command:

- **`--workspace` / `-w`** *(default: `.`)* — the directory holding this
  project's `noodle.yaml`, `noodle_tests/`, `.env`. Everything else (`.env`,
  run output, reports) resolves relative to it. Use it to run commands
  against a workspace you're not `cd`'d into. It may also point at a single
  app package (`-w <ws>/noodle_tests/app1`, or just `cd` there) — commands
  then scope to that app and its `report/` tree (NOOD_0086).

---

## noodle run

Run `.feature` files.

```
noodle run [PATH] [OPTIONS]
```

`PATH` — file or directory to run (default: workspace `tests_dir`).

**Per-app artifact routing (NOOD_0086):** when `PATH` targets a single app
package (the app dir, its `features/`, or one `.feature` inside it), the
run's whole artifacts tree — allure-results, screenshots, reports, trend
history — is written to that app's `report/` folder instead of
`<workspace>/artifacts/`, keeping every app-under-test self-contained.
Follow-up commands (`summary`, `rca-report`, `report …`, `clean`, `archive`,
MCP `get_last_result`) automatically follow the last run's root via
`.noodle/last_run_root`. An explicit `NOODLE_ARTIFACTS_DIR` overrides both.

You can also invoke noodle **from inside an app package** — `cd
noodle_tests/app1 && noodle run` re-roots on the nearest ancestor holding
`noodle.yaml` (so `.env`, secrets and config still resolve) and runs just
that app; `summary`, `rca-report`, `report …`, `clean` and `archive` invoked
there operate on that app's `report/` tree. The same works for the engine
repo's own suites (`cd sample_feature_tests/web/busterblock && noodle run`),
and over MCP by passing the app dir as `workspace`.

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | Running from outside the workspace, or against another project. |
| `--headless` | off | Force no browser UI | CI, or overriding a workspace default of `headless: false`. |
| `--headed` | off | Force a visible browser | Debugging a test locally; overrides both `--headless` and `.env`. Mutually exclusive with `--headless`. |
| `--tag`, `-t` | none | Filter by tag, e.g. `smoke` | Running a subset instead of the whole suite. |
| `--browser`, `-b` | workspace config | `chromium \| firefox \| webkit \| safari \| edge` | One-off cross-browser check without editing `noodle.yaml`. |
| `--retries` | `1` | Re-run a failed scenario N extra times | Flaky suite; `0` disables retries entirely. |
| `--log-level` | none | `DEBUG \| INFO \| WARNING \| ERROR` | Diagnosing a step/engine issue. |
| `--parallel` | `0` (off) | Run N feature files at once via behavex | Large web suites in `--headless` mode; needs `pip install -e ".[parallel]"`. Falls back to `$NOODLE_PARALLEL_PROCESSES` if unset. |
| `--parallel-scheme` | `feature` | With `--parallel`: shard by `feature` or `scenario` | `scenario` also shards inside one big feature file; `feature` keeps each file's scenarios in order on one worker. |
| `--quiet`, `-q` | off | Suppress the live behave console stream — full output still goes to `<artifacts>/run.log`, stdout gets just the run summary | Agent/CI-driven runs (NOOD_0116): the live stream is the single largest blob an agent's context holds resident per tool call across a multi-step fix loop. Single-process runs only; ignored with `--parallel`. NOOD_0117: automatic when stdout isn't a TTY (agent/CI); `NOODLE_QUIET=0` forces the stream back, `NOODLE_QUIET=1` forces quiet. |
| `--preflight` / `--no-preflight` | **on** | Before launching a browser, check every `{env:KEY}` the target references resolves to a real value (not missing, empty, or `CHANGE_ME`) | NOOD_0128/0130: a missing credential aborts the run with exit 2 instead of failing 50s later at the login step — a doomed login run is the most expensive way to learn a secret is a placeholder. `--no-preflight` is the explicit escape hatch. |
| `--serve` | off | After the run, host the Allure + RCA reports on localhost and print the URLs (same as `noodle report serve`) | NOOD_0128: one command instead of run → generate → serve. The server is a detached child, so the URL outlives the run (NOOD_0161). |
| `--json` | off | Emit one bounded JSON payload — pass/fail summary, failing step, report paths, compact RCA on red, served URLs — instead of the human summary | NOOD_0128: CLI parity with the `run_and_report` MCP tool. NOOD_0156: also carries `verified` / `unverified_reasons` / `warnings` / `healing_events` / `evidence` — success means `failed == 0` **and** `verified: true`, and the compact RCA rides along whenever `verified` is false. Implies `--quiet` (the live stream would corrupt the single object on stdout). |

Runs overwrite `artifacts/` in place (NOOD_0093) — the Allure trend history is
preserved across the wipe, so trends carry forward without archiving. To stash
a specific run's full tree on demand, use `noodle archive`.

Sample:
```
$ noodle run --tag smoke --headless
```

## noodle init

Scaffold a test workspace.

```
noodle init [PATH] [OPTIONS]
```

`PATH` — directory to scaffold into (default: `.`).

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--llm` | none | `claude \| gemini \| ollama` — persist `NOODLE_MODEL` into `.env` | You want `noodle repl` to have LLM features on every future run without passing `--llm` again. No-op (with a printed note) if `.env` already exists. |
| `--model` | preset default | Override the model string used with `--llm` | Non-default model, e.g. `--llm claude --model anthropic/claude-haiku-4-5`. |
| `--force` | off | Refresh outdated template files, backing originals up to `*.bak` | Upgrading an existing workspace after a noodle upgrade (see below). |

Re-running `init` on an **existing workspace** is the upgrade path
(NOOD_0089). Files fall into three classes with three policies:

| Class | Files | Re-init behaviour |
|---|---|---|
| engine glue | `noodle_tests/environment.py`, `noodle_tests/steps/z_catch_all.py`, sample `report/README.md` | auto-rewritten to match the installed engine |
| templates | `AGENTS.md`, `README.md`, `CLAUDE.md`, `PROMPT_TEMPLATE.md`, sample feature + POM | kept if they differ (listed as outdated); `--force` refreshes them, saving the old copy as `*.bak` |
| config | `.env`, `noodle.yaml`, `noodle_tests/pom.yaml` | never touched, even with `--force` — they're yours |

`init` also wires MCP client config in the same shot (NOOD_0095): it calls
`init mcp` (below) at the end with `--force` off, writing/merging
`.mcp.json`, `.vscode/mcp.json`, and `.copilot/mcp-config.json` so the
workspace is agent-ready for Claude Code, VS Code Copilot, and Copilot CLI
without a separate step. Run `noodle init mcp --force` yourself later only
if you need to refresh a drifted entry.

Sample:
```
$ noodle init my-project --llm claude
Created:
  my-project/noodle.yaml
  my-project/.env
  ...

MCP client config:
  my-project/.mcp.json: created
  my-project/.vscode/mcp.json: created
  my-project/.copilot/mcp-config.json: created

Next: cd my-project && noodle repl  — next steps in README.md
```

## noodle init mcp / noodle init-mcp

Wire the workspace up for MCP-driven agents.

```
noodle init mcp            # or: noodle init-mcp [PATH] [--force]
```

Writes/merges the noodle server into `.mcp.json` (Claude Code),
`.vscode/mcp.json` (VS Code Copilot agent mode), and
`.copilot/mcp-config.json` (standalone Copilot CLI, NOOD_0096). Existing
JSON is merged, never clobbered; an existing differing `noodle` entry is
kept unless `--force`. The server runs on demand over stdio — nothing to
start manually.

In CI (Azure DevOps `TF_BUILD`, or any `CI` env) the files are still written
(so a pipeline can commit them for the team) but a note reminds you that
pipelines have no interactive agent — they call the `noodle` CLI directly,
as in `azure-pipelines.yml`.

## noodle doctor

Context-aware, read-only health check (NOOD_0138).

```
noodle doctor [PATH] [OPTIONS]
```

`PATH` — directory to diagnose (default: `.`). Doctor walks the path and
its **ancestors only** to find an engine checkout or a workspace
(`noodle.yaml`); nearest match wins, engine beats workspace when both
markers share a directory.

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--scope` | `auto` | `auto \| engine \| workspace \| install` — force a profile | Unusual nested layout, or install-only verification after a (re)install. `install` probes each PATH launcher with `--version`. |
| `--json` | off | One bounded JSON object: `ok`, `context`, `checks` with stable IDs | Agents/CI acting on findings without parsing prose. |

Always checks the install: active build (version, editable/copy, path, git
SHA) and every `noodle` launcher on PATH, compared by **provenance** —
identical duplicates are `INFO` (normal: project `.venv` + uv tool shim),
conflicting builds are `FAIL` with the reinstall cure. In an engine
checkout it verifies the editable install-link and warns on stray
workspace files; in a workspace it checks `noodle.yaml`, scaffold glue,
generated-template drift (`noodle init --force` refreshes), and MCP config.
Never writes, never touches the network, never reads secrets. Exit codes:
`0` healthy (pass/info only), `1` findings (warn/fail), `2` bad
path/scope. Full check-ID table:
[manual.md → Health check](manual.md#health-check--noodle-doctor).

Sample:
```
$ noodle doctor
Context: engine /Users/me/Projects/noodle
PASS  [install.active-build] noodle 0.1.0 (editable) /Users/me/Projects/noodle/noodle @ 1511295
PASS  [install.editable] running build is editable
INFO  [install.launchers] 2 launchers execute the same build — safe; optional cleanup reduces ambiguity
PASS  [engine.source-root] engine checkout at /Users/me/Projects/noodle (pyproject metadata readable)
PASS  [engine.install-link] active noodle package resolves to this checkout
```

## noodle validate

Parse `.feature` files and check variable references — no browser launched.

```
noodle validate [PATH] [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--resolve` | off | Dry-run every step against the pattern table | See which steps will hit the (free) pattern matcher vs. need the LLM fallback, before running anything. |
| `--json` | off | With `--resolve`: per-file matched/unmatched steps as JSON | Feeding validation results to an agent or CI check instead of eyeballing terminal output. |

Sample:
```
$ noodle validate noodle_tests/ --resolve
noodle_tests/sample_app/features/login.feature
  ✓ 4/4 steps resolved (pattern)
```

## noodle list

List discovered scenarios without running them.

```
noodle list [PATH] [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--json` | off | Feature/tag inventory as JSON, skips the behave dry-run | Scripting/agents that need structured output fast (this path is quicker than the plain-text behave dry-run). |
| `--query` | none | With `--json`: substring match over path / feature / scenario / tag; matching features carry their `scenarios` list | NOOD_0162: scenario NAMES are the bulk of this payload (25 KB in the engine repo) and a caller routes on path and tags, so the unfiltered call returns `scenario_count` instead. Ask for the names you actually need. |

Sample (`--json`):
```
$ noodle list --json
{
  "tests": [
    {
      "path": "noodle_tests/sample_app/features/login.feature",
      "feature": "Sample — login",
      "tags": [],
      "scenario_count": 1
    }
  ],
  "note": "scenario names omitted — pass query='<substring>' …"
}

$ noodle list --json --query login        # names, for the matches only
{"tests": [{..., "scenarios": ["User logs in"]}], "note": "1 feature(s) matching 'login'."}
```

## noodle steps

Search the built-in step dictionary and print matching example steps.

```
noodle steps [KEYWORD]...
```

No flags besides `--help`. Each `KEYWORD` matches the step text, its
section, or its action type (e.g. `clipboard`). Several keywords in one
call print the union of hits in dictionary order (NOOD_0169 — a reviewed
session paid ten CLI calls for ten words); a keyword with no match is
noted inline, the rest still print. Use it as a fast in-terminal
vocabulary lookup while writing a feature file, instead of opening
`docs/steps_dictionary.md`.

**With no keyword you get the section index** — every section and its step
count, ~2 KB (NOOD_0161). Printing all 359 steps cost 20 KB for a caller who
wanted one; the whole dictionary is `noodle docs steps_dictionary`.

Sample:
```
$ noodle steps clipboard

Clipboard (Phase Q)
  When User copies 'https://example.com/share/42' to the clipboard
  Then the clipboard should contain 'share/42'

2 step(s). Section index: `noodle steps`; full reference: `noodle docs steps_dictionary` (MCP: read_docs('steps_dictionary'))
```

## noodle wok

List Noodle's woks — the four capability work areas (NOOD_0155) — or inspect
one.

```
noodle wok [NAME]
```

No flags besides `--help`. Without `NAME`, prints every wok (web, mobile,
desktop, performance) with its one-liner, routing tags, engines, and
whether its optional dependencies are installed on this machine. With a
name, adds the wok's sample-suite folder, per-wok unit-test folder, and how
it satisfies the screenshot capability. Concept doc:
[woks.md](woks.md).

Sample:
```
$ noodle wok performance

🍜 Performance wok (performance) — ready
   HTTP load tests from plain Gherkin — built-in threaded load generator, ...
   tags: @perf
   engine: Built-in load generator (stdlib threads + urllib, @perf)
   samples:     sample_feature_tests/performance
   unit tests:  unit_tests/woks/performance
   screenshots: Rendered latency-over-time chart PNG (Pillow) attached like any screenshot
```

## noodle step-search

Find the closest existing step for a plain-English description; drafts a new
one if nothing fits.

```
noodle step-search QUERY [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace whose `docs/` holds the project's own staged vocabulary (read **and** written here) | Same workspace `noodle run` will later load accepted suggestions from — keep them matched. |
| `--accept` | off | Non-interactively write the suggested new step (`docs/agent_patterns.yaml` + `steps_dictionary.md`) | CI/scripting, where there's no human to answer the y/N prompt `noodle repl` gives interactively. |
| `--no-llm` | off | Skip the local LLM tie-breaker even if `NOODLE_MODEL` is set | Deterministic output for tests/CI, or when the local LLM is slow/unavailable. |

Sample:
```
$ noodle step-search "click the submit button"
Best match (high confidence):
  User clicks the '{name}' button
  section: Web actions   type: click
```

## noodle probe

NOOD_0113 — proactive DOM probe: open the page(s) headless and dump every
actionable control (visible **and** hidden trigger zones) with a ready CSS
selector, which controls need a POM entry (with paste-ready POM YAML), a
vocabulary-shaped suggested step each, exact heading texts for assertions,
and same-origin next-page candidates. Run it **before** authoring a feature
against an unfamiliar or SPA page; exits non-zero if no page was reachable.

```
noodle probe URL [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--json` | off | Emit the probe payload as JSON instead of the readable summary — the **compact** author-evidence payload (`author_ready`, `headings`, `pom_yaml`, `suggested_steps`, `search`, `revealed`, `skeleton`), the same one MCP `probe_page` returns, bounded to 24 KB | Piping into scripts, or an agent that wants the structured `pages[]` payload. NOOD_0161: it is compact by default — never post-process it with `jq` to slice out author evidence, it is already only that. |
| `--full` | off | With `--json`, emit the RAW uncapped payload instead — every selector, next-pages, hundreds of KB | NOOD_0161: only when a capped list hid something you need (the compact payload's `truncated` note says when). Not an agent's default door. |
| `--timeout` | `15000` | Per-page load timeout in ms | Slow-loading apps. |
| `--click` | none | Click a control before taking a fresh snapshot — pass a control name (matched against the initial probe's own control names) or a raw selector; repeatable, executed in order | NOOD_0116: any control gated behind a click — a dev panel, a tab, a modal — is invisible to a single-load probe. Runs for real: name reveal controls, never state-mutating buttons. |
| `--do` | none | Execute a stateful transaction after any `--click` reveals: `"enter <value> in <field>"`, `"select <option> from <dropdown>"`, `"click <name>"`; repeatable, executed in order, each action followed by a settle + delta snapshot under `revealed` | NOOD_0144: multi-stage flows — fill → select → save → "login appears" is discovered in ONE probe instead of one guessed locator per red run. Actions run for real (a save/submit is the point); `{env:KEY}` in a value resolves from the workspace env files, so secrets never transit the transcript. |
| `--search` | none | Perform the site search with this term (editable-first box detection; icon-opened boxes clicked open) and summarize the RESULTS page too | NOOD_0117: search tests — surfaces the results page's new controls, its "NN results" summary element (with a ready POM entry), and the summary-count assertion to prefer over counting rendered cards. |
| `--suggest` | none | Type this partial term per-character into the search box and capture the **typeahead**: exact suggestion strings in order, the navigating selector per row, a flag on no-op icon sub-elements, and copy-ready steps (`selects the "…" suggestion for "…"`, `the search suggestions for "…" include "…"`) | NOOD_0141: autocomplete tests. Add `--follow` to also pick a row and probe the page it lands on — the whole suggestion flow in ONE probe. |
| `--pick` | none | With `--search`: bind "any matching result" to ONE concrete result caption (term or phrase match, unique stable caption + selector), click it, and snapshot the landed page under `search.picked` | NOOD_0156: a test that says "open a result" needs a real caption, not a guess. Pass `*` for any term-matching result, or a phrase to narrow. Ambiguity **refuses** instead of guessing. Read-only navigation — never a mutating control. |
| `--follow` | none | With `--suggest`: click the captured suggestion row matching this text (containment first, then fuzzy — a misspelled site row still matches your correctly-spelled ask) and summarize the page it lands on exactly like `--search` | NOOD_0142: type → suggestions → pick → results in one probe. The emitted steps carry the row's EXACT text. |
| `--expect` | none | After all navigation (`--click` / `--suggest` / `--follow` / `--search`), verify this text is present on the landed page; repeatable | NOOD_0142: one FOUND/NOT FOUND verdict line each, printed at the TOP of the output — the cheap alternative to dumping every control just to confirm a product name is there. |
| `--open-native` | off | After any `--click` reveal, automatically enumerate native `<select>` options and click-open custom comboboxes (initial page and each revealed panel), never a state-mutating control | NOOD_0128: nested dropdown options surface in this one probe instead of one guessed run per level. |
| `--max-reveal-depth` | `1` | With `--open-native`, how many levels of custom-combobox opening to follow (a dropdown revealed inside a dropdown) | Deeply nested config panels. |
| `--discover` | off | Click bounded generic disclosure candidates (hidden triggers, `aria-expanded=false`, tabs/menus, panel/settings/config-named buttons — never a state-mutating name), record each delta under `revealed`, revert between branches, and return a discovery trace naming every skip. Depth 1, capped clicks and time | NOOD_0136: when the trigger's NAME is unknown, so `--click` has nothing to aim at. NOOD_0137 — permission prompts, optional popups, standard search and requested assertions do NOT imply discovery: use it only when the goal needs an unnamed control that reveals otherwise-inaccessible UI. |
| `--compact` | off | Only what an author needs: controls needing a POM entry, paste-ready POM YAML, exact headings; drops the full control dump and next-pages list | NOOD_0117: the readable summary — a fraction of the tokens. Redundant with `--json`, which is compact unless you pass `--full`. |
| `--section` | `all` | Emit one slice only: `controls` \| `pom` \| `steps` \| `headings` \| `revealed` \| `all` | NOOD_0117: one narrow question instead of grepping the whole dump in context. `revealed` (NOOD_0126) prints ONLY what a `--click` opened — its new controls and steps, nothing from the initial load. |
| `--max-controls` | none | Cap each control list at N, noting how many were hidden | Long-tail pages. Compact mode caps at 25 by default (NOOD_0119); pass N to widen. |
| `--find` | none | Print ONLY the controls, result items, and card actions whose name, selector, step, or caption contains this text — matched PRE-cap, case/space-insensitive, with a selector, suggested step, and POM line per hit; with `--json`, the same hits as `{find, matches}` | NOOD_0169: one control out of a big page (a card's "Add to cart" below the compact cap) without grepping `.noodle/last_payload.json` — noodle output is payload-bounded; never pipe it through `grep`/`head`/`jq`. |

Sample:
```
$ noodle probe https://app.example/login
Probe: https://app.example/login — Sign In
  controls (4; * = needs POM entry):
  * [button] trigger settings panel — div[class~="trigger-settings-panel"] (hidden)  →  clicks "trigger settings panel"
    [field] username — input[name="username"]  →  enters "<value>" in the "username" field
  ...
  exact texts (copy assertions verbatim): "Welcome back"
  POM suggestion (paste into resources/pageobjects/):
    trigger settings panel:
      css: "div[class~=\"trigger-settings-panel\"]"
```

The settings panel above only renders after that trigger is clicked —
`--click` reveals it in the same probe instead of a hand-written Playwright
script:
```
$ noodle probe https://app.example/login --click "trigger settings panel"
...
  revealed after clicking "trigger settings panel" (3 new controls; * = needs POM entry):
    * [field] account id — input[formcontrolname="accountId"]  →  enters "<value>" in the "account id" field
    * [dropdown] region — custom-dropdown.e2e_settings-panel_region_dropdown  →  selects "<option>" from "region"
    * [button] save settings — button.e2e_settings-panel_save_button  →  clicks "save settings"
```
A target that can't be resolved/clicked lands in a `⚠` warning line instead of
failing the probe — the initial (pre-click) snapshot is always returned intact.

Drive the whole transaction and see what Save reveals — still one probe:
```
$ noodle probe https://app.example/login --click "trigger settings panel" \
    --do "enter 12345 in account id" --do "select East from region" \
    --do "click save settings"
...
  revealed after do: click save settings (2 new controls):
    [field] username — input[name="username"]  →  enters "<value>" in the "username" field
    [button] sign in — button[type="submit"]  →  clicks "sign in"
```
A `--do` action that fails lands in `do_warnings` the same advisory way.

Space/comma-separate several URLs to probe them in one browser session
(`noodle probe "https://a.example/login https://a.example/home"`). MCP
equivalent: `probe_page(url, click=[...], do=[...])`.

## noodle inspect

NOOD_0115 — resolve one locator phrase against a live page with the exact
machinery `find()` uses and show every candidate: source (visible text node /
image alt / aria-label / title / POM key / DOM attribute scan), match count,
per-match tag/text/visibility, and which element `find()` actually picks
(with any self-heal tier it needed). Use it when a step times out on an
element that is clearly on the page, or resolves to the wrong one — instead
of a throwaway Playwright script.

```
noodle inspect URL "LOCATOR TEXT" [OPTIONS]
```

| Option | Default | What it does | When to use |
|---|---|---|---|
| `--json` | off | Emit the raw payload as JSON | Agents/scripts consuming the structured result. |
| `--timeout` | 15000 | Page load timeout in ms | Slow-settling SPAs. |
| `--screenshot PATH` | off | Also save a screenshot with the resolved element outlined red | Visual confirmation of *which* element matched. |

```
$ noodle inspect https://www.example.com/en.html "Weekly Flyer"
Inspect: 'Weekly Flyer' on https://www.example.com/en.html
  [role=link accessible name] 1 match(es):
      <a> '' (visible)
  [image alt text] 1 match(es):
      <img> '' (visible)
  → find() resolves: <a> '' (visible)
```

MCP equivalent: `inspect_locator(url, text)`.

## noodle repl

Interactive plain-English shell.

```
noodle repl [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--llm` | none | `claude \| gemini \| ollama` — turn on free-form requests, failure repair, and compound-request planning for this session | You want to type natural-language requests beyond fixed keyword commands. Without it, the REPL is rule-based keyword matching only, no LLM required. |
| `--model` | preset default | Override the model string for `--llm` | Same as `run`/`init` — pin a specific model. |

## noodle record

Record a new test by performing actions in a browser.

```
noodle record [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--output`, `-o` | `noodle_tests/recorded.feature` | Path to write the generated `.feature` file | Recording into a specific app's test folder instead of the default. |
| `--name`, `-n` | `Recorded Feature` | Feature/scenario name | Naming the recorded scenario meaningfully instead of the default placeholder. |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |

## noodle summary

Plain-English summary of the last run.

```
noodle summary [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--llm` | `none` | `none \| claude \| gemini \| ollama` — richer narrative via litellm | You want prose explaining *why* things failed, not just counts. |
| `--json` | off | Structured output (counts + failures) for agents/CI | Piping into another tool instead of reading terminal text. |

## noodle cost

LLM token/dollar cost (NOOD_0080): the last run's actual spend, or a
pre-flight token estimate for a file. Covers Noodle's own `NOODLE_MODEL`
calls only — a driving agent's (Claude/Copilot) spend is billed to its own
subscription and is invisible to the engine (see docs/llm-setup.md §8).

```
noodle cost [TARGET] [OPTIONS]
```

| Flag / arg | Default | Purpose | When to use |
|---|---|---|---|
| `TARGET` | none | Prompt or `.feature` file to estimate | Before a big `--llm` generation or full-LLM-mode run: prints model-correct input tokens + the input-cost dollar floor (output tokens are unknowable pre-run). Omit it to print the last run's *actual* spend instead (calls, in/out tokens, dollars, split steps-vs-RCA). |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--model` | `NOODLE_MODEL` from the workspace `.env` | Model string to price against | Comparing what the same prompt would cost on a different provider. |
| `--json` | off | Structured output for agents/CI | Piping into another tool. |

Sample:
```
$ noodle cost
  💰 LLM cost: 12 call(s) | 48,210 in / 3,904 out tokens | ~$0.62 (llm $0.43, rca $0.19) | model anthropic/claude-sonnet-5
$ noodle cost noodle_tests/web/shop/features/checkout.feature --model anthropic/claude-sonnet-5
  💰 Estimate for noodle_tests/web/shop/features/checkout.feature: 412 input tokens | ~$0.0012 input-cost floor (output tokens unknowable pre-run) | model anthropic/claude-sonnet-5
```

## noodle rca-report

Root-cause every failed/errored scenario from the last run into Markdown.

```
noodle rca-report [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--compact` | off | Verdict + failing step + suggested fix per failure, a few lines total | NOOD_0117 cheap-evidence-first: read this before any screenshot or network capture. |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--out`, `-o` | stdout | Write to this file instead of printing | Saving the report alongside other artifacts, or feeding it to another step. |
| `--llm` | off | Add a prose narrative via `NOODLE_MODEL` (text-only) | The free heuristic classifier's terse output isn't enough context to act on. |
| `--propose-fix` | off | Ask `NOODLE_MODEL` for a unified-diff fix per failure (never applied automatically) | You want a starting-point patch to review, not just a diagnosis. Text-only, no vision model needed. |
| `--serve` | off | Also render `rca.html` and open it in the browser | You'd rather read a formatted page than a terminal Markdown dump — same self-contained-page approach as `noodle repl`'s "serve the rca". |

Sample:
```
$ noodle rca-report --propose-fix --out artifacts/rca.md
RCA report written to artifacts/rca.md
```

## noodle report

Manage test reports. Subcommands: `open`, `generate`, `serve`, `list`,
`stop`. All take `--workspace`/`-w`; with no explicit directory argument they
resolve against the workspace's **last-run root** (NOOD_0086): `<app>/report/`
after a single-app run, `<workspace>/artifacts/` after a workspace-wide one.

### noodle report open

```
noodle report open [REPORT_DIR] [OPTIONS]
```
Opens the last-generated Allure report in the browser. `REPORT_DIR` default:
`<last-run root>/reports/allure-report`.

### noodle report generate

```
noodle report generate [RESULTS_DIR] [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--out`, `-o` | `<last-run root>/reports/allure-report` | Output directory | Writing the HTML report somewhere other than the default. |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |

Re-builds BOTH reports (Allure HTML + `rca.md`/`rca.html` — NOOD_0082) from
existing `allure-results/` without re-running tests — useful after editing
results by hand or recovering from a report build that failed partway. The
RCA pair needs no Allure install and is always written; exits `1` if no
Allure report was built (e.g. Allure not installed), so CI and the MCP
`run_and_report` tool don't report phantom success.

### noodle report serve

```
noodle report serve [REPORT_DIR] [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--host` | `127.0.0.1` | Bind address | Default is local-only. Pass `--host 0.0.0.0` to share with teammates on the same network — failure screenshots/traces in the report can contain credentials typed during the run, so only share on a trusted network. |
| `--port`, `-p` | `8000` | Port to serve on | Avoiding a collision with another local server. |

Serves reports over HTTP. With no `REPORT_DIR` (NOOD_0082) it hosts the
last run's reports root — `<app>/report/reports` or
`<workspace>/artifacts/reports` — so ONE server carries both
the Allure report (`/allure-report/index.html`) and the RCA
(`/rca.html`), rebuilding either from `allure-results/` first if missing.
`REPORT_DIR` may also be:

- an explicit report directory (pre-0082 behaviour, unchanged),
- an `archives/artifacts_<stamp>.zip` path, or a bare stamp like
  `20260713_101112` — the archived run is extracted to a temp dir and its
  `reports/` tree served, so any older run from `noodle report list` can be
  re-hosted.

### noodle report list

```
noodle report list [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--json` | off | Machine-readable output | Agents/scripts. |

Shows what `report serve` can host: the live reports root (whether the
Allure report and `rca.html` are present, and when the Allure report was
generated) plus the timestamped `archives/` zips of earlier runs that
`noodle archive` wrote on demand (NOOD_0093 — runs no longer auto-archive).

### noodle report stop

```
noodle report stop [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--port`, `-p` | none (all) | Only stop the server on this port | Multiple report servers running, kill one without touching the rest. |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |

Stops hosted report servers (Allure + RCA) — from any terminal, not just the
one that ran `serve`. Two sources: the workspace's
`.noodle/report_servers.json` registry (written by `noodle report serve`),
and (NOOD_0095) ad-hoc servers an agent started by hand with a raw `python -m
http.server` pointed at a report directory — `noodle report stop` finds
these via `lsof` (best-effort; no-op on Windows) and kills them too, so an
agent that bypassed `report serve` doesn't leave a server orphaned. Registry
entries whose process is already gone are pruned silently.

## noodle clean

Delete the last run's artifacts tree — everything a run regenerates.

```
noodle clean [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--purge-history` | off | Also delete the Allure trend history | A true full wipe. Default preserves `reports/allure-history/` across the clean since `allure generate` folds it into the next report's trend widgets — `noodle archive` alone doesn't achieve this. |

## noodle archive

Zip the last run's artifacts tree with a timestamp.

```
noodle archive [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |
| `--out`, `-o` | `archives` | Directory to write the zip into | Stashing a run's reports before the next `noodle run` overwrites them, e.g. into a shared drop folder. |

## noodle artifacts

List what the last run's artifacts tree holds, by category, with file counts and
size.

```
noodle artifacts [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |

Sample:
```
$ noodle artifacts
allure-results/  (12 files, 340.2 KB)
reports/  (48 files, 5.1 MB)
```

---

## noodle diagnostic

Session diagnostics (NOOD_0147) — agent-written failure self-reports in the
workspace's gitignored `diagnostics/` folder. Trigger definitions, the
`diagnostic_due` run-result nudge, caps and dedupe:
[session-diagnostics.md](session-diagnostics.md).

### noodle diagnostic log

```
noodle diagnostic log <app> --trigger <t> --summary "…" [OPTIONS]
```

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--trigger`, `-t` | required | `hard-fail` \| `first-attempt-fail` \| `slow-dev` \| `over-budget` \| `manual` (repeatable) | Whatever fired — from the run result's `diagnostic_due`, or agent-evaluated (budget/manual). |
| `--summary`, `-s` | required | One short paragraph: what went wrong | Always. |
| `--timeline` / `--cause` / `--fixes` | — | Steps taken, suspected root cause, fixes tried | From session memory — never re-read logs to compose them. |
| `--duration-min` / `--attempts` | — | Dev wall clock, fix+rerun laps spent | If known. |
| `--agent` / `--agent-cost` | — | Driving agent + its OWN spend (e.g. `"codex 5.3"`, `"23 AIC"`) | Always when known — this is the number `llm_cost` can't see. |
| `--session` | — | Stable session id | A repeat call updates the same file instead of adding one. |
| `--workspace`, `-w` | `.` | Workspace dir | See top of doc. |

Engine facts (last-run counts/failures, compact RCA verdict, `llm_cost`,
version) are appended automatically; secret values are scrubbed. The folder
is capped at `NOODLE_DIAG_MAX` (default 25, oldest rotate out) and a
same-session/same-app re-log updates in place.

### noodle diagnostic list / bundle / guide

```
noodle diagnostic list [-w DIR]     # what's on disk, newest first
noodle diagnostic bundle [-w DIR]   # → diagnostics/noodle_diagnostics_<stamp>.zip
noodle diagnostic guide             # print the full contract — no MCP needed
```

`bundle` is the one file a tester sends back; a new bundle replaces the
previous zip. `guide` prints `session-diagnostics.md` from the CLI's own
bundled copy, so MCP-blocked environments get the trigger/field contract
without `read_docs`.

---

## noodle-mcp (MCP server)

Started separately from the `noodle` CLI — `noodle-mcp [OPTIONS]`. Full
lifecycle/transport details: `docs/mcp-guide.md`.

| Flag | Default | Purpose | When to use |
|---|---|---|---|
| `--workspace` | `.` | Workspace dir holding `noodle.yaml`, `noodle_tests/`, `.env` | Sets the server-wide default workspace every tool call falls back to. |
| `--workspace-root` | none (repeatable) | Directory whose subdirectories a per-call `workspace` override may point into | Letting a single running MCP server safely serve multiple workspaces. Unset over `stdio`, any path is allowed (the spawning host is already trusted); unset over `streamable-http`, overrides are locked to the `--workspace` dir only. |
| `--transport` | `stdio` | `stdio \| streamable-http` | `stdio` for local hosts (Claude Code, MAF `MCPStdioTool`); `streamable-http` for remote hosts (Azure AI Foundry, MAF `MCPStreamableHTTPTool`). |
| `--host` | `127.0.0.1` | HTTP bind address | `streamable-http` only; set `NOODLE_MCP_API_KEY` before binding beyond localhost. |
| `--port` | `8080` | HTTP port | `streamable-http` only. |

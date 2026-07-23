# Noodle Test Framework — Codebase Specification
<!-- Branch: NOOD_0150 -->

> **For:** maintainers and contributors — the formal repo inventory, not a how-to.
>
> Formal spec of the entire repository as of NOOD_0150: every package, its
> contract, the entrypoints, data layout, and configuration surface. For the
> narrative deep-dive see [architecture.md](architecture.md); for the
> user-facing manual see [encyclopedia.md](encyclopedia.md); for agent test
> authoring rules see [agent-playbook.md](agent-playbook.md).
> This page is the *inventory*: what exists, where, and what it promises.

---

## 1. What Noodle is

A BDD test framework where the test **is** the plain-English sentence.
Users write Gherkin (`behave`); a single catch-all step definition routes
every sentence through a deterministic regex pattern table to Playwright
(web), Appium (mobile), or an OpenCV/OCR visual agent (desktop). No
hand-written step glue, no hand-written page objects (an optional POM YAML
covers unnameable elements). LLMs are strictly opt-in: generation-time
authoring help and last-resort runtime fallback — never required, never
silent.

**Design principles** (from architecture.md §8): sentences over syntax;
deterministic before model; local-first; fail loudly with artifacts;
workspace = user's repo, engine = this repo.

## 2. Entrypoints

| Entrypoint | Module | Role |
|---|---|---|
| `noodle` | `noodle/cli.py` (`typer` app) | the engine CLI — run, doctor, init, init-mcp, author, cost, probe, probe-app, inspect, report (sub-app), validate, list, steps, record, clean, archive, artifacts, summary, rca-report, step-search, diagnostic (sub-app), **repl** |
| `noodle repl` | `noodle/cli.py:repl` → `noodle/repl/repl.py:run` | interactive English shell (NOOD_0056 — folded into `noodle` itself, no longer a separate binary); rule-based commands + optional `--llm` free-form tier; shells out to `noodle` for engine invocations |
| `noodle-lsp` | `noodle/lsp/server.py:main` | language server: validates `.feature` steps against the pattern table, tag/variable completions (consumed by `vscode-extension/`) |
| `noodle-mcp` | `noodle/mcp/server.py:main` | MCP server (`[mcp]` extra) — the agent core as tools for external AI agents (NOOD_0045). Transports: stdio (Claude Code, MAF `MCPStdioTool`) or `--transport streamable-http` (Azure AI Foundry / MAF `MCPStreamableHTTPTool`; `NOODLE_MCP_API_KEY` bearer/x-api-key auth, required beyond localhost) |

## 3. Package inventory (`noodle/`)

### 3.1 Execution pipeline

| Module | Contract |
|---|---|
| `steps/catch_all.py` | the ONE behave step definition (`@given/@when/@then` catch-all); delegates every sentence to the orchestrator. Registered via the workspace's generated `steps/z_catch_all.py` shim |
| `orchestrator/runner.py` | `execute_step()` — web path: resolve sentence → action fn → locator → Playwright call; owns retries/waits |
| `orchestrator/visual_runner.py` | dispatch for `@visual`-tagged steps → visual agent |
| `orchestrator/script_runner.py` | `run script/command/function` steps — external process & `path.py:fn` calls |
| `resolver/patterns.py` | ~250 regex patterns, the canonical step grammar (PATTERNS table; conditionals `run_if` must stay on top). Extensible per-workspace via agent patterns dir |
| `resolver/step_resolver.py` | `resolve(sentence)` → `(action, args)`; consults patterns, then POM keys, then fails |
| `resolver/visual_patterns.py` | pattern table for visual/desktop steps |
| `resolver/step_search_engine.py` | NOOD_0026 — lexical (optional LLM tie-break) nearest-step search over `docs/steps_dictionary.md` |
| `hooks.py` | behave lifecycle (`before_all` etc.): browser boot, .env/config load, allure wiring, parallel-worker results dirs |
| `preconditions.py` | `@precondition:NAME` tag → setup/teardown calls from `resources/preconditions.yaml` (the JDBC-fixture analog) |
| `app_lifecycle.py` | start/stop app-under-test processes (desktop + REST shared need) |
| `healing.py` | telemetry of locator self-repairs (what was healed, how) |
| `config.py` | workspace config: loads `noodle.yaml` (+ `.env`), exposes `tests_dir`, `browser`, `headless`, `env_file`, `LLM_PRESETS` |
| `secrets_akv.py` | Azure Key Vault secret loader (`{env:...}` backend option) |
| `log.py` | structured logging |

### 3.2 Device/target agents (`noodle/agents/`)

| Package | Contract |
|---|---|
| `web/` | Playwright: `actions.py` (click/fill/assert/…), `locator.py` (role → label → text → POM chain), `pom.py` (pom.yaml load + URL-scoped matching), `a11y.py` (accessibility snapshot), `screen.py`, `rest_client.py` (API steps) |
| `mobile/` | Appium analogs: `driver.py`, `actions.py`, `locator.py`, `screen.py` (platform tags per NOOD_0032) |
| `visual/` | desktop/visual: `screenshot.py`, `matcher.py` (OpenCV template match), `ocr.py`, `regions.py`, `baseline.py` (visual diff baselines), `window.py`, `desktop.py`, `vision_locate.py` (opt-in vision-LLM locate) |

### 3.3 Authoring agent (`noodle/repl/`)

| Module | Contract |
|---|---|
| `core.py` | NOOD_0045 — the callable agent API shared by REPL + MCP: `create_test`, `run_test`, `last_result`, `list_tests`, `validate_feature`, `write_feature`, `search_step`, `probe_page` (NOOD_0113 pre-authoring DOM probe → `agents/web/probe.py`), `inspect_locator` (NOOD_0115 locator-resolution debugger → `agents/web/inspect_locator.py`), `rca`, `build_report`, and persistent state (`artifacts/agent_state.json`, `resolve_target` = explicit → persisted → newest `.feature`) |
| `repl.py` | English → engine commands. Rule tier: exact grammars (`create test for X at URL`, `run …`, `summary`, scaffold-one-file, step search) + last-resort loose prose create (NOOD_0045). LLM tier: `_extract_plan()` → ordered create/scaffold/run/summary/open_report plan. Pronoun memory persists via `core` state |
| `generate.py` | rule-based generation: keyword-picked templates (login/search/checkbox/dropdown/generic) → `.feature` + skeleton POM + package resources (env yaml, secrets example); NOOD_0045 quoted-value slot filling (`extract_slots`/`fill_slots`) + loose prose parsing (`parse_free_request`); detection-based scaffolding of payloads/functions/preconditions; `generate_llm()` = model-written, vocabulary-constrained, validate+one-repair-pass |
| `prompts.py` | every model prompt in one place; `STEP_VOCABULARY` = curated canonical sentences shown to models |
| `validate.py` | dry-run `.feature` text against the pattern table (no browser); powers generation validation and `noodle validate --resolve` |
| `ground.py` | `NOODLE_GROUND=true` — probe the live page; POM entries only for labels the locator chain actually failed on |
| `reflect.py` | post-run self-repair: failed generated test → one model rewrite, kept only if fewer failures |
| `step_suggestion_engine.py` | when step-search misses: draft a new step (pattern + dictionary entry), write on user acceptance |

### 3.4 LLM layer (`noodle/llm/`)

`client.py` — `ask()` / `ask_vision()` via litellm; model from `NOODLE_MODEL`
(no default — unset means no LLM; recommended: `anthropic/claude-sonnet-5`,
local Ollama as restricted-network fallback; presets in `config.LLM_PRESETS`).
Selection rationale: [llm-setup.md](llm-setup.md).
No module imports a model client directly — everything routes here.

### 3.5 Reporting (`noodle/reporting/`)

| Module | Contract |
|---|---|
| `builder.py` | Allure 3 HTML build from `artifacts/allure-results/` |
| `summary.py` | plain-English last-run summary from allure JSON (no LLM); `summarize_llm()` optional narrative |
| `rca_report.py` | NOOD_0018 — per-failure root-cause markdown/HTML: heuristic classifier merged with `rca.py`'s agentic verdict; `propose_fixes()` = diff suggestions (never applied) |
| `rca.py` (top level) | agentic RCA: vision LLM classifies failure screenshots when `NOODLE_RCA` + vision model set |
| `allure_meta.py`, `annotate.py`, `junit.py`, `paths.py` | allure metadata, step annotation, JUnit export, canonical artifact paths |

### 3.6 Tooling

| Module | Contract |
|---|---|
| `recorder/` | browse-and-record: user drives the browser, Noodle emits `.feature` sentences; `sensitives.py` masks secrets |
| `lsp/server.py` | editor diagnostics + completions (see `vscode-extension/`) |

## 4. Workspace contract (the user's test repo)

Created by `noodle init`; the engine treats it as data:

```
<workspace>/
  noodle.yaml                  # tests_dir, browser, headless, env_file
  .env                         # NOODLE_* runtime toggles
  AGENTS.md / CLAUDE.md        # AI-agent instructions (NOOD_0086)
  noodle_tests/                # tests_dir (this repo's own is sample_feature_tests/)
    steps/z_catch_all.py       # generated shim importing the engine's catch-all
    environment.py             # generated shim importing noodle.hooks
    <type>/<app>/              # one package per app-under-test (feature-packages.md)
      features/*.feature
      resources/
        <app>_environments.yaml   # base URLs ({env:APP} resolution)
        <app>_secrets.env         # gitignored — credentials
        pageobjects/*_pom.yaml    # optional selector overrides, URL-scoped
        preconditions.yaml, payloads/, functions/, data/
      report/                  # this app's run output — allure-results, reports,
                               # screenshots, history (single-app runs, NOOD_0086)
  artifacts/                   # workspace-wide runs: allure-results, reports,
                               # screenshots, traces, logs, network
                               # + agent_state.json (agent memory), last_run.json (NOOD_0045)
  .noodle/last_run_root        # pointer to the last run's output root
```

Placeholder syntax inside features: `{env:KEY}` (secrets/env),
`{var:NAME}` (runtime-captured), `{pom:key}` (pin to a POM entry)
(NOOD_0033 unified syntax).

## 5. Repository layout (this repo)

| Path | Purpose |
|---|---|
| `noodle/` | the engine (spec above) |
| `sample_feature_tests/` | in-repo example workspace (web/mobile/desktop/api/terminal app packages; saucedemo, busterblock, …) |
| `unit_tests/` | pytest suite for the engine itself |
| `test-apps/busterblock/` | BusterBlock — local test web app (started in CI, NOOD_0042) |
| `docs/` | architecture, encyclopedia, steps dictionary, glossary, per-ticket design docs (NOOD_XXXX-*.md), roadmap |
| `vscode-extension/` | editor client for `noodle-lsp` |
| `scripts/` | dev/CI helper scripts |
| `azure-pipelines.yml`, `azure-pipelines-windows.yml` | CI (Linux + Windows), Allure publish (NOOD_0040) |
| `Dockerfile`, `Makefile`, `pyproject.toml` (uv/hatch), `behave.ini` | packaging & tooling |
| `environments.yaml`, `secrets.env(.example)` | repo-root workspace config for the in-repo `sample_feature_tests/` |
| `archives/`, `artifacts/`, `reports/` | run outputs for the in-repo workspace |

## 6. Configuration surface (env vars)

| Var | Effect |
|---|---|
| `NOODLE_MODEL` | enables every LLM feature; litellm model id |
| `NOODLE_LLM_URL` | endpoint override for Ollama / Foundry Local / self-hosted OpenAI-compatible servers (no default — cloud providers resolve their own endpoint) |
| `NOODLE_HEADLESS` / `NOODLE_BROWSER` | run-time browser toggles (CLI flags win) |
| `NOODLE_RETRIES` | failed-scenario re-runs (default 1) |
| `NOODLE_PARALLEL_PROCESSES` | behavex worker count (web, headless) |
| `NOODLE_LOG_LEVEL` | logging |
| `NOODLE_GROUND` | generation-time live-page POM grounding |
| `NOODLE_RCA` | agentic (vision) RCA on failures |

## 7. Execution lifecycle (one step, end to end)

```
.feature sentence
  → behave parses → steps/z_catch_all.py (workspace shim) → steps/catch_all.py
  → orchestrator/runner.execute_step()
  → resolver/step_resolver.resolve(): patterns.py regex → action + args
  → agents/web/actions.<action>()
  → agents/web/locator.find(): role → label → text → pom.yaml → (opt-in LLM) → fail loudly + screenshot
  → Playwright acts
  → hooks + reporting: allure result JSON, screenshots/traces on failure
  → noodle summary / rca-report / report generate consume artifacts/
```

## 8. Quality gates

- `unit_tests/` (pytest) — engine behavior; run in CI on Linux + Windows.
- `noodle validate --resolve` — every feature step dry-run against the
  pattern table; POM scope linting.
- Allure + RCA reports are mandatory outputs after any run
  (NOOD_0008 playbook rule).

## 9. Known gaps / forward work

All four MCP-readiness gaps are closed (see [mcp-guide.md](mcp-guide.md)):

- **MCP / AI-SDLC integration** — `noodle/mcp/server.py` (`noodle-mcp`
  entrypoint, stdio + streamable-http, `[mcp]` extra; setup/usage/MAF guide:
  [mcp-guide.md](mcp-guide.md)): 22 tools (`generate_test`, `run_test`,
  `get_last_result`, `run_and_report`, `preflight`, `author_test`,
  `list_tests`, `validate_feature`, `write_feature`, `search_step`,
  `probe_page`, `probe_app`, `inspect_locator`, `get_rca`, `log_diagnostic`,
  `serve_report`, `stop_report_server`, `list_reports`, `init_workspace`,
  `cost_estimate`, `server_info`, `read_docs`) + the `noodle://vocabulary`
  resource.
- **Free-prose English without an LLM** — last-resort rule-tier fallback:
  `generate.parse_free_request` (create-verb + "test" + URL token) with
  `extract_slots`/`fill_slots` quoted-value filling. Heuristic by design;
  arbitrary phrasing still belongs to `--llm` or the MCP caller.
- **Return values / `--json`** — `noodle/repl/core.py` is the callable API
  (REPL + MCP share it); `--json` on `summary`, `list`,
  `validate --resolve`; `noodle run` writes `last_run.json` into its output root (the app's `report/` or `artifacts/`).
- **Session memory** — persisted to `artifacts/agent_state.json`
  (`core.load_state`/`save_state`; durable keys only), shared by REPL and
  MCP, so "run the test" resolves across processes.

See [design-history.md](design-history.md) for the broader backlog and rationale.

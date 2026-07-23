# Noodle MCP — Setup, Usage, and MAF / Azure AI Foundry Integration
<!-- Branch: NOOD_0065 -->

> **For:** developers and AI-SDLC integrators wiring an external agent to Noodle over MCP.

`noodle-mcp` (`noodle/mcp/server.py`) exposes Noodle's agent core as
[Model Context Protocol](https://modelcontextprotocol.io) tools, so an
**external** AI agent — Claude Code, GitHub Copilot, a MAF agent, an Azure AI
Foundry agent, any MCP host — can generate, run, and inspect tests. Division
of labour:

- **The calling agent brings the language skills.** It parses the human's
  intent ("test search on example.com for office chair") and fills the
  tool parameters.
- **Noodle stays deterministic.** Template/slot generation, the pattern-table
  resolver, Playwright execution, Allure/RCA reporting — no LLM required
  anywhere inside Noodle for this flow (Noodle's own `--llm` tier remains an
  optional upgrade via `use_llm=true`).

State is shared with `noodle repl`: both persist "the last test" to
`artifacts/agent_state.json`, so "run the test" means the same file whether
asked over MCP, in the REPL, or in a later session.

---

## Contents

1. [Setup](#1-setup)
2. [Local quickstart — any coding agent, $0](#2-local-quickstart--any-coding-agent-0)
3. [Connecting a host](#3-connecting-a-host)
4. [The tools — what to call, when](#4-the-tools--what-to-call-when)
5. [The authoring loops](#5-the-authoring-loops)
6. [Why it's shaped this way](#6-why-its-shaped-this-way)
7. [MAF integration (local, stdio)](#7-maf-integration-local-stdio)
8. [MAF / Azure AI Foundry integration (remote, Streamable HTTP)](#8-maf--azure-ai-foundry-integration-remote-streamable-http)
9. [Security model](#9-security-model)
10. [Operational gotchas (found the hard way)](#10-operational-gotchas-found-the-hard-way)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Setup

```bash
# in the noodle repo (or wherever noodle is installed from)
pip install -e ".[mcp]"          # or: uv sync --extra mcp
```

That adds the `mcp` SDK and the `noodle-mcp` entrypoint. Verify:

```bash
noodle-mcp --help
```

You also need a **workspace** — the folder holding `noodle.yaml`, `noodle_tests/`,
and `.env`. Any existing workspace works; to make a fresh one:

```bash
noodle init ~/my-tests
```

Tool calls default to the workspace given at server start (`--workspace`,
default `.`); every tool also takes an optional per-call `workspace`
override (NOOD_0057, gated over HTTP — see
[§4](#4-the-tools--what-to-call-when)), so one server can drive several
test repos.

Optional extras, only if the relevant tools will be used:

- `use_llm=true` on `generate_test` needs a configured model —
  `noodle init --llm ollama|claude|gemini`, see the README's
  [LLM augmentation](manual.md#llm-augmentation-optional).
- Browsers for `run_test`: `playwright install chromium`.

## 2. Local quickstart — any coding agent, $0

The full checklist for standing up `noodle-mcp` on a laptop with no API
keys, no cloud, no paid service — using whatever coding agent is already
installed, plus a free local LLM fallback (Ollama) for Noodle's own
`--llm` mode if you want it.

```bash
# macOS
git clone https://github.com/gheeno/noodle.git && cd noodle
uv pip install -e ".[all]"      # includes the mcp extra
source .venv/bin/activate       # every new terminal, or use `uv tool install --editable` for a global PATH instead
playwright install chromium
noodle-mcp --help               # sanity check the entrypoint exists
```

```powershell
# Windows 11 (PowerShell)
git clone https://github.com/gheeno/noodle.git; cd noodle
uv pip install -e ".[all]"
.venv\Scripts\Activate.ps1      # every new terminal, or use `uv tool install --editable` for a global PATH instead
playwright install chromium
noodle-mcp --help
```

Scaffold a workspace — separate from the Noodle repo, put it wherever you
keep your own tests (`noodle init` is not interactive; the path is a plain
CLI argument, default `.` if omitted):

```bash
noodle init ~/noodle-mcp-test
```

**Optional — free local LLM fallback (Ollama).** The recommended default is a
hosted model (`NOODLE_MODEL=anthropic/claude-sonnet-5` + `ANTHROPIC_API_KEY`
in `secrets.env`); use this local option on restricted networks or for a $0
setup. Only needed for Noodle's
own `use_llm=true` tier (free-form step generation when a request doesn't
match a known template). Skip this if rule-based template matching is
enough.

```bash
# macOS
brew install ollama
ollama serve                     # own terminal, keep running
ollama pull llama3.1:8b          # any local model; one-time download
```

```powershell
# Windows 11 (PowerShell)
winget install Ollama.Ollama
ollama serve
ollama pull llama3.1:8b
```

Then in the workspace's `.env` (`~/noodle-mcp-test/.env`):

```
NOODLE_MODEL=ollama/llama3.1:8b
```

Register the server with your agent host (§3), reconnect, and drive it in
plain English:

> "Use the noodle MCP tools to create a test for youtube search, run it,
> and show me the report."

The agent calls `generate_test` → `run_test` → reads back the Allure/RCA
report. Noodle stays deterministic (templates + pattern-table resolver);
the calling agent only supplies language understanding — same division of
labour whether the caller is Claude Code, Copilot, or a MAF/Azure AI
Foundry agent.

**Gotchas:**

- `noodle repl --llm claude|gemini` (Noodle's *own* built-in LLM tier)
  needs a **separate paid API key** and is not what "use Claude/Copilot as
  the agent" means here — that's the MCP path above, billed through
  whatever plan already runs your coding agent, not per-token.
- Azure DevOps pipelines don't need any of the `source .venv/bin/activate`
  steps above — CI installs straight into the job's system Python
  (`pip install -e ".[all]"`, see `azure-pipelines.yml`), no venv, no
  activation, ephemeral container per run.

## 3. Connecting a host

**Skip this whole section if your workspace came from `noodle init`
(NOOD_0095/0096)** — it already wrote `.mcp.json`, `.vscode/mcp.json`, and
`.copilot/mcp-config.json` for you, all pointed at `noodle-mcp` with no
path to fill in (details further down this section, under "One-command
client setup"). Launch your host from inside the workspace directory and
confirm it shows connected. The manual setup below is for wiring a
workspace `noodle init` didn't scaffold, or pinning an absolute
`noodle-mcp` path (not on `PATH`, or a per-venv install rather than a
global `uv tool install`).

Two transports; pick by where the host runs.

| Transport | When | Endpoint |
|---|---|---|
| `stdio` (default) | host and Noodle share a machine — Claude Code, Copilot, local MAF agent | host spawns the process |
| `streamable-http` | host is remote — Azure AI Foundry Agent Service, MAF `MCPStreamableHTTPTool`, containerised setups | `http://<host>:<port>/mcp` |

**stdio** — the host's MCP config spawns the server. Generic shape:

```json
{ "mcpServers": { "noodle": {
    "command": "noodle-mcp",
    "args": ["--workspace", "/path/to/my-tests"]
} } }
```

**Claude Code:**

```bash
# macOS — .venv scripts live in .venv/bin/
claude mcp add noodle -- /path/to/noodle/.venv/bin/noodle-mcp --workspace ~/noodle-mcp-test
```

```powershell
# Windows 11 (PowerShell) — .venv scripts live in .venv\Scripts\, with a .exe suffix
claude mcp add noodle -- C:\path\to\noodle\.venv\Scripts\noodle-mcp.exe --workspace C:\Users\you\noodle-mcp-test
```

Writes to `.claude.json` for the project. Confirm with `claude mcp list`.

**GitHub Copilot CLI (terminal, not VS Code)** — the standalone `copilot`
agent reads MCP servers from `.copilot/mcp-config.json` in the folder it's
launched from. Create it in the **workspace** (`~/noodle-mcp-test`), not
the engine checkout:

```json
// macOS — ~/noodle-mcp-test/.copilot/mcp-config.json
{
  "mcpServers": {
    "noodle": {
      "command": "/path/to/noodle/.venv/bin/noodle-mcp",
      "args": ["--workspace", "/path/to/noodle-mcp-test"]
    }
  }
}
```

```json
// Windows 11 — %USERPROFILE%\noodle-mcp-test\.copilot\mcp-config.json
{
  "mcpServers": {
    "noodle": {
      "command": "C:\\path\\to\\noodle\\.venv\\Scripts\\noodle-mcp.exe",
      "args": ["--workspace", "C:\\Users\\you\\noodle-mcp-test"]
    }
  }
}
```

Note the key is `mcpServers` (matching the generic stdio shape above),
not the `servers` key VS Code's `.vscode/mcp.json` uses below — the two
hosts use different config file names *and* different top-level keys.
Launch `copilot` from inside the workspace directory so it finds the
config, then confirm `noodle` shows connected.

**GitHub Copilot (VS Code, agent mode)** — create `.vscode/mcp.json` in
the workspace you have open:

```json
// macOS
{
  "servers": {
    "noodle": {
      "type": "stdio",
      "command": "/path/to/noodle/.venv/bin/noodle-mcp",
      "args": ["--workspace", "/path/to/noodle-mcp-test"]
    }
  }
}
```

```json
// Windows 11
{
  "servers": {
    "noodle": {
      "type": "stdio",
      "command": "C:\\path\\to\\noodle\\.venv\\Scripts\\noodle-mcp.exe",
      "args": ["--workspace", "C:\\Users\\you\\noodle-mcp-test"]
    }
  }
}
```

Use absolute paths for both `command` and `--workspace` — no `~`
expansion, no relying on the venv being active. Copilot Chat's
`MCP: Add Server` command (Command Palette) can also write this file
interactively; the shape above is what it produces. Confirm under Copilot
Chat's tools/MCP servers panel that `noodle` shows connected and its tools
(`generate_test`, `run_test`, `run_and_report`, `get_rca`, `write_feature`,
…) are listed.

### Starting, restarting, and killing the server

**stdio (Claude Code, Copilot CLI/VS Code, a local MAF agent)** — you never
run `noodle-mcp` yourself; the host spawns it as a child process the moment
it needs a tool, and owns its lifecycle. Running `noodle-mcp` bare in a
terminal for this transport just exits immediately (stdin is closed, not a
real client pipe) — that's expected, not a bug.

MCP tool lists — and any code change to Noodle itself (a `git pull`,
`pip install -e` re-run, a hot-fix) — only take effect on the **next**
process the host spawns. There is no hot reload: a long-running server
process keeps executing whatever was on disk when it started, even after
the files underneath it change. **After any Noodle code change, or after
editing the server config, restart:**

- Claude Code: run `/mcp` to reconnect, or restart `claude` in that repo.
- Copilot CLI: restart `copilot` in the workspace directory.
- Copilot (VS Code): reload the VS Code window, or use the MCP servers
  panel's restart action.
- Any other MCP host: use that host's own "reconnect"/"restart server"
  action — the host owns the child process, so there's no host-agnostic
  restart command. If a host has no such action, closing and reopening it
  respawns every configured stdio server.

**Killing it directly** — mostly for diagnosing a stuck or stale server, or
tearing down a stray process from an earlier session:

```bash
pgrep -fl noodle-mcp                 # list running noodle-mcp processes + their --workspace
kill <pid>                           # stop one
pkill -f noodle-mcp                  # stop every noodle-mcp process for this user
```

Killing a **host-managed stdio** server disconnects that host's noodle
tools immediately, and most hosts do **not** auto-respawn it mid-session —
you (or the agent) have to trigger that host's reconnect action afterward
(above) before calling a noodle tool again. Don't kill one out from under a
session you still need unless you're prepared to reconnect it right after.

**streamable-http** is the one transport you do start/stop yourself, since
no host spawns it — it's a long-running foreground process (command below).
`Ctrl-C` stops it in the foreground; from another terminal,
`pkill -f "noodle-mcp.*streamable-http"`. Backgrounding it for a real
deployment is an ordinary process-supervision problem — `nohup ... &` for a
quick test, `systemd`/a container/`supervisord` for anything longer-lived —
Noodle doesn't prescribe one. Whatever you use, **restarting the unit after
every deploy is required**, for the same no-hot-reload reason as stdio
above. `server_info` (or the identity line the server logs to stderr at
startup) tells you whether a running process predates your last deploy —
see [§10](#10-operational-gotchas-found-the-hard-way).

```bash
export NOODLE_MCP_API_KEY="$(openssl rand -hex 24)"   # any strong secret
noodle-mcp --workspace /path/to/my-tests \
           --transport streamable-http --host 0.0.0.0 --port 8080
```

Callers authenticate every request with either header:

```
Authorization: Bearer <key>
x-api-key: <key>
```

No key + a non-localhost bind = the server refuses to start (see
[§9](#9-security-model)). Quick connectivity check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/mcp \
  -H "Authorization: Bearer $NOODLE_MCP_API_KEY" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
# 200 = up + authed; 401 = wrong/missing key
```

**One-command client setup (NOOD_0089, extended NOOD_0096):** `noodle init
mcp` (alias `noodle init-mcp`) writes/merges the noodle server entry into
`.mcp.json` (Claude Code), `.vscode/mcp.json` (VS Code Copilot agent mode),
and `.copilot/mcp-config.json` (standalone Copilot CLI) for the workspace —
existing JSON is merged, never clobbered. `noodle init` already calls this
automatically (NOOD_0095), so a fresh workspace is agent-ready for all
three hosts without any extra step; run `init mcp`/`init-mcp` yourself only
to refresh a drifted entry (`--force`) or wire MCP into a workspace that
wasn't scaffolded with `noodle init`. In an Azure DevOps pipeline
(`TF_BUILD`) the files are still written so the team can commit them, but
pipelines themselves call the `noodle` CLI directly — there is no
interactive agent to consume MCP config in CI.

## 4. The tools — what to call, when

| Tool | Parameters | Returns | Use it for |
|---|---|---|---|
| `generate_test` | `url`, `description`, `use_llm?`, `overwrite?`, `workspace?` | `ok`, `feature`, `pom`, `runnable`, `output` | "create a test for X" — templates + quoted-value slot filling; `runnable=false` means `<placeholders>` remain |
| `run_test` | `target?`, `tag?`, `workspace?`, `headless?`, `browser?`, `retries?`, `parallel?`, `parallel_scheme?` | run result (below) | "run the test" / "run login" / "run the smoke tag". Omit `target` → last created/run feature, else newest `.feature`; `workspace` may point at one app package (`<ws>/noodle_tests/app1`) — with no `target` that whole app runs, its output landing in the app's own `report/` (NOOD_0086). `headless` overrides the workspace's `.env` default for this run only — pass `true` whenever *you* are the one running it (NOOD_0059), not only when the host lacks a display: a fresh workspace's `.env` defaults to headed for a human watching locally. `browser` (chromium\|firefox\|webkit\|safari\|edge), `retries` (extra re-runs per failed scenario — pass `0` while developing; the default (1) doubles wall-clock time on every failed fix→rerun cycle) and `parallel` (N feature files at once, web only, use `headless`; `parallel_scheme` `feature`/`scenario`) mirror the CLI flags, defaulting to workspace config (NOOD_0084) |
| `get_last_result` | `workspace?` | `passed`, `failed`, `failures[]` (feature, scenario, step, message), `seconds` | "what was the last run result" |
| `run_and_report` | `target?`, `tag?`, `workspace?`, `headless?`, `browser?`, `retries?`, `parallel?`, `parallel_scheme?`, `serve_reports?` | run result + `report` (index.html path) + `rca_html`/`rca_md` paths + `rca_compact` on red (+ `served.urls` when `serve_reports=true`) | "run the test and generate the result" in one call; run params as in `run_test`. NOOD_0128 — preflights secrets first (returns `missing_secret_keys` with NO browser launch if a `{env:KEY}` is missing/placeholder), folds the compact RCA in on red, and optionally serves the reports — so no separate `preflight`/`get_rca`/`serve_report` call is needed on the normal path |
| `preflight` | `target?`, `workspace?` | `ok`, `missing_secret_keys[]`, `errors[]`, `warnings[]` | NOOD_0128 — check a test is runnable BEFORE a browser: every `{env:KEY}` it references resolves to a real value (not missing/CHANGE_ME/empty), and no redundant post-navigation waits. Tells the user which secret keys to populate in the gitignored secrets.env |
| `author_test` | `app_name`, `base_url`, `feature_path`, `feature_content`, `pom_content?`, `environment_values?`, `required_secret_keys?`, `secret_values?`, `overwrite?`, `workspace?` | `ok`, `app`, `feature`/`pom`/`environments`/`secrets` paths, `created_secret_keys[]`, `missing_secret_keys[]`, `unmatched[]`, `warnings[]`, `ready`, `blocking[]` | NOOD_0128 — create a whole test package in ONE transaction (app package + environments.yaml + POM + feature + missing secret placeholders), validated, with rollback on failure. Replaces copy-sample_app → rename → edit×4 → validate. NOOD_0130 — `secret_values` (prompt-supplied `{KEY: value}`) is written WRITE-ONLY into the gitignored `<app>_secrets.env` and never returned; any key still unset stays in `missing_secret_keys` and blocks `ready` |
| `list_tests` | `workspace?`, `query?` | `{tests: [{path, feature, tags[], scenario_count}], note}` | "what tests do we have". NOOD_0162 — index first: scenario NAMES are the bulk of this payload, so they ship only for a `query` (substring over path / feature / scenario / tag), and matching features then carry `scenarios[]` |
| `validate_feature` | `content`, `workspace?` | `error`, `steps[{step, matched}]`, `unmatched[]` | pre-flight Gherkin before writing it |
| `write_feature` | `path`, `content`, `overwrite?`, `workspace?` | `ok`, `feature`, `unmatched[]` | store caller-authored Gherkin (validated; path locked under the tests dir) |
| `search_step` | `query`, `workspace?` | `found`, `step`, `confidence`, `reason`, `candidates[{step, score}]` | "is there a step that clears the cart?" — `found` is true only on a high-confidence match (NOOD_0058); a low-confidence best guess still appears in `step`/`candidates` |
| `probe_page` | `url` (space/comma-separate several) | `pages[{url, title, controls[{kind, name, selector, visible, needs_pom, step}], pom_yaml, headings[], next_pages[]}]`, `errors[]` | NOOD_0113 — proactive DOM probe BEFORE authoring against an unfamiliar/SPA page: every actionable control (hidden trigger zones included) with a ready selector, paste-ready POM YAML for the ones generic steps can't name, a vocabulary-shaped step each, exact heading texts for assertions, and same-origin next-page candidates. Opens a real headless browser (one for all URLs) but runs nothing |
| `inspect_locator` | `url`, `text` | `candidates[{source, count, matches[{tag, text, visible}]}]`, `resolved{tag, text, visible, healed[]}`, `error?` | NOOD_0115 — debug WHY a locator phrase does/doesn't resolve: runs `find()`'s exact resolution machinery headless and labels every candidate by source (visible text node / image alt / aria-label / title / POM key / DOM scan) plus what `find()` actually picks and any self-heal tier used. Call when a step times out on an element that's clearly on the page, or grabs the wrong one |
| `get_rca` | `workspace?` | markdown | per-failure root cause after a red run |
| `serve_report` | `workspace?`, `report_dir?`, `port?` (default 0 = free port) | `ok`, `host`, `port`, `pid`, `urls[]`, `reused?` | NOOD_0082 — host the last run's Allure report + rca.html on localhost, non-blocking; rebuilds missing reports from allure-results first. Hand the `urls` to the user. NOOD_0161: a detached child whose URLs survive this MCP server restarting, and a live server for the same root is reused (`reused: true`) so the URL doesn't change every run |
| `stop_report_server` | `workspace?` | `stopped_ports[]`, `detached` | tear down every report server this workspace hosts, including the detached children that outlive this MCP process (NOOD_0161). Only once the user is done with the links — stopping it early is what makes them dead |
| `list_reports` | `workspace?` | `live` (path, allure/rca present, generated_at), `archives[{stamp, path, size_mb}]` | NOOD_0082 — what can be (re-)hosted; extract an archive and pass its reports/ dir as `report_dir` to serve an older run |
| `init_workspace` | `path`, `llm?`, `model?` | `ok`, `workspace`, `output` | NOOD_0084 — scaffold a fresh workspace (noodle.yaml, .env, AGENTS.md agent instructions, noodle_tests/sample_app/ template with features/resources/report, engine glue) without a shell; never overwrites existing files. `llm`/`model` persist NOODLE_MODEL into the new `.env`. No `workspace` param — the dir doesn't exist yet; over streamable-http `path` must fall under a `--workspace-root` |
| `cost_estimate` | `target`, `model?`, `workspace?` | `ok`, `target`, `model`, `input_tokens`, `usd_input_floor` | NOOD_0084 — pre-flight token/$ estimate for a prompt or `.feature` file before an LLM run (input-cost floor; output tokens unknowable pre-run). The last run's *actual* spend is `get_last_result`'s `llm_cost` |
| `server_info` | — | `noodle_version`, `started_at`, `pid`, `workspace` | "is this server running the code I think it is" — a `started_at` older than your last deploy means restart it (NOOD_0057) |
| `read_docs` | `name?`, `query?` | no args: `docs[{name, summary}]`; `name`: `content`; `query`: `hits[{doc, line, text}]` | NOOD_0089 — token-lean framework lookup: list the docs, fetch one by name (`agent-playbook`), or grep a fact (`query="NOODLE_FIND_TIMEOUT"`) instead of guessing or stuffing docs into your prompt. Needs a repo checkout / editable install (wheels don't ship docs/) |

`run_test` / `run_and_report` return: `ok`, `exit_code`, `target`, the
structured counts/failures (same shape as `get_last_result`), and the tail
of the engine output.

**`workspace?` (NOOD_0057)** — every tool accepts an optional `workspace`
path that overrides the server's `--workspace` for that one call, so a
single server can drive several test repos. Over **stdio** any path is
accepted (the spawning host is already fully trusted, §9). Over
**streamable-http** overrides are refused unless they fall under a
`--workspace-root` directory passed at startup (repeatable flag; without
it, remote callers are locked to the startup workspace):

```bash
noodle-mcp --workspace /workspaces/team-a \
           --workspace-root /workspaces \
           --transport streamable-http --host 0.0.0.0 --port 8080
```

**Resource:** `noodle://vocabulary` — the canonical step sentences the
deterministic resolver understands. A calling agent that loads this resource
can author valid Gherkin directly (change only quoted values, field names,
URLs).

### Every payload is bounded (NOOD_0164)

Tool returns pass a shared **payload budget** — 8 KB serialized, the same one
`noodle <cmd> --json` prints through — because a payload a host can't inline
gets spilled to a temp file, and the agent then pays inferences to `jq` back
what the tool already returned. Three sessions in a row died that way.

A trimmed payload says so in `payload_note`: what was cut and how to get the
rest (`query=` for `list_tests`, `compact=False` for the probes, the doc file
itself for `read_docs`). Small keys — `ready`, `blocking`, `author_ready`,
verdicts, paths, URLs — are never the largest value, so they always survive
whole. Raise it for a host that inlines more with
`NOODLE_PAYLOAD_BUDGET_BYTES=16000`.

## 5. The authoring loops

**Template loop** (simplest — Noodle writes the Gherkin):

```
generate_test(url="example.com",
              description='search test: searches for "office chair" and
                           asserts the results page contains "office chair"')
→ runnable=true → run_test() → get_last_result()
```

**Author loop** (most control — the calling agent writes the Gherkin,
fully LLM-free on Noodle's side):

```
read resource noodle://vocabulary
→ compose .feature text using only those sentence shapes
→ validate_feature(content)            # fix any unmatched steps
→ write_feature("noodle_tests/web/<app>/features/<name>.feature", content)
→ run_test()                           # targets it automatically
→ get_last_result() / get_rca()
```

**Full-LLM loop** (ambiguous steps — the calling agent drives, the
engine-side model fills run-time gaps):

Some steps are too ambiguous to phrase in the fixed vocabulary. Instead of
forcing them, the calling agent (Claude Code, Copilot CLI, …) leaves them
natural-language and lets the engine's own LLM tier resolve them at run
time — then harvests what the model did so each gap becomes a permanent
pattern:

```
workspace .env: NOODLE_LLM_MODE=auto   # or `full` to skip patterns entirely
                NOODLE_MODEL=anthropic/claude-sonnet-5   # local Ollama also fine
→ write_feature(...) with the ambiguous steps left natural
→ run_and_report() → get_last_result() / get_rca()
→ read <workspace>/docs/steps_dictionary_suggestions.md
   (every LLM-resolved step: text + resolved action JSON, NOOD_0049)
→ per recurring entry: `noodle step-search "<step text>" --accept`
   → stages it into <workspace>/docs/agent_patterns.yaml — loaded by every
     future run, never hits the LLM again
```

Two distinct AIs in this loop — keep them straight: the **calling agent**
(the MCP host you're chatting with) authors, runs, and harvests; the
**engine-side `NOODLE_MODEL`** is only invoked mid-run by the resolver.
There is no channel for the engine to call back into the MCP host, so
`NOODLE_MODEL` must be a real provider (Ollama/cloud) — the host agent
can't serve as the run-time model. The suggestions ledger doubles as the
promotion plan a Noodle developer can turn into a `patterns.py` PR later.

## 6. Why it's shaped this way

Noodle's deterministic core (catch-all step → ~190-pattern resolver →
web/mobile/visual agents → Playwright) is local, LLM-optional by design
(`architecture.md` §1) — the right substrate for agentic use: an external
LLM writes sentences, the engine executes them with no model in the
runtime loop. The gap MCP closes isn't in that core; it's that nothing
exposed those functions to an external caller in a machine-readable way.

The alternative — teaching Noodle's own rule-based parser to understand
arbitrary free-form English ("generate a search test that searches for
'office chair' and asserts the results page contains it") — was tried and
rejected. A prompt like that breaks the rule tier's fixed grammar (no slot
extraction for the search term or assertion text), and closing that gap
with more regexes is a losing game: unbounded phrasing, entity extraction,
coreference. **The fix is structural, not linguistic:** in an AI-SDLC
topology the caller is already an LLM agent, so MCP tool schemas move the
language problem to the caller — Noodle exposes
`generate_test(url, description, ...)` and the calling agent fills the
slots. Noodle's deterministic core then does what it's already good at.
Noodle's own `--llm` tier (`use_llm=true`) remains available as an optional
upgrade for standalone, non-agentic use.

Session state (`artifacts/agent_state.json`) makes "run the test" resolve
without a target across processes, sessions, and transports: explicit
target → last feature from state → most recently modified `.feature` under
`tests_dir` → error listing candidates.

## 7. MAF integration (local, stdio)

MAF (`agent-framework`, Python shown; .NET equivalent exists) attaches MCP
servers as tools. Local agent + local Noodle → `MCPStdioTool`:

```python
import asyncio
from agent_framework import MCPStdioTool
from agent_framework.azure import AzureOpenAIChatClient  # or any chat client

async def main():
    noodle = MCPStdioTool(
        name="noodle",
        command="noodle-mcp",
        args=["--workspace", "/path/to/my-tests"],
    )
    async with noodle:
        agent = AzureOpenAIChatClient().create_agent(
            name="qa-agent",
            instructions=(
                "You drive the Noodle test framework via its tools. "
                "For new tests prefer generate_test; check runnable before "
                "run_test; after a failed run, call get_rca and summarise."
            ),
            tools=noodle,
        )
        reply = await agent.run(
            'Generate a search test for example.com that searches for '
            '"office chair" and asserts the results page shows it, then run '
            "it and tell me the result."
        )
        print(reply)

asyncio.run(main())
```

The MAF agent's model does the intent parsing; every `generate_test` /
`run_test` / `get_last_result` call it makes executes deterministically in
the workspace.

## 8. MAF / Azure AI Foundry integration (remote, Streamable HTTP)

Hosted agents can't spawn a local process, so they connect to a running
`noodle-mcp` over Streamable HTTP.

**Step 1 — host the server** where the browsers can run (a VM, Azure
Container Apps, AKS; the repo `Dockerfile` is a starting point — headless
web/API suites only, the desktop/visual agents need a real display):

```bash
export NOODLE_MCP_API_KEY=<secret from your key vault>
noodle-mcp --workspace /workspaces/my-tests \
           --transport streamable-http --host 0.0.0.0 --port 8080
```

**Step 2a — MAF with `MCPStreamableHTTPTool`:**

```python
from agent_framework import MCPStreamableHTTPTool

noodle = MCPStreamableHTTPTool(
    name="noodle",
    url="https://test-runner.internal:8080/mcp",
    headers={"Authorization": f"Bearer {NOODLE_MCP_API_KEY}"},
)
# use exactly like the stdio example: async with noodle: ... tools=noodle
```

**Step 2b — Azure AI Foundry Agent Service (hosted MCP tool):** add an MCP
tool to the agent pointing at the server URL, passing the key as a custom
header. Via the SDK:

```python
from azure.ai.agents.models import McpTool

mcp_tool = McpTool(
    server_label="noodle",
    server_url="https://test-runner.example.com/mcp",
)
mcp_tool.update_headers("Authorization", f"Bearer {NOODLE_MCP_API_KEY}")

agent = project_client.agents.create_agent(
    model="gpt-4.1",
    name="qa-agent",
    instructions="Drive the Noodle test framework via its MCP tools...",
    tools=mcp_tool.definitions,
)
```

(Foundry's portal flow — Agents → Tools → Add → MCP — takes the same URL +
header. Foundry requires the endpoint to be reachable from Azure and
HTTPS-fronted; put the server behind your ingress/App Gateway.)

**Step 3 — approvals.** Foundry defaults to requiring approval for MCP tool
calls; `run_test`/`run_and_report` execute real browsers, so keeping
approvals on for those and auto-approving the read-only tools
(`list_tests`, `get_last_result`, `search_step`, `validate_feature`,
`get_rca`, `probe_page` — it opens a browser but only reads the page) is a
sensible split.

## 9. Security model

- **Auth is mandatory off-localhost.** `--transport streamable-http` with a
  non-localhost `--host` and no `NOODLE_MCP_API_KEY` exits with an error at
  startup. An open MCP server would execute browser sessions and write files
  for anyone who finds it.
- Single shared key, constant-time compare, accepted as
  `Authorization: Bearer` or `x-api-key`. Rotate it like any secret; put it
  in Key Vault, not in agent instructions. For per-caller identity, front
  the server with your gateway's OAuth — the SDK's auth provider is the
  upgrade path.
- **`write_feature` is path-locked** to the workspace's tests dir and
  `.feature` files only; content must parse as Gherkin before it lands.
- The engine executes wherever `noodle-mcp` runs — treat the host like a CI
  runner: its network reach is what tests (and a compromised caller) can
  touch. Scope the workspace's `{env:...}` secrets accordingly.
- stdio transport inherits the host's process sandbox; no network surface.

## 10. Operational gotchas (found the hard way)

Two gaps surfaced running this against real multi-workspace/team usage;
both were **fixed in NOOD_0057**. What remains is the discipline around
them:

**1. A running server never hot-reloads.** `noodle-mcp` is a plain
long-lived process: a server started before a `git pull` /
`pip install -e` / hot-fix keeps executing the old in-memory code
indefinitely, and a tool call can fail (or silently behave differently)
with nothing pointing at "restart me". Since NOOD_0057 the server tells
you who it is — the `server_info` tool returns
`{noodle_version, started_at, pid, workspace}`, and the same line is
logged to stderr at startup (visible in `claude mcp list` / Copilot's MCP
panel). A `started_at` older than your last deploy means the process is
stale: restart it (see [Starting, restarting, and killing the
server](#starting-restarting-and-killing-the-server)). The identity check
is a diagnostic, not a substitute for restarting as a deploy step — actual
hot-reloading (re-importing `noodle.*` per call) was considered and
rejected as disproportionate: it adds real complexity (import caching,
partially-reloaded module state) to fix what a restart already fixes for
free.

**2. One workspace per server — now per call instead.** `--workspace` used
to be the only workspace a server could ever touch; N test repos meant N
processes and N host config blocks. Since NOOD_0057 every tool accepts an
optional `workspace` parameter overriding the startup default for that one
call (see the [tool table](#4-the-tools--what-to-call-when)). Trust
boundary: **stdio** accepts any path — the spawning host is already fully
trusted (§9), and a caller that can choose *which* feature to run can
reasonably choose *which workspace*. **streamable-http** callers are
remote and only API-key-authenticated, so overrides there must fall under
a `--workspace-root` directory passed at startup — without the flag,
remote callers stay locked to the startup workspace, and a compromised key
can't aim the engine at an arbitrary filesystem path. One sharp edge
remains: the first workspace to define an env var (via its `.env`) keeps
it for the process lifetime — existing process env always wins — so
prefix env keys per app (the same rule as everywhere else in Noodle) and
don't rely on two workspaces defining the *same* key differently.

**3. The workspace's AGENTS.md may never get read (NOOD_0096).** Driving
via CLI usually means the agent is `cd`'d into the workspace, so its host
auto-loads the project's `CLAUDE.md`/`AGENTS.md` context at session start.
Driving via MCP breaks that: the host is commonly launched from the engine
checkout (or wherever) with `--workspace` merely pointing the *server* at
the test folder — the calling agent's own working directory never changes,
so the workspace's `AGENTS.md` (step-writing rules, popup handling, the
dev-fix-loop discipline) is invisible unless the agent explicitly reads it.
The server's `instructions` field (surfaced by MCP-compliant hosts at
connect time) now carries the two guidelines that matter most —
`headless=True` and `retries=0` on `run_test`/`run_and_report` while
developing — so those land even if `AGENTS.md` never gets read, but full
conventions still need `read_docs('agent-playbook')` or the workspace's
`AGENTS.md` itself. If a test takes far longer to develop over MCP than the
same prompt did over a CLI-driven session, check whether the agent ever
saw `AGENTS.md` before assuming something else is wrong.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `noodle-mcp: command not found` | `[mcp]` extra not installed — `pip install -e ".[mcp]"` / `uv sync --all-extras` |
| Server exits: "set NOODLE_MCP_API_KEY to bind beyond localhost" | intended — export the key or bind `127.0.0.1` behind a tunnel |
| Every HTTP call returns 401 | key mismatch or missing header — send `Authorization: Bearer <key>` (mind the space) or `x-api-key` |
| `run_test` returns `ok=false` with `error: no .feature files...` | empty workspace or wrong `--workspace` — `list_tests` first |
| `generate_test` returns `runnable=false` | template `<placeholders>` remain — fill them (or have the calling agent supply quoted values in the description so slot filling can) |
| Runs pass but `get_last_result` shows zeros | results live under the last run's root inside the workspace (`<app>/report/` or `artifacts/`) — server started in the wrong directory |
| Foundry can't reach the server | needs HTTPS + Azure-reachable endpoint — check ingress, not Noodle |
| `run_test`/`run_and_report` fails with a Playwright "Executable doesn't exist" error, but the identical `noodle run` works in the same shell | your MCP host spawned `noodle-mcp` with a stripped env (most hosts default to a minimal one for stdio servers, not a full inherited one) — a var like `PLAYWRIGHT_BROWSERS_PATH` never reached the process. Add an `env` block to the host's server config with whatever vars your deployment needs (NOOD_0059) |
| `run_test`/`run_and_report` fails with "launched a headed browser without having a XServer running" | the workspace's `.env` has `NOODLE_HEADLESS=false` (the `init` default) and the host has no display — pass `headless: true` on the tool call instead of editing `.env` (NOOD_0059) |

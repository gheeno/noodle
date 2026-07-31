# Logging & observability

Noodle logs through the Python standard library (`noodle/log.py`) — one logger,
two output shapes chosen by `NOODLE_LOG_FORMAT`. This is the framework's own
event stream: errors, model decisions, run outcomes, and a governance/audit
trail for a shared or containerized deployment.

Related: [architecture.md](architecture.md) for where the logger sits in the
engine, [manual.md](manual.md) for running tests.

## Enabling structured logs

| `NOODLE_LOG_FORMAT` | Output | For |
|---|---|---|
| `text` (default) | the emoji breadcrumb console, live to **stdout** | a human at a terminal, CI console |
| `json` | one JSON object per line to **stderr** | a container/CI shipping logs to a platform store |

```bash
NOODLE_LOG_FORMAT=json noodle run
```

The container image sets `NOODLE_LOG_FORMAT=json` by default (see the
`Dockerfile`). `text` mode is byte-for-byte the classic console — turning JSON
on never changes what a human sees, and the lifecycle telemetry events below are
emitted *only* in json mode.

Why stderr: the `noodle-mcp` stdio transport uses **stdout** for its protocol
frames, so every structured line goes to stderr to keep that channel clean.
`NOODLE_LOG_LEVEL` (default `INFO`) gates verbosity in both modes.

## The CI build log

`noodle run` goes `--quiet` automatically off a TTY, which is right for an agent
(the behave stream is the single heaviest thing an LLM holds resident) and wrong
for CI, where nobody is watching a file. **When a build console is watching,
Noodle streams a maven-style progress log to stderr**:

```
[INFO] Run started — tests/checkout
[INFO] Feature: Checkout — a signed-in customer buys one item
[INFO]   Scenario: Add to basket and pay by card
[INFO]   PASSED: Add to basket and pay by card (11.4s)
[INFO]   Scenario: Reject an expired card
[ERROR]     Step failed: the message 'Card expired' should be visible
[ERROR]   FAILED: Reject an expired card (6.1s)
[INFO]   Scenario: Reject an expired card [retry 1/1]
[ERROR]   FAILED: Reject an expired card (5.8s) [retry 1/1]
[ERROR] Run finished — 4 passed, 1 failed, exit 1 (48.2s)
```

Level-prefixed and emoji-free, so `grep '^\[ERROR\]'` over a job log yields
exactly the failures. The local TTY console keeps its emoji breadcrumbs — the
stripping happens at this sink only. A retried scenario is marked `[retry N/M]`:
behave re-runs it in place, so it would otherwise read as a duplicate.

`--log-level DEBUG` adds a line per step — the `mvn -X` tier, and the one that
tells you *where* a wedged suite actually stopped. Set `NOODLE_LOG_FORMAT=json`
and the same events ship as one JSON object per line instead.

### Parallel runs

`--parallel N` runs N behavex worker **processes**, and they all stream to the
same stderr. Every line carries its lane, so one worker can be replayed out of
the interleave:

```
[INFO] [w1] Feature: Seeding many records — one step per batch
[INFO] [w3] Feature: REST Write — POST, PUT, PATCH, DELETE with request bodies
[INFO] [w2]   Scenario: Full CRUD lifecycle — create, read, update, patch, delete
[ERROR] [w3]     Step failed: extracts 'id' from the response
[ERROR] [w3]   FAILED: POST — create a new object (0.4s)
```

```bash
grep '\[w3\]' build.log     # that lane's features and scenarios, in order
```

The lane is `1..N`, claimed at worker startup (`runlock.worker_index()`) and the
same number `NOODLE_WORKER_INDEX` exposes to tests. A sequential run has one
process and no tag. In json mode the same records carry `lane` alongside
`worker` (the pid, matching that worker's `noodle.p<pid>.log`) and `feature`, so
a log store can group by any of the three.

The default `--parallel-scheme feature` keeps one feature file on one worker, so
a lane reads as a coherent story. `--parallel-scheme scenario` splits a file
across workers — the tags still separate them, but the narrative won't be
per-feature.

### When the engine itself throws

A failing assertion is the test doing its job. A `TypeError` out of the engine
is a different problem, and on a build log the two used to look identical.

- **Mid-run** — a non-`AssertionError` names itself under the failed step:
  `[ERROR]       TypeError: unsupported operand`. A genuine assertion failure
  stays terse (the RCA report has the detail).
- **In a hook** — behave reports an escaped exception as a single `ABORTED:
  HOOK-ERROR in hook=before_all` and prints the traceback to stdout, which
  `--quiet` has diverted to `run.log`. The six behave boundaries (`before_all`,
  `before_feature`, `before_scenario`, `after_step`, `after_scenario`,
  `after_all`) log the class, message and traceback to the build log first,
  then re-raise:

  ```
  [ERROR] ENGINE ERROR in before_all: RuntimeError: config parse failed
  [ERROR] Traceback (most recent call last):
  [ERROR]   File ".../noodle/hooks.py", line 313, in before_all
  ```

Ordinary engine functions are deliberately **not** wrapped. A blanket
try/except over every method swallows failures rather than surfacing them; the
hooks are wrapped because they're the boundary where an exception leaves
Noodle's control and loses its traceback.

Behave's own per-action stream stays in `<artifacts>/run.log`; only the curated
event stream (plus every `WARNING`) reaches the console.

**Who gets it** — first match wins:

| | Console stream | Log file |
|---|---|---|
| `NOODLE_LOG_PROGRESS=0` / `=1` | explicit, always wins | always written |
| Agent via the MCP server or `noodle run --json` | **off** | always written |
| `CI` or `TF_BUILD` set (GitHub Actions, Azure DevOps, GitLab…) | **on**, no config | always written |
| A human at a TTY | behave already streams live | always written |

The agent doors set `NOODLE_LOG_PROGRESS=0` themselves rather than relying on
`CI` being unset — an AI-SDLC orchestrator runs *inside* the pipeline, and its
captured subprocess pipes stderr straight into the payload the model reads. So
"Copilot, run the regression tests" costs the same tokens it did before; the run
payload carries the log's **path** (`log`), which the model reads only when a
run goes red.

Nothing is ever lost either way: the file log below is unconditional.

> Azure DevOps: the stream is on stderr. ADO defaults `failOnStderr` to false,
> so this won't turn a green job red — don't enable that flag on the Noodle
> step, or set `NOODLE_LOG_FORMAT=json` and parse it instead.

## Where the logs go

- **Console stream** — stdout (text) or stderr (json), per the table above; the
  CI build log above is always stderr.
- **Per-run file log** — `<artifacts>/logs/noodle.log`, mirrored from the same
  logger. It's a direct file handler, immune to the behave runner's per-scenario
  output capture, so it's the reliable structured sink when nothing is streaming
  (CI archives the `artifacts/` tree). It honors `NOODLE_LOG_FORMAT` too.
- **Per-scenario slice** — `<artifacts>/logs/<scenario>.log`, attached to that
  scenario as `run log` in Allure and in the Azure Tests tab. It's the same
  logger's output, cut at scenario boundaries so a parallel run's interleave
  doesn't make it useless. Every step opens with its own line naming the engine
  function that ran it, so the breadcrumbs under it (POM resolutions, evidence,
  healing) have an owner:

  ```
  ▶️  Step: I click the login button
     ↳ actions.click(locator='login button')
  📋 POM: resolved 'login button' via pom.yaml
  ```

  It's a plain log line, not telemetry — it reaches the reports and the file
  log, never the CI build console (that tier is `step.end` at `--log-level
  DEBUG`). The function name is read off the runner's own dispatch chain, so it
  can't drift; an action whose branch isn't a plain call shows the action type.
- **Parallel runs** — one file per worker process, `noodle.p<pid>.log`. All
  workers share the run's `run_id`; each line also carries its own `worker` pid,
  so a merged view (`cat <artifacts>/logs/noodle.p*.log | sort`) is coherent
  (the ISO-`Z` timestamps sort chronologically).

## The record shape (JSON mode)

Field names follow the OpenTelemetry log data model, so adopting an OTel
collector later is a transport swap, not a schema change.

```json
{"timestamp":"2026-07-24T18:04:11.812Z","severity_text":"INFO","severity_number":9,
 "body":"🤖 llm call → anthropic/claude-sonnet-5 (312ms)","service_name":"noodle",
 "run_id":"9f2c4ab1d0e37c58","workspace":"team-b","feature":"login.feature",
 "scenario":"Valid login","event":"llm.call",
 "attributes":{"model":"anthropic/claude-sonnet-5","input_tokens":1200,"usd":0.0042}}
```

Every record carries: `timestamp`, `severity_text`, `severity_number`, `body`,
`service_name`, plus the correlation context (`run_id`, and `workspace` /
`feature` / `scenario` when known). Structured events add `event` and
`attributes`; a plain log line has neither but is still correlated.

`run_id` crosses the CLI → behave subprocess boundary via `NOODLE_RUN_ID`, and an
MCP tool call stamps its own, so "agent called `run_and_report`" ties to
"scenario X failed on step Y" in one query.

## Event reference

| `event` | Emitted at | Key attributes |
|---|---|---|
| `run.start` / `run.end` | the CLI, once per run | target, tags, browser, headless, parallel / duration_ms, passed, failed, verified, exit_code, llm_usd, model, engine_version, git_sha |
| `feature.start` | behave hooks | file |
| `scenario.start` / `scenario.end` | behave hooks | tags / status, duration_ms |
| `step.end` | behave hooks, **DEBUG only** | step, status, duration_ms |
| `step.fail` | behave hooks (failure only) | step, error_class, error (redacted), screenshot |
| `llm.call` | every model call | model, purpose (`llm`/`rca`), input_tokens, output_tokens, usd, duration_ms, temperature, api_host, capped |
| `locator.resolve` | vision-model locate | target, strategy (`vision`) |
| `locator.heal` | every non-primary resolution | original, technique, fuzzy |
| `rca.verdict` | AI root-cause analysis | category, label, confidence, ai_authored, model |
| `mcp.tool` | every `noodle-mcp` tool call | tool, workspace, duration_ms, ok, error, payload_bytes |
| `mcp.auth.deny` | HTTP key gate (WARNING) | remote_ip, path, reason |

`run.*`/`feature.start`/`scenario.*`/`step.*` are lifecycle telemetry: emitted in
json mode, and in text mode when a build console is watching (see above). `step.end`
additionally needs `NOODLE_LOG_LEVEL=DEBUG` / `--log-level DEBUG` — at INFO a
1000-scenario suite would emit ~10k console lines.

## Secrets are never logged

Deny-by-default redaction runs at the logger for every sink:

- Values from any `*secrets.env` file or Azure Key Vault are scrubbed by value.
- Container/host-injected credentials are swept from `os.environ` by key name
  (`*_API_KEY`, `*_PASSWORD`, `*_TOKEN`, …) and scrubbed by value too.
- Structured `attributes` are scrubbed by value and masked by credential
  key-name; token *counts*, durations, paths and model ids are left intact.
- API endpoints log the **host/provider only**, never a key-bearing URL; the
  auth-deny event logs the caller and reason, never the supplied key.

Prompt/completion content is **off by default**. `NOODLE_LOG_LLM_PAYLOADS=1`
writes it to a separate, gitignored `<artifacts>/llm/<run_id>.jsonl` (redacted,
screenshots omitted) — never the log stream.

## Monitoring (Azure Log Analytics)

The events are the metrics. Starter KQL against the Container Apps log table:

```kusto
// Pass/fail per team, last 7 days
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(7d)
| extend e = parse_json(Log_s)
| where e.event == "run.end"
| summarize runs=count(), failed=sum(toint(e.attributes.failed)) by tostring(e.workspace)

// AI spend per team per day
... | where e.event == "llm.call"
    | summarize usd=sum(todouble(e.attributes.usd)) by tostring(e.workspace), bin(TimeGenerated, 1d)

// Auth failures — the security alert
... | where e.event == "mcp.auth.deny" | summarize count() by tostring(e.attributes.remote_ip)
```

Three alerts worth having on day one: an `mcp.auth.deny` spike, a `run.end`
failure rate over threshold, and daily `llm.call` spend over budget.

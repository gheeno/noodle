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

## Where the logs go

- **Console stream** — stdout (text) or stderr (json), per the table above.
- **Per-run file log** — `<artifacts>/logs/noodle.log`, mirrored from the same
  logger. It's a direct file handler, immune to the behave runner's per-scenario
  output capture, so it's the reliable structured sink when nothing is streaming
  (CI archives the `artifacts/` tree). It honors `NOODLE_LOG_FORMAT` too.
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
| `scenario.start` / `scenario.end` | behave hooks | tags / status, duration_ms |
| `step.fail` | behave hooks (failure only) | step, error_class, error (redacted), screenshot |
| `llm.call` | every model call | model, purpose (`llm`/`rca`), input_tokens, output_tokens, usd, duration_ms, temperature, api_host, capped |
| `locator.resolve` | vision-model locate | target, strategy (`vision`) |
| `locator.heal` | every non-primary resolution | original, technique, fuzzy |
| `rca.verdict` | AI root-cause analysis | category, label, confidence, ai_authored, model |
| `mcp.tool` | every `noodle-mcp` tool call | tool, workspace, duration_ms, ok, error, payload_bytes |
| `mcp.auth.deny` | HTTP key gate (WARNING) | remote_ip, path, reason |

`run.start`/`run.end`/`scenario.*`/`step.fail` are lifecycle telemetry, emitted
in json mode only.

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

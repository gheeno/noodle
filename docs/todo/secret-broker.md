# TODO — masked secret broker: the HOST-SIDE half (deferred)

Status: the **Noodle-side** items below all shipped (NOOD_0118/0171 — see the
SHIPPED marks in the next section). What remains deferred, ticket not yet
assigned, is the **AI-SDLC host** half: prompt text and tool arguments are
not reachable by Noodle's redactor, and no engine change can make them so. As of NOOD_0130 the shipped policy accepts prompt credentials and
writes them (write-only) to the app-local gitignored `<app>_secrets.env` via
`author_test(secret_values=…)`; this file is the eventual replacement for that
temporary transcript-risk acceptance, not a description of how Noodle works
today. Do not block test-generation optimization work on it. Captured here so
the fast/cheap-authoring review's secret-handling design isn't lost.

Two boundaries: Noodle can secure its **runtime output**; the AI-SDLC host
must secure the **prompt-to-process handoff**. Neither can hide a value a host
tool already logged.

## Temporary policy in force until this ships

1. If the original prompt contains credentials, use them without re-asking.
2. Create/update the app's gitignored `<app>_secrets.env` with those values.
3. If the file is already populated, use it without confirmation.
4. Never put a raw password/username/token/API key in `.feature`, POM, or
   environments YAML — reference `{env:APP_PASSWORD}` / `{env:APP_USERNAME}`,
   app-prefixed to avoid collisions.
5. Never repeat a secret in assistant prose, progress updates, summaries,
   failures, RCA, reports, or generated steps.
6. No secret-confirmation HIL. If a required value is absent from prompt,
   file, process env, or CI store, return only the missing key name and fail
   the automation step.

Known limitation: when an agent writes a prompt-provided secret through a
normal edit/shell/MCP call, the host may record that tool argument. The agent
can avoid repeating the value; Noodle can redact its own runtime output;
neither hides a payload the host already logged.

## Noodle changes — ALL SHIPPED (NOOD_0118 / NOOD_0171)

Kept for the audit trail; none of this is open work.

1. **SHIPPED** — secret-like process-env keys register with the value
   redactor (`noodle/log.py` `register_env_secrets()`; the key set covers
   `PASSWORD`, `PASSWD`, `TOKEN`, `SECRET`, `API_KEY`, `PRIVATE_KEY`).
2. **SHIPPED** — registration runs again after app-local env loading
   (`noodle/hooks.py`) and after Key Vault loading (`noodle/secrets_akv.py`),
   so late-loaded values get the same protection.
3. **SHIPPED** — one redactor serves console, `run.log`, captured warnings
   and RCA inputs; there is no second masking system.
4. **SHIPPED** — preflight names process env, the CI secret store and a
   pre-populated `*_secrets.env` as valid sources (`noodle/repl/core.py`).
5. **SHIPPED** — missing keys return the structured preflight failure and
   launch no browser; the engine never pauses for HIL.

## AI-SDLC host changes (outside Noodle's redactor)

Prompt text and tool arguments are not reachable by Noodle's redactor. The
NOOD_0130 temporary policy accepts that exposure — `author_test(secret_values=…)`
repeats the value in a transcript-visible payload, deliberately, to restore the
proven workflow. The END STATE below removes that exposure: once the broker
ships, `secret_values` (and any `apply_patch`/`echo`/inline shell env
assignment carrying a raw value) is superseded by an opaque-ref op. Add a
host-side broker:

1. Detect secret fields in the original task input before agent tool use.
2. Store each value in a host secret envelope; assign an opaque ref
   (`secret_ref_1`).
3. Expose a masked op, e.g. `set_workspace_secret(key="SHOP_PASSWORD",
   ref="secret_ref_1")` — value never enters model output or serialized args.
4. Resolve the ref inside the trusted host: write the gitignored
   `*_secrets.env` directly, or inject into the `noodle-mcp`/`noodle run`
   process env.
5. Return only key names + success/failure to the agent. Never return values.
6. If the file is already populated, let Noodle consume it directly.
7. No masked broker → require pre-provisioned file/CI injection. Unattended,
   no follow-up question.

## Checks / exit criterion

- A sentinel CI password authenticates a test but never appears in console,
  `run.log`, warnings, RCA, report payloads, or tool arguments.
- A prompt-provided sentinel appears in the original human input and the
  secret store/file, but nowhere in assistant messages or tool logs.
- The masked writer accepts only key + opaque ref; no raw-value parameter.
- Pre-provisioned secrets complete the run with zero user questions.
- Missing secrets return only key names and launch zero browsers.
- Non-secret env values are not needlessly redacted.
- File-loaded and process-injected secrets follow the same masking path.

**Exit:** an unattended AI-SDLC job can generate, run, and serve reports with
prompt/file/CI credentials and no HIL. A secret may appear in the original
human input, but never in subsequent assistant output, tool logs, Noodle
logs, RCA, or reports.

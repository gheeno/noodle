# Session diagnostics (NOOD_0147)

**The problem this solves.** Noodle workspaces are driven by whatever LLM
agent a tester uses — Claude Code, Copilot, codex, anything that reads
`AGENTS.md` or connects over MCP. When a test-development session goes
wrong, the only record of *why* — what the agent tried, in what order, and
what it suspects — lives in that agent's session memory, and evaporates
when the tester closes the chat. Asking every tester to interrogate their
agent by hand ("why did that take so long? what did you do?") doesn't
scale. Session diagnostics make the agent write that answer down
automatically, at session end, into a file the tester can send back with
one command.

**Design constraints, in order:** automatic (no human prompt needed),
cheap (written once, from session memory — no extra model calls, no
re-reading logs), and bounded (a workspace can never accumulate more than
a capped handful of reports).

---

## The triggers — three detected by the engine, two by the agent

The engine watches the run stream itself (`diagnostics.track_run`,
per-target state in `.noodle/diag_state.json`), so the common failure
shapes are detected **deterministically, with zero model calls and zero
always-on prompt text**. When one fires, the run result the driving agent
already reads carries a `diagnostic_due` block — in the `run_test` /
`run_and_report` MCP payloads, in `noodle run --json`, and as a `🩺
diagnostic due` line on the plain CLI. That makes the mechanism automatic
even for an agent that never loaded `AGENTS.md`.

| Trigger | Fires when | Detected by | Threshold knob |
|---|---|---|---|
| `hard-fail` | The dev-fix loop's red-run count reaches the cap and the test is still red | engine | `NOODLE_DEV_FIX_ATTEMPTS` (default 10) |
| `first-attempt-fail` | The **first** run of this dev session for the target was red (why did it fail even once, when the probe→author pipeline was followed?) | engine | — |
| `slow-dev` | First-run→now wall clock exceeded the slow threshold while red — or by the time the run finally went green (easy tests: 3–5 min; complex: 10–15 min; past 20 is a failure) | engine | `NOODLE_DIAG_SLOW_MIN` (default 20 minutes) |
| `over-budget` | The **driving agent's own** session spend exceeded the budget — e.g. more than 20 AIC on a lower-tier model. This is the agent's cost, *not* Noodle's engine-side `llm_cost` | agent | `NOODLE_DIAG_COST_BUDGET` (default 20) |
| `manual` | The user's prompt contains `--diagnostic` or `skill: diagnostic` — log regardless of outcome, even on green | agent | — |

A green run clears the target's streak, and state idle longer than
`NOODLE_DIAG_SESSION_GAP_MIN` (default 120 minutes) restarts as a fresh
dev session — so a workspace that has been running for weeks can't
misreport `first-attempt-fail` or accumulate a phantom `slow-dev` clock,
and an ordinary CI failure doesn't look like a development session.

**No trigger fired → nothing is written.** That, plus the engine-side cap
and dedupe below, is what keeps a workspace from drowning in reports.

## What the agent writes vs. what the engine appends

One call per developed test, at session end:

```bash
noodle diagnostic log <app> \
  --trigger hard-fail --trigger slow-dev \
  --summary "Login test never went green: the OTP field is inside an iframe the POM can't reach." \
  --timeline "probe → author → 10 fix laps, each red on the same step" \
  --cause "engine gap: POM entries can't target in-frame controls" \
  --fixes "re-probed with --do; tried {pom:} pinning; switched to frame step — still red" \
  --duration-min 27 --attempts 10 --agent "codex 5.3" --agent-cost "23 AIC"
```

MCP: the `log_diagnostic` tool, same fields. Everything above comes from
the agent's **session memory** — it must never re-read run logs or reports
just to compose the diagnostic (that costs tokens for facts the engine
already has). The engine appends, deterministically and free:

- the last run's structured result (pass/fail counts, first failures),
- the compact RCA verdict (category + confidence + suggested fix),
- the run's engine-side `llm_cost`,
- the installed noodle version.

Secret values registered from any `*secrets.env` are scrubbed from the
written file (same NOOD_0118 value-scrub as run output), and agents are
instructed never to include credentials in the narrative in the first
place.

## Where it lands, and the anti-spam guarantees

Files are Markdown with YAML front matter (grep-able, and parseable later
if we aggregate them), named `<UTCstamp>_<app>.md`, in:

```
<workspace>/diagnostics/        # scaffolded into .gitignore by noodle init
```

Enforced in the engine — prompt drift can't break them:

- **Dedupe** — a repeat call with the same `--session` id (or, without
  one, for the same app within `NOODLE_DIAG_DEDUPE_MIN`, default 30
  minutes) **updates** the existing file instead of adding another.
- **Cap** — at most `NOODLE_DIAG_MAX` (default 25) reports; the oldest
  rotate out on overflow.
- **Truncation** — each narrative field is clipped at 4 KB; a diagnostic
  is a summary, not a transcript.

## MCP blocked? The whole loop works over the plain CLI

Corporate environments that can't run the MCP server lose nothing —
every leg of the mechanism has a CLI form, and the agent-facing rules
live in `AGENTS.md`, a plain file the agent client loads from disk with
no MCP involved:

- **Detection/nudge** — `noodle run` prints the `🩺 diagnostic due (…)`
  line (and `noodle run --json` carries the same `diagnostic_due` block).
- **Contract** — `noodle diagnostic guide` prints this document
  (bundled into installed distributions, so it works far from any source
  checkout); `noodle diagnostic log --help` lists the fields.
- **Write / inspect / ship** — `noodle diagnostic log`, `noodle
  diagnostic list`, `noodle diagnostic bundle`.

## Getting them back from testers

```bash
noodle diagnostic list      # what's on disk, newest first
noodle diagnostic bundle    # → diagnostics/noodle_diagnostics_<stamp>.zip
```

`bundle` produces the one file a tester attaches to a message. Earlier
bundles are replaced, not accumulated. Since values were scrubbed at write
time and the folder is gitignored, the zip is the intended (and only)
sharing channel.

## Tuning per deployment

All knobs are plain env vars, so a workspace you hand to testers can ship
different thresholds in its `.env`:

```bash
NOODLE_DIAG_SLOW_MIN=20      # minutes before slow-dev fires
NOODLE_DIAG_COST_BUDGET=20   # AIC/credits before over-budget fires
NOODLE_DIAG_MAX=25           # reports kept before rotation
NOODLE_DIAG_DEDUPE_MIN=30    # same-app update window, minutes
```

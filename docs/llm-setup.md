# Choosing and Configuring an LLM for Noodle
<!-- Branch: NOOD_0065 -->

> **For:** testers and admins configuring an LLM provider.
>
> Covers three things: which model to default to (local or cloud), what to
> do if you only have a work GitHub/Azure account instead of a personal API
> key, and the known gaps in the LLM layer today. For what the LLM is
> actually used for and when it's triggered, see
> [architecture.md §4](architecture.md) and
> [encyclopedia.md §16](encyclopedia.md#16-using-an-llm--setup-providers-and-modes).

---

## Current state (grounding)

Noodle's LLM layer is intentionally thin — one gateway module, two functions:

| Function | Purpose | Reads |
|---|---|---|
| `ask(prompt) -> str` | Step-JSON resolution (`resolver/step_resolver.py`) | `NOODLE_MODEL`, `NOODLE_LLM_URL` |
| `ask_vision(prompt, image_b64) -> str` | Locator fallback, semantic assertions, RCA | `NOODLE_MODEL`, `NOODLE_LLM_URL` |

Everything routes through **LiteLLM**, so any provider (Ollama, Anthropic,
Gemini, Groq, OpenAI, Foundry Local, GitHub Copilot) works via one
`<provider>/<model>` string with zero code changes. The framework is local
and deterministic by default — **no `NOODLE_MODEL` set means no LLM call,
ever** (`docs/architecture.md` §1, §8). The LLM is a labelled fallback at
four trigger points (`docs/architecture.md` §5), not a default path.

Separately, `noodle repl` (README.md § Agentic mode) is a rule-based REPL
for test creation/running/summaries. It costs $0 by default and only
touches a model when `--llm claude|gemini|ollama` is passed, for two jobs:
richer `create test` generation and richer `summary` narratives.

**Don't confuse `NOODLE_MODEL` with the coding agent driving Noodle over
MCP.** Claude Code CLI / Copilot CLI author and run tests from the outside
([mcp-guide.md](mcp-guide.md)); `NOODLE_MODEL` is the engine-side model the
*resolver* calls mid-run in Assisted (`auto`) / Pure (`full`) LLM mode. The
engine has no channel to call back into the MCP host, so the host agent can
never be the run-time model — for a local, $0 run-time model use Ollama
(§1). Every step the run-time model resolves is logged to the workspace's
`docs/steps_dictionary_suggestions.md` for promotion into a real pattern
(README § Pure LLM mode with a coding agent).

---

## 1. Recommended default — cloud, Sonnet-class

**As of NOOD_0151 the recommended default is a hosted Sonnet-class model —
`anthropic/claude-sonnet-5`** (or another current vision-capable cloud model
via LiteLLM): one id covers every trigger — step-JSON, vision locate,
semantic assertions, RCA — with none of the local-model JSON-strictness /
vision-quality trade-offs the rest of this section navigates.
`anthropic/claude-haiku-4-5` is the cheap tier when the suite only ever hits
the text fallback. §3 compares cloud providers on cost.

Local models (below) are the **fallback**, not the default — reach for them
when the network blocks hosted APIs or when compliance forbids screenshots of
the app under test leaving the machine.

### Local fallback — picking an Ollama model

`NOODLE_MODEL` is a **single** value shared by both `ask()` (text) and
`ask_vision()` (vision) — there is no separate "text model" / "vision model"
knob for the web path (only the `@visual` desktop path gets its own
`NOODLE_VISION_MODEL`). So the best local pick depends on which of the two the
workspace actually exercises more:

| Workload | Recommended default | Why |
|---|---|---|
| Step resolution (JSON action dict, `step_resolver.py`) | `ollama/qwen2.5-coder:7b` (or `:14b` with more VRAM) | Tuned for structured/code output — matches the fence-stripped JSON contract `_parse_action()` expects. Cuts hallucinated `type`/param names that would otherwise trip the `VALID_TYPES` guard or crash `runner.py` with a confusing `KeyError` instead of a clean `AssertionError`. |
| Vision (locator fallback, semantic assert, `NOODLE_RCA`) | `ollama/qwen2.5vl:7b` | Strongest open-weight vision model Ollama currently serves well. Fallback if VRAM is tight: `ollama/llama3.2-vision`. |

**Local recommendation (when cloud is not an option):**
- Web/DOM-heavy suites (LLM rarely triggers, and only ever for step-JSON) →
  `NOODLE_MODEL=ollama/qwen2.5-coder:7b`.
- Suites leaning on vision-locate / semantic assertions / RCA →
  `NOODLE_MODEL=ollama/qwen2.5vl:7b` (it's a VL model, so plain-text JSON
  still works reasonably — you're trading some JSON-strictness for vision
  quality).

Sizing guidance: 7B ≈ 8GB VRAM, 14B ≈ 16GB, 32B ≈ 24GB+. Start at 7B; move up
only if step-JSON hallucination or locator misses show up in
`artifacts/reports/healing-report.jsonl`.

---

## 2. Backup if the local LLM is down or offline

LiteLLM makes swapping providers a one-line `.env` change, but Noodle has
**no automatic failover** — nothing retries a different provider on a
connection error. Pick a backup deliberately, ahead of time:

| Tier | Model | Notes |
|---|---|---|
| Free | `gemini/gemini-2.0-flash` (or current Gemini Flash) | Already the framework's documented free/vision-capable fallback. |
| Paid, more reliable | `anthropic/claude-haiku-4-5` | $1/$5 per MTok, fast, vision-capable. Also brings GA structured-outputs support (`strict: true` tool schemas), which could eventually replace the regex/fence JSON-extraction in `_parse_action()` entirely. |

---

## 3. Cost-effective cloud LLM — Claude vs. Copilot vs. Gemini

Noodle's LLM usage is a narrow **single-call** fallback (JSON-in/JSON-out, or
screenshot-in/verdict-out), not an agentic workload — so cost-per-call and
schema reliability matter more than raw model intelligence.

- **Claude Haiku 4.5** ($1/$5 per MTok) — the standout choice for the
  *runtime* fallback path: cheap, vision-capable, and its structured-outputs
  support is a better fit for `step_resolver`'s JSON contract than
  prompt-and-hope regex extraction.
- **Claude Sonnet 5** ($2/$10 intro pricing through 2026-08-31, $3/$15
  standard) — the right step-up for `noodle repl --llm claude` when actually
  *authoring* `.feature`/POM files or narrating RCA/report summaries, where
  output quality matters more than per-call cost.
- **Gemini Flash** — cheapest/free option, already the framework's documented
  default free tier. Fine if cost is the only axis and schema rigor doesn't
  matter as much.
- **GitHub Copilot** — not a fit for the runtime LLM path via its IDE seat
  alone (no completions endpoint LiteLLM can target for a per-step JSON
  fallback) — but see §4 below for a callable path via `litellm`'s
  `github_copilot` provider if that's the only access you have.

**Bottom line:** Claude Haiku 4.5 for the runtime fallback path
(`NOODLE_MODEL`), Claude Sonnet 5 for test authoring / report narration
(`noodle repl --llm claude`).

---

## 4. Only have a work GitHub/Azure account? (no personal API key)

> Written for: cloning this repo onto a work laptop where the only AI
> access you have is through work accounts (GitHub Copilot, and maybe
> Azure), not a personal API key.

**GitHub Models is retired — don't use it.** GitHub retired GitHub Models
entirely on July 30, 2026 — "the playground, model catalog, inference API,
and bring your own key (BYOK)... no longer available to any customer,"
with brownouts starting July 16, 2026. The `github/<model>` LiteLLM
provider is not a path worth setting up.

**Copilot Chat is callable via `litellm`.** `litellm` has a
`github_copilot` provider that authenticates via GitHub's device-flow login
and talks to GitHub's own Copilot completions endpoint directly. If you
have a Copilot seat, this is the quickest path — see
[encyclopedia.md §16, Option D2](encyclopedia.md#16-using-an-llm--setup-providers-and-modes):

```bash
# .env — no secrets.env change needed
NOODLE_MODEL=github_copilot/claude-sonnet-4.5
```

First run opens a one-time device-code login in your terminal; the token
is cached at `~/.config/litellm/github_copilot/` after that.

**Azure AI Foundry is the real path for work-account-backed Claude access.**
GitHub's own Models retirement notice names Azure AI Foundry as the
programmatic-access successor. As of June 29, 2026, Claude models (Sonnet
4.5, Haiku 4.5, Opus 4.1 at last check) are GA on Azure AI Foundry. **This
is a separate resource from your Copilot seat** — it needs an Azure
subscription/tenant with Foundry provisioned, which your org may or may not
have set up. Ask IT before assuming it's there.

If your tenant has Foundry, `litellm` has a dedicated provider
(`docs.litellm.ai/docs/providers/azure/azure_anthropic`):

```bash
# secrets.env (gitignored)
AZURE_AI_API_KEY=<your Foundry resource key or AD token>
AZURE_AI_API_BASE=https://<your-resource>.services.ai.azure.com/anthropic

# .env
NOODLE_MODEL=azure_ai/claude-sonnet-4-5
```

Check your Foundry deployment's actual model name/version — deployed model
names are tenant-specific and Azure adds new Claude versions over time, so
confirm against your resource's model list rather than assuming the exact
string above. No code changes needed — same as swapping to Anthropic or
Gemini directly, `noodle/llm/client.py` just forwards `NOODLE_MODEL` to
`litellm`.

If Foundry isn't provisioned (or you don't want to wait on IT), fall back
to local Ollama — see §1 above and the addendum in §7 below for what was
actually tested (`ollama/llama3.1:8b` ran but wasn't accurate enough for the
ambiguous-verb fallback case; `qwen2.5-coder:7b` is the recommended pick for
the step-JSON path specifically).

---

## 5. Capability check

| Task | Local LLM capable? |
|---|---|
| Writing new tests (`noodle repl create test --llm ollama`) | Yes — `qwen2.5-coder` generates Gherkin fine. |
| Running tests / interacting with the agent | Doesn't need an LLM at all — `run`/`list`/`summary` are rule-based, $0, fully offline by design. |
| Reviewing report output (`summary --llm`, `NOODLE_RCA`) | Yes — bounded narrative/classification tasks suit a local model well. |
| **Spinning up the test-apps/busterblock** | **No framework support, local or cloud.** No environment-lifecycle primitive exists — only `run_command`/`run_script` (arbitrary shell, must be authored into a step) or `@precondition` (HTTP-only data seeding against an *already-running* app). An LLM could be told to emit a `run_command: docker compose up -d` step in full-LLM mode, but there is no readiness/health-check wait and no teardown-on-failure for a spun-up process. |

---

## 6. Gaps and weaknesses (independent of model choice)

1. **One `NOODLE_MODEL` for both text and web-vision.** Can't independently
   pick "best at JSON" vs. "best at vision" for the same run without
   accepting a compromise model.
2. **No app-lifecycle primitive.** Starting/stopping the app-under-test is
   entirely DIY via `run_command`, with no readiness gate or guaranteed
   teardown — contrast with `@precondition`'s teardown-even-on-failure
   guarantee for data.
3. **JSON extraction, not schema enforcement.** `_parse_action()`
   (`step_resolver.py`) and `rca.parse()` both use fence-stripping/regex
   rather than provider-native structured outputs. Works, but is inherently
   more fragile than `strict: true` tool schemas (Claude) or JSON-mode.
4. ~~**`NOODLE_LLM_MODE=full` has no cost/latency guard.**~~ — partially
   addressed (NOOD_0007): `NOODLE_LLM_MAX_CALLS` / `NOODLE_RCA_MAX_CALLS` cap
   total model calls per run (`noodle/llm/client.py::_check_cap`). Still no
   batching or caching of repeated identical step text across scenarios —
   that half of the gap is still open.
5. **No automatic failover.** LiteLLM makes switching providers trivial, but
   nothing in Noodle retries a different model on a local-model connection
   failure — that's entirely on the operator.
6. **Vision fallback is a (small) prompt-injection surface.** In
   `NOODLE_LLM_MODE=full`, a screenshot of the page under test goes to the
   vision model, so text *on the page* can try to steer the model's answer.
   Blast radius is deliberately tiny — the model only returns an element
   location (`agents/web/locator.py`), never executes steps, and calls are
   capped by `NOODLE_LLM_MAX_CALLS` — but keep full mode for apps you own,
   not arbitrary third-party sites.
7. **RCA fails silently, with zero log line.** `noodle/rca.py` — `review()`
   wraps everything in `except Exception: return None` with no log. Compare
   `agents/web/locator.py`'s vision-locate, which logs a `⚠️` warning on
   failure (added in NOOD_0031). A flaky or wrong local vision model
   degrades to "no RCA label" with no visibility trail at all. `noodle
   rca-report`'s heuristic classifier reads the same console warnings (now
   captured into the Allure result) and root-causes most failures with zero
   model calls, so an RCA verdict no longer depends on this path working.
   Open question 3 below is still unresolved.

---

## 7. Addendum — Ollama wiring + accuracy, tested (2026-07)

Ran the actual `llm_fallback.feature`/`pure_llm.feature` suite against a
locally-pulled `ollama/llama3.1:8b` (not one of this doc's recommended
`qwen2.5-coder`/`qwen2.5vl` picks — this was just what happened to be on the
box) to see what breaks in practice. Two distinct findings:

1. **Wiring gap — confirmed, not a bug.** `client.py`'s
   `ask()`/`ask_vision()` both default to `os.getenv("NOODLE_MODEL", ...)`,
   but `step_resolver.py` gates the call on `if os.getenv('NOODLE_MODEL')`
   *before* ever reaching `client.py` — so a running Ollama server is never
   auto-detected. `NOODLE_MODEL=ollama/<tag>` must be set explicitly in
   `.env`/`secrets.env`, matching whatever tag is actually pulled (`ollama
   list`), not assumed from the client's internal default string.
2. **Accuracy gap — confirmed, matches §1's recommendation.** With
   `NOODLE_MODEL=ollama/llama3.1:8b` set, the pipeline ran end-to-end (no
   config error) but picked the wrong action for `"authenticates using the
   login button"` — it hallucinated an `assert_visible` instead of `click`,
   consistently across the retry. This is exactly the failure mode §1 above
   predicts for a non-`qwen2.5-coder` model on the JSON-action-selection
   task. Two cheap fixes — **both landed in NOOD_0101**:
   - `ask()`/`ask_vision()` (`noodle/llm/client.py`) now default to
     `temperature=0` — Ollama's chat default (~0.8) was too high for a
     pick-exactly-one-enum-value task. `NOODLE_LLM_TEMPERATURE` overrides;
     set it to the empty string to omit the param for models that reject it.
   - The step-JSON prompt (`step_resolver.py::_llm_resolve_uncached`) now
     carries few-shot examples for verb-like phrasing ("authenticates" →
     click, "verifies X is displayed" → assert_visible) — exactly the
     vocabulary these two feature files use. Small local models lean much
     more on examples than architecture-scale models do.

   If a model still misresolves with both in place, that's the real signal
   to move to `qwen2.5-coder:7b` per §1, rather than tuning prompts further.
   Generation-side latency/token guidance lives in
   [llm-performance.md](llm-performance.md).

---

## 8. Who pays for what — token cost tracking (NOOD_0080)

A Noodle run has **two separate LLM spenders**, and Noodle can only see one:

| Domain | Who pays | Measured how |
|---|---|---|
| **Noodle's own calls** — step fallback (`NOODLE_LLM_MODE`), `@visual` locator, RCA vision + narrative, `--llm` summaries, LLM generation (`noodle repl --llm` / MCP `generate_test(use_llm=True)`) | The key behind `NOODLE_MODEL` (Ollama = $0) | **Exactly.** Every call funnels through `noodle/llm/client.py`; `noodle/llm/cost.py` records `usage` + `litellm.completion_cost()` per call. |
| **The driving agent** — Claude CLI/Code, Copilot CLI, VS Code Copilot reading the playbook, writing features, calling MCP tools | The user's Claude/Copilot subscription or API key | **Not by Noodle** — that traffic never touches the engine. Claude Code: run `/cost` (or OTEL for teams). Copilot: seat-based billing, no per-token cost API. |

**Where the numbers show up** (all automatic, no prompt needed):

- End of every run the engine prints one line:
  `💰 LLM cost: 12 call(s) | 48,210 in / 3,904 out tokens | ~$0.62 (llm $0.43, rca $0.19) | model anthropic/claude-sonnet-5`
- `artifacts/allure-results/llm_cost.json` — machine-readable ledger
  (per-pid files in `--parallel` runs; every reader sums them).
- The RCA markdown report footer.
- MCP results: `run_test`, `run_and_report`, `get_last_result`, and
  `generate_test --use_llm` all carry an `llm_cost` block, so a driving
  agent can relay it in chat (the playbook §6 tells it to).
- `noodle cost` — last run's actuals; `noodle cost <file>` — pre-flight
  token count + input-cost floor for a prompt/feature file
  (`litellm.token_counter`; output tokens are unknowable pre-run, so the
  dollar figure is a floor, not a forecast).

Models LiteLLM has no pricing for (self-hosted builds) report tokens with
`usd: null` — "cost unknown", never a fake $0. Runaway-spend *prevention*
stays with the existing call caps (`NOODLE_LLM_MAX_CALLS` /
`NOODLE_RCA_MAX_CALLS`, §6.4); this section is about *visibility*.

---

## Open questions

1. Should `NOODLE_MODEL` be split into `NOODLE_MODEL` (text) and
   `NOODLE_MODEL_VISION` (web vision), mirroring the existing
   `NOODLE_VISION_MODEL` split for the `@visual` desktop path?
2. Is an app-lifecycle primitive (start/health-check/teardown) worth adding
   as a first-class step family, or is `run_command` + `@precondition`
   sufficient with better documentation?
3. Should `rca.review()` gain the same `⚠️` warning-on-failure logging that
   `locator._vision_locate()` already has?

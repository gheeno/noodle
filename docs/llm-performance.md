# Generating Test Cases Fast — LLM Latency & Token Guide
<!-- Branch: NOOD_0101 -->

> **For:** anyone (human or agent) who finds LLM-driven test generation slow
> or token-hungry — and for the driving agent itself, via
> `read_docs('llm-performance')`. Covers where the time and tokens actually
> go when a model writes or updates a `.feature` file, which path to pick
> for a given request, and the engine knobs that control spend.
> Provider/model *choice* is [llm-setup.md](llm-setup.md); this doc is about
> making whatever model you picked fast.

---

## 1. The one rule that explains everything else

**LLM latency is dominated by output tokens.** Input tokens are processed in
parallel; output tokens are decoded one at a time. A call that returns a
whole 40-line `.feature` file is ~10× slower than a call that returns 3
fixed step lines, almost regardless of model. Every technique below is a
way of either (a) not calling a model at all, or (b) making the model say
less.

---

## 2. Pick the cheapest path that can do the job

Fastest first — stop at the first row that fits the request:

| # | Path | Model calls | When |
|---|------|-------------|------|
| 1 | **Rule-based templates** — `noodle repl` "create test for … at …", MCP `generate_test` (default `use_llm=False`) | **0** | Login / search / checkbox / dropdown / generic single-assertion shapes. Quoted values in the request slot-fill the template, so the file often ships runnable. |
| 2 | **Author-and-validate** — the *driving* agent (Claude Code, Copilot) writes vocabulary-shaped Gherkin itself, then MCP `validate_feature` → `write_feature` | **0 engine-side** (the driving agent's own pass is spend it was making anyway) | Anything the templates don't cover, when an agent is driving over MCP. This is almost always faster than `use_llm=True`: one authoring pass, no engine round-trip, and validation is a deterministic dry-run. |
| 3 | **Engine generation** — `generate_llm` / `generate_test(use_llm=True)` via `NOODLE_MODEL` | 1 (+1 repair max) | No driving agent (plain `noodle repl --llm`), or the caller can't author Gherkin directly. |

**Updating an existing test is never "regenerate the file".** Add a
scenario with `append_to` (REPL: "…add it to `<stem>`"; MCP `generate_test`
`append_to=`), or edit the specific lines and re-run `validate_feature` /
`noodle validate --resolve`. Regenerating pays full output-token price for
every line that wasn't changing — and risks the model rewording lines that
were already green.

**A brand-new app package is one call, not four (NOOD_0128/0129).** For a
whole new package (environments.yaml + POM + feature + secret placeholders),
lead with `author_test` / `noodle author --spec` instead of
copy-`sample_app` → rename → edit×4 → validate. It writes everything in one
transaction (parse-checked first; any write failure restores every original
byte), and returns a `ready` flag with `blocking` reasons — an unmatched step
with no model set, or a POM that can't scope to the feature's URLs — so you
**don't** need a separate `validate --resolve` call before running. Reserve
path 2 (`validate_feature` → `write_feature`) for a focused edit to an
*existing* package.

**Running is one call too (NOOD_0128).** Lead with
`run_and_report(..., serve_reports=True)` / `noodle run --json --serve`: it
preflights secrets (no browser on missing creds), runs headless, rebuilds
both reports, folds the compact RCA into the payload on red, and serves the
URLs — replacing the run + `get_rca` + `report` + `serve` chain.

---

## 3. What NOOD_0101 changed engine-side (and why)

1. **`temperature=0` by default** (`noodle/llm/client.py`). Every engine
   call is constrained-output — step JSON, vocabulary-bound Gherkin,
   pick-a-number classification. Sampling entropy there only produced
   repair loops and wrong-enum retries (measured on Ollama, chat default
   ~0.8 — llm-setup.md §7). Override with `NOODLE_LLM_TEMPERATURE`; empty
   string omits the param for models that reject it.
2. **Keyword-gated vocabulary** (`prompts.relevant_vocabulary`). The
   generation prompt used to embed all ~10 step families every time; a
   plain login test never needs the REST or payload sections. Core families
   (navigate / interact / wait / assert) always ship; the specialised ones
   only when the request's wording triggers them — roughly half the prompt
   tokens for the common case. A missed trigger costs one repair pass (the
   repair prompt keeps the full vocabulary), never a wrong file.
   `NOODLE_PROMPT_VOCAB=full` restores send-everything.
3. **Line-level repair** (`prompts.REPAIR_STEPS`,
   `generate._apply_step_repairs`). The repair pass used to resend the whole
   file and ask for the whole file back. Now the model sees only the
   unmatched lines and returns only their fixes; splicing them back in is
   deterministic string work — so the repair is fast (see §1) *and*
   structurally cannot mangle steps that already resolved. The full-file
   rewrite survives only for drafts that didn't parse as Gherkin at all.
4. **Few-shot verb examples in the runtime step-JSON prompt**
   (`step_resolver._llm_resolve_uncached`) — "authenticates" → `click`,
   "verifies X is displayed" → `assert_visible`. Small local models lean on
   examples; a wrong enum pick costs a retry (2× latency) or a failed run
   (minutes).

Already in place before NOOD_0101, worth knowing:

- **Per-run step cache** — the same unmatched sentence repeated across
  scenarios costs one model call per run, not one per occurrence
  (`step_resolver._llm_cache`).
- **Call caps** — `NOODLE_LLM_MAX_CALLS` / `NOODLE_RCA_MAX_CALLS` stop a
  badly broken run from burning a call per step.
- **Deterministic-first everywhere** — pattern table before LLM at run
  time, rule-based plan splitter before the LLM planner in the REPL,
  deterministic step ranking before the LLM tie-breaker in step-search.

---

## 4. Techniques for the driving agent

These are the habits that make a Claude/Codex-class agent generate accurate
tests quickly — the agent-playbook §0 output discipline, made concrete for
generation:

- **Probe before authoring (NOOD_0113).** On an unfamiliar page — and on
  *any* SPA/Angular app — call `probe_page(url)` (`noodle probe <url>`)
  before writing a single step. One headless load returns every actionable
  control (including hidden trigger zones like a `.trigger-dev-panel`
  hitbox), a ready CSS selector each, paste-ready POM YAML for the controls
  generic steps can't name, a vocabulary-shaped suggested step per control,
  and the exact heading texts to copy into assertions (`Branch #12`, not
  `branch#12`). That single probe replaces the expensive
  author-blind → run → RCA → hand-probe → fix-POM → re-run lap; the 100+
  interaction sessions all came from skipping it. `next_pages` in the
  payload lists same-origin links — probe the page a scenario navigates to
  in the same call (`probe_page("url1 url2")`, one browser).
- **Fetch the vocabulary once, author against it.** The
  `noodle://vocabulary` MCP resource (or `read_docs('steps_dictionary')`)
  is the complete grammar. Don't paste whole docs into context —
  `read_docs(query=…)` returns matching lines only.
- **Validate before writing, not by running.** `validate_feature` is a
  deterministic dry-run: it flags every step that would need a runtime LLM
  fallback in milliseconds, with no browser. Fix unmatched steps *then*
  write. A browser run to discover a phrasing mistake costs minutes.
- **One repair, not a rewrite.** When validation flags steps, fix those
  lines only. Emitting the whole file again pays §1's price for every
  unchanged line.
- **Reuse, don't re-derive.** `search_step` finds the closest existing
  phrasing for an action; the templates and `append_to` reuse structure.
  Every sentence the agent doesn't have to compose is output tokens saved.
- **Probe compact, ask narrow (NOOD_0117).** `noodle probe <url> --compact`
  (MCP `probe_page` is compact by default) returns only what authoring
  needs — needs-POM controls, POM YAML, suggested steps, headings — a
  fraction of the full dump; `--section pom|controls|steps|headings` and
  `--max-controls N` fetch one narrow slice instead of grepping a 24 KB
  blob in context. Testing a search flow? `--search "term"` probes the
  RESULTS page in the same call: its "NN results" summary element (with a
  ready POM entry) and the summary-count assertion to prefer over counting
  rendered cards — rendered counts are lazy-load- and headless-dependent.
  Picking a search *suggestion*? `--suggest "partial"` (NOOD_0141) captures
  the typeahead in the same call — exact suggestion strings and copy-ready
  steps — instead of reverse-engineering the dropdown one red run at a time.
  A stateful flow (fill → select → save → next screen)? `--do "enter <v> in
  <f>" --do "click save"` (NOOD_0144) executes the real transaction in the
  same probe and snapshots each new state — one session covers the whole
  flow, `{env:KEY}` values resolve engine-side, secrets stay out of the
  transcript.
- **Runs during the dev loop: `headless=True, retries=0`** (MCP) /
  `--headless --retries 0` (CLI). The default retry silently doubles
  wall-clock on every red run while iterating; turn it back up once green.
- **Quiet runs (NOOD_0116/0117).** The full behave console stream is the
  single heaviest resident blob an agent re-bills on every later call.
  `noodle run --quiet` diverts it to `<artifacts>/run.log` and prints only
  the summary — and it's automatic when stdout isn't a TTY (agent/CI);
  `NOODLE_QUIET=0` forces the stream back for a human.
- **Read failures cheaply (NOOD_0117).** In order: (1) the RCA verdict —
  `get_rca()` / `noodle rca-report --compact` gives verdict + failing step
  + suggested fix in a few lines; (2) the failure message in the quiet-run
  summary; (3) only if still unexplained, the screenshot (vision tokens
  are ~10× text) or the network capture. Never dump the network capture to
  explain a timing/locator failure.
- **Reproduce, don't re-guess (NOOD_0144).** First mid-flow locator/state
  failure: replay the exact failing state once (`noodle probe <url> --do
  "<the actions so far>"`) and re-author every downstream step from that
  snapshot — one probe + one re-author beats N guessed-fix re-runs.
- **Cap the loop.** `NOODLE_DEV_FIX_ATTEMPTS` (default 10) — a ceiling for
  cause-backed fixes, not a licence for blind laps; after that, report
  flaky with the RCA verdict instead of grinding tokens.
- **Report spend.** Relay the `llm_cost` block after runs (playbook §6);
  "LLM cost: none" is the expected answer for pattern-matched runs.

---

## 5. Knob reference

| Env var | Default | Effect |
|---|---|---|
| `NOODLE_LLM_TEMPERATURE` | `0` | Sampling temperature for every engine model call. Empty string = omit the param. |
| `NOODLE_PROMPT_VOCAB` | (unset) | `full` = always send the whole step vocabulary in generation prompts. |
| `NOODLE_LLM_MAX_CALLS` / `NOODLE_RCA_MAX_CALLS` | `0` (unlimited) | Hard per-run model-call caps. |
| `NOODLE_DEV_FIX_ATTEMPTS` | `10` | Dev-loop fix→rerun cap before reporting flaky. |
| `NOODLE_MODEL` | (unset = no LLM, ever) | Engine-side model; see [llm-setup.md](llm-setup.md). |
| `NOODLE_QUIET` | (unset = auto: quiet when stdout isn't a TTY) | `1` forces `noodle run --quiet` behaviour, `0` forces the live stream (NOOD_0117). |

## 6. The generation budget (NOOD_0117)

The measured baseline that motivated the knobs above: one real search-test
generation cost **~109 AIC — 3.8M input tokens over 54 calls** (input:output
245:1), dominated by a ~40K-token per-call instruction floor and large tool
results (run logs, probe dumps, network captures) staying resident.

After the one-call authoring/run collapse (NOOD_0128/0129), the target for an
easy unfamiliar-web test — through served Allure + RCA reports — is **≤ 25 AIC
on the pinned benchmark host and model**. Treat that as a reported goal, not a
cross-model CI gate: the driving agent owns the instruction and history floor,
so the absolute AIC differs by host. What Noodle *can* hold — and what CI
guards — are the deterministic proxies that shape it:

- easy green path: **≤ 3 Noodle tool calls** (probe → `author_test` → `run_and_report`);
  one repair: **≤ 5**;
- **≤ 2 browser launches** on the green path (probe + run);
- **0 engine-side LLM calls** on the deterministic path;
- compact probe / quiet-summary / compact-RCA payloads under their byte
  ceilings; generated `AGENTS.md` **≤ 70 lines**.

`unit_tests/test_nood_0117.py::test_generation_budget_ceilings` guards the
framework-side artifact sizes (compact probe, quiet summary, compact RCA);
`test_nood_0128.py`/`test_nood_0129.py` guard the AGENTS.md ceiling and the
one-call authoring/readiness contract. All run in both CI pipelines (macOS +
Windows).

## 7. AIC is an architecture acceptance criterion (NOOD_0156)

A reviewed simple-flow session (navigate → search → pick → add-to-cart →
verify) burned **72.8 AIC entirely driving-agent-side** — ~20 browser
launches, 14 standalone probes, repeated help/grep calls, screenshot
interpretation for a locator failure the probe had already explained, and a
manual-Gherkin fallback that discarded the structured intent. A comparable
test previously cost **~17 AIC on the pinned benchmark model (Codex 5.3)**
despite having more steps. Budgets, on that pinned host/model:

- **AIC target ≤ 17** for a comparable simple flow; **hard ceiling ≤ 25** —
  a simple-flow result above 25 is rejected even when functionally green,
  and anything above 17 requires a cost-delta explanation (optimize the
  largest contributor first: repeated model inference, resident tool
  output, payload size, output verbosity).
- **Driving-agent model inferences ≤ 3**: request→goal, result handling,
  final response.
- **Noodle calls: 1** atomic `author_test(goal=…, run_after_author=true)`
  preferred; **max 2** when an explicit standalone probe is required.
- **Browser launches ≤ 2** (target 1 when authoring owns the probe).
- **Engine-side LLM calls: 0.** **Vision calls: 0** on the green path.
- **Repair runs: 0 on green; 1** only for a named, cause-backed engine gap
  — a blocked payload carries ONE typed `next_action` code precisely so
  the agent repairs that gap instead of exploring (no repeated probes, no
  probe-to-grep loops, no guessed manual `feature_content`).
- Agent-visible payloads stay compact: intent trace, blocker,
  `next_action`, summary, report URLs. Raw DOM / result-card / probe /
  network evidence lives in `artifacts/` (e.g. `probe_goal.json`) and is
  read only when the compact blocker doesn't explain the failure.

Because absolute AIC differs by model and host, CI enforces the
deterministic proxies (call counts, browser-launch counts, payload byte
ceilings, artifact-not-inline evidence —
`test_nood_0156.py::test_goal_payload_stays_compact_and_evidence_goes_to_artifact`)
alongside the measured pinned-model benchmark. Any change to an engine
surface an agent reads must state its before/after byte or token impact.
A feature that improves correctness but grows the driving agent's resident
context, adds a reasoning turn, or opens a new unbounded repair loop is
incomplete.

## 8. The instruction budget ledger — surfaces route, docs carry (NOOD_0159)

Every byte ceiling on an always-on instruction surface lives in **one
ledger**: `noodle/instruction_budget.py`, enforced by a single test
(`test_nood_0159.py`), which prints the whole used/cap/headroom table on
failure. The scattered per-ticket pins (0117/0126/0127/0128/0130/0131/0147)
are retired, along with line-count ceilings — bytes are the only unit,
because tokens track bytes, not lines.

The ceilings are permanent because of *what they protect*, so the permanent
way to add guidance is the *router* architecture (prior art:
GoogleChrome/modern-web-guidance, whose always-on card is ~230 tokens of
"search first, retrieve on demand"):

- **Surfaces route.** An always-on surface (AGENTS.md, skill cards, MCP
  instructions, hot docstrings) earns bytes only for the workflow contract,
  triggers, and pointers.
- **Docs carry.** The substance lands as a `docs/` section. Retrieval is
  already first-class: `read_docs()` lists every doc with its byte cost,
  `read_docs(query=…)` returns doc + section + line for a fact, and
  `read_docs(name=…, section=…)` returns one section (NOOD_0158). A doc
  section costs the reader nothing until the moment it is needed.
- **New guidance is therefore ~free on the surfaces**: a doc section plus,
  at most, one pointer/trigger line (the NOOD_0147 `log_diagnostic` pattern
  — name on the surface, vocabulary in the doc).
- **Raising a cap is allowed but never silent**: edit the ledger, and state
  the before/after bytes and why the content cannot be a doc section
  (the §7 acceptance rule) in the branch's CHANGELOG entry.

The anti-duplication shingle guard
(`test_nood_0131.py::test_no_workflow_paragraph_spreads_across_surfaces`)
stays: when a surface sheds bytes, the text *moves* to a doc — it is never
pasted onto a second surface.

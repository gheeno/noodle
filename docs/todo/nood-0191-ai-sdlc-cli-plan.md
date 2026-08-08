# NOOD_0191 — Noodle as the test stage of an AI SDLC (CLI-only, no MCP)

> **Status: §2, §3, §4, §7 and §9 SHIPPED in NOOD_0191.** See §10 for what
> landed and what's deliberately parked. This document stays as the reasoning
> behind those choices and the record of the deferred work (§5, §6).

The pipeline being built:

```
Confluence spec → intake agent → Jira tickets → builder agent writes the app
                                                          ↓
                          Noodle authors the test into the project repo   ← §2, §3
                                                          ↓
                       project's own pipeline runs it → Allure tab + RCA  ← §4
                                                          ↓
                                                  app merges to main
```

Three repos: **builder** (agent framework), **noodle** (this engine), **project**
(the app being built). Two distinct moments, and they have different needs:

| | Authoring (dev time) | Execution (CI time) |
|---|---|---|
| Driver | builder agent (an LLM) | Azure Pipeline, no LLM |
| Needs | a prompt door that negotiates (§2) | `noodle run` + reports (§4) |
| Output | `.feature` + POM committed to the project repo | Allure v3 tab, RCA, Tests tab |

---

## 1. Verdict — is Noodle AI-SDLC ready?

**Yes for the work; no for two doorways.** The engine is deterministic,
headless, JSON-emitting and CI-proven. Missing: one unambiguous task entry
point for the builder agent, and a pipeline template a project repo can consume
without copy-pasting 400 lines of hard-won Allure wiring.

| # | Criterion | Today | Gap |
|---|---|---|---|
| 1 | AI-SDLC ready without MCP | 🟡 | CLI covers every MCP tool (§1.1), but no single "here is a task, do it" command |
| 2 | Hostable in a pipeline | 🟡 | `azure-pipelines.yml` works but is engine-repo-shaped; a project repo can't consume it (§4) |
| 3 | Hostable as a container app | 🟡 | Image + Terraform exist, pinned to one replica — **deferred, not on the critical path** (§6) |
| 4 | Multiple concurrent callers | 🟡 | Solid per-workspace; not per-run-in-one-workspace — **only matters for §6** (§5) |
| 5 | Understands off-template prompts | 🟡 | Numbered English compiles deterministically; free-form prose has no route and no negotiation reply (§2) |
| 6 | Headed and headless | 🟢 | **No work needed** (§7) |
| 7 | Cheap, fast, accurate | 🟢 | Zero engine-side LLM calls on the happy path; `noodle benchmark --gate` asserts it |

### 1.1 What's already done (do not rebuild)

- **CLI ⟷ MCP parity is effectively complete.** Every MCP tool has a CLI twin:

  > **Superseded by NOOD_0241.** "Effectively complete" was not complete: a
  > shell-leak audit found `author_test`'s `app_name`/`base_url` and
  > `run_and_report`'s run-leg arguments had no CLI twins, and an agent
  > guessing them off the documented tool surface ate an exit-2 cascade.
  > Those flags now exist (`--app-name`/`--app`, `--base-url`,
  > `--headless`, `--retries`, `--serve-reports`), and parity is no longer a
  > claim to take on faith — `noodle capabilities --json` emits the
  > argument-by-argument manifest from the live signatures, with a CI test
  > that fails when the two surfaces drift. Treat the table below as
  > historical.

  | MCP tool | CLI |
  |---|---|
  | `author_test` | `noodle author --spec/--prompt --json [--run]` |
  | `run_and_report` / `run_test` | `noodle run --json --serve --headless --retries 0` |
  | `get_last_result` | `noodle summary --json` |
  | `get_rca` | `noodle rca-report --compact` |
  | `probe_page` / `probe_app` | `noodle probe` / `noodle probe-app` |
  | `inspect_locator` | `noodle inspect` |
  | `validate_feature` / `preflight` | `noodle validate` / `noodle run --preflight` |
  | `list_tests` / `search_step` / `vocabulary` | `noodle list` / `noodle step-search` / `noodle steps` |
  | `serve_report` / `stop_report_server` / `list_reports` | `noodle report serve` / `stop` / `list` |
  | `init_workspace` / `cost_estimate` / `read_docs` | `noodle init` / `noodle cost` / `noodle docs` |
  | `log_diagnostic` | `noodle diagnostic log` |

  Losing MCP costs the tool *discovery* affordance, not any capability.
- **Every `--json` door is byte-bounded** (`noodle/payload_budget.py`, 8 KB) and
  spills overflow to disk instead of through the caller.
- **A deterministic prompt compiler exists** (`noodle/repl/prompt_expander.py`):
  numbered English → typed goal in three pure passes, no LLM, with provenance
  for every inference and refuse-by-name for anything outside the grammar.
- **The whole CI reporting chain is solved** in `azure-pipelines.yml`: Allure 3
  single-file report (the `--single-file` requirement for the tab), trend
  history via `history.jsonl` + pipeline cache, RCA markdown on the run summary
  plus RCA HTML, JUnit → Tests tab, feature-file sharding. §4 is about making
  that consumable, not rebuilding it.

---

## 2. Authoring handoff — how the builder agent talks to Noodle

### 2.1 The problem with the current door

`noodle author --prompt` needs numbered steps containing a URL. A builder agent
is an LLM; it will send *"write me a regression test for the new checkout
discount and tell me if it passes"*. Today that gets `no URL in the prompt` and
the loop stalls, because nothing tells the caller **what shape to send instead**.

The instinct is to make the parser smarter. Wrong rung — the caller is already
an LLM. Noodle doesn't need to guess; it needs to be *precise about what it's
missing*, once, and let the caller re-emit.

### 2.2 `noodle task` — one door, five intents

```bash
noodle task "<any text>" --workspace <ws> --json
```

Deterministic keyword classifier → one of five intents, each dispatching to a
command that already exists. No new test-authoring logic.

| Intent | Triggers on | Dispatches to |
|---|---|---|
| `generate` | *write / create / generate / new test*, or **no match** (the SDLC default) | `noodle author --prompt … --run --json` |
| `update` | *update / fix / amend / change the test* + a resolvable existing feature | `noodle author … --overwrite --run --json` |
| `run` | *run / execute / re-run / smoke / regression* | `noodle run --json --serve` |
| `report` | *report / allure / rca / show me the results* | `noodle report serve` + `noodle rca-report --compact` |
| `verdict` | *did it pass / verdict / benchmark / is it green* | `noodle summary --json` |

One envelope out, whatever the intent:

```json
{
  "ok": true, "intent": "generate", "confidence": "explicit|default",
  "workspace": "…/tests/noodle",
  "feature": "noodle_tests/checkout/features/discount.feature",
  "run": {"passed": 3, "failed": 0, "verified": true},
  "reports": ["http://…/allure/index.html", "http://…/rca.html"],
  "verdict": "PASS", "next": null
}
```

### 2.3 The negotiation contract (the load-bearing part)

When the text can't be compiled, Noodle must **not** guess and must **not**
silently reach for a model. It returns a machine-readable ask:

```json
{
  "ok": false, "intent": "generate",
  "need": ["base_url"],
  "unresolved": [{"clause": "c3", "reason": "no recognizable verb"}],
  "grammar": "go to <url>; search for <term>; click <name>; …",
  "example": {"goal": {"scenario": "…", "actions": [], "checks": []}},
  "next": "re-send with a URL and numbered steps, or send goal: {…}"
}
```

The caller is an LLM with the app it just built in context — it can fill `need`
on the second try. Converges in one round trip, costs Noodle zero inference,
keeps determinism (the product) intact. `model_fallback` stays opt-in via
`NOODLE_MODEL`, never first.

`noodle task --contract --json` emits the grammar + envelope schema + the five
intents as one payload, so an agent that isn't Claude Code or Copilot can fetch
the contract it can't get from the `.claude/skills/` card. Flag on the same
command, not a new one.

**"Is Noodle smart enough for any prompt?"** — it doesn't have to be. It has to
be unambiguous about what it needs. That's a bounded amount of code; open-ended
prompt understanding is not.

**Size:** `noodle/repl/task.py` ≈ 150 lines + one CLI command + a unit test over
a table of ~20 real off-template prompts.

---

## 3. Where the generated tests live

**The project repo owns its tests**, in a workspace committed during
development — exactly the shape the execution flow (§4) assumes:

```
<project repo>/
  src/…                     ← builder agent's application code
  azure-pipelines.yml       ← the project's own pipeline (§4)
  tests/noodle/             ← `noodle init` ran here, committed
    noodle.yaml  .env
    noodle_tests/<app>/features/*.feature
    noodle_tests/<app>/resources/pageobjects/*.yaml
```

The builder agent already has this repo checked out — it just wrote the app
there. So Noodle scaffolds and authors **into that same checkout**:

```bash
noodle init "$PROJECT_ROOT/tests/noodle"
noodle task "<task text>" --workspace "$PROJECT_ROOT/tests/noodle" --json
```

The test lands in the same branch/PR as the feature it tests, and the project's
own pipeline gates it. No cross-repo push, no PAT, no service connection, no
"I pushed and nothing triggered" — which deletes the push-to-tests-repo +
queue-by-REST dance currently documented in
[ai-sdlc-integration.md §5–6](../ai-sdlc-integration.md#5-the-full-loop-generate--run--allure-tab).

**Never** in the noodle engine repo — it's generic. For a pre-project-repo POC,
`regression_runs/projects/<project>/` needs zero change (`regression_runs/` is
already gitignored); just the convention.

A **dedicated per-project tests repo** stays supported (the existing
`useExternalTestsRepo` pipeline parameters) but is demoted: an extra repo per
project means extra permissions, extra triggers, and version skew between app
and test. Keep it only for teams whose app repo is locked down.

**Deliverable:** rewrite ai-sdlc-integration.md §3/§5/§6 around this shape.

---

## 4. Execution — the project repo's pipeline (the primary flow)

> Project repo's pipeline → clones noodle → installs it on the Linux agent →
> runs the workspace already committed in that repo → Allure v3 tab + RCA.
> **No LLMs at execution time, just `noodle` commands.**

### 4.1 The problem

`azure-pipelines.yml` already does every hard part, but it's engine-repo-shaped:
`checkout: self` is *noodle*, defaults point at `sample_feature_tests/`, and it
carries an engine unit-test gate and BusterBlock startup steps a project repo
doesn't want. The external-tests-repo mode is the *inverse* of what's needed —
it makes noodle's pipeline pull the tests, not the project's pipeline pull noodle.

Telling projects to copy the YAML is the wrong answer: every Allure fix (the
`--single-file` tab requirement, the pinned CLI, the history `tail`) would have
to be re-applied in N project repos.

### 4.2 The fix — one consumable template

Add `ci/azure/noodle-tests.yml` to this repo. A project repo consumes it in
about twelve lines:

```yaml
# <project repo>/azure-pipelines.yml
resources:
  repositories:
    - repository: noodle
      type: git                    # or 'github' + endpoint
      name: Tooling/noodle
      ref: refs/tags/1.0.0a7       # pin the engine — see §4.5

jobs:
  # …the project's own build/deploy jobs…
  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      workspaceDir: tests/noodle   # where `noodle init` was run, committed
      testTag: regression
      keyVaultUrl: $(NOODLE_KEYVAULT_URL)
```

`jobs:`-level template, not `extends:` — the project's pipeline keeps its own
build and deploy jobs and adds Noodle as a stage alongside them.

**Parameters:**

| Param | Default | Why |
|---|---|---|
| `workspaceDir` | `tests/noodle` | project-repo-relative dir holding `noodle.yaml` |
| `testTag` | `''` | `--tag smoke` etc. |
| `pool` | `{vmImage: ubuntu-latest}` | corp self-hosted agents pass their own pool name |
| `shard` | `true` | one agent per `.feature`; `false` = one job for the suite |
| `maxParallel` | `4` | matrix width |
| `parallelProcesses` | `0` | in-job behavex, for folder-per-shard runs |
| `pythonVersion` | `3.11` | |
| `extras` | `''` | e.g. `[visual]` only if the suite uses `@visual` |
| `keyVaultUrl` | `''` | **one** secrets knob (§4.4) |
| `secretEnv` | `{}` | explicit map for teams not on Key Vault |

**What the template does** — a stripped, parameterized version of the jobs that
already work today: checkout self + noodle → Python → restore browser cache →
install engine → install Allure CLI → discover shards from the *project's*
workspace → `noodle run --headless` → Allure single-file + history → RCA
md/html → JUnit → publish tab and artifacts.

### 4.3 Install line — smaller than the engine's own

The engine pipeline installs `.[all,llm]` because it gates the unit suite. A
project running tests needs far less:

```bash
pip install ./noodle -c ./noodle/constraints.txt   # base deps, pinned
playwright install chromium --with-deps
```

Non-editable, base deps only, pinned by the engine's own `constraints.txt` (so
the project repo pins one thing — the noodle `ref:` — and dependency pinning
rides along). `extras` stays a parameter for `@visual`/`@appium` suites.

Two findings while checking this:
- `allure-python-commons` (the `reporting` extra) is **imported nowhere** in the
  engine — the report is generated by the npm `allure` CLI from result JSON we
  write ourselves. Candidate for deletion from `pyproject.toml`, separate ticket.
- `noodle update` exists and is the right post-`git pull` command locally, but
  CI installs fresh every run, so it plays no part here.

### 4.4 Secrets — one knob, not N

Azure DevOps exposes non-secret variable-group values as environment variables
automatically; **secret** ones are not, they must be mapped explicitly. The
current pipeline hardcodes `BASE_URL`, `MY_EMAIL`, `MY_CARD`… which is exactly
wrong for a reusable template — every project has different keys.

Recommended: the project sets `NOODLE_KEYVAULT_URL` and grants the agent
identity `get`/`list` on a Key Vault. The engine already resolves secrets from
Key Vault (`noodle/secrets_akv.py`), so the template maps **one** variable
instead of a per-project list. `secretEnv` stays as the explicit escape hatch.

### 4.5 Self-hosted corp agent hardening (port, don't rediscover)

Three things will break the first run on a locked-down self-hosted agent. All
three are already solved in the corp job-runner pipeline shared during planning;
carry them into the template rather than learning them again:

1. **`sudo npm install -g allure` assumes sudo.** Today's pipeline uses it.
   Install by exact version into a workspace-local dir instead:
   `npm install --no-save --no-audit --prefix "$CLI_DIR" allure@3.14.1`.
2. **The `allure` bin name gets shadowed.** Agent-local npm registries have
   resolved `allure` to the wrong package. Invoke the **resolved bin path**
   (`$CLI_DIR/node_modules/.bin/allure`), never `allure` on `PATH`, and assert
   `--version` equals the pin — fail loudly, don't silently generate nothing.
3. **Self-hosted agents ship an ancient Node** (v10 seen), and Allure 3's deps
   need ≥ 20; `UseNode@1`'s tool-cache download is unreliable there. Provision a
   portable Node 20 tarball into a cached workspace dir when `node -v` < 20.

Plus the caches that make repeat runs cheap: `PLAYWRIGHT_BROWSERS_PATH` (keyed
on `constraints.txt`), the npm cache, the portable-Node extract, and Allure
`history.jsonl` (keyed on `Build.BuildId` with a `restoreKeys` prefix — the
existing trick).

**Version pinning:** `ref: refs/tags/<version>`. This repo's tags are currently
inconsistent (`1.0.0a4` alongside `v0.2.0a1`) — pick one spelling and tag every
release, or project repos have nothing stable to point at.

### 4.6 What the project team gets on a run

Unchanged from what the engine pipeline produces today: the **Allure Report**
tab (needs `qameta.allure-azure-pipelines` installed once at org level), the
**Tests** tab from JUnit, the RCA markdown on the run summary plus `rca.html`,
and screenshots/traces/videos in `TestArtifacts-*`.

---

## 5. Concurrency — findings, parked

Only matters if Noodle is ever hosted as a shared service (§6). In the §4 flow
each pipeline run is its own agent with its own checkout, so it's already
isolated. Recording the findings so they're not re-derived:

| Scope | Isolated? | Evidence |
|---|---|---|
| Two runs, **different workspaces** | ✅ mostly | `.noodle/last_run_root`, `.noodle/report_servers.json`, `artifacts/`, `diag_state.json` are all `Path(workspace)/…` |
| Two runs, **same workspace** | ❌ | both write `artifacts/allure-results`, `artifacts/last_run.json` and the last-run pointer — last writer wins, reports interleave |
| CLI process CWD leaks | ⚠️ | `_write_full_payload` → `Path(".noodle")/last_payload.json` and `runlock._CONTROL` are CWD-relative, not workspace-relative |
| Ports | ✅ | report servers bind `:0`, register in a per-workspace pidfile, and a live server for the same root is reused |

Nothing in the engine holds a global lock, a singleton browser, or a shared
session — so the fix is **one workspace per request**, which every command
already supports, not a scheduler. Ceiling to state when the time comes:
≈ 1 vCPU and 1.5 GB per concurrent headless run — the browser, not Noodle.

Deferred work, ~40 LOC: workspace-scope `_write_full_payload` and
`runlock._CONTROL`; have `noodle run` take the existing `runlock` mutex on its
artifacts root and refuse with a clear message when another run holds it.

---

## 6. Container app — deferred option

Kept as a future route, not built now. If it happens:

- The `Dockerfile` already `ENTRYPOINT ["noodle"]` and the Terraform under
  `infra/terraform/azure-container-apps/` stands up ACR + Container App + Azure
  Files, pinned `min_replicas = max_replicas = 1` because `noodle-mcp` keeps
  "the last test" on local disk.
- With §5's fixes and a workspace per request, that pin lifts.
- **Container Apps *Jobs* beat a long-lived server** for this shape: one
  execution per request, platform-managed parallelism, retry and isolation, and
  zero new code — the image already runs `noodle` as its entrypoint.
- A stdlib HTTP shim (`ThreadingHTTPServer`, ~100 lines, bearer token from
  `NOODLE_API_KEY`, synchronous `POST /task`) is only worth building if a human
  tester genuinely needs to hit a URL. Its ceiling is the ingress timeout.

---

## 7. Headed vs headless — no work needed

- **Container and pipeline: headless only.** `Dockerfile` sets
  `NOODLE_HEADLESS=true`; the pipeline passes `--headless`. Nobody can watch a
  browser inside a CI agent, so headed there has no purpose.
- **Headed is the local demo path** — `noodle run --headed` on a developer's or
  tester's own machine. Works today, no change.
- The template should therefore drop the `--headed` pipeline parameter and the
  Xvfb startup the current YAML carries. One less knob, one less failure mode.
- If a run *does* need to be watched after the fact, `@record_video` and trace
  capture already attach to the Allure report.

---

## 8. Gaps and weaknesses (ranked)

| # | Gap | Impact | Fix |
|---|---|---|---|
| 1 | No pipeline template a project repo can consume | Every project copy-pastes 400 lines and forks the Allure wiring | §4.2 |
| 2 | No single task entry point; free-form prompts have no route and no negotiation reply | Authoring loop stalls on the first off-template prompt | §2 |
| 3 | Delivery to the project repo undocumented; docs assume a separate tests repo + REST queue | Integrators build the complicated version | §3 rewrite |
| 4 | Template would need `sudo npm -g`, `allure` on PATH, and modern Node | First run on a locked-down corp agent fails three ways | §4.5 |
| 5 | Secrets hardcoded per-key in the pipeline | Not reusable across projects | §4.4 Key Vault as the one knob |
| 6 | Release tags inconsistent | Project repos have no stable `ref:` to pin | §4.5 — standardize and tag every release |
| 7 | Skill card only reaches Claude Code / Copilot | A third-party agent can't fetch the contract | `noodle task --contract --json` |
| 8 | Same-workspace run collisions | Wrong verdict, silently | §5 — parked until §6 |
| 9 | `allure-python-commons` dependency is imported nowhere | Dead weight in every install | Separate ticket |

**Explicitly not doing:** an autonomous retry-until-green agent inside Noodle.
Determinism is the product — a framework that keeps trying until the test passes
is a flaky-test generator. The reasoning loop stays in the caller.

---

## 9. Public-safety redaction (done in this branch)

Employer-specific references removed from the public repo:

- `docs/benchmark.md` — the live-drill retail prompts → site-neutral
  templates with `https://<your-retail-site>/…` placeholders.
- `noodle/regression.py` — comment reference → "the original retail-store pair".
- `CHANGELOG.md` — the same phrase in the NOOD_0185 entry.

Verified clean: no corp agent-pool names (`azure-pipelines.yml` uses
`vmImage: ubuntu-latest`), no internal hostnames, no org names, no PATs. The
sample corp pipeline shared during planning was **not** copied into this repo —
only its three agent-hardening lessons (§4.5), described generically.
`.squad/templates/` hits are the upstream Squad tool's own vendored files.

---

## 10. Phasing

### Shipped in NOOD_0191

| Work | Where |
|---|---|
| `noodle task` + negotiation contract + `--contract` (§2) | `noodle/repl/task.py`, `noodle task` |
| Consumable pipeline template + hardening (§4) | `ci/azure/noodle-tests.yml`, `ci/azure/steps-allure-cli.yml`, `ci/azure/example-project-pipeline.yml` |
| Same hardening back-ported to this repo's pipeline (§4.5) | `azure-pipelines.yml` |
| Headless-only CI, headed stays local (§7) | template has no `--headed`; a test keeps it that way |
| Project-repo delivery documented (§3) | `docs/ci-project-repo.md`; `ai-sdlc-integration.md` §3/§4.0/§5/§6 rewritten |
| Tag convention `1.0.0aN` → `1.0.0bN` → `1.0.0` (§4.5) | `CONTRIBUTING.md`; obsolete `v`-prefixed tag deleted |
| Public-safety redaction (§9) | `docs/benchmark.md`, `noodle/regression.py`, `CHANGELOG.md` |
| Routing table + pipeline drift guards | `unit_tests/test_nood_0191.py` |

### Deliberately not done

| Work | Why parked |
|---|---|
| Concurrency hardening (§5) | Only matters if Noodle becomes a shared service. In the shipped flow each pipeline run owns its agent and checkout, so it is already isolated. Findings recorded above so they aren't re-derived. |
| Container app / HTTP front door (§6) | The pipeline path covers the stated need with no new service. If the container route is taken later, §5 lands first and Container Apps *Jobs* beat a long-lived server. |
| An off-template prompt case in `benchmark --gate` | Worth adding once real builder-agent prompts exist to draw from — a fixture invented here would only test the classifier against itself. |
| `allure-python-commons` removal | It is imported nowhere, but deleting a dependency is its own ticket with its own install-matrix check. |
| `azure-pipelines-windows.yml` parity | The template is bash/POSIX. Windows agents keep the existing pipeline; the version-pin guard still covers it. |

**Cost stance (criterion 7):** the execution path makes **zero** LLM calls by
construction. The authoring path stays at zero engine-side calls too — the
classifier and the prompt compiler are both deterministic;
`noodle benchmark --gate`'s zero-cost guard (NOOD_0189) is the falsifiable
assertion, and P4 extends it to the new door.

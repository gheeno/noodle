# Running Noodle from a project repo's own pipeline
<!-- Branch: NOOD_0191 -->

> **For:** a team building an application who wants its Noodle tests to run in
> that application's own Azure DevOps pipeline, gating its own PRs.

The shape:

```
project pipeline → clones the noodle engine → installs it on the agent
                 → runs the workspace already committed in the project repo
                 → Allure Report tab + Tests tab + RCA
```

No LLM is involved. This is `noodle run` and nothing else.

---

## 1. Why the tests live in the project repo

The test belongs in the same branch and the same pull request as the feature
it tests. That gets you three things for free that a separate tests repo has
to work for: the test versions with the app, the app's own PR gate covers it,
and there is no cross-repo push, PAT, service connection, or "I pushed and
nothing triggered".

Three repos are in play and each owns exactly one thing:

| Repo | Owns |
|---|---|
| **noodle** (this one) | the engine, and the pipeline template below |
| **project** | the application *and its tests* |
| *(optional)* builder/agent | whatever generates code and tests |

Tests never go in the noodle engine repo — it's generic, and one team's
`.feature` files don't belong there.

## 2. One-time setup

**In the project repo**, scaffold a workspace and commit it:

```bash
noodle init tests/noodle
git add tests/noodle && git commit -m "add noodle test workspace"
```

That directory holds `noodle.yaml`, `.env`, and `noodle_tests/` — see
[workspace-guide.md](workspace-guide.md). Nothing engine-specific goes in it.

**In Azure DevOps**, once per organization: install the free **Allure Report**
extension (`qameta.allure-azure-pipelines`) at *organization* level. Without
it the tests still run and publish artifacts, but there is no browsable Allure
Report tab.

## 3. Wire the pipeline

Add two blocks to the project's `azure-pipelines.yml`. A complete file to copy
is [`ci/azure/example-project-pipeline.yml`](../ci/azure/example-project-pipeline.yml).

```yaml
resources:
  repositories:
    - repository: noodle
      type: git                    # 'github' for a GitHub-hosted engine
      name: Tooling/noodle         # <Project>/<Repo> in Azure Repos
      ref: refs/tags/1.0.0a33       # pin it — a moving ref is not a build

jobs:
  # …the project's own build / deploy jobs stay as they are…

  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      workspaceDir: tests/noodle
```

That's the whole integration. The template checks out both repos, installs the
engine pinned by its own `constraints.txt`, installs the Allure 3 CLI, runs the
whole suite headless in **one job** (4 behavex processes inside it), and
publishes everything.

### Parameters

| Param | Default | What it's for |
|---|---|---|
| `workspaceDir` | `tests/noodle` | the dir holding `noodle.yaml`, repo-relative — `''` when the repo root *is* the workspace |
| `testTag` | `all` | only run scenarios with this tag; `all` (or `''`) = the whole suite |
| `pool` | `{vmImage: ubuntu-latest}` | pass your own for a self-hosted pool |
| `shard` | `false` | opt in to one agent per `.feature` — see below |
| `maxParallel` | `4` | matrix width, when `shard: true` |
| `parallelProcesses` | `4` | behavex processes *within* the job (`0` = single process) |
| `pythonVersion` | `3.11` | |
| `extras` | `''` | e.g. `'[visual]'` — only if the suite uses `@visual`/`@appium` |
| `keyVaultUrl` | `''` | the one secrets knob (§4) |
| `secretEnv` | `{}` | explicit `KEY: $(VAR)` map, if not using Key Vault |
| `allureVersion` | `3.14.1` | pinned Allure 3 CLI |
| `historyRuns` | `30` | trend runs retained |
| `publishAllureTab` | `true` | publish the Allure tab (needs the org-level `qameta.allure-azure-pipelines` extension; the Tests tab and the artifacts publish regardless) |
| `jobName` | `noodle_tests` | prefix, if you need two of these in one pipeline |

**Why one job.** Sharding scales linearly in *jobs*, which is the wrong axis: a
29-feature suite listed 31 jobs, and 1000 features would list 1002 — unreadable,
and every job pays the full setup (two checkouts, `pip install`, browser
download, Allure CLI ≈ 60–90 s) to run one file. `parallelProcesses` gets the
wall-clock inside one job and pays setup once. Turn `shard: true` on when a
single agent genuinely can no longer finish the suite in time; it is a scaling
lever, not the default shape.

**`testTag` is the word `all`, not blank.** Azure's Run-pipeline panel will not
advance past an empty runtime string parameter, so a blank default made the
whole-suite manual run — the common case — literally unreachable. The template
translates `all` (any case) *or* `''` to no `--tag` flag.

**The tag has to exist.** `ref:` resolves against the *engine* repo, so
whoever releases the engine tags it there — bumping `pyproject.toml` is not
enough, and in Azure Repos an unresolvable ref fails the pipeline at
compile time (before any job runs, so there's no log to read). One command per
release, in the engine clone:

```bash
git tag 1.0.0a9 && git push origin 1.0.0a9      # no 'v' prefix
```

Without tags, every project ends up tracking a branch, and "which engine ran
this build?" stops having an answer. `/api/health` and `noodle --version` report
what a given agent actually installed.

**Pin the engine.** `ref: refs/tags/<version>` — versions are `1.0.0aN` during
alpha, `1.0.0bN` in beta, then `1.0.0`. No `v` prefix. Bump the ref
deliberately; a project that tracks a branch is not running a reproducible
build.

### Which engine build actually runs

**Azure does not match branch names across repos.** The branch chosen when the
pipeline runs applies to `self` — your repo — only. The engine version comes
from `resources.repositories.ref` and nowhere else; omit `ref` and Azure checks
out the engine's *default* branch whatever your repo is on. So a project on
`main` will happily run an engine feature branch, and a project on a feature
branch does **not** get an engine branch of the same name.

That is the right design — the engine is versioned product code, the tests are
content, and they release on different clocks — but it means "which engine ran"
is a decision someone makes in YAML, not something inferred.

Make the ref a parameter and trialling an engine build needs no commit:

```yaml
parameters:
  - name: noodleRef
    displayName: 'Engine version — refs/tags/<version>, or refs/heads/<branch> to try a build'
    type: string
    default: refs/tags/<version>

resources:
  repositories:
    - repository: noodle
      ref: ${{ parameters.noodleRef }}
```

Parameters expand at compile time, which is exactly when Azure resolves the
`@noodle` template — so this selects the *template* version too, not just the
checkout. (`noodle init` scaffolds this shape; see §8.) Keep the committed
default a tag: a branch is not a build, and a suite that passed against a branch
proves nothing repeatable.

Three places record what actually ran: the Run panel's `noodleRef` and its
Resources tab, the run summary's Repositories section (and the `Checkout` step
log), and the job log's **Engine build provenance** step, which prints the
engine checkout's SHA, any tags pointing at it, and the head commit subject.
Compare `git rev-parse <the tag you pinned>` locally against `engine sha` there.

That step exists because **`noodle --version` does not carry the SHA in CI**.
The banner appends `@ <sha>` from a walk up from the *package* directory looking
for `.git` — and CI `pip install`s a non-editable copy into `site-packages` with
no repo above it, so it silently prints no SHA. An editable local install *does*
print one, which is what makes the omission easy to miss. The provenance step
reads the checkout, which always has `.git`.

## 4. Secrets — one knob

Azure DevOps exposes **non-secret** variable-group values as environment
variables automatically. **Secret** ones are not — they have to be mapped by
hand. Rather than make every project list its own keys in the template, point
the engine at a Key Vault:

```yaml
      keyVaultUrl: $(NOODLE_KEYVAULT_URL)
```

Grant the agent identity `get` + `list` on that vault and the engine resolves
every key from it at run time. One variable to map instead of a per-project
list.

Key Vault needs the engine's `azure` extra, and the template installs it for
you when `keyVaultUrl` is set — you don't also pass `extras: '[azure]'`
(NOOD_0193; before this the install went green and the run then died at
`before_all` with "the Azure SDK is missing"). An unset variable that Azure
leaves as the literal `$(NOODLE_KEYVAULT_URL)` counts as "no vault", exactly as
the engine reads it.

Not on Key Vault? Use the escape hatch:

```yaml
      secretEnv:
        APP_PASSWORD: $(APP_PASSWORD)
        API_KEY: $(API_KEY)
```

## 5. What a run produces

Three surfaces, each carrying steps, evidence **and** logs:

| Surface | Steps | Evidence | Logs |
|---|---|---|---|
| **Tests** tab | the step list with status + timing, in `system-out` | every screenshot, as an `[[ATTACHMENT\|…]]` marker | that scenario's `run log`, same marker |
| **Allure Report** tab | native | native attachments | `run log` attachment |
| `NoodleTestArtifacts-*` | — | `screenshots/`, `traces/`, `videos/`, `network/` | `logs/*.log` (one per scenario) + `run.log` |

Plus the RCA markdown for every failure on the run summary, and the single-file
`reports/allure-report/index.html` inside that artifact — the full report opens
offline from the download, with no extension installed anywhere.

**Per-scenario logs** (NOOD_0205): the run also writes one whole-suite
`run.log`, but it is not sliceable, and under `parallelProcesses` it is
interleaved across workers. `artifacts/logs/<scenario>.log` is that one
scenario's lines only, attached to its own result — so a report tells you not
just *that* a scenario failed and what it looked like, but what the engine was
doing while it ran.

With `shard: true` the per-shard reports are merged by an extra job and the tab
comes from there instead; a `NoodleAllureReport` artifact carries the merged
single-file report.

## 6. Headless only, on purpose

CI runs headless. Nobody can watch a browser inside a build agent, so a
`--headed` knob there is a failure mode with no upside — the template doesn't
have one.

Headed is the **local** path: `noodle run --headed` on a developer's or
tester's own machine, for demos and debugging. If a CI run needs watching
after the fact, tag the scenario `@record_video` — the recording attaches to
the Allure report.

## 7. Self-hosted agents

The template already handles the three things that break a first run on a
locked-down corporate agent, so you shouldn't have to discover them:

1. **No `sudo`.** Nothing Noodle needs is installed globally; the Allure CLI
   goes into a workspace-local directory. The one step that *would* want root
   is `playwright install --with-deps`, which apt-gets the browser's system
   libraries — so the template only uses it when passwordless `sudo` is
   actually available, and otherwise downloads the browser alone and warns
   (NOOD_0193). If Chromium then fails to launch, the agent image is missing
   those libs: have an admin run `playwright install-deps` on it **once**.
   MS-hosted `ubuntu-latest` needs none of this.
2. **`allure` bin-name collisions.** An agent-local npm registry can shadow
   the `allure` package name. The template installs by exact version and
   invokes the *resolved* binary, failing loudly if its `--version` doesn't
   match the pin — rather than silently generating no report.
3. **Ancient Node.** Self-hosted agents have shipped Node 10; Allure 3 needs
   ≥ 20. A portable Node is provisioned (and cached) when the agent's is too
   old.

Caches keep repeat runs cheap: Playwright browsers (keyed on the engine's
`constraints.txt`), npm, the portable Node extract, and Allure trend history.

**Cross-project gotcha:** if the noodle repo lives in a different Azure DevOps
*project* of the same organization, the resource `name:` must be
`OtherProject/noodle` — a bare repo name only resolves within one project —
and the pipeline's build service account needs read access there.

## 8. Greenfield — a standalone test repo, from `noodle init` to a green run

Everything above assumes the workspace is a folder inside an application repo.
A repo whose **root is the workspace** is what `noodle init` scaffolds by
default, and it is the shorter path — three shell lines, then five clicks.

```bash
noodle init <path>              # or `noodle init .` in an empty clone
cd <path> && git init
git add . && git commit -m "noodle test workspace"
```

`init` writes `azure-pipelines/azure-pipelines.yml` — a folder, because a repo
grows more than one pipeline (nightly, PR, release). That file is the **only**
CI file the project owns: every step lives in the engine template, so upgrading
CI is a version bump, not a diff. It is a *template* file in the scaffold's
ownership model — written when missing, refreshed only by `noodle init --force`
(old copy kept as `.bak`), never silently overwritten. Editing it later —
schedules, tag filters, speed, pools, secrets —
→ [ci-workspace-pipeline.md](ci-workspace-pipeline.md).

### Fill the placeholders first

| In the scaffolded file | Replace with | Fails how |
|---|---|---|
| `name: REPLACE_ME/noodle` | `<Project>/<Repo>` of the engine repo (a bare repo name only resolves within one project) | compile time |
| `default: refs/tags/REPLACE_ME` | the engine version to pin, e.g. `refs/tags/1.0.0a33` | compile time |
| `workspaceDir: ''` | leave empty for a root workspace; a path when nested | run time |

Both compile-time failures happen *before any job exists*, so there is no job
log to read — which makes them the worst possible first-run failure and the
first thing to check.

### In Azure DevOps

1. **Create the pipeline** pointing at `/azure-pipelines/azure-pipelines.yml`.
   Azure stores the YAML path in the pipeline definition, not in the repo — if
   you move the file later, edit the pipeline too (Edit → ⋮ → Triggers → YAML →
   path), or the run keeps using the old path.
2. **Grant this pipeline Read on the engine repo** — Project Settings →
   Repositories → the engine repo → Security → `<Project> Build Service`.
   Without it the run dies at compile time.
3. **Install `qameta.allure-azure-pipelines`** once per organization, for the
   Allure tab. Optional — or set `publishAllureTab: false`.
4. **Wire secrets** per §4 — Key Vault, or `secretEnv` from a variable group.
   Never a secret value in the pipeline file.
5. **Run it.**

### What the first green run looks like

Exactly **one** `Noodle tests` job. A Tests tab with every scenario's steps,
screenshots and its own run log; an Allure Report tab; the RCA on the run
summary; and a `NoodleTestArtifacts-*` download that opens the whole report
offline (§5).

Triage: a failure with no job log at all is a placeholder. A failure inside the
job has an RCA on the summary and a `run log` on the scenario.

### Releasing the engine so a project can pin it

`ref:` resolves against **tags in the engine repo** — bumping `pyproject.toml`
is not enough. One command per release, in the engine clone:

```bash
git tag 1.0.0a21 && git push origin 1.0.0a21      # no 'v' prefix
```

## 9. Related

- **Editing the workspace pipeline afterwards** — recipes for every common
  change (engine version, tag filter, nightly schedule, speed, pool, secrets)
  plus a troubleshooting table →
  [ci-workspace-pipeline.md](ci-workspace-pipeline.md)
- Engine-repo-owned CI, the mirror image of this page, plus the external
  tests-repo parameters → [manual.md § Running in CI](manual.md#running-in-ci--azure-devops)
- Sharding, per-shard isolation, Key Vault detail →
  [encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops)
- Wiring an AI agent that *authors* the tests →
  [ai-sdlc-integration.md](ai-sdlc-integration.md)

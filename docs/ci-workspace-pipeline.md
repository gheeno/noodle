# Editing your workspace's Azure pipeline
<!-- Branch: NOOD_0205 -->

> **For:** whoever owns `azure-pipelines/azure-pipelines.yml` in a Noodle
> workspace — the file `noodle init` scaffolds. This is the reference for
> *editing* it. For the one-time Azure DevOps setup, see
> [ci-project-repo.md § 8](ci-project-repo.md#8-greenfield--a-standalone-test-repo-from-noodle-init-to-a-green-run).

## What this file is

It is the **only** CI file your repo owns, and it is deliberately tiny: a
trigger, two parameters, one repository resource, one template reference. Every
*step* — checkout, Python, `pip install`, browser download, Allure CLI, the run
itself, and all three publish surfaces — lives in the engine's own
`ci/azure/noodle-tests.yml`. So upgrading CI is a version bump here, not a diff.

What that means in practice:

| You want to change | Edit |
|---|---|
| when the pipeline runs, what it's called, which parameters it passes | **this file** |
| what a run *does* — install, browsers, publish, report shape | the engine template (a PR to the engine repo) |
| what the tests do | your `.feature` files and POMs |

If you find yourself wanting to add a `- bash:` step here to fix a run, that is
almost always a sign the engine template should grow the capability instead —
otherwise every workspace re-invents it and none of them get the fix.

## Anatomy

```yaml
trigger:                                  # ① when it runs
  - main

parameters:                               # ② what a human can pick at Run time
  - name: noodleRef
    default: refs/tags/1.0.0a28
  - name: testTag
    default: all

resources:                                # ③ which engine build to use
  repositories:
    - repository: noodle
      type: git
      name: <Project>/noodle
      ref: ${{ parameters.noodleRef }}

jobs:                                     # ④ hand off to the engine template
  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      workspaceDir: ''
      testTag: ${{ parameters.testTag }}
      parallelProcesses: 4
```

**The three values that must be right before the first run:**

| Value | Set it to | If it's wrong |
|---|---|---|
| `name:` | the engine repo as `<Project>/<Repo>` — a bare repo name only resolves inside one project | fails at **compile** time — no job, no log |
| `noodleRef` default | `refs/tags/<version>` of the engine you want | fails at **compile** time |
| `workspaceDir` | `''` when this repo's root is the workspace; a path (`tests/noodle`) when it's a folder inside a bigger repo | fails at run time — "no `noodle.yaml`" |

`noodle init` writes `REPLACE_ME` for the first two on purpose, so an unfilled
placeholder is obvious rather than subtly wrong.

**One more thing lives outside the repo:** Azure stores the *path* to this YAML
in the pipeline definition, not in git. Move or rename the file and you must
also edit the pipeline (Edit → ⋮ → Triggers → YAML → path), or the run keeps
using the old path.

---

## Recipes

### Run a different engine version

```yaml
      default: refs/tags/1.0.0a28      # ← the pinned version
```

`ref:` resolves against **tags in the engine repo**, so bumping the engine's
`pyproject.toml` is not enough — somebody has to `git tag` and push it. Keep the
committed default a tag: a branch is not a build.

To **trial** an engine branch without a commit, leave the default alone and type
`refs/heads/<branch>` into the `noodleRef` box in the Run panel. Parameters
expand at compile time, which is when Azure resolves the `@noodle` template — so
this swaps the *template* too, not just the checkout.

Azure does **not** match branch names across repos: the branch you pick when
running applies to your repo only. Details and how to confirm which build
actually ran →
[ci-project-repo.md § Which engine build actually runs](ci-project-repo.md#which-engine-build-actually-runs).

### Run only some of the tests

```yaml
      default: all          # 'all' (any case) or '' = the whole suite
```

Anything else is passed to `noodle run --tag`, so it's a Gherkin tag without the
`@`: `smoke`, `api`, `regression`.

Keep the default the word `all`, not blank — **Azure's Run panel refuses to
advance past an empty runtime string parameter**, so a blank default makes the
manual whole-suite run unreachable.

### Add a nightly full run next to a fast PR gate

Two pipelines, one file each, both pointing at the same engine template — that
is why `noodle init` puts the file in an `azure-pipelines/` *folder*.

```yaml
# azure-pipelines/nightly.yml
trigger: none
schedules:
  - cron: '0 2 * * *'
    displayName: 'Nightly — full suite'
    branches: {include: [main]}
    always: true

jobs:
  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      workspaceDir: ''
      jobName: nightly            # so both can coexist in one run if merged
```

Create a second pipeline definition in Azure pointing at the new path. A PR gate
usually pairs with `testTag: smoke` and branch policies; the nightly runs
everything.

### Make it faster

```yaml
      parallelProcesses: 4         # behavex worker processes inside the one job
```

Start here: it costs nothing extra — one agent, one setup. Raise it toward the
agent's core count while the suite still passes; scenarios that share state need
`@serial` or `@lock:<name>` regardless of how you parallelise.

Only when a single agent genuinely can't finish in time:

```yaml
      shard: true                  # one AGENT per .feature file
      maxParallel: 4               # matrix width
```

Sharding scales in **jobs**, and each job pays the full ~90 s setup (two
checkouts, `pip install`, browser download, Allure CLI) to run one file — a
29-feature suite becomes 31 jobs, 1000 features becomes 1002. It also adds a
discover job and a merge job, and the Allure tab then comes from the merged
report. It's a scaling lever, not a speed knob.

### Use a self-hosted agent pool

```yaml
      pool:
        name: my-linux-pool
```

The template already handles the three things that break a first run on a
locked-down agent (no passwordless `sudo`, `allure` bin-name collisions, ancient
Node) — see [ci-project-repo.md § 7](ci-project-repo.md#7-self-hosted-agents).

### Give the tests credentials

Never a secret value in this file. Two supported shapes:

```yaml
      keyVaultUrl: $(NOODLE_KEYVAULT_URL)     # one knob, engine reads every key
```

```yaml
      secretEnv:                              # explicit map, no Key Vault
        APP_PASSWORD: $(APP_PASSWORD)
        API_KEY: $(API_KEY)
```

Azure exposes **non-secret** variable-group values as environment variables
automatically; **secret** ones must be mapped by hand, which is what `secretEnv`
is for. Key Vault needs the engine's `azure` extra — the template installs it for
you when `keyVaultUrl` is set. Full detail:
[ci-project-repo.md § 4](ci-project-repo.md#4-secrets--one-knob).

### The suite uses `@visual` or `@appium`

```yaml
      extras: '[visual]'
```

Only then — every extra is install time on every run. You do **not** need
`[azure]` for Key Vault or `[parallel]` for `parallelProcesses`; the template
folds those in itself.

### The org has no Allure extension

```yaml
      publishAllureTab: false
```

The Tests tab, the RCA and the downloadable artifacts publish regardless, and
the artifact's `reports/allure-report/index.html` is a single file that opens
the full report offline. The tab task is `continueOnError` anyway, so a missing
extension degrades the tab rather than failing the suite.

### Run two suites in one pipeline

```yaml
  - template: ci/azure/noodle-tests.yml@noodle
    parameters: {workspaceDir: tests/web, jobName: web_tests}
  - template: ci/azure/noodle-tests.yml@noodle
    parameters: {workspaceDir: tests/api, jobName: api_tests, testTag: api}
```

`jobName` prefixes every job and artifact name, so two instances don't collide.

### The workspace is a folder inside an application repo

Then this scaffolded file isn't what you want — paste the `resources:` block and
the `- template:` job into the application's existing `azure-pipelines.yml`
instead, with `workspaceDir: tests/noodle`. That is the shape
[ci-project-repo.md](ci-project-repo.md) documents end to end, and it is the
better default when the app and its tests share a repo: the test lands in the
same PR as the feature it covers.

---

## Every parameter

Full table with defaults →
[ci-project-repo.md § Parameters](ci-project-repo.md#parameters).

Short version: `workspaceDir`, `testTag`, `pool`, `shard`, `maxParallel`,
`parallelProcesses`, `pythonVersion`, `extras`, `keyVaultUrl`, `secretEnv`,
`allureVersion`, `historyRuns`, `publishAllureTab`, `jobName`.

## What a green run looks like

One `Noodle tests` job, and three report surfaces that each carry steps,
evidence **and** logs — the Tests tab, the Allure Report tab, and a
`NoodleTestArtifacts-*` download that opens the whole report offline. Detail:
[ci-project-repo.md § 5](ci-project-repo.md#5-what-a-run-produces).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Run fails with **no job and no log** | compile-time failure: an unfilled `REPLACE_ME`, an unresolvable `ref:`, or the pipeline can't read the engine repo | fill the placeholders; grant Read to `<Project> Build Service` on the engine repo (Project Settings → Repositories → Security) |
| Run panel won't advance to **Resources** | a runtime string parameter is empty | `testTag` must be `all`, not blank |
| **Allure tab empty**, task green, log empty | the extension only uploads a single-file report, and skips anything else silently | the engine template handles this (`NOODLE_ALLURE_SINGLE_FILE` + a relative `reportDir`); if it persists, check the deployed extension version — 2.1.3 handles `reportDir` differently from 2.1.5 |
| `Invalid value for '--parallel': Parallel runs need behavex` | an engine older than 1.0.0a21 with `parallelProcesses` set | bump `noodleRef`, or set `parallelProcesses: 0` |
| `tar: …/.node: Cannot open` at **post-job**, every job red | `Cache@2`'s implicit save tars a path the portable-Node step never created | bump `noodleRef` to 1.0.0a20 or later |
| Ran the wrong engine | Azure doesn't match branch names across repos | read `engine sha` in the **Engine build provenance** step and compare with `git rev-parse <your tag>` |
| Editing the YAML changes nothing | Azure stores the YAML path in the pipeline definition | Edit → ⋮ → Triggers → YAML → path |
| `noodle run` exits 3, "no scenarios" | `testTag` matches nothing, or `workspaceDir` is wrong | check the tag exists; check `noodle.yaml` is at `workspaceDir` |

A failure *inside* the job always has evidence: the RCA on the run summary, and
that scenario's own `run log` next to its screenshots in both the Tests tab and
the Allure report.

## Related

- One-time setup, greenfield or nested → [ci-project-repo.md](ci-project-repo.md)
- The engine repo's own pipelines (a different thing) →
  [manual.md § Running in CI](manual.md#running-in-ci--azure-devops)
- Sharding internals, per-shard data isolation →
  [encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops)
- Wiring an AI agent that authors the tests →
  [ai-sdlc-integration.md](ai-sdlc-integration.md)

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
      ref: refs/tags/1.0.0a13       # pin it — a moving ref is not a build

jobs:
  # …the project's own build / deploy jobs stay as they are…

  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      workspaceDir: tests/noodle
```

That's the whole integration. The template checks out both repos, installs the
engine pinned by its own `constraints.txt`, installs the Allure 3 CLI, shards
by `.feature` file, runs headless, and publishes everything.

### Parameters

| Param | Default | What it's for |
|---|---|---|
| `workspaceDir` | `tests/noodle` | the dir holding `noodle.yaml`, repo-relative |
| `testTag` | `''` | only run scenarios with this tag |
| `pool` | `{vmImage: ubuntu-latest}` | pass your own for a self-hosted pool |
| `shard` | `true` | one agent per `.feature`; `false` = one job for the suite |
| `maxParallel` | `4` | matrix width |
| `parallelProcesses` | `0` | behavex processes *within* a job |
| `pythonVersion` | `3.11` | |
| `extras` | `''` | e.g. `'[visual]'` — only if the suite uses `@visual`/`@appium` |
| `keyVaultUrl` | `''` | the one secrets knob (§4) |
| `secretEnv` | `{}` | explicit `KEY: $(VAR)` map, if not using Key Vault |
| `allureVersion` | `3.14.1` | pinned Allure 3 CLI |
| `historyRuns` | `30` | trend runs retained |
| `jobName` | `noodle_tests` | prefix, if you need two of these in one pipeline |

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

| | Where |
|---|---|
| **Allure Report** tab | browsable, merged across shards, with cross-run trend history |
| **Tests** tab | pass/fail per scenario, from JUnit |
| Run summary | the RCA markdown for every failure |
| `NoodleTestArtifacts-*` | screenshots, traces, videos, `rca.html` |
| `NoodleAllureReport` | the single-file report — opens offline straight from the artifact |

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

## 8. Related

- Engine-repo-owned CI, the mirror image of this page, plus the external
  tests-repo parameters → [manual.md § Running in CI](manual.md#running-in-ci--azure-devops)
- Sharding, per-shard isolation, Key Vault detail →
  [encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops)
- Wiring an AI agent that *authors* the tests →
  [ai-sdlc-integration.md](ai-sdlc-integration.md)

# Feature Packages
<!-- Branch: NOOD_0062 -->

> **For:** testers building a new app-under-test.

Each app-under-test gets its own self-contained folder under `noodle_tests/` — its
`.feature` files, env, secrets, base URLs, page objects, test data and
functions all live inside that one folder, split into three subfolders:
`features/` (what to test), `resources/` (everything that supports it) and
`report/` (everything its runs produce — NOOD_0086).
Nothing about an app leaks into another app's folder, and nothing about an
app needs to touch the shared root files.

This works the same whether `noodle_tests/` sits inside the `noodle` repo or in a
separate workspace you run `noodle` against — see
[Two topologies](#two-topologies) below.

## The package contract

```
noodle_tests/<category>/<app>/
  features/
    *.feature
  resources/
    environments.yaml             # optional — base URL(s) for this app
                                  # (<app>_environments.yaml also accepted)
    <app>_secrets.env             # gitignored — package credentials (the working file)
    .env                          # committed, no secrets — package config overrides
    pageobjects/
      *_pom.yaml
    pom.yaml                      # local element overrides
    functions/
      *.py
    payloads/
      *.json
    data/
      *.csv
    preconditions.yaml
  report/                         # this app's run output (results + reports +
                                  # history) — single-app runs write their whole
                                  # artifacts tree here (`noodle run <...>/<app>`)
```

`resources/` holds everything an app's tests need that isn't the test spec
itself — credentials, base URLs, page objects, fixtures, seed scripts,
preconditions. A package is just "whatever folder contains a `features/`
subfolder" — there's no registry to update, `sample_feature_tests/web/busterblock/` and
`sample_feature_tests/api/` are both packages, nesting depth doesn't matter. Categories
with only one app-under-test (`api/`, `terminal/`) skip the extra app-name
layer and put `features/`/`resources/` directly under the category.

**`<category>` = the wok.** Since NOOD_0155 the category level is the formal
**wok** (capability work area) name — `web/`, `mobile/`, `desktop/`,
`performance/` (plus this repo's `api/`/`terminal/`, which belong to the web
wok). The folder name is still free-form (nothing parses it), but matching
the wok keeps suites discoverable — see [woks.md](woks.md).

Worked example: [`sample_feature_tests/web/busterblock/`](../sample_feature_tests/web/busterblock) has
`BB_USER`/`BB_PASS` in `resources/busterblock_secrets.env.example` and its
base URL in `resources/busterblock_environments.yaml`.

### `payloads/` and `data/` are conventions, not enforcement

There's no code that routes JSON to `payloads/` or CSV to `data/` — a step
like `uses this payload 'payloads/seed_cart.json'` or
`a user from this list "data/users.csv" logs in` resolves whatever relative
path you write, joined onto `resources/`. Dropping a CSV straight in
`resources/` instead of `resources/data/` still works; it just reads
inconsistently to the next person who opens the folder. Same guard as
everything else here — the convention is documentation, not a runtime check.

### `noodle_tests/environment.py` and `noodle_tests/steps/` are a behave contract, not an app folder

These two live at the root of `noodle_tests/`, not per-app, and don't get folded
into `resources/`. behave itself requires a file named exactly
`environment.py` and a folder named exactly `steps/` at the directory it's
pointed at (or an ancestor `noodle` walks up to, via `_find_behave_base`) —
that's a behave naming contract, not a Noodle convention, so it can't be
renamed or relocated into a `helpers/` folder without breaking step
discovery. `noodle_tests/steps/z_catch_all.py` re-exports the framework's one
catch-all step matcher; `noodle_tests/steps/custom_hooks.py` is where project-local
hooks and custom step definitions go (see `hooks.feature` /
`custom_steps.feature`). `noodle_tests/pom.yaml` is the one genuinely tree-wide
file — global page objects shared by every app — so it also stays at the
`noodle_tests/` root rather than under any single app's `resources/`.

## Resolution algorithm — which file wins

For the `.feature` file currently loading, `feature_dir` is its own
`features/` folder, `app_dir` is `feature_dir`'s parent (the package root —
`resources/` lives here), and `workspace` is the run's working directory
(the repo root, or whatever `--workspace` points at):

| File type | Order (highest priority first — NOOD_0133) |
|---|---|
| `{env:X}` secrets & config | 1. real process env vars (shell/CI-injected, always wins) → 2. `app_dir/resources/.env` → 3. `app_dir/resources/[<app>_]secrets.env` → 4. `workspace/.env` → 5. `workspace/secrets.env`. App-package files OVERRIDE workspace-root files on a key collision; only a real pre-run env var beats them. |
| `{env:X}` base URLs | 1. real process env vars → 2. `app_dir/resources/[<app>_]environments.yaml` → 3. `workspace/environments.yaml` — same model: app file beats root file. |
| Page objects | 1. `app_dir/resources/pageobjects/*_pom.yaml` → 2. `app_dir/resources/pom.yaml` → 3. nearest ancestor `pom.yaml`/`noodle_tests/pom.yaml` found by walking up from `feature_dir` |
| Preconditions | `app_dir/resources/preconditions.yaml` only — no cascade |
| Test data / fixtures | `app_dir/resources/**` only — no cascade |
| Functions / scripts | wherever the `.feature` file's literal path string points — conventionally `app_dir/resources/functions/**` |

"First wins" comes straight from `load_dotenv()`'s own behaviour (it only
sets a key that isn't already in `os.environ`) — there's no separate merge
step to reason about.

### Per-page POM files need `match:`

A file under `pageobjects/*_pom.yaml` is scoped to a specific page by URL —
either an explicit `match: {url_contains: "..."}` block, or, if you omit
`match:` entirely, an **implicit** one Noodle builds from the filename stem
(`checkout_pom.yaml` → `match: {url_contains: "checkout"}`). That implicit
scope is a real trap for a file meant to apply everywhere (a "shared" or
app-name-stem file, e.g. `busterblock_pom.yaml`, `shared_pom.yaml`): unless
that stem happens to be a substring of every target URL, its keys silently
become unreachable — no error, they just never match, and steps fall through
to a less reliable resolution path instead. Opt into the old
apply-everywhere behaviour explicitly with an **empty** `match: {}`:

```yaml
match: {}

shopping cart:
  css: ".shopping_cart_link"
```

See `noodle/agents/web/pom.py::_wrap_page` for the exact rule, and
`docs/design-history.md` Phase 17 ("POM file scoping") for why this defaults
to scoped rather than global.

`noodle validate` lints for the trap (NOOD_0022): a per-page file whose
stem appears in no URL-ish string in its sibling `features/` gets a
warning naming the file and the fix (rename to match the URL path, or add
an explicit `match:`). Warn-only — a stem may match a runtime redirect the
static scan can't see.

### The collision rule

`os.environ` is one flat namespace per process. Two packages must not reuse
a generic key name (`USERNAME`, `PASSWORD`) — whichever package's feature
file runs first in that process "claims" the key, and the second package
silently gets the first one's value. Prefix package-specific keys with the
app name (`BB_USER`, `SAUCE_USERNAME`) exactly like the existing root
`secrets.env.example` already does. There's no automatic collision
detection — the naming convention is the guard.

## Two topologies

- **In-repo:** `noodle/` (the framework) and its bundled
  `sample_feature_tests/` live in the same repo. `noodle run` with no
  `--workspace` resolves the tests dir relative to the repo root.
- **External workspace:** `noodle` is installed as a dependency
  (`pip install noodle`) into a separate directory that holds its own
  `noodle.yaml`, `.env`, and `noodle_tests/`:
  ```
  /path/to/workspace/noodle_tests/example/features/
  /path/to/workspace/noodle_tests/busterblock/features/
  ```
  Scaffold one with `noodle init` — it names the tests root `noodle_tests`
  (the `tests_dir` key in `noodle.yaml`), which can't collide with a host
  project's own `tests/` folder — then run with
  `noodle run --workspace /path/to/workspace`. See
  [agent-playbook.md](agent-playbook.md).

No code branches on which topology you're in for config/secrets/POM. Every
path above is resolved from `Path.cwd()` or from the feature file's own
folder — never from where `noodle` itself is installed — and the CLI always
runs `behave` with `cwd` set to the workspace. A
`noodle_tests/busterblock/resources/busterblock_secrets.env` resolves identically
either way.

The one thing that *did* branch on topology until NOOD_0027: the
step-search/suggestion engine (`noodle step-search`, `noodle repl`'s "find a
step for ...") was hardcoded to this repo's own `docs/`, regardless of
`--workspace`. Fixed by threading `--workspace` into
`step_resolver.set_docs_dir()` / `patterns.set_agent_patterns_dir()`, and
also called from `hooks.before_all` so a suggestion accepted in a workspace
actually resolves at `noodle run` time in that same workspace. See
[design-history.md § Phase 23](design-history.md#phase-23--external-workspace-verdict--nood_0030-gap-analysis-closed-out).

## Adding a new app package

1. Create `noodle_tests/<category>/<app>/features/` (or `noodle_tests/<app>/features/` in
   a single-category workspace) with your `.feature` files.
2. Add `resources/<app>_secrets.env` (gitignored) for any credentials the
   scenarios need, prefixed with the app name, and fill in real values —
   `noodle repl`'s scaffolding writes this file for you. (NOOD_0118: generate
   no longer emits a committed `.example`; that's an init-only convention.)
3. Add `resources/environments.yaml` (`<app>: https://…`) if the app has its
   own base URL — never put an app's base URL in the workspace-root `.env`;
   the app-prefixed name `<app>_environments.yaml` is also accepted.
4. Add `resources/pageobjects/`, `resources/functions/`,
   `resources/payloads/`, `resources/data/` as needed — same rules as any
   existing suite.

`noodle repl`'s `create test for ... at ...` does step 1, and step 2/3, for
you automatically, deriving the app folder from the URL's host (currently
always under `noodle_tests/web/`). If the generated `.feature` text itself
references a payload, a custom function, or an `@precondition:NAME` tag, the
agent stubs that one resource file too (detection-based — a plain login/
search test doesn't get empty `functions/`/`payloads/` folders it never
asked for).

You can also ask for one supporting file at a time, without regenerating the
whole test — `generate the secrets file for busterblock`, `generate the pom
for busterblock`, `generate a precondition for busterblock` all work as
direct commands (no LLM needed) or free-form ones (with `--llm`, e.g.
"generate the secrets file for busterblock to store the username and
password"). A follow-up that doesn't name the app again resolves it from the
last app touched this session.

# Contributing

## Setup

```bash
uv pip install -e ".[all,dev]"
source .venv/bin/activate
playwright install chromium
```

## Before opening a PR

```bash
make test                  # unit_tests/ — must be green
make lint                  # ruff check .
noodle benchmark --gate  # engine branches — exit 0 (PASS) required
```

The first two **do** gate this repo: [`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs
lint + the unit suite (Linux and Windows), the bundled BusterBlock end-to-end
suite, and a Docker build on every PR. Run them locally anyway — a red gate
found on your laptop costs six minutes less than one found on the PR.

`noodle benchmark --gate` is the end-to-end authoring benchmark
([docs/benchmark.md](docs/benchmark.md)): ~90 seconds,
zero LLM cost, exit 0 PASS / 1 REGRESSED. CI cannot run it (it drives a
live site), so it runs on your machine — required before any PR that
changes engine code (`noodle/`, `pyproject.toml`); docs-only or
workspace-only branches are exempt.

`noodle benchmark` is the other axis and is **not** a PR gate: the same app
(the bundled BusterBlock, behind its login gate) asked for a test in five
different request SHAPES — a paragraph, a numbered list, one sentence, an
ambiguous short spec, and one whose assertion is deliberately wrong. That is
what catches a change which only bites when the request isn't shaped like the
gate's cases, and the wrong-assertion spec is the only thing in the repo that
catches an engine reaching green by dropping what it could not prove. ~2-3
minutes. Run it when touching the prompt compiler, the agent doors or payload
sizes, and before a release — [docs/benchmark-specs.md](docs/benchmark-specs.md).

`azure-pipelines*.yml` at the repo root are a different thing: example
pipelines for teams to adopt in *their own* pipeline, not a gate here.

## Branch naming & commits

`feature/nood_XXXX` or `patch/nood_XXXX`, ticket number = highest existing + 1. One commit per branch (squash before pushing):

```
JIRA_ID worktype:Title of work

Short description
- did this
- did that
```

`worktype`: `feature` / `fix` / `docs` / `refactor` / `perf` / `test` / `chore`.

## Versions & release tags

`pyproject.toml`'s `[project] version` is the only version source; every
branch touching `noodle/` bumps it and adds the matching `CHANGELOG.md`
section (a unit test asserts the header matches).

Tags carry that exact string, **no `v` prefix**:

```
1.0.0a6 → 1.0.0a7 → …   alpha, the counter we're on now
1.0.0b1 → 1.0.0b2 → …   beta
1.0.0                    release
```

Tag every release — a project repo consuming
[`ci/azure/noodle-tests.yml`](ci/azure/noodle-tests.yml) pins the engine with
`ref: refs/tags/<version>`, and has nothing stable to point at otherwise. The
old `v`-prefixed spelling is obsolete and gone.

## Trust boundary — feature files are code, not data

`.feature` files can run arbitrary shell via `run_command`/`run_script`
(`noodle/orchestrator/script_runner.py`), with the authoring user's
privileges — no sandboxing. Treat feature-file authorship like commit
access, not like filling out a form:

- Only accept `.feature` files from people who'd otherwise get a merge to `main`.
- Review a PR touching `run_command`/`run_script`/`call_function` steps the way you'd review a shell script, not the way you'd skim a Gherkin scenario.
- Don't wire an external/untrusted input source (a ticket description, a form submission, an LLM prompt from an anonymous user) into a step that reaches these — see the docstring in `script_runner.py` and docs/encyclopedia.md § "Running scripts & commands".

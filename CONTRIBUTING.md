# Contributing

## Setup

```bash
uv pip install -e ".[all,dev]"
source .venv/bin/activate
playwright install chromium
```

## Before opening a PR

```bash
make test     # unit_tests/ — must be green
make lint     # ruff check .
```

Neither runs in CI automatically yet — `azure-pipelines*.yml` at the repo root are example pipelines for teams to adopt in *their own* pipeline, not a gate on this repo. Run both locally before asking for review.

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

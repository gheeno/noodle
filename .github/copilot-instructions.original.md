# Noodle — Copilot instructions

This repo is the Noodle BDD test framework (Playwright/behave/Appium under
one Gherkin surface, with Allure + RCA reporting). Full guide, kept current:
**[docs/agent-playbook.md](../docs/agent-playbook.md)**.
Read it before writing or running a test — this file is a digest of its
highest-priority rules, not a replacement.

## The rules that matter most

1. **New test case → outside this repo by default.** Unless explicitly asked
   to add to `tests/` in *this* repo (Noodle's own bundled example suites),
   scaffold to `<workspace>/noodle_tests/<app>/` via `noodle init`, not
   inside whatever project you're testing. See playbook §1.
2. **Write Gherkin steps from the existing vocabulary** —
   [docs/steps_dictionary.md](../docs/steps_dictionary.md) or
   `noodle step-search "<description>"` — don't invent new phrasing an
   unmatched step will either fail loudly or silently cost an LLM call.
   Playbook §3–4 has the full tag vocabulary and the annotation → POM →
   step resolution pipeline.
3. **`--headless` by default** for any unattended run; only drop it when a
   human is explicitly watching the browser.
4. **Whenever tests are run — always generate and open BOTH reports, not
   just one:**
   ```bash
   noodle report open <workspace>/artifacts/reports/allure-report
   noodle rca-report --workspace <workspace> --out <workspace>/artifacts/reports/rca.md --serve
   ```
   `noodle run` auto-regenerates Allure every time, but only *sometimes*
   auto-writes a bare-markdown RCA (failures + non-parallel mode only, never
   the HTML view) — call `rca-report --serve` explicitly regardless, every
   run, pass or fail, so you get the real HTML view and a positive
   confirmation on clean runs too. Playbook §5.
5. **Root-cause before you patch.** A failing assertion almost never means
   "change the expected value" — read the RCA verdict first (stale content,
   rate-limited/shared external resource, ambiguous locator, confirmed
   external bug → `@quarantine`, or genuine environment gap → skip
   cleanly). Playbook §7.
6. **Secrets never go in a `.feature` file or a committed `.env`.**
   `secrets.env`/`<app>_secrets.env` are gitignored; commit only the
   `.example` template.

## Edge cases (playbook §8 has the full list)

- Artifacts are wiped on every `noodle run` — `noodle archive` first if you
  need to keep a run's evidence.
- `NOODLE_MODEL` is never auto-detected — a running local Ollama server
  does nothing until you set it explicitly.
- `@live`/`@terminal`/`@appium` scenarios auto-skip (not fail) when their
  real dependency isn't available — that's expected, not a bug.
- A `*_pom.yaml` with no `match:` block silently never resolves unless its
  filename stem is a substring of the target URL.

## Git/commit conventions

Not covered here — see `CLAUDE.md` (JIRA-tagged commit format, squash rule,
branch naming). This file and the playbook cover test authoring/running;
`CLAUDE.md` covers the git workflow around it.

# Noodle — Copilot instructions

This repo is Noodle BDD test framework (Playwright/behave/Appium under
one Gherkin surface, w/ Allure + RCA reporting). Full guide, kept current:
**[docs/agent-playbook.md](../docs/agent-playbook.md)**.
Read it before writing or running test — this file is digest of its
highest-priority rules, not replacement.

**Terminology (NOOD_0155)** — three formal nouns, canonical in
[docs/glossary.md § The three nouns](../docs/glossary.md#the-three-nouns--engine-workspace-wok):
**noodle engine** = this repo / installed framework; **noodle workspace** =
test project scaffolded by `noodle init` (refresh: `noodle init --force`);
**noodle wok** = capability work area (web, mobile, desktop, performance —
[docs/woks.md](../docs/woks.md)). "Update the engine" → code here;
"update our workspace" → tests/config in the workspace; "update our wok
mobile" → that capability + its `unit_tests/woks/mobile/` tests.

**Changed engine code (`noodle/`, `pyproject.toml`) on branch?** Bump
`[project] version` (pre-1.0: alpha counter, `0.2.0a9` → `0.2.0a10`) and
add matching `CHANGELOG.md` section, same commit as the code — unit test
asserts the two agree. That number is what tells tester their install
predates their checkout. Tell them **`noodle update`** — the one command
after any `git pull`/`git checkout`; re-links running `noodle` to the
clone. `noodle doctor` diagnoses, `noodle update` repairs.

**Asked to "install noodle" / set up Noodle on this machine?** Follow
**[docs/llm-install.md](../docs/llm-install.md)** — agent install
runbook (macOS + Windows 11): preflight what's already there, per-phase
verification checkpoints, failure table. Installation only — don't run
tests or start test apps unless also asked.

## The rules that matter most

1. **New test case → outside this repo by default.** Unless explicitly asked
   to add to `sample_feature_tests/` in *this* repo (Noodle's own bundled example suites),
   scaffold to `<workspace>/noodle_tests/<app>/` via `noodle init`not
   inside whatever project you're testing. See playbook §1.
2. **Write Gherkin steps from existing vocabulary** —
   [docs/steps_dictionary.md](../docs/steps_dictionary.md)
   `noodle step-search "<description>"` — don't invent new phrasing
   unmatched step will either fail loudly or silently cost LLM call.
   Playbook §3–4 has full tag vocabulary and annotation → POM →
   step resolution pipeline. Generating/updating test:
   [docs/llm-performance.md](../docs/llm-performance.md) ranks paths
   (rule templates → author + `noodle validate --resolve` yourself →
   engine LLM last) — you are model, so author directly and validate
   browser-free; never regenerate whole file to change two steps.
2.5. **Never hand-write a Playwright script to look at a page or debug a
   locator.** Unfamiliar/SPA page before authoring → `noodle probe <url>`
   first (`--click <trigger>` for gated controls, `--search "<term>"` for
   result pages, `--do "enter <v> in <f>" --do "click save"` to drive a
   real fill→save transaction and capture what each step reveals — a whole
   multi-stage flow in one probe; `--find "<text>"` pulls matching
   controls whole — never grep/pipe noodle output). Locator resolving to the wrong/no
   element → `noodle inspect <url> "<phrase>"`. Both return the real
   selector, POM YAML, and assertion text in one headless load — raw
   Playwright means you skipped one (probe NOOD_0113/0116/0144, inspect
   NOOD_0115).
3. **`--headless` by default** for any unattended run; only drop it when
   human is explicitly watching browser.
4. **Whenever tests are run — always deliver BOTH reports, not one:**
   ```bash
   noodle report serve --workspace <workspace>
   #  → http://127.0.0.1:8000/allure-report/index.html + http://127.0.0.1:8000/rca.html
   ```
   Every run auto-writes both (Allure + rca.md/rca.html, pass or fail,
   parallel included — NOOD_0082); `report serve` hosts them together on
   localhost, rebuilding from `allure-results/` first if missing. Host them
   ONLY with `report serve` (or the `serve_report` MCP tool) — never `allure
   serve`, `python -m http.server`, or a raw `file://` open: Allure's SPA
   needs the real HTTP origin and only `report serve` co-hosts the RCA.
   `noodle report list` + `noodle report serve <stamp>` re-host older
   archived run. Playbook §5.
5. **Root-cause before you patch.** failing assertion almost never means
   "change expected value" — read RCA verdict first (stale content,
   rate-limited/shared external resource, ambiguous locator, click blocked
   by an overlay → close it, don't re-locate; confirmed external bug →
   `@quarantine` or genuine environment gap → skip cleanly). Mid-flow
   locator/state failure → reproduce that exact state once (`noodle probe
   <url> --do "<actions so far>"`) and re-author from it; never one
   guessed fix per red run. Playbook §5/§7.
6. **Secrets never go in `.feature` file or committed `.env`.**
   `secrets.env`/`<app>_secrets.env` are gitignored; commit only
   `.example` template.
7. **Be terse — spend tokens on test, not on narration** (playbook §0).
   Progress updates are max 2 sentences and always state your current
   intent ("Now running Noodle validate+run loop", "Run successful
   after 3 attempts", "Serving reports now") — one update per phase
   change, no restating tool output, no per-step commentary; quote only
   failing steps/errors. If user says not to output shell commands,
   echo no command lines — intent update replaces them. Batch work —
   validate whole .feature once, not line by line.
8. **Prerequisites are not test steps.** Base URL/login → `Background:`
   resolution → `@viewport:WxH` tag; certs are ignored by default
   (`NOODLE_IGNORE_HTTPS_ERRORS`). Scenario bodies read like human using
    page: navigate → see → act → verify, plain verbs first. Popups
   test doesn't need get NO steps — engine auto-dismisses blockers
   flags them in RCA warnings (check those after green runs). App-
   specific python scripts live in that app's `resources/scripts/`.

## Edge cases (playbook §8 has the full list)

- Artifacts are wiped on every `noodle run` — `noodle archive` first if you
  need to keep run's evidence.
- `NOODLE_MODEL` is never auto-detected — running local Ollama server
  does nothing until you set it explicitly.
- `@live`/`@terminal`/`@appium` scenarios auto-skip (not fail) when their
  real dependency isn't available — that's expected, not bug.
-  `*_pom.yaml` w/ no `match:` block silently never resolves unless its
  filename stem is substring of target URL.
- Never hand-write a Playwright script to look at a page or debug a locator.
  `noodle probe` (NOOD_0113/0116) is the pre-authoring probe; `noodle
  inspect` (NOOD_0115) is the locator debugger — raw Playwright means you
  skipped one.

## Git/commit conventions

Not covered here — see `CLAUDE.md` (JIRA-tagged commit format, squash rule,
branch naming). This file and playbook cover test authoring/running;
`CLAUDE.md` covers git workflow around it.


---
name: "noodle"
description: "Author and run Noodle BDD tests — .feature syntax, tags, parameterization, POM files, env/secrets files, running with --headed/--headless/--parallel, and the mandatory Allure + RCA reporting step. Use whenever asked to write, update, or run a test with the Noodle framework, via the CLI, noodle repl, or the noodle MCP server."
domain: "testing"
confidence: "high"
source: "team-decision"
---

# Noodle — agent skill

Playwright+behave BDD: plain-English Gherkin matched to a curated
pattern table (no step code); locators: accessible-name →
POM yaml → self-heal; every run writes Allure + RCA. Deep dive:
`read_docs('agent-playbook')`.

Nouns: engine = framework repo/install; workspace = `noodle init` test
project; wok = capability work area, tag-routed
(`read_docs('woks')`).

## Green path

| # | Operation | MCP tool | CLI |
|---|---|---|---|
| 1 | Probe (unfamiliar page/SPA only) | `probe_page(url, click=[…], do=[…], search="…", suggest="…", follow="…", expect=[…])` | `noodle probe <url> --compact [--click/--do/--search/--suggest/…]` |
| 2 | Author | `author_test(…)` | `noodle author --spec spec.yaml -w <ws> --json` |
| 3 | Execute+report+serve | `run_and_report(headless=True, retries=0, serve_reports=True)` | `noodle run <path> -w <ws> --headless --retries 0 --json --serve` |

- Bundle every reveal click, dropdown enum (`--open-native`), search
  term, typeahead (`--suggest`+`--follow`), `--expect` verdicts and the
  `--do` transaction into ONE probe — never per stage or re-probe to
  grep. `suggested_steps`/`pom_yaml` are copy-ready — paste.
- Goal mode is the rule for a new single-flow test: spec `goal:` +
  `--run` (`run_after_author=True`) — probes, compiles feature+POM,
  runs once, serves; 0 passed = failure; budget = 1 probe, 1 run.
  Hand-written Gherkin only after a named blocker (`intent_verified:
  false`, syntax-only). `{do: pick}` after search selects one captured
  result caption; check kind `item_in_destination` + `expected_from`
  verifies the same caption in any destination (a bare count cannot
  stand in); `evidence: screenshot` attaches the capture to that
  verify Then, never a standalone shot step. Checks anchor per page:
  `after: start` = landing, `after: <id>` = that action's page, none
  = end state. Unproven extras block —
  blockers are hard stops.

```yaml
goal:  # an OBJECT, never a string; rejections return this example
  scenario: Search returns matching results
  dismissals: [location_prompt, popups]
  actions: [{do: search, term: "<term>"}]
  checks: [{count: results, min: 1}, {any_of: ["<text>", "<alt text>"]}]
```

- `--spec` keys = `author_test` args: app_name, base_url, feature_path,
  feature_content, pom_content, environment_values, secret_values,
  overwrite — one write.
- `author_test` `ready: true` IS the validation (parse, step match,
  POM scope, `{env:}`; base URL key returns as `base_url_key`).
  `validate_feature`/`preflight` after it is waste. `ready: false` →
  repair the `blocking` list, re-author with `overwrite=true`.
- Execute payload: report paths, served URLs and, on red, `rca_compact`;
  extra RCA/report/serve calls repeat it; URLs pre-checked (`http_ok`)
  — no curl, no jq.
- Fastest path first: only standard-visible-control pages skip the
  probe; hidden/config/custom/SPA probe first (`--discover`,
  `probe-app`). `append_to` adds a scenario; `use_llm=True` last
  (llm-performance).

## Writing a .feature

```gherkin
@web
Feature: Login
  Scenario: Valid user logs in
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User clicks the login button
    Then User should see "VHS Catalog"
```

- Steps must match the pattern table — `noodle steps "<kw>"` /
  `search_step` before inventing phrasing. Goal mode needs
  neither: the engine writes the steps.
- Parameters: `{env:NAME}` (config/secrets), `{var:NAME}` (captured),
  `{pom:NAME}` (force POM).
- Never hardcode credentials or base URLs — `{env:...}` only.
- Prompt credentials: pass once as `author_test(secret_values=…)` —
  they land only in the gitignored `<app>_secrets.env`, never echoed.
- Count via the page's summary-number step, never rendered cards.
- Key tags: `@web` `@api` `@mobile` `@visual` `@headed` `@viewport:WxH`
  `@quarantine` `@precondition:NAME` `@smoke` (§3).

## POM files

Resolution: accessible name/role/text → `pageobjects/*_pom.yaml` → app
`pom.yaml` → global `pom.yaml` → self-heal. Entries
only where plain text is ambiguous. A
`*_pom.yaml` without `match:` only applies when its filename stem appears
in the URL — give shared files `match: {}` or their keys never resolve. Wrong element? `noodle inspect <url> "<phrase>"` / MCP
`inspect_locator` lists candidates and find()'s pick.

## Workspace

`noodle init <ws>` scaffolds; each app self-contained:
`features/`, `resources/` (env yaml, gitignored `<app>_secrets.env`,
`pageobjects/*_pom.yaml`), `report/`. MCP: `noodle-mcp
--workspace <ws>` in `.copilot/mcp-config.json`.

## Fixing failures

1. `rca_compact` in the run payload (`noodle rca-report --compact`)
   — verdict + failing step + fix; `mutation-failed` = the mutation
   request aborted/refused → fix the action, not the assertion.
2. The failing line in the run summary; grep `run.log`.
3. Still unexplained? screenshot (vision ≈10× text) or network
   capture — never for a timing/locator failure.
4. Locator/state failure? Reproduce the state ONCE (`probe --do
   "<flow>"`), re-author from its delta — never a guessed fix per lap.

`@quarantine` (with a comment) a confirmed external bug; Operation 3 already serves; re-host
an older run: `noodle report serve <stamp>` (`serve_report`) —
never `allure serve`, `http.server`, or `file://`.

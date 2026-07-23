# Noodle Test Framework — Where to Find What
<!-- Branch: NOOD_0066 -->

> **For:** everyone — quick reference index, human and AI agent alike.

Quick navigation for settings, files, and resources. If you're looking for a specific config value, output file, or doc section, start here.

---

## The three nouns — engine, workspace, wok

The canonical vocabulary (NOOD_0155) for talking about Noodle — in issues,
prompts to AI agents, and these docs. Each names a different thing; saying
which one you mean removes most ambiguity about where a change lands.

| Term | Means | "Update the …" means |
|---|---|---|
| **noodle engine** | The framework itself — this repo (`noodle/` package: resolver, agents, hooks, reporting, CLI) or the installed `noodle` distribution. A tool you install, like `git`. | Change framework code in this repo: feature branch, `NOOD_XXXX` ticket, unit tests, docs. |
| **noodle workspace** | The self-contained test project scaffolded by `noodle init <path>` (refresh templates with `noodle init --force`): `noodle.yaml`, `.env`, `AGENTS.md`, `noodle_tests/`, and every report a run produces. Owned by the tester, lives outside the engine repo. | Change tests, config, POM, secrets, or fixtures in that folder — never engine code. Full guide: [workspace-guide.md](workspace-guide.md). |
| **noodle wok** | A capability work area — **web, mobile, desktop, performance**: a slice through the engine (agents + pattern tables + extras) with its own samples and per-wok unit tests. | Extend that capability in the engine (its agent/patterns/tests), e.g. "update our noodle wok mobile". Full concept: [woks.md](woks.md). |

The three compose: the **engine** runs a **workspace**'s tests, each
scenario cooking in one (or, composed, several) **woks**.

## Configuration files

| What you're configuring | File | Notes |
|------------------------|------|-------|
| Base URLs (`{env:SAUCEDEMO}`, `{env:STAGING}`) | `environments.yaml` | Committed. One key per environment. |
| Browser, headless, retries, LLM mode | `.env` | Committed. No secrets here. |
| Credentials, API keys | `secrets.env` | **Gitignored.** Copy from `secrets.env.example`. |
| Per-app env/secrets/URLs | `noodle_tests/<type>/<app>/resources/` | Optional — overrides the root files for that app only. See [feature-packages.md](feature-packages.md). |
| Element aliases when labels fail | `pom.yaml` | In the app's `resources/` folder (sibling of its `features/` folder). |
| Precondition / teardown HTTP calls | `preconditions.yaml` | In the app's `resources/` folder (sibling of its `features/` folder). |
| Azure Key Vault connection | `.env` (`NOODLE_KEYVAULT_URL`) + `secrets.env` | Requires `.[azure]` extra. |

---

## Environment variables

### Run and browser settings — `.env`

| Variable | Default | What it does |
|----------|---------|-------------|
| `NOODLE_BROWSER` | `chromium` | `chromium` \| `firefox` \| `webkit` |
| `NOODLE_HEADLESS` | `false` | `true` for CI |
| `NOODLE_STRICT_LOCATOR` | `false` | `true` = ambiguous locators fail (recommended in CI) |
| `NOODLE_RETRIES` | `1` | Re-run a failed scenario N extra times |
| `NOODLE_IGNORE_HTTPS_ERRORS` | `true` | TLS + self-signed/invalid cert errors ignored in all browsers; `false` (or `@secure_certs` tag) to surface them |
| `NOODLE_DEV_FIX_ATTEMPTS` | `10` | Agent test-dev loop: max auto-fix + rerun attempts on mechanical failures before it stops and reports the test as flaky |
| `NOODLE_PARALLEL_PROCESSES` | *(unset)* | Number of parallel workers (requires `.[parallel]` extra) |
| `NOODLE_SCRIPT_TIMEOUT` | `60` | Timeout in seconds for `run the script` steps |
| `NOODLE_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

### LLM settings — `.env` (model name) + `secrets.env` (API key)

| Variable | Where | What it does |
|----------|-------|-------------|
| `NOODLE_MODEL` | `.env` | Enable LLM. E.g. `anthropic/claude-haiku-4-5-20251001`, `gemini/gemini-1.5-flash` |
| `NOODLE_LLM_MODE` | `.env` | `auto` (fallback) \| `full` (every step). Default: `auto` when model is set. |
| `NOODLE_LLM_URL` | `.env` | Override LLM endpoint (Ollama, Foundry Local) |
| `NOODLE_VISION_MODEL` | `.env` | Enable flag for the `@visual` vision-LLM fallback (the model called is still `NOODLE_MODEL`) |
| `NOODLE_RCA` | `.env` | `true` = classify failure root cause after each failed step |
| `ANTHROPIC_API_KEY` | `secrets.env` | Claude API key |
| `GEMINI_API_KEY` | `secrets.env` | Gemini API key |
| `GROQ_API_KEY` | `secrets.env` | Groq API key |
| `OPENAI_API_KEY` | `secrets.env` | OpenAI API key |

### Secrets and credentials — `secrets.env`

| Variable | What it does |
|----------|-------------|
| `BB_USER` / `BB_PASS` | BusterBlock credentials — `sample_feature_tests/web/busterblock/resources/busterblock_secrets.env` |
| `SAUCE_USERNAME` / `SAUCE_PASSWORD` | SauceDemo credentials |
| `NOODLE_KEYVAULT_URL` | Azure Key Vault URL (if using Key Vault) |
| Any `{env:VAR}` in a feature | Matching key in `secrets.env`, shell env, Key Vault, or the app's own `resources/<app>_secrets.env` |

Variable resolution order (highest wins): **Key Vault → shell/CI → root `.env` → root `secrets.env` → `<app>/resources/.env` → `<app>/resources/<app>_secrets.env` → `environments.yaml`**. See [feature-packages.md](feature-packages.md) for the per-app cascade.

---

## Source files and directories

| What you're looking for | Where |
|------------------------|-------|
| Feature files (bundled sample tests) | `sample_feature_tests/` (user workspaces: `<tests_dir>`, default `noodle_tests/`) |
| Woks (capability work areas: web/mobile/desktop/performance) | registry `noodle/wok.py` · concept doc `docs/woks.md` · CLI `noodle wok` · per-wok unit tests `unit_tests/woks/<wok>/` |
| BusterBlock test suite | `sample_feature_tests/web/busterblock/features/` |
| SauceDemo tests | `sample_feature_tests/web/saucedemo/features/` |
| API tests | `sample_feature_tests/api/features/` |
| Terminal / canvas tests | `sample_feature_tests/terminal/features/` |
| Performance (load-test) samples | `sample_feature_tests/performance/features/` |
| Cross-wok sample (Excel → web) | `sample_feature_tests/desktop/features/excel_to_web.feature` |
| Custom step definitions | `sample_feature_tests/steps/` (user workspaces: `<tests_dir>/steps/`) |
| Resource files (CSV, JSON payloads) | `noodle_tests/<type>/<suite>/resources/{data,payloads}/` |
| Per-app env/secrets/base URL | `noodle_tests/<type>/<suite>/resources/` |
| POM aliases | `pom.yaml` in the app's `resources/` folder |
| Precondition fixtures | `preconditions.yaml` in the app's `resources/` folder |
| Utility scripts (CI discovery, seeding) | `scripts/` (framework dev tooling, not test resources) |
| Unit tests | `unit_tests/` |
| BusterBlock test app source | `test-apps/busterblock/` |
| CI pipeline (Azure DevOps) | `azure-pipelines.yml`, `azure-pipelines-windows.yml` |
| Docker image | `Dockerfile` |

---

## Output files

Everything a run produces lands under one root, so CI can archive/ship it
in a single step: `<app>/report/` for a single-app run (NOOD_0086),
`artifacts/` for a workspace-wide one (`NOODLE_ARTIFACTS_DIR` overrides
both). The layout inside the root is identical either way — the table shows
it for `artifacts/`:

| Output | Where | How to open |
|--------|-------|------------|
| Allure raw results | `artifacts/allure-results/` | Input for `noodle report generate` |
| Allure HTML report | `artifacts/reports/allure-report/` | `noodle report open` (needs HTTP, not `file://`) |
| JUnit XML (CI) | `artifacts/reports/junit.xml` | Azure DevOps Tests tab |
| Failure screenshots | `artifacts/screenshots/` | Open directly |
| Playwright traces | `artifacts/traces/` | `playwright show-trace artifacts/traces/<name>.zip` |
| Healing telemetry | `artifacts/reports/healing-report.jsonl` | JSON Lines, one entry per healed locator |
| Healing report | `artifacts/reports/healing-report.txt` | Plain text with `pom.yaml` suggestions |
| RCA report | `artifacts/reports/rca.md` | Auto-written when a run has failures AND isn't `--parallel`; run `noodle rca-report` explicitly for stdout/`--out`/`--llm`, or `--serve` for the styled HTML view (never auto-written) |
| Network/console capture | `artifacts/network/<scenario>.json` | Failed scenarios only — console errors, failed requests, websocket frames |
| Run log (sys log) | `artifacts/logs/noodle.log` | Everything the console got, mirrored to a file |

`noodle artifacts` lists all of the above with file counts/size; `noodle clean`
deletes the whole tree; `noodle archive` zips it to `archives/artifacts_<timestamp>.zip`.

---

## Documentation

| Doc | What's in it |
|-----|-------------|
| `README.md` | Full setup guide (Windows + macOS), quick reference, tech stack, BusterBlock, LLM setup, agentic mode |
| `docs/encyclopedia.md` | Complete how-to: write tests, pom.yaml, shared state, CI, LLM setup |
| `docs/feature-packages.md` | Per-app packaging: `features/`, `resources/`, resolution order, in-repo vs external workspace |
| `docs/steps_dictionary.md` | All built-in step patterns with phrasings and examples |
| `docs/architecture.md` | Deep dive: components, resolution hierarchy, the LLM layer |
| `docs/woks.md` | The wok concept — the four capability work areas (web/mobile/desktop/performance), routing tags, cross-wok composition, per-wok unit tests |
| `docs/design-history.md` | Rationale behind each capability, condensed from the build phases |
| `docs/agent-playbook.md` | **The** canonical AI-agent guide — workspace routing, Gherkin/tag vocabulary, steps-dictionary/POM resolution pipeline, mandatory RCA+Allure reporting, edge cases. `.github/copilot-instructions.md` and `CLAUDE.md` both point here |
| `.github/copilot-instructions.md` | Copilot-native digest of the playbook above (Copilot auto-loads this path) |
| `docs/llm-setup.md` | Picking/configuring an LLM provider, cloud cost comparison, and reaching one through a locked-down work GitHub/Copilot/Azure account (GitHub Models is retired 2026-07-30 — Azure AI Foundry is the current path) |
| `docs/design-history.md` | Completed plans and point-in-time reviews, condensed as dated Phase entries (e.g. LLM-mode hardening NOOD_0038, the AI-agent decision NOOD_0056, the readiness audit NOOD_0058) |
| `docs/mcp-guide.md` | `noodle-mcp` setup, local quickstart, tool reference, design rationale, and MAF / Azure AI Foundry wiring |
| `docs/ai-sdlc-integration.md` | Azure DevOps setup, wiring a LangChain/MAF agent via `noodle-mcp`, and a worked multi-agent Squad-pattern example |
| `docs/external-site-walkthrough.md` | Worked example: a real suite built against a live external site |

---

## Common "where is…" questions

| Question | Answer |
|----------|--------|
| Where do I put a base URL? | `environments.yaml` — referenced as `{env:KEY}` in features |
| Where do I put a password or API key? | `secrets.env` — gitignored, never committed |
| Where do I set which browser to use? | `NOODLE_BROWSER` in `.env` |
| Where do I enable the LLM? | `NOODLE_MODEL` in `.env` + API key in `secrets.env` — or `noodle init --llm ollama\|claude\|gemini` writes it for you |
| Where do I add a POM alias for a hard-to-find element? | `pom.yaml` in your app's `resources/` folder |
| Where do I add a data precondition / teardown? | `preconditions.yaml` in your app's `resources/` folder |
| Where do I add a custom step? | `noodle_tests/steps/` — any `.py` file with `@when`/`@then` decorators |
| Where do I put a CSV or JSON fixture file? | `noodle_tests/<type>/<suite>/resources/{data,payloads}/` |
| Where is the full step reference? | `docs/steps_dictionary.md` |
| How do I write a conditional step ("click X if Y appears") or a hard sleep? | `docs/steps_dictionary.md` — "Conditional Steps" and "Waits" sections (NOOD_0044) |
| Will a step with bad grammar (past tense, smart quotes, "verify that …") still match? | Yes — `docs/steps_dictionary.md` — "Grammar tolerance" section (NOOD_0062) |
| Where do I find failure details? | `artifacts/screenshots/` for images, `artifacts/traces/` for Playwright traces, `artifacts/reports/allure-report/` for the full report |
| Where's the AI-agent guide (workspace routing, tags, RCA/Allure rule)? | `docs/agent-playbook.md` — `.github/copilot-instructions.md` and `CLAUDE.md` both point here |
| How do I point CI at a tests repo that isn't this one? | `azure-pipelines.yml`'s `useExternalTestsRepo` parameter — see [encyclopedia.md § 11](encyclopedia.md#11-ci--azure-devops) |

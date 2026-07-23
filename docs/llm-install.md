# Installing Noodle on a Brand-New Machine — LLM Agent Runbook
<!-- Branch: NOOD_0101 -->

> **Who this is for:** an AI coding agent (Claude Code, Copilot CLI, Cursor,
> or any LLM-driven tool) asked to install the Noodle Test Framework on a
> machine that may have nothing on it yet. Supports **macOS** and
> **Windows 11**. A human can follow it too, but the phrasing, checkpoints,
> and failure tables are written for an agent executing commands and reading
> their output. The human-narrative version of this material is README.md's
> Setup guide (Parts 1–7); if this file and the README ever disagree on a
> command, the README wins — report the drift.

**Contract with the user:** installation only. Do **not** run tests, start
BusterBlock or any other test app, or generate a test unless the user
explicitly asked for that too. Finish by reporting the checklist in §7.

---

## 0 — Ground rules

1. **Detect the OS yourself** — don't ask. `uname` succeeding → macOS/Linux
   path; PowerShell/`$env:OS` = `Windows_NT` → Windows 11 path.
2. **Check before you install.** Every phase starts with a detection
   command. If the thing is already there and working, say so and skip the
   install — never blindly redo a step that already works, and never
   uninstall/reinstall something working without being asked.
3. **Verify every phase before starting the next** by running its
   verification command and comparing actual output to the "expect" column.
   If it doesn't match, fix it using the failure table (§6) before moving on
   — do not stack a phase on top of a broken one.
4. **New terminal after PATH changes.** Several installers edit the shell
   profile / user PATH; those edits only apply to shells started afterwards.
   When a just-installed command isn't found, open a fresh shell (or source
   the profile) before concluding the install failed.
5. **Never disable TLS verification** to get around a corporate-proxy
   certificate error — see §6 for the sanctioned fix.

---

## 1 — Preflight: what's already here?

Run all of these first (they're read-only), then report what you found:

| Check | Command (both OS unless noted) | Already done if… |
|---|---|---|
| Noodle installed | `noodle --version` | prints a version — skip to §5 |
| Repo cloned | look for a `noodle/` checkout (`pyproject.toml` with `name = "noodle"`) | exists — skip the clone in §3 |
| Python 3.11+ | macOS: `python3 --version` / Win: `python --version` | `3.11` or higher |
| Git | `git --version` | any version |
| uv | `uv --version` | any version |
| Node.js 18+ | `node --version` | `18` or higher |
| Allure CLI | `allure --version` | `3.x` |
| VS Code + ext | `code --list-extensions \| grep -i noodle` (PowerShell: `code --list-extensions \| Select-String noodle`) | prints the noodle extension |

---

## 2 — Prerequisites

### macOS

```bash
xcode-select --install    # GUI installer; wait for it to finish (5–15 min).
                          # Errors with "already installed"? Fine — move on.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                          # Skip if `brew --version` already works. Follow the
                          # "Next steps" it prints to put brew on PATH.
brew install python@3.11 node
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g allure
brew install --cask visual-studio-code   # optional — only for the VS Code extension in §4
```

### Windows 11 (PowerShell — `winget` is preinstalled)

```powershell
winget install Python.Python.3.11
winget install Git.Git
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install OpenJS.NodeJS.LTS
npm install -g allure
winget install Microsoft.VisualStudioCode   # optional — only for the VS Code extension in §4
```

**Checkpoint (both OS, in a NEW terminal):** `python3 --version` (macOS) /
`python --version` (Windows) prints ≥ 3.11; `git --version`, `uv --version`,
`node --version` (≥ 18) and `allure --version` (3.x) all print versions. Any
miss → §6 before continuing.

---

## 3 — Get the framework

Use the permanent-PATH install (the README's **Option B**) — it's the one
that makes `noodle` work from any test workspace, which is the whole point
of an agent-driven setup. (The README's Option A per-terminal `.venv` is
only for people hacking on Noodle's own source — don't pick it here unless
the user said they're developing Noodle itself.)

Same commands on both OS. Start from a CLEAN SLATE (NOOD_0133): a prior
non-editable copy in some Python's site-packages silently shadows the
editable install and never updates on `git pull` — the unconditional
uninstall is safe here because the very next step reinstalls editable from
the clone:

```bash
# 1. clean slate — remove any prior copy from either manager (best-effort; ignore "not installed")
pip uninstall -y noodle 2>/dev/null || true
uv tool uninstall noodle 2>/dev/null || true
# 2. editable install from the clone
git clone https://github.com/gheeno/noodle.git
cd noodle
uv tool install --editable ".[all]" --with-executables-from playwright
uv tool update-shell            # puts uv's tool bin dir on PATH
# open a NEW terminal now — update-shell only affects future shells
playwright install chromium     # the browser Noodle drives
# 3. VERIFY the shim resolves into the clone's editable install — mandatory:
#    uninstalling alone is not enough, a copy earlier on PATH still wins
which -a noodle                 # Windows 11: Get-Command noodle -All
```

Optional, only if the user wants engine-side LLM features (`noodle repl
--llm`, `NOODLE_MODEL` runtime fallback) rather than driving Noodle from an
agent over MCP — it's separate because it's the one extra that can hit a
from-source build on managed networks, and nothing else depends on it:

```bash
uv tool install --editable ".[all,llm]" --with-executables-from playwright --force
```

**Checkpoint:** in a new terminal, from your home directory (NOT the clone —
that would mask a PATH problem):

| Command | Expect |
|---|---|
| `noodle --version` | version + resolved build path + git SHA — the path must be the editable install (uv's tool dir), NOT a `site-packages` copy |
| `noodle doctor --scope install` | exit 0. It probes every `noodle` launcher on PATH for the build it executes: `INFO … launchers execute the same build` is **healthy** (a project `.venv` plus the uv tool shim is the normal dev setup — do NOT "fix" it); `FAIL` on `install.launchers` or `install.editable` means a conflicting/stale copy — follow the printed reinstall command |
| `noodle-mcp --help` | usage text |
| `playwright --version` | a version string |

Do not declare success until `noodle doctor --scope install` exits 0 — the
exact incident this guards against is a stale copy winning on PATH while
the fresh editable install sits unused. (`which -a noodle` / Win:
`Get-Command noodle -All` shows the raw PATH order if you need it, but
doctor's provenance comparison, not launcher count, is the verdict.)

**Tell the user the update step before you finish.** Installation is
once; staying current is every day:

```bash
git pull                       # or: git checkout feature/nood_XXXX
noodle update                  # re-links the install to the checkout
```

`noodle update` runs the reinstall matched to how this copy was installed,
from the clone, against the interpreter running the `noodle` they typed
(venv stays venv, system stays system). Idempotent and safe after every
pull or branch switch — it is the only step, and it never runs git.
[manual.md → Troubleshooting](manual.md#troubleshooting) has the caveats
(PATH-first launcher, Windows `python -m noodle update`).

---

## 4 — VS Code extension (optional, recommended)

Skip silently if the user's request was CLI/agent-only, or `code` isn't on
PATH and VS Code isn't wanted. From inside the clone:

```bash
npm install -g @vscode/vsce
cd vscode-extension && npm install && cd ..
make install-ext
```

No `make` on Windows — run the target's two commands directly:

```powershell
cd vscode-extension
npx @vscode/vsce package --allow-missing-repository --skip-license --out ../noodle.vsix
cd ..
code --install-extension noodle.vsix --force
```

`--force` matters even on a first install: the extension manifest version
never changes between releases, so any earlier sideload (a previous agent
attempt included) makes VS Code silently skip a same-version reinstall.

**Checkpoint:** `code --list-extensions` lists the noodle extension. Then
have the user fully quit VS Code (Cmd+Q / close every window — a window
reload is NOT enough) and reopen; a `.feature` file (e.g.
`sample_feature_tests/web/busterblock/features/login.feature`) must show
real syntax colour. Still black-and-white → §6.

---

## 5 — Create a test workspace and verify end to end

Tests live **outside** the engine clone (see
[agent-playbook.md §1](agent-playbook.md)). From the clone's parent
directory:

```bash
noodle init my-tests            # scaffolds noodle.yaml, .env, AGENTS.md, noodle_tests/, MCP config
cd my-tests
noodle list                     # discovers the scaffolded sample — proves config + engine wiring
```

`noodle init <dir>` is non-destructive (never overwrites existing files), so
it's safe on a partially set-up workspace.

**Checkpoint:** `noodle list` prints the sample package's scenarios with no
traceback. That — not a test run — is the "installed" bar for this runbook.
Only run an actual test (`noodle run … --headless`, then serve both reports
per [agent-playbook.md §5](agent-playbook.md)) if the user asked for a full
install-plus-run.

---

## 6 — Failure table

| Symptom | Cause | Fix |
|---|---|---|
| Windows: `python --version` opens the Microsoft Store | Fresh Win 11 ships Store "App execution aliases" for python that shadow the real one | Settings → Apps → Advanced app settings → App execution aliases → turn OFF `python.exe`/`python3.exe`; new terminal; retry |
| Windows: `…Activate.ps1 cannot be loaded because running scripts is disabled` | Default PowerShell execution policy (only hit on the Option A dev path) | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, retry |
| `noodle: command not found` right after `uv tool install` | uv's tool bin dir (`uv tool dir --bin`) not on PATH yet | `uv tool update-shell`, then open a NEW terminal |
| Any just-installed tool "not found" | PATH edit predates this shell | new terminal first; only then re-investigate |
| `uv pip install` → `error: No virtual environment found` | Option A path without `uv venv` first | `uv venv`, activate, retry — or switch to the §3 Option B install |
| `CERTIFICATE_VERIFY_FAILED` deep in a build (usually the `llm` extra, corporate network) | TLS-inspecting proxy the OS trusts but Python build tooling doesn't | Base install is unaffected — proceed without `llm`; see README § Troubleshooting for trusting the proxy CA properly. Never disable TLS verification |
| `.feature` files uncoloured after install + full VS Code restart | Same-version reinstall silently skipped, or another Gherkin extension owns `.feature` | Reinstall with `--force`; disable the Cucumber/other Gherkin extension for the workspace; full quit + reopen |
| `noodle` works in the clone but not elsewhere | Option A venv install, not Option B | Run the §3 `uv tool install` (Option B) |
| `noodle` acts like an old version / fixes visible in source don't run | A non-editable copy in some Python's `site-packages` shadows the editable install | `noodle doctor` — it probes each PATH launcher and FAILs only on *conflicting* builds (identical duplicates are INFO, leave them); then §3 clean slate + reinstall — full cure in [manual.md → Troubleshooting](manual.md#troubleshooting) |
| Allure report opened via `file://` renders blank | Allure's SPA needs real HTTP | `noodle report serve` / `noodle report open` — never hand over a raw index.html path |

---

## 7 — Report back

Tell the user, concretely:

1. What was already installed and skipped, per the §1 preflight.
2. Each phase completed, with the verification output observed
   (`noodle --version` string, `noodle list` scenario count).
3. Anything from §6 you hit and how you fixed it.
4. What you did NOT do (no tests run, no apps started) — and the exact next
   command if they want that: `noodle run <tests_dir>/<app> --headless`
   followed by `noodle report serve` (both reports, always — see
   [agent-playbook.md §5](agent-playbook.md)).

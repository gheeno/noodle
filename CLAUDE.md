# Noodle Test Framework — Claude Instructions

## Terminology — engine, workspace, wok

Three formal nouns (NOOD_0155), canonical definitions in
[docs/glossary.md → The three nouns](docs/glossary.md#the-three-nouns--engine-workspace-wok):
**noodle engine** = this repo / the installed framework ("update the noodle
engine" = change code here); **noodle workspace** = the test project
scaffolded by `noodle init` / refreshed by `noodle init --force` ("update
our noodle workspace" = change tests/config there, never engine code);
**noodle wok** = a capability work area — web, mobile, desktop, performance
("update our noodle wok mobile" = extend that capability + its per-wok
tests, see [docs/woks.md](docs/woks.md)). Use the user's noun to decide
where a change lands.

## Test authoring and running

This file covers git workflow. For writing or running Noodle tests —
workspace routing, Gherkin/tag conventions, the steps-dictionary/POM
resolution pipeline, and the mandatory "always generate + open both the
Allure report and the RCA report after any run" rule — see
[docs/agent-playbook.md](docs/agent-playbook.md). Read
it before writing or running a test; don't duplicate its content here or
let it drift out of sync with this file's git rules.

Asked to **install Noodle on a machine** ("install noodle", "set up
noodle")? Follow [docs/llm-install.md](docs/llm-install.md) — the agent
install runbook (macOS + Windows 11) — or invoke the `/install-noodle`
skill, which points there. Any install request runs
clean-slate → editable install → verify (per that runbook's §3), never a
bare install over an unknown prior state. Generating or updating a test
case fast and token-lean:
[docs/llm-performance.md](docs/llm-performance.md).

If a user reports **old behavior, missing fixes, or ignored config**
(NOOD_0133): first suspect a stale non-editable install shadowing the
clone — `noodle doctor` / `which -a noodle` diagnose it — and point them
at [docs/manual.md → Troubleshooting](docs/manual.md#troubleshooting);
prefer an editable reinstall.

---

## Versioning — bump on every engine branch

`pyproject.toml` `[project] version` is the **only** version source, and
`noodle --version` compares it against what the install recorded. That
comparison is what tells a tester their install predates their checkout —
it only works if the number actually moves.

**Every branch that changes engine code (`noodle/`, `pyproject.toml`)
bumps the version and adds a `CHANGELOG.md` section for it.** Pre-1.0, the
bump is the alpha counter: `0.2.0a9` → `0.2.0a10`. Docs-only or
workspace-only branches don't need one. The CHANGELOG section header must
match the pyproject version exactly — a unit test asserts it.

Whenever you finish work on an engine branch, do the bump in the same
commit as the code. Then tell the user the one command that lands it on
their machine: **`noodle update`** — the step after any `git pull` or
`git checkout`. It re-links the running `noodle` to the clone (editable
install, this interpreter, no git operations). `noodle doctor` diagnoses
without changing anything; `noodle update` is what repairs.

---

## Commit message format

Every commit must follow this format exactly:

```
JIRA_ID worktype:Title of work

Short description of what this commit does
- did this
- did that
- fix this
- adds documentation
```

**JIRA_ID** — the ticket number (e.g. `NOOD_0019`). Always ask the user if not clear from context.

**worktype** — one of:
- `feature` — new capability
- `fix` — bug fix
- `docs` — documentation only
- `refactor` — code restructure, no behaviour change
- `perf` — performance improvement
- `test` — test additions or changes
- `chore` — config, deps, tooling

**Gate: never push until the commit message matches this format.** If the format is wrong, fix the commit message before pushing.

Example:
```
NOOD_0001 feature:adds baseline framework

this commit adds
- playwright
- selenium-like capabilities
- behave BDD runner
```

**On completing any piece of work, print a ready-to-use commit message in the format above** — even if not committing yet.

---

## Squash rule

**Max 1 commit per branch.** Whenever a branch has more than one commit, squash them all into one before doing any further work or pushing. The squashed commit message must still follow the format above, summarising all the work done.

To squash: `git reset --soft $(git merge-base HEAD main) && git commit` — or `git rebase -i HEAD~N` where N is the number of commits on the branch since it diverged from main.

---

## Syncing with main

**Rebase + fast-forward only — never merge main into a feature branch.** If a branch falls behind `main` (e.g. an earlier PR off the same base landed first), bring it up to date with `git rebase origin/main`, resolve any conflicts there, then force-push (`--force-with-lease`) the branch. Do not `git merge main`/`git merge origin/main` — that produces a merge commit, which both violates the squash rule above and defeats the point of a linear history.

In practice this usually collapses into the squash step: `git reset --soft origin/main && git commit` re-bases *and* squashes to one commit in a single move when the branch only has one commit's worth of net changes.

---

## Branch naming

Branches follow the pattern `feature/nood_XXXX` or `patch/nood_XXXX` — `feature/` for new capability, `patch/` for fix/docs/chore-only work.

**Canonical form — always normalize.** The exact shape is `feature/nood_XXXX` / `patch/nood_XXXX`: prefix `nood_` (never `noodle_`), lowercase, zero-padded 4-digit number. When the user gives a branch name in any other spelling (`feature/noodle_0153`, `NOOD-153`, "ticket 153", just "0153"), do NOT push that literal name — normalize it to the canonical form, keep the user's number, and state the final branch name in your reply.

**Numbering the ticket:** before starting a new feature, patch, or doc branch, check the latest commit/branch on the repo to find the highest existing `nood_XXXX` number, then use that number + 1 for the new branch. Example: if the previous branch was `nood_0046`, the next one is `nood_0047`. If the user supplies a number, use theirs — but if it collides with or skips past the sequence, point that out before pushing.

**Branch number = commit JIRA_ID.** The `NOOD_XXXX` in the branch name, the commit message header, and any ticket references in code comments/test filenames must all carry the same number. If the branch is renumbered, renumber the rest with it.

**Auto-created session branches (`claude/session-*`):** a remote Claude session may be forced to start on one. Treat it as scratch — deliver the work on a correctly named `feature/nood_XXXX`/`patch/nood_XXXX` branch (derive the number by the rule above, or ask), and tell the user the session branch can be deleted on GitHub if the tooling blocks deleting it from the session.

---

## General workflow rules

- Do not push to remote unless the user explicitly asks.
- Do not force-push to main/master.
- Always confirm before destructive git operations (reset --hard, branch -D, etc.).

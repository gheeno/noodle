# AI agent instructions — Noodle engine

You are in the **noodle engine** repo: the framework itself, not a test
workspace. This file is the entry point for any agent or assistant —
Claude, Copilot, Cursor, Codex, Gemini, Grok, or whatever comes next.
Nothing here is model-specific.

| I need | Read |
|---|---|
| git workflow, commit format, branch naming, version bump | `CLAUDE.md` |
| writing or running Noodle tests | `docs/agent-playbook.md` |
| installing Noodle on a machine | `docs/llm-install.md` |
| engine / workspace / wok — which one is being asked for | `docs/glossary.md` |

## Observability — noodle commands only

Every question about a page, a port, a payload, or the workspace has a
noodle command that answers it. Reaching for a shell tool means one was
skipped. This is not a style preference: shell answers are unbounded,
unlogged, and invisible to the reports the engine has to produce.

| Reflex | Noodle command |
|---|---|
| `curl` / `wget` a URL to see if it answers | nothing — `noodle probe <url>` names a dead origin itself ("nothing is listening there"), so the pre-check was never needed |
| `ls` / `find` to see what tests exist | `noodle list` |
| `\| grep` `\| jq` `\| sed` `\| head` on noodle output | read it as returned — every payload is pre-bounded |
| reading the app's source to find a selector | `noodle probe <url>` — Noodle is black-box; app source is never in scope |
| a locator resolving to the wrong element | `noodle inspect <url> "<phrase>"` |
| a hand-written Playwright script to look at a page | `noodle probe <url>` |
| `allure serve`, `python -m http.server`, `file://` | `noodle report serve` |
| the goal / prompt schema | `noodle author --vocabulary` |
| a step phrasing | `noodle step-search "<description>"` |
| install, env, or config health | `noodle doctor` |
| whether the engine still works end to end | `noodle feature-regression` |

**No noodle command for what you need? Say so and stop.** An unmet
observability need is an engine gap to report — not a shell command to
improvise around. Improvising hides the gap, and the next agent hits it
too. Naming it is the fix.

The one exception is `git` for the workflow in `CLAUDE.md`. That is
version control, not observability of a system under test.

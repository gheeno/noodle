"""Noodle MCP server (NOOD_0045 Phase 3) — `noodle-mcp [--workspace DIR]`.

Exposes the agent core (noodle/repl/core.py) as MCP tools over stdio so an
external agent in an AI SDLC can generate, run and inspect tests without
Noodle needing its own LLM: the caller brings the language skills, the tool
schemas carry the structure, Noodle executes deterministically.

The step vocabulary is published as the `noodle://vocabulary` resource — a
calling agent can author resolver-compatible Gherkin itself and submit it
via write_feature (validated before it lands).

Transports (NOOD_0045 — MAF/Foundry support):
  stdio (default)     — local hosts: Claude Code, MAF's MCPStdioTool.
      {"noodle": {"command": "noodle-mcp", "args": ["--workspace", "/path/to/ws"]}}
  streamable-http     — remote hosts: Azure AI Foundry Agent Service's MCP
      tool / MAF's MCPStreamableHTTPTool connect to http://host:port/mcp.
      Auth: set NOODLE_MCP_API_KEY and callers must send it as
      `Authorization: Bearer <key>` or `x-api-key: <key>`. Binding beyond
      localhost without a key is refused — network-exposed test execution
      is a trust boundary, not a convenience toggle.
"""
import argparse
import functools
import hmac
import os
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from noodle import payload_budget
from noodle.repl import core

# NOOD_0096 — surfaced by MCP-compliant hosts at connect time regardless of
# which tool gets called first, unlike the workspace's AGENTS.md: driving
# from outside the workspace dir (common — the host is usually launched from
# the engine checkout or wherever, with --workspace just pointing elsewhere)
# means that file never gets auto-loaded. Put the two things that most
# commonly bite an agent driving via MCP where it can't miss them.
_INSTRUCTIONS = """\
Noodle — deterministic BDD test runner. Conventions live in the workspace's
AGENTS.md; launched from elsewhere, call read_docs('agent-playbook') once.

Three operations make the whole pipeline — add nothing between them:
1. probe_page(url) — solely when the page is unfamiliar or an SPA. Put ALL
   discovery in that single call (click=[reveal names],
   do=["enter <v> in <f>", "click save"] runs a fill→save transaction,
   diffing each state, open_native_controls=True,
   search="term", discover=True when trigger names are unknown). Output is
   author-ready: pom_yaml, copy-ready steps, exact headings — unless
   author_ready: false; a STOP: fix the named gap, never hand-author
   feature_content around a failed probe or blocked goal. Skip the probe ONLY
   when every control the test needs is a standard visible one; any hidden
   panel, config gate, custom widget, or SPA/Flutter shell means probe
   first. probe_app(platform) is the native equivalent (Appium,
   snapshot-only).
2. author_test(...) — ready=true already IS validation (Gherkin parsed,
   steps matched, POM scoped, {env:} refs resolved): calling
   validate_feature or preflight after it is waste. ready=false: repair
   what `blocking` lists, re-author with overwrite=true. Cheapest:
   goal={...} + run_after_author=true — the engine probes, compiles
   feature/POM itself, runs once and serves, in this one call.
3. run_and_report(headless=True, retries=0, serve_reports=True) — runs,
   verifies both reports fresh, hosts them, returns the URLs; a red run
   includes rca_compact inline, so get_rca/serve_report add nothing here.
   URLs are pre-checked (http_ok) — no curl; payloads as returned — no jq.
   Green = failed == 0 AND verified: true; verified: false (fuzzy healing/
   lenient ambiguity behind a pass) is NOT a pass — read
   unverified_reasons/healing_events before claiming success.

headless=True always (the scaffolded .env default is headed, for a
watching human) and retries=0 until green (the engine's own retry re-runs a
failing scenario, doubling every failed lap's wall time).

Authoring source order: the probe's suggested steps, the noodle://vocabulary
resource, search_step. generate_test covers rule-based template shapes;
append_to extends a feature; use_llm=True is the last resort — the norm is
zero engine-side model calls (read_docs('llm-performance')).
"""
# NOOD_0147 note: the session-diagnostics contract deliberately does NOT ride
# these instructions (byte-capped, unit_tests/test_nood_0131.py) — run results
# carry a `diagnostic_due` nudge when the engine detects a failure trigger,
# and the log_diagnostic docstring holds the rest.

mcp = FastMCP("noodle", instructions=_INSTRUCTIONS)

# NOOD_0164 — every tool return passes the payload budget. Registering through
# `_tool()` instead of `mcp.tool()` is what makes that structural: a new tool
# cannot leak a 25 KB payload into a host's context by forgetting a cap,
# because the boundary is the decorator, not the tool body. Tools that know
# their own content still shrink it well first (probe's cap ladder,
# list_tests' index+query); this catches whatever still overflows.
# `_BUDGET_HINTS` names the retrieval path for the tools whose payload has one.
_BUDGET_HINTS = {
    "list_tests": "Pass query='<substring>' for just the features you want.",
    "read_docs": "Read the doc file directly for the rest, or narrow with "
                 "section=/query=.",
    "probe_page": "Re-probe with compact=False for the full dump.",
    "probe_app": "Re-probe with compact=False for the full snapshot.",
    "get_rca": "Use compact=True, or open rca.html in the served report.",
}


def _tool():
    """`@mcp.tool()` plus the payload budget (NOOD_0164)."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return payload_budget.bound(fn(*args, **kwargs),
                                        hint=_BUDGET_HINTS.get(fn.__name__, ""))
        wrapper.__budgeted__ = True
        return mcp.tool()(wrapper)
    return decorate

# Set once in main(); tools default to this workspace (per-call override below).
_WORKSPACE = "."
_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

# NOOD_0057 — roots that per-call `workspace` overrides may point into.
# None = unrestricted (stdio: the spawning host is already fully trusted, §9
# of docs/mcp-guide.md). streamable-http sets this in main() — a remote,
# key-authenticated caller must not be able to aim the engine at an
# arbitrary filesystem path.
_ALLOWED_ROOTS: list[Path] | None = None


def _ws(workspace: str | None) -> str:
    """Resolve a tool call's workspace (NOOD_0057). None -> the --workspace
    the server started with; otherwise the override, checked against
    _ALLOWED_ROOTS and with its .env loaded (existing process env wins —
    ponytail: first workspace to define a var keeps it for the process
    lifetime; per-call env isolation if that ever bites)."""
    if not workspace:
        return _WORKSPACE
    path = Path(workspace).resolve()
    if not path.is_dir():
        raise ValueError(f"workspace {workspace!r} is not a directory")
    if _ALLOWED_ROOTS is not None and not any(
            path == root or path.is_relative_to(root) for root in _ALLOWED_ROOTS):
        raise ValueError(
            f"workspace {workspace!r} is outside the roots this server was "
            "started with (--workspace / --workspace-root)")
    _load_workspace_env(workspace)
    return workspace


def _info() -> dict:
    """The server_info payload — also logged to stderr at startup so the
    host's MCP debug output shows which code a long-lived process is
    actually running (there is no hot reload; restart after deploys)."""
    try:
        version = metadata.version("noodle")
    except metadata.PackageNotFoundError:
        version = "unknown (not installed)"
    return {"noodle_version": version, "started_at": _STARTED_AT,
            "pid": os.getpid(), "workspace": str(Path(_WORKSPACE).resolve())}


@_tool()
def generate_test(url: str, description: str, use_llm: bool = False,
                  overwrite: bool = False, append_to: str | None = None,
                  workspace: str | None = None) -> dict:
    """Create a Gherkin .feature file + page-object skeleton from a plain-English
    description of a web test. Rule-based templates by default (login / search /
    checkbox / dropdown / generic, with quoted values slot-filled); use_llm=True
    routes generation through the workspace's configured NOODLE_MODEL instead.
    A bare @tag token in `description` (e.g. "tag this @smoke") is added to
    the generated scenario(s). append_to (an existing feature's file stem,
    e.g. "search_suggestion") adds this test case's scenario(s) to that file
    instead of writing a separate one — same app/topic, one more scenario;
    omit it to always get a new .feature file, e.g. for a different suite or
    topic (rule-based generation only; ignored with use_llm). Returns the
    written file paths, whether the test is runnable as-is (runnable=False
    means <placeholders> still need real values), and the generator's log
    output. workspace overrides the server's default workspace for this call."""
    return core.create_test(description, url, use_llm=use_llm,
                            overwrite=overwrite, append_to=append_to,
                            workspace=_ws(workspace))


@_tool()
def run_test(target: str | None = None, tag: str | None = None,
             workspace: str | None = None, headless: bool | None = None,
             browser: str | None = None, retries: int | None = None,
             parallel: int | None = None,
             parallel_scheme: str = "feature") -> dict:
    """Run a test WITHOUT the report/serve/RCA tail — prefer run_and_report,
    which wraps this run in the one-call pipeline. target is a feature
    name, stem substring, or path; omit it to run the last generated/run
    feature (falls back to the newest .feature). tag runs a tag filter
    instead. workspace may point at one app package — that app runs and its
    artifacts land in its own report/ folder. Pass headless=True when an
    agent drives (the .env default is headed, for a watching human) and
    retries=0 until green (the default retry re-runs a failing scenario,
    doubling failed-lap wall time). browser
    (chromium|firefox|webkit|safari|edge), parallel (N feature files via
    behavex, web only) and parallel_scheme (feature|scenario) mirror the CLI
    flags. Returns exit code + structured pass/fail counts and per-failure
    details. workspace overrides the server's default."""
    return core.run_test(target, tag=tag, workspace=_ws(workspace),
                         headless=headless, browser=browser, retries=retries,
                         parallel=parallel, parallel_scheme=parallel_scheme)


@_tool()
def get_last_result(workspace: str | None = None) -> dict:
    """Structured result of the most recent test run: passed/failed counts,
    each failure's feature, scenario, failing step and error message, and the
    total wall-clock seconds. workspace overrides the server's default
    workspace for this call."""
    return core.last_result(workspace=_ws(workspace))


@_tool()
def run_and_report(target: str | None = None, tag: str | None = None,
                   workspace: str | None = None, headless: bool | None = None,
                   browser: str | None = None, retries: int | None = None,
                   parallel: int | None = None,
                   parallel_scheme: str = "feature",
                   serve_reports: bool = True) -> dict:
    """THE execution operation: preflight → run → report → serve, one bounded
    payload. A missing/placeholder {env:KEY} returns missing_secret_keys with
    ZERO browser launches. The run hook's reports are freshness-checked
    once (rebuilt only if stale); serve_reports defaults True — the workflow
    ends on served Allure+RCA URLs, never file paths. On red the
    payload carries rca_compact (verdict + failing step + fix): read that, do
    not call get_rca separately; on a locator/state failure reproduce that
    exact state ONCE (probe_page do=[the flow]) and re-author from evidence
    — never one guessed fix per red run. Same target and
    headless/browser/retries/parallel flags as run_test (pass headless=True,
    retries=0 while iterating). workspace overrides the server's default."""
    return core.run_and_report(
        target, tag=tag, workspace=_ws(workspace), headless=headless,
        browser=browser, retries=retries, parallel=parallel,
        parallel_scheme=parallel_scheme, serve_reports=serve_reports)


@_tool()
def preflight(target: str | None = None, workspace: str | None = None) -> dict:
    """NOOD_0128 — check a test is runnable BEFORE launching a browser: every
    {env:KEY} it references resolves to a real value (not missing, not a
    CHANGE_ME/empty placeholder), and no redundant post-navigation waits. Same
    target resolution as run_test. Returns {ok, missing_secret_keys, errors,
    warnings}; run_and_report runs this automatically. Use it standalone to
    tell the user which secret keys to populate in the gitignored secrets.env
    before a run. workspace overrides the server's default workspace."""
    return core.preflight(target, workspace=_ws(workspace))


@_tool()
def author_test(app_name: str | None = None, base_url: str | None = None,
                feature_path: str | None = None,
                feature_content: str | None = None,
                pom_content: str | None = None,
                environment_values: dict | None = None,
                required_secret_keys: list[str] | None = None,
                secret_values: dict | None = None,
                goal: dict | None = None, run_after_author: bool = False,
                overwrite: bool = False,
                allow_unverified_intent: bool = False,
                prompt: str | None = None,
                workspace: str | None = None) -> dict:
    """Write a whole test package in ONE transaction (web/<app_name> or
    the existing package mapped to base_url — environments.yaml, POM,
    feature): validated first; any failure rolls every byte back.
    feature_content / pom_content are each ONE string.
    secret_values ({KEY: value}) is WRITE-ONLY (gitignored
    <app>_secrets.env, never echoed); missing required_secret_keys become
    placeholders. `prompt`: numbered plain-English steps, expanded
    DETERMINISTICALLY to a goal (underspecified steps borrow from
    neighbours, inferences echoed as prompt_expansion.assumptions; a
    URL derives the three path params).

    ELSE prefer `goal` (mutually exclusive w/ feature_content): {scenario,
    navigation?: [ordered URLs, an {env:} Given each],
    actions: [{do: search|suggest|pick|add_to|click|enter|select, id?,
    term/target/value/option; pick binds 'any matching result' to ONE
    probed result item; add_to = {item_from: pick-id, destination},
    engine-lowered to observed controls; never invent surface steps}],
    checks: [{see|count|any_of|field|item_in_destination, min?, value?,
    expected_from?: pick-id, evidence?: screenshot, after?: action-id}],
    dismissals?, probe?: {discover}}. item_in_destination re-asserts the
    bound caption (identity, not count). ONE goal-scoped probe,
    engine-compiled feature + POM; unproven requests block, never drop.
    Payloads carry goal_verified, intent_verified + intent_trace; a block
    names ONE typed next_action — repair that gap only. Manual
    feature_content is never intent_verified; allow_unverified_intent is
    a human-only override.
    run_after_author=true also runs once (headless, retries=0), serves
    both reports, returns {author, run}; 0 passed = failure.

    Returns paths, warnings, missing_secret_keys, base_url_key (use that
    exact {env:} key), and ready+blocking. ready=true = static authoring
    checks — never validate/preflight separately; run next. ready=false:
    fix `blocking`, re-author with overwrite=true. workspace overrides
    the server default."""
    return core.author_test(
        app_name=app_name, base_url=base_url, feature_path=feature_path,
        feature_content=feature_content, pom_content=pom_content,
        environment_values=environment_values,
        required_secret_keys=required_secret_keys, secret_values=secret_values,
        goal=goal, run_after_author=run_after_author,
        overwrite=overwrite,
        allow_unverified_intent=allow_unverified_intent,
        prompt=prompt, workspace=_ws(workspace))


@_tool()
def list_tests(workspace: str | None = None, query: str | None = None) -> dict:
    """Inventory of every .feature in the workspace: path, feature name, tags,
    scenario_count. No browser, nothing runs. Scenario NAMES are the bulk of
    this payload, so they ship only for a query: pass query='<substring>'
    (matches path, feature, scenario or tag) to get the matching features with
    their scenario names. workspace overrides the server's default workspace
    for this call."""
    return core.list_tests(workspace=_ws(workspace), query=query)


@_tool()
def validate_feature(content: str, workspace: str | None = None) -> dict:
    """Dry-run Gherkin text against Noodle's deterministic step-pattern table
    without running anything. Returns per-step matched/unmatched so a calling
    agent can fix unmatched steps before writing the file. workspace overrides
    the server's default workspace for this call."""
    return core.validate_feature(content, workspace=_ws(workspace))


@_tool()
def write_feature(path: str, content: str, overwrite: bool = False,
                  workspace: str | None = None) -> dict:
    """Write caller-authored Gherkin into the workspace (path must be under
    the tests dir and end in .feature). Content is validated as Gherkin first;
    steps that would need an LLM fallback at runtime are reported back.
    workspace overrides the server's default workspace for this call."""
    return core.write_feature(path, content, overwrite=overwrite,
                              workspace=_ws(workspace))


@_tool()
def probe_page(url: str, click: list[str] | None = None,
               do: list[str] | None = None,
               search: str | None = None, suggest: str | None = None,
               follow: str | None = None, expect: list[str] | None = None,
               compact: bool = True,
               open_native_controls: bool = False,
               max_reveal_depth: int = 1, discover: bool = False,
               find: str | None = None,
               workspace: str | None = None) -> dict:
    """Scout a page BEFORE authoring — fold ALL discovery into this one call;
    a second probe is a wasted browser launch. One headless load returns every
    actionable control (hidden trigger zones included): kind, name, selector,
    needs_pom with paste-ready pom_yaml, copy-ready suggested_steps (use
    as-is — never re-derive via search_step), exact headings for assertions.
    `click`: reveal-control names pressed in order (case/hyphen/
    space-insensitive; raw selectors pass; REAL clicks — never a
    state-mutating button), each under "revealed".
    `do` (NOOD_0144): ONE stateful transaction — "enter <v> in <field>" /
    "select <opt> from <dropdown>" / "click <name>" in order, run for REAL
    (save/submit included), each delta under "revealed" — fill → save →
    new-state in ONE session; {env:KEY} resolves engine-side (never paste
    a raw secret).
    `open_native_controls`: enumerates <select> options and click-opens
    custom comboboxes (bounded, never state-mutating) → `dropdown_options`.
    `search`: runs the site search and summarizes the results page incl. the
    "NN results" element + count-floor assertion. `suggest` (NOOD_0141):
    types the term per-character and captures the TYPEAHEAD — exact strings,
    row selectors, no-op icon flags, copy-ready steps; use when the goal
    picks a suggestion. `follow` (with suggest): clicks the row matching
    this text (fuzzy), summarizes the landed page; steps carry EXACT row
    text. `expect`: texts verified on the final page, a found/not-found
    verdict each; hits become `should see` steps. `discover` (NOOD_0136):
    bounded depth-1 auto-reveal when you DON'T know trigger names (never a
    state-mutating name), each delta under "revealed"; popups, permission
    prompts, search and requested assertions do NOT imply discover.
    Iframes/open shadow roots are collected (scoped "frames" blocks carry
    switch-frame; POM can't reach into a frame). Selectors are proven
    unique in scope; `author_ready: false` = fix the named warning first.
    Canvas-only/Flutter without semantics returns coverage: visual_only, not
    fake selectors. compact=True (default) = bounded author-ready payload.
    `find`: only matches, pre-cap — no spill greps. `url`: several URLs OK
    (space/comma), one browser, acts on the LAST."""
    result = core.probe_page(url, click=click, do=do, search=search,
                             suggest=suggest, follow=follow, expect=expect,
                             open_native_controls=open_native_controls,
                             max_reveal_depth=max_reveal_depth,
                             discover=discover, workspace=_ws(workspace))
    if find:
        # NOOD_0169 — pre-cap substring filter: the one control the compact
        # cap hid, whole, instead of a payload-spill grep round trip.
        from noodle.agents.web.probe import find_controls
        return {"find": find, "matches": find_controls(result, find)}
    if compact:
        from noodle.agents.web.probe import compact_payload
        return compact_payload(result)
    return result


@_tool()
def probe_app(platform: str, compact: bool = True) -> dict:
    """NOOD_0136 — native-app probe, the probe_page of Appium: start the
    platform's session (android|ios|windows|mac; app from NOODLE_<PLATFORM>_APP,
    NOODLE_APPIUM_CAPS / NOODLE_APPIUM_URL honoured like a tagged run),
    snapshot the accessibility tree ONCE, and return every interactive node
    normalized: kind, name, lookup strategy (accessibility_id / id / xpath —
    the same chain steps resolve through), visibility/enabled state, suggested
    step, and paste-ready POM entries for nameless nodes. Snapshot-only —
    nothing is tapped; reach deeper app states by running an explicit
    scenario. A tree with no accessible names returns coverage: visual_only
    with the @ocr_fallback guidance instead of fabricated selectors. compact
    (default true, like probe_page) caps the node list at 25 visible-first with
    a `truncated` note — author_ready, coverage, warnings and POM entries are
    never capped; pass compact=false only when the cap hid something."""
    return core.probe_app(platform.lower(), compact=compact)


@_tool()
def inspect_locator(url: str, text: str) -> dict:
    """Debug WHY a locator phrase does/doesn't resolve (NOOD_0115): opens url
    headless and runs the exact resolution machinery find() uses against
    `text`, returning every candidate — source (visible text node / image alt /
    aria-label / title / POM key / DOM attribute scan), match count, and
    per-match tag/text/visibility — plus what find() actually picks and any
    self-heal tier it needed. Call this when a step's element resolves to the
    wrong thing or times out despite being on the page (e.g. a caption that
    exists only as image alt text), instead of writing a throwaway Playwright
    script to poke at the DOM."""
    return core.inspect_locator(url, text)


@_tool()
def search_step(query: str, workspace: str | None = None) -> dict:
    """Find the closest existing Noodle step for a plain-English action
    description (e.g. "clear the cart") from the curated step dictionary.
    workspace overrides the server's default workspace for this call."""
    return core.search_step(query, workspace=_ws(workspace))


@_tool()
def get_rca(workspace: str | None = None, compact: bool = True) -> str:
    """Root-cause analysis of every failed/errored scenario from the last
    run. compact=True (default, NOOD_0117) is the cheap-evidence-first read:
    verdict + failing step + suggested fix per failure, a few lines total —
    usually all a fix needs; read it BEFORE any screenshot (vision tokens)
    or network capture. compact=False returns the full markdown report.
    workspace overrides the server's default workspace for this call."""
    return core.rca(workspace=_ws(workspace), compact=compact)


@_tool()
def log_diagnostic(app: str, triggers: list[str], summary: str,
                   timeline: str | None = None,
                   suspected_cause: str | None = None,
                   fixes_tried: str | None = None,
                   duration_min: float | None = None,
                   attempts: int | None = None,
                   agent: str | None = None, agent_cost: str | None = None,
                   session: str | None = None,
                   workspace: str | None = None) -> dict:
    """NOOD_0147 — session-end failure self-report, called AT MOST ONCE per
    developed test and ONLY when a trigger fired: hard-fail (dev-fix cap
    exhausted, still red), first-attempt-fail (first run of the fresh test
    was red), slow-dev (wall clock over NOODLE_DIAG_SLOW_MIN, default 20
    min), over-budget (YOUR own session spend over NOODLE_DIAG_COST_BUDGET,
    default 20 AIC/credits), or manual (the user's prompt asked for a
    diagnostic). Write everything from session memory — summary (required),
    timeline, suspected_cause, fixes_tried, duration_min, attempts, agent
    ('codex 5.3'), agent_cost ('23 AIC') — the engine appends the last-run
    result, compact RCA verdict, llm_cost and version itself, and scrubs
    secret values. The file lands in the workspace's gitignored
    diagnostics/ folder, deduped per session and capped at NOODLE_DIAG_MAX
    (default 25). No trigger fired → do not call this at all. workspace
    overrides the server's default workspace for this call."""
    from noodle import diagnostics as _diag
    return _diag.write_diagnostic(
        _ws(workspace), app=app, triggers=triggers, summary=summary,
        timeline=timeline, suspected_cause=suspected_cause,
        fixes_tried=fixes_tried, duration_min=duration_min,
        attempts=attempts, agent=agent, agent_cost=agent_cost,
        session=session)


@_tool()
def serve_report(workspace: str | None = None, report_dir: str | None = None,
                 port: int = 0) -> dict:
    """NOOD_0082 — host the last run's reports (Allure HTML + rca.html under
    one URL root) on localhost, in the background: returns immediately with
    the URLs to hand to the user. Missing reports are rebuilt from the last
    run's allure-results first, so re-hosting works from a fresh server too.
    port=0 (default) picks a free port. report_dir serves an older/extracted
    report instead (see list_reports for what exists). Always binds 127.0.0.1
    — reports can contain credential screenshots. NOOD_0161: the server is a
    DETACHED child, so its URLs survive this MCP server restarting, and a live
    server for the same reports root is reused — the URL stays the same run
    after run. It lives until stop_report_server or `noodle report stop`.
    workspace overrides the server's default workspace for this call."""
    return core.serve_report(workspace=_ws(workspace), report_dir=report_dir, port=port)


@_tool()
def stop_report_server(workspace: str | None = None) -> dict:
    """Stop every report server this workspace hosts. NOOD_0161: that includes
    the detached children serve_report spawns, which outlive this MCP server —
    an in-process-only stop left them running and unstoppable from here. Call
    it only once the user is done with the links: a stopped server is exactly
    the "this site can't be reached" the user sees next time they click one."""
    return core.stop_report_servers(workspace=_ws(workspace))


@_tool()
def list_reports(workspace: str | None = None) -> dict:
    """NOOD_0082 — what can be served: the live reports root (whether the
    Allure report and rca.html exist, and when the Allure report was
    generated) plus the timestamped archives/artifacts_<stamp>.zip snapshots
    of earlier runs (`noodle report serve <stamp>` re-hosts one). workspace
    overrides the server's default workspace for this call."""
    return core.list_reports(workspace=_ws(workspace))


@_tool()
def init_workspace(path: str, llm: str | None = None,
                   model: str | None = None) -> dict:
    """Scaffold a fresh Noodle test workspace at path (created if missing) —
    noodle.yaml, .env, README, AGENTS.md agent instructions, and a
    noodle_tests/sample_app/ template package (features/, resources/,
    report/), plus engine glue — the CLI's `noodle init` without a shell
    (NOOD_0084). Existing
    files are never overwritten, so it's safe on a partial workspace. llm
    (ollama|claude|gemini) and model persist NOODLE_MODEL into the new .env.
    No workspace resolution here — the directory doesn't exist yet by
    definition — but over streamable-http the path must still fall under a
    --workspace-root, same trust boundary as every other tool."""
    resolved = Path(path).resolve()
    if _ALLOWED_ROOTS is not None and not any(
            resolved == root or resolved.is_relative_to(root)
            for root in _ALLOWED_ROOTS):
        raise ValueError(
            f"path {path!r} is outside the roots this server was started "
            "with (--workspace / --workspace-root)")
    return core.init_workspace(path, llm=llm, model=model)


@_tool()
def cost_estimate(target: str, model: str | None = None,
                  workspace: str | None = None) -> dict:
    """Pre-flight LLM token/dollar estimate for a file (NOOD_0084): the
    model-correct input-token count plus the input-cost dollar floor (output
    tokens are unknowable pre-run). target is a prompt or .feature file,
    resolved workspace-relative. model defaults to the workspace .env's
    NOODLE_MODEL. This is the estimate half of `noodle cost`; the last run's
    actual spend is get_last_result's llm_cost. workspace overrides the
    server's default workspace for this call."""
    return core.cost_estimate(target, model=model, workspace=_ws(workspace))


@_tool()
def server_info() -> dict:
    """Identity of this server process: installed noodle version, start time
    (UTC), pid, and default workspace. A running server never hot-reloads —
    call this when results look inconsistent with the code you just changed:
    a started_at older than your last deploy/pull means the process is
    serving stale code and needs a restart (see docs/mcp-guide.md)."""
    return _info()


# NOOD_0158 — a doc at or under this rides back whole; past it the caller gets
# the section index and picks. agent-playbook.md is 57 KB: returning it whole
# cost a spilled tool result plus 7 recovery greps, to use one 4 KB section.
DOC_WHOLE_MAX_BYTES = 8_000


def _doc_sections(text: str) -> list[dict]:
    """Split a doc on its `## ` headings. The preamble (anything before the
    first heading) is section one, so nothing is unreachable."""
    secs: list[dict] = []
    title, buf = "(preamble)", []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if "".join(buf).strip():
                secs.append({"title": title, "body": "".join(buf)})
            title, buf = line[3:].strip(), [line]
        else:
            buf.append(line)
    if "".join(buf).strip():
        secs.append({"title": title, "body": "".join(buf)})
    return secs


def _pick_section(secs: list[dict], want: str) -> dict | None:
    """Match a section by 1-based number, exact title, or substring — an agent
    quoting a heading loosely ('steps dictionary') should not need a retry."""
    w = want.strip()
    if w.lstrip("#").isdigit():
        i = int(w.lstrip("#"))
        return secs[i - 1] if 1 <= i <= len(secs) else None
    for s in secs:
        if s["title"].lower() == w.lower():
            return s
    for s in secs:
        if w.lower() in s["title"].lower():
            return s
    return None


def _docs_dir() -> Path | None:
    """The framework's docs/ folder. Resolves next to the installed noodle
    package — present in a repo checkout / editable install. ponytail: wheel
    installs don't ship docs/; package them if that install mode matters."""
    d = Path(__file__).resolve().parent.parent.parent / "docs"
    return d if d.is_dir() else None


@_tool()
def read_docs(name: str | None = None, query: str | None = None,
              section: str | None = None) -> dict:
    """Framework documentation lookup (NOOD_0089) — keeps agent context lean:
    call this when you need Noodle detail instead of guessing or pasting docs
    into prompts. No args → the list of available docs, each with a one-line
    summary and its byte cost. name (e.g. 'agent-playbook') → that doc, whole
    when it is small; a large doc returns its SECTION INDEX instead
    (NOOD_0158) — pick one and call again with section='<heading or its
    number>' rather than pulling 57 KB into context. query → matching lines
    (with doc + section + line) across all docs, for one fact, not a file."""
    d = _docs_dir()
    if d is None:
        return {"error": "docs/ not found next to the installed noodle package "
                         "— read them at https://github.com/gheeno/noodle/tree/main/docs"}
    files = sorted(d.glob("*.md"))
    if name:
        stem = name.removesuffix(".md")
        f = d / f"{stem}.md"
        if not f.is_file():
            return {"error": f"no doc named {name!r}",
                    "available": [p.stem for p in files]}
        text = f.read_text()
        secs = _doc_sections(text)
        if section:
            hit = _pick_section(secs, section)
            if hit is None:
                return {"error": f"no section matching {section!r} in {f.name}",
                        "sections": [s["title"] for s in secs]}
            return {"name": f.name, "section": hit["title"],
                    "content": hit["body"]}
        if len(text) <= DOC_WHOLE_MAX_BYTES or len(secs) < 2:
            return {"name": f.name, "content": text}
        return {"name": f.name, "bytes": len(text),
                "note": (f"{len(text) // 1000} KB — too large to return whole. "
                         f"Call read_docs(name={stem!r}, section='<title or #>') "
                         f"for one section, or query=… to grep every doc."),
                "sections": [{"n": i, "title": s["title"], "bytes": len(s["body"])}
                             for i, s in enumerate(secs, 1)]}
    if query:
        q = query.lower()
        hits = []
        for f in files:
            sec = "(preamble)"       # NOOD_0159: name the section so a hit is
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if line.startswith("## "):     # retrievable without an index
                    sec = line[3:].strip()     # round trip
                if q in line.lower():
                    hits.append({"doc": f.stem, "section": sec, "line": i,
                                 "text": line.strip()})
                    if len(hits) >= 50:
                        return {"query": query, "hits": hits, "truncated": True}
        return {"query": query, "hits": hits}

    def _entry(f: Path) -> dict:
        text = f.read_text()
        summary = next((ln.strip() for ln in text.splitlines()
                        if ln.strip() and not ln.startswith("#")), "")
        # NOOD_0159: cost rides in the index so the caller knows what a
        # retrieval spends before making it.
        return {"name": f.stem, "summary": summary, "bytes": len(text),
                "sections": len(_doc_sections(text))}
    return {"docs": [_entry(f) for f in files]}


@mcp.resource("noodle://vocabulary")
def vocabulary() -> str:
    """The canonical step sentences Noodle resolves deterministically — write
    Gherkin using only these shapes (changing quoted values/field names) and
    validate_feature/write_feature will accept it with no LLM anywhere."""
    from noodle.repl import prompts
    return prompts.STEP_VOCABULARY


def _require_key(app, key: str):
    """Minimal ASGI gate for the HTTP transport: 401 unless the request
    carries the shared key as a Bearer token or x-api-key header. ponytail:
    single shared key, constant-time compare — move to real OAuth via the
    MCP SDK's auth provider if per-caller identity ever matters."""
    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            supplied = headers.get("x-api-key") or \
                headers.get("authorization", "").removeprefix("Bearer").strip()
            if not hmac.compare_digest(supplied, key):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)
    return guarded


def _load_workspace_env(workspace: str) -> None:
    """NOOD_0055 — load the workspace's .env into this process, the same file
    the REPL (`_configure_llm`) and behave's before_all load. Without it the
    NOODLE_MODEL persisted by `noodle init --llm` (plus NOODLE_LLM_URL /
    NOODLE_GROUND / NOODLE_ARTIFACTS_DIR…) was invisible to every MCP tool.
    Existing process env wins — a host-configured var stays authoritative."""
    from dotenv import load_dotenv

    from noodle import config
    load_dotenv(Path(workspace) / config.load(workspace)["env_file"])


def main(argv: list[str] | None = None) -> None:
    global _WORKSPACE, _ALLOWED_ROOTS
    parser = argparse.ArgumentParser(prog="noodle-mcp")
    parser.add_argument("--workspace", default=".",
                        help="Workspace dir holding noodle.yaml, noodle_tests/, .env")
    parser.add_argument("--workspace-root", action="append", default=[],
                        metavar="DIR",
                        help="Directory whose subdirectories per-call "
                             "`workspace` overrides may point into "
                             "(repeatable). Unset: stdio allows any path — "
                             "the spawning host is already trusted; "
                             "streamable-http locks overrides to the "
                             "--workspace dir only.")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"],
                        default="stdio",
                        help="stdio for local hosts (Claude Code, MAF "
                             "MCPStdioTool); streamable-http for remote hosts "
                             "(Azure AI Foundry, MAF MCPStreamableHTTPTool)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="HTTP bind address (streamable-http only)")
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP port (streamable-http only; /mcp endpoint)")
    args = parser.parse_args(argv)
    _WORKSPACE = args.workspace
    if args.workspace_root:
        _ALLOWED_ROOTS = [Path(r).resolve() for r in args.workspace_root] + \
                         [Path(args.workspace).resolve()]
    elif args.transport != "stdio":
        # Remote callers get no per-call escape hatch unless roots were
        # explicitly allowed at startup.
        _ALLOWED_ROOTS = [Path(args.workspace).resolve()]
    _load_workspace_env(_WORKSPACE)
    info = _info()
    print(f"noodle-mcp {info['noodle_version']} pid={info['pid']} "
          f"workspace={info['workspace']} started={info['started_at']} — "
          "no hot reload; restart after noodle code changes",
          file=sys.stderr)

    if args.transport == "stdio":
        mcp.run()
        return

    key = os.environ.get("NOODLE_MCP_API_KEY", "")
    if not key and args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("set NOODLE_MCP_API_KEY to bind beyond localhost — "
                     "an open MCP server executes tests for anyone who finds it")
    mcp.settings.host, mcp.settings.port = args.host, args.port
    app = mcp.streamable_http_app()
    if key:
        app = _require_key(app, key)
    import uvicorn  # ships with the mcp extra
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

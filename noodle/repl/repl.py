"""noodle repl — an interactive shell that maps plain English to engine
commands. Keyword matching, no LLM required (Phase 2). `create test` uses the
rule-based generator (Phase 3); `--llm ollama|claude` upgrades generation and
summaries to a model (Phases 3/5). All it does is shell out to `noodle ...`.

LLM config persists across sessions: `noodle init --llm ollama` writes
NOODLE_MODEL into the workspace .env, and this REPL loads that .env itself
(the engine's own dotenv load only happens inside behave's before_all, which
this process never runs) — so a fresh terminal just needs `noodle repl`,
no --llm/--model flags, unless you want to override the persisted model.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from noodle import config
from noodle.repl import core

HELP = """commands (plain English works too):
  run [all]                 run every feature
  run <name|tag>            run a feature file or a tag
  run that / run it / run the test   run the last created/run feature
    (remembered across sessions in artifacts/agent_state.json)
  list / what tests         list scenarios
  create test for <desc> at <url>   scaffold a feature + POM + environment/
    add 'overwrite' to replace an existing feature of the same name
    (free prose with a create-ish verb + 'test' + a URL also works —
     quoted values fill the template's search/enter/assert slots)
    (a new test with no <placeholders> left also runs once right away,
     so you get a verified result, not just a file)
  generate the secrets file for <app>        one supporting file at a time
  generate the environments yaml for <app>     (secrets, environments, pom,
  generate the pom for <app>                    preconditions, payload,
  generate a precondition for <app>             function, data)
  summary / what failed     summarise the last run
  artifacts                 list what's in artifacts/, by category
  open the report           regenerate + open the Allure HTML report (localhost)
  serve the report          regenerate + serve it on 0.0.0.0:8000 for teammates
  rca report                root-cause the last failed run (same as `noodle rca-report`)
  serve the rca report      regenerate the RCA and serve it over localhost
  find a step for <description>   search the step dictionary (or "step-search ...")
    no good match -> offers to draft + save a new one (y/N)
  help                      this message
  quit / exit               leave

With --llm, any free-form request works too, e.g.
  "go to youtube.com, search for MKBHD, and check results show MKBHD"
  "generate a feature in busterblock for the search bar"
  "generate the secrets file for busterblock to store the username and password"
Compound asks in one line also work, e.g.
  "create a test for youtube search, run it, and show me the report"

With --llm, running a test you just created also gets one automatic repair
attempt if it fails — kept only if the retry has fewer failures.
"""


def _noodle(*args, workspace="."):
    """Invoke the engine CLI in the workspace dir so it finds the right config."""
    subprocess.run([sys.executable, "-m", "noodle.cli", *args], cwd=workspace)


def _serve_rca(workspace: str) -> None:
    """Regenerate the RCA and open its styled HTML rendering
    (noodle/reporting/rca_report.py:open_html — same underlying data as
    `noodle rca-report`, laid out as a real table instead of markdown source).
    Self-contained page (inline CSS, no fetch/XHR) — a plain file:// open
    works fine, unlike the Allure report, so no local server is needed."""
    from noodle.reporting import paths as _paths
    from noodle.reporting import rca_report as _rca

    reports_dir = Path(workspace) / _paths.reports_dir()
    _noodle("rca-report", "--out", str(reports_dir / "rca.md"), workspace=workspace)
    html_path = _rca.open_html(str(Path(workspace) / _paths.results_dir()),
                                str(reports_dir / "rca.html"))
    print(f"→ Opened RCA at {html_path}")


def _open_allure(workspace: str, serve: bool = False) -> None:
    """Regenerate the Allure HTML report from the latest results, then open
    it (localhost) or serve it (0.0.0.0). Regenerating first matters: a
    serial `noodle run` only writes allure-results/ — the HTML report is
    rebuilt automatically on --parallel runs alone — so opening without
    generating shows the previous run, or nothing."""
    _noodle("report", "generate", workspace=workspace)
    _noodle("report", "serve" if serve else "open", workspace=workspace)


def _features(cfg, workspace):
    return Path(workspace) / cfg["tests_dir"]


def _find_feature(cfg, workspace, name) -> str | None:
    # NOOD_0045: logic moved to core.find_feature so the MCP server shares it.
    return core.find_feature(cfg, workspace, name)


def _normalize_url(url: str) -> str:
    return core.normalize_url(url)


def _remember_created(state: dict, feat, pom, workspace: str) -> None:
    """NOOD_0055 — persist created-file paths workspace-relative, the one shape
    core.resolve_target/write_feature use: a cwd-relative path breaks as soon
    as a different process (MCP server, a REPL started elsewhere) reads
    agent_state.json."""
    state["last_feature"] = os.path.relpath(feat, workspace)
    state["last_pom"] = os.path.relpath(pom, workspace)


def _ws_path(workspace: str, rel) -> Path:
    return Path(workspace) / rel


def _autorun_after_create(state: dict, workspace: str, llm: str | None) -> None:
    """NOOD_0030 §2.2 — 'create a test' hands back a verified-green file, not
    just a file: the freshly written feature runs once immediately (plus the
    existing one-shot try_fix repair with --llm). Gated on the generated files
    containing no <placeholder> values — a template with '<css selector>'
    holes structurally cannot pass, so running it would only produce a
    misleading red."""
    from noodle.repl import generate
    feat, pom = state.get("last_feature"), state.get("last_pom")
    if not feat:
        return
    texts = [_ws_path(workspace, feat).read_text()]
    if pom and _ws_path(workspace, pom).exists():
        texts.append(_ws_path(workspace, pom).read_text())
    if any(generate._PLACEHOLDER_RE.search(t) for t in texts):
        print(f"→ Run: noodle run {feat}   (fill in the <placeholders> first)")
        return
    print("→ Running the new test once to verify it passes...")
    _noodle("run", feat, workspace=workspace)
    if llm and pom:
        from noodle.repl import reflect
        reflect.try_fix(_ws_path(workspace, feat), _ws_path(workspace, pom), workspace)
    state["autoran_feature"] = feat


_CONNECTOR_RE = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s+", re.I)


def _clause_to_step(clause: str) -> dict | None:
    """Map one clause of a compound request onto a plan-step dict — the
    deterministic counterpart of what _extract_plan asks the model for.
    None = clause not understood."""
    low = clause.lower().strip()
    m = re.match(
        r"(?:please )?(?:create|generate|make|write)(?: me)?(?: a| a new)?\s*test(?: case)?"
        r"(?: for)?\s+(.+?)(?: at | on | targeting )['\"]?(https?://\S+|\S+\.\S+)",
        clause, re.I)
    if m:
        return {"action": "create", "description": m.group(1).strip(" '\""),
                "url": m.group(2).rstrip("'\",.;")}
    if re.fullmatch(r"(?:please )?run(?: it| that| this| the tests?| them)?", low):
        return {"action": "run"}
    if re.search(r"\b(show|open|display|serve)\b.*\breport\b", low):
        return {"action": "open_report"}
    if re.search(r"\b(summary|what failed|results?)\b", low):
        return {"action": "summary"}
    return None


def _extract_plan_rules(text: str) -> list[dict]:
    """NOOD_0052 — deterministic compound-request splitter, the no-LLM
    counterpart of _extract_plan ("create a test for X at Y, run it and show
    me the report" as one turn, no model needed). All-or-nothing: one
    unrecognized clause → [] so a half-understood compound never
    half-executes — it falls through to the single-intent grammars (and the
    LLM planner, when configured) instead."""
    clauses = [c.strip() for c in _CONNECTOR_RE.split(text) if c and c.strip()]
    if len(clauses) < 2:
        return []
    plan = []
    for clause in clauses:
        step = _clause_to_step(clause)
        if step is None:
            return []
        plan.append(step)
    # a "plan" that never creates or runs anything isn't a compound request
    return plan if any(s["action"] in {"create", "run"} for s in plan) else []


def _extract_plan(text: str) -> list[dict]:
    """LLM fallback: break a compound free-form request ("create a test for
    X, run it, and show the report") into an ordered list of
    {action: create|run|summary|open_report, ...} steps. [] when nothing
    usable parses."""
    import json

    from noodle.llm.client import ask
    from noodle.repl import prompts

    try:
        raw = ask(prompts.plan_prompt(text), system=prompts.SYSTEM)
        m = re.search(r"\[.*\]", raw, re.S)
        steps = json.loads(m.group(0)) if m else []
    except Exception as e:
        print(f"(intent extraction failed: {e})")
        return []
    return [s for s in steps if isinstance(s, dict) and s.get("action") in
            {"create", "scaffold", "run", "summary", "open_report"}]


def _lookup_app_url(cfg: dict, workspace: str, app: str) -> str | None:
    """An app's own resources/[<app>_]environments.yaml, if it already has
    one — lets 'generate a feature for <app>' skip re-stating a URL it
    already scaffolded (docs/feature-packages.md)."""
    import yaml
    res_dir = Path(workspace) / cfg["tests_dir"] / "web" / app / "resources"
    env_path = next((p for p in (res_dir / f"{app}_environments.yaml",
                                 res_dir / "environments.yaml")
                     if p.exists()), None)
    if env_path is None:
        return None
    try:
        data = yaml.safe_load(env_path.read_text()) or {}
    except Exception:
        return None
    return data.get(app)


def _run_scaffold(step: dict, cfg: dict, workspace: str, llm: str | None,
                   state: dict, overwrite: bool) -> None:
    """The 'scaffold' plan action (NOOD_0019): one supporting file, not a
    whole test — 'generate the secrets file for busterblock', 'generate the
    POM for busterblock', etc."""
    from noodle.repl import generate

    kind = step.get("kind")
    app = step.get("app") or state.get("last_app")
    if not app:
        print("Which app is this for? (e.g. \"... for busterblock\")")
        return
    state["last_app"] = app

    if kind == "feature":
        desc = step.get("description") or f"{app} test"
        url = _lookup_app_url(cfg, workspace, app)
        if not url:
            print(f"No base URL known for {app!r} yet — say "
                  f"\"create test for {desc} at <url>\" once to set one up.")
            return
        gen = generate.generate_llm if llm else generate.generate
        result = gen(desc, url, cfg, workspace, overwrite=overwrite)
        if result is None:
            return
        feat, pom = result
        _remember_created(state, feat, pom, workspace)
        print(f"→ Wrote {feat}\n→ Wrote {pom}")
        return

    generate.scaffold_one(
        kind, app, cfg, workspace,
        url=step.get("url"), fields=step.get("fields"), name=step.get("name"))


def _run_step(step: dict, cfg: dict, workspace: str, llm: str | None,
              state: dict, overwrite: bool) -> None:
    """Execute one step of a plan from _extract_plan, updating `state` the
    same way the single-shot create/run commands do."""
    action = step["action"]
    if action == "create":
        from noodle.repl import generate
        desc, url = step.get("description"), step.get("url")
        if not desc or not url or str(url).lower() == "null":
            return
        # NOOD_0052 — plans can now come from the rule-based splitter with no
        # model configured; fall back to the template generator there.
        gen = generate.generate_llm if llm else generate.generate
        result = gen(str(desc), _normalize_url(str(url)),
                     cfg, workspace, overwrite=overwrite)
        if result is None:
            return
        feat, pom = result
        _remember_created(state, feat, pom, workspace)
        state["last_app"] = Path(feat).parent.parent.name
        print(f"→ Wrote {feat}\n→ Wrote {pom}")
        _autorun_after_create(state, workspace, llm)
    elif action == "scaffold":
        _run_scaffold(step, cfg, workspace, llm, state, overwrite)
    elif action == "run":
        feat = state.get("last_feature")
        if not feat:
            print("Nothing created yet this session to refer to — say what to run.")
            return
        # Skip one explicit "run" right after an autorun of the same file —
        # the plan "create X, run it" would otherwise run it twice back to back.
        if state.pop("autoran_feature", None) == feat:
            print("→ (already ran right after creation — results are current)")
            return
        _noodle("run", feat, workspace=workspace)
        if state.get("last_pom"):
            from noodle.repl import reflect
            reflect.try_fix(_ws_path(workspace, feat),
                            _ws_path(workspace, state["last_pom"]), workspace)
    elif action == "summary":
        _noodle("summary", "--llm", llm or "none", workspace=workspace)
    elif action == "open_report":
        _open_allure(workspace)


_PRONOUN_TARGET = re.compile(
    r"^(it|that|this|that one|this one|the last one"
    r"|the one (?:you )?(?:just )?(?:made|created|wrote))$", re.I)

# Rule-based fast path for a single supporting file (NOOD_0019) — works with
# no LLM, same "deterministic first" philosophy as the rest of the engine.
# Compound/free-text asks (a description, "put it in Y") fall through to the
# LLM plan analyzer below instead of trying to out-regex arbitrary phrasing.
_SCAFFOLD_KIND_MAP = {
    "secret": "secrets", "secrets": "secrets",
    "environment": "environments", "environments": "environments", "env": "environments",
    "pom": "pom", "pageobject": "pom", "pageobjects": "pom",
    "precondition": "preconditions", "preconditions": "preconditions",
    "payload": "payload", "payloads": "payload",
    "function": "function", "functions": "function",
    "data": "data", "csv": "data",
}
_SCAFFOLD_CMD_RE = re.compile(
    r"^(?:generate|create|add|scaffold|make)\s+(?:a |the )?"
    r"(secrets?|environments?|env|pom|page\s?objects?|preconditions?|payloads?|functions?|data|csv)"
    r"\b(?:\s+files?|\s+yaml)*\s*(.*)$", re.I)
_FIELD_WORDS = ("username", "password", "email", "token", "api key")


def _match_scaffold_command(text: str) -> dict | None:
    m = _SCAFFOLD_CMD_RE.match(text.strip())
    if not m:
        return None
    kind = _SCAFFOLD_KIND_MAP.get(re.sub(r"\s+", "", m.group(1).lower()))
    if not kind:
        return None
    tail = m.group(2)
    app_m = re.search(r"\bfor\s+([a-z0-9_]+)\b", tail, re.I) or \
        re.search(r"\bin\s+([a-z0-9_]+)\b", tail, re.I)
    fields = [w.replace(" ", "_") for w in _FIELD_WORDS if w in tail.lower()] or None
    return {"action": "scaffold", "kind": kind,
            "app": app_m.group(1).lower() if app_m else None, "fields": fields}


# NOOD_0026 — step-search-engine / step-suggestion-engine. Rule-based (no
# --llm required) trigger, same "deterministic fast path first" convention
# as the scaffold matcher above; the local LLM is only consulted *inside*
# search_step()/draft_suggestion() themselves, as a tie-breaker, never to
# recognize this command.
_STEP_SEARCH_RE = re.compile(
    r"^(?:step[- ]search|find(?: a| the)? step|search(?: for)? a step|is there a step)"
    r"(?: for| that| to)?\s+(.*)$", re.I)


def _handle_step_search(query: str, llm: str | None, workspace: str) -> None:
    from pathlib import Path

    from noodle.repl import step_suggestion_engine as sse
    from noodle.resolver import patterns as _patterns
    from noodle.resolver import step_resolver
    from noodle.resolver.step_search_engine import search_step

    docs_dir = Path(workspace) / "docs"
    step_resolver.set_docs_dir(docs_dir)
    _patterns.set_agent_patterns_dir(docs_dir)

    use_llm = bool(llm)
    result = search_step(query, use_llm=use_llm)
    if result.match:
        conf = result.confidence + (", via LLM" if result.llm_used else "")
        print(f"Best match ({conf} confidence):")
        print(f"  {result.match.step}")
        return

    print(f"No good match for: {query!r}")
    suggestion = sse.draft_suggestion(query, result, use_llm=use_llm)
    if not suggestion.fits_existing_type:
        print(suggestion.rationale)
        return

    print("Suggested new step:")
    print(f"  {suggestion.keyword} {suggestion.phrase}")
    print(f"  action_type: {suggestion.action_type}  ({suggestion.rationale})")
    try:
        answer = input("Add this step? [y/N] ").strip().lower()
    except EOFError:
        answer = "n"
    if answer in {"y", "yes"}:
        written = sse.accept_suggestion(suggestion)
        print(f"→ Wrote {written['patterns_file']}")
        print(f"→ Wrote {written['dictionary_file']}")
    else:
        print("Not saved.")


def dispatch(line: str, cfg: dict, workspace: str, llm: str | None,
              state: dict | None = None) -> bool:
    """Run one command. Returns False to exit the REPL, True to keep going.

    `state` is the REPL's short-term memory (which file was last created) —
    a plain dict the caller keeps alive across calls, so "run that" can
    resolve to the previous turn's file. Omit it for one-off calls (tests).
    """
    if state is None:
        state = {}
    text = line.strip()
    low = text.lower()
    if not text:
        return True
    if low in {"quit", "exit", "q"}:
        return False
    if low in {"help", "?"}:
        print(HELP)
        return True

    if low in {"list", "what tests", "what tests do we have", "list all scenarios"}:
        _noodle("list", cfg["tests_dir"], workspace=workspace)
        return True

    # NOOD_0052 — deterministic-first compound handling: "create a test for X
    # at Y, run it and show me the report" resolves as an ordered plan with no
    # model configured. Must run before every single-intent catch-all below —
    # the summary/report branch would steal "run it and show me the report",
    # and the single-create regex would eat the whole line and capture
    # "url.com," with the trailing comma. All-or-nothing parse, so anything
    # it doesn't fully understand falls through unchanged.
    plan = _extract_plan_rules(text)
    if plan:
        overwrite = bool(re.search(r"\b(overwrite|replace|force)\b", low))
        for step in plan:
            _run_step(step, cfg, workspace, llm, state, overwrite)
        return True

    # "create" guard: a compound request like "create a test for X ... show me
    # the report" must fall through to the free-form plan below, not get
    # stolen here by the word "report". These three checks must run before
    # the generic summary/report catch-all below, since they all also match
    # its \breport\b pattern.
    if re.search(r"\bartifacts\b", low) and "create" not in low:
        _noodle("artifacts", workspace=workspace)
        return True

    if "rca" in low and "create" not in low:
        if re.search(r"\bserve\b", low):
            _serve_rca(workspace)
        else:
            _noodle("rca-report", workspace=workspace)
        return True

    if re.search(r"\bopen\b", low) and "report" in low and "create" not in low:
        _open_allure(workspace)
        return True

    # "serve the rca report" never lands here — the rca branch above wins.
    if re.search(r"\bserve\b", low) and "report" in low and "create" not in low:
        _open_allure(workspace, serve=True)
        return True

    if re.search(r"\b(summary|what failed|report)\b", low) and "create" not in low:
        _noodle("summary", "--llm", llm or "none", workspace=workspace)
        return True

    m = re.search(r"create (?:a )?test (?:for )?(.+?)(?: at | on )(https?://\S+|\S+\.\S+)", text, re.I)
    if m:
        from noodle.repl import generate
        desc, url = m.group(1).strip(), _normalize_url(m.group(2).strip())
        overwrite = bool(re.search(r"\b(overwrite|replace|force)\b", low))
        gen = generate.generate_llm if llm else generate.generate
        result = gen(desc, url, cfg, workspace, overwrite=overwrite)
        if result is None:
            return True
        feat, pom = result
        _remember_created(state, feat, pom, workspace)
        state["last_app"] = Path(feat).parent.parent.name
        print(f"→ Wrote {feat}\n→ Wrote {pom}")
        _autorun_after_create(state, workspace, llm)
        return True

    scaffold = _match_scaffold_command(text)
    if scaffold:
        overwrite = bool(re.search(r"\b(overwrite|replace|force)\b", low))
        _run_scaffold(scaffold, cfg, workspace, llm, state, overwrite)
        return True

    if low.startswith("run"):
        rest = text[3:].strip()
        if not rest or rest.lower() in {"all", "all tests", "the tests", "everything"}:
            _noodle("run", cfg["tests_dir"], workspace=workspace)
            return True
        # strip filler: "run the smoke tests" -> "smoke"
        target = re.sub(r"\b(the|tests?|test|please)\b", "", rest, flags=re.I).strip()
        if not target or _PRONOUN_TARGET.match(target):
            # "run it" / "run the test" — session state first, then the
            # persisted agent_state.json / newest .feature (NOOD_0045 Phase 2)
            # so a fresh process still knows which test "the test" is.
            feat = state.get("last_feature")
            if not feat:
                resolved = core.resolve_target(None, workspace)
                feat = resolved.get("feature")
            if not feat:
                print("Nothing created yet to refer to — say what to run.")
                return True
        else:
            feat = _find_feature(cfg, workspace, target)
        if feat:
            if state.pop("autoran_feature", None) == feat:
                print("→ (already ran right after creation — results are current)")
                return True
            _noodle("run", feat, workspace=workspace)
            # Only self-repair a feature the agent itself just wrote and ran
            # this session — never a file the user asked to run by name.
            if llm and feat == state.get("last_feature") and state.get("last_pom"):
                from noodle.repl import reflect
                reflect.try_fix(Path(feat), Path(state["last_pom"]), workspace)
        else:
            print(f"No feature matched '{target}' — treating it as a tag filter.")
            _noodle("run", cfg["tests_dir"], "--tag", target, workspace=workspace)
        return True

    m = _STEP_SEARCH_RE.match(text)
    if m and m.group(1).strip():
        _handle_step_search(m.group(1).strip(), llm, workspace)
        return True

    # Free-form fallback: with --llm, let the model break the whole line into
    # an ordered plan (create / run / summary) and execute it step by step —
    # this is what makes a compound ask like "create a test for X, run it,
    # and show me the report" a single turn instead of three.
    if llm:
        plan = _extract_plan(text)
        if plan:
            overwrite = bool(re.search(r"\b(overwrite|replace|force)\b", low))
            for step in plan:
                _run_step(step, cfg, workspace, llm, state, overwrite)
            return True

    # NOOD_0045 Phase 5 — loose no-LLM create: free prose with a create-ish
    # verb + 'test' + a URL-looking token ("Generate a new test case targeting
    # 'example.com', ... searches for 'office chair' ...") scaffolds via
    # the rule-based generator, with quoted values slot-filled into the
    # template. Last resort by design: every exact grammar above wins first.
    if not llm:
        from noodle.repl import generate
        req = generate.parse_free_request(text)
        if req:
            overwrite = bool(re.search(r"\b(overwrite|replace|force)\b", low))
            result = generate.generate(req["description"],
                                       _normalize_url(req["url"]),
                                       cfg, workspace, overwrite=overwrite)
            if result is not None:
                feat, pom = result
                _remember_created(state, feat, pom, workspace)
                state["last_app"] = Path(feat).parent.parent.name
                print(f"→ Wrote {feat}\n→ Wrote {pom}")
                _autorun_after_create(state, workspace, llm)
            return True

    hint = "" if llm else " (free-form requests need --llm)"
    print(f"Don't understand: {text!r}. Type 'help'.{hint}")
    return True


def _configure_llm(workspace: str, cfg: dict, llm: str | None, model: str | None) -> str | None:
    """Resolve the model for this session and return the effective `llm`
    sentinel used everywhere else in this module for "free-form mode is on".

    An explicit --llm/--model wins outright. Otherwise load the workspace's
    .env (this process never goes through behave's before_all, so nothing
    else loads it) and, if `noodle init --llm` already persisted a
    NOODLE_MODEL there, return "auto" — free-form mode turns on with no flags.
    Returns None only when no model is configured anywhere.
    """
    load_dotenv(Path(workspace) / cfg["env_file"])
    if llm:
        os.environ["NOODLE_MODEL"] = model or config.LLM_PRESETS.get(llm, llm)
        if llm == "ollama":
            os.environ.setdefault("NOODLE_LLM_URL", "http://localhost:11434")
        return llm
    if os.environ.get("NOODLE_MODEL"):
        return "auto"
    return None


def run(workspace: str = ".", llm: str | None = None, model: str | None = None) -> None:
    """The interactive loop itself — what `noodle repl` (noodle/cli.py) calls
    in-process. Split out from main() (NOOD_0056) so the Typer subcommand
    doesn't have to round-trip through argv just to reach this."""
    cfg = config.load(workspace)
    llm = _configure_llm(workspace, cfg, llm, model)
    print(f"noodle repl — workspace: {Path(workspace).resolve()}"
          + (f"  llm: {os.environ['NOODLE_MODEL']}" if llm else "  (rule-based, no LLM)"))
    print("Type 'help' for commands, 'quit' to exit.\n")
    # NOOD_0045 Phase 2: pronoun memory persists across sessions.
    state: dict = core.load_state(workspace)
    try:
        while True:
            try:
                line = input("noodle> ")
            except EOFError:
                break
            keep_going = dispatch(line, cfg, workspace, llm, state)
            core.save_state(state, workspace)
            if not keep_going:
                break
    except KeyboardInterrupt:
        print()


def main(argv=None):
    """Legacy argv entry point — no longer installed as its own console
    script (NOOD_0056 folded this into `noodle repl`), kept for
    `python -m noodle.repl.repl` / direct invocation."""
    argv = sys.argv[1:] if argv is None else argv
    workspace, llm, model = ".", None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--workspace", "--llm", "--model"):
            # NOOD_0055 — a trailing flag with no value was an IndexError traceback.
            if i + 1 >= len(argv):
                print(f"{a} needs a value, e.g. {a} <value>")
                sys.exit(2)
            if a == "--workspace":
                workspace = argv[i + 1]
            elif a == "--llm":
                llm = argv[i + 1]
            else:
                model = argv[i + 1]
            i += 2
        else:
            i += 1
    run(workspace, llm, model)


if __name__ == "__main__":
    main()

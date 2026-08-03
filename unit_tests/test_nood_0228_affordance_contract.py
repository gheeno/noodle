"""NOOD_0228 (F) — the Shipped Affordance Contract.

This is the deliverable that makes it the last time.

Five tickets (0153, 0211, 0215, 0225, 0227) each stopped one producer of the
step-text evidence request and each time it came back, because a prohibition
is not a prohibition while the thing remains **executable, discoverable,
recommended, or necessary**. Prose cannot enforce that; a model follows the
executable contract, not the declared one. So the guarantee is mechanical:

  F1  enumerate every agent-visible surface, from the LIVE registries — the
      Typer command tree, the docs corpus, the packaged resources — never a
      hardcoded list, or the next command/doc escapes the contract.
  F2  assert the forbidden GRAMMAR is absent from all of them (a normalised
      pattern family, not one literal string: a rejection that knows one
      spelling just redirects the next model to a variant).
  F3  assert behavioural rejection — a string scan cannot prove the parser
      refuses — AND that the refusal is not itself copy-ready.
  F4  assert command-surface closure: every registered remediation names a
      registered command and instructs no file edit.
  F5  fail loudly: name the contract, the surface, and what matched.

Scope honesty: this guarantees a Noodle RELEASE cannot reintroduce these
affordances. It does not stop an agent inventing one — that is Workstream A's
parser rejection, asserted in F3.

The guard is proved by breaking it on purpose (test_contract_guard_detects_a_
reinserted_marker): a contract test nobody has seen fail is not known to work.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle import cli, remediation
from noodle.resolver import patterns

REPO = Path(__file__).resolve().parent.parent


# ── F2: the forbidden grammar ───────────────────────────────────────────────
# Broader than patterns.EVIDENCE_MARKER_RE on purpose. That regex anchors to
# end-of-line because it grades a STEP; a doc teaches the same thing inside a
# table cell, a sentence or a fenced block, where the anchor never fires. The
# contract has to catch the TEACHING, which is what the audited agent copied.
_EV_VERB = (r"(?:take|takes|taking|capture|captures|grab|grabs|attach|"
            r"attaches|include|includes|add|adds|save|saves)")
# Bare `capture` is deliberately NOT a standalone noun here. Ordinary prose is
# full of parentheses like "(captured var)" and "(screenshots/traces)", and a
# scanner that fires on those gets suppressed with an exception list — which is
# how a real hit slips through later. The signal is a bracket whose ENTIRE
# content is the evidence phrase; that is what a marker looks like and what a
# passing parenthetical never does.
_EV_NOUN = r"(?:screen\s*shots?|screen\s*caps?|snapshots?)"
_EV_PHRASE = (
    rf"(?:please\s+)?{_EV_VERB}\s+(?:an?\s+|the\s+)?(?:evidence\s*[:=-]?\s*)?"
    rf"(?:{_EV_NOUN}|captures?|evidence)"
    rf"|(?:evidence\s*[:=-]\s*)(?:{_EV_NOUN}|captures?|evidence)"
    rf"|{_EV_NOUN}"
    rf"|(?:no|without|skip|omit)\s+(?:{_EV_VERB}\s+)?"
    rf"(?:any\s+|the\s+)?(?:evidence\s+)?(?:{_EV_NOUN}|captures?|evidence)")
_EV_TAIL = r"(?:\s+(?:here|as\s+evidence|on\s+this\s+step|for\s+this\s+step))?"
MARKER_SCAN_RE = re.compile(
    # bracketed: the whole bracket is the request
    rf"[(\[]\s*(?:{_EV_PHRASE}){_EV_TAIL}\s*[)\]]"
    # or the dash/comma form as a STEP TAIL. Anchored to end-of-line and
    # required to follow real text, so a markdown bullet ("- capture a
    # snapshot") is not mistaken for a step whose sentence ends in one.
    rf"|(?<=\S)\s+[-–—,]\s*(?:{_EV_PHRASE}){_EV_TAIL}\s*$",
    re.IGNORECASE | re.MULTILINE)

# Every spelling the parser must refuse. Drawn from the regex family's own
# branches, so a widened parser without a widened test cannot pass quietly.
MARKER_SPELLINGS = (
    "( take a screenshot )",
    "(take a screenshot)",
    "[take a screenshot]",
    "[evidence: screenshot]",
    "- attach evidence",
    "(screenshot)",
    "( no screenshot )",
    "[no evidence]",
    "- capture a snapshot",
)


def _scan(name: str, text: str) -> list[str]:
    """F5 — a violation names its surface and what matched, both."""
    return [f"{name}: {m.group(0)!r}" for m in MARKER_SCAN_RE.finditer(text or "")]


# ── F1: enumerate the agent-visible surfaces ────────────────────────────────
def _help_text(argv: list[str]) -> str:
    import os
    prev = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "100"
    try:
        return CliRunner().invoke(cli.app, [*argv, "--help"]).output
    finally:
        if prev is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = prev


def _cli_help_surfaces() -> dict[str, str]:
    """`noodle --help` plus the help of EVERY registered command, walked from
    the live Typer tree (remediation.registered_commands) — a command added
    tomorrow is inside the contract without anyone remembering to add it."""
    out = {"noodle --help": _help_text([])}
    for path in sorted(remediation.registered_commands()):
        out[f"noodle {path} --help"] = _help_text(path.split())
    return out


def _step_surfaces() -> dict[str, str]:
    """The runtime discovery commands — the exact surface that taught the
    audited agent the marker, at the moment it was blocked."""
    out = {}
    for term in ("screenshot", "evidence", "capture"):
        out[f"noodle steps {term}"] = CliRunner().invoke(
            cli.app, ["steps", term]).output
        out[f"noodle step-search {term}"] = CliRunner().invoke(
            cli.app, ["step-search", term]).output
    out["noodle steps"] = CliRunner().invoke(cli.app, ["steps"]).output
    return out


def _doc_surfaces() -> dict[str, str]:
    """Every document reachable through `noodle docs` / MCP read_docs. The
    docs corpus IS an emitted surface — that is precisely what NOOD_0227's
    "the engine never emits these any more" missed."""
    from noodle.docs_reader import _docs_dir
    d = _docs_dir()
    if d is None:                       # pragma: no cover — wheel install
        return {}
    return {f"noodle docs {p.stem}": p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.md"))
            if p.stem not in _HISTORICAL}


# F2's ONLY exemption, and it is asserted, not asserted-by-filename: a
# historical record may name what was removed. test_historical_docs_are_
# unreachable proves each one is neither served by `noodle docs` nor copied
# into an initialised workspace, which is what makes the exemption safe.
_HISTORICAL: set[str] = set()


def _instruction_surfaces() -> dict[str, str]:
    from noodle.mcp import server
    out = {
        "cli._AGENTS_MD": cli._AGENTS_MD,
        "cli._PROMPT_TEMPLATE": cli._PROMPT_TEMPLATE,
        "mcp._INSTRUCTIONS": server._INSTRUCTIONS,
    }
    for rel in (".claude/skills/noodle/SKILL.md",
                ".copilot/skills/noodle/SKILL.md",
                ".github/copilot-instructions.md",
                "AGENTS.md", "PROMPT_TEMPLATE.md"):
        f = REPO / rel
        if f.is_file():
            out[rel] = f.read_text(encoding="utf-8")
    return out


def _mcp_docstring_surfaces() -> dict[str, str]:
    from noodle.mcp import server
    out = {}
    for name in dir(server):
        fn = getattr(server, name)
        if callable(fn) and getattr(fn, "__doc__", None) and not name.startswith("_"):
            out[f"mcp.{name}.__doc__"] = fn.__doc__
    return out


def _remediation_surfaces() -> dict[str, str]:
    out = {}
    for action_id in remediation.REMEDIATIONS:
        rec = remediation.build(action_id, **{
            k: f"<{k}>" for k in remediation.REMEDIATIONS[action_id][1]})
        out[f"remediation[{action_id}]"] = rec.render()
    return out


def _init_workspace_surfaces(tmp_path: Path) -> dict[str, str]:
    """Every file `noodle init` writes into a fresh workspace. A doc that is
    purged from the repo but still scaffolded into a user's project is still
    teaching it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    CliRunner().invoke(cli.app, ["init", str(ws)])
    out = {}
    for f in sorted(ws.rglob("*")):
        if f.is_file() and f.suffix in (".md", ".feature", ".yaml", ".yml",
                                        ".env", ".txt", ""):
            try:
                out[f"init:{f.relative_to(ws).as_posix()}"] = f.read_text(
                    encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _packaged_resource_surfaces() -> dict[str, str]:
    """Docs that ship INSIDE the installed package. A repo-tree scan misses
    anything packaged separately, and the wheel is what a user actually runs."""
    import importlib.resources as ir
    out = {}
    try:
        root = ir.files("noodle")
    except (ModuleNotFoundError, TypeError):       # pragma: no cover
        return out
    for entry in root.iterdir():
        if entry.is_dir() and entry.name in ("docs", "templates"):
            for f in entry.iterdir():
                if f.name.endswith((".md", ".yaml", ".feature")):
                    out[f"pkg:{entry.name}/{f.name}"] = f.read_text(
                        encoding="utf-8")
    return out


def all_surfaces(tmp_path: Path) -> dict[str, str]:
    return {**_cli_help_surfaces(), **_step_surfaces(), **_doc_surfaces(),
            **_instruction_surfaces(), **_mcp_docstring_surfaces(),
            **_remediation_surfaces(), **_init_workspace_surfaces(tmp_path),
            **_packaged_resource_surfaces()}


# ── F2 ──────────────────────────────────────────────────────────────────────
def test_no_agent_visible_surface_teaches_the_evidence_marker(tmp_path):
    hits = []
    for name, text in all_surfaces(tmp_path).items():
        hits += _scan(name, text)
    assert not hits, (
        "SHIPPED AFFORDANCE CONTRACT violated (F2): an agent-visible surface "
        "shows an evidence request in step-text form. A surface that shows it "
        "teaches it — that is how NOOD_0153/0211/0215/0225/0227 each came "
        "back. Rewrite the surface to teach the brief, @evidence:steps= or "
        "NOODLE_EVIDENCE instead.\n  " + "\n  ".join(hits))


def test_the_docs_corpus_is_covered_by_the_scan():
    """Guards the guard: an empty doc surface would make F2 pass vacuously."""
    docs = _doc_surfaces()
    assert len(docs) > 10, docs.keys()
    assert any("steps_dictionary" in k for k in docs)
    assert any("agent-playbook" in k for k in docs)


def test_historical_docs_are_unreachable(tmp_path):
    """F2's exemption is earned, not declared. Any doc excluded from the scan
    must be unreachable through `noodle docs` AND absent from a scaffolded
    workspace — a filename-based blanket exception is how this returns."""
    served = {k.split()[-1] for k in _doc_surfaces()}
    scaffolded = set(_init_workspace_surfaces(tmp_path))
    for stem in _HISTORICAL:
        assert stem not in served, f"{stem} is still served by `noodle docs`"
        assert not any(stem in s for s in scaffolded), \
            f"{stem} is still copied into an initialised workspace"


# ── F3: behavioural rejection ───────────────────────────────────────────────
FEATURE = ('@web\nFeature: F\n\n  Scenario: S\n'
           '    Given the user navigates to "https://example.com"\n'
           '    Then the user sees "Hello"{marker}\n')


@pytest.mark.parametrize("spelling", MARKER_SPELLINGS)
def test_validate_rejects_every_marker_spelling(spelling):
    from noodle.repl import validate
    result = validate.check_feature(FEATURE.format(marker=" " + spelling))
    assert result["rejected"], f"{spelling!r} was accepted by check_feature"


@pytest.mark.parametrize("spelling", MARKER_SPELLINGS)
def test_the_rejection_message_is_not_copy_ready(spelling):
    """The single most important assertion in this file. NOOD_0227 failed
    precisely because the 'don't use this' surface was itself copy-ready: the
    agent was blocked, ran the engine's own discovery command, and pasted the
    example it printed. A refusal that reprints the forbidden spelling teaches
    it one more time."""
    why = patterns.evidence_marker_rejection(f'the user sees "x" {spelling}')
    assert why, spelling
    assert not MARKER_SCAN_RE.search(why), (
        f"the rejection for {spelling!r} contains a pasteable marker:\n{why}")
    # ...and it must route somewhere: a refusal with no destination is what
    # sent the audited session hunting through `noodle steps`.
    assert "brief" in why or "@evidence:steps" in why


@pytest.mark.parametrize("spelling", MARKER_SPELLINGS[:4])
def test_the_author_transaction_refuses_and_writes_nothing(tmp_path, spelling):
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    CliRunner().invoke(cli.app, ["init", str(ws)])
    dest = "noodle_tests/web/shop/features/f.feature"
    out = core.author_test(
        app_name="shop", base_url="https://example.com", feature_path=dest,
        feature_content=FEATURE.format(marker=" " + spelling),
        workspace=str(ws))
    assert out["ok"] is False, out
    assert not (ws / dest).exists(), "a refused transaction still wrote bytes"


def test_the_runner_refuses_a_marked_step_that_reached_it():
    """A file written by an older engine, or dropped into the tests tree by
    hand, must not run — otherwise 'the parser rejects it' holds everywhere
    except the one place that decides whether a test goes green."""
    from noodle.orchestrator import runner

    class _Ctx:
        page = None

    with pytest.raises(AssertionError) as e:
        runner.execute_step('the user sees "x" ( take a screenshot )', _Ctx())
    assert not MARKER_SCAN_RE.search(str(e.value))


def test_a_clean_step_still_resolves():
    """The rejection must not have been bought by breaking ordinary steps —
    including the explicit screenshot STEP, which is a real action."""
    from noodle.resolver import match_step
    assert match_step('the user sees "Hello"') is not None
    assert match_step("the user takes a screenshot") is not None


# ── F4: command-surface closure ─────────────────────────────────────────────
def test_every_remediation_names_a_registered_command():
    registered = remediation.registered_commands()
    dangling = {aid: cmd for aid, (cmd, _, _) in remediation.REMEDIATIONS.items()
                if cmd not in registered}
    assert not dangling, (
        "SHIPPED AFFORDANCE CONTRACT violated (F4): remediation(s) name a "
        f"command the CLI does not register: {dangling}. Every recommendation "
        "must be executable — a remediation naming nothing is what makes a "
        "file edit the only reading of it.")


def test_every_rendered_remediation_actually_parses():
    """A command that reads fine and will not run is the same defect class as
    a file-edit instruction: the reader follows it, it fails, and the shell
    looks like the way out. Caught for real — `pom resolve` takes its id
    POSITIONALLY and the first renderer emitted `--ambiguity-id`."""
    runner = CliRunner()
    bad = {}
    for action_id, (_, required, _) in remediation.REMEDIATIONS.items():
        rec = remediation.build(action_id, **{k: f"x{k}" for k in required})
        result = runner.invoke(cli.app, rec.argv())
        text = (result.output or "") + str(result.exception or "")
        # PARSER errors only. A remediation renders placeholder values, so a
        # semantic complaint ("that spec is missing app_name") is expected and
        # correct; an unknown flag or a stray positional is the defect.
        if re.search(r"No such option|Got unexpected extra argument|"
                     r"Missing argument", text):
            bad[action_id] = f"{rec.command_line()} → {text.strip()[:160]}"
    assert not bad, (
        "SHIPPED AFFORDANCE CONTRACT violated (F4): a remediation renders a "
        f"command line the CLI cannot parse: {bad}")


def test_no_remediation_instructs_a_file_edit():
    bad = {}
    for name, text in _remediation_surfaces().items():
        if (hit := remediation.filesystem_imperative(text)):
            bad[name] = hit
    assert not bad, (
        "SHIPPED AFFORDANCE CONTRACT violated (F4): a remediation instructs a "
        f"filesystem edit: {bad}. Name a noodle command instead — the audited "
        "session ran a heredoc because the engine's own ambiguity warning "
        "ended in a YAML snippet and a path.")


def test_no_rca_verdict_instructs_a_file_edit():
    """Workstream C's acceptance rule, mechanised: every diagnostic the engine
    emits that contains an imperative about a file must name a registered
    command instead. RCA verdicts are the highest-traffic diagnostic surface
    there is — an agent reads `rca_compact` on every red run — and two of them
    said "add a pom.yaml entry", which is the ambiguity warning's defect one
    layer down."""
    from noodle.reporting import rca_report

    # Drive classify() through the shapes its own rules match, so the fixtures
    # cannot drift out of step with the rule table.
    probes = [
        {"warnings": ["Ambiguous locator 'x' — matched multiple elements"]},
        {"message": "Could not find element 'x'"},
        {"message": "evidence requests are not accepted inside step text"},
        {"message": "Expected status 500, got 500"},
        {"message": "Timed out waiting for 'x'"},
        {"message": "The page shows 'undefined'"},
        {"message": "Comparison failed: expected 'a'"},
        {"message": "something nobody has a rule for"},
    ]
    bad = {}
    for probe in probes:
        entry = {"scenario": "S", "step": "Then x", "message": "",
                 "trace": "", "warnings": [], **probe}
        verdict = rca_report.classify(entry)
        text = f"{verdict['reason']} {verdict['fix']}"
        if (hit := remediation.filesystem_imperative(text)):
            bad[verdict["category"]] = f"{hit!r} in fix: {verdict['fix']}"
    assert not bad, (
        "SHIPPED AFFORDANCE CONTRACT violated (F4): an RCA verdict instructs a "
        f"filesystem edit rather than naming a command: {bad}")


def test_the_ambiguous_locator_warning_names_a_command_not_a_file():
    """The exact regression. This message is what sent an agent to a heredoc."""
    from noodle.agents.web import locator

    class _Loc:
        def element_handles(self):
            raise RuntimeError("no browser here")

    class _Page:
        url = "https://example.com/menu"

    monkey = locator._is_strict
    locator._is_strict = lambda: True
    try:
        with pytest.raises(AssertionError) as e:
            locator._on_ambiguous(_Page(), "add to order", _Loc())
    finally:
        locator._is_strict = monkey
    msg = str(e.value)
    assert "noodle pom resolve" in msg, msg
    assert remediation.filesystem_imperative(msg) is None, msg


def test_command_surface_closure_covers_the_audited_shell_reflexes():
    """Each `ls`/`cat` the audited session ran now has a command behind it.
    Named individually so a removal is a test failure, not a silent gap."""
    registered = remediation.registered_commands()
    for need in ("workspace inspect", "pom resolve", "pom set", "pom list",
                 "migrate evidence-markers"):
        assert need in registered, f"`noodle {need}` is gone — {need} regresses"


def test_the_skill_cards_no_longer_prescribe_writing_a_file():
    """The card told agents to write a spec file two screens below its own
    'Noodle commands only' block. Deleting the contradiction is worth more
    than any amount of added emphasis."""
    bad = re.compile(r"write a (?:spec )?file|cat >|<<\s*['\"]?EOF", re.I)
    for rel in (".claude/skills/noodle/SKILL.md",
                ".copilot/skills/noodle/SKILL.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not bad.search(text), f"{rel} still prescribes a file write"
        assert "--spec-text" in text, f"{rel} lost the shell-free spec input"


# ── F5: the guard is proved by breaking it ──────────────────────────────────
def test_contract_guard_detects_a_reinserted_marker(tmp_path):
    """A contract test nobody has seen fail is not known to work. Reinsert the
    marker into a copy of the doc the audit caught and assert the scanner
    fires — proving F2's grammar actually matches the historical form."""
    doc = (REPO / "docs" / "steps_dictionary.md").read_text(encoding="utf-8")
    assert not _scan("steps_dictionary.md", doc), "precondition: doc is clean"
    reinserted = doc.replace(
        "Gates — the engine never takes random screenshots:",
        "When User clicks the 'Login' button ( take a screenshot )\n\n"
        "Gates — the engine never takes random screenshots:", 1)
    hits = _scan("steps_dictionary.md", reinserted)
    assert hits, "the F2 scanner does NOT catch the historical marker form"


def test_the_migration_converts_a_marked_workspace(tmp_path):
    """The breaking change is only acceptable with a working upgrade path."""
    from noodle import evidence_migration
    ws = tmp_path / "ws"
    ws.mkdir()
    CliRunner().invoke(cli.app, ["init", str(ws)])
    feat = ws / "noodle_tests" / "web" / "legacy" / "features" / "l.feature"
    feat.parent.mkdir(parents=True)
    feat.write_text(
        '@web\nFeature: Legacy\n\n  Scenario: Old\n'
        '    Given the user navigates to "https://example.com"\n'
        '    When the user clicks the "Buy" button [take a screenshot]\n'
        '    Then the user sees "Done" ( no screenshot )\n', encoding="utf-8")

    dry = evidence_migration.migrate(str(ws))
    assert dry["features"] and not dry["written"]
    assert feat.read_text(encoding="utf-8").count("screenshot") == 2, \
        "a dry run must not rewrite anything"

    evidence_migration.migrate(str(ws), write=True)
    after = feat.read_text(encoding="utf-8")
    assert "@evidence:steps=2" in after and "@evidence:skip=3" in after
    assert not _scan("migrated", after)

    from noodle.repl import validate
    assert not validate.check_feature(after)["rejected"]


def test_brief_reaches_a_hand_authored_feature(tmp_path):
    """E's guarantee, restated as a contract: correctness must be reachable,
    or the prohibition has no legal alternative and the marker comes back."""
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    CliRunner().invoke(cli.app, ["init", str(ws)])
    out = core.author_test(
        app_name="shop", base_url="https://example.com",
        feature_path="noodle_tests/web/shop/features/f.feature",
        feature_content=(
            '@web\nFeature: F\n\n  Scenario: S\n'
            '    Given the user navigates to "{env:SHOP}"\n'
            '    Then the user sees "Pizza"\n'),
        brief="1. open the menu\n"
              "2. Verify the pizza banner is present - take a screenshot "
              "for evidence",
        workspace=str(ws))
    assert out["ok"], out
    written = (ws / "noodle_tests/web/shop/features/f.feature").read_text(
        encoding="utf-8")
    assert "@evidence:steps=2" in written, written
    assert not _scan("authored", written)


def test_a_brief_asking_for_unplaceable_evidence_blocks(tmp_path):
    """Warn-and-block. Shipping it silently is how 'it went green' stopped
    meaning 'it proved what was asked'."""
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    CliRunner().invoke(cli.app, ["init", str(ws)])
    out = core.author_test(
        app_name="shop", base_url="https://example.com",
        feature_path="noodle_tests/web/shop/features/f.feature",
        feature_content=(
            '@web\nFeature: F\n\n  Scenario: S\n'
            '    Given the user navigates to "{env:SHOP}"\n'
            '    Then the user sees "Pizza"\n'),
        brief="1. open the menu\n2. click around\n"
              "3. Verify the quarterly revenue chart renders - screenshot it",
        workspace=str(ws))
    assert out["ok"] is False
    assert "unplaced_evidence" in out or "evidence" in out["error"]


# ── the contract must be in CI ──────────────────────────────────────────────
def test_this_contract_runs_in_ci():
    """F is worthless if phase 6 can quietly drop it. The workflow must invoke
    the unit suite that contains this file."""
    wf = REPO / ".github" / "workflows"
    if not wf.is_dir():                       # pragma: no cover
        pytest.skip("no GitHub workflows in this checkout")
    texts = [p.read_text(encoding="utf-8") for p in wf.glob("*.yml")]
    texts += [p.read_text(encoding="utf-8") for p in wf.glob("*.yaml")]
    assert any("unit_tests" in t or "pytest" in t for t in texts), \
        "no CI workflow runs the unit suite — the contract is not enforced"


def test_the_contract_module_imports_standalone():
    """A contract that only passes inside a warm pytest session is not a gate;
    the module-global leak in NOOD_0203 is exactly this failure mode."""
    rc = subprocess.run(
        [sys.executable, "-c",
         "import unit_tests.test_nood_0228_affordance_contract as m; "
         "assert m.MARKER_SCAN_RE.search('( take a screenshot )')"],
        cwd=REPO, capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr

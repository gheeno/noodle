"""NOOD_0197 — the retail-search session review, fixed at the engine.

The session review (workspace 195) found a green run that was weaker than the
ask, reached in 4 noodle invocations instead of 1. Root causes pinned here:

  F1  {any_of: [A, B]} compiled to two conjunctive `sees` steps — a logic
      inversion; the red run failed on B while the page correctly showed A
  F2  no disjunctive assertion existed in the step dictionary at all
  F6  RCA classified the assertion failure `mutation-failed`, citing a
      background price-availability POST the scenario never made
  §4  the composite 'METHOD url — failure' lines lost their failure tail to
      _safe_url when the URL carried a query string

Phase-1 pins (W1–W3): one disjunctive step end to end (compiler → pattern
table → runtime), the compiler never narrows a multi-term check, RCA never
speaks mutation-failed on a read-only scenario and cites the goal node that
produced the failing step.
"""
import json

from noodle import hooks
from noodle.agents.web import actions
from noodle.repl import goal as G
from noodle.reporting import rca_report

# --- the session's goal, verbatim shape --------------------------------------

_GOAL = {
    "scenario": "Hot Wheels search shows relevant results",
    "dismissals": ["location_prompt", "popups"],
    "actions": [{"do": "search", "term": "Hot Wheels", "id": "s"}],
    "checks": [{"see": "Weekly Flyer", "after": "start"},
               {"count": "results", "min": 1, "after": "s"},
               {"any_of": ["Hot Wheels", "Die Cast"], "after": "s"}],
}

_EV = {"proven": {}, "controls": {}, "bound_targets": {},
       "resolved_controls": {}, "permission_prompts": ["geolocation"],
       "popups_closed": 1, "headings": [],
       "results_summary": {"selector": "#count"}}


def _compiled_steps(goal=None, ev=None):
    feature, pom = G.compile_goal(goal or _GOAL, ev or _EV, "APP")
    return [ln.strip() for ln in feature.splitlines()
            if ln.strip().split(" ", 1)[0] in
            ("Given", "When", "Then", "And")], pom


# --- W1: the disjunction survives compilation --------------------------------

def test_session_goal_compiles_the_disjunction_whole():
    steps, _ = _compiled_steps()
    dis = [s for s in steps if "sees any of" in s]
    assert dis == ['And the user sees any of "Hot Wheels", "Die Cast"'], steps
    # Never the conjunctive expansion that caused the red run:
    assert 'the user sees "Die Cast"' not in " / ".join(steps)


def test_every_check_node_compiles_to_exactly_one_step():
    # W2's inverse guard: a check node emitting ≠ 1 assertion step is a
    # compiler bug. 3 checks → exactly 3 Then/And assertion steps.
    steps, _ = _compiled_steps()
    asserts = [s for s in steps
               if "sees" in s or "number in" in s or "should" in s]
    assert len(asserts) == len(_GOAL["checks"]), steps


def test_dictionary_examples_for_the_disjunction_resolve():
    from noodle.repl import validate
    feat = ("Feature: f\n  Scenario: s\n"
            '    Then the user sees any of "Hot Wheels", "Die Cast"\n'
            '    And the user sees either "Order placed" or "Order confirmed"\n'
            '    And the user sees one of "Saved", "Updated", "Done"\n'
            '    And the user sees at least 2 of "A", "B", "C"\n')
    chk = validate.check_feature(feat)
    assert chk["error"] is None
    assert validate.unmatched(chk) == [], chk


def test_search_step_finds_the_disjunctive_assertion():
    # F2 — `noodle step-search "any of"` returned zero hits across 427 steps.
    from noodle.resolver.step_search_engine import search_step
    for q in ("sees any of", "either", "one of"):
        r = search_step(q)
        assert any(s.type == "assert_any_visible" for s in r.shortlist), \
            (q, [s.step for s in r.shortlist])


# --- W1: the runtime action is a real disjunction ----------------------------

class _Page:
    url = "https://x/results"

    def wait_for_timeout(self, _ms):
        pass


def _patch_visibility(monkeypatch, visible):
    monkeypatch.setattr(actions, "_find_probe_visible",
                        lambda page, text, poll=False: text in visible)
    monkeypatch.setattr(actions, "_find_timeout_ms", lambda: 0)


def test_one_visible_alternative_satisfies_the_disjunction(monkeypatch):
    _patch_visibility(monkeypatch, {"Hot Wheels"})
    actions.assert_any_visible(_Page(), ["Hot Wheels", "Die Cast"])


def test_no_visible_alternative_fails_and_names_them_all(monkeypatch):
    _patch_visibility(monkeypatch, set())
    try:
        actions.assert_any_visible(_Page(), ["Hot Wheels", "Die Cast"])
    except AssertionError as e:
        assert "Hot Wheels" in str(e) and "Die Cast" in str(e)
    else:
        raise AssertionError("expected the disjunction to fail")


def test_min_count_requires_that_many_distinct_alternatives(monkeypatch):
    _patch_visibility(monkeypatch, {"A"})
    try:
        actions.assert_any_visible(_Page(), ["A", "B", "C"], min_count=2)
    except AssertionError as e:
        assert "at least 2" in str(e)
    else:
        raise AssertionError("min_count=2 must not pass on one match")
    _patch_visibility(monkeypatch, {"A", "C"})
    actions.assert_any_visible(_Page(), ["A", "B", "C"], min_count=2)


def test_the_satisfying_member_is_logged(monkeypatch, capsys):
    # W1 — the green must record WHICH disjunct satisfied it (the step log is
    # what Allure attaches to the step).
    _patch_visibility(monkeypatch, {"Die Cast"})
    actions.assert_any_visible(_Page(), ["Hot Wheels", "Die Cast"])
    out = capsys.readouterr().out
    assert "any-of satisfied by" in out and "Die Cast" in out, out


# --- W3: RCA never blames a mutation a read-only scenario never made ---------

def _result_json(d, steps, name):
    (d / "r-result.json").write_text(json.dumps(
        {"name": name, "status": "failed", "historyId": name, "stop": 1,
         "labels": [], "steps": steps}), encoding="utf-8")


def _failed_see(text):
    return {"name": f"Then the user sees '{text}'", "status": "failed",
            "statusDetails": {
                "message": f"Expected to see '{text}' on page — not found.",
                "trace": ""}}


def test_read_only_scenario_never_classifies_mutation_failed(tmp_path):
    # The session's exact shape: navigate → dismiss → search → assert, with a
    # background price-availability POST aborted by navigation in the capture.
    results = tmp_path / "allure-results"
    results.mkdir()
    _result_json(results, [
        {"name": 'Given User is on "https://www.example-retail.com"',
         "status": "passed"},
        {"name": "When the user closes the location prompt",
         "status": "passed"},
        {"name": 'When User searches for "Hot Wheels"', "status": "passed"},
        _failed_see("Die Cast")], "hot wheels search")
    net = tmp_path / "network"
    net.mkdir()
    (net / "hot_wheels_search.json").write_text(json.dumps({
        "requests": ["https://www.example-retail.com/"],
        "failed_requests": [
            "POST https://api.example-retail.com/v1/product/api/v2/product/"
            "sku/PriceAvailability?… — net::ERR_ABORTED"],
        "mutations": [], "failed_responses": []}), encoding="utf-8")
    entries = rca_report.collect(str(results))
    assert entries[0]["heuristic"]["category"] != "mutation-failed", \
        entries[0]["heuristic"]


def test_scenario_with_a_mutation_step_still_gets_the_verdict(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    _result_json(results, [
        {"name": "When User clicks 'Add to Cart'", "status": "passed"},
        _failed_see("Added to cart")], "buy")
    net = tmp_path / "network"
    net.mkdir()
    (net / "buy.json").write_text(json.dumps({
        "requests": ["https://shop.example.ca/"],
        "failed_requests": [
            "POST https://shop.example.ca/api/cart/add — net::ERR_ABORTED"],
        "mutations": [], "failed_responses": []}), encoding="utf-8")
    entries = rca_report.collect(str(results))
    assert entries[0]["heuristic"]["category"] == "mutation-failed"


# --- W3: RCA cites the goal node that produced the failing step --------------

def test_rca_names_the_goal_node_and_suspects_the_compilation(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    _result_json(results, [_failed_see("Die Cast")],
                 "Hot Wheels search shows relevant results")
    (tmp_path / "intent_trace.json").write_text(json.dumps({
        "Hot Wheels search shows relevant results": {
            "feature": "noodle_tests/web/ct/features/hw.feature",
            "trace": G.intent_trace(_GOAL, _EV)}}), encoding="utf-8")
    entries = rca_report.collect(str(results))
    hint = entries[0].get("goal_node_hint") or ""
    assert "checks[2]" in hint, entries[0]
    assert "multi-term" in hint
    assert "goal:" in rca_report.render_compact(str(results))


def test_trace_carries_the_check_terms():
    trace = G.intent_trace(_GOAL, _EV)
    node = next(t for t in trace if t["node"] == "checks[2]")
    assert node["terms"] == ["Hot Wheels", "Die Cast"]


# --- §4: the composite failed-request line keeps its failure tail ------------

def test_safe_failed_request_redacts_only_the_url():
    line = ("POST https://api.x.com/PriceAvailability?sku=1&pin=9 "
            "— net::ERR_ABORTED")
    out = hooks._safe_failed_request(line)
    assert out == "POST https://api.x.com/PriceAvailability?… — net::ERR_ABORTED"
    # …and the parser mutation_verdict uses still reads it.
    import re
    assert re.match(r"^(\S+)\s+(\S+)\s+—\s+(.*)$", out)


def test_safe_failed_request_falls_back_to_safe_url():
    assert hooks._safe_failed_request("https://x/y?token=1") == "https://x/y?…"


# --- W4/W6: the grammar conformance corpus -----------------------------------
# Seeded with the session's prompt VERBATIM — the three clauses the parser
# rejected (`NEEDS_INTERPRETATION`, zero browser evidence) plus variants.

import pytest  # noqa: E402

from noodle.repl import prompt_expander as pe  # noqa: E402

_SESSION_PROMPT = """1. Go to https://www.example-retail.com
2. If the location prompt or use location prompt appears, close it
3. Close any popups
4. Verify the Weekly Flyer is shown
5. Use the search bar to search for Hot Wheels
6. On the results page, verify at least 1 result is found with title Hot Wheels or Die Cast"""


def test_the_session_prompt_expands_one_shot():
    exp = pe.expand(_SESSION_PROMPT)
    assert exp["ok"], exp.get("unresolved")
    g = exp["goal"]
    assert g["navigation"] == ["https://www.example-retail.com"]
    assert set(g["dismissals"]) == {"location_prompt", "popups"}
    assert [a["do"] for a in g["actions"]] == ["search"]
    assert g["actions"][0]["term"] == "Hot Wheels"
    assert {"any_of": ["Hot Wheels", "Die Cast"], "min": 1} in g["checks"]
    assert any(c.get("see") == "Weekly Flyer" for c in g["checks"]), \
        g["checks"]


@pytest.mark.parametrize("clause,expect", [
    # conditional dismissals (+ the "use location" synonym)
    ("If the location prompt appears, close it", "location_prompt"),
    ("When the cookie banner shows up, dismiss it", "popups"),
    ("if the consent modal is shown close it", "popups"),
    ("If the notifications prompt pops up, dismiss it",
     "notifications_prompt"),
])
def test_conditional_dismissals_parse(clause, expect):
    exp = pe.expand(f"1. go to https://x.com\n2. {clause}\n3. verify Done")
    assert exp["ok"], exp.get("unresolved")
    assert expect in exp["goal"]["dismissals"]


@pytest.mark.parametrize("clause", [
    "Use the search bar to search for Hot Wheels",
    "Using the search box, search for Hot Wheels",
    "The user uses the search field to search for Hot Wheels",
])
def test_instrument_preambles_parse(clause):
    exp = pe.expand(f"1. go to https://x.com\n2. {clause}\n3. verify Done")
    assert exp["ok"], exp.get("unresolved")
    assert exp["goal"]["actions"][0] == {
        "do": "search", "id": "search1", "term": "Hot Wheels"}


@pytest.mark.parametrize("clause", [
    "As a user, I would like to search for Hot Wheels",
    "I want to search for Hot Wheels",
])
def test_narrative_preambles_parse(clause):
    exp = pe.expand(f"1. go to https://x.com\n2. {clause}\n3. verify Done")
    assert exp["ok"], exp.get("unresolved")
    assert exp["goal"]["actions"][0]["term"] == "Hot Wheels"


@pytest.mark.parametrize("clause,check", [
    ("verify at least 1 result is found with title Hot Wheels or Die Cast",
     {"any_of": ["Hot Wheels", "Die Cast"], "min": 1}),
    ("verify at least 2 results with title 'A', 'B' or 'C'",
     {"any_of": ["A", "B", "C"], "min": 2}),
    ("verify at least 5 results", {"count": "results", "min": 5}),
    ("verify Order placed or Order confirmed",
     {"any_of": ["Order placed", "Order confirmed"]}),
    # a fully quoted literal containing ' or ' is ONE text, not a disjunction
    ('verify "Save or discard"', {"see": "Save or discard"}),
])
def test_result_and_disjunction_asserts(clause, check):
    exp = pe.expand(
        f"1. go to https://x.com\n2. search for toys\n3. {clause}")
    assert exp["ok"], exp.get("unresolved")
    assert check in [{k: v for k, v in c.items() if k != "after"}
                     for c in exp["goal"]["checks"]], exp["goal"]["checks"]


# --- W5: partial parse instead of a hard stop --------------------------------

def test_partial_parse_survives_an_unknown_clause():
    exp = pe.expand("1. go to https://x.com\n"
                    "2. search for toys\n"
                    "3. frobnicate the widget cache\n"
                    "4. verify Done")
    assert not exp["ok"]
    partial = exp["goal_partial"]
    assert partial and [a["do"] for a in partial["actions"]] == ["search"]
    assert {"see": "Done"} in partial["checks"]
    assert partial["navigation"] == ["https://x.com"]
    # exactly one unresolved clause, and it names the offender
    assert len(exp["unresolved"]) == 1
    assert "frobnicate" in exp["unresolved"][0]["text"]


def test_unresolved_clauses_carry_a_concrete_rewrite():
    exp = pe.expand("1. go to https://x.com\n"
                    "2. make the search thing find toys somehow")
    assert not exp["ok"]
    assert any("rewrite as:" in (u.get("suggested") or "")
               for u in exp["unresolved"]), exp["unresolved"]


# --- W7/W8/W10: the shell-free CLI surface -----------------------------------

def test_spec_accepts_an_inline_document():
    # F4 — --spec was file-or-stdin only, so the moment --prompt missed, a
    # goal spec could only reach the CLI through a heredoc (a shell approval)
    # or a temp file. Inline kills both.
    from typer.testing import CliRunner

    from noodle.cli import app
    r = CliRunner().invoke(app, ["author", "--json", "--spec",
                                 "app_name: x"])
    # Parsed as a document (missing-fields error), never as a filename.
    assert r.exit_code == 2 and "missing required field" in r.output


def test_spec_typo_path_still_reads_as_a_missing_file():
    from typer.testing import CliRunner

    from noodle.cli import app
    r = CliRunner().invoke(app, ["author", "--spec", "specs/nope.yaml"])
    assert r.exit_code == 2 and "not found" in r.output


def test_vocabulary_flag_dumps_the_schema():
    # F8/W10 — the schema on demand; before this the only machine-readable
    # copy rode a rejection payload.
    from typer.testing import CliRunner

    from noodle.cli import app
    r = CliRunner().invoke(app, ["author", "--vocabulary"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["vocabulary"]["check_keys"]
    assert payload["example"]["scenario"]
    assert "search for <term>" in payload["prompt_grammar"]


def test_author_surface_parity_cli_mcp_core():
    """W8 — every author capability stays reachable from BOTH doors. A param
    added to the engine transaction but missing from the CLI spec door (or
    the MCP tool) recreates the heredoc session."""
    import inspect
    from pathlib import Path

    from noodle import cli
    from noodle.repl import core
    params = set(inspect.signature(core._author_test_impl).parameters)
    cli_src = inspect.getsource(cli.author)
    mcp_src = (Path(cli.__file__).parent / "mcp" / "server.py") \
        .read_text(encoding="utf-8")
    mcp_author = mcp_src[mcp_src.index("def author_test("):]
    mcp_author = mcp_author[:mcp_author.index("\n@_tool")]
    missing_cli = [p for p in params if p not in cli_src]
    missing_mcp = [p for p in params if p not in mcp_author]
    assert not missing_cli, f"CLI author cannot reach: {missing_cli}"
    assert not missing_mcp, f"MCP author_test cannot reach: {missing_mcp}"


def test_doctor_checks_browser_binaries():
    # W9 — "MCP declared, workspace green, first run dies fetching Chromium"
    # is a doctor-shaped failure. Read-only: never spawns or downloads.
    from noodle import doctor
    ids = [c.id for c in doctor.install_checks()]
    assert "install.browsers" in ids


def test_rejection_payload_ships_the_partial_goal(tmp_path):
    from noodle.repl import core
    out = core.author_test(prompt="1. go to https://x.com\n"
                                  "2. search for toys\n"
                                  "3. frobnicate the widget cache",
                           workspace=str(tmp_path))
    assert out["ok"] is False
    assert out["goal_partial"]["actions"][0]["term"] == "toys"
    assert "NOODLE_MODEL" in out["error"]


# --- W12/W13: path contract honesty + probe narration ------------------------

_FEATURE_MIN = ('@web\nFeature: F\n\n  Scenario: s\n'
                '    Given User is on "{env:SHOP}"\n'
                '    Then User should see "Dashboard"\n')


def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text(
        "tests_dir: noodle_tests\nenv_file: .env\n", encoding="utf-8")
    return ws


def test_relocated_feature_path_is_warned_and_recorded(tmp_path):
    # F7 — the wok layout kept only the basename and echoed only the final
    # path; the session guessed the corrected path for its overwrite retry.
    from noodle.repl import core
    r = core.author_test(
        app_name="Shop", base_url="http://localhost:9",
        feature_path="noodle_tests/shop/features/login.feature",
        feature_content=_FEATURE_MIN, workspace=str(_ws(tmp_path)))
    assert r["ok"]
    assert r["feature"] == "noodle_tests/web/shop/features/login.feature"
    assert r["feature_path_requested"] == \
        "noodle_tests/shop/features/login.feature"
    assert any("relocated" in w for w in r["warnings"]), r["warnings"]


def test_matching_feature_path_gets_no_relocation_warning(tmp_path):
    from noodle.repl import core
    r = core.author_test(
        app_name="Shop", base_url="http://localhost:9",
        feature_path="login", feature_content=_FEATURE_MIN,
        workspace=str(_ws(tmp_path)))
    assert r["ok"]
    assert not any("relocated" in w for w in r["warnings"]), r["warnings"]


def test_browserless_author_narrates_the_skipped_probe(tmp_path):
    # F9/W13 — the internal probe must be auditable from the payload; a
    # browserless goal says so explicitly instead of looking skipped.
    from noodle.repl import core
    core.init_workspace(str(tmp_path))
    r = core.author_test(
        prompt="1. GET https://api.example.com/orders\n"
               "2. verify the response status is 200",
        workspace=str(tmp_path))
    assert r["ok"], r.get("blocking") or r.get("error")
    assert r["probe_summary"] == "probe skipped (browserless api goal)"

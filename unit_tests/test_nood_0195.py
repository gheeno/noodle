"""NOOD_0195 — a goal marked `ready: true` must compile to something the
evidence checker can actually verify.

The 33-AIC session (target < 12) lost 6.78 AIC to one loop: an `any_of` check
over two probe-proven product titles compiled to

    Then should see at least 1 "result titles"

plus a synthesized regex POM key. That step resolves no single element, so
hooks.after_step saw no fresh match, drew no box, and marked the shot invalid
— `passed: 1, failed: 0, verified: false`, which the workspace rules correctly
refuse to call green. Rewriting the identical intent as literal `see:` checks
passed and verified on the first try, so the compiler had chosen an assertion
shape its own verifier rejects while the author reported ready.

Pinned here (re-pinned by NOOD_0197 — the per-member literal expansion this
suite originally asserted was a logic inversion: it compiled "A or B" into
"A and B", and a page correctly showing only A went red):
  A  a proven any_of compiles to ONE disjunctive `sees any of` step — never
     one conjunctive `see` per member, never a synthesized locator; the
     evidence marker rides that single step
  B  partial proof (a member seen only in part) compiles the same disjunctive
     step — the disjunction is satisfiable by the member that does render
  C  an unproven any_of still blocks — compilation never invents evidence
  D  assert_count registers its first counted element, so a genuine count
     check can still produce a valid evidence shot
  E  near-miss repair hints: an invented `do`, and a suggestion option one
     edit off the page's own (misspelled) typeahead
"""
from noodle.agents.web import actions, locator
from noodle.repl import goal as G

_BISSELL = "BISSELL PowerLifter FurFinder Cordless Stick Vacuum"
_HOOVER = "Hoover WindTunnel 2 Bagless Upright Vacuum"


def _probe(headings):
    return {"errors": [], "pages": [
        {"url": "https://x/", "title": "t", "controls": [], "pom_yaml": "",
         "permission_prompts": [], "popups_closed": 0, "headings": headings}]}


def _goal(members, evidence=None):
    check = {"any_of": list(members)}
    if evidence:
        check["evidence"] = evidence
    return {"scenario": "Search shows the expected products",
            "actions": [], "checks": [check]}


def _compile(goal, probe):
    ev = G.evidence(goal, probe)
    feature, pom = G.compile_goal(goal, ev, "APP")
    thens = [ln.strip().split(" ", 1)[1] for ln in feature.splitlines()
             if ln.strip().startswith(("Then ", "And "))]
    return ev, thens, pom


# --- A: a proven any_of compiles to ONE disjunctive step ----------------------

def test_proven_any_of_compiles_one_disjunctive_step():
    ev, thens, pom = _compile(_goal([_BISSELL, _HOOVER]),
                              _probe([_BISSELL, _HOOVER]))
    assert ev["blocking"] == []
    assert thens == [f'the user sees any of "{_BISSELL}", "{_HOOVER}"'], thens
    # No count assertion, and no synthesized regex locator to go with it.
    assert not any("at least" in t for t in thens)
    assert pom is None, pom


def test_evidence_marker_rides_the_disjunctive_step():
    """NOOD_0227 (D1) — the shot request stays attached to the ONE
    disjunctive step, as tag metadata naming its position, never as prose."""
    goal = _goal([_BISSELL, _HOOVER], evidence="screenshot")
    ev = G.evidence(goal, _probe([_BISSELL, _HOOVER]))
    feature, _ = G.compile_goal(goal, ev, "APP")
    thens = [ln.strip() for ln in feature.splitlines()
             if ln.strip().startswith(("Then ", "And "))]
    assert len(thens) == 1 and "take a screenshot" not in thens[0], thens
    import re as _re
    m = _re.search(r"@evidence:steps=(\d+)", feature)
    steps = [ln.strip() for ln in feature.splitlines()
             if ln.strip().startswith(("Given", "When", "Then", "And", "But"))]
    assert m and "sees any of" in steps[int(m.group(1)) - 1]


def test_min_above_one_compiles_at_least_n_of():
    goal = _goal([_BISSELL, _HOOVER])
    goal["checks"][0]["min"] = 2
    _, thens, pom = _compile(goal, _probe([_BISSELL, _HOOVER]))
    assert thens == [
        f'the user sees at least 2 of "{_BISSELL}", "{_HOOVER}"'], thens
    assert pom is None


# --- B: partial evidence compiles the same disjunctive step -------------------

def test_member_seen_only_in_part_still_compiles_the_disjunction():
    # The probe saw a prefix of the Hoover title. The disjunction stays whole:
    # at run time the member that does render satisfies it, and the run
    # payload records which one did.
    _, thens, pom = _compile(_goal([_BISSELL, _HOOVER]),
                             _probe([_BISSELL, "Hoover WindTunnel 2"]))
    assert thens == [f'the user sees any of "{_BISSELL}", "{_HOOVER}"'], thens
    assert pom is None


# --- C: unproven any_of still blocks ------------------------------------------

def test_unproven_any_of_blocks_and_never_narrows():
    ev, thens, _ = _compile(_goal([_BISSELL, _HOOVER]), _probe(["Groceries"]))
    assert any("any_of" in b for b in ev["blocking"]), ev["blocking"]
    # The compiled step stays the faithful disjunction — blocking is the
    # honest outcome, never a narrowed assertion that would go green.
    assert not any(t == f'the user sees "{_BISSELL}"'
                   or t == f'the user sees "{_HOOVER}"' for t in thens), thens


# --- D: a real count check still yields valid evidence ------------------------

class _FakeLocator:
    def __init__(self, n):
        self._n = n
        self.first = self

    def locator(self, _sel):
        return self

    def count(self):
        return self._n


class _FakePage:
    url = "https://x/results"

    def get_by_text(self, _text, exact=False):
        return _FakeLocator(3)


def test_count_assertion_registers_an_element_for_the_evidence_shot():
    locator.clear_last_match()
    before = locator.match_seq()
    actions.assert_count(_FakePage(), 1, "product tiles", ">=")
    assert locator.match_seq() == before + 1, "no fresh match to outline"
    assert locator.last_match()[0] == "product tiles"
    # The source must not name a fuzzy tier, or hooks invalidates the shot.
    from noodle import healing
    assert locator.last_match_source() not in healing.FUZZY_STRATEGIES


def test_failed_count_registers_nothing():
    locator.clear_last_match()
    before = locator.match_seq()
    try:
        actions.assert_count(_FakePage(), 9, "product tiles", ">=")
    except AssertionError:
        pass
    else:
        raise AssertionError("expected the count assertion to fail")
    assert locator.match_seq() == before


# --- E: near-miss repair hints ------------------------------------------------

# --- G: the surfaces the CI Windows cell reads ---------------------------------

def test_always_on_surfaces_are_read_as_utf8_not_the_ansi_codepage():
    """The Windows unit-matrix cell died at COLLECTION, before a single test
    ran: three modules read a doc with `read_text()` at import, and on Windows
    that decodes as the ANSI code page (cp1252), which has no mapping for the
    variation selector in `⚠️`. Any always-on surface is UTF-8 by nature — it
    carries arrows, box drawing and emoji — so the read must say so.

    Pinned as a rule, not three call sites: the next surface with a non-ASCII
    character would otherwise re-break the same gate.
    """
    import re
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    offenders = []
    for f in sorted(repo.glob("unit_tests/test_*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'(SKILL\.md|agent-playbook\.md|copilot-instructions'
                         r'\.md)"?\s*\)?\.read_text\(\)', line):
                offenders.append(f"{f.name}:{i}")
    assert not offenders, (
        "always-on surface read without encoding='utf-8' — fails on Windows: "
        + ", ".join(offenders))


def test_invented_verb_names_the_nearest_real_one():
    errs = G.validate({"scenario": "s", "checks": [],
                       "actions": [{"do": "pick_suggestion", "target": "x"}]})
    assert any("did you mean 'suggest'" in e for e in errs), errs


# --- F: the live re-run's own findings ----------------------------------------
# Verifying the fixes above against a live retail site surfaced three more, all
# the same defect class: `ready: true` resting on nothing.

def test_a_one_letter_control_cannot_prove_a_product_title():
    # Live: the landing page carries a control literally named "a", and bare
    # character containment made it satisfy BOTH 41-character product titles —
    # evidence() recorded proven any_of[0] == ['a'].
    probe = _probe([])
    probe["pages"][0]["controls"] = [
        {"name": "a", "kind": "link", "selector": "a", "visible": True,
         "needs_pom": False, "step": "x"}]
    ev = G.evidence(_goal([_BISSELL, _HOOVER]), probe)
    assert "any_of[0]" not in ev["proven"], ev["proven"]
    assert ev["blocking"], "a one-letter match must not reach ready:true"


def test_a_multi_word_fragment_is_still_evidence():
    # The floor must not kill the real case it exists for: a truncated caption.
    assert G._find_text(_HOOVER, [{"headings": ["Hoover WindTunnel 2"],
                                   "controls": []}]) == "Hoover WindTunnel 2"


def test_suggest_goals_follow_the_suggestion_when_probing():
    args = G.probe_args({"scenario": "s", "checks": [], "actions": [
        {"do": "suggest", "term": "Vaccu", "option": "Vaccum cleaner"}]})
    assert args["follow"] == "Vaccum cleaner", args
    # The user's literal, unaltered — _pick_suggestion matches a misspelled
    # site row fuzzily, so there is nothing to "correct" here.


def test_unanchored_check_takes_its_evidence_from_the_landed_page():
    # NOOD_0158 compiles an unanchored check AFTER every action; the evidence
    # pass matched it against the LANDING page, so a goal was checked against
    # one page and asserted against another.
    goal = {"scenario": "s",
            "actions": [{"do": "suggest", "term": "Vaccu",
                         "option": "vaccum cleaner", "id": "sg"}],
            "checks": [{"any_of": [_BISSELL, _HOOVER]}]}
    probe = _probe(["Free local shipping"])
    probe["pages"][0]["suggest"] = {"term": "Vaccu",
                                    "suggestions": ["vaccum cleaner"]}
    probe["pages"][0]["search"] = {"term": "vaccum cleaner", "controls": [],
                                   "headings": [_BISSELL, _HOOVER],
                                   "pom_yaml": ""}
    ev = G.evidence(goal, probe)
    assert ev["blocking"] == [], ev["blocking"]
    # ...and with real results-page evidence, the disjunction compiles whole.
    feature, pom = G.compile_goal(goal, ev, "APP")
    assert (f'the user sees any of "{_BISSELL}", "{_HOOVER}"' in feature), \
        feature
    assert "at least" not in feature and pom is None


def test_product_titles_are_provable_from_search_result_captions():
    # Live: both requested products were in the results page's structured
    # `result_items`, and neither was in headings or control names — so a
    # `see` on a genuinely rendered product hard-blocked. One caption carries
    # the whole title, the other is truncated by the probe's ~60-char cap and
    # is reachable only through _find_text's reverse direction.
    goal = {"scenario": "s",
            "actions": [{"do": "suggest", "term": "Vaccu",
                         "option": "vaccum cleaner", "id": "sg"}],
            "checks": [{"see": _BISSELL}, {"see": _HOOVER}]}
    probe = _probe([])
    probe["pages"][0]["suggest"] = {"term": "Vaccu",
                                    "suggestions": ["vaccum cleaner"]}
    probe["pages"][0]["search"] = {
        "term": "vaccum cleaner", "controls": [], "pom_yaml": "",
        "headings": ['Showing Result(s) for "vaccum cleaner"'],
        "result_items": [{"caption": _BISSELL[:59]},          # truncated
                         {"caption": _HOOVER + "\nTop Rated"}]}   # in full
    ev = G.evidence(goal, probe)
    assert ev["blocking"] == [], ev["blocking"]
    assert f"see:{_BISSELL}" in ev["proven"] and f"see:{_HOOVER}" in ev["proven"]


def test_expect_verdicts_prove_a_title_the_captures_truncate():
    # The live blocker after result_items landed: captions cap at ~60 chars, so
    # a 68-char product title could never be proven WHOLE from them.
    # --expect is an exact full-text search of the page the probe ended on.
    goal = {"scenario": "s",
            "actions": [{"do": "suggest", "term": "Vaccu",
                         "option": "vaccum cleaner", "id": "sg"}],
            "checks": [{"any_of": [_BISSELL, _HOOVER], "after": "sg"}]}
    # NOOD_0234 — the flow's own term rides along behind the checks' literals
    # (a results page renders its rows as plain text the structured capture
    # does not hold); the literals still lead, which is what this pins.
    assert G.probe_args(goal)["expect"] == [_BISSELL, _HOOVER, "Vaccu"]
    probe = _probe([])
    probe["pages"][0]["suggest"] = {"term": "Vaccu",
                                    "suggestions": ["vaccum cleaner"]}
    probe["pages"][0]["search"] = {"term": "vaccum cleaner", "controls": [],
                                   "headings": [], "pom_yaml": "",
                                   "result_items": [{"caption": _BISSELL[:59]}]}
    probe["pages"][0]["expect"] = [{"text": _BISSELL, "found": True},
                                   {"text": _HOOVER, "found": True}]
    ev = G.evidence(goal, probe)
    assert ev["blocking"] == []
    assert "any_of[0]" in ev["proven"], "expect FOUND is evidence"
    feature, pom = G.compile_goal(goal, ev, "APP")
    assert (f'the user sees any of "{_BISSELL}", "{_HOOVER}"' in feature), \
        feature
    assert "at least" not in feature and pom is None


def test_expect_never_answers_for_a_landing_page_check():
    # --expect verdicts describe the page the probe ENDED on. A check anchored
    # at `start` observes the landing page and must not borrow them.
    goal = {"scenario": "s",
            "actions": [{"do": "suggest", "term": "Vaccu",
                         "option": "vaccum cleaner", "id": "sg"}],
            "checks": [{"see": _HOOVER, "after": "start"}]}
    probe = _probe([])
    probe["pages"][0]["suggest"] = {"term": "Vaccu",
                                    "suggestions": ["vaccum cleaner"]}
    probe["pages"][0]["search"] = {"term": "vaccum cleaner", "controls": [],
                                   "headings": [], "pom_yaml": ""}
    probe["pages"][0]["expect"] = [{"text": _HOOVER, "found": True}]
    ev = G.evidence(goal, probe)
    assert any(_HOOVER in b for b in ev["blocking"]), ev["blocking"]


def test_suggestion_miss_names_the_closest_captured_spelling():
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "suggest", "term": "Vaccu",
                         "option": "Vacuum cleaner"}]}
    probe = _probe([])
    probe["pages"][0]["suggest"] = {
        "term": "Vaccu",
        "suggestions": ["vaccum cleaner", "vaccuum", "vaccums"]}
    ev = G.evidence(goal, probe)
    assert any("did you mean 'vaccum cleaner'" in b
               for b in ev["blocking"]), ev["blocking"]

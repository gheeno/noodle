"""NOOD_0220 — prove and compile must agree.

Measured on the NOOD_0218 benchmark (TC5, lap 1): authoring reported
`proven: {"see:BeanCounter ERP banner": "BeanCounter ERP"}` — the probe
matched a SHORTER rendering than the ask, `_find_text`'s reverse direction —
but compiled `Then the user sees "BeanCounter ERP banner"`. The runtime
asserts by SUBSTRING, so it looked for text the page does not render and the
run went red on a check the payload had just called proven.

The invariant these tests pin: every compiled `see` is a substring of the
probed text that proved it — otherwise the run cannot find what authoring
promised. Narrowing carries the existing NOOD_0199 `narrowed` provenance, so
it is never silent. `any_of` is deliberately exempt (see the test below).

The root cause is fixed one layer earlier too: the trailing noun in "verify X
banner is present" names the ELEMENT, and an unquoted one is now stripped at
expansion — the quoted form stays data, as always.
"""
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

PAGE = {"url": "http://app.example/", "controls": [],
        "headings": ["Widget Depot"], "next_pages": []}
# the page renders MORE than the ask — the forward direction, already correct
WORDY = {"url": "http://app.example/", "controls": [],
         "headings": ["Welcome to Widget Depot today"], "next_pages": []}


def _compiled_sees(goal, ev):
    feature, _ = goal_mod.compile_goal(goal, ev, "APP")
    return [ln.strip() for ln in feature.splitlines() if "sees" in ln]


# --- the invariant -----------------------------------------------------------

def test_see_proven_by_a_shorter_rendering_compiles_as_that_rendering():
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Widget Depot banner"}]}
    ev = goal_mod.evidence(goal, {"pages": [PAGE]})
    assert not ev["blocking"]
    # the check was narrowed in place, so prove and compile name one string
    assert goal["checks"][0]["see"] == "Widget Depot"
    assert ev["proven"] == {"see:Widget Depot": "Widget Depot"}
    assert _compiled_sees(goal, ev) == ['Then the user sees "Widget Depot"']


def test_the_narrowing_is_never_silent():
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Widget Depot banner"}]}
    ev = goal_mod.evidence(goal, {"pages": [PAGE]})
    assert ev["narrowed"] == [{"from": "Widget Depot banner",
                               "to": "Widget Depot",
                               "probed": "Widget Depot"}]


def test_evidence_marker_survives_the_narrowing():
    """The narrowing rewrites the check's text in place; the evidence
    directive must ride along. NOOD_0227 (D1): it lands as tag metadata
    pointing at the narrowed step, never as step-text prose."""
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Widget Depot banner",
                        "evidence": "screenshot"}]}
    ev = goal_mod.evidence(goal, {"pages": [PAGE]})
    assert goal["checks"][0]["evidence"] == "screenshot"
    feature, _ = goal_mod.compile_goal(goal, ev, "APP")
    assert "take a screenshot" not in feature
    import re as _re
    m = _re.search(r"@evidence:steps=(\d+)", feature)
    steps = [ln.strip() for ln in feature.splitlines()
             if ln.strip().startswith(("Given", "When", "Then", "And", "But"))]
    assert m and 'sees "Widget Depot"' in steps[int(m.group(1)) - 1]


def test_forward_direction_is_left_alone():
    """The page rendering MORE than the ask needs no narrowing: the runtime's
    substring match finds the shorter asserted text inside the longer line."""
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Widget Depot"}]}
    ev = goal_mod.evidence(goal, {"pages": [WORDY]})
    assert ev["narrowed"] == []
    assert goal["checks"][0]["see"] == "Widget Depot"


def test_shared_phrase_narrowing_is_not_double_applied():
    """NOOD_0199's path already narrows to a run the probed text contains, so
    the NOOD_0220 guard must find nothing left to do (one entry, not two)."""
    decorated = {"url": "http://app.example/", "controls": [],
                 "headings": ["Banner 2 of 8 — View the Weekly Flyer now"],
                 "next_pages": []}
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Weekly Flyer image"}]}
    ev = goal_mod.evidence(goal, {"pages": [decorated]})
    assert len(ev["narrowed"]) == 1
    assert ev["narrowed"][0]["to"] == "Weekly Flyer"


# --- any_of carries the same invariant ---------------------------------------

def test_any_of_is_deliberately_left_whole():
    """The `see` narrowing must NOT be extended to any_of. NOOD_0195 measured
    why: an any_of runs over result TITLES, whose probe capture truncates, so
    a short probed form usually means the capture was lossy — not that the
    page renders less. Narrowing there weakens a correct assertion, and the
    disjunction only needs one member to render at run time."""
    goal = {"scenario": "s", "actions": [],
            "checks": [{"any_of": ["Widget Depot banner", "Sprocket World"]}]}
    ev = goal_mod.evidence(goal, {"pages": [PAGE]})
    assert not ev["blocking"]
    assert goal["checks"][0]["any_of"] == ["Widget Depot banner",
                                           "Sprocket World"]
    assert ev["narrowed"] == []


# --- the root cause, one layer earlier: the trailing element noun ------------

def _checks(prompt):
    r = pe.expand(prompt)
    assert r["ok"], r.get("error")
    return r["goal"]["checks"]


def test_unquoted_trailing_noun_is_stripped_at_expansion():
    assert _checks("1. go to https://app.example\n"
                   "2. Verify Widget Depot banner is present") == [
        {"see": "Widget Depot"}]


def test_quoted_text_is_data_and_keeps_its_noun():
    """Quoted content is never rewritten — the engine's standing rule. The
    evidence pass still rescues it downstream (NOOD_0199 narrowing)."""
    assert _checks('1. go to https://app.example\n'
                   '2. verify "Cookie Banner" is present') == [
        {"see": "Cookie Banner"}]


def test_a_bare_noun_survives_because_stripping_leaves_nothing():
    assert _checks("1. go to https://app.example\n"
                   "2. verify banner is present") == [
        {"see": "banner"}]


def test_text_not_ending_in_an_element_noun_is_untouched():
    assert _checks("1. go to https://app.example\n"
                   "2. verify Order Placed Successfully") == [
        {"see": "Order Placed Successfully"}]


# --- structural guard: no compiled see may outrun its own evidence -----------

def test_every_proven_see_is_a_substring_of_what_proved_it():
    """The general form of the defect, over a spread of ask/render pairs."""
    cases = [
        ("Widget Depot banner", "Widget Depot"),          # reverse
        ("Widget Depot", "Welcome to Widget Depot today"),  # forward
        ("Weekly Flyer image", "Banner 2 of 8 — View the Weekly Flyer now"),
    ]
    for ask, rendered in cases:
        page = {"url": "http://app.example/", "controls": [],
                "headings": [rendered], "next_pages": []}
        goal = {"scenario": "s", "actions": [], "checks": [{"see": ask}]}
        ev = goal_mod.evidence(goal, {"pages": [page]})
        assert not ev["blocking"], (ask, ev["blocking"])
        asserted = goal["checks"][0]["see"]
        proof = ev["proven"][f"see:{asserted}"]
        assert goal_mod._norm(asserted) in goal_mod._norm(proof), (
            f"compiled {asserted!r} is not findable in the text that "
            f"proved it ({proof!r})")

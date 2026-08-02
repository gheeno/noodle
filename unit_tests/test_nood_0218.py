"""NOOD_0218 — the three lap-wasters a 16-lap checkout-flow session measured:

1. Route discovery: action targets living one same-origin link away (the
   /menu-style page behind a landing page) blocked authoring with "no probed
   control matches", and the fix was a hand-probed navigation entry costing
   ~10 agent laps. `next_pages` was already harvested; core._route_repair now
   light-probes up to 4 candidates and hands back repair.goal with the
   proven navigation.
2. repair_goal only ever DROPPED unprovable see-checks; when the blocking
   line carries a near-miss ("did you mean X?") the page provably renders
   the fact, so the repair now REWORDS to it instead (rewritten_checks).
3. "<label> should be <value>" compiled to one literal sentence no page
   renders; the prompt expander now splits it into label + value checks.
   And _rendered_near_miss only matched one direction, so a sentence-shaped
   expectation ("subtotal should be $18.99") surfaced nothing even when the
   page rendered 'Subtotal' and '$18.99' as separate lines.
"""
from noodle.agents.web.actions import _rendered_near_miss
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

# --- fixtures (synthetic, domain-agnostic) -----------------------------------

LANDING = {"url": "https://app.example/",
           "controls": [{"name": "home", "kind": "link"}],
           "headings": ["Welcome"],
           "next_pages": ["https://app.example/broken",
                          "https://app.example/status"]}
CAND_BROKEN = {"url": "https://app.example/broken", "http_status": 404,
               "controls": [{"name": "Order Status Lookup",
                             "kind": "button"}], "headings": []}
CAND_GOOD = {"url": "https://app.example/status",
             "controls": [{"name": "Order Status Lookup", "kind": "button"}],
             "headings": []}
CLICK_GOAL = {"scenario": "s",
              "actions": [{"do": "click", "target": "Order Status Lookup"}],
              "checks": []}


def _light_probe(pages):
    return lambda url, **kw: {"pages": pages, "errors": []}


# --- 1. route discovery ------------------------------------------------------

def test_unrouted_targets_filters_the_navigable_family():
    picked = goal_mod.unrouted_targets([
        'click "buy": no probed control matches that name',
        "add_to \"order\": no probed control mutates into 'order' (nothing "
        "names the destination beyond opening it)",
        'add_to within "Deluxe Widget": no probed text on this page '
        "identifies that item",
        "pick: the probe performed no search — nothing to bind",
    ])
    assert len(picked) == 3          # the pick failure is NOT navigable


def test_route_repair_finds_the_page_that_resolves_the_target(monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        _light_probe([CAND_BROKEN, CAND_GOOD]))
    ev = goal_mod.evidence(CLICK_GOAL, {"pages": [LANDING]})
    assert goal_mod.unrouted_targets(ev["blocking"])
    rep = core._route_repair(CLICK_GOAL, ev, {"pages": [LANDING]},
                             frozenset(), "https://app.example/")
    # the 404 candidate never competes; the winner is appended after base
    assert rep["navigation_added"] == "https://app.example/status"
    assert rep["goal"]["navigation"] == [
        "https://app.example/", "https://app.example/status"]
    assert rep["goal"]["actions"] == CLICK_GOAL["actions"]


def test_route_repair_declines_when_no_candidate_resolves(monkeypatch):
    monkeypatch.setattr(core, "probe_page", _light_probe(
        [{"url": "https://app.example/status",
          "controls": [{"name": "unrelated", "kind": "button"}],
          "headings": []}]))
    ev = goal_mod.evidence(CLICK_GOAL, {"pages": [LANDING]})
    assert core._route_repair(CLICK_GOAL, ev, {"pages": [LANDING]},
                              frozenset(), "https://app.example/") is None


def test_route_repair_declines_on_a_mixed_block(monkeypatch):
    # a navigation fix that cures only half the blockers would hand back a
    # goal that blocks again — decline, keep the original evidence
    called = []
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: called.append(1) or {"pages": []})
    ev = {"blocking": [
        'click "Order Status Lookup": no probed control matches that name',
        "navigation: the final action page returned HTTP 500"]}
    assert core._route_repair(CLICK_GOAL, ev, {"pages": [LANDING]},
                              frozenset(), "https://app.example/") is None
    assert not called                # ...and never spends the page loads


def test_auto_repair_navigation_warning_keeps_intent(monkeypatch):
    """A navigation repair drops nothing: intent_verified survives and the
    warning names the page the repair lap navigates to."""
    rep = {"goal": {**CLICK_GOAL,
                    "navigation": ["https://app.example/status"]},
           "navigation_added": "https://app.example/status"}
    monkeypatch.setattr(core, "_author_test_impl", lambda **kw: {
        "ready": True, "intent_verified": True, "warnings": []})
    out = core._auto_repair(
        {"ready": False, "repair": rep}, app_name="a",
        base_url="https://app.example/", feature_path="f.feature",
        kw={"run_after_author": True})
    assert out["ready"]
    assert out["intent_verified"] is True
    assert out["dropped_checks"] == []
    assert "navigation repaired" in out["warnings"][0]


# --- 2. repair-by-reword -----------------------------------------------------

NEAR_MISS_BLOCK = (
    'check "order is placed successfully": no probed heading or control '
    "shows that text on the page it is scoped to — fix the text to probed "
    'evidence — did you mean "Order Placed Successfully!"? (probed texts '
    'here: "Order Placed Successfully!")')
NO_MISS_BLOCK = ('check "ghost text": no probed heading or control shows '
                 "that text on the page it is scoped to")


def test_repair_goal_rewords_to_the_near_miss():
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "order is placed successfully",
                        "evidence": "screenshot"}]}
    rep = goal_mod.repair_goal(goal, [NEAR_MISS_BLOCK])
    assert rep["rewritten_checks"] == [
        {"from": "order is placed successfully",
         "to": "Order Placed Successfully!"}]
    assert rep["dropped_checks"] == []
    # the check survives with its evidence marker intact
    assert rep["goal"]["checks"] == [
        {"see": "Order Placed Successfully!", "evidence": "screenshot"}]


def test_repair_goal_still_drops_without_a_near_miss():
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "ghost text"}, {"see": "kept"}]}
    rep = goal_mod.repair_goal(goal, [NO_MISS_BLOCK])
    assert rep["dropped_checks"] == ["ghost text"]
    assert "rewritten_checks" not in rep
    assert rep["goal"]["checks"] == [{"see": "kept"}]


def test_repair_goal_reword_counts_as_a_survivor():
    # a LONE check with a near-miss is rewritten, not refused: the repair
    # leaves one (reworded) check, so the zero-checks guard does not fire
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "order is placed successfully"}]}
    rep = goal_mod.repair_goal(goal, [NEAR_MISS_BLOCK])
    assert rep["goal"]["checks"] == [{"see": "Order Placed Successfully!"}]


# --- 3. "<label> should be <value>" ------------------------------------------

def _checks(prompt):
    r = pe.expand(prompt)
    assert r["ok"], r.get("error")
    return r["goal"]["checks"]


def test_should_be_splits_label_and_value():
    checks = _checks("1. go to https://app.example.com\n"
                     "2. click place order\n"
                     "3. verify subtotal should be $18.99")
    assert {"see": "subtotal"} in checks
    assert any(c.get("see") == "$18.99" for c in checks)
    assert not any("should" in str(c.get("see", "")) for c in checks)


def test_should_be_presence_tail_asserts_the_subject():
    checks = _checks("1. go to https://app.example.com\n"
                     "2. verify the banner should be visible")
    assert checks == [{"see": "banner"}]


def test_should_be_url_subject_becomes_url_contains():
    checks = _checks("1. go to https://app.example.com\n"
                     "2. click checkout\n"
                     "3. verify the url should contain /confirm")
    assert {"url_contains": "/confirm"} in checks


def test_should_be_evidence_rides_the_value_check():
    checks = _checks("1. go to https://app.example.com\n"
                     "2. click place order\n"
                     "3. verify subtotal should be $18.99 - take an evidence "
                     "screenshot on this step")
    assert checks[-1].get("see") == "$18.99"
    assert checks[-1].get("evidence") == "screenshot"


# --- 3b. near-miss containment runs both ways --------------------------------

class _Page:
    def __init__(self, text):
        self._text = text

    def evaluate(self, js):
        return self._text


RENDERED = ("Order Placed Successfully!\nThank you\nItems Ordered\n"
            "Deluxe Widget\nSubtotal\n$18.99\nTotal")


def test_near_miss_reverse_containment_surfaces_the_parts():
    msg = _rendered_near_miss(_Page(RENDERED), "subtotal should be $18.99")
    assert "'Subtotal'" in msg and "'$18.99'" in msg


def test_near_miss_forward_containment_still_works():
    msg = _rendered_near_miss(_Page(RENDERED), "items ordered Deluxe Widget")
    assert "'Deluxe Widget'" in msg


def test_near_miss_stays_silent_on_a_genuine_absence():
    assert _rendered_near_miss(_Page(RENDERED), "no such thing xyz") == ""

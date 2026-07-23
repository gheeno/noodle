"""NOOD_0156 — false-positive mitigation: the retail-site regression.

A scenario reported 8 passing steps while its evidence showed "Cart (0)",
"$0.00" and an empty-cart message. Four safeguards failed at once; these
browser-free regressions pin the three engine gates that each make the false
pass impossible on its own:

  Gate 1 — literal assertions stay literal: assert_visible never resolves
           through the DOM-attribute scan (allow_dom_scan=False end to end),
           so "should see 'Added to cart'" can't pass on
           data-testid="header-cart". Explicit {pom:...} assertions keep
           their author-pinned selector.
  Gate 2 — low-confidence action healing fails: DOM-scan token coverage is
           near-total ('Add to cart' ≠ header-cart on one shared token;
           'server dev-panel' → id="dev-panel" survives).
  Gate 3 — passing is not automatically verified: run results carry
           verified / warnings / healing_events / evidence, and a green run
           whose steps leaned on fuzzy healing is verified: false.

Plus the authoring gates: automatic postcondition synthesis (a goal with
actions but no checks gains an explicit generated `Then`, or blocks),
zero-search-results blocking, and unscoped repeated-control blocking.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noodle import healing
from noodle.agents.web import actions, dom_scan, locator
from noodle.agents.web import probe as probe_mod
from noodle.repl import goal as goal_mod
from noodle.repl import validate as _validate
from noodle.reporting import rca_report, summary

# --- fixtures ----------------------------------------------------------------

def _cand(**kw):
    c = {"tag": "button", "id": "", "name": "", "testid": "", "aria": "",
         "title": "", "ph": "", "cls": "", "visible": True, "afford": False}
    c.update(kw)
    return c


class _Scope:
    """dom_scan scope double: .evaluate returns the candidate list."""
    def __init__(self, cands):
        self._cands = cands

    def evaluate(self, _js):
        return self._cands


class _ZeroLoc:
    """A locator that never matches (count 0) and is never visible."""
    def count(self):
        return 0

    def is_visible(self):
        return False

    @property
    def first(self):
        return self


class _OneLoc:
    def count(self):
        return 1

    def is_visible(self):
        return True

    @property
    def first(self):
        return self


class _Page:
    """find() page double: every accessibility strategy misses; a DOM-scan
    selector (when permitted) resolves to one element."""
    url = "https://x/"

    def __init__(self):
        self.mouse = MagicMock()

    def _miss(self, *a, **k):
        return _ZeroLoc()

    get_by_role = get_by_label = get_by_placeholder = _miss
    get_by_title = get_by_text = _miss

    def locator(self, sel):
        return _OneLoc()

    def evaluate(self, js):
        return "1:1"


def _fast_find_env(monkeypatch):
    """Make the poll loop terminate in one bounded pass, with the DOM re-scan
    eligible immediately."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "300")
    monkeypatch.setenv("NOODLE_SETTLE_TIMEOUT", "0")
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "1")
    monkeypatch.setattr(locator, "_DOM_SCAN_AFTER_S", -1.0)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator.pom, "raw_locator", lambda page, text: None)
    healing.reset()


# --- Gate 2: DOM-scan token coverage -----------------------------------------

def test_add_to_cart_never_scores_header_cart():
    """One shared token out of two meaningful ones is not a match: the exact
    substitution that opened the cart instead of adding a product."""
    scope = _Scope([_cand(testid="header-cart")])
    assert dom_scan.best_selector(scope, "Add to cart") is None


def test_added_to_cart_never_scores_header_cart():
    scope = _Scope([_cand(testid="header-cart")])
    assert dom_scan.best_selector(scope, "Added to cart") is None


def test_server_dev_panel_still_heals_to_dev_panel():
    """Three-token phrases may miss one token — the NOOD_0089 use case the
    tier exists for survives the tighter coverage rule."""
    scope = _Scope([_cand(tag="div", id="dev-panel", visible=False)])
    assert dom_scan.best_selector(scope, "server dev-panel") \
        == '[id="dev-panel"]'


def test_two_token_phrase_matching_both_tokens_still_scores():
    scope = _Scope([_cand(testid="add-to-cart-btn")])
    sel = dom_scan.best_selector(scope, "Add to cart")
    assert sel is not None and "add-to-cart-btn" in sel


def test_four_token_phrase_may_miss_at_most_one():
    cand = _cand(id="shipping-address-form")
    assert dom_scan._score({"edit", "shipping", "address", "form"}, cand) > 0
    assert dom_scan._score({"edit", "billing", "address", "form"}, cand) == 0


# --- Gate 1: literal assertions stay literal ---------------------------------

def test_find_action_path_still_heals_via_dom_scan(monkeypatch):
    """The control: with allow_dom_scan left on (action targets), the polled
    find() still resolves through the attribute scan — proving the assertion
    test below fails for the right reason (the switch, not the double)."""
    _fast_find_env(monkeypatch)
    monkeypatch.setattr(dom_scan, "best_selector",
                        lambda scope, text: '[data-testid="header-cart"]')
    loc = locator.find(_Page(), "Added to cart", poll=True, heal=False)
    assert loc is not None
    assert [e["strategy"] for e in healing.EVENTS] == ["dom-scan"]
    assert locator.last_match_source() == "dom-scan"


def test_find_allow_dom_scan_false_never_consults_the_scan(monkeypatch):
    _fast_find_env(monkeypatch)

    def _boom(scope, text):
        raise AssertionError("DOM scan consulted during a literal assertion")

    monkeypatch.setattr(dom_scan, "best_selector", _boom)
    loc = locator.find(_Page(), "Added to cart", poll=True, heal=False,
                       allow_dom_scan=False)
    assert loc is None
    assert healing.EVENTS == []


def test_find_heal_chain_dom_scan_tier_also_gated(monkeypatch):
    """heal=True + allow_dom_scan=False: the self-heal chain's own DOM-scan
    tier is skipped too, not only the in-poll re-scans."""
    _fast_find_env(monkeypatch)
    monkeypatch.setattr(dom_scan, "best_selector",
                        lambda scope, text: '[data-testid="header-cart"]')
    loc = locator.find(_Page(), "Added to cart", poll=False, heal=True,
                       allow_dom_scan=False)
    assert loc is None


def test_assert_visible_probe_disables_dom_scan(monkeypatch):
    """assert_visible resolves with allow_dom_scan=False on every pass, so a
    page whose only 'match' is an attribute-token overlap FAILS the
    assertion."""
    seen = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True,
                  allow_dom_scan=True, any_match=False):
        seen.append(allow_dom_scan)
        return _OneLoc() if allow_dom_scan else None

    monkeypatch.setattr(actions, "find", fake_find)
    page = MagicMock()
    page.get_by_text.return_value.count.return_value = 0
    page.url = "https://x/"
    with pytest.raises(AssertionError, match="Expected to see"):
        actions.assert_visible(page, "Added to cart")
    assert seen and all(v is False for v in seen)


def test_assert_visible_exact_text_still_passes(monkeypatch):
    monkeypatch.setattr(
        actions, "find",
        lambda page, text, **kw: _OneLoc() if kw.get("poll") is False else None)
    actions.assert_visible(MagicMock(), "Added to cart")   # no raise


def test_assert_visible_explicit_pom_assertion_still_passes(monkeypatch):
    """{pom:...} is an author-pinned selector — resolved before any scan and
    unaffected by allow_dom_scan=False."""
    _fast_find_env(monkeypatch)
    monkeypatch.setattr(locator.pom, "locate",
                        lambda page, key: _OneLoc() if key == "cart_badge" else None)
    loc = locator.find(_Page(), "{pom:cart_badge}", poll=True, heal=False,
                       allow_dom_scan=False)
    assert loc is not None
    assert locator.last_match_source() == "pom-explicit"


# --- Gate 3: passing is not automatically verified ---------------------------

def _result_json(tmp_path, steps, status="passed", name="scenario", hid="h1"):
    r = {"uuid": hid, "historyId": hid, "name": name, "fullName": name,
         "labels": [{"name": "feature", "value": "f"}], "status": status,
         "steps": steps, "start": 0, "stop": 1000}
    (tmp_path / f"{hid}-result.json").write_text(json.dumps(r))


def _step(name, status="passed", **details):
    s = {"name": name, "status": status, "start": 0, "stop": 1}
    if details:
        s["statusDetails"] = details
    return s


def test_clean_exact_run_is_verified(tmp_path):
    _result_json(tmp_path, [_step("When User clicks \"Add to cart\""),
                            _step("Then the user sees \"Added to cart\"")])
    s = summary.collect(str(tmp_path))
    assert s["passed"] == 1 and s["failed"] == 0
    assert s["verified"] is True
    assert s["unverified_reasons"] == []
    assert s["healing_events"] == []


def test_green_run_with_fuzzy_healing_is_not_verified(tmp_path):
    """The reproduced regression: passed: 1, two healed locators — the run
    payload must say verified: false and surface both healing events."""
    _result_json(tmp_path, [
        _step("When User clicks 'Add to cart'",
              healing=[{"locator": "Add to cart", "strategy": "dom-scan",
                        "detail": '[data-testid="header-cart"]'}]),
        _step("Then User should see 'Added to cart'",
              healing=[{"locator": "Added to cart", "strategy": "dom-scan",
                        "detail": '[data-testid="header-cart"]'}]),
    ])
    s = summary.collect(str(tmp_path))
    assert s["passed"] == 1 and s["failed"] == 0
    assert s["verified"] is False
    assert len(s["healing_events"]) == 2
    assert any("dom-scan" in r for r in s["unverified_reasons"])


def test_confident_healing_keeps_verified_true(tmp_path):
    """scroll / visible-filter / auth-synonym are exact-match tiers — reported
    in healing_events but not grounds for unverified."""
    _result_json(tmp_path, [
        _step("When User clicks 'Login'",
              healing=[{"locator": "Login", "strategy": "auth-synonym",
                        "detail": "matched on 'sign in'"},
                       {"locator": "Login", "strategy": "scroll",
                        "detail": ""}])])
    s = summary.collect(str(tmp_path))
    assert s["verified"] is True
    assert len(s["healing_events"]) == 2


def test_lenient_ambiguity_warning_is_not_verified(tmp_path):
    _result_json(tmp_path, [
        _step("When User clicks 'Add to cart'",
              warnings=["Ambiguous locator 'Add to cart' — matched multiple "
                        "elements (lenient mode — using the first match)"])])
    s = summary.collect(str(tmp_path))
    assert s["verified"] is False
    assert any("lenient" in r for r in s["unverified_reasons"])


def test_assert_probe_opts_into_any_match(monkeypatch):
    """assert_visible/assert_hidden's probe sets any_match=True: visible
    grid/list duplicates prove an existence check, so no ambiguity warning
    (and no forced POM-disambiguation lap) for a read-only assertion."""
    seen = {}

    def fake_find(page, text, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(actions, "find", fake_find)
    actions._find_probe_visible(MagicMock(), "Hoover WindTunnel 2")
    assert seen.get("any_match") is True


def test_evidence_element_out_of_view_is_not_verified(tmp_path):
    """A green run whose evidence shot can't show the asserted element (the
    center-scroll failed, element outside the captured viewport) must say
    verified: false — the image proves nothing about the step."""
    _result_json(tmp_path, [
        _step("Then User should see 'Hoover WindTunnel 2'",
              evidence={"path": "e.jpg", "valid": True,
                        "element_in_view": False})])
    s = summary.collect(str(tmp_path))
    assert s["verified"] is False
    assert any("outside the captured viewport" in r
               for r in s["unverified_reasons"])


def test_evidence_element_in_view_stays_verified(tmp_path):
    _result_json(tmp_path, [
        _step("Then User should see 'Hoover WindTunnel 2'",
              evidence={"path": "e.jpg", "valid": True,
                        "element_in_view": True})])
    s = summary.collect(str(tmp_path))
    assert s["verified"] is True


def test_invalid_evidence_is_not_verified(tmp_path):
    _result_json(tmp_path, [
        _step("Then the user sees 'toy' in the cart",
              evidence={"step": "x", "valid": False,
                        "fuzzy_healing": ["dom-scan"]})])
    s = summary.collect(str(tmp_path))
    assert s["verified"] is False
    assert s["evidence"] and s["evidence"][0]["valid"] is False


def test_failed_run_is_never_verified(tmp_path):
    _result_json(tmp_path, [_step("When x", status="failed",
                                  message="boom", trace="")],
                 status="failed")
    s = summary.collect(str(tmp_path))
    assert s["failed"] == 1 and s["verified"] is False


def test_compact_rca_includes_passed_step_healing(tmp_path):
    _result_json(tmp_path, [
        _step("When User clicks 'Add to cart'",
              healing=[{"locator": "Add to cart", "strategy": "dom-scan",
                        "detail": '[data-testid="header-cart"]'}])])
    out = rca_report.render_compact(str(tmp_path))
    assert "passed-with-healing" in out
    assert "dom-scan" in out and "Add to cart" in out


def test_compact_rca_all_green_unhealed_unchanged(tmp_path):
    _result_json(tmp_path, [_step("When x")])
    assert rca_report.render_compact(str(tmp_path)) \
        == "All green — no failures to explain."


def test_healing_events_since_snapshot():
    healing.reset()
    healing.record("a", "scroll")
    n = healing.event_count()
    healing.record("b", "dom-scan", "[id=x]")
    assert healing.events_since(n) == [
        {"locator": "b", "strategy": "dom-scan", "detail": "[id=x]"}]
    assert healing.events_since(99) == []
    healing.reset()


# --- goal authoring: probe-evidence fixtures (shape of test_nood_0139) -------

def _ctrl(name, selector, kind="button", **extra):
    c = {"name": name, "selector": selector, "kind": kind,
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _result(controls=None, revealed=None, search=None, **over):
    pg = {"url": "https://x/", "title": "t", "controls": controls or [],
          "headings": [], "pom_yaml": "", "permission_prompts": [],
          "popups_closed": 0}
    if revealed is not None:
        pg["revealed"] = revealed
    if search is not None:
        pg["search"] = search
    pg.update(over)
    return {"pages": [pg], "errors": []}


def _search_probe(count):
    return _result(search={"term": "toy", "controls": [], "headings": [],
                           "results_summary": {"text": f"{count} results",
                                               "selector": "[id=s]",
                                               "count": count}})


def _synthesize(goal, probe):
    ev = goal_mod.evidence(goal, probe)
    synth = goal_mod.infer_postcondition(goal, ev)
    return ev, synth


# --- zero results block authoring --------------------------------------------

def test_zero_search_results_block_authoring():
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "toy", "id": "s"}],
            "checks": [{"see": "whatever", "after": "s"}]}
    ev = goal_mod.evidence(goal, _search_probe(0))
    assert any("0 results" in b for b in ev["blocking"])


def test_positive_search_results_do_not_block():
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "toy", "id": "s"}],
            "checks": [{"count": "results summary", "min": 1, "after": "s"}]}
    ev = goal_mod.evidence(goal, _search_probe(24))
    assert ev["blocking"] == []


# --- unscoped repeated controls block ----------------------------------------

def test_repeated_add_to_cart_controls_block_unscoped_click():
    probe = _result(controls=[_ctrl("Add to cart", "button#card1-add"),
                              _ctrl("Add to cart", "button#card2-add")])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "Add to cart", "id": "a"}],
            "checks": [], "allow_no_assertion": True}
    ev = goal_mod.evidence(goal, probe)
    assert any("repeated control" in b for b in ev["blocking"])


def test_single_add_to_cart_control_resolves():
    probe = _result(controls=[_ctrl("Add to cart", "button#add")])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "Add to cart", "id": "a"}],
            "checks": [], "allow_no_assertion": True}
    ev = goal_mod.evidence(goal, probe)
    assert ev["blocking"] == []
    assert ev["proven"].get("click:Add to cart") == "Add to cart"


def test_same_control_snapshotted_twice_is_not_repeated():
    probe = _result(controls=[_ctrl("Add to cart", "button#add"),
                              _ctrl("Add to cart", "button#add")])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "Add to cart", "id": "a"}],
            "checks": [], "allow_no_assertion": True}
    ev = goal_mod.evidence(goal, probe)
    assert ev["blocking"] == []


# --- automatic postcondition synthesis ---------------------------------------

def test_search_without_checks_generates_results_count_assertion():
    goal = {"scenario": "find toys",
            "actions": [{"do": "search", "term": "toy"}], "checks": []}
    ev, synth = _synthesize(goal, _search_probe(24))
    assert synth["blocking"] == []
    assert synth["checks"] == [{"count": "results summary", "min": 1,
                                "after": synth["actions"][-1]["id"]}]
    assert synth["generated"] and synth["generated"][0]["reason"]
    # compiled: the generated Then is IN the feature, and every step matches
    goal2 = dict(goal, actions=synth["actions"], checks=synth["checks"])
    ev2 = goal_mod.evidence(goal2, _search_probe(24))
    feat, pom = goal_mod.compile_goal(goal2, ev2, "APP")
    assert "the number in 'results summary' should be at least 1" in feat
    assert "[id=s]" in (pom or "")
    chk = _validate.check_feature(feat)
    assert chk["error"] is None and _validate.unmatched(chk) == []


def test_search_without_checks_and_no_summary_blocks():
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "toy"}], "checks": []}
    probe = _result(search={"term": "toy", "controls": [], "headings": [],
                            "results_summary": None})
    ev, synth = _synthesize(goal, probe)
    assert synth["generated"] == []
    assert any("no positive results summary" in b for b in synth["blocking"])


def test_enter_without_checks_generates_field_value_assertion():
    probe = _result(controls=[_ctrl("delivery postal code", "input#zip",
                                    kind="field")])
    goal = {"scenario": "s",
            "actions": [{"do": "enter", "target": "delivery postal code",
                         "value": "K1A 0B1"}], "checks": []}
    ev, synth = _synthesize(goal, probe)
    assert synth["blocking"] == []
    assert synth["checks"] == [{"field": "delivery postal code",
                                "value": "K1A 0B1",
                                "after": synth["actions"][-1]["id"]}]
    goal2 = dict(goal, actions=synth["actions"], checks=synth["checks"])
    ev2 = goal_mod.evidence(goal2, probe)
    feat, _ = goal_mod.compile_goal(goal2, ev2, "APP")
    assert 'the "delivery postal code" field should contain "K1A 0B1"' in feat
    chk = _validate.check_feature(feat)
    assert chk["error"] is None and _validate.unmatched(chk) == []
    # runtime-proven, never claimed probe-proven
    assert 'the "delivery postal code" field should contain "K1A 0B1"' \
        in ev2["runtime_asserted"]


def test_select_without_checks_generates_field_value_assertion():
    probe = _result(controls=[_ctrl("store location", "select#store",
                                    kind="dropdown",
                                    options=["Nearest", "Downtown"])])
    goal = {"scenario": "s",
            "actions": [{"do": "select", "target": "store location",
                         "option": "Downtown"}], "checks": []}
    ev, synth = _synthesize(goal, probe)
    assert synth["blocking"] == []
    assert synth["checks"][0]["field"] == "store location"
    assert synth["checks"][0]["value"] == "Downtown"


def test_reveal_click_without_checks_asserts_probe_observed_heading():
    revealed = [{"controls": [_ctrl("save", "button#save")],
                 "headings": ["Delivery Preferences"], "pom_yaml": "",
                 "revealed_by": "open settings"}]
    probe = _result(controls=[_ctrl("open settings", "button#gear")],
                    revealed=revealed)
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "open settings"}],
            "checks": []}
    ev, synth = _synthesize(goal, probe)
    assert synth["blocking"] == []
    assert synth["checks"] == [{"see": "Delivery Preferences",
                                "after": synth["actions"][-1]["id"]}]


def test_state_changing_click_without_evidence_blocks_with_suggestions():
    """Save/submit-shaped click, nothing probe-observed to anchor to → the
    goal blocks; confirmation text is never invented."""
    probe = _result(controls=[_ctrl("save preferences", "button#save")])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "save preferences"}],
            "checks": []}
    ev, synth = _synthesize(goal, probe)
    assert synth["generated"] == []
    assert any("state-changing" in b and "allow_no_assertion" in b
               for b in synth["blocking"])


def test_suggest_without_checks_asserts_canonical_option():
    probe = _result(suggest={"term": "to",
                             "suggestions": ["Toys R Us", "toy story"]})
    goal = {"scenario": "s",
            "actions": [{"do": "suggest", "term": "to",
                         "option": "toys r us"}], "checks": []}
    ev, synth = _synthesize(goal, probe)
    assert synth["blocking"] == []
    assert synth["checks"] == [{"see": "Toys R Us",
                                "after": synth["actions"][-1]["id"]}]


def test_user_supplied_checks_are_never_replaced_or_broadened():
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "toy", "id": "s"}],
            "checks": [{"see": "My Exact Text", "after": "s"}]}
    ev, synth = _synthesize(goal, _search_probe(24))
    assert synth["checks"] == [{"see": "My Exact Text", "after": "s"}]
    assert synth["generated"] == [] and synth["blocking"] == []


def test_allow_no_assertion_is_explicit_and_preserved():
    probe = _result(controls=[_ctrl("save preferences", "button#save")])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "save preferences"}],
            "checks": [], "allow_no_assertion": True}
    assert goal_mod.validate(goal) == []
    ev, synth = _synthesize(goal, probe)
    assert synth["generated"] == [] and synth["blocking"] == []
    assert synth["checks"] == []


def test_allow_no_assertion_must_be_boolean():
    goal = {"scenario": "s", "actions": [], "checks": [],
            "allow_no_assertion": "yes"}
    assert any("allow_no_assertion" in e for e in goal_mod.validate(goal))


def test_field_check_validates_and_requires_value():
    ok = {"scenario": "s",
          "actions": [{"do": "enter", "target": "zip", "value": "x",
                       "id": "z"}],
          "checks": [{"field": "zip", "value": "x", "after": "z"}]}
    assert goal_mod.validate(ok) == []
    bad = {"scenario": "s", "actions": [],
           "checks": [{"field": "zip"}]}
    assert any("value" in e for e in goal_mod.validate(bad))


def test_actions_only_goal_gains_no_hidden_runtime_check():
    """Generated checks are explicit .feature steps — the synthesized count
    assertion appears as a Then step, not as any out-of-band behavior."""
    goal = {"scenario": "find toys",
            "actions": [{"do": "search", "term": "toy"}], "checks": []}
    ev, synth = _synthesize(goal, _search_probe(24))
    goal2 = dict(goal, actions=synth["actions"], checks=synth["checks"])
    feat, _ = goal_mod.compile_goal(
        goal2, goal_mod.evidence(goal2, _search_probe(24)), "APP")
    assert feat.count("Then") == 1


# --- intent fidelity: bound result pick + item-in-destination (review fix) ---
#
# The simple-intent session review: the agent invented "buy online", weakened
# "the toy is in the cart" to "Cart (1)", and modeled the screenshot as a
# standalone step. These pin the engine path that makes each drift structural
# nonsense: a pick BINDS one probe-observed caption, the destination check
# reuses that exact caption, extra actions need provenance or block, and the
# screenshot rides the verification step via the existing NOOD_0153 marker.

def _shop_probe(picked=True, pick_warning=None):
    search = {"term": "toy", "headings": [],
              "controls": [
                  _ctrl("Paw Patrol Toy Truck", "#tile1", kind="link"),
                  _ctrl("Lego Toy Set", "#tile2", kind="link"),
                  _ctrl("Add to cart", "#card1-add"),
                  _ctrl("Add to cart", "#card2-add")],
              "results_summary": {"text": "24 results", "selector": "[id=s]",
                                  "count": 24}}
    if pick_warning:
        search["pick_warning"] = pick_warning
    elif picked:
        search["picked"] = {
            "controls": [_ctrl("Add to cart", "#pdp-add")],
            "headings": ["Paw Patrol Toy Truck"], "pom_yaml": "",
            "picked_caption": "Paw Patrol Toy Truck",
            "picked_selector": "#tile1"}
    return _result(controls=[_ctrl("Cart", "#cart", kind="link")],
                   search=search)


def _shop_goal(**over):
    g = {"scenario": "buy a toy",
         "actions": [{"do": "search", "term": "toy", "id": "s"},
                     {"do": "pick", "id": "p"},
                     {"do": "click", "target": "Add to cart", "id": "a"}],
         "checks": [{"item_in_destination": "cart", "expected_from": "p",
                     "after": "a", "evidence": "screenshot"}]}
    g.update(over)
    return g


def test_generic_pick_binds_one_concrete_result_caption():
    ev = goal_mod.evidence(_shop_goal(), _shop_probe())
    assert ev["blocking"] == []
    assert ev["bound_targets"]["p"]["caption"] == "Paw Patrol Toy Truck"
    assert "probe" in ev["bound_targets"]["p"]["evidence"]


def test_bind_result_requires_unique_stable_caption():
    # repeated captions are never bound — the reviewed session's duplicate
    # add-to-cart twins must not become a "bound result"
    controls = [_ctrl("Toy Deal", "#a", kind="link"),
                _ctrl("Toy Deal", "#b", kind="link")]
    cand, why = goal_mod.bind_result(controls, "toy")
    assert cand is None and "unique" in why
    cand, why = goal_mod.bind_result(
        [_ctrl("Garden Hose", "#h", kind="link")], "toy")
    assert cand is None and "no probed search-result caption" in why


def test_binding_is_language_universal():
    """Unicode-aware normalization: non-Latin captions bind like ASCII ones
    — the engine tests any web app in any language, no domain or script
    assumptions."""
    controls = [_ctrl("Juguete de Montaña", "#t1", kind="link"),
                _ctrl("おもちゃのトラック", "#t2", kind="link")]
    cand, _ = goal_mod.bind_result(controls, "montaña")
    assert cand and cand["selector"] == "#t1"
    cand, _ = goal_mod.bind_result(controls, "おもちゃ")
    assert cand and cand["selector"] == "#t2"


def test_bound_caption_reused_in_destination_assertion():
    goal = _shop_goal()
    ev = goal_mod.evidence(goal, _shop_probe())
    feat, pom = goal_mod.compile_goal(goal, ev, "APP")
    assert 'the user sees "Paw Patrol Toy Truck"' in feat
    # identity, never a count: no cart-count assertion anywhere
    assert "should be at least" not in feat
    # the pick click and its POM use the SAME bound caption + probed selector
    assert 'User clicks "Paw Patrol Toy Truck"' in feat
    assert "#tile1" in pom
    # the landed page's add-to-cart wins over the results-page twins
    assert "#pdp-add" in pom and "#card1-add" not in pom


def test_count_check_cannot_claim_item_identity():
    goal = _shop_goal(checks=[{"count": "Cart", "expected_from": "p",
                               "after": "a"}])
    assert any("cannot claim item identity" in e
               for e in goal_mod.validate(goal))


def test_no_extra_action_compiles_without_provenance():
    """The compiled flow is exactly: requested actions + the provenance-backed
    observation click. Nothing like 'buy online' can appear — there is no
    code path that emits an unrequested action."""
    goal = _shop_goal()
    ev = goal_mod.evidence(goal, _shop_probe())
    feat, _ = goal_mod.compile_goal(goal, ev, "APP")
    whens = [ln.strip() for ln in feat.splitlines()
             if ln.strip().startswith(("When", "And"))]
    # the network-idle wait is synchronization for the observation click
    # (NOOD_0156 follow-up — the in-flight mutation POST must land before
    # navigating away), not an action; it is the ONLY non-requested When.
    assert whens == ['When User searches for "toy"',
                     'And User clicks "Paw Patrol Toy Truck"',
                     'And User clicks "Add to cart"',
                     'And User waits for the network to be idle',
                     'And User clicks "Cart"']
    summary = goal_mod.intent_summary(goal, ev)
    assert [p["required_by"] for p in summary["required_prerequisites"]] \
        == ["observation:item_in_destination"]


def test_unprobed_extra_click_blocks_instead_of_guessing():
    """The 'buy online' repair guess, replayed against the engine: a click on
    a control no probe evidence supports must block, never compile."""
    goal = _shop_goal()
    goal["actions"].insert(2, {"do": "click", "target": "buy online",
                               "id": "b"})
    ev = goal_mod.evidence(goal, _shop_probe())
    assert any('click "buy online"' in b for b in ev["blocking"])


def test_observation_navigation_only_when_destination_named():
    # destination named → exactly one observation click, on the probed control
    ev = goal_mod.evidence(_shop_goal(), _shop_probe())
    feat, _ = goal_mod.compile_goal(_shop_goal(), ev, "APP")
    assert feat.count('clicks "Cart"') == 1
    # no destination ('' = assert in the current view) → no navigation at all
    goal = _shop_goal(checks=[{"item_in_destination": "", "expected_from": "p",
                               "after": "a"}])
    ev = goal_mod.evidence(goal, _shop_probe())
    assert ev["blocking"] == []
    feat, _ = goal_mod.compile_goal(goal, ev, "APP")
    assert 'clicks "Cart"' not in feat


def test_unprobed_destination_blocks():
    goal = _shop_goal(checks=[{"item_in_destination": "wishlist",
                               "expected_from": "p", "after": "a"}])
    ev = goal_mod.evidence(goal, _shop_probe())
    assert any('item_in_destination "wishlist"' in b for b in ev["blocking"])


def test_screenshot_evidence_rides_the_verification_step():
    """The NOOD_0153 marker lands ON the verification Then — never a
    standalone screenshot business step — and still matches the dictionary."""
    goal = _shop_goal()
    ev = goal_mod.evidence(goal, _shop_probe())
    feat, _ = goal_mod.compile_goal(goal, ev, "APP")
    assert 'Then the user sees "Paw Patrol Toy Truck" ( take a screenshot )' \
        in feat
    assert "takes a screenshot" not in feat      # no separate step
    chk = _validate.check_feature(feat)
    assert chk["error"] is None and _validate.unmatched(chk) == []


def test_item_check_is_runtime_asserted_never_probe_proven():
    ev = goal_mod.evidence(_shop_goal(), _shop_probe())
    assert any("Paw Patrol Toy Truck" in s for s in ev["runtime_asserted"])
    assert not any(k.startswith("see:") for k in ev["proven"])


def test_pick_without_landed_evidence_blocks():
    ev = goal_mod.evidence(_shop_goal(), _shop_probe(picked=False))
    assert any("no picked-result evidence" in b for b in ev["blocking"])
    ev = goal_mod.evidence(_shop_goal(),
                           _shop_probe(pick_warning="no match bound"))
    assert any("pick: no match bound" in b for b in ev["blocking"])


def test_pick_requires_a_preceding_search():
    goal = {"scenario": "s", "actions": [{"do": "pick", "id": "p"}],
            "checks": [], "allow_no_assertion": True}
    assert any("after a search action" in e for e in goal_mod.validate(goal))


def test_probe_args_never_execute_post_pick_clicks():
    """The add-to-cart click after the pick is a runtime action — the probe
    must not perform it (it would mutate real state)."""
    args = goal_mod.probe_args(_shop_goal())
    assert args["pick"] == "*" and args["search"] == "toy"
    assert args["click"] is None


def test_pick_without_checks_generates_caption_postcondition():
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "toy", "id": "s"},
                        {"do": "pick", "id": "p"}], "checks": []}
    ev, synth = _synthesize(goal, _shop_probe())
    assert synth["blocking"] == []
    assert synth["checks"] == [{"see": "Paw Patrol Toy Truck", "after": "p"}]


def test_author_manual_content_is_never_intent_verified(tmp_path):
    """ready: true for hand-written Gherkin is syntax/static readiness only —
    the engine never received the intent, so it can't verify fidelity."""
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    r = core.author_test(
        app_name="Shop", base_url="http://localhost:9", feature_path="t",
        feature_content=('@web\nFeature: F\n\n  Scenario: s\n'
                         '    Given User is on "{env:SHOP}"\n'
                         '    Then User should see "Dashboard"\n'),
        workspace=str(ws))
    assert r["ok"] and r["ready"] is True
    assert r["source"] == "manual"
    assert r["intent_verified"] is False


def test_author_goal_with_full_provenance_is_intent_verified(tmp_path,
                                                             monkeypatch):
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    monkeypatch.setattr(core, "probe_page", lambda url, **kw: _shop_probe())
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="t", goal=_shop_goal(),
                         workspace=str(ws))
    assert r["ok"] and r["ready"] is True
    assert r["source"] == "goal" and r["intent_verified"] is True
    assert r["intent"]["bound_targets"]["p"]["caption"] \
        == "Paw Patrol Toy Truck"
    assert r["intent"]["requested_actions"][1]["do"] == "pick"
    assert r["evidence"]["bound_targets"]
    # blocked goal → intent_verified false
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _shop_probe(picked=False))
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="t2", goal=_shop_goal(),
                         workspace=str(ws))
    assert r["intent_verified"] is False and r["blocking"]


# --- intent contract v2 (NOOD_0156 context/intent-fidelity fix) --------------
#
# The retail-site session review: 891 observed results collapsed to zero
# product controls, the agent invented 'Choose options' from a screenshot,
# cart-count replaced item identity, one requested URL was dropped, and a
# blocked goal silently became a guessed manual run. These pin the engine
# path that makes each drift structural nonsense.


def _raw(tag="a", href="", text="", cls="", visible=True, **extra):
    c = {"tag": tag, "id": "", "role": "", "type": "", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": cls, "href": href, "text": text, "label": "",
         "visible": visible, "expanded": "", "shadow": ""}
    c.update(extra)
    return c


def _retail_raw():
    """A retail results DOM in document order: global chrome links, then
    repeated product cards sharing one class selector (the exact shape whose
    selector-dedup collapsed 891 results to zero product controls), each
    with a per-card action button."""
    return [
        _raw(href="/", text="Logo", cls="site-logo"),
        _raw(href="/cart.html", text="Cart", cls="header-cart"),
        _raw(tag="button", text="Feedback", cls="feedback-tab"),
        _raw(href="/p/dreamhouse.html", text="Barbie Dreamhouse",
             cls="product-tile__link"),
        _raw(tag="button", text="Add to cart", cls="product-tile__add"),
        _raw(href="/p/rc-truck.html", text="Monster RC Truck",
             cls="product-tile__link"),
        _raw(tag="button", text="Add to cart", cls="product-tile__add"),
        _raw(href="/p/puzzle.html", text="1000-Piece Puzzle",
             cls="product-tile__link"),
        _raw(tag="button", text="Add to cart", cls="product-tile__add"),
    ]


def test_result_items_survive_repeated_class_selectors():
    """Repeated card structure with shared class selectors yields one item
    per card — not one deduped control — with unique per-card selectors."""
    items = probe_mod.build_result_items(_retail_raw())
    caps = [it["caption"] for it in items]
    assert caps == ["Barbie Dreamhouse", "Monster RC Truck",
                    "1000-Piece Puzzle"]
    sels = [it["selector"] for it in items]
    assert len(set(sels)) == 3
    # global chrome and the feedback tab are NOT result items
    assert not any(c in ("Logo", "Cart", "Feedback") for c in caps)


def test_result_items_scope_repeated_card_actions():
    items = probe_mod.build_result_items(_retail_raw())
    acts = [it["actions"] for it in items]
    assert all(a and a[0]["name"] == "Add to cart" for a in acts)
    # repeated per-card action selectors stay addressable per instance
    assert len({a[0]["selector"] for a in acts}) == 3


def test_result_items_unique_href_beats_class_selector():
    items = probe_mod.build_result_items(_retail_raw())
    assert items[0]["selector"] == 'a[href="/p/dreamhouse.html"]'


def test_bind_result_items_needs_no_term_in_caption():
    """A branded doll/game/truck rarely repeats the generic query word —
    membership in the result region IS the provenance."""
    items = probe_mod.build_result_items(_retail_raw())
    cand, why = goal_mod.bind_result(
        [_ctrl("Cart", "#cart", kind="link")], "toy", items=items)
    assert why is None and cand["name"] == "Barbie Dreamhouse"
    assert cand["selector"] == 'a[href="/p/dreamhouse.html"]'


def test_bind_result_items_named_target_filters_by_caption():
    items = probe_mod.build_result_items(_retail_raw())
    cand, _ = goal_mod.bind_result([], "toy", target="puzzle", items=items)
    assert cand["name"] == "1000-Piece Puzzle"
    cand, why = goal_mod.bind_result([], "toy", target="lawnmower",
                                     items=items)
    assert cand is None and "no result item caption matches" in why


def test_singleton_result_still_binds_via_diff_fallback():
    raw = [_raw(href="/cart.html", text="Cart", cls="header-cart"),
           _raw(href="/p/only.html", text="The Only Toy", cls="one-off")]
    items = probe_mod.build_result_items(
        raw, prev_selectors={probe_mod._selector(raw[0])})
    assert [it["caption"] for it in items] == ["The Only Toy"]


# --- ordered navigation contract ---------------------------------------------

def _nav_goal(**over):
    g = {"scenario": "search and add",
         "navigation": ["https://app.example/warmup.html",
                        "https://app.example/en.html"],
         "dismissals": ["popups", "location_prompt"],
         "actions": [{"do": "search", "term": "toy", "id": "s"},
                     {"do": "pick", "id": "p", "from": "s",
                      "strategy": "first_actionable"},
                     {"do": "add_to", "id": "a", "item_from": "p",
                      "destination": "cart"}],
         "checks": [{"item_in_destination": "cart", "expected_from": "p",
                     "after": "a", "evidence": "screenshot"}]}
    g.update(over)
    return g


def _nav_probe(mutation=True, extra_landed=None, mutation_path=None,
               **shop_kw):
    """Two pages probed in order; the flow acts on the LAST. mutation=False
    strips the landed page's add control (the options-gated PDP shape);
    mutation_path injects a probe-recorded prerequisite reveal proof."""
    warm = {"url": "https://app.example/warmup.html", "title": "w",
            "controls": [], "headings": [], "pom_yaml": "",
            "permission_prompts": [], "popups_closed": 0}
    shop = _shop_probe(**shop_kw)["pages"][0]
    picked = (shop.get("search") or {}).get("picked")
    if picked is not None:
        if not mutation:
            picked["controls"] = [c for c in picked["controls"]
                                  if c["name"] != "Add to cart"]
        if extra_landed:
            picked["controls"] = picked["controls"] + list(extra_landed)
        if mutation_path:
            picked["mutation_path"] = mutation_path
    return {"pages": [warm, shop], "errors": []}


def test_goal_navigation_validates():
    assert goal_mod.validate(_nav_goal()) == []
    bad = _nav_goal(navigation=[42])
    assert any("navigation[0]" in e for e in goal_mod.validate(bad))
    bad = _nav_goal(navigation=[])
    assert any("navigation" in e for e in goal_mod.validate(bad))


def test_original_intent_compiles_ordered_dual_navigation():
    """Both requested URLs compile, in order, as {env:} references — no
    literal URL in the feature."""
    goal = _nav_goal()
    ev = goal_mod.evidence(goal, _nav_probe(mutation=True))
    assert ev["blocking"] == []
    nav_env = goal_mod.navigation_env(goal, "shop")
    keys = [k for k, _ in nav_env]
    assert keys == ["SHOP_WARMUP", "SHOP_EN"]
    feat, _ = goal_mod.compile_goal(goal, ev, "SHOP", nav_keys=keys)
    lines = [ln.strip() for ln in feat.splitlines()]
    given = [ln for ln in lines if "User is on" in ln]
    assert given == ['Given User is on "{env:SHOP_WARMUP}"',
                     'And User is on "{env:SHOP_EN}"']
    assert "app.example" not in feat


def test_dropped_navigation_url_blocks_before_authoring():
    goal = _nav_goal()
    probe = _nav_probe(mutation=True)
    probe["pages"] = probe["pages"][:1]        # second URL never loaded
    ev = goal_mod.evidence(goal, probe)
    assert any(b.startswith("navigation") for b in ev["blocking"])
    assert goal_mod.next_action(ev["blocking"]) == "fix_navigation_contract"
    trace = goal_mod.intent_trace(goal, ev)
    assert any(t["node"].startswith("navigation") and not t["ok"]
               for t in trace)


# --- semantic add_to lowering ------------------------------------------------

def test_add_to_compiles_probed_mutation_control_and_identity_check():
    """The full contract: pick binds a caption, add_to lowers to the probed
    landed-page control, the cart opens ONCE for observation, and the
    identity assertion (with screenshot marker) reuses the bound caption."""
    goal = _nav_goal()
    ev = goal_mod.evidence(goal, _nav_probe(mutation=True))
    assert ev["blocking"] == []
    feat, pom = goal_mod.compile_goal(
        goal, ev, "SHOP", nav_keys=["SHOP_WARMUP", "SHOP_EN"])
    whens = [ln.strip() for ln in feat.splitlines()
             if ln.strip().startswith(("When", "And"))
             and "User is on" not in ln]
    assert whens == ["When the user closes the location prompt",
                     "And closes the popup if it appears within 10 seconds",
                     'And User searches for "toy"',
                     'And User clicks "Paw Patrol Toy Truck"',
                     'And User clicks "Add to cart"',
                     'And User waits for the network to be idle',
                     'And User clicks "Cart"']
    assert feat.count('clicks "Cart"') == 1
    assert 'Then the user sees "Paw Patrol Toy Truck" ( take a screenshot )' \
        in feat
    assert "should be at least" not in feat      # identity, never a count
    assert "#pdp-add" in pom
    chk = _validate.check_feature(feat)
    assert chk["error"] is None and _validate.unmatched(chk) == []


def test_add_to_without_mutation_evidence_blocks_never_guesses():
    """The 'Choose options' replay: a landed page with no add control and no
    proven reveal must block with a typed next_action — no invented step."""
    goal = _nav_goal()
    probe = _nav_probe(mutation=False, extra_landed=[
        _ctrl("Choose options", "#pdp-choose")])
    ev = goal_mod.evidence(goal, probe)
    assert any("add_to" in b and "never guessed" in b for b in ev["blocking"])
    assert goal_mod.next_action(ev["blocking"]) == "mutation_path_missing"
    feat, _ = goal_mod.compile_goal(goal, ev, "SHOP")
    assert "Choose options" not in feat


def test_proven_prerequisite_reveal_compiles_with_provenance():
    """A prerequisite may compile ONLY off the probe's before/after reveal
    proof — and it carries required_by: mutation:add_to."""
    goal = _nav_goal()
    probe = _nav_probe(mutation=False, mutation_path={
        "prerequisite": {"name": "Select options", "selector": "#pdp-choose"},
        "control": {"name": "Add to cart", "selector": "#pdp-add",
                    "kind": "button"},
        "evidence": "click revealed the requested mutation control "
                    "(before/after delta recorded)"})
    ev = goal_mod.evidence(goal, probe)
    assert ev["blocking"] == []
    feat, pom = goal_mod.compile_goal(goal, ev, "SHOP")
    steps = [ln.strip() for ln in feat.splitlines()]
    i_pre = next(i for i, s in enumerate(steps) if 'Select options' in s)
    i_add = next(i for i, s in enumerate(steps) if 'clicks "Add to cart"' in s)
    assert i_pre < i_add
    summary = goal_mod.intent_summary(goal, ev)
    pre = [p for p in summary["required_prerequisites"]
           if p["required_by"] == "mutation:add_to"]
    assert len(pre) == 1 and pre[0]["action"] == "Select options"
    assert "revealed" in pre[0]["evidence"]


def test_add_to_requires_bound_pick():
    goal = _nav_goal()
    ev = goal_mod.evidence(goal, _nav_probe(picked=False))
    assert any("add_to" in b or "pick" in b for b in ev["blocking"])
    bad = {"scenario": "s",
           "actions": [{"do": "search", "term": "toy", "id": "s"},
                       {"do": "add_to", "id": "a", "item_from": "nope",
                        "destination": "cart"}],
           "checks": [], "allow_no_assertion": True}
    assert any("item_from" in e for e in goal_mod.validate(bad))


def test_add_to_probe_args_prove_never_perform():
    args = goal_mod.probe_args(_nav_goal())
    assert args["mutate"] == "cart" and args["pick"] == "*"
    assert args["click"] is None        # the mutation is never probe-clicked


def test_mutation_control_is_shared_and_strict():
    ok, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#add"), _ctrl("Cart", "#cart", kind="link")],
        "cart")
    assert why is None and ok["name"] == "Add to cart"
    # a bare destination-opener can never be the mutation
    none, why = goal_mod.mutation_control(
        [_ctrl("Cart", "#cart", kind="link")], "cart")
    assert none is None
    # NOOD_0168 — a FEW same-named visible instances are responsive
    # duplicates of one control (buy box + sticky bar): first visible binds.
    ok, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#c1"), _ctrl("Add to cart", "#c2")], "cart")
    assert why is None and ok["selector"] == "#c1"
    # MANY distinct instances are one-per-card — still block, never pick-first
    none, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", f"#c{i}") for i in range(4)], "cart")
    assert none is None and "scope" in why
    # invisible duplicates don't qualify as the responsive-duplicate case
    none, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#c1", visible=False),
         _ctrl("Add to cart", "#c2", visible=False)], "cart")
    assert none is None and "scope" in why


def test_add_to_without_checks_generates_identity_postcondition():
    goal = _nav_goal(checks=[])
    ev, synth = _synthesize(goal, _nav_probe(mutation=True))
    assert synth["blocking"] == []
    assert synth["checks"] == [{"item_in_destination": "cart",
                                "expected_from": "p", "after": "a"}]


# --- honest intent verification + manual-fallback gate -----------------------

def test_intent_trace_covers_every_contract_requirement():
    goal = _nav_goal()
    ev = goal_mod.evidence(goal, _nav_probe(mutation=True))
    trace = goal_mod.intent_trace(goal, ev)
    nodes = [t["node"] for t in trace]
    assert "navigation[0]" in nodes and "navigation[1]" in nodes
    assert "actions[0]" in nodes and "checks[0]" in nodes
    assert all(t["ok"] for t in trace)
    shot = [t for t in trace if t["node"] == "checks[0]"]
    assert shot[0].get("screenshot") is True


def test_author_goal_records_contract_and_blocks_manual_autorun(
        tmp_path, monkeypatch):
    """The exact drift path, replayed: a goal blocks → the host switches to
    manual feature_content with run_after_author — the engine refuses the
    auto-run (files still written) unless the human override is explicit."""
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _shop_probe(picked=False))
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="t", goal=_shop_goal(),
                         workspace=str(ws))
    assert r["intent_verified"] is False and r["blocking"]
    assert r["next_action"]
    manual = ('@web\nFeature: F\n\n  Scenario: s\n'
              '    Given User is on "{env:SHOP}"\n'
              '    When User clicks "Choose options"\n'
              '    Then User should see "Added to cart"\n')
    calls = []
    monkeypatch.setattr(core, "run_and_report",
                        lambda *a, **kw: calls.append(1) or
                        {"ok": True, "passed": 1})
    r2 = core.author_test(app_name="Shop", base_url="http://localhost:9",
                          feature_path="t", feature_content=manual,
                          overwrite=True, run_after_author=True,
                          workspace=str(ws))
    assert r2["ok"] is False and calls == []
    assert "intent contract" in r2["run"]["skipped"]
    assert r2["next_action"] == "fix_blocked_goal"
    assert r2["author"]["intent_verified"] is False
    # explicit expert override still exists — and is never set autonomously
    r3 = core.author_test(app_name="Shop", base_url="http://localhost:9",
                          feature_path="t", feature_content=manual,
                          overwrite=True, run_after_author=True,
                          allow_unverified_intent=True, workspace=str(ws))
    assert calls == [1] and r3["ok"] is True


def test_goal_payload_stays_compact_and_evidence_goes_to_artifact(
        tmp_path, monkeypatch):
    """Token-cheap boundaries: the model-visible payload carries the trace,
    blocker, and typed next_action — the raw result-card/probe evidence
    lives only in artifacts/probe_goal.json."""
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    probe = _shop_probe()
    probe["pages"][0]["search"]["result_items"] = [
        {"caption": "Paw Patrol Toy Truck", "selector": "#tile1",
         "href": "/p/1", "actions": [{"name": "Add to cart",
                                      "selector": ":nth-match(b, 1)"}]}]
    monkeypatch.setattr(core, "probe_page", lambda url, **kw: probe)
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="t", goal=_shop_goal(),
                         workspace=str(ws))
    payload = json.dumps(r)
    assert "result_items" not in payload         # raw evidence never inline
    artifact = ws / "artifacts" / "probe_goal.json"
    assert artifact.is_file() and "result_items" in artifact.read_text()
    # compact ceilings: trace + evidence + intent stay bounded
    assert len(json.dumps(r.get("intent_trace"))) < 2048
    assert len(json.dumps(r.get("evidence"))) < 4096
    assert len(json.dumps(r.get("intent"))) < 4096
    # blocked payloads stay compact too, and carry ONE typed next_action
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _shop_probe(picked=False))
    rb = core.author_test(app_name="Shop", base_url="http://localhost:9",
                          feature_path="t", goal=_shop_goal(),
                          overwrite=True, workspace=str(ws))
    assert isinstance(rb["next_action"], str)
    assert len(json.dumps({"blocking": rb["blocking"],
                           "next_action": rb["next_action"]})) < 2048


# --- mutation-aware RCA (review fix §5) --------------------------------------

def _entry(message="Comparison failed: expected 'Paw Patrol Toy Truck'"):
    return {"scenario": "buy a toy", "step": "Then the user sees",
            "message": message, "trace": "", "warnings": [],
            "scenario_warnings": []}


def _net(**over):
    net = {"console_errors": [], "page_errors": [],
           "requests": ["https://shop.example.ca/en.html",
                        "https://analytics.example-cdn.com/collect"],
           "failed_requests": [], "mutations": [], "failed_responses": [],
           "ws_frames": []}
    net.update(over)
    return net


def test_aborted_mutation_outranks_generic_assertion_mismatch():
    net = _net(failed_requests=[
        "POST https://shop.example.ca/api/cart/add?sku=123&session=abc "
        "— net::ERR_ABORTED"])
    v = rca_report.mutation_verdict(_entry(), net)
    assert v["category"] == "mutation-failed" and v["confidence"] == "high"
    assert "POST /api/cart/add" in v["reason"]
    # redaction: no host, no query, no payload
    assert "shop.example.ca" not in v["reason"] and "sku=" not in v["reason"]


def test_non_success_mutation_status_is_named():
    net = _net(failed_responses=[
        "503 POST https://shop.example.ca/api/cart/add"])
    v = rca_report.mutation_verdict(_entry(), net)
    assert v["category"] == "mutation-failed"
    assert "HTTP 503" in v["reason"] and "shop.example.ca" not in v["reason"]


def test_successful_mutation_with_failed_postcondition_is_distinguished():
    net = _net(mutations=["POST https://shop.example.ca/api/cart/add"])
    v = rca_report.mutation_verdict(_entry(), net)
    assert v["category"] == "app-regression"
    assert "completed without a network error" in v["reason"]


def test_analytics_and_get_failures_are_ignored():
    net = _net(failed_requests=[
        "POST https://analytics.example-cdn.com/collect — net::ERR_ABORTED",
        "GET https://shop.example.ca/api/recommendations — net::ERR_ABORTED"])
    assert rca_report.mutation_verdict(_entry(), net) is None


def test_non_assertion_failure_gets_no_mutation_verdict():
    net = _net(failed_requests=[
        "POST https://shop.example.ca/api/cart/add — net::ERR_ABORTED"])
    assert rca_report.mutation_verdict(
        _entry(message="TimeoutError: page.goto"), net) is None


def test_collect_correlates_mutation_from_network_capture(tmp_path):
    """End to end over the artifacts the run already writes: failed result
    JSON + network/<scenario>.json → the RCA entry names the dead mutation,
    no probe/inspect/screenshot needed."""
    results = tmp_path / "allure-results"
    results.mkdir()
    _result_json(results, [_step(
        "Then the user sees 'Paw Patrol Toy Truck'", status="failed",
        message="Comparison failed: expected 'Paw Patrol Toy Truck'",
        trace="")], status="failed", name="buy a toy")
    net_dir = tmp_path / "network"
    net_dir.mkdir()
    (net_dir / "buy_a_toy.json").write_text(json.dumps(_net(
        failed_requests=["POST https://shop.example.ca/api/cart/add "
                         "— net::ERR_ABORTED"])))
    entries = rca_report.collect(str(results))
    assert entries[0]["heuristic"]["category"] == "mutation-failed"
    assert "POST /api/cart/add" in entries[0]["heuristic"]["reason"]


def test_engine_stamped_verdicts_keep_priority_over_mutation(tmp_path):
    """navigation-mismatch (engine-stamped, high) must not be displaced."""
    results = tmp_path / "allure-results"
    results.mkdir()
    _result_json(results, [_step(
        "Then the user sees 'x'", status="failed",
        message="Expected to see 'x' not found\n"
                "[navigation-mismatch] expected /cart, current /home",
        trace="")], status="failed", name="nav case")
    net_dir = tmp_path / "network"
    net_dir.mkdir()
    (net_dir / "nav_case.json").write_text(json.dumps(_net(
        failed_requests=["POST https://shop.example.ca/api/cart/add "
                         "— net::ERR_ABORTED"])))
    entries = rca_report.collect(str(results))
    assert entries[0]["heuristic"]["category"] == "navigation-mismatch"


# --- artifact-derived diagnostics (review fix §6) ----------------------------

def _history(ws, stops, category="app-regression"):
    reports = ws / "artifacts" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"historyId": "h1", "stop": s, "scenario": "buy a toy",
                         "category": category, "ai_category": None})
             for s in stops]
    (reports / "rca-history.jsonl").write_text("\n".join(lines) + "\n")


def test_diagnostic_attempts_come_from_run_history(tmp_path, monkeypatch):
    """Six persisted red runs beat the agent's remembered four — and the
    failure sequence survives into the front matter."""
    from noodle import diagnostics
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    base = 1_700_000_000_000
    stale = base - 10 * 24 * 60 * 60 * 1000       # an old session, excluded
    _history(ws, [stale] + [base + i * 60_000 for i in range(6)])
    r = diagnostics.write_diagnostic(
        str(ws), app="shop", triggers=["hard-fail"], summary="six red runs",
        attempts=4, agent_cost="57.57 AIC")
    fm = diagnostics._front_matter(Path(r["path"]))
    assert fm["attempts"] == 6
    assert fm["attempts_source"] == "run-history"
    assert fm["attempts_reported_by_agent"] == 4
    assert len(fm["failure_sequence"]) == 6
    assert fm["agent_cost"] == "57.57 AIC"


def test_diagnostic_cost_is_unreported_never_na(tmp_path, monkeypatch):
    from noodle import diagnostics
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    r = diagnostics.write_diagnostic(
        str(ws), app="shop", triggers=["manual"], summary="x",
        agent_cost="n/a")
    fm = diagnostics._front_matter(Path(r["path"]))
    assert fm["agent_cost"] == "unreported"
    r = diagnostics.write_diagnostic(
        str(ws), app="shop2", triggers=["manual"], summary="x")
    assert diagnostics._front_matter(Path(r["path"]))["agent_cost"] \
        == "unreported"


def test_version_report_flags_stale_installed_metadata(monkeypatch):
    """The 0.2.0a3-vs-0.2.0a7 confusion: installed dist-info lagging the
    checkout's pyproject is a named mismatch, not silent drift."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.2.0a3")
    monkeypatch.setattr(install_check, "source_version", lambda: "0.2.0a7")
    vr = install_check.version_report()
    assert vr == {"installed": "0.2.0a3", "source": "0.2.0a7",
                  "mismatch": True}
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.2.0a7")
    assert install_check.version_report()["mismatch"] is False
    # no checkout (installed wheel) → nothing to compare, no false alarm
    monkeypatch.setattr(install_check, "source_version", lambda: None)
    assert install_check.version_report()["mismatch"] is False


# --- `noodle update` — one command after git pull / git checkout ----------------

def test_reinstall_argv_targets_this_interpreter_and_is_single_step(monkeypatch):
    """The pip path must install into sys.executable's environment (the one
    that imported THIS noodle), and must not uninstall first — a failed
    resolve has to leave the working install standing."""
    import sys as _sys

    from noodle import install_check
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/home/u/.local/lib/python3.11/site-packages/noodle"))
    monkeypatch.setattr(install_check, "_has_pip", lambda: True)
    argv = install_check.reinstall_argv()
    assert argv[:4] == [_sys.executable, "-m", "pip", "install"]
    assert "-e" in argv and "uninstall" not in argv
    # uv tool install replaces in place via --force, also single-step
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/home/u/.local/share/uv/tools/noodle/lib/noodle"))
    argv = install_check.reinstall_argv()
    assert argv[:3] == ["uv", "tool", "install"]
    assert "--force" in argv and "--editable" in argv and "uninstall" not in argv


def test_editable_uv_tool_install_is_detected_from_the_interpreter(monkeypatch):
    """The reported break: an EDITABLE uv tool install leaves the package in
    the clone, so a package-dir-only test sees no uv/tools and falls through to
    `python -m pip` — and uv's venvs have no pip ("No module named pip"). Only
    sys.executable still names uv's tool venv in that case."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/Users/u/Projects/noodle/noodle"))  # the clone
    monkeypatch.setattr(install_check.sys, "executable",
                        "/Users/u/.local/share/uv/tools/noodle/bin/python3")
    assert install_check.reinstall_argv()[:3] == ["uv", "tool", "install"]
    assert "uv tool install" in install_check.reinstall_cmd()


def test_pipless_venv_falls_back_to_uv_pip_targeting_the_same_interpreter(monkeypatch):
    """A uv-created PROJECT venv (not a uv tool install) has no pip either;
    `uv pip install --python <that interpreter>` must target the same
    environment rather than uv's default."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/proj/.venv/lib/python3.11/site-packages/noodle"))
    monkeypatch.setattr(install_check.sys, "executable", "/proj/.venv/bin/python")
    monkeypatch.setattr(install_check, "_has_pip", lambda: False)
    assert install_check.reinstall_argv() == [
        "uv", "pip", "install", "--python", "/proj/.venv/bin/python", "-e", ".[all]"]


def test_clone_root_prefers_the_editable_link_then_the_cwd(tmp_path, monkeypatch):
    """Editable install → the package's own parent IS the checkout. Non-editable
    (site-packages) → fall back to a checkout at/above the cwd, because that is
    the clone the tester just pulled."""
    from noodle import install_check

    def make_clone(root: Path) -> Path:
        (root / "noodle").mkdir(parents=True)
        (root / "unit_tests").mkdir()
        (root / "noodle" / "__init__.py").touch()
        (root / "noodle" / "cli.py").touch()
        (root / "pyproject.toml").write_text('[project]\nname = "noodle"\n')
        return root

    clone = make_clone(tmp_path / "clone")
    monkeypatch.setattr(install_check, "package_dir", lambda: clone / "noodle")
    monkeypatch.chdir(tmp_path)
    assert install_check.clone_root() == clone

    installed = tmp_path / "site-packages" / "noodle"
    installed.mkdir(parents=True)
    monkeypatch.setattr(install_check, "package_dir", lambda: installed)
    monkeypatch.chdir(clone / "unit_tests")          # deep inside the clone
    assert install_check.clone_root() == clone
    monkeypatch.chdir(tmp_path / "site-packages")    # nowhere near a clone
    assert install_check.clone_root() is None


def test_update_runs_the_reinstall_in_the_clone_and_never_git(tmp_path, monkeypatch):
    """It reinstalls in the checkout and touches nothing else — no git, and no
    install attempt at all when there is no clone to link to (exit 2)."""
    from typer.testing import CliRunner

    from noodle import cli, install_check
    calls = []

    class _Done:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **kw: (calls.append((argv, kw.get("cwd"))), _Done())[1])
    monkeypatch.setattr(install_check, "clone_root", lambda: tmp_path)
    monkeypatch.setattr(install_check, "reinstall_argv", lambda: ["pip", "install", "-e", ".[all]"])
    res = CliRunner().invoke(cli.app, ["update"])
    assert res.exit_code == 0
    assert calls == [(["pip", "install", "-e", ".[all]"], tmp_path)]
    assert not any("git" in a[0][0] for a in calls)

    calls.clear()
    monkeypatch.setattr(install_check, "clone_root", lambda: None)
    res = CliRunner().invoke(cli.app, ["update"])
    assert res.exit_code == 2 and not calls


def test_warn_if_stale_names_update_for_a_stale_editable_install(monkeypatch):
    """An editable install whose recorded version lags the checkout still
    warns — that is the branch-changed-dependencies case, not just cosmetics."""
    from noodle import install_check
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.2.0a9")
    monkeypatch.setattr(install_check, "source_version", lambda: "0.2.0a10")
    out = []
    install_check.warn_if_stale(out.append)
    assert len(out) == 1 and "noodle update" in out[0]
    assert "0.2.0a9" in out[0] and "0.2.0a10" in out[0]
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.2.0a10")
    out.clear()
    install_check.warn_if_stale(out.append)
    assert out == []


def test_pyproject_version_has_a_changelog_section():
    """The versioning contract (CLAUDE.md): a bumped version without its
    CHANGELOG section is the drift that makes `noodle --version` meaningless."""
    import tomllib

    from unit_tests.test_nood_0110 import REPO
    with (REPO / "pyproject.toml").open("rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    assert f"## [{version}]" in (REPO / "CHANGELOG.md").read_text()


def test_update_is_discoverable_in_help_with_a_one_line_summary():
    """Testers find `noodle update` by scanning `noodle --help`, so it must be
    listed there — and with a SHORT summary: the full docstring rendered into
    the command list as a 12-line wall."""
    from typer.testing import CliRunner

    from noodle import cli
    top = CliRunner().invoke(cli.app, ["--help"]).stdout
    assert "update" in top and "git pull" in top
    # the long-form rationale belongs to the detail view only
    assert "Deliberately never runs git" not in top
    assert "Deliberately never runs git" in CliRunner().invoke(
        cli.app, ["update", "--help"]).stdout


# --- NOOD_0156 follow-up: the add-to-cart session review ---------------------
#
# A "search a toy, add to cart, verify" session took 9 probes + 5 author/run
# calls. Root causes pinned browser-free here:
#   R1  a nav/promo strip repeats structurally (shared class, distinct
#       hrefs), so "pick any result" bound a header banner ("Support")
#   R2  compact payloads dropped result_items AND capped "add to cart" out
#       of suggested_steps — forcing 600 KB compact=False re-probes
#   R3  a working add-to-cart click read as no-change (1 s settle cap) and
#       the no-delta click left NO record at all
#   R4  strict enum rejection of free-text dismissals / boolean
#       item_in_destination cost one author round trip per phrasing miss
#   R5  the compiled add_to → destination-click sequence aborts the cart
#       POST in flight (net::ERR_ABORTED) — settle before observing

def _rawc(**over):
    c = {"tag": "a", "id": "", "role": "", "type": "", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True}
    c.update(over)
    return c


def _chrome_and_products():
    chrome = [_rawc(text="Flyer", href="/en/flyer.html", cls="nav-item"),
              _rawc(text="Weekly Deals", href="/en/deals.html",
                    cls="nav-item"),
              _rawc(text="Support", href="/en/support.html", cls="nav-item")]
    products = [_rawc(text="Zuru Water Blaster", href="/pdp/zuru.html",
                      cls="product-card"),
                _rawc(text="Tonka Bulldozer", href="/pdp/tonka.html",
                      cls="product-card"),
                _rawc(text="Lego City Set", href="/pdp/lego.html",
                      cls="product-card")]
    return chrome, products


def test_result_items_drop_chrome_group_persisted_by_name():
    # R1 — the nav strip's captions all existed on the pre-search page: the
    # group is chrome, not results, even though its structure repeats.
    chrome, products = _chrome_and_products()
    items = probe_mod.build_result_items(
        chrome + products, prev_selectors=set(),
        prev_names={"flyer", "weekly deals", "support"})
    assert [i["caption"] for i in items] == \
        ["Zuru Water Blaster", "Tonka Bulldozer", "Lego City Set"]


def test_result_items_drop_chrome_group_persisted_by_selector():
    chrome, products = _chrome_and_products()
    prev = {probe_mod._selector(c) for c in chrome}
    items = probe_mod.build_result_items(chrome + products,
                                         prev_selectors=prev)
    assert [i["caption"] for i in items] == \
        ["Zuru Water Blaster", "Tonka Bulldozer", "Lego City Set"]


def test_result_items_without_prev_context_keep_structural_groups():
    # No previous-page context (direct results-URL probe): structure alone
    # still decides, both repeating groups stay — the legacy contract.
    chrome, products = _chrome_and_products()
    items = probe_mod.build_result_items(chrome + products)
    assert len(items) == 6


def test_result_items_minority_overlap_keeps_the_group():
    # A returning product (seen in a homepage carousel) must not disqualify
    # the whole result group — only MAJORITY-persisted groups are chrome.
    _, products = _chrome_and_products()
    items = probe_mod.build_result_items(
        products, prev_selectors=set(), prev_names={"zuru water blaster"})
    assert len(items) == 3


def test_bind_result_never_binds_persisted_chrome():
    # R1 end-to-end: with the chrome group dropped, the generic pick binds
    # the first real product, not the banner the session got.
    chrome, products = _chrome_and_products()
    items = probe_mod.build_result_items(
        chrome + products, prev_selectors=set(),
        prev_names={"flyer", "weekly deals", "support"})
    cand, why = goal_mod.bind_result([], "toy", items=items)
    assert why is None and cand["name"] == "Zuru Water Blaster"


def test_compact_search_block_carries_result_items():
    # R2 — the author-ready cards must survive compact mode.
    pg = {"url": "u", "title": "t", "controls": [], "headings": [],
          "pom_yaml": "", "term": "toy",
          "result_items": [{"caption": "Zuru", "selector": "#z",
                            "href": "/z", "actions": []}]}
    out = probe_mod._compact_page(pg, 40)
    assert out["result_items"][0]["caption"] == "Zuru"


def test_rank_ready_mutating_button_beats_the_chrome_flood():
    # R2 — "add to cart" is a plain button (no type=submit); 40+ chrome
    # controls before it must not cap it out of the compact steps.
    chrome = [{"kind": "button", "name": f"chrome {i}", "selector": f"#c{i}",
               "visible": True, "needs_pom": False, "step": f"clicks c{i}"}
              for i in range(45)]
    atc = {"kind": "button", "name": "add to cart", "selector": "#atc",
           "visible": True, "needs_pom": False,
           "step": 'clicks "add to cart"'}
    ranked = probe_mod._rank_ready(chrome + [atc])
    assert ranked[0]["name"] == "add to cart"


def _noop_click_page():
    page = MagicMock()
    loc = MagicMock()
    page.locator.return_value = loc

    def _evaluate(js, *a, **k):
        if "__noodleMo" in js or "__noodleMut" in js:
            return True
        return {"controls": [], "headings": []}
    page.evaluate.side_effect = _evaluate
    page.url = "https://app.example/x"
    page.title.return_value = "t"
    return page


def test_do_noop_click_still_leaves_a_revealed_record():
    # R3 — "click did nothing" and "click worked, rendered late" were
    # indistinguishable because the empty delta left no record at all.
    page = _noop_click_page()
    pg = {"url": "https://app.example/x", "title": "t", "headings": [],
          "controls": [{"kind": "button", "name": "add to cart",
                        "selector": "#atc", "visible": True,
                        "needs_pom": False, "step": "s"}],
          "pom_yaml": "", "next_pages": []}
    probe_mod._do(page, pg, probe_mod.parse_do(["click add to cart"]),
                  timeout_ms=1000)
    assert pg["do_completed"] == 1
    (rev,) = pg["revealed"]
    assert rev["revealed_by"] == "do: click add to cart"
    assert "no observable delta" in rev["note"]


def test_do_noop_fill_stays_silent():
    # a no-delta fill is the NORMAL case — no noise record for it
    page = _noop_click_page()
    pg = {"url": "https://app.example/x", "title": "t", "headings": [],
          "controls": [{"kind": "field", "name": "zip", "selector": "#z",
                        "visible": True, "needs_pom": False, "step": "s"}],
          "pom_yaml": "", "next_pages": []}
    probe_mod._do(page, pg, probe_mod.parse_do(["enter 90210 in zip"]),
                  timeout_ms=1000)
    assert "revealed" not in pg


def test_settle_gives_mutating_clicks_a_server_round_trip():
    # R3 — the 1 s first-change cap misread a working add-to-cart as
    # no-change; a mutating click waits out a network round trip (5 s).
    for mutating, budget in ((True, 5000), (False, 1000)):
        page = MagicMock()
        page.url = "u"
        calls = []
        page.wait_for_function.side_effect = \
            lambda js, timeout=None: calls.append(timeout)
        page.evaluate.return_value = True
        probe_mod._settle(page, 30000, armed=object(), url_before="u",
                          mutating=mutating)
        assert calls[0] == budget


def test_normalize_maps_free_text_dismissals():
    # R4 — a tester's words, not our enum (the session's exact phrasings).
    g, notes = goal_mod.normalize({
        "scenario": "s",
        "dismissals": ["closes the location prompt",
                       "closes the popup if it appears within 10 seconds"]})
    assert g["dismissals"] == ["location_prompt", "popups"]
    assert len(notes) == 2
    assert not any("dismissal" in e for e in goal_mod.validate(g))


def test_normalize_dedupes_collapsed_dismissals():
    g, _ = goal_mod.normalize(
        {"scenario": "s", "dismissals": ["close the popup", "modal",
                                         "popups"]})
    assert g["dismissals"] == ["popups"]


def test_normalize_boolean_item_in_destination():
    g, notes = goal_mod.normalize({
        "scenario": "s",
        "actions": [{"do": "pick", "id": "p"},
                    {"do": "add_to", "id": "a", "item_from": "p",
                     "destination": "cart"}],
        "checks": [{"item_in_destination": True, "expected_from": "p"}]})
    assert g["checks"][0]["item_in_destination"] == "cart"
    assert any("cart" in n for n in notes)


def test_normalize_evidence_phrase_means_screenshot():
    g, _ = goal_mod.normalize({
        "scenario": "s",
        "checks": [{"see": "x",
                    "evidence": "take screenshot for verification"}]})
    assert g["checks"][0]["evidence"] == "screenshot"


def test_normalize_leaves_the_unmappable_for_validate():
    g, notes = goal_mod.normalize(
        {"scenario": "s", "dismissals": ["dance a jig"]})
    assert g["dismissals"] == ["dance a jig"] and notes == []
    assert any("unknown dismissal" in e for e in goal_mod.validate(g))


def test_compile_goal_settles_before_the_destination_click():
    # R5 — navigating to the cart the instant add-to-cart returns aborts
    # the mutation POST in flight; the compiled flow settles first.
    goal = _shop_goal()
    feat, _ = goal_mod.compile_goal(goal, goal_mod.evidence(
        goal, _shop_probe()), "APP")
    lines = [ln.strip() for ln in feat.splitlines()]
    idle = next(i for i, ln in enumerate(lines)
                if "network to be idle" in ln)
    dest = next(i for i, ln in enumerate(lines)
                if 'User clicks "Cart"' in ln)
    assert idle == dest - 1


def test_mcp_run_and_report_serves_by_default():
    # the documented workflow ALWAYS ends on served links, never file paths
    import inspect
    server = pytest.importorskip("noodle.mcp.server")
    sig = inspect.signature(server.run_and_report)
    assert sig.parameters["serve_reports"].default is True

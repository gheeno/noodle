"""NOOD_0207 — one simple web test cost 45.7 AIC, then 39 on the re-run.

Two reviewed sessions authored the SAME ask against a list page whose 20
sibling item cards share ONE action-button selector. The first took 21
author/run laps, 10 standalone probes, ~14 browser launches, a red run and a
hand-edited POM selector. The second shipped a GREEN TEST FOR THE WRONG ITEM
— it gave up on the requested one, renamed the scenario, and asserted a
sibling. A false green is a worse defect than a slow author, and it followed
directly from a repair instruction that could not be satisfied.

Cost is `calls x context` and context only grows, so a refusal on clause 1 is
the most expensive object in the system. The root causes, pinned here:

§1 diagnose-without-repair — at every block the engine held the evidence
   that fixes the block (its own probe) and shipped the problem statement
   alone. The costliest instance: `_locate` counted DISTINCT SELECTOR STRINGS
   to decide "repeated control", while the probe stores ONE representative
   selector per family plus `unique: False` / `matches: 20` and literally
   prints "⚠ selector matches 20 nodes". 20 identical nodes collapse to one
   string, so it scored a unique match, compiled the POM, reached
   `ready: true` — and the run acted on instance 1.
§2 unauthorable — nothing could scope an action to ONE instance of a repeated
   control, and `add_to` demanded a prior `search` a catalogue page has no
   box for. The only way through was a hand-written POM selector: the one
   repair a test author cannot automate.
§3 refused shapes the grammar already owned — an AC preamble, a label list, a
   compound verify and a bare imperative. Four laps, each re-paying for the
   whole transcript.
§4 RCA blaming the part that worked — the flow HAD succeeded (right page,
   right item, right total) and only the assertion's wording differed; RCA
   said "the click left the page unchanged — the destination was never
   reached". Provably false, and it points the reader at the one thing that
   was working.

Every rule added is expressed over page structure and clause shape — sibling
controls sharing a selector, destination-word position, comma-separated
label/value pairs, imperative openers. No app, product or brand name appears
in any regex, message or fixture. No browser, no LLM, no network.
"""
import json
from types import SimpleNamespace

import pytest

from noodle.agents.web import actions
from noodle.agents.web import probe as probe_mod
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe
from noodle.reporting import rca_report as rr

# --- fixtures: the shape, not a domain ---------------------------------------

def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _grid(n=20, control="Add to basket", dest="basket"):
    """A page of `n` sibling cards sharing ONE action-button selector — the
    exact payload the probe emits for a repeated control family: one
    representative control, `unique: False`, `matches: n`, and the per-card
    captions as headings."""
    pg = {"url": "https://x/", "title": "t",
          "controls": [_ctrl(control, ".card button",
                             unique=False, matches=n)],
          "headings": [f"Widget {chr(65 + i)}" for i in range(n)],
          "pom_yaml": "", "permission_prompts": [], "popups_closed": 0}
    return {"pages": [pg], "errors": []}, dest


def _blocks(result):
    return goal_mod._page_blocks(result["pages"][0])


# --- §1 the probe's own proof is honoured, and carries its repair ------------

def test_proven_ambiguity_blocks_instead_of_acting_on_instance_one():
    result, _ = _grid()
    ctrl, _, _, note = goal_mod._locate("Add to basket", _blocks(result))
    assert ctrl is None, "20 nodes behind one selector is not a unique match"
    assert "matches 20" in note
    assert "within:" in note        # the block names its own fix


def test_distinct_selectors_still_block_the_old_way():
    # the pre-existing len(sels) > 1 path is untouched
    result, _ = _grid()
    result["pages"][0]["controls"] = [_ctrl("Edit", "#r1 .edit"),
                                      _ctrl("Edit", "#r2 .edit")]
    ctrl, _, _, note = goal_mod._locate("Edit", _blocks(result))
    assert ctrl is None and "matches 2" in note


def test_a_responsive_duplicate_pair_is_one_control_not_a_grid():
    """The threshold that keeps the gate a bug-catcher instead of a tax.

    A responsive page renders its header twice (desktop bar + collapsed menu),
    so `matches: 2` on a header link is ONE control at two breakpoints — both
    instances go to the same place. NOOD_0168 already settled this rule for
    the mutation path; the gate above shares its ceiling. Caught live: without
    it the benchmark's tc2 went from green to blocked.
    """
    result, _ = _grid()
    result["pages"][0]["controls"] = [_ctrl("Create account", "#pt-createaccount",
                                            unique=False, matches=2)]
    ctrl, _, _, note = goal_mod._locate("Create account", _blocks(result))
    assert ctrl is not None and note is None
    assert goal_mod._DUPLICATE_CEILING == 3


def test_past_the_duplicate_ceiling_it_is_a_per_item_family():
    result, _ = _grid()
    result["pages"][0]["controls"] = [_ctrl("Edit", ".row .edit",
                                            unique=False, matches=4)]
    ctrl, _, _, note = goal_mod._locate("Edit", _blocks(result))
    assert ctrl is None and "matches 4" in note


def test_a_genuinely_unique_control_is_unaffected():
    result, _ = _grid()
    result["pages"][0]["controls"] = [_ctrl("Submit", "#go", unique=True,
                                            matches=1)]
    ctrl, _, _, note = goal_mod._locate("Submit", _blocks(result))
    assert ctrl is not None and note is None


def test_within_is_exactly_what_the_gate_asks_for_so_it_passes():
    result, _ = _grid()
    ctrl, _, _, note = goal_mod._locate("Add to basket", _blocks(result),
                                        True)
    assert ctrl is not None and note is None


def test_unmatched_target_names_the_near_miss_and_the_probed_shortlist():
    result, _ = _grid()
    miss = goal_mod._near_miss("Widget Z", _blocks(result))
    assert "did you mean" in miss and "Widget A" in miss
    # capped — the shortlist is a hint, not a probe dump
    assert miss.count('"') <= 20 and "more)" in miss


def test_near_miss_is_silent_when_there_is_nothing_to_suggest():
    assert goal_mod._near_miss("anything", []) == ""


# --- §2 within: makes one instance of a repeated control authorable ----------

def test_within_click_compiles_to_the_row_scoped_step_and_no_pom():
    result, _ = _grid()
    goal = {"scenario": "Act on one card",
            "actions": [{"do": "click", "target": "Add to basket",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    assert goal_mod.validate(goal) == []
    ev = goal_mod.evidence(goal, result)
    assert ev["blocking"] == [], ev["blocking"]
    feature, pom = goal_mod.compile_goal(goal, ev, "BASE_URL")
    assert 'in the row containing "Widget G"' in feature
    # NO POM entry: the probed selector matches every card, so pinning it
    # would re-bind the step to instance 1 and undo the scoping.
    assert pom is None or ".card button" not in pom


def test_within_enter_compiles_to_the_row_scoped_fill():
    result, _ = _grid(control="Quantity")
    result["pages"][0]["controls"][0]["kind"] = "textbox"
    goal = {"scenario": "Fill one row",
            "actions": [{"do": "enter", "target": "Quantity", "value": "3",
                         "within": "Widget C"}],
            "checks": [{"see": "Widget C"}]}
    ev = goal_mod.evidence(goal, result)
    assert ev["blocking"] == [], ev["blocking"]
    feature, _ = goal_mod.compile_goal(goal, ev, "BASE_URL")
    assert 'in the "Quantity" field in the row containing "Widget C"' in feature


@pytest.mark.parametrize("step", [
    'User clicks "Add to basket" in the row containing "Widget G"',
    'User enters "3" in the "Quantity" field in the row containing "Widget C"',
])
def test_the_compiled_scoped_steps_resolve_in_the_pattern_table(step):
    # the steps have existed since NOOD_0011; goal mode simply never reached
    # them. If that ever stops being true the compiler emits dead Gherkin.
    from noodle.resolver.patterns import match, normalize_subject
    assert match(normalize_subject(step)) is not None, step


def test_within_naming_no_captured_instance_blocks_and_lists_what_exists():
    result, _ = _grid()
    goal = {"scenario": "Act on a card that isn't there",
            "actions": [{"do": "click", "target": "Add to basket",
                         "within": "Widget ZZZ"}],
            "checks": [{"see": "Widget A"}]}
    ev = goal_mod.evidence(goal, result)
    assert any("Widget ZZZ" in b and "Widget A" in b for b in ev["blocking"])


def test_row_scope_climbs_to_the_item_container_when_there_is_no_table_row():
    """_row_scope covered role=row only, so the same step that worked on a
    table silently failed on a card grid — the commoner shape."""
    calls = []

    class _Loc:
        def __init__(self, depth=0):
            self.depth = depth

        def count(self):
            return 0 if self.depth == -1 else 1

        def filter(self, **_):
            return _Loc(-1)          # no role=row anywhere

        def locator(self, sel):
            if sel == "visible=true":
                return self
            calls.append(sel)
            return _Loc(self.depth + 1)

        @property
        def first(self):
            return self

    page = SimpleNamespace(url="https://x/",
                           get_by_role=lambda r: _Loc(),
                           get_by_text=lambda t, exact=False: _Loc())
    # the control is found two ancestors up — the innermost container that
    # holds BOTH the caption and the control, i.e. the card, never the grid
    seen = {"n": 0}

    def _find(_page, _text, scope=None, **_kw):
        seen["n"] += 1
        return object() if seen["n"] >= 2 else None

    orig, actions.find = actions.find, _find
    try:
        scope = actions._row_scope(page, "Widget G", "Add to basket")
    finally:
        actions.find = orig
    assert scope is not None
    assert calls.count("xpath=..") == 2      # bounded climb, stops on the hit


def test_row_scope_without_a_control_hint_keeps_the_strict_behaviour():
    class _Empty:
        def count(self):
            return 0

        def filter(self, **_):
            return self

        def locator(self, _):
            return self

    page = SimpleNamespace(url="https://x/", get_by_role=lambda r: _Empty(),
                           get_by_text=lambda t, exact=False: _Empty())
    with pytest.raises(AssertionError, match="No row containing"):
        actions._row_scope(page, "Widget G")


# --- §2b the searchless add_to: a grid has no search box ---------------------

def test_add_to_without_a_search_is_expanded_not_refused():
    exp = pe.expand("1. go to https://shop.example.com\n"
                    "2. add Widget G to basket\n"
                    "3. verify basket has Widget G")
    assert exp["ok"], exp.get("unrecognized")
    add = next(a for a in exp["goal"]["actions"] if a["do"] == "add_to")
    assert add["within"] == "Widget G" and add["destination"] == "basket"
    assert not any(a["do"] in ("search", "pick")
                   for a in exp["goal"]["actions"])


def test_add_to_still_needs_the_item_named_one_way_or_the_other():
    errs = goal_mod.validate({"scenario": "s",
                              "actions": [{"do": "add_to",
                                           "destination": "basket"}],
                              "checks": [{"see": "x"}]})
    assert any("item_from" in e and "within" in e for e in errs)


def test_a_scoped_add_to_never_gets_an_implied_pick_wired_into_it():
    g, _ = goal_mod.normalize(
        {"scenario": "s",
         "actions": [{"do": "search", "term": "t"},
                     {"do": "add_to", "destination": "basket",
                      "within": "Widget G"}],
         "checks": [{"see": "x"}]})
    assert not any(a["do"] == "pick" for a in g["actions"])
    assert "item_from" not in g["actions"][-1]


def test_scoped_mutation_resolves_on_a_grid_instead_of_blocking():
    controls = [_ctrl("Add to basket", ".card button", unique=False,
                      matches=20)]
    ctrl, why = goal_mod.mutation_control(controls, "basket", scoped=True)
    assert ctrl is not None and why is None
    # unscoped, the same grid still blocks — and names the repair
    ctrl, why = goal_mod.mutation_control(
        [_ctrl("Add to basket", "#c1"), _ctrl("Add to basket", "#c2"),
         _ctrl("Add to basket", "#c3"), _ctrl("Add to basket", "#c4")],
        "basket")
    assert ctrl is None and "within:" in why


def test_destination_prefix_tie_break_prefers_the_label_that_acts():
    # "basket now" NAVIGATES to the destination; "add to basket" ACTS on it.
    # Position is the discriminator — _is_mutating returns False for both
    # once the destination word is stripped.
    ctrl, why = goal_mod.mutation_control(
        [_ctrl("basket now", "#nav"), _ctrl("add to basket", "#act")],
        "basket")
    assert ctrl is not None and ctrl["name"] == "add to basket"


def test_two_acting_labels_stay_ambiguous():
    ctrl, why = goal_mod.mutation_control(
        [_ctrl("add to basket", "#a"), _ctrl("move to basket", "#b")],
        "basket")
    assert ctrl is None and "ambiguous" in why


def test_searchless_add_to_compiles_scoped_and_generates_an_identity_check():
    result, _ = _grid()
    goal = {"scenario": "Add one item",
            "actions": [{"do": "add_to", "id": "a1", "destination": "basket",
                         "within": "Widget G"}]}
    ev = goal_mod.evidence(goal, result)
    assert ev["blocking"] == [], ev["blocking"]
    post = goal_mod.infer_postcondition(goal, ev)
    assert post["blocking"] == []
    assert post["checks"][0]["any_of"] == ["Widget G"]   # identity, not count
    feature, pom = goal_mod.compile_goal(
        {**goal, "checks": post["checks"]}, ev, "BASE_URL")
    assert 'in the row containing "Widget G"' in feature
    assert pom is None or ".card button" not in pom


# --- §2c past the evidence gate the probe never looked ----------------------

def test_evidence_gate_is_narrower_than_the_runtime_gate():
    # the probe DOES follow a pick and resolve an enter's field, so an
    # unprobed control there is genuinely absent and must still block
    # (test_nood_0156 pins that). It never performs a state WRITE.
    acts = [{"do": "search", "term": "t"}, {"do": "pick", "id": "p"},
            {"do": "enter", "target": "f", "value": "v"},
            {"do": "add_to", "destination": "d", "item_from": "p"},
            {"do": "click", "target": "Place order"}]
    assert goal_mod._runtime_gate(acts) == 1      # the pick
    assert goal_mod._evidence_gate(acts) == 3     # the add_to
    assert goal_mod._evidence_gate([{"do": "click", "target": "x"}]) is None


def test_a_control_past_the_gate_is_deferred_to_the_run_not_blocked():
    result, _ = _grid()
    goal = {"scenario": "Multi-page flow",
            "actions": [{"do": "add_to", "id": "a1", "destination": "basket",
                         "within": "Widget G"},
                        {"do": "click", "target": "Continue to checkout"}],
            "checks": [{"see": "Widget G"}]}
    ev = goal_mod.evidence(goal, result)
    assert ev["blocking"] == [], ev["blocking"]
    feature, _ = goal_mod.compile_goal(goal, ev, "BASE_URL")
    assert 'User clicks "Continue to checkout"' in feature


def test_the_same_control_before_the_gate_still_blocks():
    result, _ = _grid()
    goal = {"scenario": "One page",
            "actions": [{"do": "click", "target": "Continue to checkout"},
                        {"do": "add_to", "destination": "basket",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    ev = goal_mod.evidence(goal, result)
    assert any("Continue to checkout" in b for b in ev["blocking"])


def test_an_unproven_see_names_what_the_page_does_show():
    result, _ = _grid()
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "Add to basket",
                         "within": "Widget A"}],
            "checks": [{"see": "Widget A"}, {"see": "Sprocket"}]}
    ev = goal_mod.evidence(goal, result)
    bad = next(b for b in ev["blocking"] if "Sprocket" in b)
    assert "Widget A" in bad        # the probed vocabulary rides the blocker


# --- §3 the grammar stops refusing shapes it already owns --------------------

def test_an_acceptance_criteria_preamble_is_metadata_not_a_bogus_assertion():
    exp = pe.expand("AC: the user can add an item and reach the summary\n"
                    "1. go to https://shop.example.com\n"
                    "2. verify Widget A")
    assert exp["ok"], exp.get("unrecognized")
    assert any("brief metadata" in a for a in exp["assumptions"])
    assert not any("add an item and reach" in str(c)
                   for c in exp["goal"]["checks"])


def test_a_label_list_becomes_one_enter_per_pair():
    exp = pe.expand('1. go to https://shop.example.com\n'
                    '2. fill the customer information: Full name - A Person, '
                    'Email: a@b.co, Postcode: SW1A 1AA\n'
                    '3. verify Widget A')
    assert exp["ok"], exp.get("unrecognized")
    enters = [a for a in exp["goal"]["actions"] if a["do"] == "enter"]
    assert len(enters) == 3
    assert [e["value"] for e in enters] == ["A Person", "a@b.co", "SW1A 1AA"]
    assert enters[0]["target"] == "Full name"


def test_a_half_matching_label_list_is_left_alone():
    # all-or-nothing: guessing half a form is worse than refusing whole
    out = pe._rewrite_asks([{"id": "clause-1", "line": 1, "evidence": False,
                             "text": "fill the form: Name - A, and then some "
                                     "prose with no pair in it at all"}])
    assert len(out) == 1


def test_a_compound_verify_becomes_one_check_per_part():
    exp = pe.expand("1. go to https://shop.example.com\n"
                    "2. verify Widget A, Widget B and Widget C")
    assert exp["ok"], exp.get("unrecognized")
    seen = [c.get("see") for c in exp["goal"]["checks"]]
    assert seen == ["Widget A", "Widget B", "Widget C"]


@pytest.mark.parametrize("body", [
    'Widget A" or "Widget B',
    "at least 1 result with title Widget A",
    "Widget A or Widget B",
])
def test_a_compound_verify_never_splits_a_shape_the_grammar_reads_as_one(body):
    # load-bearing guard: NOOD_0197 disjunctions and NOOD_0125 count floors
    # are ONE assertion each. Splitting them breaks the tests that pin them.
    out = pe._rewrite_asks([{"id": "clause-1", "line": 1, "evidence": False,
                             "text": f"verify {body}"}])
    assert len(out) == 1


def test_a_bare_terminal_imperative_is_the_control_it_names():
    exp = pe.expand("1. go to https://shop.example.com\n"
                    "2. verify Widget A\n3. place the order")
    assert exp["ok"], exp.get("unrecognized")
    last = exp["goal"]["actions"][-1]
    assert last["do"] == "click" and last["target"] == "place the order"


def test_an_evidence_suffix_no_longer_leaks_into_the_asserted_literal():
    exp = pe.expand("1. go to https://shop.example.com\n"
                    "2. verify Widget A is present - take a screenshot on "
                    "this step")
    assert exp["ok"], exp.get("unrecognized")
    chk = exp["goal"]["checks"][0]
    # the screenshot request survives as structured evidence; only its words
    # stop leaking into the literal the step asserts
    assert chk["evidence"] == "screenshot"
    assert "on this step" not in chk["see"] and "screenshot" not in chk["see"]


# --- §4 RCA stops blaming the part that worked ------------------------------

def _stub(url="https://x/app", click=None, rendered=""):
    p = SimpleNamespace(url=url,
                        evaluate=lambda _js: (len(rendered)
                                              if "length" in _js else rendered))
    if click is not None:
        p._noodle_click = click
    return p


def test_an_spa_re_render_is_not_a_stuck_click():
    # same URL is NOT the same page: an SPA draws the destination in place.
    page = _stub(click=("place order", "https://x/app", 40), rendered="x" * 900)
    assert actions.stuck_click(page) is None


def test_same_url_and_same_render_still_is_a_stuck_click():
    page = _stub(click=("place order", "https://x/app", 5), rendered="xxxxx")
    note = actions.stuck_click(page)
    assert note and "rendered page is identical" in note


def test_an_unknown_fingerprint_never_claims_sameness():
    page = _stub(click=("place order", "https://x/app", None), rendered="x")
    assert actions.stuck_click(page) is None


def test_a_failed_assertion_reports_what_the_page_actually_renders():
    page = _stub(rendered="Basket\nOrder Placed Successfully!\nTotal 12.00")
    with pytest.raises(AssertionError) as e:
        actions._assert_visible_ocr_or_fail(page, "Order placed successfully")
    assert "near-miss" in str(e.value)
    assert "Order Placed Successfully!" in str(e.value)


def test_the_near_miss_falls_back_to_token_containment_for_short_text():
    page = _stub(rendered="Your order 12345 was placed successfully today")
    assert "placed successfully" in actions._rendered_near_miss(
        page, "order placed successfully").casefold()


def test_assertion_wording_outranks_the_click_verdict():
    e = {"message": "Expected to see 'Order placed successfully' on page — "
                    "not found. [near-miss] the page renders: "
                    "'Order Placed Successfully!'\nURL: https://x/app",
         "trace": "", "warnings":
             ["[no-navigation] clicking 'Place order' left the page unchanged "
              "(URL still /app, and the rendered page is identical)"],
         "step": "Then the page shows ...", "scenario": "s", "feature": "f"}
    v = rr.classify(e)
    assert v["category"] == "assertion-wording"
    assert "assertion-wording" in rr.CATEGORIES
    assert "reached its destination" in v["reason"]


def test_an_ambiguous_click_outranks_the_submit_control_verdict():
    e = {"message": "Expected to see 'Widget G' on page — not found.",
         "trace": "", "warnings":
             ["Ambiguous locator 'Add to basket' — matched multiple elements:"],
         "scenario_warnings": [], "step": "Then ...", "scenario": "s",
         "feature": "f"}
    v = rr.classify(e)
    assert v["category"] == "ambiguous-item-click"
    assert "within:" in v["fix"]


def test_an_ambiguous_locator_elsewhere_does_not_hijack_other_failures():
    e = {"message": "Could not find element: 'Save'",
         "trace": "", "warnings":
             ["Ambiguous locator 'Add to basket' — matched multiple elements:"],
         "scenario_warnings": [], "step": "When ...", "scenario": "s",
         "feature": "f"}
    assert rr.classify(e)["category"] != "ambiguous-item-click"


# --- §5 paper cuts, each one a measured round trip --------------------------

def test_a_present_but_unquoted_scalar_says_quote_it_not_is_required():
    errs = goal_mod.validate(
        {"scenario": "s",
         "actions": [{"do": "enter", "target": "Postcode", "value": 5000}],
         "checks": [{"see": "x"}]})
    assert any("must be a string" in e and "quote it" in e for e in errs)
    assert not any("is required" in e for e in errs)


def test_feature_path_is_derived_from_the_goal_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "probe_page", lambda *a, **k: _grid()[0])
    res = core.author_test(
        app_name="shop", base_url="https://x/",
        goal={"scenario": "Add one item to the basket",
              "actions": [{"do": "click", "target": "Add to basket",
                           "within": "Widget G"}],
              "checks": [{"see": "Widget G"}]},
        workspace=str(tmp_path))
    assert "feature_path" not in str(res.get("error", ""))
    assert "add_one_item_to_the_basket" in json.dumps(res)


def test_the_exists_error_carries_its_own_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "probe_page", lambda *a, **k: _grid()[0])
    goal = {"scenario": "Add one item",
            "actions": [{"do": "click", "target": "Add to basket",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    kw = dict(app_name="shop", base_url="https://x/",
              feature_path="noodle_tests/shop/features/f.feature",
              goal=goal, workspace=str(tmp_path))
    core.author_test(**kw)
    again = core.author_test(**kw)
    assert again["ok"] is False and "overwrite: true" in again["error"]


def test_a_repeated_family_cannot_push_a_submit_control_past_the_cap():
    result, _ = _grid(n=40)
    pg = result["pages"][0]
    pg["controls"] = [_ctrl(f"Add to basket {i}", ".card button",
                            unique=False, matches=40) for i in range(40)]
    pg["controls"].append(_ctrl("Place order", "#submit", submit=True))
    out = probe_mod._render_section(result, "controls", max_controls=5)
    assert "Place order" in out


def test_find_also_searches_headings_and_names_the_state_it_searched():
    result, _ = _grid()
    assert probe_mod.find_controls(result, "Widget G")
    miss = probe_mod.render_find(result, "nothing here")
    assert "https://x/" in miss and "--do" in miss


def test_an_empty_state_dependent_route_says_so_instead_of_reading_as_thin():
    pg = {"url": "https://x/checkout", "title": "t",
          "controls": [], "headings": ["Checkout"], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    probe_mod._apply_page_signals(pg, {})
    assert any("reach that state first" in w for w in pg["warnings"])


def test_a_populated_page_gets_no_empty_state_hint():
    pg = {"url": "https://x/", "title": "t",
          "controls": [_ctrl("a", "#a"), _ctrl("b", "#b")],
          "headings": ["Home"], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    probe_mod._apply_page_signals(pg, {})
    assert not any("reach that state first" in w
                   for w in pg.get("warnings", []))

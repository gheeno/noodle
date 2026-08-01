"""NOOD_0212 — a pasted brief authors without a rewrite lap, and the escape
hatches for an ambiguous control actually work.

Every case here came from a real session in which the engine refused, or
authored something that died at run time, on a prompt a human wrote by hand.
The expensive ones refuse at clause 1: each retry re-pays the whole transcript,
so a brief that needs three laps costs roughly three times what it should.
"""
import pytest
import yaml

from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

URL = "https://shop.example.com/en.html"


# --- the brief scaffolding that used to refuse --------------------------------

@pytest.mark.parametrize("line", [
    # bare section headers — the clause splitter strips the trailing colon, so
    # _META_LABEL (which requires one) never saw them
    "Web Test",
    "AC :",
    "AC:",
    "Acceptance Criteria :",
    "Pre-requisites :",
    # addressed to the agent, not the browser
    "Generate a Noodle test in this workspace",
    "Create a test for the checkout flow",
    # about how the test should be written or reported
    "Agent rules: follow AGENTS.md (token economy + step-writing rules)",
    "pre-requisites in Background:, step-dictionary sentences only, validate",
    "before running, finish with the Allure + RCA report links and nothing else",
])
def test_brief_scaffolding_is_metadata_not_a_refusal(line):
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] == "metadata"


@pytest.mark.parametrize("line,kind", [
    ("click the Continue button", "click"),
    ("search for a toy", "search"),
    ("add to cart", "add_to"),
    ("verify the page shows Welcome", "verify"),
    (f"go to {URL}", "nav"),
])
def test_real_steps_keep_their_verb(line, kind):
    """The rescue runs only after every verb has had its turn — a step always
    wins. Without this the new patterns would eat real instructions."""
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] == kind


@pytest.mark.parametrize("line", [
    # the word "test"/"generate" appears, but the clause is an ATTEMPTED STEP
    "create a test account",
    "generate a report from the dashboard",
    "create a new account",
    "write the summary into the notes field",
])
def test_near_misses_still_refuse_rather_than_being_swallowed(line):
    """These are unparsed today — the point is that they keep refusing with a
    rewrite hint. Silently filing a real instruction as brief noise would drop
    it from the test without anyone noticing."""
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] != "metadata"


# --- a URL that never says the word "url" -------------------------------------

def test_ui_label_carries_the_base_url_and_its_back_reference_resolves():
    """`UI : <url>` … `go to UI` — the brief's own alias. Refused before for
    want of a URL, because only a label containing the word "url" counted."""
    exp = pe.expand(f"Web Test\nUI : {URL}\nAC :\n1. go to UI\n"
                    "2. assert that the page returns 200")
    assert not exp.get("unresolved")
    assert exp["goal"]["navigation"] == [URL]


def test_url_label_wrapped_in_prose_yields_the_url_not_the_sentence():
    """"base url : run this on <url>" used to compile a click on the whole
    sentence, so the brief ended with no URL and its own "open the website"
    line refused."""
    exp = pe.expand(f"base url : Noodle run on {URL}\n"
                    "1. Open the website\n2. Search for a toy")
    assert not exp.get("unresolved")
    assert exp["goal"]["navigation"] == [URL]


def test_a_brief_with_no_url_anywhere_still_refuses():
    """The fix must not paper over the genuine case."""
    exp = pe.expand("1. Open the website\n2. Search for a toy")
    assert [u["text"] for u in exp["unresolved"]] == ["Open the website"]


# --- caller-supplied POM survives goal mode -----------------------------------

def test_caller_pom_entries_are_folded_into_the_compiled_page_block():
    """core.py used to rebind the caller's pom_content to compile_goal's
    return value, dropping it without a word — so the documented way to pin a
    control the compiler cannot infer silently did nothing."""
    goal = {"scenario": "order status", "navigation": [URL],
            "actions": [{"do": "click", "id": "c1", "target": "order status"}],
            "checks": [{"see": "Please enter your email address"}]}
    ev = {"proven": {}, "proven_phase": {}, "blocking": [], "runtime_asserted": []}
    _, pom = goal_mod.compile_goal(
        goal, ev, "SHOP",
        extra_pom="order status:\n  css: 'a[data-id=\"order-status\"]'\n")
    pages = yaml.safe_load(pom)["pages"]
    entry = next(iter(pages.values()))
    assert entry["order status"]["css"] == 'a[data-id="order-status"]'


def test_caller_pom_rides_the_page_pin_not_a_filename_scope():
    """Folded INSIDE the @page block on purpose: the pin spans every URL the
    scenario visits, so a control clicked to REACH a page still resolves. A
    sibling pageobjects/<page>_pom.yaml is scoped to its own filename, which
    never covers the page the control actually sits on."""
    goal = {"scenario": "order status", "navigation": [URL],
            "actions": [{"do": "click", "id": "c1", "target": "order status"}],
            "checks": [{"see": "hello"}]}
    ev = {"proven": {}, "proven_phase": {}, "blocking": [], "runtime_asserted": []}
    _, pom = goal_mod.compile_goal(goal, ev, "SHOP",
                                   extra_pom="order status:\n  css: 'a.x'\n")
    assert "pages:" in pom and "match:" not in pom


@pytest.mark.parametrize("text", [None, "", "   ", "not: [valid", "- a\n- b"])
def test_unusable_caller_pom_is_ignored_rather_than_crashing(text):
    assert goal_mod._flat_pom_entries(text) == {}


def test_a_caller_authored_pages_block_is_left_alone():
    """They scoped it themselves; re-nesting it would change the ask."""
    assert goal_mod._flat_pom_entries("pages:\n  mine:\n    a: b\n") == {}


# --- the wordings the regression drill's own briefs use -----------------------

def test_search_phrased_target_first_is_still_a_search():
    """"In the search bar, type 'Vaccu'" — the enter verb wants
    "<value> into <target>", so the inverted order refused."""
    n = pe._parse_clause(pe._clauses(
        'In the search bar, type "Vaccu" — deliberately incomplete')[0])
    assert n["kind"] == "search" and n["term"] == "Vaccu"


def test_suggestion_named_before_the_word_suggestion_pairs_with_the_search():
    """"Click the vacuum-cleaner suggestion, worded exactly as the site
    renders it" used to compile a click on that whole descriptive phrase."""
    exp = pe.expand(f"1. go to {URL}\n"
                    '2. In the search bar, type "Vaccu"\n'
                    "3. Click the vacuum-cleaner suggestion, worded exactly "
                    "as the site renders it\n"
                    '4. verify the page shows "Some Vacuum"')
    act = exp["goal"]["actions"][0]
    assert act["do"] == "suggest"
    assert act["term"] == "Vaccu" and act["option"] == "vacuum-cleaner"


def test_results_page_narration_is_scene_setting_not_a_step():
    assert pe._parse_clause(pe._clauses(
        "The results page lists these products")[0])["kind"] == "observation"


def test_a_wrapped_directive_paragraph_does_not_refuse_on_its_tail():
    """The clause splitter works line by line, so "…finish with the Allure +
    RCA report links and" / "nothing else" arrived as two clauses; the head
    matched as scaffolding and the verbless tail refused on its own."""
    exp = pe.expand(f"1. go to {URL}\n2. verify the page shows \"Hello\"\n"
                    "Follow AGENTS.md — validate before running, finish with "
                    "the Allure + RCA report links and\nnothing else.")
    assert not exp.get("unresolved")


def test_an_outcome_worded_verify_is_never_silently_dropped():
    """Guard on a rule that was tried and REVERTED. Dropping a "Verify:" line
    that looks like a goal restatement also ate "order is placed successfully",
    a real assertion — a test that proves less than it claims is worse than a
    lap spent fixing the wording. If this fails, that rule came back."""
    exp = pe.expand("1. go to https://x.example\n"
                    "2. verify order is placed successfully, subtotal is "
                    "shown, items listed")
    assert len(exp["goal"]["checks"]) == 3


# --- the card's own Add button (the add-to-cart lap) --------------------------
#
# A retail results grid keeps its per-item buttons in `result_items[].actions[]`
# and labels them "Add" — the word "cart" appears nowhere near them. The prover
# read `blk["controls"]` only and required the destination word inside the
# control's own name, so it reported "no probed control mutates into 'cart'"
# while `probe --find add` was printing the button as [result-item-action].
# NOOD_0195 fixed this exact shape for captions; these pin it for controls.

def _card(caption, *actions):
    return {"caption": caption,
            "actions": [{"name": n, "selector": s, "kind": "button",
                         "visible": True} for n, s in actions]}


def _grid_block(*cards, controls=None):
    return {"controls": list(controls or []), "result_items": list(cards)}


def test_a_card_action_named_only_add_binds_as_the_mutation_control():
    blk = _grid_block(_card("STEM Lab Kit", ("Add", '[aria-label="Add"]')),
                      controls=[{"name": "cart", "selector": "[aria-label=Cart]",
                                 "kind": "link", "visible": True}])
    ctrl, why = goal_mod.mutation_control(
        goal_mod.block_mutation_candidates(blk), "cart")
    assert why is None and ctrl["name"] == "Add"
    assert ctrl["item_caption"] == "STEM Lab Kit"


def test_a_flat_add_to_cart_control_still_wins_over_a_card_action():
    """The card list is a FALLBACK, never a peer: a page that already resolved
    must bind exactly what it bound before."""
    blk = _grid_block(_card("STEM Lab Kit", ("Add", ".card-add")),
                      controls=[{"name": "Add to cart", "selector": "#atc",
                                 "kind": "button", "visible": True}])
    ctrl, _ = goal_mod.mutation_control(
        goal_mod.block_mutation_candidates(blk), "cart")
    assert ctrl["selector"] == "#atc"


@pytest.mark.parametrize("label", ["Remove", "Save for later", "Delete",
                                   "Add to wishlist", "Compare", "Entfernen"])
def test_a_destructive_or_deferral_card_action_never_binds_as_an_add(label):
    """_MUTATING_RE matches remove/delete/save, so reusing it here would have
    bound the one button that must never stand in for an add."""
    blk = _grid_block(_card("STEM Lab Kit", (label, ".x")))
    ctrl, why = goal_mod.mutation_control(
        goal_mod.block_mutation_candidates(blk), "cart")
    assert ctrl is None and "mutates into" in why


def test_the_blocker_hint_names_the_cards_actions_not_the_landing_chrome():
    """The searchless hint enumerated blk["controls"] in block order, so a
    results-page add_to advertised the landing page's carousel ("play",
    "pause") as what the page offers — vocabulary for a rename that could
    never work."""
    grid = _grid_block(_card("STEM Lab Kit", ("Save for later", ".s")))
    names = [c["name"] for c in goal_mod.block_mutation_candidates(grid)]
    assert names == ["Save for later"]


# --- the PDP detour ----------------------------------------------------------
#
# "search for a toy / add to cart" gets an implied pick (NOOD_0168). That pick
# CLICKS the product, so the prover then looked for the add button on the
# product page — the one page it cannot be on when the grid owns it. The card
# the probe happened to click carried no Add at all (in-store-only stock), so
# the flow blocked on a page it never needed to visit.

def _detour_probe(picked_caption="ZURU Water Blaster"):
    return {"pages": [{
        "url": "https://shop.example.com/en.html", "title": "t",
        "controls": [{"name": "cart", "selector": '[aria-label="Cart"]',
                      "kind": "link", "visible": True}],
        "headings": [], "pom_yaml": "", "permission_prompts": [],
        "popups_closed": 0,
        "search": {
            "term": "toy", "headings": [], "controls": [],
            "result_items": [
                _card(picked_caption),                       # no add action
                _card("STEM Lab Kit", ("Add", '[aria-label="Add"]'))],
            "picked": {"controls": [], "headings": [picked_caption],
                       "pom_yaml": "", "result_items": [],
                       "picked_caption": picked_caption,
                       "picked_selector": "#tile1"}}}],
        "errors": []}


def _detour_goal(pick_target=None):
    pick = {"do": "pick", "id": "p"}
    if pick_target:
        pick["target"] = pick_target
    return {"scenario": "add a toy to the cart",
            "actions": [{"do": "search", "term": "toy", "id": "s"}, pick,
                        {"do": "add_to", "destination": "cart",
                         "item_from": "p", "id": "a"}],
            "checks": [{"item_in_destination": "cart", "expected_from": "p",
                        "evidence": "screenshot"}]}


def test_an_untargeted_pick_rebinds_to_the_card_that_can_actually_be_added():
    ev = goal_mod.evidence(_detour_goal(), _detour_probe())
    assert ev["blocking"] == []
    assert ev["mutation_plans"]["a"]["on_results"] is True
    # the assertion must name the product that was actually added
    assert ev["bound_targets"]["p"]["caption"] == "STEM Lab Kit"


def test_the_products_own_page_is_never_opened_when_the_grid_owns_the_add():
    goal = _detour_goal()
    ev = goal_mod.evidence(goal, _detour_probe())
    feat, _ = goal_mod.compile_goal(goal, ev, "SHOP")
    assert 'User clicks "ZURU Water Blaster"' not in feat
    assert 'User clicks "Add" in the row containing "STEM Lab Kit"' in feat


def test_a_pick_that_names_its_target_never_swaps_in_another_product():
    """Re-binding is only safe because an untargeted pick means "any matching
    result". Name one, and adding a different product would answer a question
    the author didn't ask — that still blocks."""
    ev = goal_mod.evidence(_detour_goal(pick_target="ZURU Water Blaster"),
                           _detour_probe())
    assert any("no proven mutation path" in b for b in ev["blocking"])


# --- the card climb ----------------------------------------------------------

def test_the_card_climb_sees_a_button_whose_only_label_is_aria_label():
    """NOOD_0207 taught `within:` to climb from a caption to the card, but the
    climb's shape probe runs with allow_dom_scan=False, and an aria-label-only
    match lives behind that scan. Retail grids label the Add control exactly
    that way, so every level missed it and the card grid — the shape NOOD_0207
    exists for — reported "No row containing '<caption>'"."""
    from noodle.agents.web import actions as act

    class _Loc:
        def __init__(self, n): self._n = n
        def count(self): return self._n
        @property
        def first(self): return self

    class _Scope:
        def __init__(self, label=0, role=0): self._l, self._r = label, role
        def get_by_label(self, name, exact=True): return _Loc(self._l)
        def get_by_role(self, role, name=None, exact=True): return _Loc(self._r)

    assert act._accessible_hit(_Scope(label=1), "Add")      # aria-label only
    assert act._accessible_hit(_Scope(role=1), "Add")       # accessible name
    assert not act._accessible_hit(_Scope(), "Add")         # genuinely absent


def test_the_climb_probe_survives_a_locator_that_raises():
    from noodle.agents.web import actions as act

    class _Boom:
        def get_by_label(self, *a, **k): raise RuntimeError("detached")
        def get_by_role(self, *a, **k): raise RuntimeError("detached")

    assert act._accessible_hit(_Boom(), "Add") is False


# --- same name, same destination --------------------------------------------
#
# A responsive header renders its nav twice (desktop bar + collapsed menu).
# Both instances carry the same label and link to the same page, but their
# selectors differ, so the ambiguity gate called them "structurally different"
# and refused — advising "name the instance by nearby unique text", which has
# no answer when both sit in chrome with no nearby text of their own.

def _link(name, selector, href, visible=True):
    return {"name": name, "selector": selector, "href": href, "kind": "link",
            "visible": visible, "needs_pom": False, "step": "x"}


def _page(*controls):
    return [({"url": "https://shop.example.com/", "controls": list(controls),
              "headings": [], "result_items": []}, "initial", None)]


def test_two_chrome_links_to_the_same_destination_resolve_instead_of_blocking():
    blocks = _page(_link("order status", 'a[class~="nl-order-status"]', "/orders"),
                   _link("order status", "text=Order Status", "/orders"))
    ctrl, _phase, _trig, note = goal_mod._locate("order status", blocks)
    assert note is None and ctrl is not None
    assert ctrl["collapsed_from"] == 2


def test_same_named_links_to_DIFFERENT_destinations_still_block():
    """The collapse is proof, not a tie-break: two links that genuinely go to
    different pages are a real ambiguity and must keep refusing."""
    blocks = _page(_link("order status", "#a", "/orders"),
                   _link("order status", "#b", "/help/orders"))
    ctrl, _p, _t, note = goal_mod._locate("order status", blocks)
    assert ctrl is None and "structurally different" in note


def test_the_collapse_needs_a_real_href_not_a_missing_one():
    """Two links whose href the probe never captured cannot be shown to share
    a destination — absent evidence is not matching evidence."""
    blocks = _page(_link("order status", "#a", None),
                   _link("order status", "#b", None))
    ctrl, _p, _t, note = goal_mod._locate("order status", blocks)
    assert ctrl is None and note


def test_the_probe_carries_a_links_href_onto_the_control():
    """The href was scraped (the selector builder reads it) but dropped from
    the emitted control, so NOOD_0208's navigation-shaped demotion and the
    same-destination collapse both read None on every link."""
    from noodle.agents.web import probe as pb
    out = pb.summarize({"controls": [
        {"tag": "a", "href": "/en/check-order-status.html", "role": "link",
         "text": "Order Status", "visible": True},
        {"tag": "button", "role": "button", "text": "Go", "visible": True}]},
        url="https://shop.example.com/")
    link = next(c for c in out["controls"] if c["name"] == "order status")
    assert link["href"] == "/en/check-order-status.html"
    # a non-link never grows the key
    assert "href" not in next(c for c in out["controls"] if c["name"] == "go")

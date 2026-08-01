"""NOOD_0209 — authoring lap waste: a blocked lap writes nothing, and the
lap-causing matchers are fixed.

One reviewed authoring request burned 9 laps and left 4 feature files and
2 POM files where one of each was wanted. The engine makes zero model calls,
so every lap re-pays the calling agent's whole transcript: fewer laps is the
ONLY cost lever. The defects pinned here, all one root cause — user text
matched against page text by raw substring containment, at two stages, with
no normalisation at either:

D1 a brief's metadata line was discarded whole, taking the only URL with it
D2 "Go to UI" compiled to click "UI"
D3 a blocked goal lap still wrote its artifacts — the file multiplier
D8 action targets kept their quote DELIMITERS ('click the "Sign in" button')
D9 the substring matcher had no length or word-boundary guard, so "edit"
   matched "credit" and a control named "e" matched nearly everything
D10 `pick` existed in the goal schema but NO prompt phrasing reached it, and
    the rewrite advice replaced the ACTION with an assertion

Every fixture is a generic shape (a demo storefront, a reference site) —
no app, product or brand behaviour is pinned. No browser, no LLM, no network.
"""
import json

import pytest

from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe


def _ctrl(name, selector="#x", **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _page(controls, headings=()):
    return {"url": "https://x/", "title": "t", "controls": controls,
            "headings": list(headings), "pom_yaml": "",
            "permission_prompts": [], "popups_closed": 0}


def _actions(exp, *skip):
    return [a for a in exp["goal"]["actions"] if a["do"] not in skip]


# --- D1: the metadata line's URL is harvested, never discarded ---------------

def test_a_metadata_line_url_is_spent_as_the_base_url():
    exp = pe.expand("App: Demo · UI `https://demo.example.org`\n"
                    "1. Go to UI\n2. Search for \"kettles\"")
    assert exp["ok"], exp.get("error")
    assert exp["base_url"] == "https://demo.example.org"
    assert any("metadata line" in a for a in exp["assumptions"])


def test_a_prompt_with_no_url_anywhere_still_refuses():
    exp = pe.expand("App: Demo\n1. Go to UI\n2. Search for \"kettles\"")
    assert not exp["ok"]
    assert "no URL" in json.dumps(exp)


# --- D2: "Go to UI" is contextual navigation, not a click --------------------

@pytest.mark.parametrize("ref", ["UI", "the ui", "the front end", "web app"])
def test_go_to_a_surface_reference_is_never_a_click(ref):
    exp = pe.expand(f"1. Go to {ref}\n2. Search for \"kettles\"",
                    base_url="https://x/")
    assert exp["ok"], exp.get("error")
    assert not [a for a in exp["goal"]["actions"] if a["do"] == "click"]


# --- D3: a blocked goal lap writes NOTHING (the file multiplier) -------------

def _probe(controls):
    return {"pages": [_page(controls)], "errors": []}


def test_a_blocked_goal_lap_rolls_back_its_feature_and_pom(tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: _probe([_ctrl("Search")]))
    res = core.author_test(
        app_name="shop", base_url="https://x/",
        goal={"scenario": "blocked lap",
              "actions": [{"do": "click", "target": "No Such Control"}],
              "checks": [{"see": "whatever"}]},
        workspace=str(tmp_path))
    assert res["ready"] is False and res["blocking"]
    assert res["feature"] is None and res["pom"] is None
    litter = [p for p in tmp_path.rglob("*")
              if p.is_file() and p.suffix in (".feature", ".yaml")
              and ("features" in p.parts or "pageobjects" in p.parts)]
    assert litter == []
    assert any("nothing written" in w for w in res["warnings"])
    # the compiled text still rides the payload for inspection
    assert res["compiled"]["feature"]


def test_a_blocked_lap_keeps_env_and_secrets_and_a_green_retry_lands(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: _probe([_ctrl("Search")]))
    kw = dict(app_name="shop", base_url="https://x/", workspace=str(tmp_path))
    core.author_test(goal={"scenario": "blocked lap",
                           "actions": [{"do": "click", "target": "Nope"}],
                           "checks": [{"see": "x"}]}, **kw)
    envs = list(tmp_path.rglob("*_environments.yaml"))
    assert envs, "env is app-level and idempotent — never rolled back"
    res = core.author_test(goal={"scenario": "green lap",
                                 "actions": [{"do": "click",
                                              "target": "Search"}],
                                 "checks": [{"see": "Search"}]}, **kw)
    assert res["ready"] is True and res["feature"]


def test_a_blocked_overwrite_restores_the_previous_green_feature(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: _probe([_ctrl("Search")]))
    kw = dict(app_name="shop", base_url="https://x/", workspace=str(tmp_path),
              feature_path="noodle_tests/shop/features/flow.feature")
    good = core.author_test(goal={"scenario": "green lap",
                                  "actions": [{"do": "click",
                                               "target": "Search"}],
                                  "checks": [{"see": "Search"}]}, **kw)
    feat = tmp_path / good["feature"]
    original = feat.read_bytes()
    blocked = core.author_test(goal={"scenario": "green lap",
                                     "actions": [{"do": "click",
                                                  "target": "Nope"}],
                                     "checks": [{"see": "x"}]},
                               overwrite=True, **kw)
    assert blocked["ready"] is False
    assert feat.read_bytes() == original


def test_manual_feature_content_keeps_write_always(tmp_path):
    # the caller owns hand-written Gherkin — a block still fixes in place
    res = core.author_test(
        app_name="shop", base_url="https://x/",
        feature_path="noodle_tests/shop/features/manual.feature",
        feature_content=("Feature: manual\n  Scenario: s\n"
                         "    Given completely unmatched step text here\n"),
        workspace=str(tmp_path))
    assert res["ready"] is False
    assert res["feature"] and (tmp_path / res["feature"]).is_file()


# --- NOOD_0209: multi-part quoted assertions become real checks --------------

@pytest.mark.parametrize("step,want", [
    ('verify "Order Placed!", "$9.99" and "Item A"',
     [{"see": "Order Placed!"}, {"see": "$9.99"}, {"see": "Item A"}]),
    ('verify "A" and "B"', [{"see": "A"}, {"see": "B"}]),
    ('verify "Demo Shop" logo is present', [{"see": "Demo Shop"}]),
    ('Verify the "Demo Shop" logo is present', [{"see": "Demo Shop"}]),
    ('Verify the article shows "A", "B" and "C"',
     [{"see": "A"}, {"see": "B"}, {"see": "C"}]),
])
def test_quoted_conjunctions_split_into_one_check_per_member(step, want):
    exp = pe.expand(f"1. go to https://x.org\n2. {step}")
    assert exp["ok"], exp.get("error")
    got = [{k: v for k, v in c.items() if k in ("see",)}
           for c in exp["goal"]["checks"]]
    assert got == want


@pytest.mark.parametrize("step,want", [
    ('verify "A" or "B"', [{"any_of": ["A", "B"]}]),          # OR stays ONE
    ('verify "Hello, world"', [{"see": "Hello, world"}]),     # one literal
    ('verify "Add to cart" button is disabled',               # state, whole
     [{"see": '"Add to cart" button is disabled'}]),
])
def test_conservative_splitting_tripwires_hold(step, want):
    exp = pe.expand(f"1. go to https://x.org\n2. {step}")
    assert exp["ok"], exp.get("error")
    got = [{k: v for k, v in c.items() if k in ("see", "any_of")}
           for c in exp["goal"]["checks"]]
    assert got == want


def test_a_quoted_negation_never_inverts_into_a_presence_check():
    exp = pe.expand('1. go to https://x.org\n2. verify "Error" is not visible')
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["checks"] == [{"not_see": "Error"}]


def test_an_unquoted_compound_verify_still_splits_on_commas():
    exp = pe.expand("1. go to https://x.org\n"
                    "2. verify order is placed successfully, subtotal is "
                    "shown, items listed")
    assert exp["ok"], exp.get("error")
    assert len(exp["goal"]["checks"]) == 3


# --- D8: action targets shed their quote delimiters --------------------------

@pytest.mark.parametrize("step,do,target", [
    ('Click the "Sign in" button', "click", "Sign in"),
    ('Click "Sign in"', "click", "Sign in"),
    ('enter "x" into the "Email" field', "enter", "Email"),
    ('hover over the "Deals" menu', "hover", "Deals"),
    ('go to the "Pricing" page', "click", "Pricing"),
])
def test_a_quoted_action_target_reduces_to_its_content(step, do, target):
    exp = pe.expand(f"1. go to https://x.org\n2. {step}")
    assert exp["ok"], exp.get("error")
    acts = [a for a in exp["goal"]["actions"] if a["do"] == do]
    assert acts and acts[0]["target"] == target


def test_a_multi_member_or_prose_target_is_left_whole():
    # not "exactly one quoted member + control noun" — falls through to the
    # pre-NOOD_0209 cleaning untouched (edge-quote strip, content preserved)
    exp = pe.expand('1. go to https://x.org\n2. Click "A" next to "B"')
    assert exp["ok"], exp.get("error")
    clicks = [a for a in exp["goal"]["actions"] if a["do"] == "click"]
    assert clicks[0]["target"] == 'A" next to "B'


# --- D9: the substring matcher is guarded ------------------------------------

def _blocks(controls):
    return goal_mod._page_blocks(_page(controls))


_NOISY = [_ctrl("edit interlanguage links", "#a"), _ctrl("edit source", "#b"),
          _ctrl("accredited partners directory", "#c"),
          _ctrl("d", "#d"), _ctrl("e", "#e")]


def test_a_substring_match_needs_a_word_boundary_and_three_chars():
    c, _, _, note = goal_mod._locate("edit", _blocks(_NOISY))
    assert c is None and note is not None
    assert "accredited" not in note and '"d"' not in note and '"e"' not in note
    assert "edit interlanguage links" in note and "edit source" in note


def test_the_reverse_direction_still_matches_on_a_boundary():
    c, _, _, note = goal_mod._locate("cart", _blocks([_ctrl("add to cart")]))
    assert note is None and c is not None and c["name"] == "add to cart"


def test_an_exact_short_name_still_matches_exactly():
    c, _, _, note = goal_mod._locate("e", _blocks(_NOISY))
    assert note is None and c is not None and c["name"] == "e"


def test_ambiguity_candidates_are_ranked_closest_first():
    blocks = _blocks([_ctrl("edit interlanguage links", "#a"),
                      _ctrl("edit this page here now", "#b"),
                      _ctrl("edit source", "#c")])
    # the blocker must LEAD with the likeliest candidate, not an
    # arbitrary cut of the match list
    _, _, _, note = goal_mod._locate("edit source", blocks)
    if note is None:
        _, _, _, note = goal_mod._locate("edit", blocks)
    assert note is not None
    assert note.index('"edit source"') \
        < note.index('"edit interlanguage links"')


def test_near_miss_shortlist_is_ranked_by_closeness():
    msg = goal_mod._near_miss("edit", _blocks(_NOISY))
    first = msg.split('"')[1::2][0] if '"' in msg else ""
    assert first.startswith("edit")


# --- D10: pick is reachable from prompt mode ---------------------------------

@pytest.mark.parametrize("step", [
    'Pick the "Steel Kettle" result',
    'Select the "Steel Kettle" result',
    'Pick "Steel Kettle" from the results',
    'Open the "Steel Kettle" article',
])
def test_a_named_result_selection_compiles_to_a_pick(step):
    exp = pe.expand(f'1. go to https://x.org\n2. search for "kettle"\n'
                    f"3. {step}")
    assert exp["ok"], exp.get("error")
    picks = [a for a in exp["goal"]["actions"] if a["do"] == "pick"]
    assert picks == [{"do": "pick", "id": "pick1", "from": "search1",
                      "target": "Steel Kettle"}]


@pytest.mark.parametrize("step", [
    "Open the first result", "Click the first search result",
])
def test_an_ordinal_result_selection_picks_the_first_actionable(step):
    exp = pe.expand(f'1. go to https://x.org\n2. search for "kettle"\n'
                    f"3. {step}")
    assert exp["ok"], exp.get("error")
    picks = [a for a in exp["goal"]["actions"] if a["do"] == "pick"]
    assert picks == [{"do": "pick", "id": "pick1", "from": "search1",
                      "strategy": "first_actionable"}]


def test_a_named_selection_without_a_search_degrades_to_a_click():
    exp = pe.expand('1. go to https://x.org\n2. Open the "Pricing" article')
    assert exp["ok"], exp.get("error")
    assert _actions(exp) == [{"do": "click", "target": "Pricing"}]


def test_an_ordinal_selection_without_a_search_is_refused_by_name():
    exp = pe.expand("1. go to https://x.org\n2. Open the first result")
    assert not exp["ok"]
    assert "needs a search step" in json.dumps(exp["conflicts"])


def test_a_pick_still_feeds_a_later_add_to():
    exp = pe.expand('1. go to https://x.org\n2. search for "kettle"\n'
                    '3. Open the "Steel Kettle" result\n4. add it to cart')
    assert exp["ok"], exp.get("error")
    dos = [a["do"] for a in exp["goal"]["actions"]]
    assert dos == ["search", "pick", "add_to"]


def test_selection_shaped_rewrite_advice_offers_the_action_not_a_verify():
    exp = pe.expand("1. go to https://x.org\n2. search for \"cats\"\n"
                    "3. Choose result number two")
    assert not exp["ok"]
    suggested = json.dumps(exp["unresolved"])
    assert "pick the" in suggested
    assert "verify at least" not in suggested


def test_a_plain_contact_link_click_is_never_a_pick():
    exp = pe.expand('1. go to https://x.org\n2. search for "cats"\n'
                    '3. Click the "Contact" link')
    assert exp["ok"], exp.get("error")
    assert [a["do"] for a in exp["goal"]["actions"]] == ["search", "click"]


# --- D7: a navigation URL equal to the base reuses the app's own key ---------

def test_a_navigation_url_equal_to_the_base_reuses_the_app_key():
    goal = {"scenario": "s", "navigation": ["https://x.example"],
            "actions": [], "checks": []}
    nav = goal_mod.navigation_env(goal, "shop", base_url="https://x.example/")
    assert nav == [("SHOP", "https://x.example")]


def test_a_navigation_url_on_another_path_still_mints_its_own_key():
    goal = {"scenario": "s", "navigation": ["https://x.example/menu"],
            "actions": [], "checks": []}
    nav = goal_mod.navigation_env(goal, "shop", base_url="https://x.example")
    assert nav == [("SHOP_MENU", "https://x.example/menu")]


# --- D5: scoping advice tells per-card families from page-level twins --------

def test_a_per_card_family_still_gets_the_within_advice():
    blocks = _blocks([
        _ctrl("add to basket", ".card:nth-child(1) button"),
        _ctrl("add to basket", ".card:nth-child(2) button"),
    ])
    _, _, _, note = goal_mod._locate("add to basket", blocks)
    assert note is not None and "within:" in note


def test_structurally_different_twins_are_never_advised_a_row_anchor():
    # a page-level control and per-section ones sharing a label — a `within:`
    # anchor authored ready:true and died at run time ("No row containing …")
    blocks = _blocks([
        _ctrl("edit", "#page-edit-tab"),
        _ctrl("edit", "section.intro a.edit-link"),
    ])
    _, _, _, note = goal_mod._locate("edit", blocks)
    assert note is not None
    assert "structurally different" in note
    assert 'add within:' not in note


# --- D6: a blocked target is never best-effort bound into the POM ------------

def test_a_blocked_target_keeps_author_wording_and_mints_no_pom_entry():
    # the guarded matcher refuses "name" vs a control named "n" (1 char), but
    # the compile fallback (_find_control, unguarded) would still bind it —
    # the observed artifact tied an enter target to a quantity stepper
    goal = {"scenario": "fill the form",
            "actions": [{"do": "enter", "target": "name", "value": "x"}],
            "checks": [{"see": "whatever"}]}
    ev = goal_mod.evidence(goal, _probe([_ctrl("n", "#stepper")]))
    assert any(b.startswith('enter "name"') for b in ev["blocking"])
    feature, pom = goal_mod.compile_goal(goal, ev, "APP")
    assert '"name"' in feature            # the author's own wording
    assert "#stepper" not in (pom or "")  # no guessed binding


# --- D4: the scenario title truncates at a label boundary, quotes balanced ---

def test_a_long_scenario_truncates_at_a_label_boundary_with_balanced_quotes():
    exp = pe.expand(
        "1. go to https://x.org\n"
        '2. click "Order The Special Deal Of The Day Right Now"\n'
        '3. click "Add Extra Toppings And A Very Large Drink"\n'
        '4. click "Proceed All The Way To The Final Checkout"')
    assert exp["ok"], exp.get("error")
    s = exp["goal"]["scenario"]
    assert len(s) <= 80
    assert s.count("'") % 2 == 0          # never cut mid-quote
    assert not s.endswith(",")

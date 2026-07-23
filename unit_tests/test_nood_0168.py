"""NOOD_0168 — expand the simple prompt: engine gaps found by re-running the
"search a toy, add it to cart, verify the cart" session against a live retail
SPA. Five universal (domain-free) fixes, each pinned browser-free here:

  1. mutation_control — a FEW same-named visible instances are responsive
     duplicates of ONE control (buy box + sticky bar): first visible binds;
     MANY distinct instances stay a block (one-per-card grid).
  2. build_result_items — landmark chrome (nav/header/footer/breadcrumb,
     flagged by the collector) is never a result item, however card-shaped
     the strip is.
  3. normalize — the simple-prompt goal shape expands instead of walling
     off: search → add_to with no pick inserts the implied pick; an
     item_in_destination check without expected_from wires to the sole pick.
  4. search() — submitting is not searching: the step passes only once the
     page observably reacts (URL change, new term echo, body-text delta).
  5. wait_networkidle — a settle WAIT is best-effort: ad-heavy pages are
     never strictly idle, so a timeout proceeds instead of failing a green
     flow.  pom.relaxed_locator — [attr="value"] rots when the app suffixes
     live state into a label ('Cart' → 'Cart, 1 item'); the prefix-anchored
     form substitutes deterministically, recorded as healing.
"""
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from noodle import healing
from noodle.agents.web import actions, locator
from noodle.agents.web import pom as pom_mod
from noodle.agents.web import probe as probe_mod
from noodle.repl import goal as goal_mod


def _ctrl(name, selector, kind="button", **extra):
    c = {"name": name, "selector": selector, "kind": kind,
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


# --- 1. mutation_control: responsive duplicates vs per-card grids ------------

def test_mutation_duplicates_bind_first_visible():
    ok, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#add-to-cart"),
         _ctrl("Add to cart", "#add-to-cart-sticky-buy-bar")], "cart")
    assert why is None and ok["selector"] == "#add-to-cart"


def test_mutation_duplicates_prefer_the_visible_instance():
    ok, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#hidden", visible=False),
         _ctrl("Add to cart", "#shown")], "cart")
    assert why is None and ok["selector"] == "#shown"


def test_mutation_grid_of_instances_still_blocks():
    none, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", f"#card-{i}") for i in range(5)], "cart")
    assert none is None and "scope" in why


def test_mutation_all_hidden_duplicates_still_block():
    none, why = goal_mod.mutation_control(
        [_ctrl("Add to cart", "#a", visible=False),
         _ctrl("Add to cart", "#b", visible=False)], "cart")
    assert none is None


# --- 2. build_result_items: landmark chrome is never a result ----------------

def _link(name, href, cls="tile", chrome=False):
    return {"tag": "a", "name": name, "text": name, "href": href, "cls": cls,
            "visible": True, "chrome": chrome, "id": "", "testid": "",
            "aria": "", "title": "", "ph": "", "alt": "", "label": ""}


def test_chrome_flagged_links_never_become_result_items():
    raw = [_link("Shop Home page", "/en.html", cls="tile",
                 chrome=True),
           _link("Water Blaster Set", "/pdp/blaster.html"),
           _link("Learning Toy Book", "/pdp/book.html")]
    items = probe_mod.build_result_items(raw)
    caps = [i["caption"] for i in items]
    assert "Shop Home page" not in caps
    assert caps == ["Water Blaster Set", "Learning Toy Book"]


def test_unflagged_links_unchanged_by_chrome_field_absence():
    raw = [_link("Water Blaster Set", "/pdp/blaster.html"),
           _link("Learning Toy Book", "/pdp/book.html")]
    assert len(probe_mod.build_result_items(raw)) == 2


# --- 3. normalize: the simple-prompt goal shape expands ----------------------

def _simple_goal(**over):
    g = {"scenario": "Search for a toy and add it to cart",
         "actions": [{"do": "search", "id": "s", "term": "toy"},
                     {"do": "add_to", "id": "a", "destination": "cart"}],
         "checks": [{"item_in_destination": "cart", "after": "a"}]}
    g.update(over)
    return g


def test_normalize_inserts_the_implied_pick():
    g, notes = goal_mod.normalize(_simple_goal())
    dos = [a["do"] for a in g["actions"]]
    assert dos == ["search", "pick", "add_to"]
    pick = g["actions"][1]
    assert g["actions"][2]["item_from"] == pick["id"]
    assert any("implied pick" in n for n in notes)


def test_normalize_wires_expected_from_to_the_sole_pick():
    g, notes = goal_mod.normalize(_simple_goal())
    assert g["checks"][0]["expected_from"] == g["actions"][1]["id"]
    assert any("expected_from" in n for n in notes)


def test_normalize_leaves_an_explicit_pick_alone():
    explicit = _simple_goal(actions=[
        {"do": "search", "id": "s", "term": "toy"},
        {"do": "pick", "id": "p"},
        {"do": "add_to", "id": "a", "item_from": "p",
         "destination": "cart"}])
    g, _ = goal_mod.normalize(explicit)
    assert g["actions"] == explicit["actions"]


def test_normalize_never_invents_a_pick_without_a_search():
    g, _ = goal_mod.normalize(_simple_goal(actions=[
        {"do": "add_to", "id": "a", "destination": "cart"}]))
    assert [a["do"] for a in g["actions"]] == ["add_to"]


def test_normalize_does_not_mutate_the_caller_goal():
    goal = _simple_goal()
    goal_mod.normalize(goal)
    assert "item_from" not in goal["actions"][1]
    assert "expected_from" not in goal["checks"][0]


# --- 4. search(): submitting is not searching --------------------------------

class _SearchPage:
    """String url + string body text — the observable surface search() checks."""

    def __init__(self, url="https://x/", body="home page"):
        self.url, self._body = url, body
        self.box = MagicMock()

    def evaluate(self, script, *a):
        return self._body


def _searchable(monkeypatch, page):
    monkeypatch.setenv("NOODLE_SETTLE_TIMEOUT", "400")
    monkeypatch.setattr(actions, "find_first",
                        lambda p, c, scope=None, prefer=None: page.box)


def test_search_passes_on_url_change(monkeypatch):
    page = _SearchPage()
    _searchable(monkeypatch, page)
    page.box.press.side_effect = \
        lambda *_: setattr(page, "url", "https://x/search?q=toy")
    actions.search(page, "toy")
    page.box.fill.assert_called_once_with("toy")


def test_search_passes_on_new_term_echo(monkeypatch):
    page = _SearchPage()
    _searchable(monkeypatch, page)
    page.box.press.side_effect = \
        lambda *_: setattr(page, "_body", 'results for "toy"')
    actions.search(page, "toy")


def test_search_fails_when_nothing_reacts(monkeypatch):
    page = _SearchPage()
    _searchable(monkeypatch, page)
    with pytest.raises(AssertionError) as e:
        actions.search(page, "toy")
    assert "never reacted" in str(e.value)
    assert "NOODLE_SETTLE_TIMEOUT" in str(e.value)


def test_search_skips_postcondition_on_unobservable_page(monkeypatch):
    box = MagicMock()
    monkeypatch.setattr(actions, "find_first",
                        lambda p, c, scope=None, prefer=None: box)
    actions.search(MagicMock(), "toy")     # MagicMock url/evaluate → skip
    box.press.assert_called_once_with("Enter")


# --- 5. best-effort settle + POM attribute-prefix relaxation -----------------

def test_wait_networkidle_swallows_the_timeout(monkeypatch):
    monkeypatch.setenv("NOODLE_SETTLE_TIMEOUT", "1500")
    page = MagicMock()
    page.wait_for_load_state.side_effect = PlaywrightTimeoutError("busy")
    actions.wait_networkidle(page)         # must not raise
    page.wait_for_load_state.assert_called_once_with(
        "networkidle", timeout=1500)


def test_relaxed_locator_prefixes_attr_equals(monkeypatch):
    monkeypatch.setattr(pom_mod, "_lookup",
                        lambda text, url: '[aria-label="Cart"]')
    monkeypatch.setattr(pom_mod, "_build_locator",
                        lambda page, entry, text: entry)
    page = MagicMock()
    page.url = "https://x/"
    assert pom_mod.relaxed_locator(page, "cart") == '[aria-label^="Cart"]'


def test_relaxed_locator_handles_dict_entries(monkeypatch):
    monkeypatch.setattr(pom_mod, "_lookup",
                        lambda text, url: {"css": '[title="Cart"]',
                                           "frame": "checkout"})
    monkeypatch.setattr(pom_mod, "_build_locator",
                        lambda page, entry, text: entry)
    page = MagicMock()
    page.url = "https://x/"
    assert pom_mod.relaxed_locator(page, "cart") == {
        "css": '[title^="Cart"]', "frame": "checkout"}


def test_relaxed_locator_none_when_nothing_to_relax(monkeypatch):
    monkeypatch.setattr(pom_mod, "_lookup", lambda text, url: "#cart-icon")
    page = MagicMock()
    page.url = "https://x/"
    assert pom_mod.relaxed_locator(page, "cart") is None


def test_pom_zero_match_substitutes_prefix_form(monkeypatch):
    """The live-session failure: [aria-label="Cart"] matched 0 after the app
    relabelled the icon 'Cart, 1 item' — the prefix form resolves, recorded
    as healing; the loud failure stays for a genuinely missing element."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "300")
    monkeypatch.delenv("NOODLE_HEAL_POM_KEYS", raising=False)
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    raw = MagicMock()
    raw.count.return_value = 0
    relaxed = MagicMock()
    relaxed.count.return_value = 1
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator.pom, "raw_locator", lambda page, text: raw)
    monkeypatch.setattr(locator.pom, "relaxed_locator",
                        lambda page, text: relaxed)
    healing.reset()
    page = MagicMock()
    page.url = "https://x/"
    assert locator.find(page, "cart") is relaxed.first
    assert any(e["strategy"] == "pom-attr-prefix"
               for e in healing.events_since(0))


def test_pom_zero_match_still_fails_loudly_without_relaxation(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "300")
    monkeypatch.delenv("NOODLE_HEAL_POM_KEYS", raising=False)
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    raw = MagicMock()
    raw.count.return_value = 0
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator.pom, "raw_locator", lambda page, text: raw)
    monkeypatch.setattr(locator.pom, "relaxed_locator",
                        lambda page, text: None)
    page = MagicMock()
    page.url = "https://x/"
    with pytest.raises(AssertionError) as e:
        locator.find(page, "cart")
    assert "Not substituting" in str(e.value)

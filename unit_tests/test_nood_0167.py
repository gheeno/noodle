"""NOOD_0167 — quote the app's own response instead of dead-ending.

A reviewed retail session went red on an empty-destination assertion three
steps after the page announced the real cause ("Out of stock at <store>")
in a role=alert toast nothing ever read, and its authoring phase dead-ended
twice on evidence that was correct but mute: inspect said "no source
matches" on a page whose tiles carried a differently-named control, and the
goal blocker said "no proven mutation path" without naming what the landed
page offers. Three generic fixes, no domain matching anywhere:

  1. actions.py captures NEW page announcements after a click (ARIA
     alert/alertdialog/status roles, live regions, toast/snackbar class
     conventions) and hooks.py stamps them on the failing step, where a new
     RCA verdict (app-rejected-action) quotes them.
  2. goal.py's add_to blocker appends the landed page's control vocabulary.
  3. inspect's zero-candidate render lists the page's interactive controls.
"""
from noodle.agents.web import actions
from noodle.agents.web import inspect_locator as il
from noodle.repl import goal as goal_mod
from noodle.reporting import rca_report as rr
from unit_tests.test_nood_0156 import _ctrl, _nav_goal, _nav_probe


class _FakePage:
    """page-shaped double: evaluate() returns the current announcement set."""

    def __init__(self, url="https://app.example/pdp", announce=None):
        self.url = url
        self.announce = announce if announce is not None else []
        self.broken = False

    def evaluate(self, js):
        if self.broken:
            raise RuntimeError("execution context destroyed")
        return list(self.announce)


def _entry(message="", trace="", warnings=None):
    return {"message": message, "trace": trace, "warnings": warnings or []}


# --- announcement capture (actions.py) ---------------------------------------

def test_new_announcement_recorded_as_click_response():
    page = _FakePage(announce=["We use cookies", "Out of stock at Springfield"])
    actions._harvest_announcement(page, "Add to cart",
                                  {"announce": ["We use cookies"]})
    assert page._noodle_response == ("Add to cart",
                                     "Out of stock at Springfield")
    note = actions.page_response(page)
    assert note == ("[page-response] after clicking 'Add to cart' the page "
                    'announced: "Out of stock at Springfield"')


def test_baseline_announcements_never_reported():
    # A live region present BEFORE the click (cookie bar, persistent status)
    # is not a response to it.
    page = _FakePage(announce=["We use cookies"])
    actions._harvest_announcement(page, "Add to cart",
                                  {"announce": ["We use cookies"]})
    assert getattr(page, "_noodle_response", None) is None
    assert actions.page_response(page) is None


def test_late_announcement_harvested_before_next_click():
    # The toast rides a network round trip: nothing at click time, present
    # when the next harvest looks (argless call, stored baseline).
    page = _FakePage(announce=[])
    actions._harvest_announcement(page, "Add to cart", {"announce": []})
    assert getattr(page, "_noodle_response", None) is None
    page.announce = ["Out of stock at Springfield"]
    actions._harvest_announcement(page)
    assert page._noodle_response == ("Add to cart",
                                     "Out of stock at Springfield")


def test_navigation_invalidates_the_baseline():
    # After the page moved, the old baseline cannot tell a response from the
    # NEW page's own live regions — the recorded response survives, no new
    # harvest happens.
    page = _FakePage(announce=[])
    actions._harvest_announcement(page, "Add to cart", {"announce": []})
    page.url = "https://app.example/cart"
    page.announce = ["2 items in cart"]
    actions._harvest_announcement(page)
    assert getattr(page, "_noodle_response", None) is None


def test_harvest_never_raises_on_unscriptable_page():
    page = _FakePage(announce=["x"])
    actions._harvest_announcement(page, "Add to cart", {"announce": []})
    page.broken = True
    actions._harvest_announcement(page)          # must not raise
    assert actions.page_response(page) is not None   # last recorded survives


def test_no_baseline_no_harvest():
    page = _FakePage(announce=["Saved!"])
    actions._harvest_announcement(page)          # never armed — no-op
    assert getattr(page, "_noodle_response", None) is None


# --- RCA verdict (rca_report.py) ---------------------------------------------

_RESP = ("[page-response] after clicking 'Add to cart' the page "
         'announced: "Out of stock at Springfield"')


def test_page_response_classifies_app_rejected_action():
    v = rr.classify(_entry(message="AssertionError: expected 'Toy' in cart",
                           warnings=[_RESP]))
    assert v["category"] == "app-rejected-action"
    assert "Out of stock at Springfield" in v["reason"]
    assert "Add to cart" in v["reason"]
    assert "precondition" in v["fix"]


def test_app_rejected_action_is_a_registered_category():
    assert "app-rejected-action" in rr.CATEGORIES


def test_navigation_mismatch_outranks_page_response():
    v = rr.classify(_entry(
        warnings=["[navigation-mismatch] expected /a, current /b", _RESP]))
    assert v["category"] == "navigation-mismatch"


def test_page_response_outranks_wrong_action_target():
    # Form-validation shape: the submit click "went nowhere" AND the page
    # said why. "Point at another submit control" would be wrong advice.
    v = rr.classify(_entry(warnings=[
        "[no-navigation] clicking 'Submit' left the page unchanged "
        "(URL still /form)",
        "[page-response] after clicking 'Submit' the page "
        'announced: "Email is required"']))
    assert v["category"] == "app-rejected-action"
    assert "Email is required" in v["reason"]


# --- goal blocker vocabulary (goal.py) ---------------------------------------

def test_add_to_blocker_names_landed_page_vocabulary():
    goal = _nav_goal()
    probe = _nav_probe(mutation=False,
                       extra_landed=[_ctrl("Choose options", "#pdp-choose")])
    ev = goal_mod.evidence(goal, probe)
    blk = next(b for b in ev["blocking"] if "add_to" in b)
    assert "never guessed" in blk                # original contract intact
    assert "the landed page offers:" in blk
    assert "'Choose options'" in blk


# --- inspect zero-candidate vocabulary (inspect_locator.py) -------------------

def test_render_zero_candidates_lists_page_vocabulary():
    res = {"url": "https://x/", "text": "add to cart", "candidates": [],
           "resolved": None, "screenshot": None, "error": None,
           "page_controls": [["Options", 96], ["Wishlist", 12]]}
    txt = il.render(res)
    assert "no source matches" in txt
    assert "'Options' ×96" in txt
    assert "'Wishlist' ×12" in txt


def test_render_zero_candidates_without_vocabulary_unchanged():
    res = {"url": "https://x/", "text": "add to cart", "candidates": [],
           "resolved": None, "screenshot": None, "error": None}
    txt = il.render(res)
    assert "no source matches" in txt
    assert "interactive controls" not in txt


def test_page_vocabulary_shapes_pairs_and_survives_unscriptable_page():
    class _P:
        def evaluate(self, js):
            return [["Options", 96], ["Add", 4]]

    assert il.page_vocabulary(_P()) == [["Options", 96], ["Add", 4]]

    class _Broken:
        def evaluate(self, js):
            raise RuntimeError("navigated")

    assert il.page_vocabulary(_Broken()) == []

"""NOOD_0106 — close the dead ends that stalled a weaker LLM generating a
retail-site search test (Example post-mortem):

1. search() asks editable-first and opens an icon-hidden search box instead of
   fill()ing the search *button* whose accessible name also says "search".
2. An ambiguous match set with exactly ONE visible member resolves to it —
   hidden mobile/desktop duplicate markup no longer wins a blind .first.
3. A '{pom:key}' miss explains WHY (which files were consulted; whether the key
   exists but was scoped out by the per-page filename-stem match: default).
4. "closes the popup if it appears within N seconds" sweeps until the deadline
   for overlays that arrive seconds after load.

No browser, no LLM, no network.
"""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions, locator
from noodle.agents.web import pom as pom_mod
from noodle.orchestrator import runner
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _match(step_text: str):
    return match(normalize_phrasing(normalize_subject(step_text)))


# --- 1. search(): editable-first, icon-hidden box gets opened ------------------

class _El:
    """Minimal locator stand-in with editability + visibility answers."""
    def __init__(self, editable: bool, visible: bool = True):
        self._editable = editable
        self._visible = visible
        self.clicked = False
        self.filled = None
        self.pressed = None

    def evaluate(self, expr, arg=None):
        return self._editable

    def is_visible(self):
        return self._visible

    def click(self):
        self.clicked = True

    def fill(self, value):
        self.filled = value

    def press(self, key):
        self.pressed = key


def test_search_opens_icon_hidden_box_then_fills_it(monkeypatch):
    """The Example shape: the best 'search' match is a BUTTON (the icon
    that opens the box). search() must click it open and fill the revealed
    input — not fill() the button."""
    button = _El(editable=False)
    box = _El(editable=True)
    prefers = []

    def fake_find_first(page, candidates, scope=None, prefer=None):
        prefers.append(prefer)
        return button if len(prefers) == 1 else box

    monkeypatch.setattr(actions, "find_first", fake_find_first)
    actions.search(MagicMock(), "baby hanging toy")

    assert prefers == ["input", "input"]   # editable-first both times
    assert button.clicked is True
    assert button.filled is None           # the button never takes the fill
    assert box.filled == "baby hanging toy"
    assert box.pressed == "Enter"


def test_search_icon_open_but_no_box_fails_clearly(monkeypatch):
    """Clicked the search control, nothing editable appeared anywhere → a
    plain-English failure, not Playwright's 'Element is not an <input>'."""
    button = _El(editable=False)
    calls = {"n": 0}

    def fake_find_first(page, candidates, scope=None, prefer=None):
        calls["n"] += 1
        return button if calls["n"] == 1 else None

    monkeypatch.setattr(actions, "find_first", fake_find_first)
    page = MagicMock()
    page.get_by_role.return_value.count.return_value = 0
    with pytest.raises(AssertionError, match="no editable search box appeared"):
        actions.search(page, "boots")
    assert button.clicked is True


def test_is_editable_unknowable_counts_as_editable():
    """A locator that can't answer (evaluate raises) must not trigger the
    icon-open path — fill() stays the one that reports the real error."""
    loc = MagicMock()
    loc.evaluate.side_effect = RuntimeError("detached")
    assert actions._is_editable(loc) is True


def test_search_fills_a_visible_editable_box_directly(monkeypatch):
    """NOOD_0123 happy path: the box is editable AND visible → fill it straight
    away, no trigger click."""
    box = _El(editable=True, visible=True)
    monkeypatch.setattr(actions, "find_first", lambda *a, **k: box)
    actions.search(MagicMock(), "hotwheels")
    assert box.clicked is False
    assert box.filled == "hotwheels"
    assert box.pressed == "Enter"


def test_search_opens_hidden_editable_input_via_visible_trigger(monkeypatch):
    """NOOD_0123 — the Example shape find() returns a UNIQUE editable but
    HIDDEN <input> first (no visibility check on a unique match). fill()ing it
    would wait out the timeout, so search() must click a visible trigger to
    reveal it, re-resolve editable-first, and fill the now-visible box."""
    hidden_input = _El(editable=True, visible=False)
    trigger = _El(editable=False, visible=True)
    revealed = _El(editable=True, visible=True)
    prefers, generics = [], []

    def fake_find_first(page, candidates, scope=None, prefer=None):
        if prefer == "input":
            prefers.append(prefer)
            return hidden_input if len(prefers) == 1 else revealed
        generics.append(prefer)          # generic (no prefer) → visible trigger
        return trigger

    monkeypatch.setattr(actions, "find_first", fake_find_first)
    actions.search(MagicMock(), "hotwheels")

    assert trigger.clicked is True
    assert hidden_input.filled is None   # the hidden input never takes the fill
    assert revealed.filled == "hotwheels"
    assert revealed.pressed == "Enter"
    assert generics == [None]            # trigger resolved generically, once


def test_search_hidden_input_and_no_visible_trigger_fails_clearly(monkeypatch):
    """NOOD_0123 — hidden editable box and no visible trigger anywhere → a
    plain-English failure, not a silent 10s fill() timeout on the hidden box."""
    hidden_input = _El(editable=True, visible=False)

    def fake_find_first(page, candidates, scope=None, prefer=None):
        return hidden_input if prefer == "input" else None

    monkeypatch.setattr(actions, "find_first", fake_find_first)
    page = MagicMock()
    page.get_by_role.return_value.count.return_value = 0
    with pytest.raises(AssertionError, match="visible search box or a trigger"):
        actions.search(page, "hotwheels")
    assert hidden_input.filled is None


# --- 2. ambiguity: exactly one visible match resolves --------------------------

class _VisSubset:
    def __init__(self, n, first="visible-first"):
        self._n = n
        self.first = first

    def count(self):
        return self._n

    def element_handles(self):
        return []


class _AmbiguousLoc:
    """A match set of 3 whose visible=true narrowing yields `vis`."""
    def __init__(self, vis):
        self._vis = vis
        self.first = "blind-first"

    def locator(self, selector):
        assert selector == "visible=true"
        return self._vis

    def count(self):
        return 3

    def element_handles(self):
        return []


def _no_pom(monkeypatch):
    monkeypatch.setattr(locator.pom, "is_explicit", lambda t: None)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)


def test_single_visible_match_wins_over_hidden_duplicates(monkeypatch):
    """3 matches, 1 visible (hidden mobile+desktop twins) → the visible one
    resolves, no ambiguity warning, no POM needed."""
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(1))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    assert locator._find(MagicMock(), "search", poll=False) == "visible-first"


def test_multiple_visible_matches_stay_ambiguous_but_visible(monkeypatch):
    """Still ambiguous among VISIBLE elements → lenient mode's .first must at
    least be a visible element, not the hidden twin."""
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(2, first="first-visible"))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    locator.set_strict(False)
    try:
        assert locator._find(MagicMock(), "search", poll=False) == "first-visible"
    finally:
        locator.set_strict(None)


def test_visible_narrowing_failure_falls_back_to_old_path(monkeypatch):
    """A scope that can't chain visible=true keeps the pre-NOOD_0106 lenient
    behaviour instead of crashing."""
    _no_pom(monkeypatch)

    class _NoChain(_AmbiguousLoc):
        def locator(self, selector):
            raise RuntimeError("cannot chain")

    amb = _NoChain(None)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    locator.set_strict(False)
    try:
        assert locator._find(MagicMock(), "search", poll=False) == "blind-first"
    finally:
        locator.set_strict(None)


def test_strict_mode_still_fails_on_multiple_visible(monkeypatch):
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(2))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    locator.set_strict(True)
    try:
        with pytest.raises(AssertionError, match="Ambiguous locator"):
            locator._find(MagicMock(), "search", poll=False)
    finally:
        locator.set_strict(None)


# --- NOOD_0157: any_match — existence assertions accept any visible match -----

def test_any_match_resolves_first_visible_without_warning(monkeypatch):
    """2 visible duplicates (grid + list product card) + any_match → first
    visible, and the lenient/warning path is never reached — the warning
    would flip a green run to verified: false for a read-only assertion."""
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(2, first="first-visible"))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))

    def boom(*a, **k):
        raise AssertionError("_on_ambiguous must not be reached with any_match")
    monkeypatch.setattr(locator, "_on_ambiguous", boom)
    assert locator._find(MagicMock(), "product card", poll=False,
                         any_match=True) == "first-visible"
    assert locator.last_match_source() == "any-visible"


def test_any_match_beats_strict_mode(monkeypatch):
    """@strict guards actions from blind guesses; an existence assertion with
    several visible matches is proven by any of them — it must not fail."""
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(2, first="first-visible"))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    locator.set_strict(True)
    try:
        assert locator._find(MagicMock(), "product card", poll=False,
                             any_match=True) == "first-visible"
    finally:
        locator.set_strict(None)


def test_any_match_all_hidden_falls_through_to_lenient(monkeypatch):
    """0 visible members → any_match has nothing to prove with; the existing
    lenient path keeps its behaviour (assert_visible then sees a hidden
    .first and correctly moves on to its later phases)."""
    _no_pom(monkeypatch)
    amb = _AmbiguousLoc(_VisSubset(0))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, p=None: (amb, True))
    locator.set_strict(False)
    try:
        assert locator._find(MagicMock(), "product card", poll=False,
                             any_match=True) == "blind-first"
    finally:
        locator.set_strict(None)


# --- 3. {pom:key} miss explains itself ------------------------------------------

def _workspace(tmp_path, pom_files: dict):
    """A minimal app package; returns its features/ dir (pom.set_context arg)."""
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n")
    app = tmp_path / "noodle_tests" / "ct"
    features = app / "features"
    features.mkdir(parents=True)
    pod = app / "resources" / "pageobjects"
    pod.mkdir(parents=True)
    for name, text in pom_files.items():
        (pod / name).write_text(text)
    return features


def test_explain_miss_names_the_scoped_out_file(tmp_path):
    """THE trap that stalled the Codex run: search_pom.yaml with no match:
    defaults to url_contains 'search', which never matches the homepage URL —
    so {pom:search} 'has no entry' despite the key existing. The message must
    name the file, the scoping, and the match: fix."""
    features = _workspace(tmp_path, {
        "search_pom.yaml": "search:\n  css: '#search-input:visible'\n",
    })
    pom_mod.set_context(str(features))
    try:
        msg = pom_mod.explain_miss("search", "https://www.example.com/en.html")
    finally:
        pom_mod.set_context(None)
    assert "search_pom.yaml" in msg
    assert "IS defined" in msg
    assert "match: {}" in msg
    assert "example.com" in msg


def test_explain_miss_lists_checked_files_when_key_absent(tmp_path):
    features = _workspace(tmp_path, {
        "search_pom.yaml": "search:\n  css: '#s'\n",
    })
    pom_mod.set_context(str(features))
    try:
        msg = pom_mod.explain_miss("cart total", "https://example.com/")
    finally:
        pom_mod.set_context(None)
    assert "not found in" in msg
    assert "pageobjects/search_pom.yaml" in msg


def test_explain_miss_no_pom_files_says_so(tmp_path):
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n")
    features = tmp_path / "noodle_tests" / "ct" / "features"
    features.mkdir(parents=True)
    pom_mod.set_context(str(features))
    try:
        msg = pom_mod.explain_miss("search", "https://example.com/")
    finally:
        pom_mod.set_context(None)
    assert "no pom.yaml found" in msg


def test_explicit_pom_miss_warning_carries_the_diagnosis(monkeypatch, capsys):
    """locator._find surfaces explain_miss for a '{pom:...}' step, so the fix
    ships inside the run output the driving agent reads."""
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator.pom, "explain_miss",
                        lambda key, url="": "DIAGNOSIS-SENTINEL")
    assert locator._find(MagicMock(), "{pom:search}") is None
    assert "DIAGNOSIS-SENTINEL" in capsys.readouterr().out


# --- 4. timed popup close --------------------------------------------------------

@pytest.mark.parametrize("step,within", [
    ("User closes the popup if it appears within 10 seconds", 10),
    ("User dismisses the popups if one appears within 15 seconds", 15),
    ("User closes the popup if it appears in 8 seconds", 8),
    ("User closes all popups within 20 seconds", 20),
])
def test_timed_popup_close_patterns(step, within):
    t, p = _match(step)
    assert t == "close_popups"
    assert p == {"within": within}


def test_untimed_popup_close_unchanged():
    assert _match("User closes all popups") == ("close_popups", {})
    t, p = _match("User closes the popups if any appear")
    assert t == "close_popups" and p == {}


def test_search_step_still_matches():
    t, p = _match("User searches for 'baby hanging toy'")
    assert t == "search"
    assert p == {"query": "baby hanging toy"}


class _LateEl:
    def __init__(self):
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, timeout=None):
        self.clicked = True


class _LatePopupPage:
    """No popup on the first sweep; one 'arrives' during the first wait."""
    def __init__(self):
        self.ready = False
        self.el = _LateEl()
        self.keyboard = MagicMock()
        self.waits = 0

    def wait_for_timeout(self, ms):
        self.waits += 1
        self.ready = True

    def locator(self, selector):
        page = self

        class _Loc:
            def count(self):
                return 1 if page.ready and selector == 'button[aria-label="Close" i]' else 0

            def nth(self, i):
                return page.el

        return _Loc()


def test_close_popups_within_catches_late_popup():
    page = _LatePopupPage()
    actions.close_popups(page, within=30)   # returns on first close, not at deadline
    assert page.el.clicked is True
    assert page.waits >= 1
    page.keyboard.press.assert_called_once_with("Escape")


def test_close_popups_default_is_single_sweep():
    page = _LatePopupPage()   # popup only arrives after a wait — never swept
    actions.close_popups(page)
    assert page.waits == 0
    assert page.el.clicked is False


def test_close_popups_within_gives_up_at_deadline():
    page = _LatePopupPage()
    page.wait_for_timeout = lambda ms: None   # popup never arrives
    start = time.time()
    actions.close_popups(page, within=0.2)
    assert time.time() - start < 5            # bounded, no hang
    page.keyboard.press.assert_called_once_with("Escape")


def test_runner_threads_within_to_close_popups(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.actions, "close_popups",
                        lambda page, within=0, deny_permissions=None: seen.update(within=within))
    ctx = SimpleNamespace(page=MagicMock(), _vars={})
    runner.execute_step("User closes the popup if it appears within 7 seconds", ctx)
    assert seen == {"within": 7}

"""NOOD_0180 — "on screen" is not "visible".

Playwright's is_visible() answers "does this element have a box in the
layout", not "can the tester see it". An element scrolled outside its own
scroll container's overflow keeps a box, so is_visible() stays True. Measured
live against https://codepen.io/jaemskyle/pen/XWJvRL — a 500px-wide strip
holding 1020px of 170px sections:

    scrollLeft=0    section six: is_visible=True   intersectionRatio=0.00
    after scroll    section six: is_visible=True   intersectionRatio=1.00

So the obvious scroll test — "scrolls right / should see 'six'" — is green
before the scroll ever runs. assert_on_screen asks IntersectionObserver
instead, the one native API that clips the target against every ancestor's
overflow as well as the viewport. No browser, no LLM.
"""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions
from noodle.resolver.step_resolver import VALID_TYPES, resolve


def _page_showing(ratio):
    """A find()-able element whose IntersectionObserver reports `ratio`."""
    loc = MagicMock()
    loc.first.evaluate.return_value = ratio
    return MagicMock(), loc


# --- the action ----------------------------------------------------------

@pytest.mark.parametrize("ratio,on", [(0.0, False), (1.0, True), (0.12, True)])
def test_assert_on_screen_passes_when_ratio_matches(monkeypatch, ratio, on):
    """Any sliver counts as on screen; only a hard 0 is off screen."""
    page, loc = _page_showing(ratio)
    monkeypatch.setattr(actions, "find", lambda *a, **k: loc)
    actions.assert_on_screen(page, "six", on=on)   # must not raise


@pytest.mark.parametrize("ratio,on", [(0.0, True), (1.0, False)])
def test_assert_on_screen_fails_when_ratio_contradicts(monkeypatch, ratio, on):
    page, loc = _page_showing(ratio)
    monkeypatch.setattr(actions, "find", lambda *a, **k: loc)
    with pytest.raises(AssertionError) as e:
        actions.assert_on_screen(page, "six", on=on)
    # The message must say it IS in the DOM — otherwise the reader hunts a
    # locator bug when the real answer is "scroll the container".
    assert "in the DOM" in str(e.value)


def test_assert_on_screen_never_scrolls_what_it_measures(monkeypatch):
    """heal=False is load-bearing: the self-heal chain's first move is to
    scroll the element into view, which would scroll the very container under
    measurement and turn every "is not in view" into a false failure."""
    seen = {}
    page, loc = _page_showing(0.0)

    def fake_find(page, text, **kw):
        seen.update(kw)
        return loc

    monkeypatch.setattr(actions, "find", fake_find)
    actions.assert_on_screen(page, "six", on=False)
    assert seen["heal"] is False
    assert seen["allow_dom_scan"] is False


def test_missing_element_reports_not_found(monkeypatch):
    monkeypatch.setattr(actions, "find", lambda *a, **k: None)
    with pytest.raises(AssertionError):
        actions.assert_on_screen(MagicMock(), "nope")


# --- the steps -----------------------------------------------------------

@pytest.mark.parametrize("step,on", [
    ('"six" is in view', True),
    ('the "six" section is in view', True),
    ('"six" should be on screen', True),
    ('"section {" is scrolled into view', True),
    ('the "css pane" panel is in the viewport', True),
    ('"six" is not in view', False),
    ('"six" is no longer in view', False),
    ('the "six" section should not be on screen', False),
])
def test_in_view_steps_resolve(step, on):
    assert resolve(step) == {"type": "assert_on_screen",
                             "text": step.split('"')[1], "on": on}


@pytest.mark.parametrize("step", [
    'should see "six"',
    'sees "six" on the screen',
    '"six" is visible on the page',
])
def test_existing_visibility_phrasings_are_untouched(step):
    """The new patterns sit ABOVE the assert_visible block, so they must not
    swallow any phrasing that block already owned."""
    assert resolve(step)["type"] == "assert_visible"


def test_action_is_registered_for_the_llm_fallback():
    """VALID_TYPES is a hand-kept mirror of runner.py's dispatch chain; a new
    action missing from it gets rejected as a bogus type at runtime."""
    assert "assert_on_screen" in VALID_TYPES

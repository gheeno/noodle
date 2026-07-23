"""NOOD_0018-2 — vision-locate selector parsing.

The vision LLM is the last-resort locator fallback. The old code took the
model's raw reply, stripped single backticks, and fed it straight to
page.locator() — a markdown-fenced reply or a hallucinated selector broke or
silently mislocated. _parse_vision_selector hardens that: structured JSON with
an explicit "can't find" (null) path, plus tolerant parsing. Pure function, so
these need no page or model.
"""
from noodle.agents.web.locator import _parse_vision_selector


def test_structured_selector_returned():
    assert _parse_vision_selector('{"selector": "#login-btn"}') == "#login-btn"


def test_structured_null_means_not_found():
    # The model saying it can't see the element must NOT yield a selector.
    assert _parse_vision_selector('{"selector": null}') is None


def test_structured_empty_string_is_not_found():
    assert _parse_vision_selector('{"selector": ""}') is None


def test_json_fence_is_stripped():
    # The exact bug from the review: a fenced reply used to break page.locator().
    assert _parse_vision_selector('```json\n{"selector": ".cart"}\n```') == ".cart"


def test_selector_recovered_from_surrounding_prose():
    raw = 'Sure, the element is here: {"selector": "button.primary"} hope that helps'
    assert _parse_vision_selector(raw) == "button.primary"


def test_bare_selector_fallback_for_models_that_ignore_json():
    assert _parse_vision_selector("#search-box") == "#search-box"


def test_fenced_bare_selector_is_stripped():
    # css-fenced bare selector (no JSON) — must not leak backticks into locator.
    assert _parse_vision_selector("```css\n.add-to-cart\n```") == ".add-to-cart"


def test_empty_reply_is_none():
    assert _parse_vision_selector("   ") is None


def test_non_string_is_none():
    assert _parse_vision_selector(None) is None

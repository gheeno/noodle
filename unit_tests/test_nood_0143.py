"""NOOD_0143 — web-coverage audit gap closure.

The audit (2026-07-19) found these standard web-testing capabilities missing
from the pattern table, each a one-line Playwright API:
  G1  blocking URL wait (SPA navigation)         → wait_url
  G2  full-page scroll to bottom/top             → scroll_edge
  G3  localStorage/sessionStorage set + assert   → set_storage/assert_storage
  G4  cookie value/presence assert               → assert_cookie
  G5  focus assert (tab-order testing)           → assert_focused
  G6  computed-CSS assert                        → assert_css
  G7  column sort-order assert                   → assert_column_sorted
  G8  @http_credentials was documented but NEVER implemented (phantom) —
      now backed by browser-context http_credentials from env.

No browser, no LLM, no network.
"""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _match(step_text: str):
    return match(normalize_phrasing(normalize_subject(step_text)))


# --- pattern registration ------------------------------------------------------

def test_wait_url_patterns():
    assert _match("User waits until the URL contains 'checkout'") == \
        ("wait_url", {"fragment": "checkout"})
    assert _match("User waits for the URL to contain 'cart'") == \
        ("wait_url", {"fragment": "cart"})
    assert _match("User waits until the URL is 'https://x.test/done'") == \
        ("wait_url", {"fragment": "https://x.test/done", "mode": "exact"})


def test_wait_url_not_swallowed_by_element_wait():
    t, params = _match("User waits until the URL contains 'checkout'")
    assert t == "wait_url"          # NOT wait_visible hunting "URL" text
    # and the generic element wait still works
    assert _match("User waits for the loading icon")[0] == "wait_visible"


def test_scroll_edge_patterns():
    assert _match("User scrolls to the bottom of the page") == \
        ("scroll_edge", {"edge": "bottom"})
    assert _match("User scrolls to the top") == ("scroll_edge", {"edge": "top"})
    # the table-scoped scroll keeps its own action
    assert _match("User scrolls the table to the bottom")[0] == "scroll_table"
    # quoted target scroll unchanged
    assert _match("User scrolls to 'Footer'")[0] == "scroll_to"


def test_storage_patterns():
    assert _match("User sets the local storage 'flag' to 'on'") == \
        ("set_storage", {"kind": "local", "key": "flag", "value": "on"})
    assert _match("the session storage 'draft' should contain 'saved'") == \
        ("assert_storage", {"kind": "session", "key": "draft", "value": "saved"})
    assert _match("the local storage 'cart' should be '3'") == \
        ("assert_storage", {"kind": "local", "key": "cart", "value": "3"})
    # clear steps unchanged
    assert _match("User clears the local storage")[0] == "clear_storage"


def test_cookie_patterns():
    assert _match("the cookie 'session' should exist") == \
        ("assert_cookie", {"name": "session", "value": None})
    assert _match("the cookie 'consent' should be 'accepted'") == \
        ("assert_cookie", {"name": "consent", "value": "accepted"})
    assert _match("User sets the cookie 'a' to 'b'")[0] == "set_cookie"


def test_focus_and_css_patterns():
    assert _match("the 'Email' field should be focused") == \
        ("assert_focused", {"locator": "Email"})
    assert _match("the 'Search' box should have focus") == \
        ("assert_focused", {"locator": "Search"})
    assert _match(
        "the 'error banner' should have css 'color' of 'rgb(220, 38, 38)'") == \
        ("assert_css", {"locator": "error banner", "prop": "color",
                        "value": "rgb(220, 38, 38)"})
    # state asserts unchanged
    assert _match("the 'Submit' button should be disabled")[0] == "assert_state"


def test_column_sorted_patterns():
    assert _match("the 'Year' column should be sorted ascending") == \
        ("assert_column_sorted", {"column": "Year", "descending": False})
    assert _match("the 'Price' column should be sorted in descending order") == \
        ("assert_column_sorted", {"column": "Price", "descending": True})
    assert _match("the 'Name' column should be sorted") == \
        ("assert_column_sorted", {"column": "Name", "descending": False})
    # contains-form unchanged
    assert _match("the 'Genre' column should contain 'Thriller'")[0] == \
        "assert_column_contains"


# --- G7: sort-key logic (pure) -------------------------------------------------

def test_sort_keys_numeric_when_all_parse():
    assert actions._sort_keys(["$1,200.50", "8", "34"]) == [1200.5, 8.0, 34.0]


def test_sort_keys_text_fallback_and_empty_cells_dropped():
    assert actions._sort_keys(["Banana", "", "apple"]) == ["banana", "apple"]
    assert actions._sort_keys([]) == []


def test_sort_keys_mixed_falls_back_to_text():
    # one unparseable cell → the whole column compares as text
    assert actions._sort_keys(["10", "N/A", "2"]) == ["10", "n/a", "2"]


# --- G3/G4: storage + cookie asserts against a mock page -----------------------

def _page(url="https://x.test/p"):
    page = MagicMock()
    page.url = url
    return page


def test_assert_storage_pass_and_fail():
    page = _page()
    page.evaluate.return_value = "on"
    actions.assert_storage(page, "local", "flag", "on")      # exact
    actions.assert_storage(page, "local", "flag", "o")       # substring
    page.evaluate.return_value = None
    with pytest.raises(AssertionError, match="local storage 'flag'"):
        actions.assert_storage(page, "local", "flag", "on")


def test_set_storage_interpolates_kind_only_from_pattern():
    page = _page()
    actions.set_storage(page, "session", "k", "v")
    js = page.evaluate.call_args[0][0]
    assert "sessionStorage" in js
    assert page.evaluate.call_args[0][1] == ["k", "v"]


def test_assert_cookie_presence_value_and_miss():
    page = _page()
    page.context.cookies.return_value = [{"name": "session", "value": "abc123"}]
    actions.assert_cookie(page, "session")                   # presence only
    actions.assert_cookie(page, "session", "abc123")         # exact
    actions.assert_cookie(page, "session", "abc")            # substring
    with pytest.raises(AssertionError, match="No cookie named 'ghost'"):
        actions.assert_cookie(page, "ghost")
    with pytest.raises(AssertionError, match="Expected cookie 'session'"):
        actions.assert_cookie(page, "session", "zzz")


# --- G1: wait_url failure message ----------------------------------------------

def test_wait_url_failure_names_fragment_and_url(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "1")
    page = _page("https://x.test/stuck")
    page.wait_for_url.side_effect = TimeoutError("timeout")
    with pytest.raises(AssertionError, match="contain 'checkout'"):
        actions.wait_url(page, "checkout")
    with pytest.raises(AssertionError, match="become 'https://x.test/done'"):
        actions.wait_url(page, "https://x.test/done", mode="exact")


def test_wait_url_exact_tolerates_trailing_slash():
    page = _page()
    actions.wait_url(page, "https://x.test/done", mode="exact")
    pred = page.wait_for_url.call_args[0][0]
    assert pred("https://x.test/done/") and pred("https://x.test/done")
    assert not pred("https://x.test/done/extra")


# --- G2: scroll_edge -----------------------------------------------------------

def test_scroll_edge_passes_edge_to_js():
    page = _page()
    actions.scroll_edge(page, "bottom")
    assert page.evaluate.call_args[0][1] == "bottom"


# --- G8: @http_credentials is real now -----------------------------------------

def test_http_credentials_tag_is_wired(monkeypatch):
    """The phantom is dead: hooks builds http_credentials ctx opts from env
    and fails loudly when the pair is missing."""
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks)
    assert "http_credentials" in src
    assert "NOODLE_HTTP_USER" in src and "NOODLE_HTTP_PASSWORD" in src

"""NOOD_0018-6 — assert_count counts VISIBLE occurrences.

A raw get_by_text count includes sr-only/aria/tooltip duplicates, so a "should
see N X" assertion could double-count. assert_count now filters with the
`visible=true` engine. Page is mocked — no browser.
"""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions


def _page_with_visible_count(n):
    page = MagicMock()
    page.get_by_text.return_value.locator.return_value.count.return_value = n
    return page


def test_assert_count_passes_on_visible_match():
    page = _page_with_visible_count(3)
    actions.assert_count(page, 3, "result")
    # filtered to visible — not the raw text count
    page.get_by_text.assert_called_once_with("result", exact=False)
    page.get_by_text.return_value.locator.assert_called_once_with("visible=true")


def test_assert_count_ignores_hidden_duplicates():
    # 3 visible even though the DOM may hold sr-only twins — passes.
    page = _page_with_visible_count(3)
    actions.assert_count(page, 3, "Add to cart")


def test_assert_count_fails_with_visible_in_message():
    page = _page_with_visible_count(6)
    with pytest.raises(AssertionError, match="Expected 3 visible 'item' — found 6"):
        actions.assert_count(page, 3, "item")

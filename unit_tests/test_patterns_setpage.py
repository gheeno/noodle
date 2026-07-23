"""Phase 9.3 — the page-pin step pattern, and that it doesn't shadow navigate."""
from noodle.resolver.patterns import match


def test_page_pin_pattern():
    assert match('is on the "Search Results" page') == ('set_page', {'name': 'Search Results'})
    assert match('is on "Cart" page') == ('set_page', {'name': 'Cart'})


def test_navigate_still_matches():
    action, params = match('is on "https://example.com"')
    assert action == 'navigate'
    assert params == {'url': 'https://example.com'}

"""Phase 9.2/9.3 — page-scoped POM lookup and the page-pin override."""
import textwrap
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import pom


@pytest.fixture
def pom_dir(tmp_path):
    (tmp_path / "pom.yaml").write_text(textwrap.dedent("""
        pages:
          home:
            match: { url_contains: "example.com/$" }
            search: { css: "input.home-search" }
          search results:
            match: { url_contains: "/search" }
            search: { css: "input.results-filter" }
        shared:
          cookie accept: { id: onetrust-accept-btn-handler }
    """))
    pom.set_context(str(tmp_path))
    pom.set_active_page(None)
    pom._load_yaml.cache_clear()
    yield tmp_path
    pom.set_context(None)
    pom.set_active_page(None)


def test_same_key_resolves_per_page_by_url(pom_dir):
    assert pom._lookup("search", "https://example.com/") == {"css": "input.home-search"}
    assert pom._lookup("search", "https://example.com/search?q=x") == {"css": "input.results-filter"}


def test_shared_block_is_page_agnostic(pom_dir):
    on_home = pom._lookup("cookie accept", "https://example.com/")
    on_results = pom._lookup("cookie accept", "https://example.com/search")
    assert on_home == on_results == {"id": "onetrust-accept-btn-handler"}


def test_pinned_page_overrides_url(pom_dir):
    # URL says home, but we pinned the results page → results selector wins.
    pom.set_active_page("search results")
    assert pom._lookup("search", "https://example.com/") == {"css": "input.results-filter"}


def test_unknown_key_returns_none(pom_dir):
    assert pom._lookup("nonexistent widget", "https://example.com/") is None


def test_legacy_flat_format_still_works(tmp_path):
    (tmp_path / "pom.yaml").write_text("burger menu:\n  id: react-burger-menu-btn\n")
    pom.set_context(str(tmp_path))
    pom.set_active_page(None)
    pom._load_yaml.cache_clear()
    try:
        assert pom._lookup("burger menu", "https://anything/") == {"id": "react-burger-menu-btn"}
        assert pom._lookup("Burger Menu", "") == {"id": "react-burger-menu-btn"}  # case-insensitive
    finally:
        pom.set_context(None)


# ---------------------------------------------------------------------------
# _build_locator — every selector type reaches its matching Playwright call.
# ---------------------------------------------------------------------------

def _fake_page(method: str):
    """A page mock whose `method` returns a locator with count()==1."""
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    getattr(page, method).return_value = loc
    return page, loc


@pytest.mark.parametrize("selector_type,method,extra_kwargs", [
    ("text", "get_by_text", {"exact": False}),
    ("label", "get_by_label", {"exact": False}),
    ("placeholder", "get_by_placeholder", {"exact": False}),
    ("title", "get_by_title", {"exact": False}),
    ("alt_text", "get_by_alt_text", {"exact": False}),
])
def test_build_locator_text_family(selector_type, method, extra_kwargs):
    page, loc = _fake_page(method)
    result = pom._build_locator(page, {selector_type: "Username"}, "username field")
    assert result is loc  # raw locator — locate()/locate_all() apply .first policy
    getattr(page, method).assert_called_once_with("Username", **extra_kwargs)


def test_build_locator_text_family_exact_flag():
    page, loc = _fake_page("get_by_placeholder")
    pom._build_locator(page, {"placeholder": {"value": "Username", "exact": True}}, "username field")
    page.get_by_placeholder.assert_called_once_with("Username", exact=True)


def test_build_locator_role_bare_string():
    page, loc = _fake_page("get_by_role")
    result = pom._build_locator(page, {"role": "navigation"}, "nav")
    assert result is loc
    page.get_by_role.assert_called_once_with("navigation")


def test_build_locator_role_with_name():
    page, loc = _fake_page("get_by_role")
    result = pom._build_locator(
        page, {"role": {"type": "button", "name": "Login"}}, "login button")
    assert result is loc
    page.get_by_role.assert_called_once_with("button", exact=False, name="Login")


def test_build_locator_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown POM selector type"):
        pom._build_locator(MagicMock(), {"bogus": "x"}, "thing")

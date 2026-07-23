"""{key} explicit POM syntax — a step-authored pin straight to a pom.yaml
entry, skipping accessibility/self-heal/vision entirely. Mirrors the
@page:<name> tag (test_hooks_hardening.py::TestPagePinTag) but scoped to one
element instead of the whole scenario."""
import pytest

from noodle.agents.web import locator, pom


class TestIsExplicit:
    def test_wrapped_in_braces(self):
        assert pom.is_explicit("{login button}") == "login button"

    def test_plain_text_is_not_explicit(self):
        assert pom.is_explicit("login button") is None

    def test_brackets_and_backticks_are_not_explicit(self):
        assert pom.is_explicit("[BUSTERBLOCK]") is None
        assert pom.is_explicit("`USERNAME`") is None

    def test_strips_outer_whitespace(self):
        assert pom.is_explicit("  {search field}  ") == "search field"


class _RaisesIfTouched:
    """Stands in for `page` — fails the test if any accessibility strategy
    touches it, proving the explicit path never falls through to _find's
    normal chain."""
    def __getattr__(self, name):
        raise AssertionError(f"accessibility strategy '{name}' ran for an explicit {{key}}")


class TestExplicitFind:
    def test_hit_returns_pom_locator_without_touching_accessibility(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(
            pom, "locate",
            lambda page, text: sentinel if text == "login button" else None,
        )
        assert locator._find(_RaisesIfTouched(), "{login button}") is sentinel

    def test_miss_returns_none_without_falling_back(self, monkeypatch):
        monkeypatch.setattr(pom, "locate", lambda page, text: None)
        assert locator._find(_RaisesIfTouched(), "{missing key}") is None


class TestExplicitWait:
    def test_wait_for_miss_raises_naming_the_key(self, monkeypatch):
        monkeypatch.setattr(pom, "locate", lambda page, text: None)
        with pytest.raises(AssertionError, match=r"\{pom:missing key\}"):
            locator.wait_for(object(), "{missing key}")

    def test_wait_hidden_miss_raises_naming_the_key(self, monkeypatch):
        monkeypatch.setattr(pom, "locate", lambda page, text: None)
        with pytest.raises(AssertionError, match=r"\{pom:missing key\}"):
            locator.wait_hidden(object(), "{missing key}")

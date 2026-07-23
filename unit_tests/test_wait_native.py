"""NOOD_0018-4 / NOOD_0115 — wait_for / wait_hidden resolution.

NOOD_0018-4: delegate to Playwright's native locator.wait_for
(MutationObserver-backed) on the POM path, and surface a clean AssertionError
on timeout. NOOD_0115: the non-POM path resolves through find()'s full chain
(role/name accessibility, aria-label/alt/title, self-heal) instead of a bare
get_by_text — an image tile whose caption exists only as alt text has no text
node, so the old text wait could never succeed on it. Page is mocked — no
browser.
"""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import locator


def test_wait_for_uses_pom_native_wait(monkeypatch):
    pom_loc = MagicMock()
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: pom_loc)
    page = MagicMock()

    locator.wait_for(page, "Login", timeout=5000)

    pom_loc.wait_for.assert_called_once_with(state="visible", timeout=5000)
    page.get_by_text.assert_not_called()   # POM short-circuits the find path


def test_wait_for_resolves_via_find_and_waits_visible(monkeypatch):
    """The alt-only caption case: get_by_text has nothing, find() resolves."""
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    loc = MagicMock()
    calls = {}

    def fake_find(page, text, **kw):
        calls["kw"] = kw
        return loc

    monkeypatch.setattr(locator, "find", fake_find)
    page = MagicMock()

    locator.wait_for(page, "Weekly Flyer", timeout=3000)

    # bounded wait uses the cheap probe, not the full self-heal chain per lap
    assert calls["kw"] == {"poll": False, "heal": False}
    assert loc.wait_for.call_count == 1
    assert loc.wait_for.call_args.kwargs["state"] == "visible"


def test_wait_for_default_budget_uses_full_find(monkeypatch):
    """No explicit timeout → one full-power find() (it polls internally)."""
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    loc = MagicMock()
    calls = {}

    def fake_find(page, text, **kw):
        calls["kw"] = kw
        return loc

    monkeypatch.setattr(locator, "find", fake_find)

    locator.wait_for(MagicMock(), "Weekly Flyer")

    assert calls["kw"] == {}   # no poll/heal downgrade — the rich chain runs


def test_wait_for_unresolvable_raises_assertion(monkeypatch):
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator, "find", lambda page, text, **kw: None)

    with pytest.raises(AssertionError, match="Timed out waiting for visible 'Ghost'"):
        locator.wait_for(MagicMock(), "Ghost", timeout=300)


def test_wait_for_resolved_but_never_visible_raises(monkeypatch):
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    loc = MagicMock()
    loc.wait_for.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(locator, "find", lambda page, text, **kw: loc)

    with pytest.raises(AssertionError, match="never became visible"):
        locator.wait_for(MagicMock(), "Ghost", timeout=1000)


def test_wait_for_ocr_coordinate_counts_as_visible(monkeypatch):
    """Phase T sentinel — OCR located the text on rendered pixels."""
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator, "find",
                        lambda page, text, **kw: ("coordinate", 10.0, 20.0))

    locator.wait_for(MagicMock(), "Shadow Text", timeout=1000)   # no raise


def test_wait_hidden_resolves_via_find_and_waits_hidden(monkeypatch):
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    loc = MagicMock()
    calls = {}

    def fake_find(page, text, **kw):
        calls["kw"] = kw
        return loc

    monkeypatch.setattr(locator, "find", fake_find)

    locator.wait_hidden(MagicMock(), "Spinner", timeout=2000)

    # absence probe must not scroll/vision-heal or poll for appearance
    assert calls["kw"] == {"poll": False, "heal": False}
    loc.wait_for.assert_called_once_with(state="hidden", timeout=2000)


def test_wait_hidden_unresolvable_is_already_gone(monkeypatch):
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    monkeypatch.setattr(locator, "find", lambda page, text, **kw: None)

    locator.wait_hidden(MagicMock(), "Spinner", timeout=1000)   # returns at once


def test_wait_hidden_timeout_raises_assertion(monkeypatch):
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    loc = MagicMock()
    loc.wait_for.side_effect = RuntimeError("still there")
    monkeypatch.setattr(locator, "find", lambda page, text, **kw: loc)

    with pytest.raises(AssertionError, match="Timed out waiting for 'Spinner' to disappear"):
        locator.wait_hidden(MagicMock(), "Spinner", timeout=1000)

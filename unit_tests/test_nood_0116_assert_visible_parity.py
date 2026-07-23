"""NOOD_0116 — assert_visible gets find()'s smart-wait budget.

Observed on an Angular SPA where a form-submit click reboots the whole app
shell: "the user sees 'X'" failed at the old flat NOODLE_TIMEOUT even
though the text renders ~15s later — while click/fill steps on the same
page state poll NOODLE_FIND_TIMEOUT with the NOOD_0103 settle early-exit.
Fix: assert_visible's wait phase delegates to find(poll=True, heal=False)
instead of a flat get_by_text wait_for, so the budget (and the settle
early-exit that caps genuinely-absent text) come from the one place that
already implements them. No browser, no LLM."""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions


def _no_text_page():
    """Page whose get_by_text never yields a visible match."""
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 0
    page.get_by_text.return_value = loc
    return page


def test_assert_visible_polls_past_flat_noodle_timeout(monkeypatch):
    """Text that only resolves once the poll runs (SPA reload settling) must
    pass: the cheap poll=False probe misses, the poll=True pass hits."""
    visible = MagicMock()
    visible.is_visible.return_value = True
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True,
                  allow_dom_scan=True, any_match=False):
        calls.append((poll, heal))
        return visible if poll else None

    monkeypatch.setattr(actions, "find", fake_find)
    actions.assert_visible(_no_text_page(), "Welcome back")  # must not raise
    assert calls == [(False, False), (True, False)]


def test_assert_visible_still_respects_settle_early_exit(monkeypatch):
    """The budget is find()'s, not a flat timeout of assert_visible's own:
    the wait phase is exactly one poll=True find() call — no extra wait_for
    with its own NOODLE_TIMEOUT deadline. The settle early-exit (NOOD_0103)
    lives inside that poll, so delegating is what keeps a genuinely-absent
    text capped at NOODLE_SETTLE_TIMEOUT instead of 120s."""
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True,
                  allow_dom_scan=True, any_match=False):
        calls.append(poll)
        return None

    monkeypatch.setattr(actions, "find", fake_find)
    monkeypatch.setattr(actions, "_assert_visible_ocr_or_fail",
                        lambda p, t: None)
    page = _no_text_page()
    actions.assert_visible(page, "never there")
    assert calls == [False, True]           # one cheap pass, one polled pass
    page.get_by_text.return_value.first.wait_for.assert_not_called()


def test_assert_visible_falls_through_to_ocr_after_poll_exhausted(monkeypatch):
    """Poll spent and no visible text match → the existing OCR/hard-fail
    fallback still fires; the change only widens the window before it."""
    monkeypatch.setattr(actions, "find", lambda *a, **k: None)
    with pytest.raises(AssertionError, match="Expected to see"):
        actions.assert_visible(_no_text_page(), "ghost text")


def test_assert_visible_happy_path_unchanged_when_text_present_immediately(monkeypatch):
    """Text already resolvable → exactly one cheap (poll=False) find() pass,
    no poll — guards against regressing the common case to a slower one."""
    visible = MagicMock()
    visible.is_visible.return_value = True
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True,
                  allow_dom_scan=True, any_match=False):
        calls.append(poll)
        return visible

    monkeypatch.setattr(actions, "find", fake_find)
    actions.assert_visible(_no_text_page(), "Welcome back")
    assert calls == [False]


def test_assert_visible_sronly_first_match_scans_for_visible_duplicate(monkeypatch):
    """find() resolves an sr-only (hidden) unique match → the visible-duplicate
    scan over get_by_text matches must still pass the assertion."""
    hidden = MagicMock()
    hidden.is_visible.return_value = False
    monkeypatch.setattr(actions, "find", lambda *a, **k: hidden)
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 2
    loc.nth.side_effect = lambda i: MagicMock(is_visible=lambda: i == 1)
    page.get_by_text.return_value = loc
    actions.assert_visible(page, "Welcome back")  # must not raise


def test_assert_hidden_probe_stays_cheap(monkeypatch):
    """assert_hidden's absence probe must NOT inherit the poll — a "not there"
    answer polling NOODLE_FIND_TIMEOUT would make every negative assertion
    cost minutes."""
    polls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True,
                  allow_dom_scan=True, any_match=False):
        polls.append(poll)
        return None

    monkeypatch.setattr(actions, "find", fake_find)
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 0
    page.get_by_text.return_value = loc
    actions.assert_hidden(page, "gone")
    assert polls == [False]

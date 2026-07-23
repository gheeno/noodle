"""NOOD_0044 — conditional steps (run_if), popup conditionals, and the
expanded hard-sleep vocabulary (sleep/pause verbs, optional "for", ms/min
units, and the "waits for the page to load : N seconds" thread-sleep form).

Pattern matching is tested through the same normalize pipeline resolve()
uses; runner dispatch is tested with a mocked page — no browser.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.orchestrator import runner
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
from noodle.resolver.step_resolver import VALID_TYPES


def _match(step_text: str):
    return match(normalize_phrasing(normalize_subject(step_text)))


# --- pattern matching: conditionals -----------------------------------------

def test_trailing_if_positive():
    t, p = _match("User clicks 'Skip' if 'Tour popup' appears")
    assert t == "run_if"
    assert p == {"then": "clicks 'Skip'", "condition": "Tour popup", "negate": False}


def test_trailing_when_is_visible():
    t, p = _match("User clicks 'Retry' when 'Error banner' is visible")
    assert t == "run_if"
    assert p["condition"] == "Error banner" and p["negate"] is False


def test_trailing_if_negated():
    t, p = _match("User clicks 'Open menu' if 'Sidebar' is not visible")
    assert t == "run_if"
    assert p == {"then": "clicks 'Open menu'", "condition": "Sidebar", "negate": True}


def test_trailing_if_wraps_a_fill_step():
    t, p = _match("User enters 'admin' in the username field if 'Login form' appears")
    assert t == "run_if"
    assert p["then"] == "enters 'admin' in the username field"


def test_leading_if_positive():
    t, p = _match("if 'Cookie banner' appears, clicks 'Accept all'")
    assert t == "run_if"
    assert p == {"condition": "Cookie banner", "negate": False, "then": "clicks 'Accept all'"}


def test_leading_if_negated():
    t, p = _match("if 'Welcome tour' does not appear, clicks 'Start tour'")
    assert t == "run_if"
    assert p["negate"] is True and p["then"] == "clicks 'Start tour'"


def test_bare_appears_on_page_perform():
    t, p = _match("a 'Promo modal' appears on the page, performs clicks 'Close'")
    assert t == "run_if"
    assert p == {"condition": "Promo modal", "negate": False, "then": "clicks 'Close'"}


def test_bare_form_without_on_the_page_does_not_match_run_if():
    # Without the "on the page" anchor the bare form must NOT fire — that
    # phrasing space belongs to assertions/waits.
    result = _match("a 'Promo modal' appears perform clicks 'Close'")
    assert result is None or result[0] != "run_if"


def test_popup_conditional_routes_to_close_popups():
    t, p = _match("if the page appears to have a pop-up, closes the pop-up")
    assert t == "close_popups" and p == {}


def test_popup_conditional_trailing_form():
    t, _ = _match("User closes the popups if any appear")
    assert t == "close_popups"


def test_wait_until_appears_still_routes_to_wait_visible():
    # Guard: the conditional patterns must not swallow the existing waits.
    t, _ = _match("User waits until 'Welcome' appears")
    assert t == "wait_visible"


def test_run_if_is_a_valid_runner_type():
    assert "run_if" in VALID_TYPES


# --- pattern matching: hard sleeps -------------------------------------------

@pytest.mark.parametrize("step,seconds", [
    ("User waits 3 seconds", 3),
    ("User waits for 3 seconds", 3),
    ("User sleeps 2 seconds", 2),
    ("User pauses for 1 minute", 60),
    ("User waits for 500 ms", 0.5),
    ("User waits 2 hours", 7200),
])
def test_hard_sleep_vocabulary(step, seconds):
    t, p = _match(step)
    assert t == "wait_seconds"
    assert p["seconds"] == seconds


def test_timed_page_load_is_a_hard_sleep():
    t, p = _match("User waits for the page to load : 20 seconds")
    assert t == "wait_seconds"
    assert p["seconds"] == 20


def test_timed_page_load_from_unresolved_var_ref():
    # {var:20 seconds} that survives substitution is canonicalized to
    # `20 seconds` by normalize_phrasing — still a hard sleep.
    t, p = _match(normalize_phrasing("waits for the page to load : {var:20 seconds}"))
    assert t == "wait_seconds"
    assert p["seconds"] == 20


def test_plain_page_load_wait_unchanged():
    t, _ = _match("User waits for the page to load")
    assert t == "wait_load"


# --- subject normalization ----------------------------------------------------

def test_normalize_subject_strips_a_user():
    assert normalize_subject("a user waits for the page to load") == \
        "waits for the page to load"


def test_normalize_subject_leaves_a_quoted_thing_alone():
    text = "a 'Promo modal' appears on the page, performs clicks 'Close'"
    assert normalize_subject(text) == text


# --- runner dispatch -----------------------------------------------------------

def _context():
    return SimpleNamespace(page=MagicMock(), _vars={})


def test_run_if_executes_inner_step_when_condition_visible(monkeypatch):
    monkeypatch.setattr(runner.actions, "is_visible", lambda page, text: True)
    click = MagicMock()
    monkeypatch.setattr(runner.actions, "click", click)

    runner.execute_step("User clicks 'Skip' if 'Tour popup' appears", _context())

    click.assert_called_once()
    assert click.call_args[0][1] == "Skip"


def test_run_if_skips_inner_step_when_condition_absent(monkeypatch):
    monkeypatch.setattr(runner.actions, "is_visible", lambda page, text: False)
    click = MagicMock()
    monkeypatch.setattr(runner.actions, "click", click)

    runner.execute_step("User clicks 'Skip' if 'Tour popup' appears", _context())

    click.assert_not_called()


def test_run_if_negated_runs_when_absent(monkeypatch):
    monkeypatch.setattr(runner.actions, "is_visible", lambda page, text: False)
    click = MagicMock()
    monkeypatch.setattr(runner.actions, "click", click)

    runner.execute_step("User clicks 'Open menu' if 'Sidebar' is not visible", _context())

    click.assert_called_once()


def test_run_if_inner_step_goes_through_full_resolver(monkeypatch):
    # The "then" text is any dictionary step — here a fill, not a click.
    monkeypatch.setattr(runner.actions, "is_visible", lambda page, text: True)
    fill = MagicMock()
    monkeypatch.setattr(runner.actions, "fill", fill)

    runner.execute_step(
        "User enters 'admin' in the username field if 'Login form' appears", _context())

    fill.assert_called_once()
    assert fill.call_args[0][1:] == ("username", "admin")


# --- is_visible probe -----------------------------------------------------------

def test_is_visible_true_when_found_and_visible(monkeypatch):
    from noodle.agents.web import actions
    loc = MagicMock()
    loc.first.is_visible.return_value = True
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: loc)
    assert actions.is_visible(MagicMock(), "Popup") is True


def test_is_visible_false_when_not_found(monkeypatch):
    from noodle.agents.web import actions
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: None)
    assert actions.is_visible(MagicMock(), "Ghost") is False


def test_is_visible_never_raises(monkeypatch):
    from noodle.agents.web import actions
    def boom(page, text):
        raise RuntimeError("detached")
    monkeypatch.setattr(actions, "find", boom)
    assert actions.is_visible(MagicMock(), "Flaky") is False

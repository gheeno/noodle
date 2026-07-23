"""NOOD_0088 — navigation gets the full NOODLE_FIND_TIMEOUT budget (one goto,
no retry — see NOOD_0092), and ordinary element lookups (click/fill/etc.)
poll for late-rendering content instead of failing on a single synchronous
check. No browser, no LLM, no network."""
from unittest.mock import MagicMock

from noodle.agents.web import actions, locator

# --- navigation gets the full NOODLE_FIND_TIMEOUT budget in one goto ---------
# NOOD_0092 — retrying goto() is a refresh that restarts a slow load, so
# navigate() makes one attempt with the whole find budget instead.

def test_navigate_uses_find_timeout(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "30000")
    page = MagicMock()
    actions.navigate(page, "https://example.com")
    page.goto.assert_called_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=30000
    )


def test_navigate_defaults_to_2min(monkeypatch):
    monkeypatch.delenv("NOODLE_FIND_TIMEOUT", raising=False)
    page = MagicMock()
    actions.navigate(page, "https://example.com")
    page.goto.assert_called_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=120000
    )


def test_reload_go_back_go_forward_use_noodle_timeout(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "20000")
    page = MagicMock()
    actions.reload(page)
    actions.go_back(page)
    actions.go_forward(page)
    page.reload.assert_called_once_with(wait_until="domcontentloaded", timeout=20000)
    page.go_back.assert_called_once_with(wait_until="domcontentloaded", timeout=20000)
    page.go_forward.assert_called_once_with(wait_until="domcontentloaded", timeout=20000)


# --- _poll_strategies: waits for late-rendering elements ---------------------

def test_poll_strategies_returns_immediately_on_first_match(monkeypatch):
    """The common case (element already there) pays no extra cost."""
    calls = []

    def fake_try_strategies(scope, text, prefer=None):
        calls.append(1)
        return "the_locator", False

    monkeypatch.setattr(locator, "_try_strategies", fake_try_strategies)
    loc, ambiguous = locator._poll_strategies(MagicMock(), "Login")
    assert loc == "the_locator"
    assert ambiguous is False
    assert len(calls) == 1


def test_poll_strategies_retries_until_element_appears(monkeypatch):
    """A spinner/async row that isn't in the DOM on the first pass is still
    found once it renders, instead of failing find() outright."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "2000")
    monkeypatch.setattr(locator.time, "sleep", lambda s: None)  # don't actually sleep in tests

    attempts = {"n": 0}

    def fake_try_strategies(scope, text, prefer=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return None, False
        return "found_it", False

    monkeypatch.setattr(locator, "_try_strategies", fake_try_strategies)
    loc, ambiguous = locator._poll_strategies(MagicMock(), "Submit")
    assert loc == "found_it"
    assert attempts["n"] == 3


def test_poll_strategies_gives_up_after_timeout(monkeypatch):
    """An element that never appears still fails — polling is bounded.
    NOOD_0089: the budget knob is NOODLE_FIND_TIMEOUT; the network-quiet
    extension and DOM re-scan are stubbed out so the bound is exact."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "100")
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)

    def fake_try_strategies(scope, text, prefer=None):
        return None, False

    monkeypatch.setattr(locator, "_try_strategies", fake_try_strategies)
    loc, ambiguous = locator._poll_strategies(MagicMock(), "Nonexistent")
    assert loc is None


# --- poll=False: absence probes stay one-shot ---------------------------------

def _no_poll_setup(monkeypatch):
    """Absent element everywhere; records whether the poll loop ran."""
    polled = {"n": 0}

    def fake_poll(scope, text, prefer=None, allow_dom_scan=True):
        polled["n"] += 1
        return None, False

    monkeypatch.setenv("NOODLE_TIMEOUT", "60000")
    monkeypatch.setattr(locator, "_poll_strategies", fake_poll)
    monkeypatch.setattr(locator, "_try_strategies", lambda scope, text, prefer=None: (None, False))
    monkeypatch.setattr(locator, "_vision_locate", lambda page, text: None)
    monkeypatch.setattr(locator.pom, "locate", lambda page, text: None)
    return polled


def test_find_poll_false_skips_poll_loop(monkeypatch):
    """Conditional (run_if) probes and REPL grounding ask 'is it there RIGHT
    NOW' — a miss must not block for NOODLE_TIMEOUT."""
    polled = _no_poll_setup(monkeypatch)
    assert locator.find(MagicMock(), "Nonexistent", poll=False) is None
    assert polled["n"] == 0


def test_find_default_still_polls(monkeypatch):
    polled = _no_poll_setup(monkeypatch)
    assert locator.find(MagicMock(), "Nonexistent") is None
    assert polled["n"] == 1


def test_is_visible_absent_does_not_poll(monkeypatch):
    polled = _no_poll_setup(monkeypatch)
    assert actions.is_visible(MagicMock(), "Nonexistent") is False
    assert polled["n"] == 0

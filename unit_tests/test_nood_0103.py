"""NOOD_0103 — multi-probe find() fallbacks short-circuit early probes.

The regression: an action written as "try key A, else key B" chained two
find() calls, and each doomed early probe polled the FULL smart-wait budget
(NOODLE_FIND_TIMEOUT, ~2 min) before falling through — so a page whose POM
defines 'search' but not 'searchbox' spent minutes on the doomed 'searchbox'
probe. The engine fix: find_first() gives every candidate but the last a single
cheap pass (heal=False, poll=False); only the final candidate pays the full
budget and the self-heal chain. No browser, no LLM, no network."""
from unittest.mock import MagicMock

from noodle.agents.web import actions, locator

# --- find_first: only the last candidate pays the full budget -----------------

def test_find_first_early_probe_is_cheap_and_short_circuits(monkeypatch):
    """The first candidate that matches wins, and it ran cheap: poll=False so no
    2-min budget, heal=False so no self-heal chain."""
    calls = []
    sentinel = object()

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True):
        calls.append((text, poll, heal))
        return sentinel if text == "searchbox" else None

    monkeypatch.setattr(locator, "find", fake_find)
    loc = locator.find_first(MagicMock(), ["searchbox", "search"])
    assert loc is sentinel
    # only the first probe ran, and it ran cheap
    assert calls == [("searchbox", False, False)]


def test_find_first_last_probe_gets_full_budget(monkeypatch):
    """When early probes miss, the LAST candidate is the one that gets the full
    smart-wait budget (poll=True) and the self-heal chain (heal=True)."""
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True):
        calls.append((text, poll, heal))
        return "hit" if text == "search" else None

    monkeypatch.setattr(locator, "find", fake_find)
    loc = locator.find_first(MagicMock(), ["searchbox", "search"])
    assert loc == "hit"
    assert calls == [("searchbox", False, False), ("search", True, True)]


def test_find_first_all_miss_returns_none(monkeypatch):
    """Nothing found anywhere → None, and no early probe was ever polled."""
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True):
        calls.append((text, poll, heal))
        return None

    monkeypatch.setattr(locator, "find", fake_find)
    assert locator.find_first(MagicMock(), ["a", "b", "c"]) is None
    assert [c[1:] for c in calls] == [(False, False), (False, False), (True, True)]


def test_find_first_single_candidate_gets_full_budget(monkeypatch):
    """A lone candidate is the last candidate — it must keep the full budget."""
    calls = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True):
        calls.append((text, poll, heal))
        return None

    monkeypatch.setattr(locator, "find", fake_find)
    locator.find_first(MagicMock(), ["only"])
    assert calls == [("only", True, True)]


def test_find_first_empty_is_none(monkeypatch):
    monkeypatch.setattr(locator, "find",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert locator.find_first(MagicMock(), []) is None


def test_find_first_threads_scope_and_prefer(monkeypatch):
    """scope/prefer reach every probe — a fallback chain inside a row/section or
    for a fill target must not lose those constraints."""
    seen = []

    def fake_find(page, text, scope=None, poll=True, prefer=None, heal=True):
        seen.append((text, scope, prefer))
        return None

    monkeypatch.setattr(locator, "find", fake_find)
    row = MagicMock()
    locator.find_first(MagicMock(), ["searchbox", "search"], scope=row, prefer="input")
    assert seen == [("searchbox", row, "input"), ("search", row, "input")]


# --- _find heal gate: the cheap pass skips the self-heal chain -----------------

def test_find_heal_false_skips_self_heal(monkeypatch):
    """heal=False: a miss returns None without scrolling, DOM-scanning, or asking
    the vision model — the expensive fallbacks are the final candidate's job."""
    page = MagicMock()
    monkeypatch.setattr(locator.pom, "is_explicit", lambda t: None)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, pr=None: (None, False))
    vision = {"n": 0}
    monkeypatch.setattr(locator, "_vision_locate",
                        lambda p, t: vision.update(n=vision["n"] + 1))

    loc = locator._find(page, "searchbox", poll=False, heal=False)
    assert loc is None
    page.mouse.wheel.assert_not_called()   # no scroll self-heal
    assert vision["n"] == 0                 # no vision round-trip


def test_find_heal_true_still_runs_self_heal(monkeypatch):
    """The default (heal=True) is unchanged: a miss still scrolls and retries,
    so nothing that resolved via self-heal before stops resolving."""
    page = MagicMock()
    monkeypatch.setattr(locator.pom, "is_explicit", lambda t: None)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, pr=None: (None, False))
    monkeypatch.setattr(locator, "_vision_locate", lambda p, t: None)
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)

    loc = locator._find(page, "searchbox", poll=False, heal=True)
    assert loc is None
    page.mouse.wheel.assert_called()       # scroll self-heal ran


def test_find_heal_false_never_polls(monkeypatch):
    """heal=False probes must not enter _poll_strategies — that is the 2-min
    budget the whole fix exists to avoid on a doomed early probe."""
    monkeypatch.setattr(locator.pom, "is_explicit", lambda t: None)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator, "_poll_strategies",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("polled")))
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, pr=None: (None, False))
    # poll=False is what find_first passes for early probes; assert it never polls
    assert locator._find(MagicMock(), "searchbox", poll=False, heal=False) is None


# --- settled-page early exit: general fix for ANY unresolvable label ----------

def _settle_env(monkeypatch, find_ms="60000", settle_ms="1"):
    """A poll that would grind for a minute, with the settle window already
    open and instant sampling — so tests exercise the exit, not wall clocks."""
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", find_ms)
    monkeypatch.setenv("NOODLE_SETTLE_TIMEOUT", settle_ms)
    monkeypatch.setattr(locator, "_SETTLE_SAMPLE_S", 0.0)
    monkeypatch.setattr(locator.time, "sleep", lambda s: None)
    monkeypatch.setattr(locator, "_try_strategies", lambda s, t, pr=None: (None, False))
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)


def test_poll_exits_early_when_page_settled(monkeypatch):
    """The NOOD_0103 headline: the page finished loading (network quiet, DOM
    stable) but the label can never resolve — the poll must not grind out the
    full NOODLE_FIND_TIMEOUT. Works for ANY label, not just search()'s."""
    import time as _time
    _settle_env(monkeypatch)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    monkeypatch.setattr(locator, "_dom_fingerprint", lambda s: "42:1000")
    start = _time.monotonic()
    loc, amb = locator._poll_strategies(MagicMock(), "renamed button")
    assert loc is None and amb is False
    assert _time.monotonic() - start < 5  # nowhere near the 60s budget


def test_poll_settle_disabled_by_zero(monkeypatch):
    """NOODLE_SETTLE_TIMEOUT=0 restores unconditional full-budget polling."""
    _settle_env(monkeypatch, find_ms="200", settle_ms="0")
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    sampled = {"n": 0}
    monkeypatch.setattr(locator, "_dom_fingerprint",
                        lambda s: sampled.update(n=sampled["n"] + 1) or "42:1000")
    loc, _ = locator._poll_strategies(MagicMock(), "anything")
    assert loc is None
    assert sampled["n"] == 0  # settle machinery never engaged


def test_poll_no_early_exit_while_network_busy(monkeypatch):
    """A page still fetching data is NOT settled — the poll keeps waiting, so a
    genuinely slow element that arrives late is still caught."""
    _settle_env(monkeypatch)
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: False)
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "0")
    monkeypatch.setattr(locator, "_dom_fingerprint", lambda s: "42:1000")
    attempts = {"n": 0}

    def late(scope, text, prefer=None):
        attempts["n"] += 1
        return ("arrived", False) if attempts["n"] >= 50 else (None, False)

    monkeypatch.setattr(locator, "_try_strategies", late)
    loc, _ = locator._poll_strategies(MagicMock(), "slow row")
    assert loc == "arrived"  # early exit never fired despite stable DOM


def test_poll_no_early_exit_while_dom_still_changing(monkeypatch):
    """Quiet network but a mutating DOM (JS still rendering) is NOT settled."""
    import time as _time
    _settle_env(monkeypatch, find_ms="300")
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    tick = {"n": 0}
    monkeypatch.setattr(locator, "_dom_fingerprint",
                        lambda s: str(tick.update(n=tick["n"] + 1) or tick["n"]))
    start = _time.monotonic()
    loc, _ = locator._poll_strategies(MagicMock(), "still rendering")
    assert loc is None
    assert _time.monotonic() - start >= 0.3  # ran to the (tiny) full budget


def test_poll_no_early_exit_when_fingerprint_unavailable(monkeypatch):
    """A scope that can't evaluate page JS (fingerprint None) never settles —
    the conservative fallback is the old full-budget behaviour."""
    import time as _time
    _settle_env(monkeypatch, find_ms="300")
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: True)
    monkeypatch.setattr(locator, "_dom_fingerprint", lambda s: None)
    start = _time.monotonic()
    loc, _ = locator._poll_strategies(MagicMock(), "in a locator scope")
    assert loc is None
    assert _time.monotonic() - start >= 0.3


def test_dom_fingerprint_none_on_evaluate_error():
    scope = MagicMock()
    scope.evaluate.side_effect = RuntimeError("Locator scope can't page-evaluate")
    assert locator._dom_fingerprint(scope) is None


# --- search() routes through find_first ---------------------------------------

def test_search_uses_find_first(monkeypatch):
    """search() no longer chains two full-budget find() calls; it hands the
    candidate order to find_first so the doomed 'searchbox' probe is cheap —
    and asks editable-first (prefer="input", NOOD_0106) so a search BUTTON
    whose accessible name says "search" can't take the fill."""
    seen = {}
    match = MagicMock()   # MagicMock.evaluate is truthy → counts as editable

    def fake_find_first(page, candidates, scope=None, prefer=None):
        seen["candidates"] = candidates
        seen["prefer"] = prefer
        return match

    monkeypatch.setattr(actions, "find_first", fake_find_first)
    actions.search(MagicMock(), "winter tires")
    assert seen["candidates"] == ["searchbox", "search"]
    assert seen["prefer"] == "input"
    match.fill.assert_called_once_with("winter tires")
    match.press.assert_called_once_with("Enter")


def test_search_falls_back_to_searchbox_role(monkeypatch):
    """When no candidate resolves, the role=searchbox fallback still runs."""
    monkeypatch.setattr(actions, "find_first", lambda p, c, scope=None, prefer=None: None)
    page = MagicMock()
    role_loc = MagicMock()
    role_loc.count.return_value = 1
    page.get_by_role.return_value = role_loc
    actions.search(page, "boots")
    page.get_by_role.assert_called_once_with("searchbox")
    role_loc.first.fill.assert_called_once_with("boots")
    role_loc.first.press.assert_called_once_with("Enter")

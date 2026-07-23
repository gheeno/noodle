"""NOOD_0152 — table-column dispatch regression.

The `assert_column_sorted` branch (NOOD_0143) was spliced into the *middle*
of the `assert_column_contains` branch in runner.execute_step, leaving:

    elif t == 'assert_column_contains':
        values = action.get('values')      # body ends here — NO-OP
    elif t == 'assert_column_sorted':
        actions.assert_column_sorted(...)
        if values is None:                 # `values` unbound — always raises
            ...
        actions.assert_column_contains(...)

Two independent failures, both invisible to the pattern tests (the steps
resolved correctly the whole time — only dispatch was wrong):

  1. `the 'Price' column should contain 'X'` asserted NOTHING and passed —
     a false green, the worst failure mode a test tool has.
  2. `the 'Price' column should be sorted descending` always died with
     UnboundLocalError after running its real check.

These tests go through the real resolver + dispatch, so a future edit that
re-nests the branches fails here. No browser, no LLM, no network.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions
from noodle.orchestrator import runner
from noodle.resolver.patterns import PATTERNS, match, normalize_phrasing, normalize_subject


def _r(text):
    """The exact pipeline resolve() uses — normalise, then match."""
    return match(normalize_phrasing(normalize_subject(text)))


def _ctx():
    return SimpleNamespace(page=MagicMock(), _vars={})


def test_column_contains_actually_asserts(monkeypatch):
    """The regression: this step used to be a silent no-op that always passed."""
    seen = {}
    monkeypatch.setattr(runner.actions, "assert_column_contains",
                        lambda page, column, values: seen.update(column=column, values=values))
    runner.execute_step("the 'Price' column should contain '$10.00'", _ctx())
    assert seen == {"column": "Price", "values": ["$10.00"]}


def test_column_contains_failure_propagates(monkeypatch):
    """A real mismatch must surface as a failure — the no-op swallowed these."""
    def boom(page, column, values):
        raise AssertionError("column mismatch")
    monkeypatch.setattr(runner.actions, "assert_column_contains", boom)
    with pytest.raises(AssertionError, match="column mismatch"):
        runner.execute_step("the 'Price' column should contain '$10.00'", _ctx())


def test_column_sorted_does_not_touch_values(monkeypatch):
    """assert_column_sorted must dispatch alone — it used to fall through into
    assert_column_contains' body and hit an unbound `values`."""
    seen = {}
    monkeypatch.setattr(runner.actions, "assert_column_sorted",
                        lambda page, column, descending: seen.update(column=column, desc=descending))
    monkeypatch.setattr(runner.actions, "assert_column_contains",
                        lambda *a, **k: pytest.fail("sorted must not call assert_column_contains"))
    runner.execute_step("the 'Price' column should be sorted descending", _ctx())
    assert seen == {"column": "Price", "desc": True}


def test_column_sorted_ascending_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.actions, "assert_column_sorted",
                        lambda page, column, descending: seen.update(desc=descending))
    runner.execute_step("the 'Name' column should be sorted", _ctx())
    assert seen == {"desc": False}


# =============================================================================
# NOOD_0152 — audit gap closure. Everything below is new vocabulary.
# =============================================================================

# --- SILENT MIS-ROUTES ------------------------------------------------------
# The most dangerous class found by the audit: these phrasings MATCHED some
# other pattern and did the wrong thing without any warning. A clean miss is
# recoverable (the editor flags it, the LLM fallback logs it); a confident
# wrong answer in a test framework is not. Each assertion below pins the step
# to the action it must reach, so a future reorder of PATTERNS fails here.

@pytest.mark.parametrize("step, expected_type", [
    # was: fill, typing the LITERAL string "today's date" into the box
    ("User enters today's date in the 'Start date' field",      "fill_date"),
    # was: right_click on a locator called "Row 1' and selects 'Delete"
    ("User right-clicks 'Row 1' and selects 'Delete'",          "context_menu_select"),
    # was: plain click, with the modifier baked into the locator text
    ("User clicks 'Row 2' while holding Shift",                 "click_modifier"),
    # was: long_press with locator "'Row 1' for 2 seconds"
    ("User long-presses 'Row 1' for 2 seconds",                 "long_press"),
    # was: assert_compare against the literal words "the downloaded file"
    ("the downloaded file should contain 'Invoice #123'",       "assert_download_content"),
    # was: switch_frame with name "inner' frame inside the 'outer"
    ("User switches to the 'inner' frame inside the 'outer' frame", "switch_frame_chain"),
    # was: store_text, hunting the DOM for an element named "clipboard"
    ("User stores the clipboard as `CLIP`",                     "store_clipboard"),
    # was: wait_visible, hunting for the literal text "response from '/api/x'"
    ("User waits for the response from '/api/orders'",          "wait_response"),
    # was: wait_visible on the sentence "the 'Save' button is enabled"
    ("User waits until the 'Save' button is enabled",           "wait_state"),
    # was: assert_count, hunting for text "serious accessibility violations"
    ("the page should have at most 3 serious accessibility violations", "assert_a11y"),
])
def test_former_mis_routes_now_reach_the_right_action(step, expected_type):
    resolved = _r(step)
    assert resolved is not None, f"{step!r} no longer resolves at all"
    assert resolved[0] == expected_type, f"{step!r} -> {resolved[0]}"


def test_email_steps_refuse_instead_of_string_comparing():
    """There is no mail adapter. Falling through to assert_compare would
    compare the literal words "the email" and produce an undiagnosable red,
    so the step must refuse AT RESOLUTION with a usable workaround."""
    with pytest.raises(AssertionError, match="no email adapter"):
        _r("the email should contain 'Verify your account'")
    with pytest.raises(AssertionError, match="no SMS adapter"):
        _r("the sms should contain '123456'")


# --- C1: BROWSER WINDOW -----------------------------------------------------

@pytest.mark.parametrize("step, w, h", [
    ("User resizes the browser to tablet width",  768, 1024),
    ("User resizes the browser to mobile size",   390, 844),
    ("User switches to desktop view",            1440, 900),
    ("User resizes the browser window to 800x600", 800, 600),
])
def test_named_breakpoints(step, w, h):
    assert _r(step) == ("set_viewport", {"width": w, "height": h})


@pytest.mark.parametrize("step, orientation", [
    ("User rotates the device to landscape", "landscape"),
    ("User rotates the screen to portrait",  "portrait"),
    ("User rotates the device",              None),
])
def test_rotate_patterns(step, orientation):
    assert _r(step) == ("rotate_viewport", {"orientation": orientation})


@pytest.mark.parametrize("start, want, expected", [
    ((390, 844), "landscape", (844, 390)),   # portrait -> landscape
    ((844, 390), "landscape", (844, 390)),   # already landscape: idempotent
    ((844, 390), "portrait",  (390, 844)),
    ((390, 844), "portrait",  (390, 844)),   # already portrait: idempotent
    ((800, 600), None,        (600, 800)),   # bare rotate always swaps
])
def test_rotate_transposes_live_viewport(start, want, expected):
    """Rotation reads the LIVE viewport so it composes with whatever size a
    prior step set, rather than hardcoding a device."""
    page = MagicMock()
    page.viewport_size = {"width": start[0], "height": start[1]}
    ctx = SimpleNamespace(page=page, _vars={})
    step = ("User rotates the device" if want is None
            else f"User rotates the device to {want}")
    runner.execute_step(step, ctx)
    page.set_viewport_size.assert_called_once_with(
        {"width": expected[0], "height": expected[1]})


def test_rotate_without_viewport_explains_why():
    page = MagicMock()
    page.viewport_size = None
    with pytest.raises(AssertionError, match="no viewport size"):
        runner.execute_step("User rotates the device", SimpleNamespace(page=page, _vars={}))


def test_assert_viewport_detects_a_resize_that_did_not_land():
    page = MagicMock()
    page.viewport_size = {"width": 1024, "height": 768}
    actions.assert_viewport(page, 1024, 768)              # passes
    actions.assert_viewport(page, 1024)                   # width-only form
    with pytest.raises(AssertionError, match="expected 800x600"):
        actions.assert_viewport(page, 800, 600)


# --- C2: COMPLEX INTERACTIONS (the mouse primitive) -------------------------

@pytest.mark.parametrize("step, expected", [
    ("User drags 'Card' by 100, 50",
     ("mouse_drag", {"locator": "Card", "dx": 100, "dy": 50})),
    ("User drags 'Card' by (100, -50)",
     ("mouse_drag", {"locator": "Card", "dx": 100, "dy": -50})),
    ("User drags 'split pane' 100 pixels right",
     ("mouse_drag", {"locator": "split pane", "dx": 100, "dy": 0})),
    ("User drags 'split pane' 100 pixels up",
     ("mouse_drag", {"locator": "split pane", "dx": 0, "dy": -100})),
    ("User resizes the 'sidebar' panel 120 pixels right",
     ("drag_edge", {"locator": "sidebar", "dx": 120, "dy": 0, "edge": "right"})),
    ("User drags 'Task A' to the 'Done' column",
     ("mouse_drag_to", {"source": "Task A", "target": "Done"})),
    ("User drags 'Item 1' above 'Item 3'",
     ("mouse_drag_to", {"source": "Item 1", "target": "Item 3"})),
    ("User drags the 'volume' slider to 75",
     ("set_slider", {"locator": "volume", "value": 75.0})),
    ("User ctrl-clicks 'Row 2'",
     ("click_modifier", {"locator": "Row 2", "modifiers": ["ctrl"]})),
])
def test_mouse_level_patterns(step, expected):
    assert _r(step) == expected


def test_mouse_drag_issues_real_press_move_release():
    """drag_to only synthesises HTML5 drag events; split panes and sliders
    listen for mousemove and ignore it. The order of events is the point."""
    page = MagicMock()
    calls = []
    page.mouse.move.side_effect = lambda *a, **k: calls.append(("move", a, k))
    page.mouse.down.side_effect = lambda *a, **k: calls.append(("down", a, k))
    page.mouse.up.side_effect = lambda *a, **k: calls.append(("up", a, k))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "_box",
                   lambda p, loc_t, w: {"x": 10, "y": 20, "width": 100, "height": 40})
        actions.mouse_drag(page, "Card", 30, -15)
    assert [c[0] for c in calls] == ["move", "down", "move", "up"]
    assert calls[0][1] == (60.0, 40.0)          # centre of the box
    assert calls[2][1] == (90.0, 25.0)          # centre + (dx, dy)
    assert calls[2][2]["steps"] > 1             # intermediate moves, not a jump


def test_drag_edge_grabs_the_border_not_the_centre():
    page = MagicMock()
    moves = []
    page.mouse.move.side_effect = lambda x, y, **k: moves.append((x, y))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "_box",
                   lambda p, loc_t, w: {"x": 0, "y": 0, "width": 200, "height": 100})
        actions.drag_edge(page, "sidebar", 40, 0, "right")
    assert moves[0] == (200, 50.0)              # right border, vertical centre
    assert moves[1] == (240, 50.0)


def test_drag_edge_rejects_an_unknown_edge():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "_box",
                   lambda p, loc_t, w: {"x": 0, "y": 0, "width": 10, "height": 10})
        with pytest.raises(AssertionError, match="Unknown edge"):
            actions.drag_edge(MagicMock(), "x", 1, 0, "diagonal")


def test_horizontal_resize_always_grabs_the_trailing_edge():
    """Dragging a panel LEFT still grabs its right border — that is where the
    handle lives; the direction only decides which way you pull."""
    from noodle.resolver.patterns import _edge_offset
    assert _edge_offset("sidebar", "80", "left")["edge"] == "right"
    assert _edge_offset("sidebar", "80", "right")["edge"] == "right"
    assert _edge_offset("sidebar", "80", "down")["edge"] == "bottom"
    assert _edge_offset("sidebar", "80", "left")["dx"] == -80


def test_modifier_aliases_normalise_to_playwright_names():
    page = MagicMock()
    loc = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, t: loc)
        actions.click_modifier(page, "Row 2", ["ctrl", "shift"])
    loc.click.assert_called_once_with(modifiers=["Control", "Shift"])


def test_slider_uses_the_native_setter_for_a_range_input():
    """Assigning .value alone updates the DOM but leaves React/Vue state
    stale, so the UI silently ignores the change."""
    page, loc = MagicMock(), MagicMock()
    loc.evaluate.return_value = True                     # is a range input
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, t: loc)
        actions.set_slider(page, "volume", 75)
    script = loc.evaluate.call_args_list[-1][0][0]
    assert "getOwnPropertyDescriptor" in script
    assert "new Event('input'" in script and "new Event('change'" in script


def test_custom_slider_without_aria_range_says_what_to_do_instead():
    page, loc = MagicMock(), MagicMock()
    loc.evaluate.return_value = False                    # not a range input
    loc.get_attribute.return_value = None                # no aria-valuemin/max
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, t: loc)
        with pytest.raises(AssertionError, match="Drag it by pixels instead"):
            actions.set_slider(page, "price", 50)


# --- WAITS (the flake fix) --------------------------------------------------

@pytest.mark.parametrize("step, expected", [
    ("User waits until the 'Save' button is enabled",
     ("wait_state", {"locator": "Save", "state": "enabled"})),
    ("User waits until 'Submit' is no longer disabled",
     ("wait_state", {"locator": "Submit", "state": "enabled"})),
    ("User waits until there are 10 'rows'",
     ("wait_count", {"locator": "rows", "count": 10, "op": ">="})),
    ("User waits until the 'total' changes from '10'",
     ("wait_text_change", {"locator": "total", "was": "10"})),
    ("User waits until the 'total' changes",
     ("wait_text_change", {"locator": "total", "was": None})),
])
def test_wait_patterns(step, expected):
    assert _r(step) == expected


def test_poll_until_reraises_the_last_real_failure():
    """A bare 'timed out' hides the cause; the last genuine AssertionError is
    what tells you why it never became ready."""
    page = MagicMock()
    def never():
        raise AssertionError("button is still disabled")
    with pytest.raises(AssertionError) as e:
        actions._poll_until(page, never, timeout_ms=1, describe="'Save' is enabled")
    assert "button is still disabled" in str(e.value)
    assert "'Save' is enabled" in str(e.value)


def test_poll_until_returns_as_soon_as_the_check_passes():
    page, state = MagicMock(), {"n": 0}
    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise AssertionError("not yet")
    actions._poll_until(page, flaky, timeout_ms=5000, describe="ready")
    assert state["n"] == 3


def test_wait_text_change_snapshots_before_polling():
    """With no explicit 'from' value it must compare against the text as it
    reads WHEN THE STEP STARTS, not a fixed literal."""
    page, seen = MagicMock(), iter(["10", "10", "11"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "get_text", lambda p, loc_t: next(seen))
        actions.wait_text_change(page, "total", timeout=5000)


# --- SCROLL / INFINITE LOAD -------------------------------------------------

def test_scroll_container_targets_the_element_not_the_page():
    page, loc = MagicMock(), MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, t: loc)
        actions.scroll_container(page, "sidebar", "bottom")
    assert "scrollHeight" in loc.evaluate.call_args[0][0]
    page.mouse.wheel.assert_not_called()


def test_scroll_until_visible_stops_when_the_text_appears():
    page = MagicMock()
    page.evaluate.return_value = 1000
    with pytest.MonkeyPatch.context() as mp:
        import noodle.agents.web.locator as _loc
        mp.setattr(_loc, "find", lambda p, t, poll=True: object())
        actions.scroll_until_visible(page, "Item 100")
    page.mouse.wheel.assert_not_called()      # found on the first check


def test_scroll_until_visible_fails_honestly_when_never_found():
    page = MagicMock()
    page.evaluate.return_value = 1000          # height never grows
    with pytest.MonkeyPatch.context() as mp:
        import noodle.agents.web.locator as _loc
        mp.setattr(_loc, "find", lambda p, t, poll=True: None)
        with pytest.raises(AssertionError, match="without finding"):
            actions.scroll_until_visible(page, "Item 100", max_scrolls=5)


def test_load_all_by_scrolling_stops_at_a_stable_height_without_failing():
    """The no-text form means 'load everything' — exhausting the list is
    success, not failure."""
    page = MagicMock()
    page.evaluate.return_value = 1000
    actions.scroll_until_visible(page, None, max_scrolls=10)


# --- ASSERTIONS -------------------------------------------------------------

def test_assert_matches_regex():
    page = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "get_text", lambda p, loc_t: "$1,234.56")
        actions.assert_matches(page, "total", r"^\$[0-9,]+\.[0-9]{2}$")
        with pytest.raises(AssertionError, match="does not match"):
            actions.assert_matches(page, "total", r"^[0-9]+$")


def test_assert_matches_rejects_an_invalid_regex_clearly():
    page = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "get_text", lambda p, loc_t: "x")
        with pytest.raises(AssertionError, match="Invalid regex"):
            actions.assert_matches(page, "total", "[unclosed")


def test_currency_format_shorthand_accepts_real_prices():
    import re as _re
    resolved = _r("the 'total' should be formatted as currency")
    rx = _re.compile(resolved[1]["pattern"])
    for good in ["$1,234.56", "£45.00", "1234.56", "$99"]:
        assert rx.search(good), good
    for bad in ["free", "N/A", "12.3456789abc"]:
        assert not rx.search(bad), bad


@pytest.mark.parametrize("actual, ok", [(100.0, True), (100.005, True),
                                        (99.99, True), (100.5, False)])
def test_number_tolerance(actual, ok):
    page = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "read_number", lambda p, loc_t: actual)
        if ok:
            actions.assert_number_tolerance(page, "balance", 100.0, 0.01)
        else:
            with pytest.raises(AssertionError, match="off by"):
                actions.assert_number_tolerance(page, "balance", 100.0, 0.01)


@pytest.mark.parametrize("actual, ok", [(10, True), (15, True), (20, True),
                                        (9.99, False), (20.01, False)])
def test_number_between_is_inclusive(actual, ok):
    page = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "read_number", lambda p, loc_t: actual)
        if ok:
            actions.assert_number_between(page, "amount", 10, 20)
        else:
            with pytest.raises(AssertionError, match="expected between"):
                actions.assert_number_between(page, "amount", 10, 20)


@pytest.mark.parametrize("n, count, op, ok", [
    (49, 50, "<", True), (50, 50, "<", False), (50, 50, "<=", True),
])
def test_request_count_budget(n, count, op, ok):
    page = MagicMock()
    reqs = [f"https://x/{i}" for i in range(n)]
    if ok:
        actions.assert_request_count(page, reqs, count, op)
    else:
        with pytest.raises(AssertionError, match="expected"):
            actions.assert_request_count(page, reqs, count, op)


def test_download_content_needs_an_actual_download():
    with pytest.raises(AssertionError, match="No file has been downloaded"):
        actions.assert_download_content(MagicMock(), [], needle="x")


def test_download_content_reads_the_file(tmp_path):
    f = tmp_path / "export.csv"
    f.write_text("id,name\n1,Alice\n2,Bob\n")
    dl = SimpleNamespace(path=lambda: str(f), suggested_filename="export.csv")
    page = MagicMock()
    actions.assert_download_content(page, [dl], needle="Alice")
    actions.assert_download_content(page, [dl], rows=2)     # header excluded
    with pytest.raises(AssertionError, match="does not contain"):
        actions.assert_download_content(page, [dl], needle="Charlie")
    with pytest.raises(AssertionError, match="expected 5"):
        actions.assert_download_content(page, [dl], rows=5)


def test_download_content_explains_a_binary_file(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\xfd")
    dl = SimpleNamespace(path=lambda: str(f), suggested_filename="report.pdf")
    with pytest.raises(AssertionError, match="not UTF-8 text"):
        actions.assert_download_content(MagicMock(), [dl], needle="Invoice")


# --- DATES ------------------------------------------------------------------

@pytest.mark.parametrize("step, offset", [
    ("User enters today's date in the 'Start date' field",           0),
    ("User enters tomorrow's date in the 'Check-in' field",          1),
    ("User enters yesterday's date in the 'From' field",            -1),
    ("User enters the date 3 days from now in the 'Start' field",    3),
    ("User enters the date 7 days ago in the 'Start' field",        -7),
])
def test_relative_date_offsets(step, offset):
    resolved = _r(step)
    assert resolved[0] == "fill_date"
    assert resolved[1]["offset_days"] == offset


def test_fill_date_uses_iso_for_a_native_date_input():
    """<input type=date> takes ISO on the wire whatever locale it displays."""
    from datetime import date
    page, loc, filled = MagicMock(), MagicMock(), {}
    loc.evaluate.return_value = True                      # native date input
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, t: loc)
        mp.setattr(actions, "fill", lambda p, loc_t, v: filled.update(v=v))
        actions.fill_date(page, "Start date", 0)
    assert filled["v"] == date.today().isoformat()


# --- STRUCTURAL GUARD -------------------------------------------------------

def test_every_pattern_action_is_dispatched():
    """The bug that opened NOOD_0152 was a dispatch defect that every pattern
    test still passed through. This is the structural check that catches the
    whole class: a pattern whose action has no branch in execute_step crashes
    at runtime, and a branch no pattern reaches is dead code."""
    import re as _re
    from pathlib import Path

    from noodle.resolver.desktop_patterns import PATTERNS as DESKTOP_PATTERNS
    from noodle.resolver.perf_patterns import PATTERNS as PERF_PATTERNS

    # NOOD_0155 — the wok tables (perf, desktop) dispatch through the same
    # execute_step, so the same structural guard spans them too.
    produced = {a for _, a, _ in PATTERNS if a != "_reject"}
    produced |= {a for _, a, _ in PERF_PATTERNS} | {a for _, a, _ in DESKTOP_PATTERNS}
    src = Path(runner.__file__).read_text()
    dispatched = set(_re.findall(r"\bt == '([a-z0-9_]+)'", src))
    assert not (produced - dispatched), \
        f"patterns produce actions with no dispatch: {sorted(produced - dispatched)}"
    assert not (dispatched - produced), \
        f"execute_step handles actions no pattern reaches: {sorted(dispatched - produced)}"

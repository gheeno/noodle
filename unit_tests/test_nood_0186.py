"""NOOD_0186 — the five audited web-coverage gaps.

1. HTML5 media (play/pause/mute/seek/volume + state/time/duration asserts)
2. Multi-file upload (one set_input_files call — repeats REPLACE each other)
3. Click-driven calendar pickers (pick_date drives the popup)
4. Hover-menu composites (menu_select: hover → reveal → click)
5. Native OS dialogs (print dialog / file picker) refuse at resolution

Routing goes through the real resolver pipeline; dispatch through the real
runner with the action monkeypatched. No browser, no LLM, no network.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.agents.web.actions import _parse_target_date
from noodle.orchestrator import runner
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
from noodle.resolver.step_resolver import VALID_TYPES


def _r(text):
    """The exact pipeline resolve() uses — normalise, then match."""
    return match(normalize_phrasing(normalize_subject(text)))


def _ctx():
    return SimpleNamespace(page=MagicMock(), _vars={})


# --- 1. HTML5 media routing --------------------------------------------------

@pytest.mark.parametrize("step, expected", [
    ("User plays the video", ("media_play", {"locator": None})),
    ("User plays the 'promo' video", ("media_play", {"locator": "promo"})),
    ("I play the video", ("media_play", {"locator": None})),
    ("User resumes the video", ("media_play", {"locator": None})),
    ("User pauses the 'promo' player", ("media_pause", {"locator": "promo"})),
    ("User mutes the video", ("media_mute", {"locator": None, "muted": True})),
    ("User unmutes the 'background' audio",
     ("media_mute", {"locator": "background", "muted": False})),
    ("User seeks the video to 1:30",
     ("media_seek", {"locator": None, "seconds": 90})),
    ("User seeks to 90 seconds",
     ("media_seek", {"locator": None, "seconds": 90.0})),
    ("User skips the 'intro' video to 42 seconds",
     ("media_seek", {"locator": "intro", "seconds": 42.0})),
    ("User sets the volume to 50%",
     ("media_volume", {"locator": None, "percent": 50.0})),
])
def test_media_action_routing(step, expected):
    assert _r(step) == expected


@pytest.mark.parametrize("step, expected", [
    ("the video should be playing",
     ("assert_media_state", {"locator": None, "state": "playing", "negate": False})),
    ("the 'promo' video is paused",
     ("assert_media_state", {"locator": "promo", "state": "paused", "negate": False})),
    ("the video should not be playing",
     ("assert_media_state", {"locator": None, "state": "playing", "negate": True})),
    ("the video should have ended",
     ("assert_media_state", {"locator": None, "state": "ended", "negate": False})),
    ("the 'background' audio should be muted",
     ("assert_media_state", {"locator": "background", "state": "muted", "negate": False})),
    ("the video should have played at least 5 seconds",
     ("assert_media_time", {"locator": None, "seconds": 5.0, "op": ">="})),
    ("the video should be 30 seconds long",
     ("assert_media_duration", {"locator": None, "seconds": 30.0})),
    ("the 'promo' video duration should be 30 seconds",
     ("assert_media_duration", {"locator": "promo", "seconds": 30.0})),
])
def test_media_assert_routing(step, expected):
    assert _r(step) == expected


def test_media_nouns_are_interchangeable():
    for noun in ("video", "audio", "player", "clip", "movie", "song",
                 "track", "podcast", "recording"):
        assert _r(f"User plays the {noun}")[0] == "media_play", noun


# --- 2. Multi-file upload ----------------------------------------------------

def test_upload_multi_routing():
    assert _r("User uploads 'a.png' and 'b.png' to the 'Photos' input") == \
        ("upload_multi", {"paths": ["a.png", "b.png"], "locator": "'Photos' input"})
    assert _r("User uploads 'a.png', 'b.png' and 'c.png' into the gallery") == \
        ("upload_multi", {"paths": ["a.png", "b.png", "c.png"], "locator": "gallery"})


def test_single_upload_not_shadowed():
    assert _r("User uploads 'a.png' to the 'Photos' input") == \
        ("upload", {"path": "a.png", "locator": "'Photos' input"})


def test_upload_multi_dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.actions, "upload_multi",
                        lambda page, locator, paths: seen.update(locator=locator, paths=paths))
    runner.execute_step("uploads 'a.png' and 'b.png' to the gallery", _ctx())
    assert seen == {"locator": "gallery", "paths": ["a.png", "b.png"]}


# --- 3. Calendar pickers -----------------------------------------------------

def test_pick_date_routing():
    assert _r("User picks 'March 15, 2026' from the 'Departure' calendar") == \
        ("pick_date", {"date": "March 15, 2026", "locator": "Departure"})
    assert _r("User picks '2026-03-15' in the 'Start' date picker") == \
        ("pick_date", {"date": "2026-03-15", "locator": "Start"})
    assert _r("User selects tomorrow from the 'Check-in' calendar") == \
        ("pick_date", {"locator": "Check-in", "offset_days": 1})
    assert _r("User selects today from the 'Appointment' date picker") == \
        ("pick_date", {"locator": "Appointment", "offset_days": 0})


def test_fill_date_not_shadowed():
    """Typing a relative date into a plain field is still fill_date."""
    action, params = _r("User enters today's date in the 'Start date' field")
    assert action == "fill_date"
    assert params["offset_days"] == 0


def test_dropdown_select_not_shadowed():
    """'from the X' without a calendar noun is still the dropdown select."""
    assert _r("User selects 'Action' from the genre") == \
        ("select", {"value": "Action", "locator": "genre"})


@pytest.mark.parametrize("text, iso", [
    ("2026-03-15", "2026-03-15"),
    ("March 15, 2026", "2026-03-15"),
    ("Mar 15, 2026", "2026-03-15"),
    ("15 March 2026", "2026-03-15"),
    ("03/15/2026", "2026-03-15"),
])
def test_parse_target_date_formats(text, iso):
    assert _parse_target_date(text).isoformat() == iso


def test_parse_target_date_rejects_garbage():
    with pytest.raises(AssertionError, match="Accepted formats"):
        _parse_target_date("the twelfth of never")


def test_pick_date_dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        runner.actions, "pick_date",
        lambda page, locator, date=None, offset_days=None:
        seen.update(locator=locator, date=date, offset_days=offset_days))
    runner.execute_step("picks 'March 15, 2026' from the 'Departure' calendar", _ctx())
    assert seen == {"locator": "Departure", "date": "March 15, 2026", "offset_days": None}


# --- 4. Hover menus ----------------------------------------------------------

@pytest.mark.parametrize("step, trigger, item", [
    ("User opens the 'Products' menu and clicks 'Shoes'", "Products", "Shoes"),
    ("User hovers over the 'Account' menu and selects 'Settings'", "Account", "Settings"),
    ("User clicks 'Shoes' in the 'Products' menu", "Products", "Shoes"),
    ("User selects 'Docs' from the 'Resources' dropdown menu", "Resources", "Docs"),
])
def test_menu_select_routing(step, trigger, item):
    assert _r(step) == ("menu_select", {"trigger": trigger, "item": item})


def test_navigate_and_hover_not_shadowed():
    assert _r("User opens 'https://x.com'") == ("navigate", {"url": "https://x.com"})
    assert _r("User hovers over the profile menu") == ("hover", {"locator": "profile menu"})
    # a quoted DOM section is still the scoped click, not a menu
    assert _r("User clicks 'Save' in the 'Settings' section") == \
        ("click_in_section", {"locator": "Save", "section": "Settings"})


def test_menu_select_dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.actions, "menu_select",
                        lambda page, trigger, item: seen.update(trigger=trigger, item=item))
    runner.execute_step("opens the 'Products' menu and clicks 'Shoes'", _ctx())
    assert seen == {"trigger": "Products", "item": "Shoes"}


# --- 5. Native OS dialogs refuse at resolution -------------------------------

@pytest.mark.parametrize("step", [
    "the print dialog should appear",
    "User clicks 'Save' in the print dialog",
    "User cancels the print preview",
])
def test_print_dialog_refused(step):
    with pytest.raises(AssertionError, match="OS chrome"):
        _r(step)


@pytest.mark.parametrize("step", [
    "User selects 'photo.png' in the file picker",
    "User closes the file chooser",
    "User clicks 'Open' in the save file dialog",
])
def test_file_picker_refused(step):
    with pytest.raises(AssertionError, match="OS file picker"):
        _r(step)


def test_prints_the_page_maps_to_pdf_export():
    assert _r("User prints the page") == ("save_pdf", {"path": "printed-page.pdf"})
    assert _r("User prints the page as 'invoice.pdf'") == \
        ("save_pdf", {"path": "invoice.pdf"})


# --- LLM-fallback mirror (VALID_TYPES) ---------------------------------------

def test_new_types_mirrored_for_llm_fallback():
    for t in ("media_play", "media_pause", "media_mute", "media_seek",
              "media_volume", "assert_media_state", "assert_media_time",
              "assert_media_duration", "upload_multi", "pick_date",
              "menu_select"):
        assert t in VALID_TYPES, t


# --- media dispatch ----------------------------------------------------------

def test_media_dispatch(monkeypatch):
    calls = []
    for name in ("media_play", "media_pause", "media_mute", "media_seek",
                 "media_volume", "assert_media_state", "assert_media_time",
                 "assert_media_duration"):
        monkeypatch.setattr(runner.actions, name,
                            lambda *a, _n=name, **kw: calls.append((_n, a[1:], kw)))
    runner.execute_step("plays the 'promo' video", _ctx())
    runner.execute_step("seeks the video to 1:30", _ctx())
    runner.execute_step("the video should not be playing", _ctx())
    assert calls[0] == ("media_play", ("promo",), {})
    assert calls[1] == ("media_seek", (90, None), {})
    assert calls[2] == ("assert_media_state", ("playing", None, True), {})


# --- typeahead debounce race (regression-benchmark TC1) ----------------------

def test_rows_match_term_rejects_stale_prefix_list():
    # The exact TC1 failure: "Vacuum cleane" typed, "Va" response captured.
    stale = [{"text": "Vanuatu Country in Oceania"},
             {"text": "Van Halen American rock band"},
             {"text": "Vancouver City in British Columbia, Canada"}]
    from noodle.agents.web.probe import _rows_match_term
    assert not _rows_match_term(stale, "Vacuum cleane")


def test_rows_match_term_accepts_partial_word():
    # Partial typing ("cleane") must still anchor on the full row ("cleaner").
    from noodle.agents.web.probe import _rows_match_term
    assert _rows_match_term(
        [{"text": "Vacuum cleaner Device that sucks up dirt"}], "cleane")


def test_rows_match_term_short_term_anchors_nothing():
    # A ≤2-char term has no content word to anchor on — accept what's shown.
    from noodle.agents.web.probe import _rows_match_term
    assert _rows_match_term([{"text": "Vanuatu"}], "Va")


# --- author --overwrite reaches the engine (CLI/MCP parity) ------------------

def test_author_prompt_overwrite_flag_reaches_engine(monkeypatch):
    from typer.testing import CliRunner

    from noodle import cli
    from noodle.repl import core
    seen = {}
    monkeypatch.setattr(core, "author_test",
                        lambda **kw: seen.update(kw) or {"ok": True})
    CliRunner().invoke(cli.app, ["author", "--prompt",
                                 "1. go to https://x.test", "--overwrite"])
    assert seen.get("overwrite") is True

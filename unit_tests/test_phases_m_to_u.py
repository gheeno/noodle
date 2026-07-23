"""Phases M–U + F/G/H/I/J/K/L (2026-07) — pattern coverage, pure helpers,
app lifecycle, OCR phrase matching, per-pool LLM caps, CLI/LSP DX."""
import os
import time

import pytest

from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))


# --- Phase M — console & network error visibility ---------------------------

def test_console_error_patterns():
    assert _resolve("no console errors should be logged") == ("assert_no_console_errors", {})
    assert _resolve("no uncaught JS errors should occur") == ("assert_no_page_errors", {})
    assert _resolve("no network requests should fail") == ("assert_no_failed_requests", {})


def test_assert_none_captured_lists_items():
    from noodle.agents.web.actions import _assert_none_captured
    _assert_none_captured([], "console errors", "http://x")   # empty → no raise
    with pytest.raises(AssertionError, match="TypeError: boom"):
        _assert_none_captured(["TypeError: boom"], "console errors", "http://x")


# --- Phase L — request assertion + soft assertions ---------------------------

def test_request_made_pattern():
    assert _resolve("a request to '**/api/cart*' should have been made") == \
        ("assert_request_made", {"url": "**/api/cart*"})
    assert _resolve("all soft assertions should pass") == ("soft_assert_check", {})


def test_assert_request_made_substring_and_glob():
    from noodle.agents.web.actions import assert_request_made

    class _P:  # no polling needed when the URL is already there
        def wait_for_timeout(self, ms): pass

    assert_request_made(_P(), ["https://x/api/cart?id=1"], "/api/cart")
    assert_request_made(_P(), ["https://x/api/cart?id=1"], "*api/cart*")

    class _Deadline:
        def wait_for_timeout(self, ms): pass

    os.environ["NOODLE_TIMEOUT"] = "1"
    try:
        with pytest.raises(AssertionError, match="No request to"):
            assert_request_made(_Deadline(), ["https://x/other"], "/api/cart")
    finally:
        os.environ["NOODLE_TIMEOUT"] = "10000"


# --- Phase N — emulation tags/env + runtime steps ----------------------------

def test_emulation_patterns():
    assert _resolve("sets geolocation to '51.5,-0.12'") == \
        ("set_geolocation", {"coords": "51.5,-0.12"})
    assert _resolve("User grants permission 'notifications'") == \
        ("grant_permissions", {"permissions": "notifications"})


def test_dismiss_permission_prompt_patterns():
    # the exact phrasing from the ticket, subject stripped like any other step
    assert _resolve("the user closes the location prompt") == \
        ("dismiss_permission_prompt", {"permission": "location"})
    assert _resolve("User dismisses the geolocation pop-up") == \
        ("dismiss_permission_prompt", {"permission": "geolocation"})
    assert _resolve("closes the notifications prompt") == \
        ("dismiss_permission_prompt", {"permission": "notifications"})
    # regressions: neighbouring close/dismiss patterns keep their meaning
    assert _resolve("closes the popups") == ("close_popups", {})
    assert _resolve("dismisses the prompt") == \
        ("arm_dialog", {"response": "dismiss", "answer": None})


def test_dismiss_permission_prompt_denies_via_cdp_on_chromium():
    from noodle.agents.web.actions import dismiss_permission_prompt

    sent = []

    class _Cdp:
        def send(self, method, params):
            sent.append((method, params))

    class _Ctx:
        def new_cdp_session(self, page):
            return _Cdp()

    class _Page:
        url = "https://www.example.com/en.html?q=1"
        context = _Ctx()

    dismiss_permission_prompt(_Page(), "location")
    assert sent == [("Browser.setPermission", {
        "permission": {"name": "geolocation"},
        "setting": "denied",
        "origin": "https://www.example.com",
    })]


def test_dismiss_permission_prompt_noop_on_firefox_webkit():
    from noodle.agents.web.actions import dismiss_permission_prompt

    # engine is detected up front (NOOD_0122): firefox/webkit is a no-op without
    # ever touching CDP — new_cdp_session would blow up if it were reached.
    class _Ctx:
        browser = type("B", (), {"browser_type": type("T", (), {"name": "firefox"})()})()

        def new_cdp_session(self, page):
            raise AssertionError("must not reach CDP on firefox/webkit")

    class _Page:
        url = "https://example.com/"
        context = _Ctx()

    # no native prompt exists there — the step must pass, not raise
    dismiss_permission_prompt(_Page(), "geolocation")


def test_dismiss_permission_prompt_unknown_kind_raises():
    from noodle.agents.web.actions import dismiss_permission_prompt
    with pytest.raises(AssertionError, match="Unknown permission prompt"):
        dismiss_permission_prompt(None, "bluetooth")


def test_emulation_opts_from_tags(monkeypatch):
    from noodle.hooks import _emulation_opts
    for var in ("NOODLE_GEOLOCATION", "NOODLE_PERMISSIONS", "NOODLE_LOCALE",
                "NOODLE_TIMEZONE", "NOODLE_COLOR_SCHEME", "NOODLE_OFFLINE"):
        monkeypatch.delenv(var, raising=False)
    opts = _emulation_opts({
        "geo:51.5,-0.12", "permissions:geolocation,notifications",
        "locale:fr-FR", "timezone:America/New_York", "color_scheme:dark",
        "offline",
    })
    assert opts["geolocation"] == {"latitude": 51.5, "longitude": -0.12}
    assert opts["permissions"] == ["geolocation", "notifications"]
    assert opts["locale"] == "fr-FR"
    assert opts["timezone_id"] == "America/New_York"
    assert opts["color_scheme"] == "dark"
    assert opts["offline"] is True


def test_emulation_opts_env_fallback_and_default(monkeypatch):
    from noodle.hooks import _emulation_opts
    monkeypatch.setenv("NOODLE_LOCALE", "de-DE")
    monkeypatch.delenv("NOODLE_OFFLINE", raising=False)
    opts = _emulation_opts(set())
    assert opts.get("locale") == "de-DE"
    assert "offline" not in opts
    assert "geolocation" not in opts


def test_emulation_opts_bad_geo_raises():
    from noodle.hooks import _emulation_opts
    with pytest.raises(ValueError, match="expected 'lat,lon'"):
        _emulation_opts({"geo:not-a-coord"})


# --- Phase O — offline & throttling -------------------------------------------

def test_offline_and_throttle_patterns():
    assert _resolve("User goes offline") == ("set_offline", {"offline": True})
    assert _resolve("User goes back online") == ("set_offline", {"offline": False})
    assert _resolve("goes back") == ("go_back", {})   # regression: history nav intact
    assert _resolve("throttles the network to 'slow-3g'") == \
        ("throttle_network", {"profile": "slow-3g"})


def test_throttle_unknown_profile():
    from noodle.agents.web.actions import throttle_network
    with pytest.raises(AssertionError, match="Unknown throttling profile"):
        throttle_network(None, "warp-speed")


# --- Phase P — accessibility ---------------------------------------------------

def test_a11y_patterns():
    assert _resolve("the page should have no accessibility violations") == \
        ("assert_a11y", {"impact": None})
    assert _resolve("the page should have no critical accessibility violations") == \
        ("assert_a11y", {"impact": "critical"})
    assert _resolve("User should see at most 3 accessibility violations") == \
        ("assert_a11y", {"impact": None, "max": 3})


def test_filter_violations_threshold():
    from noodle.agents.web.actions import filter_violations
    violations = [{"impact": "minor"}, {"impact": "moderate"},
                  {"impact": "serious"}, {"impact": "critical"}, {"impact": None}]
    assert len(filter_violations(violations, None)) == 5
    assert [v["impact"] for v in filter_violations(violations, "serious")] == \
        ["serious", "critical"]
    assert [v["impact"] for v in filter_violations(violations, "critical")] == ["critical"]


def test_vendored_axe_exists():
    from noodle.agents.web.a11y import _AXE_PATH
    assert _AXE_PATH.is_file() and _AXE_PATH.stat().st_size > 100_000


# --- Phase Q — clipboard --------------------------------------------------------

def test_clipboard_patterns():
    assert _resolve("User copies 'hello' to the clipboard") == \
        ("write_clipboard", {"text": "hello"})
    assert _resolve("the clipboard should contain 'https://x'") == \
        ("assert_clipboard", {"text": "https://x"})


# --- Phase R — websockets --------------------------------------------------------

def test_ws_patterns():
    assert _resolve("a websocket message containing 'order' should be sent") == \
        ("assert_ws_message", {"contains": "order", "direction": "sent"})
    assert _resolve("a websocket message containing 'tick' should have been received") == \
        ("assert_ws_message", {"contains": "tick", "direction": "received"})


def test_assert_ws_message_matches_and_fails():
    from noodle.agents.web.actions import assert_ws_message
    frames = [{"url": "ws://x", "direction": "received", "payload": '{"e":"tick"}'},
              {"url": "ws://x", "direction": "sent", "payload": b"subscribe"}]
    assert_ws_message(None, frames, "tick", "received")
    assert_ws_message(None, frames, "subscribe", None)     # bytes decode
    with pytest.raises(AssertionError, match="No websocket message"):
        assert_ws_message(None, frames, "tick", "sent")    # wrong direction


# --- Phase S — print / PDF --------------------------------------------------------

def test_print_pdf_patterns():
    assert _resolve("User emulates print media") == ("emulate_media", {"media": "print"})
    assert _resolve("saves the page as pdf 'reports/out.pdf'") == \
        ("save_pdf", {"path": "reports/out.pdf"})


# --- Phase J — multi-user contexts --------------------------------------------------

def test_multi_context_patterns():
    assert _resolve("a new browser context as 'buyer'") == ("new_context", {"name": "buyer"})
    assert _resolve("acting as 'buyer'") == ("use_context", {"name": "buyer"})
    assert _resolve("User switches to the 'seller' context") == \
        ("use_context", {"name": "seller"})
    # regression: frame/tab switching still owns its phrases
    assert _resolve("switches to the 'pay' frame") == ("switch_frame", {"name": "pay"})
    assert _resolve("switches to the new tab") == ("switch_tab", {"target": "new"})


# --- Phase G4 — app lifecycle ---------------------------------------------------------

def test_app_lifecycle_patterns():
    assert _resolve("User launches the app 'python -m http.server 8000'") == \
        ("app_launch", {"command": "python -m http.server 8000"})
    assert _resolve("the app should be running") == ("app_assert_running", {"port": None})
    assert _resolve("the app should be running on port 8000") == \
        ("app_assert_running", {"port": 8000})
    assert _resolve("User stops the app") == ("app_stop", {})


def test_app_lifecycle_launch_assert_stop():
    import sys

    from noodle import app_lifecycle
    app_lifecycle.launch(f"{sys.executable} -c \"import time; time.sleep(30)\"")
    try:
        app_lifecycle.assert_running()
    finally:
        app_lifecycle.stop_all()
    assert not app_lifecycle._procs


def test_app_lifecycle_dead_process_fails():
    import sys

    from noodle import app_lifecycle
    app_lifecycle.launch(f"{sys.executable} -c pass")
    time.sleep(0.5)
    try:
        with pytest.raises(AssertionError, match="exited early"):
            app_lifecycle.assert_running()
    finally:
        app_lifecycle.stop_all()


def test_app_lifecycle_no_launch_fails():
    from noodle import app_lifecycle
    app_lifecycle.stop_all()
    with pytest.raises(AssertionError, match="No app was launched"):
        app_lifecycle.assert_running()


# --- Phase F — mobile patterns + capabilities loader -------------------------------------

def test_mobile_patterns():
    assert _resolve("User swipes left") == ("swipe", {"direction": "left"})
    assert _resolve("User presses the back button") == ("device_key", {"key": "back"})
    assert _resolve("User presses the home button") == ("device_key", {"key": "home"})
    # regression: ordinary buttons still click
    assert _resolve("User presses the Login button") == ("click", {"locator": "Login"})


def test_appium_caps_loader(tmp_path):
    from noodle.agents.mobile.driver import load_capabilities
    caps = load_capabilities('{"platformName": "Android"}')
    assert caps == {"platformName": "Android"}
    p = tmp_path / "caps.json"
    p.write_text('{"platformName": "iOS"}')
    assert load_capabilities(str(p)) == {"platformName": "iOS"}
    with pytest.raises(AssertionError, match="not set"):
        load_capabilities(None)
    with pytest.raises(AssertionError, match="not valid JSON"):
        load_capabilities("{nope")
    with pytest.raises(AssertionError, match="missing file"):
        load_capabilities("/no/such/caps.json")


# --- Phase G2 — multi-word OCR phrase matching ----------------------------------------------

def _ocr_data(words):
    """Hand-built image_to_data dict: words = [(text, block, par, line, l, t, w, h)]."""
    return {
        "text":      [w[0] for w in words],
        "conf":      [90] * len(words),
        "block_num": [w[1] for w in words],
        "par_num":   [w[2] for w in words],
        "line_num":  [w[3] for w in words],
        "left":      [w[4] for w in words],
        "top":       [w[5] for w in words],
        "width":     [w[6] for w in words],
        "height":    [w[7] for w in words],
    }


def test_pick_phrase_spans_words():
    from noodle.agents.visual.ocr import _pick_phrase
    data = _ocr_data([
        ("File", 1, 1, 1, 0, 0, 40, 20),
        ("Save", 1, 1, 2, 0, 30, 40, 20),
        ("As", 1, 1, 2, 50, 30, 20, 20),
    ])
    x, y = _pick_phrase(data, "Save As")
    assert (x, y) == (35, 40)         # union bbox (0..70, 30..50) centre


def test_pick_phrase_single_word_and_miss():
    from noodle.agents.visual.ocr import _pick_phrase
    data = _ocr_data([("Cancel", 1, 1, 1, 100, 200, 60, 20)])
    assert _pick_phrase(data, "cancel") == (130, 210)
    assert _pick_phrase(data, "Save As") is None
    assert _pick_phrase(data, "   ") is None


def test_pick_phrase_ignores_other_lines():
    from noodle.agents.visual.ocr import _pick_phrase
    data = _ocr_data([
        ("Save", 1, 1, 1, 0, 0, 40, 20),
        ("As", 2, 1, 1, 50, 100, 20, 20),   # different block — not the phrase
    ])
    assert _pick_phrase(data, "Save As") is None


# --- Phase I — per-pool LLM call caps ---------------------------------------------------------

def test_llm_caps_are_separate_pools(monkeypatch):
    from noodle.llm import client
    client.reset_calls()
    monkeypatch.setenv("NOODLE_LLM_MAX_CALLS", "1")
    monkeypatch.setenv("NOODLE_RCA_MAX_CALLS", "1")
    client._check_cap()                                   # LLM pool: 1/1
    client._check_cap("NOODLE_RCA_MAX_CALLS")             # RCA pool: 1/1
    with pytest.raises(AssertionError, match="NOODLE_LLM_MAX_CALLS"):
        client._check_cap()
    with pytest.raises(AssertionError, match="NOODLE_RCA_MAX_CALLS"):
        client._check_cap("NOODLE_RCA_MAX_CALLS")
    client.reset_calls()
    client._check_cap()                                   # reset clears both


# --- Phase K — step retry helpers --------------------------------------------------------------

def test_step_retries_env_and_tag(monkeypatch):
    from noodle.steps.catch_all import _step_retries
    monkeypatch.delenv("NOODLE_STEP_RETRIES", raising=False)
    assert _step_retries(set()) == 0
    assert _step_retries({"retry_step"}) == 1
    monkeypatch.setenv("NOODLE_STEP_RETRIES", "3")
    assert _step_retries(set()) == 3
    assert _step_retries({"retry_step"}) == 3


def test_run_with_retries_retries_assertions_only(monkeypatch):
    from noodle import healing
    from noodle.steps import catch_all
    healing.reset()
    calls = {"n": 0}

    def flaky(step_text, context):
        calls["n"] += 1
        if calls["n"] < 2:
            raise AssertionError("flap")

    monkeypatch.setattr(catch_all, "execute_step", flaky)
    catch_all._run_with_retries("When flaky", None, retries=1)
    assert calls["n"] == 2
    assert any(e["strategy"] == "step-retry" for e in healing.EVENTS)

    def broken(step_text, context):
        raise ValueError("not retryable")

    monkeypatch.setattr(catch_all, "execute_step", broken)
    with pytest.raises(ValueError):
        catch_all._run_with_retries("When broken", None, retries=5)
    healing.reset()


# --- Phase T — OCR fallback gating ----------------------------------------------------------------

def test_ocr_fallback_flag(monkeypatch):
    from noodle.agents.web import locator
    monkeypatch.delenv("NOODLE_OCR_FALLBACK", raising=False)
    locator.set_ocr_fallback(None)
    assert locator._is_ocr_fallback() is False
    monkeypatch.setenv("NOODLE_OCR_FALLBACK", "true")
    assert locator._is_ocr_fallback() is True
    locator.set_ocr_fallback(False)                # tag wins over env
    assert locator._is_ocr_fallback() is False
    locator.set_ocr_fallback(None)


# --- Phase U — steps CLI index + LSP hover -----------------------------------------------------------

def test_example_index_has_sections_and_types():
    from noodle.resolver.step_resolver import example_index
    index = example_index()
    assert index, "docs/steps_dictionary.md should yield examples"
    clicks = [e for e in index if e["type"] == "click"]
    assert clicks and all(e["section"] for e in clicks)


def test_hover_markdown_known_and_unknown():
    from noodle.lsp.server import _hover_markdown
    md = _hover_markdown("    When User clicks the 'Login' button")
    assert "click" in md and "Examples" in md
    md = _hover_markdown("    When User does something impossible to parse")
    assert "LLM" in md
    assert _hover_markdown("Feature: nope") is None


def test_soft_report_not_collected_as_soft():
    from noodle.orchestrator.runner import SoftAssertionReport
    assert issubclass(SoftAssertionReport, AssertionError)

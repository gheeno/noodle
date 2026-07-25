"""NOOD_0181 — mobile-web capability gaps found reviewing the framework after
NOOD_0180's scroll-assertion miss.

The theme: @mobile already built a genuinely touch-capable browser context
(maxTouchPoints 1, pointer:coarse, mobile UA, device DPR), but the step
language could not reach it — swipe/long-press hard-failed with "tag @appium",
and every click went out as a mouse event. These tests pin that the capability
is now reachable AND that the width-only impostor is called out.
"""
import pytest

from noodle import hooks, wok
from noodle.agents.web import actions

playwright = pytest.importorskip("playwright.sync_api")


# --- Gap 6: the device roster is Playwright's, not two hardcoded names -------

class _Devices(dict):
    """Stand-in for playwright.devices — only the keys matter here."""


_DEVICES = _Devices({
    "iPhone 13": {"has_touch": True}, "Pixel 5": {"has_touch": True},
    "Pixel 7": {"has_touch": True}, "iPhone 15 Pro": {"has_touch": True},
})


def test_device_tag_resolves_any_playwright_preset():
    # Gherkin tags cannot contain spaces — underscores and hyphens both work.
    assert hooks._device_from(["device:pixel_7"], _DEVICES) == "Pixel 7"
    assert hooks._device_from(["device:iphone-15-pro"], _DEVICES) == "iPhone 15 Pro"
    assert hooks._device_from(["device:IPHONE 15 PRO"], _DEVICES) == "iPhone 15 Pro"


def test_mobile_tag_keeps_its_historic_defaults():
    assert hooks._device_from(["mobile"], _DEVICES) == "Pixel 5"
    assert hooks._device_from(["mobile", "iphone"], _DEVICES) == "iPhone 13"
    assert hooks._device_from(["web"], _DEVICES) is None


def test_unknown_device_fails_loudly_with_suggestions():
    with pytest.raises(ValueError) as e:
        hooks._device_from(["device:pixel_99"], _DEVICES)
    assert "pixel_99" in str(e.value) and "Pixel 5" in str(e.value)


# --- Gap 1/2: touch is only claimed when the context actually has it ---------

class _FakePage:
    def __init__(self, touch_points):
        self._tp = touch_points

    def evaluate(self, *_a, **_k):
        return self._tp


def test_has_touch_reads_the_real_context():
    assert actions.has_touch(_FakePage(1)) is True
    assert actions.has_touch(_FakePage(0)) is False


def test_has_touch_is_false_for_a_page_that_cannot_answer():
    """A unit-test fake whose evaluate() returns a Mock is truthy — if that
    read as touch, every click in the suite would silently route to tap()."""
    class _Mocky:
        def evaluate(self, *_a, **_k):
            return object()

    class _Boom:
        def evaluate(self, *_a, **_k):
            raise RuntimeError("no page")

    assert actions.has_touch(_Mocky()) is False
    assert actions.has_touch(_Boom()) is False


def test_gesture_without_touch_points_at_mobile_not_appium():
    """The old error told testers to tag @appium — i.e. go buy a device — for
    something an emulated browser does. It must name @mobile first now."""
    with pytest.raises(AssertionError) as e:
        actions.swipe_web(_FakePage(0), "up")
    msg = str(e.value)
    assert "@mobile" in msg
    assert "viewport alone" in msg          # the width-only trap, named


# --- Gap 3: a narrow viewport is not mobile emulation -----------------------

class _ViewportPage(_FakePage):
    """Desktop context (no touch) that records what it was resized to."""
    def __init__(self):
        super().__init__(0)
        self.size = None

    def set_viewport_size(self, size):
        self.size = size


class _Ctx:
    def __init__(self, page):
        self.page = page
        self._vars = {}


def test_narrow_viewport_warns_that_it_is_not_mobile(caplog):
    from noodle.orchestrator import runner
    page = _ViewportPage()
    ctx = _Ctx(page)
    with caplog.at_level("WARNING"):
        runner.execute_step("sets the viewport to 390x844", ctx)
    assert page.size == {'width': 390, 'height': 844}     # still resized
    assert "@mobile" in caplog.text and "no touch" in caplog.text
    # Warned once per scenario, not once per resize — this is guidance, not noise.
    caplog.clear()
    with caplog.at_level("WARNING"):
        runner.execute_step("switches to mobile view", ctx)
    assert "@mobile" not in caplog.text


def test_desktop_width_does_not_warn(caplog):
    from noodle.orchestrator import runner
    page = _ViewportPage()
    with caplog.at_level("WARNING"):
        runner.execute_step("sets the viewport to 1440x900", _Ctx(page))
    assert page.size == {'width': 1440, 'height': 900}
    assert "@mobile" not in caplog.text


# --- Gap: routing no longer sends a swipe test to a device it doesn't need ---

def test_swipe_routes_to_web_native_only_gestures_to_appium():
    assert wok.infer_tag("", "Feature: x\n  When User swipes up\n") == "web"
    assert wok.infer_tag("", "Feature: x\n  When User long presses 'Row 1'\n") == "web"
    assert wok.infer_tag("", "Feature: x\n  When User hides the keyboard\n") == "appium"
    assert wok.infer_tag("", "Feature: x\n  When User sends the app to the background\n") == "appium"


# --- The capability itself, against a real browser --------------------------
# The point of the whole ticket: prove the gesture reaches the page as TOUCH
# and actually moves it. A mouse-event stand-in would pass a weaker assertion.

_TOUCH_FIXTURE = """
<body style="margin:0"><div style="height:3000px">
  <div id="t" style="height:300px;background:#ccc">target</div></div>
<script>
  window.seen = [];
  for (const e of ['touchstart', 'touchmove', 'touchend'])
    document.addEventListener(e, () => window.seen.push(e), {passive: true});
  document.getElementById('t').addEventListener('pointerdown',
    e => window.pointerType = e.pointerType);
</script></body>
"""


@pytest.fixture
def chromium():
    """A launched Chromium, or a skip when only the binary is missing.

    The playwright PACKAGE is a core dependency, so importorskip never fires —
    but the browser download is separate, and both CI gates run `pytest
    unit_tests` without it. Skip (don't error) so a contributor with no
    browsers isn't blocked; both gates now run `playwright install chromium`,
    so a skip here in CI means that step regressed.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except PlaywrightError as e:
            if "Executable doesn't exist" not in str(e):
                raise
            pytest.skip("chromium not downloaded — run `playwright install chromium`")
        yield p, browser
        browser.close()


@pytest.fixture
def mobile_page(chromium):
    p, browser = chromium
    page = browser.new_context(**p.devices["Pixel 5"]).new_page()
    page.set_content(_TOUCH_FIXTURE)
    return page


def test_swipe_fires_real_touch_events_and_scrolls(mobile_page):
    assert actions.has_touch(mobile_page) is True
    assert mobile_page.evaluate("scrollY") == 0
    actions.swipe_web(mobile_page, "up")
    # Finger up scrolls the page down — the same convention as the Appium wok.
    assert mobile_page.evaluate("scrollY") > 0
    assert set(mobile_page.evaluate("[...new Set(window.seen)]")) == {
        "touchstart", "touchmove", "touchend"}


def test_swipe_down_reverses_it(mobile_page):
    actions.swipe_web(mobile_page, "up")
    scrolled = mobile_page.evaluate("scrollY")
    actions.swipe_web(mobile_page, "down")
    assert mobile_page.evaluate("scrollY") < scrolled


def test_bad_direction_is_rejected(mobile_page):
    with pytest.raises(AssertionError, match="Unknown swipe direction"):
        actions.swipe_web(mobile_page, "sideways")


def test_click_in_a_touch_context_arrives_as_touch(mobile_page):
    actions.click(mobile_page, "target")
    assert mobile_page.evaluate("window.pointerType") == "touch"


def test_long_press_holds_a_real_touch(mobile_page):
    actions.long_press_web(mobile_page, "target", seconds=0.3)
    assert mobile_page.evaluate("window.pointerType") == "touch"
    assert "touchstart" in mobile_page.evaluate("window.seen")


# --- Gap 5: the documented file-drop step was a phantom ---------------------

_DROPZONE = """
<div id="zone" style="width:300px;height:200px;border:1px solid">Drop here</div>
<div id="out"></div>
<script>
  const z = document.getElementById('zone');
  z.addEventListener('dragover', e => e.preventDefault());
  z.addEventListener('drop', e => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    document.getElementById('out').textContent = f.name + '|' + f.size + '|' + f.type;
  });
</script>
"""


def test_dragging_a_real_file_onto_a_dropzone(chromium, tmp_path, monkeypatch):
    _, browser = chromium
    (tmp_path / "report.csv").write_text("a,b\n1,2\n")
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.set_content(_DROPZONE)
    # The step documented since NOOD_0009 — it used to look for a DOM element
    # literally named 'report.csv' and fail as "not found".
    actions.drag(page, "report.csv", "Drop here")
    assert page.locator("#out").text_content() == "report.csv|8|text/csv"


def test_element_drag_still_works_when_the_name_is_not_a_file(chromium, tmp_path,
                                                              monkeypatch):
    _, browser = chromium
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.set_content(
        '<div id="a" draggable="true">Card A</div>'
        '<div id="b">Done column</div>'
        '<script>const b=document.getElementById("b");'
        'b.addEventListener("dragover",e=>e.preventDefault());'
        'b.addEventListener("drop",()=>b.textContent="dropped");</script>')
    actions.drag(page, "Card A", "Done column")
    assert page.locator("#b").text_content() == "dropped"


# --- Gap 4: the documented Chrome-product-UI escape hatch now exists ---------

def test_browser_args_env_reaches_launch(monkeypatch):
    """steps_dictionary tells testers to 'add a disable flag' for Chrome's
    sign-in/save-password bubbles; there was previously nowhere to put one."""
    monkeypatch.delenv("NOODLE_BROWSER_ARGS", raising=False)
    assert hooks._browser_args() == []           # absent = unchanged launch
    monkeypatch.setenv("NOODLE_BROWSER_ARGS",
                       '--disable-features=PasswordManager --user-data-dir="/tmp/a b"')
    assert hooks._browser_args() == [
        "--disable-features=PasswordManager", "--user-data-dir=/tmp/a b"]


# --- Addressing a specific window past the two-page model -------------------
# _switch_tab is positional (new/last → pages[-1], everything else → pages[0]),
# which covers the opener↔popup toggle but cannot name the middle one of three.
# A shop page, a support chat and a payment window open at once is ordinary.

_SHOP = ('<title>Your orders</title><button id=chat onclick="window.open('
         "'about:blank','support','width=420,height=640')" '">chat</button>')


def test_named_switch_resolves_by_title_and_by_url():
    from noodle.resolver.step_resolver import resolve
    for step in ("switches to the window titled 'Support'",
                 "switches to the tab named 'Support'",
                 "switches to the window called 'Support'",
                 "switches to the 'Support' window",
                 "switches to the 'Support' tab"):
        assert resolve(step) == {'type': 'switch_tab_named', 'name': 'Support'}


def test_named_switch_does_not_swallow_neighbouring_phrasings():
    """The quoted form sits beside `switches to the 'inner' frame` and the
    positional tab words; neither may be captured."""
    from noodle.resolver.step_resolver import resolve
    assert resolve("switches to the new window")['type'] == 'switch_tab'
    assert resolve("switches to the original tab")['type'] == 'switch_tab'
    assert resolve("switches to the 'inner' frame")['type'] == 'switch_frame'
    assert resolve("switches to the mobile view")['type'] == 'set_viewport'


def test_action_is_registered_for_the_llm_fallback():
    """VALID_TYPES mirrors runner.py's dispatch; a new action missing from it
    is rejected as a bogus type at runtime."""
    from noodle.resolver.step_resolver import VALID_TYPES
    assert "switch_tab_named" in VALID_TYPES


class _FakeWindow:
    def __init__(self, title, url):
        self._title, self.url = title, url

    def title(self):
        return self._title


class _WindowCtx:
    """runner._pages() reads context._bctx.pages — the browser context holds
    every page, whether the browser drew it as a tab or a separate window."""
    def __init__(self, pages):
        self._bctx = type("Bctx", (), {"pages": pages})()
        self.page = pages[0]
        self._vars = {}


def _switch(pages, name):
    from noodle.orchestrator import runner
    ctx = _WindowCtx(pages)
    runner._switch_named(ctx, name)
    return ctx.page


def test_switches_to_the_window_matching_a_title():
    shop = _FakeWindow("Your orders", "https://shop.test/orders")
    chat = _FakeWindow("Support chat", "https://chat.test/session")
    pay = _FakeWindow("Payment", "https://pay.test/checkout")
    assert _switch([shop, chat, pay], "Support") is chat
    assert _switch([shop, chat, pay], "Payment") is pay
    # Middle-of-three is exactly what the positional words cannot reach.
    assert _switch([shop, chat, pay], "orders") is shop


def test_matches_on_url_when_the_popup_has_no_title():
    """A chat widget commonly sets document.title only after its socket
    connects, so title-only matching would miss the window entirely."""
    shop = _FakeWindow("Your orders", "https://shop.test/orders")
    chat = _FakeWindow("", "https://chat.test/session")
    assert _switch([shop, chat], "chat.test") is chat


def test_matching_is_case_insensitive():
    chat = _FakeWindow("Support Chat", "https://chat.test/")
    shop = _FakeWindow("Your orders", "https://shop.test/")
    assert _switch([shop, chat], "support chat") is chat


def test_no_match_lists_what_is_open():
    shop = _FakeWindow("Your orders", "https://shop.test/orders")
    with pytest.raises(AssertionError) as e:
        _switch([shop], "Support")
    assert "No open tab or window matches 'Support'" in str(e.value)
    assert "Your orders" in str(e.value)          # diagnostics, not just a refusal


def test_ambiguity_is_an_error_not_a_guess():
    """Silently taking the first of two matches is how a test asserts against
    the wrong window and passes anyway."""
    a = _FakeWindow("Support chat", "https://chat.test/1")
    b = _FakeWindow("Support ticket", "https://chat.test/2")
    with pytest.raises(AssertionError, match="matches 2 open tabs/windows"):
        _switch([a, b], "Support")


def test_error_messages_redact_query_strings():
    """NOOD_0177 — a query string is where an SSO callback carries its token,
    and these messages are printed and land in the report."""
    pg = _FakeWindow("Callback", "https://sso.test/cb?token=SECRETVALUE")
    with pytest.raises(AssertionError) as e:
        _switch([pg], "nothing-matches")
    assert "SECRETVALUE" not in str(e.value)
    assert "https://sso.test/cb" in str(e.value)


def test_a_window_that_cannot_be_queried_is_skipped_not_fatal():
    """A page closed mid-scenario raises on .title(); it must not take the
    whole switch down with it."""
    class _Dead:
        url = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))

        def title(self):
            raise RuntimeError("closed")

    chat = _FakeWindow("Support chat", "https://chat.test/")
    assert _switch([_Dead(), chat], "Support") is chat

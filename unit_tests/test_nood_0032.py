"""NOOD_0032 — native-app testing via Appium platform tags:
@android / @ios (emulators), @windows (Windows 11 native apps, via
appium-windows-driver), @mac (macOS, via appium-mac2-driver).

Everything here runs with fake drivers — no Appium client install, no server,
no device. That is deliberate: the dev box is macOS, so the Windows paths are
exercised through the same pure functions the real session uses
(docs/native-apps.md covers running the real thing on a Windows 11 box).
"""
import sys
import types

import pytest

from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


@pytest.fixture(autouse=True)
def _stub_appium(monkeypatch):
    """The appium client is an optional extra ([mobile]) and not installed in
    the unit-test env. AppiumBy is just W3C strategy strings — stub the module
    so locator tests run anywhere."""
    if "appium" in sys.modules:        # real client installed — use it
        yield
        return
    by_mod = types.ModuleType("appium.webdriver.common.appiumby")
    by_mod.AppiumBy = types.SimpleNamespace(
        ACCESSIBILITY_ID="accessibility id", ID="id", XPATH="xpath",
        ANDROID_UIAUTOMATOR="-android uiautomator")
    for name, mod in [
        ("appium", types.ModuleType("appium")),
        ("appium.webdriver", types.ModuleType("appium.webdriver")),
        ("appium.webdriver.common", types.ModuleType("appium.webdriver.common")),
        ("appium.webdriver.common.appiumby", by_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    yield


def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))


# --- capabilities: platform tag -> default caps -------------------------------

def test_default_caps_android_forms():
    from noodle.agents.mobile.driver import default_capabilities as caps
    assert caps("android", "app.apk")["appium:app"] == "app.apk"
    c = caps("android", "com.android.settings/.Settings")
    assert c["appium:appPackage"] == "com.android.settings"
    assert c["appium:appActivity"] == ".Settings"
    assert caps("android", "com.foo.bar")["appium:appPackage"] == "com.foo.bar"
    assert caps("android", None) == {
        "platformName": "Android", "appium:automationName": "UiAutomator2"}


def test_default_caps_ios_forms():
    from noodle.agents.mobile.driver import default_capabilities as caps
    assert caps("ios", "MyApp.app")["appium:app"] == "MyApp.app"
    assert caps("ios", "com.apple.Preferences")["appium:bundleId"] == "com.apple.Preferences"
    assert caps("ios", None)["appium:automationName"] == "XCUITest"


def test_default_caps_windows():
    from noodle.agents.mobile.driver import default_capabilities as caps
    c = caps("windows", r"C:\Windows\System32\notepad.exe")
    assert c == {"platformName": "Windows", "appium:automationName": "Windows",
                 "appium:app": r"C:\Windows\System32\notepad.exe"}
    # AUMID (Store app) and the whole-desktop session pass straight through
    assert caps("windows", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")[
        "appium:app"].endswith("!App")
    assert caps("windows", "Root")["appium:app"] == "Root"


def test_default_caps_mac():
    from noodle.agents.mobile.driver import default_capabilities as caps
    assert caps("mac", "com.apple.calculator")["appium:bundleId"] == "com.apple.calculator"
    assert caps("mac", "/Applications/Calculator.app")["appium:appPath"] == \
        "/Applications/Calculator.app"
    assert caps("mac", None)["appium:automationName"] == "Mac2"


def test_resolve_caps_explicit_overrides_defaults():
    from noodle.agents.mobile.driver import resolve_capabilities
    # explicit NOODLE_APPIUM_CAPS entries win; platform fills in underneath
    c = resolve_capabilities("windows", '{"appium:app": "explicit.exe"}', "default.exe")
    assert c["appium:app"] == "explicit.exe"
    assert c["platformName"] == "Windows"
    # platform tag alone: pure defaults
    assert resolve_capabilities("android", None, "a.apk")["appium:app"] == "a.apk"
    # no platform, no caps → the existing how-to error
    with pytest.raises(AssertionError, match="NOODLE_APPIUM_CAPS is not set"):
        resolve_capabilities(None, None, None)


# --- hooks: tag -> platform ----------------------------------------------------

def test_appium_platform_tag_detection():
    from noodle.hooks import appium_platform
    assert appium_platform({"windows"}) == "windows"
    assert appium_platform({"mac", "smoke"}) == "mac"
    assert appium_platform({"android"}) == "android"
    assert appium_platform({"ios"}) == "ios"
    assert appium_platform({"appium"}) is None      # plain @appium: caps from env
    assert appium_platform({"web", "smoke"}) is None
    # regression: '@mobile @android' is Playwright Pixel-5 web emulation, not Appium
    assert appium_platform({"mobile", "android"}) is None
    assert appium_platform({"mobile", "iphone"}) is None


# --- patterns: new gestures -----------------------------------------------------

def test_long_press_patterns():
    assert _resolve("User long-presses the Archive button") == \
        ("long_press", {"locator": "Archive"})
    assert _resolve("User presses and holds 'Chat item'") == \
        ("long_press", {"locator": "Chat item"})
    # regressions: the click/press/device-key family is untouched
    assert _resolve("User presses the Login button") == ("click", {"locator": "Login"})
    assert _resolve("User presses the back button") == ("device_key", {"key": "back"})
    assert _resolve("User presses Enter") == ("press_key", {"key": "Enter"})


def test_keyboard_and_background_patterns():
    assert _resolve("User hides the keyboard") == ("hide_keyboard", {})
    assert _resolve("User dismisses the keyboard") == ("hide_keyboard", {})
    assert _resolve("User sends the app to the background for 5 seconds") == \
        ("background_app", {"seconds": 5})
    assert _resolve("User sends the app to the background") == \
        ("background_app", {"seconds": 3})


# --- locator: one chain serves all four platforms --------------------------------

class _FakeDriver:
    """Records find_elements calls; returns a hit for one (strategy, value)."""
    def __init__(self, hit_when=None, capabilities=None):
        self.calls, self._hit = [], hit_when
        self.capabilities = capabilities or {}
        self.keycodes, self.scripts, self.backgrounded = [], [], []
        self.keyboard_hidden = False

    def find_elements(self, strategy, value):
        self.calls.append((strategy, value))
        return ["<element>"] if self._hit and self._hit(strategy, value) else []

    def press_keycode(self, code):
        self.keycodes.append(code)

    def execute_script(self, script, args):
        self.scripts.append((script, args))

    def hide_keyboard(self):
        self.keyboard_hidden = True

    def background_app(self, seconds):
        self.backgrounded.append(seconds)


def test_locator_windows_name_fallback():
    from noodle.agents.mobile.locator import find
    d = _FakeDriver(hit_when=lambda s, v: "@Name" in v)
    assert find(d, "Equals") == "<element>"
    assert any("@AutomationId" in v for _, v in d.calls)


def test_locator_mac_title_fallback():
    from noodle.agents.mobile.locator import find
    d = _FakeDriver(hit_when=lambda s, v: "@title" in v)
    assert find(d, "Calculator") == "<element>"


def test_locator_miss_names_the_chain():
    from noodle.agents.mobile.locator import find
    with pytest.raises(AssertionError, match="accessibility id"):
        find(_FakeDriver(), "Nowhere")


# --- actions: platform-aware device keys ------------------------------------------

def test_device_key_per_platform():
    from noodle.agents.mobile.actions import device_key
    android = _FakeDriver(capabilities={"platformName": "Android"})
    device_key(android, "back")
    assert android.keycodes == [4]

    ios = _FakeDriver(capabilities={"platformName": "iOS"})
    device_key(ios, "home")
    assert ios.scripts == [("mobile: pressButton", {"name": "home"})]

    windows = _FakeDriver(capabilities={"platformName": "Windows"})
    with pytest.raises(AssertionError, match="no back button on windows"):
        device_key(windows, "back")


def test_hide_keyboard_and_background():
    from noodle.agents.mobile.actions import background_app, hide_keyboard
    d = _FakeDriver()
    hide_keyboard(d)
    background_app(d, 5)
    assert d.keyboard_hidden and d.backgrounded == [5]


# --- runner: new action types route to the mobile agent ---------------------------

def test_mobile_types_cover_new_actions():
    from noodle.orchestrator.runner import _MOBILE_TYPES
    assert {"long_press", "hide_keyboard", "background_app", "screenshot"} <= _MOBILE_TYPES


def test_execute_mobile_dispatch():
    from noodle.orchestrator.runner import _execute_mobile

    class _Ctx:
        _mobile = _FakeDriver()

    _execute_mobile(_Ctx, {"type": "hide_keyboard"})
    _execute_mobile(_Ctx, {"type": "background_app", "seconds": 2})
    assert _Ctx._mobile.keyboard_hidden and _Ctx._mobile.backgrounded == [2]


# --- OCR fallback (NOOD_0032): last resort for apps with no accessible names ------
#
# Covers apps whose framework doesn't expose accessibility metadata at all —
# unlabeled legacy Win32/MFC controls, canvas-drawn UI, games — on any of the
# four platforms. Opt in with @ocr_fallback / NOODLE_OCR_FALLBACK, same tag
# the web agent already uses (noodle.agents.web.locator). Mocked at the
# screen.locate_text/tap_at boundary — no tesseract binary needed to test the
# plumbing; noodle.agents.visual.ocr's own tests cover the recognition itself.

@pytest.fixture
def _ocr_fallback_on():
    from noodle.agents.web import locator as web_locator
    web_locator.set_ocr_fallback(True)
    yield
    web_locator.set_ocr_fallback(None)


def test_locator_ocr_off_by_default_still_raises():
    from noodle.agents.mobile.locator import find
    with pytest.raises(AssertionError, match=r"add @ocr_fallback"):
        find(_FakeDriver(), "Mystery Button")


def test_locator_ocr_fallback_returns_coordinate(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import locator, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: (12, 34))
    assert locator.find(_FakeDriver(), "Mystery Button") == ("coordinate", 12, 34)


def test_locator_ocr_fallback_miss_still_raises(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import locator, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: None)
    with pytest.raises(AssertionError, match="and OCR"):
        locator.find(_FakeDriver(), "Nowhere")


def test_tap_and_fill_use_ocr_coordinate(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import actions, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: (5, 6))
    taps = []
    monkeypatch.setattr(screen, "tap_at", lambda driver, x, y: taps.append((x, y)))

    d = _FakeDriver()
    actions.tap(d, "Mystery Button")
    assert taps == [(5, 6)]

    sent = []
    d.switch_to = types.SimpleNamespace(
        active_element=types.SimpleNamespace(send_keys=lambda v: sent.append(v)))
    actions.fill(d, "Mystery Field", "hello")
    assert taps == [(5, 6), (5, 6)]
    assert sent == ["hello"]


def test_assert_visible_ocr_coordinate_counts_as_visible(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import actions, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: (1, 2))
    actions.assert_visible(_FakeDriver(), "Ghost Label", timeout=0.1)  # no raise


def test_assert_hidden_ocr_coordinate_means_visible(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import actions, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: (1, 2))
    with pytest.raises(AssertionError, match="NOT be visible"):
        actions.assert_hidden(_FakeDriver(), "Ghost Label")


def test_long_press_uses_ocr_coordinate(monkeypatch, _ocr_fallback_on):
    from noodle.agents.mobile import actions, screen
    monkeypatch.setattr(screen, "locate_text", lambda driver, text: (7, 8))
    perform_calls = []

    class _FakeActionBuilder:
        def __init__(self, driver, mouse):
            self.pointer_action = self
        def move_to_location(self, x, y):
            self.xy = (x, y)
            return self
        def pointer_down(self):
            return self
        def pause(self, s):
            return self
        def pointer_up(self):
            return self
        def perform(self):
            perform_calls.append(self.xy)

    monkeypatch.setattr(
        "selenium.webdriver.common.actions.action_builder.ActionBuilder",
        _FakeActionBuilder)
    actions.long_press(_FakeDriver(), "Ghost Button")
    assert perform_calls == [(7, 8)]


def test_pointer_kind_platform_selection():
    from selenium.webdriver.common.actions import interaction

    from noodle.agents.mobile.screen import _pointer_kind
    assert _pointer_kind(_FakeDriver(capabilities={"platformName": "Android"}))[0] == \
        interaction.POINTER_TOUCH
    assert _pointer_kind(_FakeDriver(capabilities={"platformName": "iOS"}))[0] == \
        interaction.POINTER_TOUCH
    assert _pointer_kind(_FakeDriver(capabilities={"platformName": "Windows"}))[0] == \
        interaction.POINTER_MOUSE
    assert _pointer_kind(_FakeDriver(capabilities={"platformName": "Mac"}))[0] == \
        interaction.POINTER_MOUSE


def test_screen_locate_text_passes_screenshot_and_text(monkeypatch):
    import io as _io

    from PIL import Image

    from noodle.agents.mobile import screen
    img = Image.new("RGB", (10, 10))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")

    captured = {}

    def _fake_find(img_arg, text):
        captured["size"] = img_arg.size
        captured["text"] = text
        return (3, 4)

    monkeypatch.setattr(screen.ocr, "find_text_in_image", _fake_find)
    d = _FakeDriver()
    d.get_screenshot_as_png = lambda: buf.getvalue()
    assert screen.locate_text(d, "Ghost") == (3, 4)
    assert captured == {"size": (10, 10), "text": "Ghost"}


# --- CI sharding: desktop/native features never land in web shards -----------------

def test_windows_mac_features_excluded_from_web_shards(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "list_features",
        Path(__file__).resolve().parents[1] / "scripts" / "list_features.py")
    lf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lf)

    win = tmp_path / "w.feature"
    win.write_text("@windows\nFeature: W\n  Scenario: s\n    Given x\n")
    mac = tmp_path / "m.feature"
    mac.write_text("@mac\nFeature: M\n  Scenario: s\n    Given x\n")
    web = tmp_path / "web.feature"
    web.write_text("Feature: X\n  Scenario: s\n    Given x\n")
    assert not lf.is_web_shard(win)
    assert not lf.is_web_shard(mac)
    assert lf.is_web_shard(web)

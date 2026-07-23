"""Appium session lifecycle (Phase F; NOOD_0032 — Android/iOS/Windows/macOS).

Capabilities come from NOODLE_APPIUM_CAPS — either a JSON string or a path to
a .json file. The server URL comes from NOODLE_APPIUM_URL (or the
APPIUM_SERVER key in environments.yaml, loaded into env by hooks).

NOOD_0032 — a platform tag (@android/@ios/@windows/@mac) makes caps optional:
sensible defaults are built from the tag plus one env var naming the app
(NOODLE_ANDROID_APP / NOODLE_IOS_APP / NOODLE_WINDOWS_APP / NOODLE_MAC_APP).
Windows 11 native apps ride the same protocol via appium-windows-driver;
macOS via appium-mac2-driver — see docs/native-apps.md for server setup.
"""
import json
import os
from pathlib import Path

from noodle.log import logger

DEFAULT_URL = "http://localhost:4723"


def _appium():
    try:
        from appium import webdriver
        from appium.options.common import AppiumOptions
        return webdriver, AppiumOptions
    except ImportError:
        raise ImportError(
            "@appium scenarios require the Appium client: pip install noodle[mobile] "
            "(and a running Appium server + device/emulator)"
        )


def load_capabilities(raw: str | None) -> dict:
    """NOODLE_APPIUM_CAPS as a dict — accepts a JSON string or a .json path.
    Pure — unit-testable without Appium."""
    if not raw:
        raise AssertionError(
            "NOODLE_APPIUM_CAPS is not set — provide Appium capabilities as a "
            "JSON string or a path to a .json file (e.g. "
            '\'{"platformName": "Android", "appium:automationName": "UiAutomator2"}\')'
        )
    raw = raw.strip()
    if not raw.startswith("{"):
        p = Path(raw)
        if not p.is_file():
            raise AssertionError(f"NOODLE_APPIUM_CAPS points to a missing file: {p}")
        raw = p.read_text()
    try:
        caps = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AssertionError(f"NOODLE_APPIUM_CAPS is not valid JSON: {e}") from e
    if not isinstance(caps, dict):
        raise AssertionError("NOODLE_APPIUM_CAPS must be a JSON object of capabilities")
    return caps


# NOOD_0032 — automation drivers per platform tag. Windows/Mac are the
# official Appium 2 desktop drivers (appium-windows-driver wraps WinAppDriver;
# appium-mac2-driver wraps XCUITest for macOS) — same client, same protocol.
_PLATFORM_CAPS = {
    "android": {"platformName": "Android", "appium:automationName": "UiAutomator2"},
    "ios":     {"platformName": "iOS",     "appium:automationName": "XCUITest"},
    "windows": {"platformName": "Windows", "appium:automationName": "Windows"},
    "mac":     {"platformName": "Mac",     "appium:automationName": "Mac2"},
}


def default_capabilities(platform: str, app: str | None) -> dict:
    """Capabilities for a platform tag + the NOODLE_<PLATFORM>_APP value.
    Pure — unit-testable without Appium. `app` per platform:
      android — .apk path, or "package/Activity", or a bare package name
      ios     — .app/.ipa path, or a bundle id
      windows — .exe path, an AUMID (Store apps), or "Root" (whole desktop)
      mac     — a bundle id, or a path to the .app
    """
    caps = dict(_PLATFORM_CAPS[platform])
    if not app:
        return caps
    if platform == "android":
        if app.endswith(".apk"):
            caps["appium:app"] = app
        elif "/" in app:
            pkg, activity = app.split("/", 1)
            caps["appium:appPackage"], caps["appium:appActivity"] = pkg, activity
        else:
            caps["appium:appPackage"] = app
    elif platform == "ios":
        if app.endswith((".app", ".ipa")):
            caps["appium:app"] = app
        else:
            caps["appium:bundleId"] = app
    elif platform == "windows":
        caps["appium:app"] = app          # exe path, AUMID, or "Root"
    elif platform == "mac":
        if "/" in app:
            caps["appium:appPath"] = app
        else:
            caps["appium:bundleId"] = app
    return caps


def resolve_capabilities(platform: str | None, raw_caps: str | None,
                         app: str | None) -> dict:
    """Final session caps. Explicit NOODLE_APPIUM_CAPS entries always win;
    a platform tag fills in the defaults underneath. Pure — unit-testable."""
    if raw_caps:
        caps = load_capabilities(raw_caps)
        if platform:
            caps = {**default_capabilities(platform, app), **caps}
        return caps
    if platform:
        return default_capabilities(platform, app)
    return load_capabilities(raw_caps)     # raises with the how-to message


def start_session(platform: str | None = None):
    """Connect to the Appium server and start a session. Returns the driver.
    `platform` is the scenario's platform tag (android/ios/windows/mac), or
    None for a plain @appium scenario (caps must then come from the env)."""
    webdriver, AppiumOptions = _appium()
    url = os.getenv("NOODLE_APPIUM_URL") or os.getenv("APPIUM_SERVER") or DEFAULT_URL
    app = os.getenv(f"NOODLE_{platform.upper()}_APP") if platform else None
    caps = resolve_capabilities(platform, os.getenv("NOODLE_APPIUM_CAPS"), app)
    options = AppiumOptions().load_capabilities(caps)
    driver = webdriver.Remote(url, options=options)
    logger.info(f"\n  📱 Appium session started against {url} "
                f"({caps.get('platformName', '?')})")
    return driver


def stop_session(driver) -> None:
    try:
        driver.quit()
    except Exception:
        pass

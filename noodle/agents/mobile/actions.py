"""Mobile actions (Phase F) — tap, swipe, keys, fill, visibility.

Gestures use W3C Actions via the driver's helpers (Appium-Python-Client 3+
removed TouchAction). Everything raises AssertionError with the human label on
failure, matching the web agent's error style.
"""
import time

from noodle.log import logger

from .locator import find


def tap(driver, locator_text: str):
    el = find(driver, locator_text)
    if isinstance(el, tuple) and el[0] == "coordinate":
        from . import screen
        screen.tap_at(driver, el[1], el[2])
        return
    el.click()


def fill(driver, locator_text: str, value: str):
    el = find(driver, locator_text)
    if isinstance(el, tuple) and el[0] == "coordinate":
        from . import screen
        screen.tap_at(driver, el[1], el[2])
        driver.switch_to.active_element.send_keys(value)
        return
    el.clear()
    el.send_keys(value)


def type_text(driver, text: str):
    """Type into whatever has focus."""
    driver.switch_to.active_element.send_keys(text)


def swipe(driver, direction: str):
    """Swipe across the middle 60% of the screen in `direction`."""
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    cx, cy = w // 2, h // 2
    dx, dy = int(w * 0.3), int(h * 0.3)
    start, end = {
        "left":  ((cx + dx, cy), (cx - dx, cy)),
        "right": ((cx - dx, cy), (cx + dx, cy)),
        "up":    ((cx, cy + dy), (cx, cy - dy)),
        "down":  ((cx, cy - dy), (cx, cy + dy)),
    }[direction]
    driver.swipe(start[0], start[1], end[0], end[1], 300)
    logger.info(f"\n  👆 Swiped {direction}")


def long_press(driver, locator_text: str, seconds: float = 1.0):
    el = find(driver, locator_text)
    if isinstance(el, tuple) and el[0] == "coordinate":
        x, y = el[1], el[2]
    else:
        rect = el.rect
        x, y = rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2
    # W3C pointer sequence — TouchAction is gone in client v3.
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput

    from . import screen
    kind, name = screen._pointer_kind(driver)
    actions = ActionBuilder(driver, mouse=PointerInput(kind, name))
    actions.pointer_action.move_to_location(int(x), int(y)).pointer_down() \
        .pause(seconds).pointer_up()
    actions.perform()


def _platform(driver) -> str:
    try:
        return (driver.capabilities.get("platformName") or "").lower()
    except Exception:
        return ""


def device_key(driver, key: str):
    """Hardware/navigation key: back or home. Android uses keycodes 4/3,
    iOS the pressButton endpoint; desktop apps have no such keys (NOOD_0032)."""
    platform = _platform(driver)
    if platform == "ios":
        driver.execute_script("mobile: pressButton", {"name": key})
    elif platform in ("windows", "mac"):
        raise AssertionError(
            f"'{key}' is a mobile device key — there is no {key} button on {platform}"
        )
    else:
        driver.press_keycode({"back": 4, "home": 3}[key])


def hide_keyboard(driver):
    """Dismiss the on-screen keyboard (mobile only — no-op error on desktop)."""
    driver.hide_keyboard()


def background_app(driver, seconds: int):
    """Send the app to the background for N seconds, then bring it back."""
    driver.background_app(seconds)
    logger.info(f"\n  📱 App backgrounded for {seconds}s")


def screenshot(driver, name: str, path: str) -> str:
    """Native screenshot via the Appium driver — same artifact layout as web."""
    import os
    os.makedirs(path, exist_ok=True)
    file_path = f"{path}/{name}.png"
    driver.get_screenshot_as_file(file_path)
    return file_path


def assert_visible(driver, text: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            el = find(driver, text)
            # A ('coordinate', x, y) OCR sentinel means the text was found
            # rendered on screen — visible by definition, no element to ask.
            if isinstance(el, tuple) or el.is_displayed():
                return
        except AssertionError as e:
            last_err = e
        time.sleep(0.5)
    raise last_err or AssertionError(f"Expected to see '{text}' — not visible")


def assert_hidden(driver, text: str):
    try:
        el = find(driver, text)
    except AssertionError:
        return
    if isinstance(el, tuple) or el.is_displayed():
        raise AssertionError(f"Expected '{text}' to NOT be visible — but it is")

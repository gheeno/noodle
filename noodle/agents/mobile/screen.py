"""Native pixel/OCR bridge (NOOD_0032) — the last-resort locator for apps
whose UI framework doesn't expose accessible names: unlabeled legacy Win32/
MFC controls, canvas-drawn UI, games. Opt in with the same @ocr_fallback tag
/ NOODLE_OCR_FALLBACK env var the web agent uses (noodle.agents.web.locator).

Source-agnostic noodle.agents.visual.ocr does the recognition; this module
only owns the Appium screenshot and the coordinate tap.

ponytail: a native screenshot's pixel space is assumed to match the
coordinate space Appium taps use directly (true for Android/iOS/Windows/Mac
in practice) — unlike the web agent, which has to divide by
devicePixelRatio. If a specific device/driver disagrees, add a scale factor
here.
"""
import io

from PIL import Image

from noodle.agents.visual import ocr
from noodle.log import logger


def _screenshot_image(driver) -> Image.Image:
    return Image.open(io.BytesIO(driver.get_screenshot_as_png()))


def locate_text(driver, text: str):
    """Rendered (x, y) of `text` on the current native screen, or None."""
    return ocr.find_text_in_image(_screenshot_image(driver), text)


def _pointer_kind(driver):
    """Touch for mobile (Android/iOS), mouse for desktop (Windows/Mac) — a
    touch pointer isn't a native input on a desktop OS."""
    from selenium.webdriver.common.actions import interaction
    try:
        platform = (driver.capabilities.get("platformName") or "").lower()
    except Exception:
        platform = ""
    if platform in ("windows", "mac"):
        return interaction.POINTER_MOUSE, "mouse"
    return interaction.POINTER_TOUCH, "touch"


def tap_at(driver, x, y):
    """Tap a raw screen coordinate — the only way to hit an element the
    accessibility tree can't name."""
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    kind, name = _pointer_kind(driver)
    actions = ActionBuilder(driver, mouse=PointerInput(kind, name))
    actions.pointer_action.move_to_location(int(x), int(y)).pointer_down().pointer_up()
    actions.perform()
    logger.info(f"\n  👆 Tapped ({x:.0f}, {y:.0f}) via OCR coordinate")

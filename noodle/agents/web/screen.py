"""Web pixel/OCR bridge — drive canvas/terminal UIs that have no semantic DOM.

Frames come from page.screenshot(); actions go through Playwright's mouse and
keyboard in page (CSS-pixel) coordinates. OCR is deterministic (pytesseract);
a vision LLM is a coordinate fallback only when NOODLE_MODEL is set.

Coordinate correctness (the easy thing to get wrong):
  - page.screenshot() pixels are at devicePixelRatio (dpr).
  - page.mouse takes CSS pixels.
  - screenshot is viewport-only (full_page=False) so its origin matches mouse's.
So OCR (device px) is divided by dpr before clicking, and a focus region (CSS
px from the viewport) is multiplied by dpr before cropping the screenshot.
"""
import io
import os
import sys
import time

from PIL import Image

from noodle.agents.visual import ocr
from noodle.log import logger

_WAIT_POLL = 0.5

# Per-scenario focus region in viewport CSS pixels (or None). Set by the
# `focuses on "<region>"` step, reset between scenarios by hooks.
_region = None


def set_region(region):
    global _region
    _region = region


def _dpr(page):
    try:
        return float(page.evaluate("window.devicePixelRatio")) or 1.0
    except Exception:
        return 1.0


def _to_css(x, y, dpr):
    """Device-pixel OCR coords → CSS-pixel mouse coords. Pure."""
    return x / dpr, y / dpr


def _device_region(page, region=None):
    """A CSS-pixel region (default: the focus region) scaled to the
    screenshot's device pixels."""
    region = region or _region
    if not region:
        return None
    d = _dpr(page)
    return {k: int(v * d) for k, v in region.items()}


def _screenshot_image(page):
    return Image.open(io.BytesIO(page.screenshot(full_page=False)))


def _screen_text(page):
    return ocr.find_all_text_in_image(_screenshot_image(page), _device_region(page))


# --- actions ----------------------------------------------------------------

def type_text(page, text):
    """Type into whatever is focused — no locator (terminals, canvas inputs)."""
    page.keyboard.type(text)


def click_at(page, x, y):
    page.mouse.click(float(x), float(y))


def _locate(page, text):
    """Rendered position of `text` → CSS-pixel (x,y), or None. OCR first;
    vision-LLM coordinate fallback when NOODLE_MODEL is set."""
    img = _screenshot_image(page)
    hit = ocr.find_text_in_image(img, text, _device_region(page))
    if hit is None and os.getenv("NOODLE_MODEL"):
        from noodle.agents.visual import vision_locate
        hit = vision_locate.locate_by_description(text, image=img)
    if hit is None:
        return None
    return _to_css(hit[0], hit[1], _dpr(page))


# Public alias (Phase T) — locator.py's OCR fallback tier needs the rendered
# position without the click.
def locate_text(page, text):
    return _locate(page, text)


def click_text(page, text):
    pos = _locate(page, text)
    if pos is None:
        raise AssertionError(f"Could not find text on screen to click: '{text}'")
    page.mouse.click(pos[0], pos[1])


def assert_text_visible(page, text):
    if text.lower() not in _screen_text(page).lower():
        raise AssertionError(
            f"Expected screen to show '{text}' — not found by OCR.\nURL: {page.url}"
        )


def assert_text_hidden(page, text):
    if text.lower() in _screen_text(page).lower():
        raise AssertionError(
            f"Expected screen NOT to show '{text}' — but OCR found it.\nURL: {page.url}"
        )


def wait_text_visible(page, text, timeout=None):
    secs = (timeout or int(os.getenv("NOODLE_TIMEOUT", "10000")) / 1000)
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if text.lower() in _screen_text(page).lower():
            logger.info(f"\n  👁  Screen shows '{text}'")
            return
        time.sleep(_WAIT_POLL)
    raise AssertionError(f"Timed out ({secs:.0f}s) waiting for screen to show '{text}'")


# --- DOM-renderer terminals (gap 6): text IS in the DOM ---------------------

_TERMINAL_SELECTORS = ".xterm-rows, .xterm-screen, pre, code, [role=log]"


def buffer_text(page):
    """Joined inner_text of the terminal container — for DOM renderers
    (xterm.js DOM mode) and <pre>/<code> blobs where text lives in the DOM.
    POM key 'terminal' wins; otherwise a default selector set."""
    from noodle.agents.web import pom
    loc = pom.locate(page, "terminal") or page.locator(_TERMINAL_SELECTORS)
    n = loc.count()
    if n == 0:
        return ""
    return "\n".join((loc.nth(i).inner_text() or "") for i in range(min(n, 20)))


def assert_buffer_contains(page, text):
    buf = buffer_text(page)
    if text.lower() not in buf.lower():
        raise AssertionError(
            f"Terminal buffer does not contain '{text}'.\nBuffer:\n{buf[:500]}\nURL: {page.url}"
        )


# --- element-scoped image scanning (NOOD_0114) ------------------------------
# Carousels, flyers, banners, logos, avatars: the text/object lives in an
# image's pixels, so these steps OCR (or vision-check) one DOM element's
# rendered box instead of the whole viewport.

def _clamp_box(box, vp):
    """Pure: intersect an element bounding box with the viewport (screenshots
    are viewport-only). {x,y,width,height} ints, or None when no overlap."""
    x1, y1 = max(0.0, box["x"]), max(0.0, box["y"])
    x2 = min(box["x"] + box["width"], vp["width"])
    y2 = min(box["y"] + box["height"], vp["height"])
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return {"x": int(x1), "y": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}


def _element_region(page, target):
    """Viewport CSS-pixel region of a DOM element (POM key, label, or CSS)."""
    from noodle.agents.web import locator as _locator
    loc = _locator.find(page, target)
    if loc is None or isinstance(loc, tuple):
        raise AssertionError(f"Cannot scan '{target}' — no matching element")
    loc = loc.first
    try:
        loc.scroll_into_view_if_needed()
    except Exception:
        pass
    box = loc.bounding_box()
    vp = page.viewport_size or {"width": 1280, "height": 720}
    region = _clamp_box(box, vp) if box else None
    if region is None:
        raise AssertionError(
            f"Cannot scan '{target}' — element has no visible area in the viewport")
    return region


def focus_element(page, target):
    """Focus later screen/OCR steps on one element's rendered box."""
    set_region(_element_region(page, target))
    logger.info(f"\n  🔍 Screen focus: '{target}'")


def read_text(page, target=None):
    """OCR text of an element's image (or the focus region / whole viewport)."""
    region = _element_region(page, target) if target else None
    return ocr.find_all_text_in_image(
        _screenshot_image(page), _device_region(page, region)).strip()


def first_number(text):
    """Pure: first numeric token in OCR text as a string ('Now $1,299.99!' ->
    '1299.99'). NOOD_0141 — European formats parse too ('1.299,99' ->
    '1299.99') via the shared locale-tolerant parser. None when no digits."""
    from .probe import parse_number
    n = parse_number(text or "")
    return None if n is None else f"{n:g}"


def read_number(page, target=None):
    text = read_text(page, target)
    num = first_number(text)
    if num is None:
        raise AssertionError(
            f"No number found by OCR in '{target or 'screen'}'.\nOCR text:\n{text[:300]}")
    return num


def click_text_in(page, target, text):
    """OCR-click `text` inside one element's rendered box (carousel tile…)."""
    region = _element_region(page, target)
    hit = ocr.find_text_in_image(
        _screenshot_image(page), text, _device_region(page, region))
    if hit is None:
        raise AssertionError(f"Could not find text '{text}' in '{target}' by OCR")
    x, y = _to_css(hit[0], hit[1], _dpr(page))
    page.mouse.click(x, y)


def _norm_ws(s):
    return " ".join((s or "").split()).lower()


def assert_image_text(page, target, text):
    got = read_text(page, target)
    if _norm_ws(text) not in _norm_ws(got):
        raise AssertionError(
            f"Expected '{target}' to show '{text}' — not found by OCR.\n"
            f"OCR read:\n{got[:300]}\nURL: {page.url}")


def assert_image_text_hidden(page, target, text):
    got = read_text(page, target)
    if _norm_ws(text) in _norm_ws(got):
        raise AssertionError(
            f"Expected '{target}' NOT to show '{text}' — but OCR found it.\nURL: {page.url}")


def assert_depicts(page, description, target=None):
    """Vision-LLM object/scene check ('a dog', 'a red sale banner') — not OCR.
    Nondeterministic: scenarios using it should carry @potential-flake."""
    from noodle.agents.visual import vision_locate
    img = _screenshot_image(page)
    region = _device_region(page, _element_region(page, target) if target else None)
    if region:
        img = img.crop((region["x"], region["y"],
                        region["x"] + region["width"],
                        region["y"] + region["height"]))
    verdict = vision_locate.image_matches(description, image=img)
    if verdict is None:
        msg = ("this step requires a vision LLM for image recognition, but no "
               "model is configured (set NOODLE_MODEL) — cannot verify "
               f"'{description}'. Tag the scenario @potential-flake due to "
               "image requirement.")
        print(f"⚠ Noodle: {msg}", file=sys.stderr)
        raise AssertionError(msg)
    if not verdict:
        where = f"'{target}' image" if target else "the screen"
        raise AssertionError(
            f"Vision model says {where} does not show '{description}'.\nURL: {page.url}")

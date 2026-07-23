"""Mobile element lookup (Phase F) — mirrors the web locator's chain:
accessibility first, POM YAML second, fail loudly third.

Strategies, in order: accessibility id → resource-id → content-desc (contains)
→ visible text (XPath contains) → iOS label/name → Windows Name/AutomationId
→ macOS title (NOOD_0032). Strategies for other platforms just return no
elements — one chain serves Android, iOS, Windows and Mac. POM entries reuse
the web pom.yaml files with mobile selector types:
accessibility_id | id | xpath | uiautomator.
"""
from noodle.agents.web import pom as web_pom
from noodle.log import logger


def _by():
    from appium.webdriver.common.appiumby import AppiumBy
    return AppiumBy


def _pom_find(driver, text: str):
    """Resolve `text` through the same pom.yaml chain the web locator uses,
    interpreting mobile selector types. Returns an element or None."""
    entry = web_pom._lookup(text)
    if entry is None:
        return None
    AppiumBy = _by()
    if isinstance(entry, str):                       # shorthand → accessibility id
        strategy, value = AppiumBy.ACCESSIBILITY_ID, entry
    else:
        selector_type = next(iter(entry)).lower()
        value = entry[selector_type]
        strategy = {
            "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
            "id": AppiumBy.ID,
            "xpath": AppiumBy.XPATH,
            "uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
        }.get(selector_type)
        if strategy is None:
            return None                              # web-only entry (css/testid/…)
    els = driver.find_elements(strategy, value)
    return els[0] if els else None


def find(driver, text: str):
    """Resolve a human label to an Appium element, or raise with the chain it
    tried (fail loudly — no silent None like a missing web element gives)."""
    AppiumBy = _by()
    quoted = text.replace('"', '\\"')
    strategies = [
        (AppiumBy.ACCESSIBILITY_ID, text),
        (AppiumBy.ID, text),
        (AppiumBy.XPATH, f'//*[contains(@content-desc, "{quoted}")]'),
        (AppiumBy.XPATH, f'//*[contains(@text, "{quoted}")]'),
        (AppiumBy.XPATH, f'//*[contains(@label, "{quoted}") or contains(@name, "{quoted}")]'),  # iOS
        (AppiumBy.XPATH, f'//*[contains(@Name, "{quoted}") or contains(@AutomationId, "{quoted}")]'),  # Windows (UIA)
        (AppiumBy.XPATH, f'//*[contains(@title, "{quoted}")]'),  # macOS (Mac2)
    ]
    for strategy, value in strategies:
        try:
            els = driver.find_elements(strategy, value)
        except Exception:
            continue
        if els:
            return els[0]

    el = _pom_find(driver, text)
    if el is not None:
        logger.info(f"\n  📋 POM: resolved '{text}' via pom.yaml (mobile)")
        return el

    # NOOD_0032 — opt-in OCR fallback (same @ocr_fallback / NOODLE_OCR_FALLBACK
    # tag the web agent uses): the last resort for apps whose framework doesn't
    # expose accessible names at all — unlabeled Win32/MFC controls,
    # canvas-drawn UI, games. Returns a ('coordinate', x, y) sentinel instead
    # of an element; tap/fill/assert_visible/assert_hidden handle it.
    from noodle.agents.web.locator import _is_ocr_fallback
    if _is_ocr_fallback():
        from . import screen
        try:
            pos = screen.locate_text(driver, text)
        except Exception as e:
            logger.warning(f"\n  ⚠️  OCR fallback failed for '{text}': {e}")
            pos = None
        if pos is not None:
            logger.info(f"\n  🔧 Located '{text}' via OCR at ({pos[0]:.0f}, {pos[1]:.0f})")
            return ("coordinate", pos[0], pos[1])

    raise AssertionError(
        f"Could not find element '{text}' — tried accessibility id, "
        f"resource-id, content-desc, visible text/label/Name/title, and pom.yaml"
        + (" and OCR" if _is_ocr_fallback() else
           " (add @ocr_fallback to also try OCR)")
    )

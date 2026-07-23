"""NOOD_0155 — Tier-1 step patterns for the desktop wok's browserless helpers.

Spreadsheet reads/asserts (agents/desktop/spreadsheet.py). Same structure as
patterns.py / visual_patterns.py; consulted by step_resolver.resolve() only
after the web table misses, so these compose into any scenario — the
Excel-value-into-a-web-assertion flow. The desktop wok's *UI-driving* verbs
stay where they were: visual_patterns.py (@visual) and the Appium
@windows/@mac step set.
"""
import re

PATTERNS = [
    (r'^reads? cell ["\'](.+?)["\'] from sheet ["\'](.+?)["\'] of '
     r'(?:spreadsheet|workbook) ["\'](.+?)["\'] in(?:to)? ["\'](.+?)["\']$',
     'desktop_read_cell', lambda m: {'cell': m.group(1), 'sheet': m.group(2),
                                     'file': m.group(3), 'var': m.group(4)}),

    (r'^reads? cell ["\'](.+?)["\'] from (?:spreadsheet|workbook) ["\'](.+?)["\'] '
     r'in(?:to) ?["\'](.+?)["\']$',
     'desktop_read_cell', lambda m: {'cell': m.group(1), 'sheet': None,
                                     'file': m.group(2), 'var': m.group(3)}),

    # "expects ... to equal" works everywhere ("expects" is stripped as
    # subject phrasing before matching). The natural "should equal/should be"
    # phrasing only wins inside a desktop-wok scenario (@windows/@mac), where
    # this table outranks the web assert_compare catch-all that owns
    # "X should equal Y" elsewhere (wok.pattern_priority — tag-aware grammar).
    (r'^(?:expects? )?cell ["\'](.+?)["\'] (?:of|in) (?:spreadsheet|workbook) '
     r'["\'](.+?)["\'] (?:to|should) (?:equal|be) ["\'](.*?)["\']$',
     'desktop_assert_cell', lambda m: {'cell': m.group(1), 'sheet': None,
                                       'file': m.group(2), 'expected': m.group(3)}),

    (r'^(?:expects? )?cell ["\'](.+?)["\'] (?:of|in) sheet ["\'](.+?)["\'] of '
     r'(?:spreadsheet|workbook) ["\'](.+?)["\'] (?:to|should) (?:equal|be) ["\'](.*?)["\']$',
     'desktop_assert_cell', lambda m: {'cell': m.group(1), 'sheet': m.group(2),
                                       'file': m.group(3), 'expected': m.group(4)}),
]


def match(step_text: str):
    """Return (action_type, params) or None."""
    for pattern, action_type, extractor in PATTERNS:
        m = re.match(pattern, step_text.strip(), re.IGNORECASE)
        if m:
            return action_type, extractor(m)
    return None

"""
Tier-1 step patterns for the visual/desktop agent.
Same structure as patterns.py — PATTERNS list + match() function.

NOOD_0067 — verbs are written in the engine's canonical 3rd person, same as
patterns.py, because callers hand us text that has already been through
normalize_subject() ("I click image ..." → "clicks image ..."). The trailing
`s`/`es` stays optional so the bare infinitive ("click image ...") a caller may
pass un-normalized still matches.
"""
import re

PATTERNS = [
    # Image matching
    (r'^clicks? image ["\'](.+?)["\']$',
     'click_image', lambda m: {'template': m.group(1), 'confidence': 0.85}),

    (r'^clicks? image ["\'](.+?)["\'] with confidence ([\d.]+)$',
     'click_image', lambda m: {'template': m.group(1), 'confidence': float(m.group(2))}),

    (r'^right-?clicks? image ["\'](.+?)["\']$',
     'right_click_image', lambda m: {'template': m.group(1)}),

    (r'^double-?clicks? image ["\'](.+?)["\']$',
     'double_click_image', lambda m: {'template': m.group(1)}),

    (r'^scrolls? to image ["\'](.+?)["\']$',
     'scroll_to_image', lambda m: {'template': m.group(1)}),

    (r'^should see image ["\'](.+?)["\'] on screen$',
     'assert_image_visible', lambda m: {'template': m.group(1)}),

    (r'^should not see image ["\'](.+?)["\'] on screen$',
     'assert_image_hidden', lambda m: {'template': m.group(1)}),

    (r'^waits? until image ["\'](.+?)["\'] appears?$',
     'wait_image_visible', lambda m: {'template': m.group(1)}),

    (r'^waits? until image ["\'](.+?)["\'] disappears?$',
     'wait_image_hidden', lambda m: {'template': m.group(1)}),

    # OCR / text on screen
    (r'^clicks? text ["\'](.+?)["\'] on screen$',
     'click_text', lambda m: {'text': m.group(1)}),

    (r'^should see text ["\'](.+?)["\'] on screen$',
     'assert_text_visible', lambda m: {'text': m.group(1)}),

    (r'^waits? until text ["\'](.+?)["\'] appears? on screen$',
     'wait_text_visible', lambda m: {'text': m.group(1)}),

    # Keyboard / typing
    (r'^types? ["\'](.+?)["\']$',
     'type_text', lambda m: {'text': m.group(1)}),

    (r'^press(?:es)? key ["\'](.+?)["\']$',
     'press_key', lambda m: {'key': m.group(1)}),

    # Scroll
    (r'^scrolls? down (\d+) times?$',
     'scroll', lambda m: {'direction': 'down', 'clicks': int(m.group(1))}),

    (r'^scrolls? up (\d+) times?$',
     'scroll', lambda m: {'direction': 'up', 'clicks': int(m.group(1))}),

    # Drag
    (r'^drags? ["\'](.+?)["\'] to ["\'](.+?)["\']$',
     'drag_image', lambda m: {'source': m.group(1), 'target': m.group(2)}),

    # Region focus
    (r'^focus(?:es)? on screen region ["\'](.+?)["\']$',
     'focus_region', lambda m: {'region': m.group(1)}),

    # Window management (Phase G3)
    (r'^focus(?:es)? (?:the )?window ["\'](.+?)["\']$',
     'focus_window', lambda m: {'title': m.group(1)}),
]


def match(step_text: str):
    """Return (action_type, params) or None."""
    for pattern, action_type, extractor in PATTERNS:
        m = re.match(pattern, step_text.strip(), re.IGNORECASE)
        if m:
            return action_type, extractor(m)
    return None

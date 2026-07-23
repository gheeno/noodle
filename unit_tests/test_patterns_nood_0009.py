"""NOOD_0009 — web-testing gap fills: every new pattern resolves to the right
action with the right params, and the neighbouring patterns they were wedged
between still route as before.
"""
from noodle.resolver.patterns import match

# --- Scoped assertions --------------------------------------------------------

def test_assert_in_row():
    assert match("should see 'Shipped' in the row containing 'Order #123'") == \
        ("assert_in_row", {"negate": False, "text": "Shipped", "row": "Order #123"})
    assert match("should not see 'Error' in the row with 'Order #123'") == \
        ("assert_in_row", {"negate": True, "text": "Error", "row": "Order #123"})


def test_assert_in_section():
    assert match("should see 'Total: $42' in the 'Summary' section") == \
        ("assert_in_section", {"negate": False, "text": "Total: $42", "section": "Summary"})
    assert match("should not see 'Discount' in the 'Summary' panel") == \
        ("assert_in_section", {"negate": True, "text": "Discount", "section": "Summary"})


def test_generic_should_see_still_works():
    assert match("should see 'Welcome'") == ("assert_visible", {"text": "Welcome"})
    assert match("should not see 'Error'") == ("assert_hidden", {"text": "Error"})


# --- Scoped fills ---------------------------------------------------------------

def test_fill_in_row():
    assert match("enters '5' in the 'Qty' field in the row containing 'Widget'") == \
        ("fill_in_row", {"value": "5", "locator": "Qty", "row": "Widget"})


def test_fill_in_section():
    assert match("types 'Alice' into the 'Name' field in the 'Billing' section") == \
        ("fill_in_section", {"value": "Alice", "locator": "Name", "section": "Billing"})


def test_generic_fill_still_works():
    assert match("enters 'a@b.com' in the email field") == \
        ("fill", {"value": "a@b.com", "locator": "email"})


# --- Keyboard chords -----------------------------------------------------------

def test_key_chords():
    assert match("presses 'Control+A'") == ("press_key", {"key": "Control+A"})
    assert match("presses 'Ctrl+Shift+K'") == ("press_key", {"key": "Ctrl+Shift+K"})
    assert match("presses Shift+Tab") == ("press_key", {"key": "Shift+Tab"})


def test_single_keys_and_button_clicks_unchanged():
    assert match("presses Enter") == ("press_key", {"key": "Enter"})
    assert match("presses the login button") == ("click", {"locator": "login"})


def test_press_key_chord_normalises_modifiers():
    from noodle.agents.web import actions

    class _KB:
        def __init__(self):
            self.pressed = []

        def press(self, k):
            self.pressed.append(k)

    class _Page:
        keyboard = None

    p = _Page()
    p.keyboard = _KB()
    actions.press_key(p, "Ctrl+A")
    actions.press_key(p, "Cmd + Shift + p")
    actions.press_key(p, "Escape")
    assert p.keyboard.pressed == ["Control+A", "Meta+Shift+p", "Escape"]


# --- Cookies / storage -----------------------------------------------------------

def test_cookies_and_storage():
    assert match("clears all cookies") == ("clear_cookies", {})
    assert match("clears cookies") == ("clear_cookies", {})
    assert match("clears the local storage") == ("clear_storage", {"kind": "local"})
    assert match("clears session storage") == ("clear_storage", {"kind": "session"})
    assert match("sets the cookie 'session' to 'abc123'") == \
        ("set_cookie", {"name": "session", "value": "abc123"})


def test_clear_field_still_routes_to_form_clear():
    assert match("clears the search field") == ("clear", {"locator": "search"})


# --- Drag and drop ---------------------------------------------------------------

def test_drag():
    assert match("drags 'Card A' onto 'Done column'") == \
        ("drag", {"source": "Card A", "target": "Done column"})
    assert match("drags 'file.png' to the 'upload area'") == \
        ("drag", {"source": "file.png", "target": "upload area"})


# --- iframe exit -----------------------------------------------------------------

def test_switch_main_frame():
    assert match("switches back to the main frame") == ("switch_main_frame", {})
    assert match("switches to the main content") == ("switch_main_frame", {})
    # named iframe entry unchanged; "main tab" still routes to tab switching
    assert match("switches to the 'payment' iframe") == ("switch_frame", {"name": "payment"})
    assert match("switches to the main window") == ("switch_tab", {"target": "main"})


# --- Per-step wait timeout --------------------------------------------------------

def test_wait_with_timeout():
    assert match("waits until 'Report ready' is visible for up to 30 seconds") == \
        ("wait_visible", {"text": "Report ready", "timeout": 30000})
    assert match("waits until 'Spinner' disappears within 45 seconds") == \
        ("wait_hidden", {"text": "Spinner", "timeout": 45000})


def test_wait_without_timeout_unchanged():
    assert match("waits until 'Welcome' is visible") == ("wait_visible", {"text": "Welcome"})


# --- Count comparisons ------------------------------------------------------------

def test_count_comparisons():
    assert match("should see at least 3 'product' items") == \
        ("assert_count", {"count": 3, "locator": "product", "op": ">="})
    assert match("should see at most 10 results") == \
        ("assert_count", {"count": 10, "locator": "results", "op": "<="})
    assert match("should see more than 0 rows") == \
        ("assert_count", {"count": 0, "locator": "rows", "op": ">"})
    assert match("should see fewer than 5 errors") == \
        ("assert_count", {"count": 5, "locator": "errors", "op": "<"})


def test_exact_count_unchanged():
    assert match("should see 3 results") == ("assert_count", {"count": 3, "locator": "results"})


# --- URL asserts ------------------------------------------------------------------

def test_url_modes():
    assert match("should have url ending with '/checkout'") == \
        ("assert_url", {"fragment": "/checkout", "mode": "ends"})
    assert match("the url should be 'https://example.com/done'") == \
        ("assert_url", {"fragment": "https://example.com/done", "mode": "exact"})
    assert match("should have url containing '/cart'") == \
        ("assert_url", {"fragment": "/cart"})

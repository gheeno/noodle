"""NOOD_0062 — step-engine robustness: qualifier phrasing ("the button with
a label 'X'"), page-contains, gone/no-longer-seen, generic element waits,
grammar tolerance (past tense, bare infinitive, smart quotes, doubled
whitespace, trailing punctuation) and the verify-that wrapper.

Every test goes through the same pipeline resolve() uses:
match(normalize_phrasing(normalize_subject(text))).
"""
import pytest

from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))


# --- the NOOD_0062 headline examples ------------------------------------------

def test_click_button_with_label():
    assert _resolve("the user clicks the button with a label 'stonemountain'") == \
        ("click", {"locator": "stonemountain"})


def test_page_with_url_value():
    assert _resolve("the login page with the url value of 'www.stone.com'") == \
        ("navigate", {"url": "www.stone.com"})


def test_element_with_text_value_is_seen():
    assert _resolve("the element with the text value of '1234xyz' is seen") == \
        ("assert_visible", {"text": "1234xyz"})


def test_page_does_not_contain_text():
    assert _resolve("the page does not contain the text '1234'") == \
        ("assert_hidden", {"text": "1234"})


def test_quoted_is_gone():
    assert _resolve("the 'loading icon' is gone") == \
        ("assert_hidden", {"text": "loading icon"})


def test_unquoted_no_longer_seen():
    assert _resolve("the loading icon is no longer seen") == \
        ("assert_hidden", {"text": "loading icon"})


def test_waits_for_bare_element():
    assert _resolve("user waits for the loading icon") == \
        ("wait_visible", {"text": "loading icon"})


# --- click qualifiers ----------------------------------------------------------

@pytest.mark.parametrize("step", [
    "clicks the button with a label 'Save'",
    "clicks the link whose text is 'Save'",
    "clicks on the element containing 'Save'",
    "taps the button labelled 'Save'",
    "presses the button with the text value of 'Save'",
    "clicks the menu item with the name 'Save'",
])
def test_click_qualifier_variants(step):
    assert _resolve(step) == ("click", {"locator": "Save"})


def test_click_on_tolerated():
    assert _resolve("clicks on the login button") == ("click", {"locator": "login"})
    assert _resolve("clicks on 'Login'") == ("click", {"locator": "Login"})
    assert _resolve("clicks upon the Login link") == ("click", {"locator": "Login"})


def test_existing_click_patterns_unchanged():
    assert _resolve("User clicks the login button") == ("click", {"locator": "login"})
    assert _resolve("clicks 'Login'") == ("click", {"locator": "Login"})


# --- navigate phrasings ---------------------------------------------------------

@pytest.mark.parametrize("step", [
    "visits 'http://x.test'",
    "browses to 'http://x.test'",
    "opens the page 'http://x.test'",
    "the home page with the url 'http://x.test'",
    "is on the checkout page whose url is 'http://x.test'",
])
def test_navigate_variants(step):
    assert _resolve(step) == ("navigate", {"url": "http://x.test"})


def test_navigate_existing_unchanged():
    assert _resolve("User is on 'http://x.test'") == ("navigate", {"url": "http://x.test"})


# --- visibility phrasings --------------------------------------------------------

@pytest.mark.parametrize("step,expected", [
    ("the user can see 'Welcome'", "assert_visible"),
    ("the user sees 'Welcome'", "assert_visible"),
    ("the user is able to see the message 'Welcome'", "assert_visible"),
    ("I can see 'Welcome'", "assert_visible"),
    ("the user cannot see 'Welcome'", "assert_hidden"),
    ("the user can no longer see 'Welcome' anymore", "assert_hidden"),
    ("the page contains the text 'Welcome'", "assert_visible"),
    ("the page shows 'Welcome'", "assert_visible"),
    ("the page should display the message 'Welcome'", "assert_visible"),
    ("the page doesn't contain 'Welcome'", "assert_hidden"),
    ("the page no longer shows the text 'Welcome'", "assert_hidden"),
    ("'Welcome' is visible", "assert_visible"),
    ("the 'Welcome' banner is visible on the page", "assert_visible"),
    ("the 'Welcome' message is displayed", "assert_visible"),
    ("the welcome banner is displayed", "assert_visible"),
    ("the 'spinner' is dismissed", "assert_hidden"),
    ("the 'spinner' is not visible anymore", "assert_hidden"),
    ("the spinner is not visible", "assert_hidden"),
    ("the spinner was gone", "assert_hidden"),
    ("the element with text 'Welcome' is shown", "assert_visible"),
    ("the element containing 'Welcome' is not seen", "assert_hidden"),
])
def test_visibility_variants(step, expected):
    result = _resolve(step)
    assert result is not None, step
    assert result[0] == expected, (step, result)


def test_visibility_captures_text():
    assert _resolve("the page contains 'Welcome'") == ("assert_visible", {"text": "Welcome"})
    assert _resolve("the 'spinner' is gone") == ("assert_hidden", {"text": "spinner"})


# --- waits -----------------------------------------------------------------------

@pytest.mark.parametrize("step,expected_type,text", [
    ("waits for the loading icon", "wait_visible", "loading icon"),
    ("waits for the 'loading icon'", "wait_visible", "loading icon"),
    ("waits for the loading icon to disappear", "wait_hidden", "loading icon"),
    ("waits for the 'spinner' to be gone", "wait_hidden", "spinner"),
    ("waits for the toast to appear", "wait_visible", "toast"),
    ("waits for the results to finish loading", "wait_visible", "results"),
])
def test_wait_variants(step, expected_type, text):
    assert _resolve(step) == (expected_type, {"text": text})


def test_wait_timed_and_load_not_hijacked():
    assert _resolve("waits for the page to load") == ("wait_load", {})
    assert _resolve("waits 3 seconds") == ("wait_seconds", {"seconds": 3.0})
    assert _resolve("waits for up to 5 seconds") == ("wait_seconds", {"seconds": 5.0})
    assert _resolve("waits for the network to be idle") == ("wait_networkidle", {})


# --- state / URL extras -------------------------------------------------------------

def test_state_variants():
    assert _resolve("the save button is disabled") == \
        ("assert_state", {"locator": "save", "state": "disabled"})
    assert _resolve("the 'Save' button should be clickable") == \
        ("assert_state", {"locator": "Save", "state": "enabled"})
    assert _resolve("the submit button is greyed out") == \
        ("assert_state", {"locator": "submit", "state": "disabled"})


def test_url_variants():
    assert _resolve("the user lands on the checkout page") == \
        ("assert_url", {"fragment": "checkout"})
    assert _resolve("the user is redirected to 'http://x.test/next'") == \
        ("assert_url", {"fragment": "http://x.test/next"})
    assert _resolve("the url contains 'checkout'") == \
        ("assert_url", {"fragment": "checkout"})
    # existing phrasing unchanged
    assert _resolve("should be on the checkout page") == \
        ("assert_url", {"fragment": "checkout"})


# --- fills / selects ------------------------------------------------------------------

def test_fill_variants():
    assert _resolve("enters 'john' as the username") == \
        ("fill", {"value": "john", "locator": "username"})
    assert _resolve("types 'abc' into the field with the label 'Username'") == \
        ("fill", {"value": "abc", "locator": "Username"})
    assert _resolve("the user inputs 'abc' in the search field") == \
        ("fill", {"value": "abc", "locator": "search"})


def test_select_variants():
    assert _resolve("chooses 'Ontario' from the province dropdown") == \
        ("select", {"value": "Ontario", "locator": "province dropdown"})
    assert _resolve("picks 'Ontario' from the province dropdown") == \
        ("select", {"value": "Ontario", "locator": "province dropdown"})
    assert _resolve("selects 'Ontario' option from the province dropdown") == \
        ("select", {"value": "Ontario", "locator": "province dropdown"})


# --- grammar tolerance ------------------------------------------------------------------

def test_past_tense():
    assert _resolve("the user clicked the login button") == ("click", {"locator": "login"})
    assert _resolve("the user entered 'x' in the name field") == \
        ("fill", {"value": "x", "locator": "name"})
    assert _resolve("the user went to 'http://x.test'") == ("navigate", {"url": "http://x.test"})


def test_bare_infinitive_no_subject():
    assert _resolve("click the login button") == ("click", {"locator": "login"})
    assert _resolve("clicked the login button") == ("click", {"locator": "login"})


def test_smart_quotes_and_whitespace():
    assert _resolve("clicks  the  button with a label ‘stonemountain’") == \
        ("click", {"locator": "stonemountain"})
    assert _resolve("the user clicks “Login”.") == ("click", {"locator": "Login"})


def test_trailing_period_stripped_outside_quotes_only():
    assert _resolve("navigates to 'http://x.test/a.'") == \
        ("navigate", {"url": "http://x.test/a."})
    assert _resolve("waits 3 seconds.") == ("wait_seconds", {"seconds": 3.0})


# --- verify-that wrapper --------------------------------------------------------------

@pytest.mark.parametrize("step,expected", [
    ("verifies that 'Welcome' is visible", ("assert_visible", {"text": "Welcome"})),
    ("verify the page contains 'Welcome'", ("assert_visible", {"text": "Welcome"})),
    ("ensures that the user sees 'Welcome'", ("assert_visible", {"text": "Welcome"})),
    ("makes sure the 'spinner' is gone", ("assert_hidden", {"text": "spinner"})),
    ("checks that the save button is disabled",
     ("assert_state", {"locator": "save", "state": "disabled"})),
])
def test_verify_wrapper(step, expected):
    assert _resolve(step) == expected


def test_verify_wrapper_does_not_break_checkbox():
    assert _resolve("checks the 'Remember me' checkbox") == \
        ("check", {"locator": "Remember me"})
    assert _resolve("checks the terms checkbox") == ("check", {"locator": "terms"})


# --- collision regressions --------------------------------------------------------------

def test_conditionals_not_hijacked():
    t, p = _resolve("if 'Cookie banner' appears, clicks 'Accept all'")
    assert t == "run_if" and p["condition"] == "Cookie banner"
    t, p = _resolve("clicks 'Skip' if 'Tour popup' appears")
    assert t == "run_if"


def test_existing_asserts_not_hijacked():
    assert _resolve("should see 'VHS Catalog'") == ("assert_visible", {"text": "VHS Catalog"})
    assert _resolve("should not see 'Your Cart'") == ("assert_hidden", {"text": "Your Cart"})
    assert _resolve("the page title should contain 'Catalog'") == \
        ("assert_title", {"fragment": "Catalog"})
    assert _resolve("the page should have no accessibility violations")[0] == "assert_a11y"
    t, _ = _resolve("the screen should match the pixel baseline")
    assert t == "pixel_baseline"
    t, _ = _resolve("the 'Status' column should contain 'Active'")
    assert t == "assert_column_contains"
    t, _ = _resolve("the cart badge should equal '3'")
    assert t == "assert_compare"

"""Phase 11 — step coverage expansion: every new pattern resolves correctly,
new verbs normalise from 1st person, and stored vars substitute.

Patterns are matched in 3rd-person form (after normalize_subject), mirroring the
existing pattern tests.
"""
from noodle.orchestrator.runner import substitute
from noodle.resolver.patterns import match, normalize_subject

# --- Tier A -----------------------------------------------------------------

def test_keyboard_keys():
    assert match("presses Enter") == ("press_key", {"key": "Enter"})
    assert match("presses the Escape key") == ("press_key", {"key": "Escape"})
    assert match("presses Tab") == ("press_key", {"key": "Tab"})


def test_press_button_is_still_a_click_not_a_key():
    assert match("presses the login button") == ("click", {"locator": "login"})


def test_hover():
    assert match("hovers over the Account menu") == ("hover", {"locator": "Account menu"})


def test_wait_hidden():
    assert match('waits until "Loading" disappears') == ("wait_hidden", {"text": "Loading"})
    assert match("waits until the spinner is gone") == ("wait_hidden", {"text": "the spinner"})


def test_assert_value():
    assert match('the "Email" field should contain "a@b.com"') == \
        ("assert_value", {"locator": "Email", "value": "a@b.com"})
    assert match('the Email should have value "a@b.com"') == \
        ("assert_value", {"locator": "Email", "value": "a@b.com"})


def test_assert_value_not():
    # NOOD_0021 — negated mirror of assert_value, scoped to one element.
    assert match('the "trailer runtime" should not contain "undefined"') == \
        ("assert_value_not", {"locator": "trailer runtime", "value": "undefined"})


def test_assert_state():
    assert match('the "Submit" button should be disabled') == \
        ("assert_state", {"locator": "Submit", "state": "disabled"})
    assert match("the Remember me should be checked") == \
        ("assert_state", {"locator": "Remember me", "state": "checked"})


def test_assert_attribute_covers_svg():
    assert match('the chart line should have attribute "stroke" equal to "green"') == \
        ("assert_attribute", {"locator": "chart line", "attribute": "stroke", "value": "green"})


def test_assert_count():
    assert match("should see 3 results") == ("assert_count", {"count": 3, "locator": "results"})
    # plain "should see X" must still work (count pattern requires a leading number)
    assert match("should see Welcome") == ("assert_visible", {"text": "Welcome"})


def test_store_text():
    assert match("stores the order number as [ORDER]") == \
        ("store_text", {"locator": "order number", "var": "ORDER"})


# --- Tier B -----------------------------------------------------------------

def test_click_in_row():
    assert match('clicks "Edit" in the row containing "Contoso"') == \
        ("click_in_row", {"locator": "Edit", "row": "Contoso"})


def test_click_in_section():
    # Recommended phrasing: quote the target, then "in the '<section>' section".
    assert match('clicks "Save" in the "Payment" section') == \
        ("click_in_section", {"locator": "Save", "section": "Payment"})


def test_assert_cell():
    assert match('the cell in row "Contoso" column "Status" should be "Active"') == \
        ("assert_cell", {"row": "Contoso", "column": "Status", "expected": "Active"})


def test_assert_row_count():
    assert match("the grid should have 5 rows") == ("assert_row_count", {"count": 5})


def test_switch_frame():
    assert match('switches to the "main" frame') == ("switch_frame", {"name": "main"})


# --- normalize: new 1st-person verbs ---------------------------------------

def test_first_person_verbs_normalise():
    assert normalize_subject("I hover over the menu") == "hovers over the menu"
    assert normalize_subject("I store the total as [T]") == "stores the total as [T]"
    assert normalize_subject('I switch to the "x" frame') == 'switches to the "x" frame'


# --- store_text -> substitute round-trip -----------------------------------

def test_stored_var_substitutes():
    vars_ = {"ORDER": "ABC-123"}
    assert substitute("User should see [ORDER]", vars_) == "User should see ABC-123"
    # unknown var is left untouched (no env, no store)
    assert substitute("see [NOPE]", {}) == "see [NOPE]"


def test_existing_patterns_unaffected():
    assert match("clicks the login button") == ("click", {"locator": "login"})
    assert match('is on "https://x.com"') == ("navigate", {"url": "https://x.com"})
    assert match("should not see Error") == ("assert_hidden", {"text": "Error"})

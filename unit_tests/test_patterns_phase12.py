"""Phase 12 — step dependencies & shared state.

Covers: the new patterns (set_var, store_attribute, comparisons), the
assert_compare action's numeric/string logic, and the full
substitute -> resolve -> dispatch chain end to end (no browser needed, since
set_var and assert_compare never touch the DOM).
"""
import types

import pytest

from noodle.agents.web.actions import assert_compare
from noodle.orchestrator.runner import execute_step
from noodle.resolver.patterns import match, normalize_subject

# --- patterns ---------------------------------------------------------------

def test_set_var():
    assert match('sets [TAX] to "0.13"') == ("set_var", {"var": "TAX", "value": "0.13"})


def test_set_var_backtick():
    assert match('sets `tax` to "0.13"') == ("set_var", {"var": "tax", "value": "0.13"})


def test_store_backtick():
    assert match("stores the result as `title`") == \
        ("store_text", {"locator": "result", "var": "title"})


def test_grab_is_alias_for_store():
    assert match("grabs the secondresulttitle as `result`") == \
        ("store_text", {"locator": "secondresulttitle", "var": "result"})


def test_search_step():
    assert match('searches for "office chair toolbox"') == \
        ("search", {"query": "office chair toolbox"})


def test_close_popups_step():
    assert match("closes all popups") == ("close_popups", {})
    assert match("closes the popup window") == ("close_popups", {})


def test_store_attribute_precedes_store_text():
    assert match('stores attribute "data-id" of the row as [ID]') == \
        ("store_attribute", {"attribute": "data-id", "locator": "row", "var": "ID"})
    # the generic store-text still works for the plain case
    assert match("stores the result as [Y]") == \
        ("store_text", {"locator": "result", "var": "Y"})


@pytest.mark.parametrize("sentence,op", [
    ('"42" should be greater than "7"', ">"),
    ("42 should be greater than 7", ">"),
    ('"7" should be less than "42"', "<"),
    ('"7" should be greater than or equal to "7"', ">="),
    ('"7" should be at least "5"', ">="),
    ('"7" should be less than or equal to "9"', "<="),
    ('"7" should be at most "9"', "<="),
    ('"42" should equal "42"', "=="),
    ('"42" should be equal to "42"', "=="),
    ('"42" should not equal "7"', "!="),
    ('"abc" should contain "b"', "contains"),
])
def test_comparison_patterns(sentence, op):
    action, params = match(sentence)
    assert action == "assert_compare"
    assert params["op"] == op


def test_first_person_set_normalises():
    assert normalize_subject('I set [X] to "5"') == 'sets [X] to "5"'


# --- assert_compare action --------------------------------------------------

def test_assert_compare_numeric_pass():
    assert_compare("42", ">", "7")        # no raise
    assert_compare("7", "<=", "7")
    assert_compare("8", "==", "8.0")      # numeric equality across formats


def test_assert_compare_numeric_fail():
    with pytest.raises(AssertionError):
        assert_compare("7", ">", "42")


def test_assert_compare_string():
    assert_compare("hello world", "contains", "world")
    with pytest.raises(AssertionError):
        assert_compare("hello", "contains", "xyz")


def test_assert_compare_rejects_nonnumeric_ordering():
    with pytest.raises(AssertionError):
        assert_compare("apple", ">", "banana")


# --- end-to-end: substitute -> resolve -> dispatch --------------------------

def _ctx():
    """Minimal behave-like context (no browser); page unused by these steps."""
    return types.SimpleNamespace(_vars={}, page=None)


def test_set_then_compare_round_trip():
    ctx = _ctx()
    execute_step('User sets [PRICE] to "42"', ctx)
    execute_step('User sets [BUDGET] to "50"', ctx)
    assert ctx._vars == {"PRICE": "42", "BUDGET": "50"}

    # [VAR] in a later step is substituted before the comparison runs
    execute_step("[PRICE] should be less than [BUDGET]", ctx)   # passes
    execute_step('[PRICE] should equal "42"', ctx)             # passes


def test_failing_comparison_raises_through_execute_step():
    ctx = _ctx()
    execute_step('User sets [Y] to "3"', ctx)
    with pytest.raises(AssertionError):
        execute_step('[Y] should be greater than "10"', ctx)


def test_backtick_is_capture_only_not_env(monkeypatch):
    from noodle.orchestrator.runner import substitute
    monkeypatch.setenv("SECRET", "from-env")
    # backticks read ONLY the run store, never .env
    assert substitute("`SECRET`", {}) == "`SECRET`"            # not in store -> untouched
    assert substitute("`SECRET`", {"SECRET": "captured"}) == "captured"
    # brackets still read .env
    assert substitute("[SECRET]", {}) == "from-env"


def test_backtick_round_trip_through_execute_step():
    ctx = _ctx()
    execute_step('User sets `price` to "42"', ctx)
    assert ctx._vars == {"PRICE": "42"}
    execute_step('`price` should equal "42"', ctx)             # passes
    execute_step('`price` should be less than "50"', ctx)      # passes

"""
NOOD_0063 — assert_compare() failure messages must read as an unambiguous
expected-vs-actual statement, not echo the raw operator ("'X' contains 'Y'
is not true" reads as if X were the assertion, when X is the actual value).
"""
import pytest

from noodle.agents.web.actions import assert_compare


class TestAssertCompareMessages:
    def test_contains_names_expected_and_actual(self):
        with pytest.raises(AssertionError) as exc:
            assert_compare("Office Chair Miniature 2-Drawer Green Steel Toolbox",
                            "contains", "Office Chair Mini Toolbox with 2 Drawers")
        msg = str(exc.value)
        assert msg.startswith(
            "Expected 'Office Chair Miniature 2-Drawer Green Steel Toolbox' "
            "to contain 'Office Chair Mini Toolbox with 2 Drawers'"
        )
        assert "but it did not" in msg
        assert "(compared as text)" in msg

    def test_equal_message(self):
        with pytest.raises(AssertionError, match=r"Expected 'foo' to equal 'bar', but they differ"):
            assert_compare("foo", "==", "bar")

    def test_not_equal_message(self):
        with pytest.raises(AssertionError, match=r"Expected 'foo' to not equal 'foo', but they are equal"):
            assert_compare("foo", "!=", "foo")

    def test_greater_than_message(self):
        with pytest.raises(AssertionError, match=r"Expected 1 to be greater than 2, but it was not"):
            assert_compare("1", ">", "2")

    def test_less_than_message(self):
        with pytest.raises(AssertionError, match=r"Expected 2 to be less than 1, but it was not"):
            assert_compare("2", "<", "1")

    def test_at_least_message(self):
        with pytest.raises(AssertionError, match=r"Expected 1 to be at least 2, but it was not"):
            assert_compare("1", ">=", "2")

    def test_at_most_message(self):
        with pytest.raises(AssertionError, match=r"Expected 2 to be at most 1, but it was not"):
            assert_compare("2", "<=", "1")

    def test_numeric_comparisons_tagged_as_numbers(self):
        with pytest.raises(AssertionError, match=r"\(compared as numbers\)$"):
            assert_compare("1", ">", "2")

    def test_passing_comparison_raises_nothing(self):
        assert_compare("hello world", "contains", "world") is None

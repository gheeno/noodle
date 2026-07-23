"""NOOD_0062 — Scenario Outline & Data Table readiness.

- validate.check_feature substitutes the first Examples row into Outline
  steps, so a step that only matches with real data ("waits <n> seconds")
  dry-runs as matched.
- _table_cells treats a headerless table's heading row as data instead of
  silently dropping it; label headers (| field | value |) stay labels.
- _row_get is case-insensitive for header-keyed tables (| Key | / | key |).
"""
from noodle.orchestrator.runner import _row_get, _table_cells
from noodle.repl.validate import check_feature


class _Row:
    def __init__(self, headings, cells):
        self.headings, self.cells = headings, cells


class _Table:
    def __init__(self, headings, rows):
        self.headings = headings
        self._rows = [_Row(headings, r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


class _Ctx:
    def __init__(self, headings, rows):
        self.table = _Table(headings, rows)
        self._vars = {}


# --- Scenario Outline validation ----------------------------------------------

OUTLINE = """\
Feature: outline
  Scenario Outline: waits
    When User waits <n> seconds
    And User enters <username> in the username field

    Examples:
      | n | username  |
      | 3 | reel_ryan |
"""


def test_outline_steps_validate_with_examples_substituted():
    result = check_feature(OUTLINE)
    assert result["error"] is None
    assert all(ok for _, ok in result["steps"]), result["steps"]


def test_outline_multiple_examples_blocks():
    text = OUTLINE + """
    Examples: more
      | n | username |
      | 5 | tanya    |
"""
    result = check_feature(text)
    assert all(ok for _, ok in result["steps"]), result["steps"]


def test_plain_scenario_unaffected():
    text = """\
Feature: plain
  Scenario: click
    When User clicks the login button
"""
    result = check_feature(text)
    assert all(ok for _, ok in result["steps"])


# --- data-table heuristics ------------------------------------------------------

def test_label_headings_are_skipped():
    ctx = _Ctx(["field", "value"], [["username", "ryan"]])
    assert _table_cells(ctx, "s") == [["username", "ryan"]]


def test_headerless_table_keeps_first_row():
    # Tester omitted the | field | value | label row — Gherkin makes the
    # first data row the headings; it must still be filled.
    ctx = _Ctx(["username", "ryan"], [["password", "pw"]])
    assert _table_cells(ctx, "s") == [["username", "ryan"], ["password", "pw"]]


def test_headings_as_data_false_keeps_headings_out():
    ctx = _Ctx(["Title", "Year"], [["Jaws", "1975"]])
    assert _table_cells(ctx, "s", headings_as_data=False) == [["Jaws", "1975"]]


def test_row_get_case_insensitive():
    row = _Row(["KEY", "value"], ["name", "Ada"])
    assert _row_get(row, "Key") == "name"
    assert _row_get(row, "Value") == "Ada"
    assert _row_get(row, "missing") is None
    assert _row_get(row, "missing", "") == ""

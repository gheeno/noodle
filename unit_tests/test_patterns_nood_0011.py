"""NOOD_0011 — grids & tables (Dynamics 365-style), table-driven form fill,
browser session persistence, authenticated api_call: every new pattern resolves
to the right action with the right params, the table-cell substitution helper
behaves, and the neighbouring patterns still route as before.
"""
import types

import pytest

from noodle.resolver.patterns import match
from noodle.resolver.step_resolver import VALID_TYPES

# --- Cell under a header --------------------------------------------------------

def test_assert_cell_under_header():
    assert match("the cell under 'Director' in the row containing 'Jaws' should be 'Steven Spielberg'") == \
        ("assert_cell", {"column": "Director", "row": "Jaws", "expected": "Steven Spielberg"})
    assert match("the cell under the 'Status' header in the row with 'Contoso' should contain 'Active'") == \
        ("assert_cell", {"column": "Status", "row": "Contoso", "expected": "Active"})


def test_assert_cell_under_header_empty_value():
    assert match("the cell under 'Notes' in the row containing 'Jaws' should be ''") == \
        ("assert_cell", {"column": "Notes", "row": "Jaws", "expected": ""})


def test_legacy_assert_cell_still_works():
    assert match("the cell in row 'Alice' column 'Role' should be 'Admin'") == \
        ("assert_cell", {"row": "Alice", "column": "Role", "expected": "Admin"})


def test_click_cell():
    assert match("clicks the cell under 'Actions' in the row containing 'Jaws'") == \
        ("click_cell", {"column": "Actions", "row": "Jaws"})
    assert match("clicks the cell under the 'Owner' column in the row with 'INV-001'") == \
        ("click_cell", {"column": "Owner", "row": "INV-001"})


def test_click_in_row_still_works():
    assert match("clicks 'Edit' in the row containing 'Order #123'") == \
        ("click_in_row", {"locator": "Edit", "row": "Order #123"})


# --- Row values -----------------------------------------------------------------

def test_assert_row_values_inline():
    assert match("the row containing 'Jaws' should have values '1975', 'Thriller' and 'Steven Spielberg'") == \
        ("assert_row_values", {"row": "Jaws", "values": ["1975", "Thriller", "Steven Spielberg"]})


def test_assert_row_values_table_form():
    # values None → runner reads the step's | column | value | table
    assert match("the row containing 'Jaws' should have values:") == \
        ("assert_row_values", {"row": "Jaws", "values": None})
    assert match("the row with 'INV-001' should have these values:") == \
        ("assert_row_values", {"row": "INV-001", "values": None})


# --- Headers / columns ------------------------------------------------------------

def test_assert_table_headers_inline():
    assert match("the table should have columns 'Title', 'Year' and 'Genre'") == \
        ("assert_table_headers", {"names": ["Title", "Year", "Genre"]})
    assert match("the grid should have column 'Status'") == \
        ("assert_table_headers", {"names": ["Status"]})


def test_assert_table_headers_table_form():
    assert match("the table should have columns:") == \
        ("assert_table_headers", {"names": None})


def test_assert_column_contains():
    assert match("the 'Genre' column should contain 'Thriller'") == \
        ("assert_column_contains", {"column": "Genre", "values": ["Thriller"]})
    assert match("the 'Price' column should contain:") == \
        ("assert_column_contains", {"column": "Price", "values": None})


def test_assert_table_rows():
    assert match("the grid should contain rows:") == ("assert_table_rows", {})
    assert match("the table should contain the rows:") == ("assert_table_rows", {})


def test_row_count_still_works():
    assert match("the table should have 5 rows") == ("assert_row_count", {"count": 5})


# --- Table/grid scrollbars ---------------------------------------------------------

def test_scroll_table():
    assert match("scrolls the table to the bottom") == \
        ("scroll_table", {"name": None, "direction": "bottom"})
    assert match("scrolls the grid right") == \
        ("scroll_table", {"name": None, "direction": "right"})
    assert match("scrolls the 'Movie catalog table' table to the top") == \
        ("scroll_table", {"name": "Movie catalog table", "direction": "top"})


def test_generic_scroll_still_works():
    assert match("scrolls down") == ("scroll", {"direction": "down"})
    assert match("scrolls to 'Footer'") == ("scroll_to", {"locator": "Footer"})


# --- Table-driven form fill ---------------------------------------------------------

def test_fill_form_table():
    assert match("fills in the form with:") == ("fill_form_table", {})
    assert match("fills the form with:") == ("fill_form_table", {})


def test_generic_fill_still_works():
    assert match("fills the username with 'bob'") == \
        ("fill", {"locator": "username", "value": "bob"})


# --- Session persistence -------------------------------------------------------------

def test_save_session():
    assert match("saves the browser session as 'reports/session.json'") == \
        ("save_session", {"path": "reports/session.json"})
    assert match("saves the session to 'auth.json'") == \
        ("save_session", {"path": "auth.json"})


# --- Runner plumbing -----------------------------------------------------------------

def test_valid_types_include_nood_0011_actions():
    for t in ("click_cell", "scroll_table", "assert_row_values",
              "assert_table_headers", "assert_column_contains",
              "assert_table_rows", "fill_form_table", "save_session"):
        assert t in VALID_TYPES, t


def test_table_cells_substitutes_vars():
    from noodle.orchestrator.runner import _table_cells
    ctx = types.SimpleNamespace(
        table=[types.SimpleNamespace(cells=["username", "`WHO`"])],
        _vars={"WHO": "alice"},
    )
    assert _table_cells(ctx, "fills in the form with:") == [["username", "alice"]]


def test_table_cells_missing_table_errors():
    from noodle.orchestrator.runner import _table_cells
    ctx = types.SimpleNamespace(table=None, _vars={})
    with pytest.raises(AssertionError, match="needs a Gherkin data table"):
        _table_cells(ctx, "fills in the form with:")

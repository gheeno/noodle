"""NOOD_0155 — desktop wok: spreadsheet reader, patterns, cross-wok dispatch.

The visual-agent engine tests (OpenCV matcher, OCR, windowing) predate the
wok concept and stay in unit_tests/test_visual_*.py; this folder owns the
wok's browserless side — stdlib .xlsx cell access and its composition into
web scenarios (the "Excel value into a web assertion" flow).
"""
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle import wok
from noodle.agents.desktop import spreadsheet
from noodle.orchestrator import runner
from noodle.resolver.step_resolver import resolve

_M = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _write_xlsx(path, sheets, shared=()):
    """Minimal hand-rolled .xlsx: {sheet_name: sheet_xml_cells}, optional
    shared strings. Only the parts spreadsheet.read_cell parses."""
    names = list(sheets)
    with zipfile.ZipFile(path, "w") as z:
        sheet_tags = "".join(
            f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(names))
        z.writestr("xl/workbook.xml",
                   f'<workbook {_M} {_R}><sheets>{sheet_tags}</sheets></workbook>')
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="t" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(names)))
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   f'package/2006/relationships">{rels}</Relationships>')
        for i, n in enumerate(names):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       f'<worksheet {_M}><sheetData><row r="1">{sheets[n]}</row>'
                       '</sheetData></worksheet>')
        if shared:
            sis = "".join(f"<si><t>{s}</t></si>" for s in shared)
            z.writestr("xl/sharedStrings.xml",
                       f'<sst {_M} count="{len(shared)}">{sis}</sst>')


@pytest.fixture
def workbook(tmp_path):
    path = tmp_path / "inventory.xlsx"
    _write_xlsx(path, {
        "Stock": ('<c r="A1" t="s"><v>0</v></c>'
                  '<c r="B2"><v>19.99</v></c>'
                  '<c r="C3"><v>42.0</v></c>'
                  '<c r="D4" t="b"><v>1</v></c>'
                  '<c r="E5" t="inlineStr"><is><t>Blade Runner</t></is></c>'),
        "Empty": '<c r="A1" t="s"><v>1</v></c>',
    }, shared=("Widget Pro", "Second Sheet"))
    return str(path)


# --- the reader ---------------------------------------------------------------

def test_reads_every_cell_type(workbook):
    assert spreadsheet.read_cell(workbook, "A1") == "Widget Pro"      # shared
    assert spreadsheet.read_cell(workbook, "B2") == "19.99"           # number
    assert spreadsheet.read_cell(workbook, "C3") == "42"              # 42.0 → 42
    assert spreadsheet.read_cell(workbook, "D4") == "TRUE"            # boolean
    assert spreadsheet.read_cell(workbook, "E5") == "Blade Runner"    # inline


def test_reads_named_sheet_and_defaults_to_first(workbook):
    assert spreadsheet.read_cell(workbook, "A1", sheet="Empty") == "Second Sheet"
    assert spreadsheet.read_cell(workbook, "A1", sheet="Stock") == "Widget Pro"


def test_empty_cell_is_empty_string(workbook):
    assert spreadsheet.read_cell(workbook, "Z99") == ""


def test_clear_errors_for_bad_inputs(workbook, tmp_path):
    with pytest.raises(AssertionError, match="No sheet named 'Prices'"):
        spreadsheet.read_cell(workbook, "A1", sheet="Prices")
    with pytest.raises(AssertionError, match="not a cell reference"):
        spreadsheet.read_cell(workbook, "1A")
    with pytest.raises(AssertionError, match="not found"):
        spreadsheet.read_cell(str(tmp_path / "ghost.xlsx"), "A1")
    junk = tmp_path / "junk.xlsx"
    junk.write_text("not a zip")
    with pytest.raises(AssertionError, match="bad zip"):
        spreadsheet.read_cell(str(junk), "A1")


# --- patterns -----------------------------------------------------------------

def test_spreadsheet_steps_resolve():
    read = resolve('User reads cell "B2" from spreadsheet "inv.xlsx" into "PRICE"')
    assert read == {'type': 'desktop_read_cell', 'cell': 'B2', 'sheet': None,
                    'file': 'inv.xlsx', 'var': 'PRICE'}
    sheet = resolve('reads cell "A1" from sheet "Stock" of workbook "inv.xlsx" into "NAME"')
    assert sheet['sheet'] == 'Stock'
    check = resolve('expects cell "C3" of spreadsheet "inv.xlsx" to equal "42"')
    assert check == {'type': 'desktop_assert_cell', 'cell': 'C3', 'sheet': None,
                     'file': 'inv.xlsx', 'expected': '42'}
    # Tag-aware grammar: untagged (web-first best guess) "should equal" stays
    # with the web assert_compare catch-all; inside a desktop-wok scenario
    # (@windows/@mac) the desktop table outranks it and the natural phrasing
    # is a real cell assertion (wok.pattern_priority).
    sentence = 'cell "C3" of spreadsheet "inv.xlsx" should equal "42"'
    assert resolve(sentence)['type'] == 'assert_compare'
    assert resolve(sentence, tags={'windows'})['type'] == 'desktop_assert_cell'
    assert resolve(sentence, tags={'mac'})['type'] == 'desktop_assert_cell'


# --- cross-wok dispatch -------------------------------------------------------

def _app_context(tmp_path, workbook_name="inventory.xlsx"):
    """A fake behave context whose feature lives in <app>/features/, with the
    workbook in the sibling resources/ — the documented fixture layout."""
    app = tmp_path / "erp"
    (app / "features").mkdir(parents=True)
    (app / "resources").mkdir()
    _write_xlsx(app / "resources" / workbook_name,
                {"Stock": '<c r="B2"><v>19.99</v></c>'})
    feature = SimpleNamespace(filename=str(app / "features" / "erp.feature"))
    return SimpleNamespace(page=MagicMock(), _vars={}, feature=feature)


def test_read_cell_resolves_against_app_resources(tmp_path):
    context = _app_context(tmp_path)
    runner.execute_step('User reads cell "B2" from spreadsheet "inventory.xlsx" into "PRICE"',
                        context)
    assert context._vars["PRICE"] == "19.99"


def test_excel_value_feeds_a_web_step(tmp_path, monkeypatch):
    # The scenario the wok concept exists for: desktop wok reads the workbook,
    # web wok uses the value — one scenario, two woks.
    fill = MagicMock()
    monkeypatch.setattr(runner.actions, "fill", fill)
    context = _app_context(tmp_path)
    runner.execute_step('User reads cell "B2" from spreadsheet "inventory.xlsx" into "PRICE"',
                        context)
    runner.execute_step('User fills "Unit price" with "{var:PRICE}"', context)
    assert fill.call_args[0][2] == "19.99"


def test_assert_cell_pass_and_fail(tmp_path):
    context = _app_context(tmp_path)
    runner.execute_step('expects cell "B2" of spreadsheet "inventory.xlsx" to equal "19.99"',
                        context)
    with pytest.raises(AssertionError, match="expected '20.00'"):
        runner.execute_step('expects cell "B2" of spreadsheet "inventory.xlsx" to equal "20.00"',
                            context)


def test_spreadsheet_steps_are_browserless(tmp_path):
    context = _app_context(tmp_path)
    context.page = None                   # @api/@perf scenario — no browser
    runner.execute_step('User reads cell "B2" from spreadsheet "inventory.xlsx" into "PRICE"',
                        context)
    assert context._vars["PRICE"] == "19.99"


# --- registry -----------------------------------------------------------------

def test_desktop_wok_covers_both_native_paths():
    w = wok.WOKS["desktop"]
    assert wok.wok_for_tags(["visual"]) is w      # pixel agent (SikuliX-style)
    assert wok.wok_for_tags(["windows"]) is w     # Appium WinAppDriver
    assert wok.wok_for_tags(["mac"]) is w         # Appium Mac2
    assert any("OpenCV" in e for e in w.engines)
    assert any("Spreadsheet" in e for e in w.engines)

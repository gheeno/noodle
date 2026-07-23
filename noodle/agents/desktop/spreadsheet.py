"""NOOD_0155 — desktop wok: read values out of Excel workbooks, browserless.

The cross-wok workhorse: "read cell B2 from inventory.xlsx, then assert that
price on the web app". An .xlsx is a zip of XML, so single-cell reads need no
openpyxl/pandas — stdlib zipfile + ElementTree keep this in the core install
and unit-testable offline. This reads the *file* (saved values, including a
formula's last-calculated result); driving the Excel *application* UI is the
visual/Appium side of the desktop wok.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _sheet_xml_path(z: zipfile.ZipFile, sheet: str | None) -> str:
    """The archive path of the named sheet's XML (first sheet when None)."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = wb.findall("m:sheets/m:sheet", _NS)
    if not sheets:
        raise AssertionError("Workbook has no sheets")
    if sheet is None:
        chosen = sheets[0]
    else:
        chosen = next((s for s in sheets if s.get("name") == sheet), None)
        if chosen is None:
            names = ", ".join(s.get("name", "?") for s in sheets)
            raise AssertionError(f"No sheet named '{sheet}' — workbook has: {names}")
    rid = chosen.get(f"{{{_NS['r']}}}id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall("pr:Relationship", _NS):
        if rel.get("Id") == rid:
            target = rel.get("Target", "")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise AssertionError(f"Workbook relationship {rid} not found")


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    # A shared string may be split across rich-text runs — join every <t>.
    return ["".join(t.text or "" for t in si.findall(".//m:t", _NS))
            for si in ET.fromstring(raw).findall("m:si", _NS)]


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    ctype = cell.get("t", "n")
    if ctype == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//m:t", _NS))
    v = cell.find("m:v", _NS)
    raw = (v.text or "") if v is not None else ""
    if ctype == "s":
        return shared[int(raw)] if raw else ""
    if ctype == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if raw and re.fullmatch(r"-?\d+\.0+", raw):
        return raw.split(".")[0]        # 42.0 → "42": step text compares strings
    return raw


def read_cell(path: str, cell_ref: str, sheet: str | None = None) -> str:
    """The displayed value of one cell ('B2'), '' for an empty/missing cell."""
    cell_ref = cell_ref.upper().strip()
    if not re.fullmatch(r"[A-Z]{1,3}[1-9]\d*", cell_ref):
        raise AssertionError(f"'{cell_ref}' is not a cell reference (like A1 or B12)")
    try:
        z = zipfile.ZipFile(path)
    except FileNotFoundError:
        raise AssertionError(f"Spreadsheet not found: {path}")
    except zipfile.BadZipFile:
        raise AssertionError(f"Not an .xlsx workbook (bad zip): {path}")
    with z:
        root = ET.fromstring(z.read(_sheet_xml_path(z, sheet)))
        target = root.find(f'.//m:c[@r="{cell_ref}"]', _NS)
        if target is None:
            return ""
        return _cell_text(target, _shared_strings(z))

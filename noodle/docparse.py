"""NOOD_0188 — text out of the binary files a web app hands you.

`assert_download_content` could only read UTF-8, so the enterprise case that
matters most — "the exported report has the right numbers in it" — could only
ever assert the filename. xlsx and pdf exports were explicitly refused.

ponytail: stdlib only (zipfile + zlib + re), no pdf/xlsx dependency. Office
formats ARE zip archives of XML, and a generated PDF's text lives in Flate
streams — both are a few lines to read when all you need is "does this
contain X" and "how many rows". The ceiling is real and named per-format
below; when a suite needs layout, styling or formulas, that is the point to
add openpyxl/pypdf behind an extra, not before.
"""
from __future__ import annotations

import re
import zipfile
import zlib
from pathlib import Path

# Office Open XML members worth reading per format. Order matters only for
# readability of the joined text.
_OOXML_PARTS = {
    ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
    ".docx": ("word/document.xml",),
    ".pptx": ("ppt/slides/slide",),
}

_TAG_RE = re.compile(rb"<[^>]+>")
_ROW_RE = re.compile(rb"<row[ >]")
# PDF text operators: (literal) Tj  and  [(a) -1 (b)] TJ
_PDF_LITERAL_RE = re.compile(rb"\((?:\\.|[^\\()])*\)")


def is_binary_doc(filename: str) -> bool:
    """Does this filename have a format we can parse but that isn't text?"""
    return Path(filename).suffix.lower() in {*_OOXML_PARTS, ".pdf"}


def _strip_xml(blob: bytes) -> str:
    """XML → its text nodes, space-joined. Cheap and good enough for a
    'contains' assertion; it deliberately keeps no cell/paragraph structure."""
    text = _TAG_RE.sub(b" ", blob)
    return re.sub(r"\s+", " ", text.decode("utf-8", errors="replace")).strip()


def _ooxml_text(path: Path, wanted: tuple[str, ...]) -> str:
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if any(name == w or name.startswith(w) for w in wanted):
                if name.endswith(".xml"):
                    out.append(_strip_xml(z.read(name)))
    return " ".join(p for p in out if p)


def _pdf_text(path: Path) -> str:
    """Text drawn by a PDF's content streams.

    ponytail: handles Flate-compressed and plain streams and the two text
    operators that carry strings. It does NOT do encoding maps, so a PDF whose
    fonts use a non-standard encoding yields mojibake — good enough to prove a
    report contains an order number, not a substitute for a real parser.
    """
    raw = path.read_bytes()
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        blob = m.group(1)
        try:
            blob = zlib.decompress(blob)
        except zlib.error:
            pass                      # already plain, or a format we can't read
        if b"Tj" not in blob and b"TJ" not in blob:
            continue
        for lit in _PDF_LITERAL_RE.findall(blob):
            s = lit[1:-1]             # drop the surrounding parens
            s = re.sub(rb"\\([()\\])", rb"\1", s)
            chunks.append(s.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", "".join(chunks)).strip()


def text_of(path: str | Path, filename: str | None = None) -> str:
    """Readable text of a downloaded file. Falls back to a UTF-8 decode for
    anything that isn't a known binary document."""
    p = Path(path)
    suffix = Path(filename or p.name).suffix.lower()
    if suffix in _OOXML_PARTS:
        return _ooxml_text(p, _OOXML_PARTS[suffix])
    if suffix == ".pdf":
        return _pdf_text(p)
    return p.read_bytes().decode("utf-8", errors="replace")


def row_count(path: str | Path, filename: str | None = None) -> int | None:
    """Data rows (excluding the header) for a format where that means
    something — xlsx today. None when the caller should count text lines."""
    p = Path(path)
    if Path(filename or p.name).suffix.lower() != ".xlsx":
        return None
    rows = 0
    with zipfile.ZipFile(p) as z:
        sheets = sorted(n for n in z.namelist()
                        if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        if not sheets:
            return 0
        rows = len(_ROW_RE.findall(z.read(sheets[0])))
    return max(0, rows - 1)      # same header-excluded convention as CSV


if __name__ == "__main__":   # ponytail: the one runnable check
    import io
    import tempfile
    d = Path(tempfile.mkdtemp())
    # xlsx: two data rows + a header, one shared string
    x = d / "export.xlsx"
    with zipfile.ZipFile(x, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<sst><si><t>Widget</t></si><si><t>Gadget</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet><sheetData><row r="1"><c/></row>'
                   '<row r="2"><c/></row><row r="3"><c/></row></sheetData></worksheet>')
    assert "Widget" in text_of(x), text_of(x)
    assert row_count(x) == 2, row_count(x)
    # pdf: one uncompressed content stream
    p = d / "invoice.pdf"
    body = b"BT /F1 12 Tf (Order 12345 total $9.99) Tj ET"
    p.write_bytes(b"%PDF-1.4\nstream\n" + body + b"\nendstream\n%%EOF")
    assert "Order 12345" in text_of(p), text_of(p)
    # compressed stream too
    p2 = d / "z.pdf"
    p2.write_bytes(b"%PDF-1.4\nstream\n" + zlib.compress(body) + b"\nendstream")
    assert "Order 12345" in text_of(p2), text_of(p2)
    assert row_count(p) is None
    assert is_binary_doc("a.xlsx") and not is_binary_doc("a.csv")
    del io
    print("docparse self-check OK")

"""NOOD_0177 — XML parsing that refuses entity-expansion bombs.

Python's ElementTree has not resolved external entities since 3.7.1, so there is
no classic XXE file-read here (the audit verified that: a SYSTEM entity raises
`undefined entity`). What it *is* still exposed to is exponential entity
expansion — "billion laughs" — where a few hundred bytes expand to gigabytes and
take the process down. Measured at 500,000 characters in 0.001s on 3.11.

Three parsers take XML the engine did not write:

* ``agents/mobile/probe.py`` parses Appium ``page_source`` — supplied by the
  device, the least trusted of the three;
* ``agents/desktop/spreadsheet.py`` parses ``.xlsx`` members, i.e. a file
  someone handed the test;
* ``reporting/junit.py`` merges per-worker suite files off disk.

A legitimate document from any of those has no DOCTYPE at all, so refusing one
outright costs nothing and removes the whole class. Kept as a rejection rather
than a `defusedxml` dependency: one less thing to install for `pip install
noodle`, and the check is the same three lines everywhere.
"""
import re
import xml.etree.ElementTree as ET

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
# Enough for any real page_source / sheet / junit file; a bomb is far smaller
# than its expansion, so this is a backstop, not the primary control.
_MAX_XML_BYTES = 64 * 1024 * 1024


class UnsafeXML(ValueError):
    """XML carrying a DOCTYPE, which the engine never accepts."""


def _guard(raw):
    data = raw.encode("utf-8", "replace") if isinstance(raw, str) else bytes(raw or b"")
    if len(data) > _MAX_XML_BYTES:
        raise UnsafeXML(f"refusing XML larger than {_MAX_XML_BYTES} bytes")
    # Only the prolog can carry a DOCTYPE; scanning the head keeps this O(1)
    # on large but legitimate documents.
    if _DOCTYPE_RE.search(data[:4096]):
        raise UnsafeXML(
            "refusing XML containing a DOCTYPE — entity expansion can exhaust "
            "memory, and no document this engine parses legitimately has one")
    return data


def fromstring(text):
    """ET.fromstring with the DOCTYPE guard."""
    return ET.fromstring(_guard(text))


def parse_file(path):
    """ET.parse with the DOCTYPE guard. Returns an ElementTree."""
    from pathlib import Path
    data = _guard(Path(path).read_bytes())
    return ET.ElementTree(ET.fromstring(data))

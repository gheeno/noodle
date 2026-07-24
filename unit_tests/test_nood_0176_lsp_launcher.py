"""NOOD_0176 — the VS Code extension launches the `noodle-lsp` console script
by preference. That entry point is a contract: if it's dropped from pyproject,
the extension silently falls back to guessing a python and hover breaks again.
"""
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
_EXT_JS = (Path(__file__).resolve().parent.parent
           / "vscode-extension" / "client" / "extension.js")


def _scripts():
    return tomllib.loads(_PYPROJECT.read_text())["project"]["scripts"]


def test_noodle_lsp_console_script_declared():
    scripts = _scripts()
    assert scripts.get("noodle-lsp") == "noodle.lsp.server:main", (
        "extension.js launches the `noodle-lsp` console script; keep this entry "
        "point (NOOD_0176)")


def test_lsp_server_main_exists():
    from noodle.lsp import server
    assert callable(server.main)  # what the console script points at


def test_extension_prefers_the_console_script():
    # Guard the extension actually reaches for noodle-lsp, not just `-m`.
    js = _EXT_JS.read_text()
    assert "noodle-lsp" in js and "findServer" in js

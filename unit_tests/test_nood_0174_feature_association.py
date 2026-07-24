"""NOOD_0174 — .vscode/settings.json files.associations merge (Option B)
plus the vendored-extension install helper."""
import json
from pathlib import Path

from noodle.cli import (
    _NOODLE_ASSOC,
    _install_extension_into,
    _merge_vscode_association,
)

_EXT_SRC = Path(__file__).resolve().parent.parent / "vscode-extension"


def test_creates_when_absent(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    assert _merge_vscode_association(f) == "created"
    assert json.loads(f.read_text())["files.associations"][_NOODLE_ASSOC] == "noodle"


def test_preserves_existing_settings(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps({
        "editor.tabSize": 2,
        "files.associations": {"*.env": "dotenv"},
    }))
    assert _merge_vscode_association(f) == "updated"
    data = json.loads(f.read_text())
    assert data["editor.tabSize"] == 2                      # unrelated key kept
    assert data["files.associations"]["*.env"] == "dotenv"  # existing assoc kept
    assert data["files.associations"][_NOODLE_ASSOC] == "noodle"


def test_idempotent(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    _merge_vscode_association(f)
    assert _merge_vscode_association(f) == "kept (already configured)"


def test_unparseable_json_is_left_alone(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    f.parent.mkdir()
    f.write_text("{ not json")
    assert _merge_vscode_association(f).startswith("kept (unparseable")
    assert f.read_text() == "{ not json"  # untouched


def test_install_extension_lands_and_points_at_source(tmp_path):
    dst, how = _install_extension_into(_EXT_SRC, tmp_path)
    assert how in ("linked", "copied")
    assert dst.parent == tmp_path
    assert (dst / "package.json").is_file()          # extension actually there
    ver = json.loads((_EXT_SRC / "package.json").read_text())["version"]
    assert dst.name == f"noodle.noodle-{ver}"        # publisher.name-version


def test_install_extension_replaces_prior_install(tmp_path):
    stale = tmp_path / "noodle.noodle-0.0.1"         # a leftover real-dir sideload
    stale.mkdir()
    (stale / "junk.txt").write_text("old")
    dst, _ = _install_extension_into(_EXT_SRC, tmp_path)
    assert not stale.exists()                        # old install removed
    assert [p.name for p in tmp_path.glob("noodle.noodle*")] == [dst.name]

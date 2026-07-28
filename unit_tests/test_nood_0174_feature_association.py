"""NOOD_0174 — .vscode/settings.json files.associations merge (Option B),
NOOD_0176 tests_dir scoping, plus the vendored-extension install helper."""
import json
from pathlib import Path

from noodle.cli import (
    _install_extension_into,
    _merge_vscode_association,
    _noodle_assoc,
)

_EXT_SRC = Path(__file__).resolve().parent.parent / "vscode-extension"
_ASSOC = _noodle_assoc("noodle_tests")  # "**/noodle_tests/**/*.feature"


def test_scoped_to_tests_dir():
    assert _noodle_assoc("noodle_tests") == "**/noodle_tests/**/*.feature"
    assert _noodle_assoc("tests") == "**/tests/**/*.feature"


def test_creates_when_absent(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    assert _merge_vscode_association(f, _ASSOC) == "created"
    assert json.loads(f.read_text(encoding="utf-8"))["files.associations"][_ASSOC] == "noodle"


def test_preserves_existing_settings(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps({
        "editor.tabSize": 2,
        "files.associations": {"*.env": "dotenv"},
    }), encoding="utf-8")
    assert _merge_vscode_association(f, _ASSOC) == "updated"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["editor.tabSize"] == 2                      # unrelated key kept
    assert data["files.associations"]["*.env"] == "dotenv"  # existing assoc kept
    assert data["files.associations"][_ASSOC] == "noodle"


def test_idempotent(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    _merge_vscode_association(f, _ASSOC)
    assert _merge_vscode_association(f, _ASSOC) == "kept (already configured)"


def test_migrates_old_broad_default(tmp_path):
    # A workspace scaffolded pre-0177 has the broad "**/*.feature" — re-init
    # should narrow it (drop the broad key, add the scoped one).
    f = tmp_path / ".vscode" / "settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"files.associations": {"**/*.feature": "noodle"}}), encoding="utf-8")
    assert _merge_vscode_association(f, _ASSOC) == "updated"
    assoc = json.loads(f.read_text(encoding="utf-8"))["files.associations"]
    assert "**/*.feature" not in assoc          # old broad default removed
    assert assoc[_ASSOC] == "noodle"            # scoped one added


def test_does_not_touch_a_users_own_star_feature(tmp_path):
    # If the scoped glob IS the broad one (tests_dir at root), don't self-delete.
    f = tmp_path / ".vscode" / "settings.json"
    assert _merge_vscode_association(f, "**/*.feature") == "created"
    assert json.loads(f.read_text(encoding="utf-8"))["files.associations"]["**/*.feature"] == "noodle"


def test_unparseable_json_is_left_alone(tmp_path):
    f = tmp_path / ".vscode" / "settings.json"
    f.parent.mkdir()
    f.write_text("{ not json", encoding="utf-8")
    assert _merge_vscode_association(f, _ASSOC).startswith("kept (unparseable")
    assert f.read_text(encoding="utf-8") == "{ not json"  # untouched


def test_install_extension_lands_and_points_at_source(tmp_path):
    dst, how = _install_extension_into(_EXT_SRC, tmp_path)
    assert how in ("linked", "copied")
    assert dst.parent == tmp_path
    assert (dst / "package.json").is_file()          # extension actually there
    ver = json.loads((_EXT_SRC / "package.json").read_text(encoding="utf-8"))["version"]
    assert dst.name == f"noodle.noodle-{ver}"        # publisher.name-version


def test_install_extension_replaces_prior_install(tmp_path):
    stale = tmp_path / "noodle.noodle-0.0.1"         # a leftover real-dir sideload
    stale.mkdir()
    (stale / "junk.txt").write_text("old", encoding="utf-8")
    dst, _ = _install_extension_into(_EXT_SRC, tmp_path)
    assert not stale.exists()                        # old install removed
    assert [p.name for p in tmp_path.glob("noodle.noodle*")] == [dst.name]

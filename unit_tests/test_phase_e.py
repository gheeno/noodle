"""Phase E — file-level web sharding discovery (scripts/list_features.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import list_features as lf  # noqa: E402


def _write(d, name, body):
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_web_file_included(tmp_path):
    p = _write(tmp_path, "a.feature", "@web\nFeature: A\n  Scenario: s\n    Given x\n")
    assert lf.is_web_shard(p)


def test_empty_or_comment_only_excluded(tmp_path):
    empty = _write(tmp_path, "e.feature", "")
    comment = _write(tmp_path, "c.feature", "# just a note\nFeature: C\n")
    assert not lf.is_web_shard(empty)
    assert not lf.is_web_shard(comment)


def test_non_web_platform_excluded(tmp_path):
    appium = _write(tmp_path, "m.feature", "@appium\nFeature: M\n  Scenario: s\n    Given x\n")
    desktop = _write(tmp_path, "d.feature", "Feature: D\n  @desktop\n  Scenario: s\n    Given x\n")
    assert not lf.is_web_shard(appium)
    assert not lf.is_web_shard(desktop)


def test_discover_only_returns_web(tmp_path):
    _write(tmp_path, "saucedemo/login.feature", "@web\nFeature: L\n  Scenario: s\n    Given x\n")
    _write(tmp_path, "mobile/smoke.feature", "@appium\nFeature: S\n  Scenario: s\n    Given x\n")
    found = lf.discover(str(tmp_path))
    assert len(found) == 1 and found[0].endswith("saucedemo/login.feature")


def test_matrix_keys_unique_and_shaped(tmp_path):
    paths = ["features/a/login.feature", "features/b/login.feature"]
    m = lf.to_matrix(paths)
    assert len(m) == 2  # same stem, distinct keys
    assert all(set(v) == {"featurePath"} for v in m.values())
    assert {v["featurePath"] for v in m.values()} == set(paths)

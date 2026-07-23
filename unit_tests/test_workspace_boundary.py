"""NOOD_0027 — the ancestor walk-up in pom.py/_global_pom_path() and
cli.py/_find_behave_base() must stop at the workspace root (the directory
holding noodle.yaml), not keep climbing into whatever happens to sit above
it. Real risk once a test repo and the noodle engine repo are siblings
under the same parent directory (e.g. /Projects/noodle_tests and
/Projects/noodle) and the test repo is missing a root tests/pom.yaml or
steps/ dir.
"""
from pathlib import Path

from noodle.agents.web import pom
from noodle.cli import _find_behave_base


def test_global_pom_path_does_not_escape_workspace_missing_root_pom(tmp_path):
    # An unrelated ancestor directory that happens to have its own pom.yaml —
    # stands in for a sibling/parent project, or noodle's own repo checkout.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "pom.yaml").write_text("pages: {}\n")

    workspace = tmp_path / "workspace"
    feature_dir = workspace / "tests" / "web" / "app" / "features"
    feature_dir.mkdir(parents=True)
    (workspace / "noodle.yaml").write_text("tests_dir: tests\n")
    # Deliberately no tests/pom.yaml or pom.yaml inside the workspace itself.

    pom.set_context(str(feature_dir))
    try:
        result = pom._global_pom_path()
    finally:
        pom.set_context(None)

    assert result == Path("tests/pom.yaml")  # fallback, not the ancestor's file
    assert result != tmp_path / "tests" / "pom.yaml"


def test_find_behave_base_does_not_escape_workspace_missing_root_markers(tmp_path):
    (tmp_path / "tests" / "steps").mkdir(parents=True)  # unrelated ancestor marker

    workspace = tmp_path / "workspace"
    feature_dir = workspace / "tests" / "web" / "app" / "features"
    feature_dir.mkdir(parents=True)
    (workspace / "noodle.yaml").write_text("tests_dir: tests\n")
    feature_file = feature_dir / "sample.feature"
    feature_file.write_text("Feature: sample\n")
    # Deliberately no steps/ or environment.py inside the workspace itself.

    result = _find_behave_base(feature_file)

    assert result == Path("tests")  # fallback, not the ancestor's tests/ dir
    assert result != tmp_path / "tests"

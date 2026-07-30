"""NOOD_0204 — `noodle init` must reach existing .gitignores too: the repos
that already have one (most, via repo-init) are exactly the ones where run
output — traces with auth headers, screenshots, session cookie jars — must
never become committable.
"""
from noodle import cli


def test_fresh_repo_gets_full_scaffold(tmp_path):
    assert cli._merge_gitignore(tmp_path) == "created"
    assert "artifacts/" in (tmp_path / ".gitignore").read_text()


def test_existing_file_is_merged_not_replaced(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("node_modules/\n.env\nartifacts/\n")

    assert cli._merge_gitignore(tmp_path) == "updated"
    text = gi.read_text()
    # user lines untouched, at the top, in order
    assert text.startswith("node_modules/\n.env\nartifacts/\n")
    # missing entries appended; already-present ones not duplicated
    assert "**/report/" in text
    assert text.count("artifacts/") == 1


def test_rerun_is_idempotent(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("node_modules/\n")
    cli._merge_gitignore(tmp_path)
    once = gi.read_text()

    assert cli._merge_gitignore(tmp_path) is None
    assert gi.read_text() == once

"""NOOD_0098 — `noodle init` also copies the /noodle agent skill
(.claude/skills/noodle, .copilot/skills/noodle) into every scaffolded
workspace, the same "zero to hero, nothing to configure by hand" treatment
NOOD_0095/0097 already gave MCP client config."""
from pathlib import Path

from typer.testing import CliRunner

from noodle.cli import app

runner = CliRunner()


def test_init_copies_skills(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".claude" / "skills" / "noodle" / "SKILL.md").is_file()
    assert (tmp_path / ".copilot" / "skills" / "noodle" / "SKILL.md").is_file()
    assert "Agent skill" in result.output


def test_init_skill_copy_is_idempotent(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    marker = tmp_path / ".claude" / "skills" / "noodle" / "SKILL.md"
    marker.write_text("local edit")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert marker.read_text() == "local edit"  # kept, not clobbered
    assert "kept" in result.output


def test_init_skill_force_refreshes(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    marker = tmp_path / ".claude" / "skills" / "noodle" / "SKILL.md"
    marker.write_text("local edit")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert marker.read_text() != "local edit"  # refreshed from the engine copy


def test_copy_skills_skips_missing_source(tmp_path, monkeypatch):
    """A wheel install without .claude/skills/ shipped: skip, don't fail init."""
    from noodle import cli
    monkeypatch.setattr(cli, "_SKILL_DIRS",
                         [(Path(".claude") / "skills" / "does-not-exist", "Nowhere")])
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert not (tmp_path / ".claude" / "skills" / "does-not-exist").exists()

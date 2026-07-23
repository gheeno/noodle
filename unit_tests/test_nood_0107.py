"""NOOD_0107 — scaffold templates are agent-proof: PROMPT_TEMPLATE.md is
paste-clean (flush-left, no hard-wrapped sentences, survives chat/code-block
paste), and CLAUDE.md points at AGENTS.md without @-importing it (NOOD_0117:
native AGENTS.md loading made the import a double-injection). No browser, no LLM."""
from typer.testing import CliRunner

from noodle.cli import app

runner = CliRunner()


def _scaffold(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    return tmp_path


def test_prompt_template_is_paste_clean(tmp_path):
    tpl = (_scaffold(tmp_path) / "PROMPT_TEMPLATE.md").read_text()
    for line in tpl.splitlines():
        assert line == line.lstrip(), f"indented line breaks chat paste: {line!r}"
    # one logical item per line — a sentence hard-wrapped mid-way would leave
    # a continuation line without a colon-key, bullet, or blank separator
    assert "\t" not in tpl
    for marker in ("[APP NAME]", "[https://...]", "AGENTS.md"):
        assert marker in tpl, marker


def test_prompt_template_has_no_hard_wrapped_sentences(tmp_path):
    tpl = (_scaffold(tmp_path) / "PROMPT_TEMPLATE.md").read_text()
    # every non-blank line is a complete field or sentence — a hard-wrapped
    # line would end mid-phrase, without a terminator
    for line in tpl.splitlines():
        if line:
            assert line.endswith((".", ":", "]")), f"hard-wrapped: {line!r}"


def test_claude_md_points_at_agents_md_without_importing(tmp_path):
    # NOOD_0117 — the @-import doubled AGENTS.md on every model call once
    # Claude Code started loading AGENTS.md natively; the pointer is now
    # plain text so no client injects the file twice.
    claude = (_scaffold(tmp_path) / "CLAUDE.md").read_text()
    assert "AGENTS.md" in claude
    assert "@AGENTS.md" not in claude, "an @-import double-injects AGENTS.md"

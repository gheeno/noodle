"""NOOD_0112 — agent output logging rules on every agent surface: the
workspace AGENTS.md (read by Claude CLI / Claude Code via the CLAUDE.md
@-import, and by Copilot CLI / VS Code Chat natively), PROMPT_TEMPLATE.md,
the playbook §0 and the repo Copilot digest all require max-2-sentence
progress updates stating current intent, and honour a user's "do not
output the shell command". No browser, no LLM."""
from pathlib import Path

from noodle.cli import _AGENTS_MD, _PROMPT_TEMPLATE

REPO = Path(__file__).resolve().parents[1]


def _flat(text: str) -> str:
    """Collapse hard-wrapped prose so phrase asserts survive line breaks."""
    return " ".join(text.split())


def test_agents_md_requires_intent_progress_updates():
    agents = _flat(_AGENTS_MD)
    assert "max 2 sentences" in agents
    assert "current intent" in agents
    # example lines so agents copy the shape, not invent commentary
    assert "Serving the reports now" in agents
    # the old blanket no-narration rule is replaced, not stacked on top
    assert "Don't narrate" not in agents


def test_agents_md_honours_no_shell_command_request():
    agents = _flat(_AGENTS_MD)
    assert "do not output the shell command" in agents
    assert "echo no command line" in agents


def test_prompt_template_keeps_shell_opt_out_field():
    # NOOD_0125 — the prompt no longer duplicates AGENTS.md's rules (the
    # max-2-sentences rule now lives only there). It keeps the shell opt-out
    # as a fillable field, because it changes the agent's reply behaviour and
    # can't be inferred.
    tpl = _flat(_PROMPT_TEMPLATE)
    assert "Shell commands in replies: [ok | do not output the shell command]" in tpl
    assert "max 2 sentences" not in tpl  # a rule, not a task fact — lives in AGENTS.md


def test_playbook_section_0_matches():
    playbook = _flat((REPO / "docs" / "agent-playbook.md").read_text())
    assert "max 2 sentences" in playbook
    assert "current intent" in playbook
    assert "do not output the shell command" in playbook
    assert 'Don\'t narrate ("Now I will…")' not in playbook


def test_copilot_digest_matches():
    digest = _flat((REPO / ".github" / "copilot-instructions.md").read_text())
    assert "max 2 sentences" in digest
    assert "current intent" in digest
    assert "not to output shell commands" in digest

"""NOOD_0101 — faster, token-lean LLM test generation: temperature=0 default,
keyword-gated vocabulary sections, line-level repair, few-shot step-JSON
examples, the REPL's rule-based fast path, and the LLM install runbook.

No LLM, no network, no browser — every model call is monkeypatched.
"""
from pathlib import Path
from types import SimpleNamespace

from noodle import config
from noodle.llm import client
from noodle.repl import generate, prompts

DOCS = Path(__file__).resolve().parent.parent / "docs"

UNMATCHED_FEATURE = """@web
Feature: Login

  Scenario: Valid login
    Given User is on "https://example.com"
    When User performs an interpretive dance on the login form
    Then User should see "Welcome"
"""


# --- temperature (llm/client.py) ----------------------------------------------

def _fake_litellm(captured: list) -> SimpleNamespace:
    def completion(**kwargs):
        captured.append(kwargs)
        msg = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=None, model="fake/model")
    return SimpleNamespace(completion=completion)


def test_ask_defaults_to_temperature_zero(monkeypatch):
    monkeypatch.delenv("NOODLE_LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("NOODLE_LLM_MAX_CALLS", raising=False)
    client.reset_calls()
    captured: list = []
    monkeypatch.setattr(client, "_litellm", lambda: _fake_litellm(captured))
    assert client.ask("hi") == "ok"
    assert captured[0]["temperature"] == 0.0


def test_temperature_env_override(monkeypatch):
    monkeypatch.setenv("NOODLE_LLM_TEMPERATURE", "0.7")
    client.reset_calls()
    captured: list = []
    monkeypatch.setattr(client, "_litellm", lambda: _fake_litellm(captured))
    client.ask("hi")
    assert captured[0]["temperature"] == 0.7


def test_temperature_empty_string_omits_param(monkeypatch):
    # Some providers/models reject temperature — empty string opts out.
    monkeypatch.setenv("NOODLE_LLM_TEMPERATURE", "")
    client.reset_calls()
    captured: list = []
    monkeypatch.setattr(client, "_litellm", lambda: _fake_litellm(captured))
    client.ask("hi")
    assert "temperature" not in captured[0]


def test_sampling_kwargs_shared_with_vision(monkeypatch):
    # ask_vision routes through the same knob — checked at the helper level
    # so the test doesn't need to fake an image round-trip.
    monkeypatch.delenv("NOODLE_LLM_TEMPERATURE", raising=False)
    assert client._sampling_kwargs() == {"temperature": 0.0}
    monkeypatch.setenv("NOODLE_LLM_TEMPERATURE", " ")
    assert client._sampling_kwargs() == {}


# --- keyword-gated vocabulary (repl/prompts.py) --------------------------------

def test_relevant_vocabulary_core_always_specialised_gated(monkeypatch):
    monkeypatch.delenv("NOODLE_PROMPT_VOCAB", raising=False)
    v = prompts.relevant_vocabulary("valid login shows the dashboard")
    for core in ("Navigation / setup:", "Interaction:", "Waiting:", "Assertions:"):
        assert core in v
    assert "REST API:" not in v
    assert "Scenario Outline" not in v
    assert "payloads/seed_cart.json" not in v


def test_relevant_vocabulary_triggers():
    assert "REST API:" in prompts.relevant_vocabulary(
        "the api returns status 200")
    assert "Scenario Outline" in prompts.relevant_vocabulary(
        "login with multiple users")
    assert "{var:total}" in prompts.relevant_vocabulary(
        "stores the total and compares it")
    assert "fills in the form with:" in prompts.relevant_vocabulary(
        "fill in the checkout form")
    assert "payloads/seed_cart.json" in prompts.relevant_vocabulary(
        "seed the cart with a payload")


def test_relevant_vocabulary_full_escape_hatch(monkeypatch):
    monkeypatch.setenv("NOODLE_PROMPT_VOCAB", "full")
    assert prompts.relevant_vocabulary("valid login") == prompts.STEP_VOCABULARY


def test_step_vocabulary_still_publishes_every_family():
    # The noodle://vocabulary MCP resource and the repair prompts still get
    # the complete grammar — gating only applies to the generation prompt.
    for family in ("Navigation / setup:", "Conditional", "Variables:",
                   "Scenario Outline", "Table-driven", "REST API:",
                   "payloads/seed_cart.json"):
        assert family in prompts.STEP_VOCABULARY


def test_generation_prompt_is_smaller_for_simple_requests(monkeypatch):
    monkeypatch.delenv("NOODLE_PROMPT_VOCAB", raising=False)
    trimmed = prompts.generation_prompt("valid login", "https://example.com")
    full = prompts.GENERATION.format(
        vocabulary=prompts.STEP_VOCABULARY, url="https://example.com",
        description="valid login", negative_rule="")
    assert len(trimmed) < len(full)


def test_repair_steps_prompt_lists_misses_with_full_vocabulary():
    p = prompts.repair_steps_prompt(["When User does a thing"])
    assert "- When User does a thing" in p
    assert "REST API:" in p          # backstop keeps the complete grammar
    assert "one per line" in p


# --- line-level repair (repl/generate.py) --------------------------------------

def test_parse_repair_lines_strips_fence_numbering_and_bullets():
    reply = ('```\n1. When User clicks the login button\n'
             '- Then User should see "Welcome"\n```')
    assert generate._parse_repair_lines(reply, expected=2) == [
        "When User clicks the login button", 'Then User should see "Welcome"']


def test_parse_repair_lines_rejects_wrong_count():
    assert generate._parse_repair_lines("just one line", expected=2) is None
    assert generate._parse_repair_lines("", expected=1) is None


def test_apply_step_repairs_preserves_indent_and_keyword():
    feature = ('@web\nFeature: X\n\n  Scenario: S\n'
               '    Given User is on "https://example.com"\n'
               '    And User performs an interpretive dance\n')
    fixed = generate._apply_step_repairs(
        feature,
        ["And User performs an interpretive dance"],
        ["When User clicks the login button"])
    # keyword And and the 4-space indent survive; untouched lines untouched
    assert "    And User clicks the login button" in fixed.split("\n")
    assert "interpretive dance" not in fixed
    assert '    Given User is on "https://example.com"' in fixed.split("\n")


def test_generate_llm_repair_is_line_level(tmp_path, monkeypatch):
    responses = [UNMATCHED_FEATURE, "When User clicks the login button"]
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, _ = generate.generate_llm("login", "https://example.com",
                                    cfg, str(tmp_path))
    assert len(calls) == 2
    # The repair prompt carries the miss but NOT the rest of the draft —
    # that's the token saving under test.
    assert "interpretive dance" in calls[1]
    assert "Feature: Login" not in calls[1]
    assert "Scenario: Valid login" not in calls[1]
    text = feat.read_text()
    assert "When User clicks the login button" in text
    assert 'Then User should see "Welcome"' in text   # untouched line kept


def test_generate_llm_unusable_repair_reply_keeps_original(tmp_path, monkeypatch, capsys):
    # Two lines back for one miss → unusable alignment → original kept.
    responses = [UNMATCHED_FEATURE, "line one\nline two"]
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, _ = generate.generate_llm("login", "https://example.com",
                                    cfg, str(tmp_path))
    assert "interpretive dance" in feat.read_text()
    assert "[LLM]" in capsys.readouterr().out         # miss still reported


def test_generate_llm_parse_error_falls_back_to_full_rewrite(tmp_path, monkeypatch):
    good = ('@web\nFeature: Login\n\n  Scenario: Valid login\n'
            '    Given User is on "https://example.com"\n'
            '    Then User should see "Welcome"\n')
    responses = ["not gherkin at all", good]
    calls = []

    def fake_ask(prompt, system=None):
        calls.append(prompt)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "ask", fake_ask)
    cfg = config.load(str(tmp_path))
    feat, _ = generate.generate_llm("login", "https://example.com",
                                    cfg, str(tmp_path))
    assert len(calls) == 2
    assert "did not parse as Gherkin" in calls[1]     # full-file repair path
    assert 'Then User should see "Welcome"' in feat.read_text()


# --- few-shot verb examples (resolver/step_resolver.py) -------------------------

def test_step_json_prompt_carries_verb_examples(tmp_path, monkeypatch):
    from noodle.resolver import step_resolver
    step_resolver.set_docs_dir(tmp_path)   # suggestions log → tmp, not repo docs
    seen = []

    def fake_ask(prompt, system=None):
        seen.append(prompt)
        return '{"type": "click", "locator": "login button"}'

    monkeypatch.setattr("noodle.llm.client.ask", fake_ask)
    action = step_resolver._llm_resolve_uncached(
        "User authenticates using the login button")
    assert action["type"] == "click"
    p = seen[0]
    assert '"User authenticates using the login button" -> {"type": "click"' in p
    assert '"User verifies the dashboard is displayed"' in p


# --- REPL fast path stays model-free --------------------------------------------

def test_repl_create_test_rule_based_writes_without_llm(tmp_path, capsys):
    from noodle.repl import repl
    cfg = config.load(str(tmp_path))
    state: dict = {}
    assert repl.dispatch("create test for login at https://example.com",
                         cfg, str(tmp_path), None, state)
    feat = (tmp_path / cfg["tests_dir"] / "web" / "example" /
            "features" / "login.feature")
    assert feat.is_file()
    assert state["last_feature"].endswith("login.feature")
    # placeholders present → the autorun guard must NOT have launched a run
    assert "fill in the <placeholders> first" in capsys.readouterr().out


# --- docs shipped with the change ------------------------------------------------

def test_llm_install_doc_covers_both_oses():
    text = (DOCS / "llm-install.md").read_text()
    assert "macOS" in text and "Windows 11" in text
    assert "winget install Python.Python.3.11" in text   # Windows path
    assert "brew install" in text                        # macOS path
    assert "uv tool install" in text
    assert "playwright install chromium" in text
    assert "noodle --version" in text                    # verification bar
    assert "installation only" in text.lower()


def test_llm_performance_doc_documents_the_knobs():
    text = (DOCS / "llm-performance.md").read_text()
    assert "NOODLE_LLM_TEMPERATURE" in text
    assert "NOODLE_PROMPT_VOCAB" in text
    assert "write_feature" in text                       # the 0-call fast path


# --- agent-facing surfaces (driving-agent follow-up) ----------------------------
# The perf guidance and the install runbook must live where a Claude Code /
# Copilot CLI agent actually loads instructions from — the committed skills,
# CLAUDE.md / copilot-instructions.md, and the AGENTS.md `noodle init`
# scaffolds into workspaces — not only in docs/ it may never open.

REPO = Path(__file__).resolve().parent.parent


def test_noodle_skill_carries_fast_generation_guidance():
    for skill in (REPO / ".claude" / "skills" / "noodle" / "SKILL.md",
                  REPO / ".copilot" / "skills" / "noodle" / "SKILL.md"):
        text = skill.read_text()
        assert "fastest path first" in text.lower(), skill
        assert "use_llm=True" in text, skill             # warned against
        assert "llm-performance" in text, skill


def test_install_noodle_skill_exists_for_both_agents():
    for skill in (REPO / ".claude" / "skills" / "install-noodle" / "SKILL.md",
                  REPO / ".copilot" / "skills" / "install-noodle" / "SKILL.md"):
        text = skill.read_text()
        assert "name:" in text and "install-noodle" in text, skill
        assert "install noodle" in text.lower(), skill   # the trigger phrase
        assert "docs/llm-install.md" in text, skill
        assert "Installation only" in text, skill


def test_repo_agent_entrypoints_route_install_requests():
    for entry in (REPO / "CLAUDE.md",
                  REPO / ".github" / "copilot-instructions.md"):
        assert "llm-install.md" in entry.read_text(), entry


def test_vscode_copilot_prompt_file_gives_slash_install_noodle():
    # /install-noodle in VS Code Copilot Chat resolves via a prompt file;
    # chat.promptFiles keeps it on for VS Code versions where it's opt-in.
    prompt = REPO / ".github" / "prompts" / "install-noodle.prompt.md"
    text = prompt.read_text()
    assert "docs/llm-install.md" in text
    assert "Installation only" in text
    assert '"chat.promptFiles": true' in (REPO / ".vscode" / "settings.json").read_text()


def test_scaffolded_agents_md_ranks_generation_paths():
    from noodle.cli import _AGENTS_MD
    assert "use_llm=True" in _AGENTS_MD
    assert "llm-performance" in _AGENTS_MD
    assert "append_to" in _AGENTS_MD

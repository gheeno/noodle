"""NOOD_0031 — full LLM mode toggle + provider-agnostic api_base.

No live model and no browser: _llm_resolve is monkeypatched to a sentinel, so we
assert *routing* (does full mode skip patterns?) without a network call.
"""
import pytest

from noodle.llm import client
from noodle.resolver import step_resolver

# --- NOODLE_LLM_MODE routing ------------------------------------------------

def _spy_llm(monkeypatch):
    """Replace _llm_resolve with a sentinel-returning spy; returns the call log."""
    calls = []

    def fake(step_text):
        calls.append(step_text)
        return {"type": "click", "locator": "spy"}

    monkeypatch.setattr(step_resolver, "_llm_resolve", fake)
    return calls


def test_auto_mode_matches_pattern_without_calling_llm(monkeypatch):
    monkeypatch.delenv("NOODLE_LLM_MODE", raising=False)
    monkeypatch.setenv("NOODLE_MODEL", "anything")  # set, but must NOT be used
    calls = _spy_llm(monkeypatch)

    action = step_resolver.resolve("User clicks the login button")

    assert action == {"type": "click", "locator": "login"}
    assert calls == []  # pattern matched → LLM never consulted


def test_full_mode_skips_patterns_and_calls_llm(monkeypatch):
    monkeypatch.setenv("NOODLE_LLM_MODE", "full")
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-4-6")
    calls = _spy_llm(monkeypatch)

    # This step WOULD match the click pattern in auto mode — full mode must still
    # route it to the LLM.
    action = step_resolver.resolve("User clicks the login button")

    assert action == {"type": "click", "locator": "spy"}
    assert calls == ["User clicks the login button"]


def test_full_mode_without_model_raises_clear_error(monkeypatch):
    monkeypatch.setenv("NOODLE_LLM_MODE", "full")
    monkeypatch.delenv("NOODLE_MODEL", raising=False)

    with pytest.raises(AssertionError, match="NOODLE_LLM_MODE=full but NOODLE_MODEL is not set"):
        step_resolver.resolve("User clicks the login button")


def test_full_mode_resolves_rest_steps(monkeypatch):
    monkeypatch.setenv("NOODLE_LLM_MODE", "full")
    monkeypatch.setenv("NOODLE_MODEL", "gemini/gemini-1.5-flash")
    calls = _spy_llm(monkeypatch)

    step_resolver.resolve("performs a POST call at '/users' with body '{}'")

    assert calls == ["performs a POST call at '/users' with body '{}'"]


# --- provider-agnostic api_base (the "support ALL LLMs" fix) -------------------

def test_api_base_is_none_when_url_unset(monkeypatch):
    """Cloud providers (Anthropic/Gemini/Groq/OpenAI) must NOT get a hardcoded
    Ollama localhost base — that silently misroutes them. Unset → None."""
    monkeypatch.delenv("NOODLE_LLM_URL", raising=False)
    assert client._api_base() is None


def test_api_base_passes_through_explicit_url(monkeypatch):
    """Ollama / Foundry Local / self-hosted still get their endpoint."""
    monkeypatch.setenv("NOODLE_LLM_URL", "http://localhost:11434")
    assert client._api_base() == "http://localhost:11434"


# --- suggestion log (NOOD_0049) ---------------------------------------------

def test_log_suggestion_writes_and_dedups(tmp_path, monkeypatch):
    """Every LLM resolution (auto-fallback or full mode) is appended to the
    workspace's steps_dictionary_suggestions.md for human review/promotion —
    the same step_text logged twice must not duplicate the entry."""
    step_resolver.set_docs_dir(tmp_path)
    try:
        monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-4-6")
        action = {"type": "click", "locator": "x"}
        step_resolver._log_suggestion("User does a thing", action)
        path = tmp_path / "steps_dictionary_suggestions.md"
        assert path.exists()
        assert "User does a thing" in path.read_text()

        step_resolver._log_suggestion("User does a thing", action)
        assert path.read_text().count("- **Step:**") == 1  # no duplicate entry
    finally:
        step_resolver.set_docs_dir(None)


def test_log_suggestion_counts_hits(tmp_path, monkeypatch):
    """NOOD_0152 — dedup must not erase recurrence: the promotion rule is
    'promote a recurring one', so a repeat bumps Hits rather than vanishing.
    A distinct step still gets its own entry, each counted independently."""
    step_resolver.set_docs_dir(tmp_path)
    try:
        monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-4-6")
        path = tmp_path / "steps_dictionary_suggestions.md"
        hot = {"type": "click", "locator": "x"}
        for _ in range(4):
            step_resolver._log_suggestion("User does a thing", hot)
        assert "- **Hits:** 4" in path.read_text()

        step_resolver._log_suggestion("User does another thing", {"type": "search", "query": "q"})
        text = path.read_text()
        assert text.count("- **Step:**") == 2
        assert "- **Hits:** 4" in text and "- **Hits:** 1" in text

        step_resolver._log_suggestion("User does another thing", {"type": "search", "query": "q"})
        text = path.read_text()
        assert "- **Hits:** 4" in text and "- **Hits:** 2" in text
    finally:
        step_resolver.set_docs_dir(None)

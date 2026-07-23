"""NOOD_0026 — the step-search-engine: deterministic ranking over
step_resolver.example_index(), with a local LLM as a tie-breaker only when
ranking is ambiguous. No network/Ollama calls — noodle.llm.client.ask is
monkeypatched, same convention as unit_tests/test_nood_0007.py.
"""
from noodle.llm import client
from noodle.resolver import step_search_engine as sse


def test_tokenize_strips_stopwords_and_quotes():
    tokens = sse.tokenize('When User clicks the "Login" button')
    assert "the" not in tokens
    assert "user" not in tokens
    assert "login" in tokens
    assert "click" in tokens or "clicks" in tokens  # stemmed


def test_rank_returns_scored_candidates_sorted_desc():
    results = sse.rank("click the login button")
    assert results == sorted(results, key=lambda s: s.score, reverse=True)
    assert results[0].type == "click"


def test_search_step_finds_existing_match_for_store_and_reuse_phrase():
    """The feature request's own worked example — the dictionary already
    has an answer for this, deterministically, no LLM needed."""
    result = sse.search_step("store a return param and use it to another step", use_llm=False)
    assert result.match is not None
    assert result.match.type in {"store_text", "store_attribute"}


def test_search_step_no_match_for_nonsense_query():
    result = sse.search_step("frobnicates the widget xyzzy", use_llm=False)
    assert result.match is None
    assert result.confidence == "none"


def test_search_step_low_confidence_without_llm_still_reports_best_effort(monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    result = sse.search_step("keep this response value around for a subsequent call", use_llm=True)
    assert result.match is not None
    assert result.confidence == "low"
    assert result.llm_used is False


def test_search_step_high_confidence_skips_llm_entirely(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "ask", lambda *a, **k: calls.append(1) or "1")
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    result = sse.search_step("click the login button", use_llm=True)
    assert result.confidence == "high"
    assert result.llm_used is False
    assert calls == []  # never even asked


def test_search_step_uses_llm_classify_when_ambiguous(monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "1")
    result = sse.search_step("the count should equal 5", use_llm=True)
    assert result.confidence == "high"
    assert result.llm_used is True
    assert result.match is not None


def test_search_step_llm_says_none_falls_through_to_no_match(monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "NONE")
    result = sse.search_step("the count should equal 5", use_llm=True)
    assert result.match is None
    assert result.confidence == "none"
    assert result.llm_used is True


def test_search_step_llm_failure_falls_back_to_deterministic(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ollama server")
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", boom)
    result = sse.search_step("the count should equal 5", use_llm=True)
    assert result.match is not None
    assert result.confidence == "low"
    assert result.llm_used is False


def test_search_step_degrades_when_docs_missing(monkeypatch):
    monkeypatch.setattr(sse, "example_index", lambda: [])
    result = sse.search_step("anything at all", use_llm=False)
    assert result.match is None
    assert result.confidence == "none"

"""NOOD_0026 — the step-suggestion-engine: drafting + accepting a new step
when step_search_engine finds no good match. No network/Ollama calls —
noodle.llm.client.ask is monkeypatched throughout.
"""
import re

import yaml

from noodle.llm import client
from noodle.repl import step_suggestion_engine as sse
from noodle.resolver import patterns as _patterns
from noodle.resolver.step_search_engine import search_step


def test_draft_suggestion_reuses_nearest_neighbor_action_type():
    query = "keep this response value around for a subsequent call"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=False)
    assert suggestion.fits_existing_type is True
    assert suggestion.action_type is not None
    assert suggestion.based_on is not None


def test_draft_suggestion_regex_matches_its_own_query():
    """Load-bearing correctness property: whatever we draft must actually
    match the normalized body it was drafted from."""
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    sse.draft_suggestion(query, result, use_llm=False)
    # Force a fits_existing_type=True suggestion deterministically by
    # reusing draft internals directly, since without an LLM this query
    # correctly reports fits_existing_type=False (see the "no type" test).
    body = "frobnicates the sprocket widget"
    regex = sse._build_regex(body)
    assert re.match(regex, body, re.IGNORECASE) is not None
    assert re.match(regex, "frobnicate the sprocket widget", re.IGNORECASE) is not None  # "I" form


def test_draft_suggestion_quoted_span_becomes_group_param():
    body = "frobnicates the 'sprocket' widget and saves it as OUT"
    params = sse._draft_params(body, "store_text")
    group_params = [p for p in params if p.get("source") == "group"]
    assert group_params == [{"name": "locator", "source": "group", "group": 1, "quoted": True}]


def test_draft_suggestion_flags_when_no_deterministic_type_available(monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=True)
    assert suggestion.fits_existing_type is False
    assert suggestion.action_type is None
    assert "not auto-generated" in suggestion.rationale


def test_draft_suggestion_llm_classifies_into_valid_type_when_no_neighbor(monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=True)
    assert suggestion.fits_existing_type is True
    assert suggestion.action_type == "click"


def test_draft_suggestion_rejects_llm_type_not_in_valid_types(monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "made_up_type")
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=True)
    assert suggestion.fits_existing_type is False


def test_conjugate_leading_verb_uses_known_map_and_naive_fallback():
    assert sse._conjugate_leading_verb("click the button") == "clicks the button"
    assert sse._conjugate_leading_verb("frobnicate the widget") == "frobnicates the widget"


def test_accept_suggestion_writes_yaml_and_dictionary_and_clears_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=True)
    assert suggestion.fits_existing_type is True

    written = sse.accept_suggestion(suggestion, docs_dir=tmp_path)
    assert written["patterns_file"] == tmp_path / "agent_patterns.yaml"
    assert written["dictionary_file"] == tmp_path / "steps_dictionary.md"

    entries = yaml.safe_load(written["patterns_file"].read_text())
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action_type"] == "click"
    assert entry["source_query"] == query
    assert entry["status"] == "staging"
    assert "phrase" in entry and "params" in entry and "added_on" in entry

    dictionary_text = written["dictionary_file"].read_text()
    assert "frobnicates the sprocket widget" in dictionary_text
    assert sse._ANCHOR in dictionary_text

    # Resolves end-to-end through patterns.match() after cache invalidation,
    # pointed at this tmp_path instead of the real docs/ dir. conftest.py's
    # autouse fixture resets this override after the test.
    _patterns.set_agent_patterns_dir(tmp_path)
    from noodle.resolver.patterns import match, normalize_subject
    resolved = match(normalize_subject("User frobnicates the sprocket widget"))
    assert resolved == ("click", {"locator": "<TODO>"})


def test_accept_suggestion_appends_to_existing_staged_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")

    for query in ("frobnicate the sprocket widget", "wibble the gizmo panel"):
        result = search_step(query, use_llm=False)
        suggestion = sse.draft_suggestion(query, result, use_llm=True)
        sse.accept_suggestion(suggestion, docs_dir=tmp_path)

    entries = yaml.safe_load((tmp_path / "agent_patterns.yaml").read_text())
    assert len(entries) == 2


def test_accept_suggestion_rejects_a_suggestion_that_does_not_fit(monkeypatch, tmp_path):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    query = "frobnicate the sprocket widget"
    result = search_step(query, use_llm=False)
    suggestion = sse.draft_suggestion(query, result, use_llm=False)
    assert suggestion.fits_existing_type is False
    try:
        sse.accept_suggestion(suggestion, docs_dir=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("expected accept_suggestion to reject a non-fitting suggestion")

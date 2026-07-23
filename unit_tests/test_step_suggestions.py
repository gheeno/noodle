"""NOOD_0009 — 'Did you mean' suggestions on unresolved steps. Uses stdlib
difflib against the live docs/steps_dictionary.md corpus, so no dependency
and no drift between the suggestion list and the actual dictionary."""
from noodle.resolver import step_resolver
from noodle.resolver.step_resolver import _example_corpus, _suggest, resolve


def test_corpus_loads_from_docs():
    corpus = _example_corpus()
    assert "User clicks the login button" in corpus


def test_suggest_near_miss():
    hint = _suggest("User clicks the log in button")
    assert "log" in hint.lower() and "button" in hint.lower()


def test_suggest_empty_for_nonsense():
    hint = _suggest("frobnicates the widget xyzzy")
    assert hint == ""


def test_suggest_empty_when_corpus_missing(monkeypatch):
    monkeypatch.setattr(step_resolver, "_example_corpus", lambda: [])
    assert _suggest("anything") == ""


def test_resolve_error_includes_suggestion():
    try:
        resolve("User frobnicates the login widget")
    except AssertionError as e:
        msg = str(e)
        assert "No pattern matched" in msg
        assert "Did you mean" in msg
        assert "login" in msg.lower()
    else:
        raise AssertionError("expected resolve() to fail for an unmatched step")

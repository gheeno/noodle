"""NOOD_0026 — noodle repl's `find a step for ...` / `step-search ...`
dispatch branch: reports a search result, and only accepts a drafted
suggestion on an explicit 'y' at the interactive prompt. No browser, no
LLM, no network — step_search_engine/step_suggestion_engine are
monkeypatched at their own module level (dispatch() imports them lazily
inside _handle_step_search, so patching the module attribute is enough).
"""
from noodle import config
from noodle.repl import repl
from noodle.repl import step_suggestion_engine as sse
from noodle.resolver import step_search_engine


class _FakeMatch:
    step = 'When User clicks the "Login" button'


class _FakeResult:
    def __init__(self, match=None):
        self.match = match
        self.confidence = "high" if match else "none"
        self.llm_used = False


class _FakeSuggestion:
    fits_existing_type = True
    keyword = "When"
    phrase = "frobnicates the sprocket widget"
    action_type = "click"
    rationale = "test rationale"


def test_dispatch_step_search_reports_match(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(step_search_engine, "search_step",
                         lambda query, use_llm=True: _FakeResult(match=_FakeMatch()))
    cfg = config.load(str(tmp_path))

    keep_going = repl.dispatch("find a step for clicking the login button",
                                cfg, str(tmp_path), llm=None)

    assert keep_going is True
    out = capsys.readouterr().out
    assert "Best match" in out
    assert "Login" in out


def test_dispatch_step_search_via_step_search_alias(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(step_search_engine, "search_step",
                         lambda query, use_llm=True: _FakeResult(match=_FakeMatch()))
    cfg = config.load(str(tmp_path))

    repl.dispatch('step-search "clicking the login button"', cfg, str(tmp_path), llm=None)

    assert "Best match" in capsys.readouterr().out


def test_dispatch_step_search_accepts_on_yes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(step_search_engine, "search_step",
                         lambda query, use_llm=True: _FakeResult(match=None))
    monkeypatch.setattr(sse, "draft_suggestion",
                         lambda query, result, use_llm=True: _FakeSuggestion())
    accepted = []
    monkeypatch.setattr(sse, "accept_suggestion",
                         lambda suggestion: accepted.append(suggestion) or
                         {"patterns_file": tmp_path / "agent_patterns.yaml",
                          "dictionary_file": tmp_path / "steps_dictionary.md"})
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    cfg = config.load(str(tmp_path))
    repl.dispatch("find a step for frobnicating a sprocket", cfg, str(tmp_path), llm=None)

    assert len(accepted) == 1
    out = capsys.readouterr().out
    assert "Suggested new step" in out
    assert "Wrote" in out


def test_dispatch_step_search_declines_on_no(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(step_search_engine, "search_step",
                         lambda query, use_llm=True: _FakeResult(match=None))
    monkeypatch.setattr(sse, "draft_suggestion",
                         lambda query, result, use_llm=True: _FakeSuggestion())
    accepted = []
    monkeypatch.setattr(sse, "accept_suggestion", lambda suggestion: accepted.append(suggestion))
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    cfg = config.load(str(tmp_path))
    repl.dispatch("find a step for frobnicating a sprocket", cfg, str(tmp_path), llm=None)

    assert accepted == []
    assert "Not saved" in capsys.readouterr().out


def test_dispatch_step_search_no_fitting_type_never_prompts(monkeypatch, tmp_path, capsys):
    class _NoFitSuggestion:
        fits_existing_type = False
        rationale = "needs new runtime logic"

    monkeypatch.setattr(step_search_engine, "search_step",
                         lambda query, use_llm=True: _FakeResult(match=None))
    monkeypatch.setattr(sse, "draft_suggestion",
                         lambda query, result, use_llm=True: _NoFitSuggestion())

    def _unexpected_input(*_a, **_k):
        raise AssertionError("must not prompt when nothing fits an existing type")
    monkeypatch.setattr("builtins.input", _unexpected_input)

    cfg = config.load(str(tmp_path))
    repl.dispatch("find a step for frobnicating a sprocket", cfg, str(tmp_path), llm=None)

    assert "needs new runtime logic" in capsys.readouterr().out


# --- NOOD_0058 — core.search_step payload: found=high-confidence only, ---------
# low-confidence guesses stay visible in step/candidates.

def test_core_search_step_high_confidence_found(monkeypatch, tmp_path):
    from noodle.repl import core
    hi = step_search_engine.ScoredStep(
        section="Clicks", step="When User clicks the login button",
        type="click", score=0.91)
    lo = step_search_engine.ScoredStep(
        section="Storage", step="When User clears the local storage",
        type="storage", score=0.31)
    monkeypatch.setattr(
        step_search_engine, "search_step",
        lambda query, use_llm=True: step_search_engine.StepSearchResult(
            query=query, match=hi, shortlist=[hi, lo],
            confidence="high", reason="clear best match"))
    r = core.search_step("click the login button", workspace=str(tmp_path))
    assert r["found"] is True
    assert r["step"] == hi.step and r["confidence"] == "high"
    assert [c["step"] for c in r["candidates"]] == [hi.step, lo.step]
    assert r["candidates"][0]["score"] == 0.91


def test_core_search_step_low_confidence_not_found(monkeypatch, tmp_path):
    from noodle.repl import core
    lo = step_search_engine.ScoredStep(
        section="Storage", step="When User clears the local storage",
        type="storage", score=0.31)
    monkeypatch.setattr(
        step_search_engine, "search_step",
        lambda query, use_llm=True: step_search_engine.StepSearchResult(
            query=query, match=lo, shortlist=[lo],
            confidence="low", reason="ranking was ambiguous"))
    r = core.search_step("clear the cart", workspace=str(tmp_path))
    assert r["found"] is False               # weak guess must not read as a match
    assert r["step"] == lo.step and r["confidence"] == "low"
    assert r["reason"] and r["candidates"][0]["step"] == lo.step

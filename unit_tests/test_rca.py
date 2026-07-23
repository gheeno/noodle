"""NOOD_0018-7 — agentic RCA failure reviewer.

Covers the pure parse/enable logic, the best-effort review() wrapper (LLM
mocked), and the after_step wiring that attaches the rca_category label. No
browser, no real model.
"""
from unittest.mock import MagicMock

from noodle import rca

# --- enabled() gating -------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOODLE_RCA", raising=False)
    monkeypatch.setenv("NOODLE_MODEL", "gpt-4o")
    assert rca.enabled() is False


def test_needs_both_flag_and_model(monkeypatch):
    monkeypatch.setenv("NOODLE_RCA", "true")
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    assert rca.enabled() is False
    monkeypatch.setenv("NOODLE_MODEL", "gpt-4o")
    assert rca.enabled() is True


# --- parse() ----------------------------------------------------------------

def test_parse_valid_verdict_adds_label():
    raw = '{"category": "B", "confidence": "high", "reason": "label changed", "suggested_fix": "update pom.yaml"}'
    v = rca.parse(raw)
    assert v["category"] == "B"
    assert v["label"] == "locator-rot"


def test_parse_strips_fence_and_prose():
    raw = 'Here you go:\n```json\n{"category": "C", "confidence": "low", "reason": "timeout", "suggested_fix": "retry"}\n```'
    v = rca.parse(raw)
    assert v["label"] == "environment-flap"


def test_parse_rejects_unknown_category():
    assert rca.parse('{"category": "Z", "reason": "x"}') is None


def test_parse_rejects_non_json():
    assert rca.parse("no idea what happened") is None


def test_parse_fills_missing_optional_fields():
    v = rca.parse('{"category": "A"}')
    assert v["label"] == "app-regression"
    assert v["confidence"] == "unknown"
    assert v["reason"] == ""


# --- review() ---------------------------------------------------------------

def test_review_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("NOODLE_RCA", raising=False)
    assert rca.review("a step", "boom", "/nonexistent.png") is None


def test_review_returns_verdict(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_RCA", "true")
    monkeypatch.setenv("NOODLE_MODEL", "gpt-4o")
    shot = tmp_path / "fail.png"
    shot.write_bytes(b"\x89PNG\r\n")  # bytes are only base64'd, not decoded

    import noodle.llm.client as client
    monkeypatch.setattr(
        client, "ask_vision",
        lambda prompt, image_b64, cap_var="NOODLE_LLM_MAX_CALLS":
            '{"category": "D", "confidence": "high", "reason": "no seed", "suggested_fix": "seed it"}',
    )
    v = rca.review("Given a seeded cart", "not found", str(shot))
    assert v["label"] == "test-data"


def test_review_swallows_llm_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_RCA", "true")
    monkeypatch.setenv("NOODLE_MODEL", "gpt-4o")
    shot = tmp_path / "fail.png"
    shot.write_bytes(b"x")

    import noodle.llm.client as client
    def _boom(prompt, image_b64, cap_var="NOODLE_LLM_MAX_CALLS"):
        raise RuntimeError("model down")
    monkeypatch.setattr(client, "ask_vision", _boom)
    assert rca.review("a step", "err", str(shot)) is None  # never raises


# --- after_step wiring ------------------------------------------------------

def test_after_step_attaches_rca_label(monkeypatch):
    from noodle import hooks
    from noodle.reporting import writer

    # Real ScenarioResult so _allure_result accepts it (it isinstance-checks).
    scenario = MagicMock()
    scenario.name = "S"
    scenario.feature.name = "F"
    scenario.tags = []
    ar = writer.ScenarioResult(scenario)

    context = MagicMock()
    context._allure_result = ar
    monkeypatch.setattr(hooks, "_REPORTING", True)
    monkeypatch.setattr(
        "noodle.rca.review",
        lambda step_name, error, path: {"category": "B", "label": "locator-rot"},
    )

    step = MagicMock()
    step.status = "failed"
    step.name = "When I click Login"
    step.error_message = "not found"

    hooks.after_step(context, step)

    labels = [lab for lab in ar.result["labels"] if lab["name"] == "rca_category"]
    assert labels == [{"name": "rca_category", "value": "locator-rot"}]

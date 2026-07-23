"""NOOD_0090 — errored steps must not report green, and navigation gets the
same NOODLE_FIND_TIMEOUT smart-wait budget as element finds."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from noodle import hooks
from noodle.agents.web import actions
from noodle.reporting import writer

# --- Bug 1: after_step must treat behave's Status.error as a failure ---------

class _ErrorStatus:
    """Mirrors behave's Status.error: has_failed() is True but == 'failed' is
    False — the exact combination the old `status == "failed"` check missed."""

    def has_failed(self):
        return True

    def __eq__(self, other):
        return other == "error"

    def __hash__(self):
        return hash("error")


def _run_after_step(monkeypatch, tmp_path, status):
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(hooks, "_REPORTING", True)
    scenario = MagicMock()
    scenario.name = "S"
    scenario.feature.name = "F"
    scenario.tags = []
    context = MagicMock()
    context._allure_result = writer.ScenarioResult(scenario)
    context.page = None  # @api-style scenario: no screenshot path exercised
    step = MagicMock()
    step.status = status
    step.name = "opens the login page"
    step.error_message = "Page.goto: Timeout 10000ms exceeded"
    step.exception = TimeoutError("Timeout 10000ms exceeded")
    hooks.after_step(context, step)
    return context


def test_errored_step_is_recorded_as_failed(monkeypatch, tmp_path):
    context = _run_after_step(monkeypatch, tmp_path, _ErrorStatus())
    assert context._scenario_failed is True
    assert context._allure_result.result["steps"][-1]["status"] == "failed"


def test_plain_string_status_still_works(monkeypatch, tmp_path):
    # unit-test doubles pass bare strings — the getattr fallback keeps them working
    context = _run_after_step(monkeypatch, tmp_path, "failed")
    assert context._allure_result.result["steps"][-1]["status"] == "failed"
    context = _run_after_step(monkeypatch, tmp_path, "passed")
    assert context._allure_result.result["steps"][-1]["status"] == "passed"


# --- Bug 2 (revised NOOD_0092): navigate() = ONE goto, full budget, no retry -
# Retrying goto() is a page refresh: it restarts a slow-but-progressing load
# and makes an overloaded server slower. One attempt gets the whole
# NOODLE_FIND_TIMEOUT budget instead.

def test_navigate_calls_goto_once_with_full_budget(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "45000")
    calls = []

    def goto(url, **kw):
        calls.append(kw)

    actions.navigate(SimpleNamespace(goto=goto), "http://slow.local/")
    assert len(calls) == 1
    assert calls[0]["timeout"] == 45000
    assert calls[0]["wait_until"] == "domcontentloaded"


def test_navigate_never_retries_on_timeout(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "45000")
    calls = []

    def goto(url, **kw):
        calls.append(url)
        raise PlaywrightTimeoutError("Timeout 45000ms exceeded")

    with pytest.raises(PlaywrightTimeoutError, match="NOODLE_FIND_TIMEOUT"):
        actions.navigate(SimpleNamespace(goto=goto), "http://slow.local/")
    assert len(calls) == 1


def test_not_found_message_names_the_configured_budget(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "45000")
    msg = actions._not_found("Could not find element to click: 'Login'")
    assert "NOODLE_FIND_TIMEOUT=45000ms" in msg
    assert "45s" in msg

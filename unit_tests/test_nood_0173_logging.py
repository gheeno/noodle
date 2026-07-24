"""NOOD_0173 — phase 5: run/scenario/step lifecycle telemetry.

These events are machine telemetry (json mode only), so the human text console
stays byte-for-byte unchanged — that gating is the core thing to prove here.
The run.end payload (counts + exit code + model/engine provenance) is unit
tested; scenario.*/step.fail are verified end-to-end by a live run.
"""
import json
import os

import pytest

from noodle import log


def _reconfigure_from_env():
    log.logger.handlers.clear()
    log.logger.filters.clear()
    log.logger._noodle_configured = False
    log._configure()


def _use_json():
    os.environ["NOODLE_LOG_FORMAT"] = "json"
    _reconfigure_from_env()


@pytest.fixture(autouse=True)
def _isolate():
    log._secret_values.clear()
    log._context.set({})
    yield
    log._secret_values.clear()
    log._context.set({})
    os.environ.pop("NOODLE_LOG_FORMAT", None)
    _reconfigure_from_env()


def _events(err, name):
    return [json.loads(ln) for ln in err.strip().splitlines() if f'"{name}"' in ln]


def test_telemetry_is_silent_in_text_mode(capsys):
    # default text mode: a lifecycle event must NOT touch the human console.
    log.telemetry("run.start", "🏁 run.start", target="login.feature")
    log.telemetry("scenario.end", "⏹️ scenario.end", status="passed")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


def test_telemetry_emits_in_json_mode(capsys):
    _use_json()
    log.bind(run_id="run-abc", feature="login.feature")
    log.telemetry("scenario.start", "▶️ start", tags=["@smoke", "@web"])
    e = _events(capsys.readouterr().err, "scenario.start")[-1]
    assert e["event"] == "scenario.start"
    assert e["attributes"]["tags"] == ["@smoke", "@web"]
    assert e["run_id"] == "run-abc" and e["feature"] == "login.feature"  # correlated


def test_run_end_carries_counts_exit_code_and_provenance(monkeypatch, capsys, tmp_path):
    from noodle import cli
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setenv("NOODLE_LLM_MODE", "auto")
    _use_json()
    log.bind(run_id="run-xyz")
    data = {"passed": 3, "failed": 1, "verified": True}
    cli._log_run_end(str(tmp_path), rc=1, t0=0.0, data=data)
    e = _events(capsys.readouterr().err, "run.end")[-1]
    a = e["attributes"]
    assert a["passed"] == 3 and a["failed"] == 1 and a["verified"] is True
    assert a["exit_code"] == 1
    assert a["model"] == "anthropic/claude-sonnet-5" and a["llm_mode"] == "auto"
    assert "engine_version" in a and "git_sha" in a and "duration_ms" in a
    assert e["run_id"] == "run-xyz"


def test_run_end_is_silent_in_text_mode(capsys, tmp_path):
    from noodle import cli
    cli._log_run_end(str(tmp_path), rc=0, t0=0.0, data={"passed": 1, "failed": 0})
    assert capsys.readouterr().err == ""   # text mode: no run.end noise on the console

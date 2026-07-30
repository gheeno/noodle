"""NOOD_0202 — the CI build log, and the token guard that keeps it out of an
LLM's context.

The defect: `--quiet` is automatic off a TTY, so every CI job got it — and quiet
folded the behave child's *stderr* into run.log alongside its stdout. An Azure
job therefore watched a 20-minute suite in total silence, and every
scenario/step/llm event documented in docs/logging.md never reached the log
stream at all (a `NOODLE_LOG_FORMAT=json` run shipped exactly two records:
run.start and run.end, both from the parent CLI).

The fix is a sink decision, not an on/off one: the file log is unconditional,
and the console stream turns on when a build console is watching. What must NOT
regress is the other consumer — an agent's captured subprocess pipes stderr
straight into the payload a model reads (repl.core.run_test's `output` tail), and
an AI-SDLC orchestrator runs *inside* the pipeline where CI=true. So the agent
doors set the gate themselves rather than trusting CI to be unset. That guard is
what most of this file pins.

No browser, no network — behave is stubbed, the logger is driven directly.
"""
import logging
import os
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from noodle import cli, log

runner = CliRunner()

# The env that decides the sink. Cleared before every test so nothing here
# depends on whether the suite itself is running on a CI runner.
_GATE = ("NOODLE_LOG_PROGRESS", "CI", "TF_BUILD")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for k in _GATE:
        monkeypatch.delenv(k, raising=False)
    log._context.set({})
    yield
    # Rebuild the logger from scratch: attach_progress_handler adds a handler and
    # route_console_to_build_log re-points/re-formats the console one, and both
    # are process-global. Leaving either in place broke NOOD_0171's
    # "the human console is unchanged" test when it ran after this file.
    log._progress_handler = None
    log.logger.handlers.clear()
    log.logger.filters.clear()
    log.logger._noodle_configured = False
    log._configure()
    log._context.set({})


# --------------------------------------------------------------------------
# progress_mode() — who gets the console stream
# --------------------------------------------------------------------------

def test_ci_turns_the_build_log_on_with_no_configuration(monkeypatch):
    # The user's requirement: on by default in CI, no pipeline yaml change.
    assert log.progress_mode() is False
    monkeypatch.setenv("CI", "true")
    assert log.progress_mode() is True


def test_azure_devops_is_recognised(monkeypatch):
    # ADO sets TF_BUILD, not CI, on some agent/task combinations.
    monkeypatch.setenv("TF_BUILD", "True")
    assert log.progress_mode() is True


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("1", True), ("true", True),
])
def test_explicit_switch_beats_ci_detection(monkeypatch, value, expected):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("NOODLE_LOG_PROGRESS", value)
    assert log.progress_mode() is expected


# --------------------------------------------------------------------------
# The token guard — an LLM session must never be charged for this stream
# --------------------------------------------------------------------------

def test_agent_door_forces_the_stream_off_even_inside_ci(monkeypatch):
    """repl.core._engine captures stdout+stderr into the payload a model reads.
    Inside a pipeline CI=true would otherwise switch the child into progress
    mode and bill every scenario line to the context window."""
    from noodle.repl import core
    monkeypatch.setenv("CI", "true")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    core._engine("run", "tests", workspace=".")
    assert seen["env"]["NOODLE_LOG_PROGRESS"] == "0"
    assert seen["capture_output"] is True   # the reason the guard is needed


def test_json_flag_forces_the_stream_off_even_inside_ci(monkeypatch, tmp_path):
    """`--json` is a machine-facing caller by construction (agent tool call, a
    wrapper parsing stdout) — the one that pays per byte."""
    monkeypatch.setenv("CI", "true")
    _stub_behave(monkeypatch, tmp_path, {})
    runner.invoke(cli.app, ["run", "tests", "-w", str(tmp_path / "ws"), "--json"])
    assert os.environ["NOODLE_LOG_PROGRESS"] == "0"


# --------------------------------------------------------------------------
# The child's stderr: build console vs run.log
# --------------------------------------------------------------------------

def _stub_behave(monkeypatch, tmp_path, record):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "_resolve_run_target",
                        lambda w, p: (str(ws), p or "tests"))
    monkeypatch.setattr(cli.config, "load",
                        lambda w: {"tests_dir": "tests", "browser": "chromium",
                                   "headless": True})
    monkeypatch.setattr(cli, "_app_report_dir", lambda c, p: None)
    monkeypatch.setattr(cli._paths, "record_last_run_root", lambda c: None)
    monkeypatch.setattr(cli, "_write_last_run", lambda *a, **k: None)
    monkeypatch.setenv("NOODLE_ALLOW_EMPTY", "1")
    monkeypatch.setenv("NOODLE_SERVE_REPORTS", "0")

    def fake_run(args, **kw):
        record.update(kw)
        return MagicMock(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return ws


def test_ci_lets_the_childs_stderr_reach_the_build_console(monkeypatch, tmp_path):
    record = {}
    ws = _stub_behave(monkeypatch, tmp_path, record)
    monkeypatch.setenv("CI", "true")
    runner.invoke(cli.app, ["run", "tests", "-w", str(ws), "--quiet"])
    assert record.get("stdout") is not None            # firehose still to run.log
    assert record.get("stderr") is None                # ...but progress flows out


def test_without_a_console_stderr_still_folds_into_run_log(monkeypatch, tmp_path):
    record = {}
    ws = _stub_behave(monkeypatch, tmp_path, record)
    runner.invoke(cli.app, ["run", "tests", "-w", str(ws), "--quiet"])
    assert record.get("stderr") == cli.subprocess.STDOUT


# --------------------------------------------------------------------------
# What lands on the build console
# --------------------------------------------------------------------------

def test_lifecycle_events_reach_a_text_console_in_progress_mode(monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.telemetry("scenario.end", "  PASSED: Valid login (1.2s)", status="passed")
    assert "[INFO]   PASSED: Valid login (1.2s)" in capsys.readouterr().err


def test_build_log_is_level_prefixed_and_emoji_free(monkeypatch, capsys):
    """A build log is a log file, not a terminal — it has to grep and diff. The
    engine's console breadcrumbs are stripped at this sink only; a human at a
    TTY still gets them (NOOD_0171's unchanged-console contract)."""
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.logger.warning("\n  🩹 healed 'Sign in' via partial text")
    line = capsys.readouterr().err.strip()
    assert line == "[WARNING]   healed 'Sign in' via partial text"


def test_a_failed_scenario_logs_at_error(monkeypatch, capsys):
    # `grep '^\\[ERROR\\]'` over a CI build log is the first thing anyone does.
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.telemetry("scenario.end", "  FAILED: Valid login (1.2s)",
                  level=logging.ERROR, status="failed")
    assert capsys.readouterr().err.startswith("[ERROR]")


def test_the_action_firehose_stays_off_the_build_console(monkeypatch, capsys):
    """81 logger.info sites in actions.py alone. A build log gets the curated
    stream — those lines belong in run.log, which is why the handler filters on
    `event` rather than just re-pointing the console."""
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.logger.info("✅ clicked 'Submit'")          # a plain action breadcrumb
    log.telemetry("scenario.start", "  Scenario: Valid login")
    err = capsys.readouterr().err
    assert "clicked 'Submit'" not in err
    assert "Valid login" in err


def test_warnings_always_reach_the_build_console(monkeypatch, capsys):
    # A healed locator or a missing extra is exactly what a CI operator needs.
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.logger.warning("⚠️ healed 'Submit' via partial text")
    assert "healed 'Submit'" in capsys.readouterr().err


def test_step_detail_is_the_debug_tier(monkeypatch, capsys):
    """`--log-level DEBUG` is the `mvn -X` of a run — the tier that shows WHERE
    a wedged suite stopped. At INFO a 1000-scenario suite would emit ~10k lines."""
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    try:
        log.telemetry("step.end", "    Step: When clicks 'Save'", level=logging.DEBUG)
        assert capsys.readouterr().err == ""
        log.set_level("DEBUG")
        log.telemetry("step.end", "    Step: When clicks 'Save'", level=logging.DEBUG)
        assert "clicks 'Save'" in capsys.readouterr().err
    finally:
        log.set_level("INFO")


def test_retry_marker_tells_a_rerun_from_a_duplicate(monkeypatch):
    """behave's autoretry re-enters before_scenario, so a retried scenario printed
    twice on the build log with nothing saying why. The denominator is what the
    run was ALLOWED, so a reader can tell 'last chance' from 'more to come'."""
    from noodle import hooks
    monkeypatch.setenv("NOODLE_RETRIES", "2")
    assert hooks._retry_tag(1) == ""            # first attempt: no noise
    assert "retry 1/2" in hooks._retry_tag(2)
    assert "retry 2/2" in hooks._retry_tag(3)


# --------------------------------------------------------------------------
# Parallel runs — 8-10 workers, one stderr
# --------------------------------------------------------------------------

def test_parallel_lanes_are_tagged_so_one_worker_can_be_replayed(monkeypatch, capsys):
    """behavex workers all stream to the SAME stderr. Untagged, a 10-worker run
    is ten features' scenarios shuffled together — two identical 'Step failed'
    lines in a row belonged to different features. `grep '\\[w3\\]'` has to
    replay one lane in order."""
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.bind(lane=3, feature="checkout.feature")
    log.telemetry("scenario.start", "  Scenario: Pay by card")
    log.logger.error("boom\nsecond line")
    out = capsys.readouterr().err.strip().splitlines()
    assert out[0] == "[INFO] [w3]   Scenario: Pay by card"
    # multi-line records stay attributable line by line, or a traceback from
    # lane 3 reads as if it came from whichever lane logged last
    assert out[1] == "[ERROR] [w3] boom"
    assert out[2] == "[ERROR] [w3] second line"


def test_a_sequential_run_has_no_lane_noise(monkeypatch, capsys):
    # Nothing to disambiguate when there's one process — don't pay for a tag.
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()
    log.telemetry("scenario.start", "  Scenario: Pay by card")
    assert capsys.readouterr().err.strip() == "[INFO]   Scenario: Pay by card"


# --------------------------------------------------------------------------
# An engine regression, not a test failure
# --------------------------------------------------------------------------

def test_a_hook_crash_reports_itself_before_behave_swallows_it(monkeypatch, capsys):
    """behave turns an exception escaping a hook into one line — 'ABORTED:
    HOOK-ERROR in hook=before_all' — and prints the traceback to stdout, which
    --quiet has already diverted to run.log. A CI operator had to download an
    artifact to learn that the framework, not the test, broke."""
    from noodle import hooks
    monkeypatch.setenv("CI", "true")
    log.attach_progress_handler()

    @hooks._report_engine_crash
    def before_all(context):
        raise RuntimeError("engine exploded")

    with pytest.raises(RuntimeError):        # re-raised: behave still decides
        before_all(None)
    err = capsys.readouterr().err
    assert "ENGINE ERROR in before_all: RuntimeError: engine exploded" in err
    assert "Traceback (most recent call last)" in err
    assert err.strip().splitlines()[-1].startswith("[ERROR]")   # every line prefixed


def test_every_behave_boundary_is_covered(monkeypatch):
    """The six hooks are where an engine exception escapes into behave. Ordinary
    engine functions are deliberately NOT wrapped — a blanket try/except would
    swallow failures rather than surface them."""
    from noodle import hooks
    for name in ("before_all", "before_feature", "before_scenario",
                 "after_step", "after_scenario", "after_all"):
        fn = getattr(hooks, name)
        assert getattr(fn, "__wrapped__", None) is not None, f"{name} unguarded"


def test_progress_handler_is_a_noop_in_json_mode(monkeypatch):
    """json mode's console handler is already stderr — a second one would
    double every record shipped to the log store."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("NOODLE_LOG_FORMAT", "json")
    before = len(log.logger.handlers)
    log.attach_progress_handler()
    assert len(log.logger.handlers) == before

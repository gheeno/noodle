"""NOOD_0206 — a step says which Python method ran it.

The per-scenario log that reaches Allure and the Azure Tests tab showed the
engine's breadcrumbs (POM resolutions, evidence) with no step boundaries and no
sign of which function did the work. What must hold: every step logs its own
line into that slice, the line names the real engine call, and it stays off the
CI build console (which carries telemetry only — a line per step there would be
10k lines on a big suite).
"""
import logging

from noodle import log
from noodle.orchestrator.runner import _handler_names, _log_step


def test_handler_map_reads_the_real_dispatch_chain():
    m = _handler_names()
    assert m["click"] == "actions.click"
    assert m["select"] == "actions.select_option"      # type != function name
    assert m["close_tab"] == "_close_tab"              # module-local handler
    # Guard against the regex silently rotting to zero matches if the chain's
    # shape changes: the fallback (bare type name) is graceful and invisible.
    assert len(m) > 100


def test_step_line_lands_in_the_scenario_log():
    log.start_scenario_log()
    try:
        _log_step("I click the login button",
                  {"type": "click", "locator": "login button"})
    finally:
        text = log.pop_scenario_log()
    assert "Step: I click the login button" in text
    assert "actions.click(locator='login button')" in text


def test_sensitive_args_are_masked_and_long_ones_truncated():
    log.start_scenario_log()
    try:
        _log_step("I send it", {"type": "fill", "auth_token": "supersecrettoken",
                                "body": "x" * 300})
    finally:
        text = log.pop_scenario_log()
    assert "supersecrettoken" not in text
    assert "..." in text and len(text.splitlines()[-1]) < 220


def test_same_named_scenarios_keep_their_own_log(tmp_path, monkeypatch):
    """--parallel: two workers running same-titled scenarios (two features, an
    outline's rows, a retry) wrote the same logs/<name>.log, so one report
    attached the other scenario's log."""
    from noodle import hooks
    from unit_tests.test_nood_0023 import _base_context, _make_scenario
    monkeypatch.chdir(tmp_path)

    class _FakeResult:
        def __init__(self, uuid):
            self.uuid, self.attachments = uuid, []

        def add_attachment(self, name, path, mime_type):
            self.attachments.append(path)

        def finish(self, scenario):
            pass

    monkeypatch.setattr(hooks._writer, "write_result", lambda ar: None)
    paths = []
    for uuid, line in (("aaaaaaaa1111", "worker one"), ("bbbbbbbb2222", "worker two")):
        fake = _FakeResult(uuid)
        monkeypatch.setattr(hooks, "_allure_result", lambda ctx, f=fake: f)
        log.start_scenario_log()
        log.logger.info(line)
        hooks.after_scenario(_base_context(_scenario_failed=False),
                             _make_scenario("Same title"))
        paths += fake.attachments

    assert len(set(paths)) == 2, paths
    from pathlib import Path
    assert "worker one" in Path(paths[0]).read_text(encoding="utf-8")
    assert "worker two" in Path(paths[1]).read_text(encoding="utf-8")


def test_step_line_stays_off_the_ci_build_console():
    """The progress handler filters on `event`; _log_step must not set one."""
    seen = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(getattr(r, "event", None))
    log.logger.addHandler(h)
    try:
        _log_step("I click", {"type": "click", "locator": "x"})
    finally:
        log.logger.removeHandler(h)
    assert seen == [None]

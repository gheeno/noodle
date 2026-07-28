"""NOOD_0187 — the framework never lies: report integrity, parallel scale,
CI readiness. Pins every false-green path the five-lens audit found closed."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.reporting import junit as junit_mod
from noodle.reporting import summary, writer


def _scenario(name="S", feature="F"):
    sc = MagicMock()
    sc.name = name
    sc.feature.name = feature
    sc.tags = []
    return sc


def _result_json(tmp_path, *, name="S", status="passed", history_id=None,
                 stop=None, tags=(), steps=None):
    """Write a minimal allure result JSON the readers understand."""
    import uuid
    r = {"uuid": str(uuid.uuid4()),
         "historyId": history_id or name,
         "name": name, "fullName": f"F: {name}",
         "labels": [{"name": "feature", "value": "F"},
                    *({"name": "tag", "value": t} for t in tags)],
         "steps": steps or [], "status": status,
         "start": 1, "stop": stop if stop is not None else 2}
    p = tmp_path / f"{r['uuid']}-result.json"
    p.write_text(json.dumps(r), encoding="utf-8")
    return p


# --- finish() reads behave's verdict, not just recorded steps ----------------

def test_soft_assert_synthetic_step_fails_result():
    ar = writer.ScenarioResult(_scenario())
    ar.add_failure("soft assertion failure(s)", "- price mismatch")
    ar.finish(_scenario())
    assert ar.result["status"] == "failed"
    assert "price mismatch" in ar.result["statusDetails"]["message"]


def test_hook_failure_with_no_steps_is_failed():
    sc = _scenario()
    sc.hook_failed = True
    sc.status = SimpleNamespace(name="hook_error")
    sc.steps = []
    ar = writer.ScenarioResult(sc)
    ar.finish(sc)
    assert ar.result["status"] == "failed"
    assert "hook/precondition" in ar.result["statusDetails"]["message"]


def test_undefined_step_is_failed_and_named():
    sc = _scenario()
    sc.status = SimpleNamespace(name="failed")
    sc.hook_failed = False
    sc.steps = [SimpleNamespace(status=SimpleNamespace(name="passed"), name="ok step"),
                SimpleNamespace(status=SimpleNamespace(name="undefined"),
                                name="mystery step nobody implemented")]
    ar = writer.ScenarioResult(sc)
    ar.add_step(SimpleNamespace(keyword="When", name="ok step", duration=0.1,
                                exception=None, error_message=""), "passed")
    ar.finish(sc)
    assert ar.result["status"] == "failed"
    assert "mystery step nobody implemented" in ar.result["statusDetails"]["message"]


def test_plain_mock_scenario_still_passes():
    # unit-test doubles that don't behave like behave objects keep the old
    # steps-only contract — no false reds from the honesty check.
    ar = writer.ScenarioResult(_scenario())
    ar.finish(MagicMock())
    assert ar.result["status"] == "passed"


def test_step_durations_are_real():
    ar = writer.ScenarioResult(_scenario())
    step = SimpleNamespace(keyword="When", name="waits", duration=2.5,
                           exception=None, error_message="")
    ar.add_step(step, "passed")
    s = ar.result["steps"][-1]
    assert s["stop"] - s["start"] == pytest.approx(2500, abs=50)


# --- skips exist ------------------------------------------------------------

def test_write_skip_result_and_summary_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_RESULTS_DIR", str(tmp_path))
    path, r = writer.write_skip_result(_scenario("gated"), "@live is opt-in")
    assert Path(path).is_file()
    assert r.result["status"] == "skipped"
    data = summary.collect(str(tmp_path))
    assert data["skipped"] == 1 and data["passed"] == 0 and data["failed"] == 0


def test_junit_counts_skipped(tmp_path):
    sc = _scenario("gated")
    _, r = None, writer.ScenarioResult(sc)
    r.result["status"] = "skipped"
    r.result["statusDetails"] = {"message": "no tesseract"}
    out = junit_mod.write_junit([r], str(tmp_path / "junit.xml"))
    text = Path(out).read_text(encoding="utf-8")
    assert 'skipped="1"' in text and "<skipped" in text


# --- flaky (retried-then-green) is visible ----------------------------------

def test_mark_flaky_stamps_final_attempt(tmp_path):
    _result_json(tmp_path, name="S", status="failed", history_id="h1", stop=10)
    final = _result_json(tmp_path, name="S", status="passed", history_id="h1", stop=20)
    assert summary.mark_flaky(str(tmp_path)) == 1
    assert json.loads(final.read_text(encoding="utf-8"))["statusDetails"]["flaky"] is True
    data = summary.collect(str(tmp_path))
    assert data["flaky"] == [{"scenario": "S", "attempts": 2}]
    assert data["passed"] == 1 and data["failed"] == 0


def test_retried_green_not_a_failure_for_quarantine_or_failed(tmp_path):
    from noodle.cli import _all_failures_quarantined
    _result_json(tmp_path, name="S", status="failed", history_id="h1", stop=10)
    _result_json(tmp_path, name="S", status="passed", history_id="h1", stop=20)
    # last attempt passed → nothing to judge (was: counted the dead attempt)
    assert _all_failures_quarantined(str(tmp_path)) is None


def test_quarantine_scan_still_sees_real_failures(tmp_path):
    from noodle.cli import _all_failures_quarantined
    _result_json(tmp_path, name="A", status="failed", tags=("quarantine",))
    assert _all_failures_quarantined(str(tmp_path)) is True
    _result_json(tmp_path, name="B", status="failed")
    assert _all_failures_quarantined(str(tmp_path)) is False


# --- empty run honesty ------------------------------------------------------

def test_rca_empty_run_never_renders_green(tmp_path):
    from noodle.reporting import rca_report
    md = rca_report.render_markdown(str(tmp_path))
    html = rca_report.render_html(str(tmp_path))
    for text in (md, html):
        assert "0 scenarios ran" in text
        assert "✅" not in text


def test_rca_provenance_header(tmp_path):
    from noodle.reporting import rca_report
    _result_json(tmp_path, name="S", status="passed")
    (tmp_path / "environment.properties").write_text("noodle.run.id=abc123\n", encoding="utf-8")
    md = rca_report.render_markdown(str(tmp_path))
    assert "run abc123" in md
    assert "1 passed, 0 failed, 0 skipped" in md


# --- shard slicing ----------------------------------------------------------

def test_shard_slice_partitions_deterministically(tmp_path):
    from noodle.cli import _shard_slice
    base = tmp_path / "tests"
    for n in "abcde":
        (base / n / "features").mkdir(parents=True)
        (base / n / "features" / f"{n}.feature").write_text("Feature: x\n", encoding="utf-8")
    slices = [_shard_slice(str(tmp_path), "tests", f"{i}/3") for i in (1, 2, 3)]
    flat = sorted(p for s in slices for p in s)
    assert flat == sorted(p.relative_to(base).as_posix()
                          for p in base.rglob("*.feature"))
    assert [len(s) for s in slices] == [2, 2, 1]


def test_shard_slice_rejects_bad_spec(tmp_path):
    import typer

    from noodle.cli import _shard_slice
    (tmp_path / "tests").mkdir()
    for bad in ("3", "0/4", "5/4", "x/y"):
        with pytest.raises(typer.BadParameter):
            _shard_slice(str(tmp_path), "tests", bad)


# --- parallel merge ---------------------------------------------------------

def test_merge_globs_junit_slices_and_flattens_leaves(tmp_path):
    from noodle.cli import _merge_worker_results
    root = tmp_path / "artifacts"
    results = root / "allure-results"
    wd = results / "p123"
    wd.mkdir(parents=True)
    # two per-feature junit slices in ONE worker dir (the behavex reality)
    for i, name in enumerate(("junit.aaaa.xml", "junit.bbbb.xml")):
        (wd / name).write_text(
            '<?xml version="1.0"?><testsuite name="Noodle" tests="1" '
            f'failures="0" skipped="0" time="1"><testcase name="s{i}" '
            'classname="F" time="1"/></testsuite>', encoding="utf-8")
    (wd / "environment.properties").write_text("a=1\n", encoding="utf-8")
    (wd / "deadbeef-result.json").write_text("{}", encoding="utf-8")
    (wd / "healing-report.aaaa.txt").write_text("healed X", encoding="utf-8")
    (results / "environment.properties").write_text("first=1\n", encoding="utf-8")  # existing wins
    (root / "traces" / "p123").mkdir(parents=True)
    (root / "traces" / "p123" / "Checkout.zip").write_text("z", encoding="utf-8")
    _merge_worker_results(results)
    merged = (root / "reports" / "junit.xml").read_text(encoding="utf-8")
    assert merged.count("<testcase") == 2          # both features survive
    assert (results / "deadbeef-result.json").is_file()
    assert (results / "environment.properties").read_text(encoding="utf-8") == "first=1\n"
    assert not (results / "p123").exists()
    assert (root / "traces" / "Checkout.zip").is_file()   # RCA can see it now
    assert "healed X" in (root / "reports" / "healing-report.txt").read_text(encoding="utf-8")


# --- runlock hardening ------------------------------------------------------

def test_stale_lock_break_is_single_winner(tmp_path, monkeypatch):
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOODLE_LOCK_TTL", "5")
    lock = Path(".noodle") / "locks" / "cart"
    lock.mkdir(parents=True)
    old = time.time() - 60
    os.utime(lock, (old, old))
    assert runlock._claim(lock) is True            # stale → broken and taken
    assert lock.is_dir()
    assert runlock._claim(lock) is False           # fresh again → held


def test_lane_heartbeat_refreshes_mtime(tmp_path, monkeypatch):
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    monkeypatch.setattr(runlock, "_my_index", None)
    idx = runlock.worker_index()
    lane = Path(".noodle") / "workers" / str(idx)
    old = time.time() - 1000
    os.utime(lane, (old, old))
    runlock.touch_worker_lane()
    assert time.time() - lane.stat().st_mtime < 60
    runlock.release_worker_index()


def test_reset_control_dir_takes_workspace(tmp_path):
    from noodle import runlock
    (tmp_path / ".noodle" / "locks" / "x").mkdir(parents=True)
    (tmp_path / ".noodle" / "workers" / "1").mkdir(parents=True)
    runlock.reset_control_dir(str(tmp_path))
    assert not (tmp_path / ".noodle" / "locks").exists()
    assert not (tmp_path / ".noodle" / "workers").exists()


# --- server-error assertion step --------------------------------------------

def test_no_server_errors_pattern_resolves():
    from noodle.resolver.patterns import match, normalize_phrasing
    for phrase in ("no server errors should occur",
                   "no server errors should have occurred",
                   "no HTTP errors should have occurred"):
        m = match(normalize_phrasing(phrase))
        assert m is not None and m[0] == "assert_no_server_errors", phrase


def test_assert_no_server_errors_action():
    from noodle.agents.web import actions
    page = SimpleNamespace(url="http://x/")
    actions.assert_no_server_errors(page, [])
    with pytest.raises(AssertionError):
        actions.assert_no_server_errors(page, ["500 POST http://x/api"])


def test_server_errors_in_valid_types():
    from noodle.resolver.step_resolver import VALID_TYPES
    assert "assert_no_server_errors" in VALID_TYPES


# --- contract lock scoped to blocked ----------------------------------------

def test_write_feature_allows_clean_contract_blocks_blocked(tmp_path, monkeypatch):
    from noodle.repl import core
    ws = tmp_path
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\n", encoding="utf-8")
    (ws / "noodle_tests").mkdir()
    content = "Feature: T\n  Scenario: s\n    When the user clicks 'Go'\n"
    rel = "noodle_tests/t.feature"
    state = {"intent_contracts": {rel: {"blocked": False, "intent_verified": True}}}
    monkeypatch.setattr(core, "load_state", lambda w=".": dict(state))
    monkeypatch.setattr(core, "save_state", lambda s, w=".": None)
    ok = core.write_feature(rel, content, workspace=str(ws))
    assert ok.get("error") in (None, "") or "intent contract" not in str(ok.get("error"))
    state["intent_contracts"][rel]["blocked"] = True
    blocked = core.write_feature(rel, content, overwrite=True, workspace=str(ws))
    assert blocked["ok"] is False
    assert "BLOCKED" in blocked["error"]


# --- cost ledger slices -----------------------------------------------------

def test_cost_write_json_slice_suffix(tmp_path, monkeypatch):
    from noodle.llm import cost
    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    monkeypatch.setattr(cost, "summary", lambda: {"model": "m", "by_purpose": {
        "author": {"calls": 1, "input_tokens": 1, "output_tokens": 1, "usd": 0.1}}})
    cost.write_json(tmp_path, suffix="aaaa")
    cost.write_json(tmp_path, suffix="bbbb")
    files = sorted(p.name for p in tmp_path.glob("llm_cost*.json"))
    assert len(files) == 2 and all(".p" in f for f in files)
    total = cost.load_total(tmp_path)
    assert total["by_purpose"]["author"]["calls"] == 2

"""NOOD_0183 — parallel-run safety + the web-wok capability gaps.

Two halves, both pinning things that were silently wrong rather than loudly
broken, which is why they needed a test more than most:

  * parallel — evidence files collided by NAME across workers, every scenario
    relaunched a browser, and nothing expressed "these two features share a
    login".
  * web wok — traces were written and never mentioned again; the page clock and
    pre-boot script injection had no vocabulary at all.
"""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Artifact isolation — the collision that made a parallel report untrustworthy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fn", ["screenshots_dir", "traces_dir", "network_dir", "videos_dir"])
def test_evidence_dirs_are_per_worker_only_in_parallel(fn, monkeypatch):
    from noodle.reporting import paths
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "artifacts")

    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)
    flat = getattr(paths, fn)()
    assert flat.name in ("screenshots", "traces", "network", "videos"), \
        "a sequential run must keep the classic flat layout"

    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    sharded = getattr(paths, fn)()
    assert sharded == flat / f"p{os.getpid()}"


def test_evidence_filenames_would_collide_without_the_worker_leaf(monkeypatch):
    """The concrete bug: two features sharing a step sentence — the whole point
    of a shared step vocabulary — produced the same filename, and the report
    attached whichever process wrote last."""
    from noodle.reporting import paths
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "artifacts")
    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    step = "FAILED_User_should_see__Your_Cart_.png"
    mine = paths.screenshots_dir() / step
    theirs = Path("artifacts/screenshots/p999999") / step        # another worker
    assert mine != theirs


# ---------------------------------------------------------------------------
# @serial / @lock:<name> — cross-worker mutual exclusion
# ---------------------------------------------------------------------------


def test_lock_names_from_tags():
    from noodle import runlock
    assert runlock.lock_names(["serial"]) == ["global"]
    assert runlock.lock_names(["lock:cart", "web", "smoke"]) == ["cart"]
    assert runlock.lock_names(["serial", "lock:pay", "lock:cart"]) == ["cart", "global", "pay"]
    assert runlock.lock_names([]) == []
    assert runlock.lock_names(["lock:"]) == []                   # empty name is not a lock
    assert runlock.lock_names(["lock:a/b"]) == ["a_b"]           # can't escape the lock dir


def test_lock_is_exclusive_and_reentrant(tmp_path, monkeypatch):
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    runlock._held.clear()
    runlock.acquire("cart")
    assert not runlock._claim(Path(".noodle/locks/cart")), "held lock must not be re-claimable"
    runlock.acquire("cart")                                      # re-entrant, not a deadlock
    runlock.release("cart")
    assert not runlock._claim(Path(".noodle/locks/cart")), "released too early"
    runlock.release("cart")
    assert runlock._claim(Path(".noodle/locks/cart"))


def test_lock_times_out_loudly_rather_than_running_unlocked(tmp_path, monkeypatch):
    """Silently proceeding would reintroduce exactly the race the tag exists
    to prevent, so a stuck lock must fail the scenario."""
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOODLE_LOCK_TIMEOUT", "0.3")
    monkeypatch.setenv("NOODLE_LOCK_TTL", "9999")                # never "stale"
    runlock._held.clear()
    (tmp_path / ".noodle/locks/busy").mkdir(parents=True)        # someone else holds it
    with pytest.raises(AssertionError, match="Timed out.*waiting for lock 'busy'"):
        runlock.acquire("busy")


def test_stale_lock_from_a_killed_worker_is_broken(tmp_path, monkeypatch):
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOODLE_LOCK_TTL", "0")                   # everything is stale
    runlock._held.clear()
    (tmp_path / ".noodle/locks/orphan").mkdir(parents=True)
    runlock.acquire("orphan")                                    # must not hang
    assert "orphan" in runlock._held


def test_lock_excludes_a_second_PROCESS(tmp_path):
    """The single-process checks above can't prove the thing that matters —
    that a lock held by one behavex worker blocks a different OS process."""
    from noodle import runlock
    runlock._held.clear()
    holder = textwrap.dedent("""
        import os, sys, time
        from noodle import runlock          # import BEFORE chdir — '' on sys.path is cwd
        os.chdir(sys.argv[1])
        runlock.acquire("shared_account")
        print("HELD", flush=True)
        time.sleep(1.5)
        runlock.release("shared_account")
    """)
    p = subprocess.Popen([sys.executable, "-c", holder, str(tmp_path)],
                         stdout=subprocess.PIPE, text=True,
                         cwd=Path(__file__).resolve().parent.parent)
    assert p.stdout.readline().strip() == "HELD"
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        os.environ["NOODLE_LOCK_TTL"] = "9999"
        t0 = time.monotonic()
        runlock.acquire("shared_account")        # must WAIT for the holder
        waited = time.monotonic() - t0
        runlock.release("shared_account")
    finally:
        os.chdir(cwd)
        os.environ.pop("NOODLE_LOCK_TTL", None)
        p.wait(timeout=10)
    assert waited > 0.5, f"second process took the lock while it was held (waited {waited:.2f}s)"


def test_worker_index_is_one_when_sequential_and_distinct_when_parallel(tmp_path, monkeypatch):
    from noodle import runlock
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)
    runlock._my_index = None
    assert runlock.worker_index() == 1

    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    runlock._my_index = None
    first = runlock.worker_index()
    assert runlock.worker_index() == first                       # cached, claimed once
    runlock._my_index = None                                     # pretend to be another worker
    second = runlock.worker_index()
    assert second != first, "two workers must not share a lane"
    runlock.release_worker_index()


# ---------------------------------------------------------------------------
# Per-lane credentials — N workers logging in as N different users
# ---------------------------------------------------------------------------


def test_env_ref_prefers_this_lane_then_falls_back(monkeypatch):
    from noodle.orchestrator.runner import substitute
    monkeypatch.setenv("SHOP_USER", "shared")
    monkeypatch.setenv("SHOP_USER_2", "lane_two")
    monkeypatch.setenv("BASE_URL", "http://x")

    monkeypatch.setenv("NOODLE_WORKER_INDEX", "2")
    assert substitute("logs in as {env:SHOP_USER}") == "logs in as lane_two"
    assert substitute("opens {env:BASE_URL}") == "opens http://x", \
        "a key with no lane variants must resolve exactly as before"

    monkeypatch.setenv("NOODLE_WORKER_INDEX", "3")               # no SHOP_USER_3 defined
    assert substitute("logs in as {env:SHOP_USER}") == "logs in as shared"

    monkeypatch.delenv("NOODLE_WORKER_INDEX")
    assert substitute("logs in as {env:SHOP_USER}") == "logs in as shared"


def test_preflight_accepts_lane_only_credentials(tmp_path, monkeypatch):
    """A suite that defines only SHOP_USER_1..N (no bare SHOP_USER) resolves
    fine at run time, so preflight must not block the run as 'missing'."""
    from noodle.repl import core
    app = tmp_path / "shop"
    (app / "features").mkdir(parents=True)
    (app / "features" / "login.feature").write_text(
        "Feature: L\n  Scenario: S\n    When User enters '{env:SHOP_USER}' in the user field\n")
    monkeypatch.chdir(tmp_path)
    # resolved_feature= bypasses target resolution, which would otherwise skip
    # the whole check (ok=True) outside a real workspace and prove nothing.
    monkeypatch.setattr(core, "_resolved_env", lambda *a, **k: {"SHOP_USER_1": "ryan"})
    assert core.preflight(resolved_feature="shop")["ok"], \
        "lane-only credentials wrongly reported missing"

    monkeypatch.setattr(core, "_resolved_env", lambda *a, **k: {})
    missing = core.preflight(resolved_feature="shop")
    assert not missing["ok"], "a genuinely missing key must still fail"
    assert missing["missing_secret_keys"] == ["SHOP_USER"]


# ---------------------------------------------------------------------------
# Browser reuse — the throughput fix (0.672s → 0.034s of setup per scenario)
# ---------------------------------------------------------------------------


def _fake_playwright():
    pw = MagicMock()
    browser = MagicMock()
    browser.is_connected.return_value = True
    pw.chromium.launch.return_value = browser
    pw.firefox.launch.return_value = browser
    return pw, browser


def test_same_launch_options_reuse_one_browser(monkeypatch):
    from noodle import hooks
    monkeypatch.delenv("NOODLE_REUSE_BROWSER", raising=False)
    hooks._browsers.clear()
    pw, browser = _fake_playwright()
    with patch("noodle.hooks.sync_playwright") as f:
        f.return_value.start.return_value = pw
        a = hooks._browser_for("chromium", {"headless": True, "slow_mo": 0}, None)
        b = hooks._browser_for("chromium", {"headless": True, "slow_mo": 0}, None)
    assert a is b
    pw.chromium.launch.assert_called_once()
    hooks._browsers.clear()


def test_a_different_browser_is_never_served_from_the_cache(monkeypatch):
    """@firefox, @headed and extra args all change what the browser IS — the
    cache must key on them or a scenario silently runs in the wrong engine."""
    from noodle import hooks
    monkeypatch.delenv("NOODLE_REUSE_BROWSER", raising=False)
    hooks._browsers.clear()
    pw, _ = _fake_playwright()
    with patch("noodle.hooks.sync_playwright") as f:
        f.return_value.start.return_value = pw
        base = {"headless": True, "slow_mo": 0}
        hooks._browser_for("chromium", base, None)
        hooks._browser_for("chromium", {**base, "headless": False}, None)   # @headed
        hooks._browser_for("chromium", {**base, "slow_mo": 500}, None)      # @slow
        hooks._browser_for("chromium", {**base, "args": ["--foo"]}, None)
        hooks._browser_for("firefox", base, None)                           # @firefox
    assert pw.chromium.launch.call_count == 4 and pw.firefox.launch.call_count == 1
    assert len(hooks._browsers) == 5
    hooks._browsers.clear()


def test_a_crashed_browser_is_evicted_not_handed_out(monkeypatch):
    from noodle import hooks
    monkeypatch.delenv("NOODLE_REUSE_BROWSER", raising=False)
    hooks._browsers.clear()
    pw, browser = _fake_playwright()
    with patch("noodle.hooks.sync_playwright") as f:
        f.return_value.start.return_value = pw
        hooks._browser_for("chromium", {"headless": True}, None)
        browser.is_connected.return_value = False        # died between scenarios
        hooks._browser_for("chromium", {"headless": True}, None)
    assert pw.chromium.launch.call_count == 2, "a dead browser was handed to the next scenario"
    hooks._browsers.clear()


def test_reuse_can_be_switched_off(monkeypatch):
    from noodle import hooks
    monkeypatch.setenv("NOODLE_REUSE_BROWSER", "0")
    hooks._browsers.clear()
    pw, _ = _fake_playwright()
    with patch("noodle.hooks.sync_playwright") as f:
        f.return_value.start.return_value = pw
        hooks._browser_for("chromium", {"headless": True}, None)
        hooks._browser_for("chromium", {"headless": True}, None)
    assert pw.chromium.launch.call_count == 2
    assert not hooks._browsers, "reuse off must not populate the cache"


def test_after_scenario_keeps_the_browser_but_drops_the_context(monkeypatch):
    """Isolation comes from the CONTEXT (cookies, storage, cache, permissions),
    so a fresh one per scenario buys the same separation the relaunch did."""
    from noodle import hooks
    monkeypatch.delenv("NOODLE_REUSE_BROWSER", raising=False)
    context = MagicMock()
    bctx, browser, pw = MagicMock(), MagicMock(), MagicMock()
    context._bctx, context._browser, context._pw = bctx, browser, pw
    scenario = MagicMock()
    scenario.name = "s"
    scenario.effective_tags = []

    hooks.after_scenario(context, scenario)

    bctx.close.assert_called_once()
    browser.close.assert_not_called()
    pw.stop.assert_not_called()


def test_after_all_closes_every_cached_browser(monkeypatch):
    from noodle import hooks
    pw, browser = MagicMock(), MagicMock()
    hooks._browsers.clear()
    hooks._browsers[("chromium", None, ())] = (pw, browser)
    hooks.close_cached_browsers()
    browser.close.assert_called_once()
    pw.stop.assert_called_once()
    assert not hooks._browsers


# ---------------------------------------------------------------------------
# CLI — --sequential, --failed, and the parallel-scheme guard
# ---------------------------------------------------------------------------


def test_sequential_beats_the_env_default(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from noodle import cli
    seen = {}
    monkeypatch.setattr(cli, "_run_parallel",
                        lambda *a, **k: seen.update(n=a[1]) or (0, False))
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0})())
    # NOOD_0187 — the run tail reads real results now; isolate from the repo's
    # artifacts tree and opt out of the zero-scenarios guard (nothing runs here).
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("NOODLE_ALLOW_EMPTY", "1")
    r = CliRunner().invoke(cli.app, ["run", "tests/", "--headless", "--sequential"],
                           env={"NOODLE_PARALLEL_PROCESSES": "4"})
    assert r.exit_code == 0
    assert "n" not in seen, "--sequential must override $NOODLE_PARALLEL_PROCESSES"


def test_scenario_scheme_warns_that_it_splits_a_feature_file(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from noodle import cli
    monkeypatch.setattr(cli, "_run_parallel",
                        lambda *a, **k: (0, False))
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("NOODLE_ALLOW_EMPTY", "1")
    r = CliRunner().invoke(cli.app, ["run", "tests/", "--headless",
                                     "--parallel", "2", "--parallel-scheme", "scenario"])
    assert "splits ONE feature file" in r.output


def test_failed_rerun_reads_the_last_run_results(tmp_path, monkeypatch):
    import json

    from noodle.cli import _failed_scenario_names
    results = tmp_path / "artifacts" / "allure-results"
    results.mkdir(parents=True)
    (results / "a-result.json").write_text(json.dumps({"name": "Checkout works", "status": "passed"}))
    (results / "b-result.json").write_text(json.dumps({"name": "Login rejects a bad password", "status": "failed"}))
    (results / "c-result.json").write_text(json.dumps({"name": "Cart totals", "status": "broken"}))
    (results / "d-result.json").write_text("not json")           # must not crash the rerun
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)

    assert _failed_scenario_names(str(tmp_path)) == ["Cart totals", "Login rejects a bad password"]


# ---------------------------------------------------------------------------
# Web wok — traces surfaced, clock control, pre-boot scripts
# ---------------------------------------------------------------------------


def test_rca_names_the_trace_for_every_failure(tmp_path, monkeypatch):
    """The trace was written on every failure and mentioned only in the live
    console — which --quiet, the default for agents and CI, discards."""
    from noodle.reporting import rca_report
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "artifacts")
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)
    traces = tmp_path / "artifacts" / "traces"
    traces.mkdir(parents=True)
    (traces / "Checkout_fails_on_an_empty_cart.zip").write_bytes(b"PK")

    out = rca_report._traces_md([{"scenario": "Checkout fails on an empty cart"},
                                 {"scenario": "A scenario with no trace saved"}])
    body = "\n".join(out)
    assert "playwright show-trace" in body
    assert "Checkout_fails_on_an_empty_cart.zip" in body
    assert "no trace saved" not in body, "must not advertise a trace that isn't there"
    assert rca_report._traces_md([]) == []


@pytest.mark.parametrize("text,expected", [
    ("sets the clock to '2026-01-01'", ("set_clock", {"when": "2026-01-01"})),
    ("pins the system date to '2026-12-25'", ("set_clock", {"when": "2026-12-25"})),
    ("freezes the clock", ("freeze_clock", {"when": None})),
    ("freezes the page clock at '2026-01-01'", ("freeze_clock", {"when": "2026-01-01"})),
    ("advances the clock by 30 days", ("advance_clock", {"amount": 30.0, "unit": "day"})),
    ("advances the system time by 1.5 hours", ("advance_clock", {"amount": 1.5, "unit": "hour"})),
    ("runs the script 'stubs.js' before every page load",
     ("add_init_script", {"script": "stubs.js"})),
])
def test_clock_and_init_script_vocabulary(text, expected):
    from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
    assert match(normalize_phrasing(normalize_subject(text))) == expected


def test_new_patterns_do_not_swallow_the_steps_next_to_them():
    from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject

    def act(t):
        m = match(normalize_phrasing(normalize_subject(t)))
        return m[0] if m else None
    assert act("waits 3 seconds") == "wait_seconds"
    assert act("runs the script 'window.x=1'") == "run_script"


def test_advance_clock_rejects_a_unit_it_cannot_convert():
    from noodle.agents.web import actions
    with pytest.raises(AssertionError, match="Unknown time unit 'fortnight'"):
        actions.advance_clock(MagicMock(), 1, "fortnights")


def test_init_script_reads_a_js_file_but_passes_inline_js_through(tmp_path, monkeypatch):
    from noodle.agents.web import actions
    monkeypatch.chdir(tmp_path)
    Path("stub.js").write_text("window.FLAG = 1;")
    page = MagicMock()

    actions.add_init_script(page, "stub.js")
    page.context.add_init_script.assert_called_with("window.FLAG = 1;")

    actions.add_init_script(page, "window.OTHER = 2;")
    page.context.add_init_script.assert_called_with("window.OTHER = 2;")

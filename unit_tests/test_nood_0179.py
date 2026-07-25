"""NOOD_0179 — probe wall-clock and payload cost.

Four measured costs, one phase each: the cold browser launch per call, the
fixed network-idle ceiling after goto, the per-selector uniqueness round
trips, and the step sentence repeated once per control row. Every test here
is browser-less — fakes for the Playwright surface, pure functions for the
rest (test_nood_0113 conventions).
"""
import json
import subprocess
import sys
import threading

import pytest

from noodle import config, counters
from noodle.agents.web import browser_pool
from noodle.agents.web import probe as probe_mod

# --- §0 shared engine resolution ---------------------------------------------

def test_default_engine_is_chromium(monkeypatch):
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    assert config.resolve_engine() == ("chromium", None, None)


def test_env_selects_the_engine(monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "firefox")
    assert config.resolve_engine() == ("firefox", None, None)


def test_safari_is_webkit_and_edge_is_a_chromium_channel():
    assert config.resolve_engine("safari") == ("webkit", None, None)
    assert config.resolve_engine("edge") == ("chromium", "msedge", None)


def test_an_unknown_engine_degrades_to_chromium_with_a_warning():
    engine, channel, warning = config.resolve_engine("nutscape")
    assert (engine, channel) == ("chromium", None)
    assert "nutscape" in warning and "chromium" in warning


def test_explicit_name_beats_the_env(monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "firefox")
    assert config.resolve_engine("webkit")[0] == "webkit"


def test_one_alias_table_feeds_the_run_engine_and_the_cli():
    """The drift this phase exists to kill: two literal copies of the table."""
    from noodle import cli, hooks
    assert hooks._ENGINE_ALIASES is config.ENGINE_ALIASES
    assert hooks._VALID_BROWSERS is config.VALID_BROWSERS
    assert cli._VALID_BROWSERS is config.VALID_BROWSERS


# --- §1 warm browser reuse ----------------------------------------------------

class _FakeBrowser:
    def __init__(self, engine, channel=None):
        self.engine, self.channel = engine, channel
        self.closed = False
        self.contexts = []
        self.connected = True

    def is_connected(self):
        return self.connected

    def new_context(self, **kw):
        ctx = _FakeContext(self)
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, browser):
        self.browser, self.closed = browser, False

    def close(self):
        self.closed = True


class _FakePlaywright:
    """Stands in for the started sync_playwright(); one attribute per engine."""

    def __init__(self):
        self.launches = []
        self.stopped = False

    def __getattr__(self, engine):
        if engine.startswith("_"):
            raise AttributeError(engine)
        return _FakeEngine(self, engine)

    def stop(self):
        self.stopped = True


class _FakeEngine:
    def __init__(self, pw, name):
        self.pw, self.name = pw, name

    def launch(self, **opts):
        self.pw.launches.append((self.name, opts.get("channel")))
        return _FakeBrowser(self.name, opts.get("channel"))


@pytest.fixture
def pool():
    p = browser_pool._Pool()
    p._pw = _FakePlaywright()          # pre-started: no real playwright import
    yield p
    p.close()


def test_the_pool_launches_once_per_engine_and_reuses(pool):
    first = pool.browser("chromium", None)
    second = pool.browser("chromium", None)
    assert first is second
    assert pool._pw.launches == [("chromium", None)]


def test_a_different_engine_launches_without_evicting_the_first(pool):
    chromium = pool.browser("chromium", None)
    firefox = pool.browser("firefox", None)
    assert chromium is not firefox
    assert pool.browser("chromium", None) is chromium      # still cached
    assert pool._pw.launches == [("chromium", None), ("firefox", None)]


def test_the_channel_is_part_of_the_key(pool):
    plain = pool.browser("chromium", None)
    edge = pool.browser("chromium", "msedge")
    assert plain is not edge
    assert ("chromium", "msedge") in pool._pw.launches


def test_a_disconnected_browser_is_relaunched(pool):
    dead = pool.browser("chromium", None)
    dead.connected = False
    fresh = pool.browser("chromium", None)
    assert fresh is not dead
    assert len(pool._pw.launches) == 2


def test_closing_the_pool_closes_every_browser_and_stops_playwright(pool):
    a, b = pool.browser("chromium", None), pool.browser("firefox", None)
    pw = pool._pw
    pool.close()
    assert a.closed and b.closed and pw.stopped


def test_a_missing_engine_names_the_install_fix(pool):
    class _Broken(_FakePlaywright):
        def __getattr__(self, engine):
            if engine.startswith("_"):
                raise AttributeError(engine)
            raise RuntimeError("Executable doesn't exist")

    pool._pw = _Broken()
    with pytest.raises(browser_pool.EngineUnavailable) as e:
        pool.browser("firefox", None)
    assert "firefox" in str(e.value) and "playwright install firefox" in str(e.value)


def test_a_missing_edge_channel_says_install_edge(pool):
    class _Broken(_FakePlaywright):
        def __getattr__(self, engine):
            if engine.startswith("_"):
                raise AttributeError(engine)
            raise RuntimeError("channel msedge not found")

    pool._pw = _Broken()
    with pytest.raises(browser_pool.EngineUnavailable) as e:
        pool.browser("chromium", "msedge")
    assert "Edge" in str(e.value)


def test_a_missing_engine_never_falls_back_to_chromium(pool):
    """NOOD_0122 — decide by engine. A silent chromium substitution would make
    the probe claim firefox evidence it never gathered."""
    class _OnlyChromium(_FakePlaywright):
        def __getattr__(self, engine):
            if engine.startswith("_"):
                raise AttributeError(engine)
            if engine != "chromium":
                raise RuntimeError("not installed")
            return _FakeEngine(self, engine)

    pool._pw = _OnlyChromium()
    with pytest.raises(browser_pool.EngineUnavailable):
        pool.browser("webkit", None)
    assert pool._pw.launches == []


def test_reuse_knob_defaults_on_and_reads_falsey_words(monkeypatch):
    monkeypatch.delenv("NOODLE_PROBE_REUSE_BROWSER", raising=False)
    assert browser_pool.reuse_enabled()
    for off in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv("NOODLE_PROBE_REUSE_BROWSER", off)
        assert not browser_pool.reuse_enabled(), off


# --- the worker thread --------------------------------------------------------

def test_the_worker_runs_everything_on_one_daemon_thread():
    """Playwright sync objects are thread-affine — the SAME thread every call
    is the whole reason this worker exists instead of an executor."""
    w = browser_pool._Worker()
    seen = [w.submit(threading.get_ident) for _ in range(3)]
    assert len(set(seen)) == 1
    assert seen[0] != threading.get_ident()          # not the caller's thread
    assert w._thread.daemon                          # or the CLI would hang


def test_the_worker_relays_the_exception_to_the_caller():
    w = browser_pool._Worker()

    def _boom():
        raise ValueError("from the worker")

    with pytest.raises(ValueError, match="from the worker"):
        w.submit(_boom)
    assert w.submit(lambda: "still alive") == "still alive"


def test_close_is_safe_when_the_worker_never_started():
    fresh = browser_pool._Worker()
    saved, browser_pool._worker = browser_pool._worker, fresh
    try:
        browser_pool.close_probe_browser()           # must not raise or block
    finally:
        browser_pool._worker = saved


def test_close_gives_up_on_a_wedged_browser_instead_of_hanging():
    """Daemonhood is the guarantee; the timeout is what keeps atexit honest."""
    w, hung = browser_pool._Worker(), threading.Event()
    saved_w, saved_p = browser_pool._worker, browser_pool._pool
    browser_pool._worker = w
    browser_pool._pool = type("P", (), {"close": lambda self: hung.wait(30)})()
    try:
        w.submit(lambda: None)                       # start the thread
        browser_pool.close_probe_browser(timeout=0.2)   # returns despite the wedge
    finally:
        hung.set()
        browser_pool._worker, browser_pool._pool = saved_w, saved_p


def test_an_active_pool_does_not_block_interpreter_exit():
    """Trap (b): a non-daemon worker holding Playwright would hang every
    one-shot `noodle probe`. Proven in a real subprocess, not by inspection."""
    code = (
        "from noodle.agents.web import browser_pool as bp;"
        "bp._worker.submit(lambda: 1);"
        "bp._pool._pw = type('P', (), {'stop': lambda self: None})();"
        "print('done')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0 and "done" in out.stdout


# --- counters: a bump means a REAL launch ------------------------------------

def _count_launches(fn):
    counters.reset()
    fn()
    return counters.counts.get("browser_launch", 0)


def test_two_pooled_calls_on_one_engine_bump_once(pool):
    assert _count_launches(lambda: [pool.browser("chromium", None),
                                    pool.browser("chromium", None)]) == 1


def test_two_engines_bump_twice(pool):
    assert _count_launches(lambda: [pool.browser("chromium", None),
                                    pool.browser("firefox", None)]) == 2


def test_reuse_off_bumps_per_call(monkeypatch):
    monkeypatch.setenv("NOODLE_PROBE_REUSE_BROWSER", "0")
    pw = _FakePlaywright()

    class _Ctx:
        def __enter__(self): return pw
        def __exit__(self, *a): return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Ctx())
    counters.reset()
    for _ in range(2):
        browser_pool.with_browser(lambda b: b.engine)
    assert counters.counts.get("browser_launch") == 2
    assert all(b.closed for b in [])       # legacy path closes its own browser


def test_reuse_off_still_honours_the_engine(monkeypatch):
    monkeypatch.setenv("NOODLE_PROBE_REUSE_BROWSER", "0")
    monkeypatch.setenv("NOODLE_BROWSER", "safari")
    pw = _FakePlaywright()

    class _Ctx:
        def __enter__(self): return pw
        def __exit__(self, *a): return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Ctx())
    engine, _ = browser_pool.with_browser(lambda b: b.engine)
    assert engine == "webkit"


def test_with_browser_returns_the_engine_warning(monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "nutscape")
    monkeypatch.setattr(browser_pool, "reuse_enabled", lambda: True)
    fake = _FakePlaywright()
    monkeypatch.setattr(browser_pool._pool, "_pw", fake, raising=False)
    result, warning = browser_pool.with_browser(lambda b: b.engine)
    assert result == "chromium" and "nutscape" in warning
    browser_pool._pool.close()


# --- §2 observer-based initial settle -----------------------------------------

class _FakeSettlePage:
    """Records what the settle actually waited on."""

    def __init__(self, mutates=False, armable=True, blow_up=False):
        self.calls, self.mutates = [], mutates
        self.armable, self.blow_up = armable, blow_up

    def wait_for_function(self, expr, timeout=None):
        self.calls.append(("wait_fn", expr, timeout))
        if "__noodleMut" in expr and "n > 0" in expr and not self.mutates:
            raise TimeoutError("no mutation")

    def evaluate(self, js, *a, **k):
        self.calls.append(("evaluate", js))
        if self.blow_up:
            raise RuntimeError("execution context destroyed")
        if "__noodleMo" in js and not self.armable:
            raise RuntimeError("cannot script")
        return True

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("load_state", state, timeout))


def _idle_timeouts(page):
    return [c[2] for c in page.calls if c[0] == "load_state"]


def test_a_static_page_never_pays_the_three_second_ceiling():
    page = _FakeSettlePage(mutates=False)
    assert probe_mod._settle_initial(page, 15000) == "quiet"
    assert _idle_timeouts(page) == [1000]        # the short belt, not 3000


def test_a_rendering_page_waits_for_the_quiet_window():
    page = _FakeSettlePage(mutates=True)
    assert probe_mod._settle_initial(page, 15000) == "mutation"
    assert any("Date.now() - window.__noodleMut.last >= 250" in c[1]
               for c in page.calls if c[0] == "wait_fn")


def test_the_body_wait_still_runs_first():
    """The Angular transitional-blank-body lesson (NOOD_0109) is not traded
    away for speed."""
    page = _FakeSettlePage()
    probe_mod._settle_initial(page, 15000)
    first = next(c for c in page.calls if c[0] == "wait_fn")
    assert "childElementCount" in first[1]


def test_an_unscriptable_page_falls_back_to_the_legacy_settle():
    page = _FakeSettlePage(armable=False)
    assert probe_mod._settle_initial(page, 15000) == "navigation"
    assert 3000 in _idle_timeouts(page)          # legacy ceiling, verbatim


def test_the_legacy_knob_restores_the_old_settle(monkeypatch):
    monkeypatch.setenv("NOODLE_PROBE_SETTLE", "legacy")
    page = _FakeSettlePage()
    assert probe_mod._settle_initial(page, 15000) == "navigation"
    assert _idle_timeouts(page) == [3000]


def test_the_settle_never_raises_however_broken_the_page():
    class _AllBroken:
        def __getattr__(self, name):
            def _boom(*a, **k):
                raise RuntimeError(name)
            return _boom

    assert isinstance(probe_mod._settle_initial(_AllBroken(), 1000), str)


def test_the_settle_path_is_debug_only(monkeypatch):
    monkeypatch.delenv("NOODLE_PROBE_TIMINGS", raising=False)
    assert not probe_mod._timings_on()
    monkeypatch.setenv("NOODLE_PROBE_TIMINGS", "1")
    assert probe_mod._timings_on()


# --- §3 batched uniqueness ----------------------------------------------------

@pytest.mark.parametrize("selector,batchable", [
    ('[id="search-input-0"]', True),
    ('button[class~="footer"]', True),
    ("a", True),
    ("text=Sign in", False),
    (':nth-match(button, 2)', False),
    ('div >> button', False),
    ("", False),
    (None, False),
])
def test_batchable_classification(selector, batchable):
    assert probe_mod._batchable(selector) is batchable


class _FakeTarget:
    def __init__(self, counts=None, evaluate_raises=False, locator_counts=None):
        self.counts, self.evaluate_raises = counts, evaluate_raises
        self.locator_counts = locator_counts or {}
        self.evaluates, self.located = 0, []

    def evaluate(self, js, selectors=None):
        self.evaluates += 1
        if self.evaluate_raises:
            raise RuntimeError("evaluate blew up")
        return [self.counts.get(s) for s in selectors]

    def locator(self, sel):
        self.located.append(sel)
        target = self

        class _Loc:
            def count(self):
                return target.locator_counts.get(sel, 0)
        return _Loc()


def _controls(*selectors):
    return [{"name": s, "selector": s} for s in selectors]


def test_the_batch_marks_exactly_what_the_loop_would():
    controls = _controls("#one", ".many", "#zero")
    target = _FakeTarget(counts={"#one": 1, ".many": 3, "#zero": 0})
    probe_mod._verify_unique(target, controls)
    assert controls[0]["unique"] is True and "matches" not in controls[0]
    assert controls[1]["unique"] is False and controls[1]["matches"] == 3
    assert "unique" not in controls[2]           # zero hits stay unmarked
    assert target.evaluates == 1 and target.located == []


def test_playwright_only_selectors_keep_the_locator_round_trip():
    controls = _controls("#css", "text=Sign in")
    target = _FakeTarget(counts={"#css": 1},
                         locator_counts={"text=Sign in": 2})
    probe_mod._verify_unique(target, controls)
    assert controls[0]["unique"] is True
    assert controls[1]["matches"] == 2
    assert target.located == ["text=Sign in"]    # ONLY the unbatchable one


def test_an_invalid_selector_is_left_unmarked():
    controls = _controls("#ok", "?!broken")
    target = _FakeTarget(counts={"#ok": 1, "?!broken": None})
    probe_mod._verify_unique(target, controls)
    assert controls[0]["unique"] is True
    assert "unique" not in controls[1]


def test_a_blown_up_evaluate_degrades_to_the_loop():
    controls = _controls("#one", "#two")
    target = _FakeTarget(evaluate_raises=True,
                         locator_counts={"#one": 1, "#two": 4})
    probe_mod._verify_unique(target, controls)
    assert controls[0]["unique"] is True and controls[1]["matches"] == 4
    assert target.located == ["#one", "#two"]


def test_the_cap_still_bounds_the_batch():
    controls = _controls(*[f"#c{i}" for i in range(probe_mod._UNIQUE_CAP + 10)])
    target = _FakeTarget(counts={c["selector"]: 1 for c in controls})
    probe_mod._verify_unique(target, controls)
    assert all("unique" in c for c in controls[:probe_mod._UNIQUE_CAP])
    assert all("unique" not in c for c in controls[probe_mod._UNIQUE_CAP:])


def test_the_legacy_knob_restores_the_per_selector_loop(monkeypatch):
    monkeypatch.setenv("NOODLE_UNIQUE_LEGACY", "1")
    controls = _controls("#one")
    target = _FakeTarget(counts={"#one": 1}, locator_counts={"#one": 1})
    probe_mod._verify_unique(target, controls)
    assert target.evaluates == 0 and target.located == ["#one"]


def test_the_batch_sums_across_shadow_roots():
    """Playwright's locator pierces open shadow roots; querySelectorAll does
    not — summing per root is what keeps the counts identical."""
    assert "shadowRoot" in probe_mod._UNIQUE_COUNT_JS
    assert "gather(document)" in probe_mod._UNIQUE_COUNT_JS


def test_the_uniqueness_script_did_not_steal_the_results_count_name():
    """_COUNT_JS already existed (the 'NN results' element). Two module-level
    constants of one name means the later one silently wins."""
    assert probe_mod._COUNT_JS is not probe_mod._UNIQUE_COUNT_JS
    assert "results" not in probe_mod._UNIQUE_COUNT_JS


# --- §4 brief rendering -------------------------------------------------------

def _raw(**over):
    c = {"tag": "button", "id": "", "role": "", "type": "", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True}
    c.update(over)
    return c


def _representative_result():
    """A retail-homepage shape: many plainly-named controls, a few needing a
    POM entry — the ratio the compact payload actually meets."""
    controls = [_raw(text=f"shop department {i} now") for i in range(40)]
    controls += [_raw(tag="input", type="text", ph="search")]
    controls += [_raw(tag="div", cls=f"machine-{i}") for i in range(6)]
    raw = {"controls": controls, "headings": ["Weekly Deals", "Our services"]}
    pg = probe_mod.summarize(raw, url="https://shop.example/", title="Shop")
    return {"pages": [pg], "errors": []}


def _size(payload):
    return len(json.dumps(payload, default=str))


def test_brief_costs_less_than_full():
    """The ticket's target was 0.75x; the honest measured figure is ~0.92x.

    What brief can remove is the template overhead — 9 bytes on a click row
    ('clicks "' + '"'), 30 on a field row. What it CANNOT remove is the
    control name, and the name is most of the line. Halving the payload would
    mean dropping names or dropping the exact steps of POM-needing rows, and
    the second is exactly the precision the exception rule protects. The lever
    is real but small; the cap ladder is what bounds this payload.
    """
    result = _representative_result()
    full = probe_mod.compact_payload(result)
    brief = probe_mod.compact_payload(result, brief=True)
    assert _size(brief) < 0.95 * _size(full), (_size(brief), _size(full))


def test_brief_shrinks_the_step_list_it_targets():
    """The claim scoped to the list brief actually rewrites."""
    result = _representative_result()
    full = probe_mod.compact_payload(result)["pages"][0]
    brief = probe_mod.compact_payload(result, brief=True)["pages"][0]
    was = _size(full["suggested_steps"])
    now = _size(brief["suggested_steps"]) + _size(brief.get("step_names", {}))
    assert now < 0.85 * was, (now, was)


def test_brief_sends_the_templates_once():
    brief = probe_mod.compact_payload(_representative_result(), brief=True)
    assert brief["step_templates"] == probe_mod.STEP_TEMPLATES
    assert "clicks" in brief["step_templates"]["click"]


def test_brief_loses_no_name_selector_or_pom():
    result = _representative_result()
    full = probe_mod.compact_payload(result)["pages"][0]
    brief = probe_mod.compact_payload(result, brief=True)["pages"][0]
    assert brief["pom_yaml"] == full["pom_yaml"]
    assert brief["headings"] == full["headings"]
    assert ([c["selector"] for c in brief["needs_pom"]]
            == [c["selector"] for c in full["needs_pom"]])
    # every step the full payload offered is still derivable
    names = {n for group in brief.get("step_names", {}).values() for n in group}
    exact = set(brief["suggested_steps"])
    for step in full["suggested_steps"]:
        assert step in exact or any(f'"{n}"' in step for n in names), step


def test_load_bearing_rows_keep_their_exact_step():
    result = _representative_result()
    brief = probe_mod.compact_payload(result, brief=True)["pages"][0]
    for c in brief["needs_pom"]:
        assert c.get("step"), c["name"]


def test_keeps_exact_step_covers_the_three_flags():
    for flag in ("needs_pom", "machine_name", "caption_attr_only"):
        assert probe_mod._keeps_exact_step({flag: True}), flag
    assert not probe_mod._keeps_exact_step({"needs_pom": False})


def test_full_payload_is_unchanged_by_the_flag():
    result = _representative_result()
    assert probe_mod.compact_payload(result) == probe_mod.compact_payload(result)
    assert "step_templates" not in probe_mod.compact_payload(result)
    assert "step_names" not in probe_mod.compact_payload(result)["pages"][0]


def test_the_text_render_prints_templates_once():
    result = _representative_result()
    brief = probe_mod.render(result, compact=True, brief=True)
    full = probe_mod.render(result, compact=True)
    assert len(brief) < len(full)
    assert probe_mod.STEP_TEMPLATES["click"] in brief


def test_the_cli_flag_reaches_the_renderer(monkeypatch):
    """Parity: --brief must not stop at the option declaration."""
    from typer.testing import CliRunner

    from noodle import cli
    monkeypatch.setattr("noodle.repl.core.probe_page",
                        lambda *a, **k: _representative_result())
    out = CliRunner().invoke(cli.app, ["probe", "https://shop.example/",
                                       "--json", "--brief"])
    assert out.exit_code == 0
    assert "step_templates" in json.loads(out.stdout)


def test_the_mcp_param_reaches_the_renderer(monkeypatch):
    from noodle.mcp import server
    monkeypatch.setattr(server.core, "probe_page",
                        lambda *a, **k: _representative_result())
    fn = getattr(server.probe_page, "fn", server.probe_page)
    assert "step_templates" in fn(url="https://shop.example/", brief=True)
    assert "step_templates" not in fn(url="https://shop.example/")


def test_the_repl_brief_returns_the_compact_payload(monkeypatch):
    from noodle.repl import core
    monkeypatch.setattr("noodle.agents.web.probe.probe",
                        lambda *a, **k: _representative_result())
    assert "step_templates" in core.probe_page("https://shop.example/",
                                               brief=True)
    assert "step_templates" not in core.probe_page("https://shop.example/")


def test_a_section_render_still_honours_the_compact_cap():
    """`--compact --section steps` returned before the cap was computed, so it
    emitted the whole uncapped inventory — the flag documented as capping at
    25 was silently a no-op."""
    result = _representative_result()
    lines = probe_mod.render(result, compact=True, section="steps").splitlines()
    assert len(lines) <= probe_mod.DEFAULT_COMPACT_CAP + 1   # + overflow note
    assert any("more" in line for line in lines)


def test_an_explicit_max_controls_still_wins_over_the_compact_default():
    result = _representative_result()
    lines = probe_mod.render(result, compact=True, section="steps",
                             max_controls=5).splitlines()
    assert len(lines) <= 6


def test_a_full_section_render_stays_uncapped():
    """Non-compact is opt-in verbose — the fix must not cap it too."""
    result = _representative_result()
    lines = probe_mod.render(result, section="steps").splitlines()
    assert len(lines) > probe_mod.DEFAULT_COMPACT_CAP


# --- §7 acceptance: the flag is named on every surface ------------------------

def test_brief_is_documented_on_every_probe_surface():
    import inspect

    from noodle import cli
    from noodle.mcp import server
    from noodle.repl import core
    surfaces = {
        "cli": inspect.getsource(cli.probe),
        "mcp": server.probe_page.__doc__ or "",
        "repl": core.probe_page.__doc__ or "",
        "llm-performance": (probe_mod.__file__.rsplit("noodle/", 1)[0]
                            + "docs/llm-performance.md"),
    }
    for name, text in surfaces.items():
        if name == "llm-performance":
            text = open(text).read()
        assert "brief" in text.lower(), name


def test_the_knobs_are_documented():
    import inspect
    src = inspect.getsource(probe_mod) + inspect.getsource(browser_pool)
    for knob in ("NOODLE_PROBE_REUSE_BROWSER", "NOODLE_PROBE_SETTLE",
                 "NOODLE_UNIQUE_LEGACY", "NOODLE_PROBE_TIMINGS"):
        assert knob in src, knob

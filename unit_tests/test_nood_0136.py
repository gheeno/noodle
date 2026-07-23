"""NOOD_0136 — probe hardening: one bounded probe that survives unfamiliar
SPAs, shadow DOM, iframes, virtualized listboxes, Flutter Web, and native
accessibility trees — and is HONEST when it can't (visual_only, author_ready,
named warnings) instead of emitting weak selectors.

The reviewed config-gated login session burned 131 AIC because the agent
treated the page as a template, skipped the probe, and guessed selectors into
a five-run repair loop. These tests pin:

  §1  decision contract — every always-on surface narrows the template
      exemption to "every needed control standard AND visible".
  §2  scope — shadow chains ride as `scope`, frames are separate blocks with
      the switch step and NO page-global POM, selectors are proven unique.
  §3  settle — MutationObserver-armed, reason-coded, denylist unchanged.
  §4  bounded discovery — generic disclosure candidates, never a mutating
      name, capped, with a trace that names every skip.
  §6  Flutter/canvas — visual_only verdict suppresses selector output.
  §7  native probe — pure XML normalization for all four platforms,
      vocabulary-shaped steps, snapshot-only.
  §8  payload — coverage/warnings/author_ready survive the compact diet;
      auto-open failures name the control and phase.

Pure Python: fake pages, fixture XML strings. No browser, no device, no LLM
(the _COLLECT_JS shadow/frame walk itself was verified end-to-end against
scratchpad fixtures with a real browser, like NOOD_0134's host-swap).
"""
from unittest.mock import MagicMock

from noodle import cli
from noodle.agents.mobile import probe as mprobe
from noodle.agents.web import probe
from noodle.mcp import server
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing
from unit_tests.test_nood_0110 import REPO

# --- §1 decision contract on every always-on surface -------------------------

_SURFACES = {
    "AGENTS.md floor": cli._AGENTS_MD,
    "MCP instructions": server._INSTRUCTIONS,
    ".claude skill card": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
    ".copilot skill card": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
    "agent-playbook": (REPO / "docs/agent-playbook.md").read_text(),
}


def test_template_exemption_is_narrow_on_every_surface():
    """The 131-AIC session: 'login-shaped' excused skipping the probe on a
    page with a hidden config panel. Every surface must now condition the
    exemption on standard AND visible, and route hidden/config/custom/SPA
    to the probe."""
    for name, text in _SURFACES.items():
        flat = " ".join(text.split()).lower()
        assert "visible" in flat and "probe" in flat, name
        assert "hidden" in flat, f"{name}: no hidden-control trigger"
        assert "config" in flat, f"{name}: no config-gate trigger"
        assert "custom" in flat, f"{name}: no custom-control trigger"
        assert "spa" in flat, f"{name}: no SPA trigger"


def test_playbook_names_the_config_gate_counterexample():
    pb = _SURFACES["agent-playbook"]
    assert "NOT template-shaped" in pb
    assert "--discover" in pb
    assert "probe-app" in pb


def test_mcp_instructions_carry_discover_and_probe_app():
    assert "discover=True" in server._INSTRUCTIONS
    assert "probe_app" in server._INSTRUCTIONS
    assert "author_ready" in server._INSTRUCTIONS


# --- §2 scope: shadow chains, frames, uniqueness ------------------------------

def _raw(**over):
    c = {"tag": "input", "id": "", "role": "", "type": "text", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True,
         "expanded": "", "shadow": ""}
    c.update(over)
    return c


def test_summarize_threads_shadow_scope():
    pg = probe.summarize({"controls": [
        _raw(id="u", label="user", shadow="app-root > login-card"),
        _raw(id="v", label="pass")], "headings": []})
    assert pg["controls"][0]["scope"] == "shadow:app-root > login-card"
    assert "scope" not in pg["controls"][1]          # top document stays lean


def test_summarize_threads_discovery_signals():
    pg = probe.summarize({"controls": [
        _raw(tag="button", type="", text="Filters", expanded="false"),
        _raw(tag="div", role="tab", text="Devices", cls="tab")],
        "headings": []})
    assert pg["controls"][0]["expanded"] == "false"
    assert pg["controls"][1]["role"] == "tab"


def _loc_page(counts):
    """Fake page whose locator(sel).count() returns counts[sel]."""
    page = MagicMock()

    def locator(sel):
        loc = MagicMock()
        loc.count.return_value = counts.get(sel, 0)
        return loc

    page.locator.side_effect = locator
    return page


def test_verify_unique_marks_ambiguity_with_match_count():
    controls = [{"selector": "#one"}, {"selector": ".many"},
                {"selector": ".gone"}]
    probe._verify_unique(_loc_page({"#one": 1, ".many": 3}), controls)
    assert controls[0]["unique"] is True
    assert controls[1]["unique"] is False and controls[1]["matches"] == 3
    assert "unique" not in controls[2]        # 0 = unverifiable, not a verdict


def test_verify_unique_ignores_mock_counts():
    # a MagicMock count() (non-int) must never fabricate a verdict
    controls = [{"selector": "#x"}]
    probe._verify_unique(MagicMock(), controls)
    assert "unique" not in controls[0]


class _Frame:
    def __init__(self, name, url, raw):
        self.name, self.url, self._raw = name, url, raw

    def evaluate(self, js):
        return self._raw

    def locator(self, sel):
        loc = MagicMock()
        loc.count.return_value = 1
        return loc


def test_collect_frames_scopes_and_forbids_pom():
    main = _Frame("", "https://h/", {})
    pay = _Frame("payframe", "https://pay.example/checkout",
                 {"controls": [_raw(tag="input", id="cc", label="card number"),
                               _raw(tag="input")],          # anonymous
                  "headings": ["Payment"]})
    page = MagicMock()
    page.main_frame = main
    page.frames = [main, pay]
    pg = {"controls": [], "headings": []}
    probe._collect_frames(page, pg, 1000)
    blk = pg["frames"][0]
    assert blk["frame"] == "payframe"
    assert blk["switch_step"] == 'switches to the "payframe" frame'
    assert pattern_match(normalize_phrasing(blk["switch_step"]))
    assert blk["pom_yaml"] == ""              # POM cannot reach into a frame
    assert all(c["scope"] == "frame:payframe" for c in blk["controls"])
    assert all("pom" not in c for c in blk["controls"])
    assert any("unreachable via POM" in w for w in blk["warnings"])


def test_collect_frames_skips_empty_and_survives_errors():
    main = _Frame("", "https://h/", {})
    empty = _Frame("e", "https://h/e", {"controls": [], "headings": []})
    broken = _Frame("b", "https://h/b", {})
    broken.evaluate = MagicMock(side_effect=RuntimeError("detached"))
    page = MagicMock()
    page.main_frame = main
    page.frames = [main, empty, broken]
    pg = {"controls": [], "headings": []}
    probe._collect_frames(page, pg, 1000)
    assert "frames" not in pg
    assert any("detached" in w for w in pg["warnings"])


# --- §3 settle: observer-armed, reason-coded ---------------------------------

class _SettlePage:
    def __init__(self, url="https://h/", first_wait_raises=False):
        self.url, self.calls = url, []
        self.first_wait_raises = first_wait_raises

    def wait_for_function(self, expr, arg=None, timeout=None):
        self.calls.append(("wait_for_function", timeout))
        if self.first_wait_raises and len(self.calls) == 1:
            raise TimeoutError("no mutation")

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append((state, timeout))

    def evaluate(self, js):
        self.calls.append(("evaluate",))
        return True


def test_settle_reports_no_change_when_nothing_mutates():
    p = _SettlePage(first_wait_raises=True)
    assert probe._settle(p, 15000, armed=True, url_before="https://h/") == \
        "no-change"
    # the 1 s change-wait cap survives the observer upgrade
    assert p.calls[0][1] <= 1000
    assert not any(c[0] == "networkidle" for c in p.calls)


def test_settle_disconnects_the_observer():
    p = _SettlePage()
    probe._settle(p, 15000, armed=True, url_before="https://h/")
    assert ("evaluate",) in p.calls


def test_arm_returns_none_when_page_cannot_be_scripted():
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("navigating")
    assert probe._arm(page) is None


# --- §4 bounded discovery -----------------------------------------------------

def _ctrl(**over):
    c = {"kind": "button", "name": "x", "selector": "#x", "visible": True,
         "needs_pom": False, "step": 'clicks "x"'}
    c.update(over)
    return c


def test_discover_candidates_generic_signals_only():
    controls = [
        _ctrl(name="trigger dev panel", visible=False),        # hidden trigger
        _ctrl(name="filters", expanded="false"),               # aria-expanded
        _ctrl(name="devices", role="tab"),                     # tab role
        _ctrl(name="advanced settings"),                       # disclosure name
        _ctrl(name="plain widget"),                            # no signal
        _ctrl(kind="field", name="user id", visible=True),     # not a button
    ]
    cands, skipped = probe._discover_candidates(controls)
    assert [c["name"] for c, _ in cands] == \
        ["trigger dev panel", "filters", "devices", "advanced settings"]
    assert skipped == []


def test_discover_candidates_never_touch_a_mutating_name():
    controls = [_ctrl(name="submit order panel", visible=False),
                _ctrl(name="delete settings", expanded="false"),
                _ctrl(name="login menu", role="tab")]
    cands, skipped = probe._discover_candidates(controls)
    assert cands == []
    assert all(s["reason"] == "state-mutating name" for s in skipped)
    assert len(skipped) == 3


class _DiscoverPage:
    """Fake page: every candidate click reveals one fresh control."""

    def __init__(self):
        self.url = "https://h/"
        self.clicked, self.escapes, self.n = [], [], 0
        self.keyboard = MagicMock()
        self.keyboard.press.side_effect = \
            lambda k: self.escapes.append(k)

    def locator(self, sel):
        loc = MagicMock()
        loc.first.click.side_effect = lambda **k: self.clicked.append(sel)
        loc.first.dispatch_event.side_effect = \
            lambda *a: self.clicked.append(sel)
        loc.count.return_value = 1
        return loc

    def evaluate(self, js):
        if "__noodleMo" in js:
            return True
        self.n += 1
        return {"controls": [_rawnew(self.n)], "headings": []}

    def wait_for_function(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def title(self):
        return "t"

    def goto(self, *a, **k):
        raise AssertionError("discovery must not navigate on a same-page reveal")


def _rawnew(n):
    return _raw(tag="button", type="", id=f"new{n}", text=f"New {n}")


def test_discover_clicks_records_and_reverts():
    page = _DiscoverPage()
    pg = {"controls": [_ctrl(name="trigger dev panel", selector="#t",
                             visible=False),
                       _ctrl(name="advanced options", selector="#a")],
          "headings": [], "pom_yaml": "", "next_pages": []}
    probe._discover(page, pg, 1000)
    assert page.clicked == ["#t", "#a"]
    assert page.escapes == ["Escape", "Escape"]      # reverted between branches
    revealed = pg["revealed"]
    assert [r["revealed_by"] for r in revealed] == \
        ["trigger dev panel", "advanced options"]
    assert all(r["discovered"] is True for r in revealed)
    trace = pg["discovery"]
    assert len(trace["clicked"]) == 2 and not trace["capped"]
    assert all(c["new_controls"] == 1 for c in trace["clicked"])


def test_discover_click_cap_is_honest_in_the_trace():
    page = _DiscoverPage()
    pg = {"controls": [_ctrl(name=f"panel {i}", selector=f"#p{i}",
                             visible=False) for i in range(12)],
          "headings": [], "pom_yaml": "", "next_pages": []}
    probe._discover(page, pg, 1000)
    trace = pg["discovery"]
    assert len(trace["clicked"]) == probe._DISCOVER_CLICK_CAP
    assert trace["capped"] is True
    assert sum(1 for s in trace["skipped"]
               if s["reason"] == "click/time budget exhausted") == 4


def test_discover_failure_lands_in_trace_not_raise():
    page = _DiscoverPage()
    page.locator = MagicMock(side_effect=RuntimeError("gone"))
    pg = {"controls": [_ctrl(name="settings panel", visible=False)],
          "headings": [], "pom_yaml": "", "next_pages": []}
    probe._discover(page, pg, 1000)
    assert pg["discovery"]["clicked"] == []
    assert any("gone" in s["reason"] for s in pg["discovery"]["skipped"])


# --- §5 virtualized listbox ---------------------------------------------------

class _VirtualPage:
    """Options come in windows; each scroll reveals the next window and
    (virtualization) drops the previous one from the DOM."""

    def __init__(self, windows):
        self.windows, self.i = windows, 0

    def evaluate(self, js):
        if js is probe._SCROLL_LISTBOX_JS:
            if self.i + 1 < len(self.windows):
                self.i += 1
                return True
            return False
        return self.windows[self.i]


def test_scroll_options_accumulates_across_virtual_windows():
    page = _VirtualPage([["A", "B"], ["C", "D"], ["E"]])
    opts = probe._scroll_options(page, set(), ["A", "B"])
    assert opts == ["A", "B", "C", "D", "E"]


def test_scroll_options_stops_when_values_stabilize():
    page = _VirtualPage([["A"], ["A"], ["Z"]])   # second window adds nothing
    assert probe._scroll_options(page, set(), ["A"]) == ["A"]


def test_scroll_options_respects_the_40_cap():
    windows = [[f"o{i}" for i in range(30)], [f"o{i}" for i in range(30, 60)]]
    page = _VirtualPage(windows)
    opts = probe._scroll_options(page, set(), list(windows[0]))
    assert len(opts) >= 40 and len(opts) <= 60      # stops scrolling at cap


# --- §6 honesty: closed shadow, canvas, Flutter -------------------------------

def test_closed_shadow_suspects_become_warnings():
    pg = probe.summarize({"controls": [_raw(id="k", label="known")],
                          "headings": []})
    probe._apply_page_signals(pg, {"closed_shadow": ["locked-widget"]})
    assert any("closed shadow root" in w and "locked-widget" in w
               for w in pg["warnings"])
    assert pg["coverage"] == "dom"                  # a suspect doesn't block


def test_canvas_only_page_is_visual_only_and_suppresses_pom():
    pg = probe.summarize({"controls": [_raw(tag="a", href="/x", text="")],
                          "headings": []})
    pg["pom_yaml"] = "would-be-fabricated: {}"
    probe._apply_page_signals(pg, {"canvas_ratio": 0.9})
    assert pg["coverage"] == "visual_only"
    assert pg["pom_yaml"] == ""
    assert probe._author_ready(pg) is False


def test_dom_rich_page_with_incidental_canvas_stays_dom():
    pg = probe.summarize({"controls": [
        _raw(id=f"c{i}", label=f"ctl {i}") for i in range(5)], "headings": []})
    probe._apply_page_signals(pg, {"canvas_ratio": 0.9})
    assert pg["coverage"] == "dom"


def test_flutter_marker_becomes_framework_hint():
    pg = probe.summarize({"controls": [], "headings": []})
    probe._apply_page_signals(pg, {"flutter": True, "canvas_ratio": 0.95})
    assert pg["framework_hints"] == ["flutter-web"]
    assert pg["coverage"] == "visual_only"


def test_author_ready_false_on_proven_ambiguous_pom_selector():
    pg = probe.summarize({"controls": [_raw(tag="input")], "headings": []})
    probe._apply_page_signals(pg, {})
    assert probe._author_ready(pg) is True          # unverified ≠ ambiguous
    pg["controls"][0]["unique"] = False
    assert probe._author_ready(pg) is False


# --- §7 native probe ----------------------------------------------------------

_ANDROID_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <android.widget.FrameLayout displayed="true">
    <android.widget.EditText resource-id="com.pos:id/user" text=""
      content-desc="user name" clickable="true" enabled="true" displayed="true"/>
    <android.widget.Button resource-id="com.pos:id/go" text="Login"
      content-desc="" clickable="true" enabled="true" displayed="true"/>
    <android.widget.Spinner resource-id="com.pos:id/dev" content-desc="device type"
      clickable="true" enabled="true" displayed="true"/>
    <android.widget.CheckBox text="Remember me" clickable="true"
      enabled="false" displayed="true"/>
    <android.widget.ImageButton clickable="true" enabled="true" displayed="false"/>
  </android.widget.FrameLayout>
</hierarchy>"""

_IOS_XML = """<?xml version='1.0' encoding='UTF-8'?>
<AppiumAUT>
  <XCUIElementTypeApplication name="POS">
    <XCUIElementTypeTextField name="user" label="User name" visible="true"
      enabled="true"/>
    <XCUIElementTypeSecureTextField name="pass" label="Password" visible="true"
      enabled="true"/>
    <XCUIElementTypeButton name="login" label="Login" visible="true"
      enabled="true"/>
    <XCUIElementTypeSwitch name="remember" label="Remember" visible="true"
      enabled="true"/>
  </XCUIElementTypeApplication>
</AppiumAUT>"""

_WINDOWS_XML = """<?xml version='1.0' encoding='utf-8'?>
<Window Name="Calculator">
  <Button AutomationId="num1Button" Name="One"/>
  <Button AutomationId="plusButton" Name="Plus"/>
  <Edit AutomationId="display" Name="Display is 0"/>
  <ComboBox AutomationId="modePicker" Name="Mode"/>
</Window>"""

_MAC_XML = """<?xml version='1.0' encoding='UTF-8'?>
<AppiumAUT>
  <XCUIElementTypeApplication title="Notes">
    <XCUIElementTypeButton title="New Note" enabled="true"/>
    <XCUIElementTypeTextView title="Body" enabled="true"/>
  </XCUIElementTypeApplication>
</AppiumAUT>"""


def test_android_tree_normalizes_kinds_names_and_strategies():
    out = mprobe.summarize_source(_ANDROID_XML)
    by_name = {c["name"]: c for c in out["controls"]}
    user = by_name["user name"]
    assert user["kind"] == "field"
    assert user["selector"] == {"accessibility_id": "user name"}
    assert user["step"] == 'enters "<value>" in the "user name" field'
    login = by_name["login"]
    assert login["kind"] == "button"
    assert login["selector"] == {"id": "com.pos:id/go"}   # no content-desc
    assert by_name["device type"]["kind"] == "dropdown"
    remember = by_name["remember me"]
    assert remember["kind"] == "toggle" and remember["enabled"] is False
    assert out["coverage"] == "dom" and out["author_ready"] is True


def test_android_nameless_node_needs_pom_with_paste_ready_entry():
    out = mprobe.summarize_source(_ANDROID_XML)
    anon = [c for c in out["controls"] if c["needs_pom"]]
    assert len(anon) == 1 and anon[0]["visible"] is False
    assert anon[0]["step"] is None                 # no fabricated name
    assert anon[0]["pom"]                          # but a paste-ready entry


def test_ios_windows_mac_trees_normalize():
    ios = {c["name"]: c for c in mprobe.summarize_source(_IOS_XML)["controls"]}
    assert ios["user name"]["kind"] == "field"
    assert ios["user name"]["selector"] == {"accessibility_id": "user"}
    assert ios["remember"]["kind"] == "toggle"
    win = {c["name"]: c
           for c in mprobe.summarize_source(_WINDOWS_XML)["controls"]}
    assert win["one"]["kind"] == "button"
    assert win["one"]["selector"] == {"accessibility_id": "One"}
    assert win["mode"]["kind"] == "dropdown"
    mac = {c["name"]: c for c in mprobe.summarize_source(_MAC_XML)["controls"]}
    assert mac["new note"]["kind"] == "button"
    assert mac["body"]["kind"] == "field"


def test_native_steps_all_match_the_pattern_table():
    for xml in (_ANDROID_XML, _IOS_XML, _WINDOWS_XML, _MAC_XML):
        for c in mprobe.summarize_source(xml)["controls"]:
            if c["step"]:
                step = c["step"].replace("<value>", "x")
                assert pattern_match(normalize_phrasing(step)), c["step"]


def test_empty_or_generic_tree_is_visual_only_with_ocr_pointer():
    out = mprobe.summarize_source(
        "<hierarchy><android.view.View displayed='true'/></hierarchy>")
    assert out["coverage"] == "visual_only"
    assert out["author_ready"] is False
    assert any("@ocr_fallback" in w for w in out["warnings"])


def test_unparseable_source_fails_honestly():
    out = mprobe.summarize_source("this is not xml")
    assert out["coverage"] == "visual_only" and out["author_ready"] is False
    assert any("not parseable" in w for w in out["warnings"])


def test_probe_app_is_snapshot_only_and_always_quits():
    drv = MagicMock()
    drv.page_source = _ANDROID_XML
    import noodle.agents.mobile.driver as mdriver
    orig_start, orig_stop = mdriver.start_session, mdriver.stop_session
    stopped = []
    mdriver.start_session = lambda p=None: drv
    mdriver.stop_session = lambda d: stopped.append(d)
    try:
        out = mprobe.probe_app("android")
    finally:
        mdriver.start_session, mdriver.stop_session = orig_start, orig_stop
    assert stopped == [drv]
    assert out["platform"] == "android" and out["coverage"] == "dom"
    # snapshot-only: page_source is the ONLY driver interaction
    assert not drv.find_element.called and not drv.tap.called


def test_probe_app_session_failure_is_advisory():
    import noodle.agents.mobile.driver as mdriver
    orig = mdriver.start_session

    def boom(p=None):
        raise RuntimeError("no appium server")

    mdriver.start_session = boom
    try:
        out = mprobe.probe_app("ios")
    finally:
        mdriver.start_session = orig
    assert "no appium server" in out["error"]
    assert out["author_ready"] is False


def test_cli_has_probe_app_command():
    from typer.testing import CliRunner
    r = CliRunner().invoke(cli.app, ["probe-app", "--help"])
    assert r.exit_code == 0
    assert "snapshot" in r.output.lower()


# --- §8 payload contract ------------------------------------------------------

def test_compact_payload_keeps_the_honesty_keys():
    pg = probe.summarize({"controls": [_raw(id="k", label="known")],
                          "headings": []})
    pg["warnings"] = ["closed shadow root suspected at <x-y>"]
    pg["coverage"] = "visual_only"
    pg["author_ready"] = False                     # the load-bearing False
    pg["frames"] = [dict(probe.summarize({"controls": [], "headings": ["F"]}),
                         frame="f", switch_step='switches to the "f" frame')]
    out = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert out["coverage"] == "visual_only"
    assert out["author_ready"] is False            # False must SURVIVE compact
    assert out["warnings"] == pg["warnings"]
    assert out["frames"][0]["frame"] == "f"
    assert out["frames"][0]["switch_step"] == 'switches to the "f" frame'


def test_auto_open_failure_names_control_and_phase():
    page = MagicMock()
    page.url = "http://x"
    page.evaluate.side_effect = RuntimeError("ctx destroyed")
    loc = MagicMock()
    loc.first.count.return_value = 1
    loc.first.evaluate.return_value = None         # not a native <select>
    page.locator.return_value = loc
    blk = {"controls": [{"kind": "dropdown", "name": "device dropdown",
                         "selector": "#dd"}], "headings": []}
    probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert any('open_native "device dropdown" failed at open' in w
               for w in blk["warnings"])


def test_render_surfaces_the_honesty_headers():
    pg = probe.summarize({"controls": [_raw(id="k", label="known")],
                          "headings": []})
    pg["coverage"] = "visual_only"
    pg["author_ready"] = False
    pg["framework_hints"] = ["flutter-web"]
    text = probe.render({"pages": [pg], "errors": []})
    assert "visual_only" in text
    assert "author_ready: false" in text
    assert "flutter-web" in text


def test_render_marks_proven_ambiguous_selectors():
    pg = probe.summarize({"controls": [_raw(id="k", label="known")],
                          "headings": []})
    pg["controls"][0]["unique"] = False
    pg["controls"][0]["matches"] = 4
    text = probe.render({"pages": [pg], "errors": []})
    assert "matches 4 nodes" in text


def test_render_frames_block_carries_the_switch_step():
    pg = probe.summarize({"controls": [], "headings": []})
    fb = probe.summarize({"controls": [_raw(id="c", label="card number")],
                          "headings": []})
    fb["frame"], fb["switch_step"] = "pay", 'switches to the "pay" frame'
    pg["frames"] = [fb]
    text = probe.render({"pages": [pg], "errors": []})
    assert 'iframe "pay"' in text
    assert 'switches to the "pay" frame' in text

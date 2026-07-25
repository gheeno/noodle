"""NOOD_0178 — the probe follows new tabs (open → probe → switch back).

`probe()` drove a single page object, so a reveal/do click that opened a tab
(target=_blank, window.open) left the probe evaluating the ORIGINAL page: the
new tab's controls were never collected and the click was reported with the
false "no observable delta" note. The runtime already switches tabs
(runner._switch_tab); these tests pin the probe learning the same awareness,
and emitting the runtime's EXACT step vocabulary.

  §1  detection by page-list GROWTH — never wait_for_event("popup") after the
      click (NOOD_0177: the event already fired) — plus every edge case as a
      warning, never a raise
  §2  the `switch to <target> tab` do-verb: grammar round trip, bad targets
      rejected before any browser launches, bounded
  §3  the switch steps are verbatim runtime vocabulary (pattern table)
  §4  the transaction continues ACROSS tabs: active page follows the click,
      switches back on request, and the false no-delta note is gone
  §5  payload — the new keys ride inside existing blocks, so the 8 KB budget
      trims them like anything else
  §6  render + skeleton — labelled like a frame block, switch steps first, and
      woven into the paste-ready scenario in runtime order
  §7  CLI + MCP + REPL + docs parity

Pure Python: fake context/pages. No browser (the live target=_blank +
window.open smoke ran separately, per the ticket's acceptance).
"""
from unittest.mock import MagicMock

import pytest

from noodle import cli
from noodle.agents.web import probe as probe_mod
from noodle.mcp import server
from noodle.repl import core
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing
from unit_tests.test_nood_0110 import REPO


def _raw(**over):
    c = {"tag": "input", "id": "", "role": "", "type": "text", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True}
    c.update(over)
    return c


class _Ctx:
    """A browser context: just the page list the probe watches for growth."""

    def __init__(self):
        self.pages = []


class _Page:
    """The slice of the Playwright page surface the tab paths touch. `opens`
    is called on the first click and spawns a tab in the same context."""

    def __init__(self, ctx, url, raws=(), title="t", opens=None):
        self.context, self.url, self.title_ = ctx, url, title
        self._raws, self.opens = list(raws), opens
        self.closed, self.clicked = False, []
        ctx.pages.append(self)

    def evaluate(self, js, *a, **k):
        if "__noodlePerm" in js:
            return {}
        if "__noodleMo" in js or "__noodleMut" in js:
            return True
        return self._raws.pop(0)

    def title(self):
        return self.title_

    def is_closed(self):
        return self.closed

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_function(self, *a, **k):
        pass

    def locator(self, sel):
        loc = MagicMock()
        loc.count.return_value = 1

        def _click(*a, **k):
            self.clicked.append(sel)
            if self.opens:
                self.opens()
                self.opens = None
        loc.first.click.side_effect = _click
        loc.first.dispatch_event.side_effect = _click
        return loc


@pytest.fixture(autouse=True)
def _no_popup_sweep(monkeypatch):
    """The popup sweep is a real Playwright walk — out of scope here."""
    monkeypatch.setattr(probe_mod, "_dismiss_popups", lambda page: 0)


# --- §1 detection by page-list growth ----------------------------------------

def test_new_tab_is_detected_by_page_list_growth():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    before = probe_mod._pages(home)
    tab = _Page(ctx, "https://app/preview")
    warn = []
    assert probe_mod._new_tab(home, before, warn) is tab
    assert warn == []


def test_no_growth_means_no_tab():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    warn = []
    assert probe_mod._new_tab(home, probe_mod._pages(home), warn) is None
    assert warn == []


def test_several_tabs_at_once_probes_the_newest_and_says_so():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    before = probe_mod._pages(home)
    _Page(ctx, "https://app/a")
    newest = _Page(ctx, "https://app/b")
    warn = []
    assert probe_mod._new_tab(home, before, warn) is newest
    assert any("2 new tabs" in w and "newest" in w for w in warn)


def test_a_tab_that_closes_immediately_is_a_warning_not_a_raise():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    before = probe_mod._pages(home)
    tab = _Page(ctx, "https://app/dl")
    tab.closed = True
    warn = []
    assert probe_mod._new_tab(home, before, warn) is None
    assert any("closed immediately" in w for w in warn)


def test_a_tab_stuck_on_about_blank_is_a_warning():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    before = probe_mod._pages(home)
    _Page(ctx, "about:blank")
    warn = []
    assert probe_mod._new_tab(home, before, warn) is None
    assert any("about:blank" in w for w in warn)


def test_an_unreadable_tab_is_a_warning():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    before = probe_mod._pages(home)
    tab = _Page(ctx, "https://app/x")
    tab.is_closed = MagicMock(side_effect=RuntimeError("target crashed"))
    warn = []
    assert probe_mod._new_tab(home, before, warn) is None
    assert any("target crashed" in w for w in warn)


def test_pages_is_advisory_when_the_context_is_gone():
    assert probe_mod._pages(object()) == []      # no .context — never raises


def test_perm_shim_is_installed_on_the_context_not_the_page():
    """A page-level init script never reaches a tab that page opens."""
    src = REPO / "noodle/agents/web/probe.py"
    body = src.read_text()
    assert "page.context.add_init_script(_PERM_JS)" in body
    assert "page.add_init_script(_PERM_JS)" not in body


# --- §2 the switch-to-tab do verb --------------------------------------------

def test_parse_do_round_trips_every_tab_target():
    assert probe_mod.parse_do(
        ["switch to new tab", "switch to the original tab",
         "SWITCH TO Last Tab", "switch to previous tab",
         "switch to first tab", "switch to the main tab"]) == [
        ("switch", "new", None), ("switch", "original", None),
        ("switch", "last", None), ("switch", "previous", None),
        ("switch", "first", None), ("switch", "main", None)]


def test_parse_do_rejects_a_bad_tab_target_and_points_at_click():
    with pytest.raises(ValueError, match="bogus"):
        probe_mod.parse_do(["switch to bogus tab"])
    # A tab WITHIN the page is a click — the message must say so, or the
    # caller retries the same unparseable action.
    with pytest.raises(ValueError, match='"click <name>"'):
        probe_mod.parse_do(["switch to the settings tab"])


def test_the_other_three_verbs_are_unchanged():
    assert probe_mod.parse_do(
        ["click switch to dark mode tab", "enter 1 in zip",
         "select East from region"]) == [
        ("click", "switch to dark mode tab", None),
        ("enter", "zip", "1"), ("select", "region", "East")]


def test_tab_grammar_stays_bounded():
    # NOOD_0177 — parse_do runs BEFORE any browser launches; the tab
    # alternative must not add a backtracking surface.
    with pytest.raises(ValueError):
        probe_mod.parse_do(["switch to " + "a " * 2000 + "tab"])


def test_a_bad_tab_action_never_launches_a_browser():
    r = probe_mod.probe(["http://x"], do=["switch to bogus tab"])
    assert r["pages"] == [] and "bogus" in r["errors"][0]["error"]


# --- §3 the step strings are runtime vocabulary ------------------------------

def _resolves(step: str):
    for prefix in ("Given ", "When ", "Then ", "And ", "the user ", "User "):
        if step.startswith(prefix):
            step = step[len(prefix):]
    return pattern_match(normalize_phrasing(step))


def test_switch_steps_are_verbatim_and_resolve_against_the_pattern_table():
    assert list(probe_mod._TAB_SWITCH_STEPS) == [
        "Then a new tab should open", "When User switches to the new tab"]
    assert probe_mod._TAB_RETURN_STEP == "When User switches to the original tab"
    assert _resolves(probe_mod._TAB_SWITCH_STEPS[0])[0] == "assert_new_tab"
    assert _resolves(probe_mod._TAB_SWITCH_STEPS[1]) == \
        ("switch_tab", {"target": "new"})
    assert _resolves(probe_mod._TAB_RETURN_STEP) == \
        ("switch_tab", {"target": "original"})


def test_probe_and_runner_agree_on_the_tab_targets():
    from noodle.orchestrator import runner
    for target in (*probe_mod._TAB_NEW, *probe_mod._TAB_HOME):
        assert _resolves(f"When User switches to the {target} tab") == \
            ("switch_tab", {"target": target})
    assert runner._switch_tab is not None      # the runtime end of the contract


# --- §4 the transaction continues across tabs --------------------------------

def _pg(controls, url="https://app/"):
    return {"url": url, "title": "t", "controls": controls, "pom_yaml": "",
            "headings": [], "next_pages": []}


_OPENER = {"kind": "button", "name": "preview", "selector": "a#preview",
           "visible": True, "needs_pom": False, "step": "s"}

_TAB_RAW = {"controls": [_raw(label="download pdf", tag="button", type="")],
            "headings": ["Invoice preview"]}


def _home_and_tab(home_raws, tab_raws=(_TAB_RAW,),
                  tab_url="https://app/preview"):
    """A home page whose first click opens a tab carrying `tab_raws`."""
    ctx = _Ctx()
    box = {}
    home = _Page(ctx, "https://app/", home_raws)
    home.opens = lambda: box.setdefault(
        "tab", _Page(ctx, tab_url, tab_raws, title="Preview"))
    return home, box


def test_reveal_follows_the_tab_and_collects_it_as_its_own_block():
    home, box = _home_and_tab([{"controls": [], "headings": []}])
    pg = _pg([_OPENER])
    active = probe_mod._reveal(home, pg, ["preview"], timeout_ms=1000)
    assert active is box["tab"]                      # flow moved to the tab
    blk = pg["revealed"][-1]
    assert blk["new_tab"] and blk["tab_url"] == "https://app/preview"
    assert blk["opened_by"] == "preview"
    assert blk["switch_steps"] == list(probe_mod._TAB_SWITCH_STEPS)
    assert {c["name"] for c in blk["controls"]} == {"download pdf"}
    assert blk["headings"] == ["Invoice preview"]
    # the tab's selectors are proven in the TAB's scope, not the opener's
    assert all(c.get("unique") for c in blk["controls"])
    # an empty opener-page delta is noise once the reveal happened over there
    assert len(pg["revealed"]) == 1


def test_reveal_keeps_the_opener_delta_when_the_opener_also_changed():
    home, _ = _home_and_tab(
        [{"controls": [_raw(label="close preview", tag="button", type="")],
          "headings": []}])
    pg = _pg([_OPENER])
    probe_mod._reveal(home, pg, ["preview"], timeout_ms=1000)
    assert [b.get("new_tab", False) for b in pg["revealed"]] == [False, True]


def test_do_click_that_opens_a_tab_drops_the_false_no_delta_note():
    home, box = _home_and_tab([{"controls": [], "headings": []}])
    pg = _pg([_OPENER])
    active = probe_mod._do(home, pg, probe_mod.parse_do(["click preview"]),
                           timeout_ms=1000)
    assert active is box["tab"]
    blocks = pg["revealed"]
    assert [b["revealed_by"] for b in blocks] == ["do: click preview"]
    assert blocks[0]["new_tab"] and "note" not in blocks[0]
    assert pg["do_completed"] == 1


def test_a_click_with_no_tab_and_no_delta_still_gets_the_note():
    """The NOOD_0156 signal must survive — only the tab case suppresses it."""
    ctx = _Ctx()
    home = _Page(ctx, "https://app/", [{"controls": [], "headings": []}])
    pg = _pg([_OPENER])
    probe_mod._do(home, pg, probe_mod.parse_do(["click preview"]),
                  timeout_ms=1000)
    assert "no observable delta" in pg["revealed"][0]["note"]


def test_do_actions_after_the_tab_opens_act_on_the_tab():
    home, box = _home_and_tab([{"controls": [], "headings": []}],
                              tab_raws=(_TAB_RAW,
                                        {"controls": [], "headings": []}))
    pg = _pg([_OPENER])
    probe_mod._do(home, pg, probe_mod.parse_do(
        ["click preview", "click download pdf"]), timeout_ms=1000)
    tab = box["tab"]
    # the second click resolved against the TAB's control and fired there
    assert tab.clicked and not [c for c in home.clicked if "download" in c]
    assert pg["do_completed"] == 2


def test_switch_back_repoints_the_active_page_and_diffs_it():
    home, box = _home_and_tab(
        [{"controls": [], "headings": []},                  # the opening click
         {"controls": [_raw(label="cart (1)", tag="button", type="")],
          "headings": []}])                                 # seen on return
    pg = _pg([_OPENER])
    active = probe_mod._do(home, pg, probe_mod.parse_do(
        ["click preview", "switch to original tab"]), timeout_ms=1000)
    assert active is home and box["tab"] is not home
    assert pg["tab_switches"] == ["original"]
    back = pg["revealed"][-1]
    assert back["revealed_by"] == "do: switch to original tab"
    assert back["switched_to"] == "original"
    assert {c["name"] for c in back["controls"]} == {"cart (1)"}
    assert pg["do_completed"] == 2


def _tab_of(home):
    """The page _reveal left the flow on."""
    return [p for p in probe_mod._pages(home) if p is not home][-1]


def test_switching_to_a_page_probed_by_reveal_does_not_re_report_it():
    """--click opens the tab, then --do switches back: the home page's initial
    snapshot must not resurface as a switch-back 'delta'."""
    home, _ = _home_and_tab([{"controls": [], "headings": []},
                             {"controls": [_raw(label="cart (1)", tag="button",
                                                type="")], "headings": []}])
    pg = probe_mod.summarize(
        {"controls": [_raw(tag="a", text="preview", href="/p")],
         "headings": ["Invoices"]}, url="https://app/", title="t")
    probe_mod._reveal(home, pg, ["preview"], timeout_ms=1000)
    probe_mod._do(_tab_of(home), pg,
                  probe_mod.parse_do(["switch to original tab"]),
                  timeout_ms=1000)
    back = pg["revealed"][-1]
    assert back["switched_to"] == "original"
    assert {c["name"] for c in back["controls"]} == {"cart (1)"}


def test_switch_to_new_tab_target_picks_the_newest_page():
    home, box = _home_and_tab(
        [{"controls": [], "headings": []}, {"controls": [], "headings": []}])
    pg = _pg([_OPENER])
    active = probe_mod._do(home, pg, probe_mod.parse_do(
        ["click preview", "switch to original tab", "switch to new tab"]),
        timeout_ms=1000)
    assert active is box["tab"]
    assert pg["tab_switches"] == ["original", "new"]


def test_switch_with_one_tab_open_warns_and_blocks_authoring():
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    pg = _pg([_OPENER])
    active = probe_mod._do(home, pg, probe_mod.parse_do(
        ["switch to new tab"]), timeout_ms=1000)
    assert active is home
    assert any("only one tab is open" in w for w in pg["do_warnings"])
    # a requested state the probe never reached must not read as author-ready
    assert probe_mod._author_ready(pg) is False


def test_a_failed_action_halts_and_still_returns_a_page():
    """_do returns a page on every exit path — probe() rebinds from it — and a
    skipped switch is labelled the way the caller wrote it."""
    ctx = _Ctx()
    home = _Page(ctx, "https://app/")
    home.locator = MagicMock(side_effect=RuntimeError("detached"))
    pg = _pg([_OPENER])
    actions = probe_mod.parse_do(["click preview", "switch to original tab"])
    assert probe_mod._do(home, pg, actions, timeout_ms=1000) is home
    assert pg["do_failed"]["skipped"] == ["do: switch to original tab"]


# --- §5 payload budget -------------------------------------------------------

def _two_tab_result():
    """A representative result: 40 controls on the home page, a followed tab
    with 40 of its own, and a return delta."""
    def _controls(prefix, n):
        return [_raw(tag="button", type="", id=f"{prefix}{i}",
                     cls=f"{prefix}-btn-{i}") for i in range(n)]
    pg = probe_mod.summarize({"controls": _controls("home", 40),
                              "headings": ["Invoices"]}, url="https://app/",
                             title="Invoices")
    tab = probe_mod.summarize({"controls": _controls("tab", 40),
                               "headings": ["Invoice preview"]},
                              url="https://app/preview", title="Preview")
    tab["new_tab"], tab["tab_url"] = True, "https://app/preview"
    tab["opened_by"] = tab["revealed_by"] = "do: click preview"
    tab["switch_steps"] = list(probe_mod._TAB_SWITCH_STEPS)
    back = probe_mod.summarize({"controls": _controls("back", 5),
                                "headings": []}, url="https://app/")
    back["revealed_by"] = "do: switch to original tab"
    back["switched_to"] = "original"
    pg["revealed"] = [tab, back]
    pg["tab_switches"] = ["original"]
    pg["author_ready"] = probe_mod._author_ready(pg)
    return {"pages": [pg], "errors": []}


def test_a_two_tab_payload_stays_inside_the_budget():
    import json
    out = probe_mod.compact_payload(_two_tab_result())
    assert len(json.dumps(out, default=str)) <= \
        probe_mod.COMPACT_BUDGET_BYTES


def test_the_tab_contract_survives_the_compact_diet():
    out = probe_mod.compact_payload(_two_tab_result())
    tab = out["pages"][0]["revealed"][0]
    assert tab["new_tab"] and tab["tab_url"] == "https://app/preview"
    assert tab["opened_by"] == "do: click preview"
    assert tab["switch_steps"] == list(probe_mod._TAB_SWITCH_STEPS)
    assert out["pages"][0]["revealed"][1]["switched_to"] == "original"
    assert out["pages"][0]["tab_switches"] == ["original"]


# --- §6 render + skeleton ----------------------------------------------------

def test_render_labels_the_tab_block_and_leads_with_the_switch_steps():
    out = probe_mod.render(_two_tab_result(), compact=True)
    lines = out.splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("  new tab:"))
    assert "https://app/preview" in lines[head]
    assert 'opened by: "do: click preview"' in lines[head]
    assert lines[head + 1].strip() == "Then a new tab should open"
    assert lines[head + 2].strip() == "When User switches to the new tab"


def test_render_labels_the_switch_back_delta_as_such():
    assert "seen after switching back to the original tab" in \
        probe_mod.render(_two_tab_result(), compact=True)


def test_render_shows_a_switch_that_produced_no_delta():
    res = _two_tab_result()
    res["pages"][0]["revealed"] = res["pages"][0]["revealed"][:1]
    assert "tab switches performed: original" in probe_mod.render(res)


def test_skeleton_weaves_the_tab_steps_in_runtime_order():
    pg = _two_tab_result()["pages"][0]
    steps = probe_mod._skeleton_steps(pg)
    assert steps[:6] == [
        'Given User is on "{env:<APP>}"',
        'When User clicks "preview"',
        "Then a new tab should open",
        "When User switches to the new tab",
        'Then the user sees "Invoice preview"',
        "When User switches to the original tab"]


def test_the_return_step_appears_only_when_the_probe_returned():
    pg = _two_tab_result()["pages"][0]
    pg.pop("tab_switches")
    assert probe_mod._TAB_RETURN_STEP not in probe_mod._skeleton_steps(pg)


def test_every_woven_tab_step_matches_the_pattern_table():
    for step in probe_mod._skeleton_steps(_two_tab_result()["pages"][0]):
        step = step.replace("{env:<APP>}", "https://app/")
        assert _resolves(step) is not None, step


def test_no_tab_no_tab_steps():
    pg = probe_mod.summarize({"controls": [], "headings": []}, url="u")
    assert probe_mod._tab_steps(pg) == []


# --- §7 CLI + MCP + REPL + docs parity ---------------------------------------

def _do_help() -> str:
    import inspect
    src = inspect.getsource(cli.probe)
    return src[src.index('"--do"'):src.index('"--do"') + 400].lower()


def test_the_new_verb_is_documented_on_every_probe_surface():
    surfaces = {
        "cli --do help": _do_help(),
        "mcp probe_page": (server.probe_page.__doc__ or "").lower(),
        "repl probe_page": (core.probe_page.__doc__ or "").lower(),
        "agent-playbook": (REPO / "docs/agent-playbook.md").read_text().lower(),
    }
    for name, text in surfaces.items():
        assert "tab" in text, name
        assert "switch to" in text, f"{name}: the do-verb is not shown"


def test_the_instruction_budget_still_holds():
    from noodle import instruction_budget
    for row in instruction_budget.ledger():
        if row["used"] is not None:
            assert row["headroom"] >= 0, \
                f'{row["surface"]}\n{instruction_budget.format_ledger()}'

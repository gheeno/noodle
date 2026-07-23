"""NOOD_0116 — probe --click: see past the first click.

Observed on an SPA whose settings panel (account-id field, region dropdown,
save button) hides behind a `div.trigger-settings-panel` click — invisible
to a single-load probe, so real selectors cost ad-hoc Playwright heredoc
scripts and their raw-HTML dumps. Fix: `--click` names a reveal control;
probe clicks it, settles, and appends the diff (controls/headings not
present before) as summarize()-shaped "revealed" states. Pure Python — fake
page, no browser, no LLM (test_nood_0113 conventions)."""
from unittest.mock import MagicMock

from noodle.agents.web import probe as probe_mod


def _raw_control(**over):
    """A control dict shaped like _COLLECT_JS emits."""
    c = {"tag": "input", "id": "", "role": "", "type": "text", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True}
    c.update(over)
    return c


def _initial_pg():
    """Summarize()-shaped initial snapshot holding just the hidden trigger."""
    return {"url": "https://app.example/login", "title": "Sign In",
            "controls": [{"kind": "button", "name": "trigger settings panel",
                          "selector": 'div[class~="trigger-settings-panel"]',
                          "visible": False,
                          "needs_pom": True,
                          "step": 'clicks "trigger settings panel"'}],
            "pom_yaml": "", "headings": [], "next_pages": []}


def _page(raws):
    """Fake Playwright page: records call order, feeds one raw dict per
    post-click evaluate."""
    order = []
    page = MagicMock()
    loc = MagicMock()
    loc.first.click.side_effect = lambda **k: order.append("click")
    # NOOD_0135 — known-hidden triggers dispatch directly; both count as "click"
    loc.first.dispatch_event.side_effect = lambda *a, **k: order.append("click")
    page.locator.return_value = loc

    def _evaluate(js, *a, **k):
        if "__noodleMo" in js:               # NOOD_0136 settle observer —
            return True                      # not a snapshot, keep order clean
        order.append("evaluate")
        return raws.pop(0)

    page.evaluate.side_effect = _evaluate
    page.url = "https://app.example/login"
    page.title.return_value = "Sign In"
    return page, order


def test_probe_clicks_targets_before_snapshot():
    raw = {"controls": [_raw_control(name="accountId", label="account id")],
           "headings": []}
    page, order = _page([raw])
    pg = _initial_pg()
    probe_mod._reveal(page, pg, ["trigger settings panel"], timeout_ms=1000)
    assert order == ["click", "evaluate"]
    # the trigger's name resolved to its probed selector, not a text guess
    # (NOOD_0136: later locator calls are uniqueness verification, not clicks)
    assert page.locator.call_args_list[0].args[0] == \
        'div[class~="trigger-settings-panel"]'


def test_probe_multiple_click_targets_produce_ordered_revealed_states():
    raw1 = {"controls": [_raw_control(name="accountId")],
            "headings": ["Settings"]}
    raw2 = {"controls": [_raw_control(tag="button", type="",
                                      cls="e2e_settings-panel_save_button",
                                      text="Save Settings")],
            "headings": []}
    page, _ = _page([raw1, raw2])
    pg = _initial_pg()
    probe_mod._reveal(page, pg, ["trigger settings panel", "account id"],
                      timeout_ms=1000)
    revealed = pg["revealed"]
    assert [r["revealed_by"] for r in revealed] == ["trigger settings panel",
                                                    "account id"]
    # each state is summarize()-shaped
    for r in revealed:
        assert {"controls", "headings", "pom_yaml", "next_pages"} <= set(r)
    assert revealed[0]["controls"][0]["kind"] == "field"
    assert revealed[0]["headings"] == ["Settings"]
    # second click resolved against a control revealed by the first
    assert any("accountId" in c.args[0]
               for c in page.locator.call_args_list)


def test_probe_click_target_not_found_is_advisory_not_fatal():
    page = MagicMock()
    bad = MagicMock()
    bad.first.click.side_effect = RuntimeError("strict mode violation")
    bad.first.dispatch_event.side_effect = RuntimeError("no element")
    page.locator.return_value = bad
    pg = _initial_pg()
    before = list(pg["controls"])
    probe_mod._reveal(page, pg, ["no such control"], timeout_ms=1000)
    assert pg["controls"] == before          # initial snapshot intact
    assert "revealed" not in pg
    assert any("no such control" in w for w in pg["click_warnings"])


def test_probe_hidden_trigger_falls_back_to_dispatch_event():
    """A 0-size hidden hitbox has no click box — click() fails, the probe
    dispatches a synthetic click instead of giving up."""
    raw = {"controls": [], "headings": []}
    page = MagicMock()
    loc = MagicMock()
    loc.first.click.side_effect = RuntimeError("element is not visible")
    page.locator.return_value = loc
    page.evaluate.return_value = raw
    page.url, page.title = "u", MagicMock(return_value="t")
    pg = _initial_pg()
    probe_mod._reveal(page, pg, ["trigger settings panel"], timeout_ms=1000)
    loc.first.dispatch_event.assert_called_once_with("click")
    assert "click_warnings" not in pg


def test_probe_revealed_diffs_out_already_seen_controls():
    """The post-click snapshot re-collects the whole DOM — revealed states
    carry only what the click ADDED, or the token savings are gone."""
    trigger_raw = _raw_control(tag="div", type="", cls="trigger-settings-panel")
    raw = {"controls": [trigger_raw,
                        _raw_control(name="accountId", label="account id")],
           "headings": []}
    page, _ = _page([raw])
    pg = _initial_pg()
    probe_mod._reveal(page, pg, ["trigger settings panel"], timeout_ms=1000)
    names = [c["name"] for c in pg["revealed"][0]["controls"]]
    assert "trigger settings panel" not in names
    assert names == ["account id"]


def test_render_shows_revealed_controls_under_their_own_heading():
    pg = _initial_pg()
    pg["revealed"] = [{"revealed_by": "trigger settings panel",
                       "controls": [{"kind": "field", "name": "account id",
                                     "selector": "input[name=\"accountId\"]",
                                     "visible": True, "needs_pom": False,
                                     "step": 'enters "<value>" in the "account id" field'}],
                       "headings": ["Settings"], "next_pages": [],
                       "pom_yaml": ""}]
    pg["click_warnings"] = ['--click "bogus": no element']
    out = probe_mod.render({"pages": [pg], "errors": []})
    assert 'revealed after clicking "trigger settings panel"' in out
    assert "account id" in out and 'input[name="accountId"]' in out
    # revealed controls are labelled apart from the on-load dump
    assert out.index("controls (1;") < out.index("revealed after clicking")
    assert '⚠ --click "bogus"' in out


def test_probe_without_clicks_unchanged():
    """No --click → no revealed/click_warnings keys anywhere (byte-for-byte
    today's payload)."""
    pg = probe_mod.summarize(
        {"controls": [_raw_control(name="q", label="search")],
         "headings": ["Welcome"]},
        url="https://x.test", title="X")
    assert "revealed" not in pg and "click_warnings" not in pg
    out = probe_mod.render({"pages": [pg], "errors": []})
    assert "revealed" not in out

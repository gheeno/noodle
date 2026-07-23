"""NOOD_0145 — probe naming/transaction honesty, deterministic goal targets,
and the no-navigation failure verdict.

A reviewed login-flow session (four red runs before green) pinned these on
the engine, all covered browser-free:

  P0-1  An unlabeled editable input was named by its live VALUE (collected
        through `innerText || value`), called directly resolvable, and lost
        its POM entry — runtime locators resolve labels/roles/placeholders/
        visible text, never values, so the phrase could not resolve on any
        run (invisible to the NOOD_0144 machine-name fix: source read "text").
  P0-2  Failed `--do` actions disappeared from human and compact output, the
        transaction kept running against an invalid state, and `--expect`
        misses left author_ready true — three red runs authored off evidence
        from the wrong state.
  P0-3  The probe's transaction selected native-<select>-only while the
        runtime handles custom dropdowns — they now share one implementation.
  P1-1  Goal matching picked the first substring match for a generic "login"
        target — a machine-named lookalike — over the visible submit control;
        ambiguity now blocks instead of guessing.
  P1-2  A submit-like click that never navigated was classified as a broad
        app regression; it is now `wrong-action-target`.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions, probe
from noodle.repl import goal as goal_mod
from noodle.reporting import rca_report as rr
from unit_tests.test_nood_0117 import _c
from unit_tests.test_nood_0144 import _fake_page, _pg_with


def _entry(message="", trace="", warnings=None):
    return {"message": message, "trace": trace, "warnings": warnings or []}


def _page(controls, **extra):
    pg = probe.summarize({"controls": controls, "headings": []},
                         url="https://x/")
    pg.update(extra)
    return pg


# --- P0-1: editable values are never visible text -----------------------------

def test_editable_input_value_never_names_the_control():
    # The real-world shape: an editable input with empty label/aria/
    # placeholder/name/id, identified only by a class token, whose VALUE an
    # older collector leaked into `text`.
    raw = _c(tag="input", text="AB12", cls="settings-panel-code__input")
    pg = _page([raw])
    ctrl = pg["controls"][0]
    assert "ab12" not in ctrl["name"]          # value never becomes the name
    assert ctrl["name"] == "settings panel code input"
    assert ctrl["needs_pom"] is True
    assert ctrl["selector"] == 'input[class~="settings-panel-code__input"]'
    # and the POM entry ships ready to paste, with that exact selector
    assert ctrl["pom"][0] == "settings panel code input:"
    assert 'input[class~="settings-panel-code__input"]' in pg["pom_yaml"]


def test_textarea_value_never_names_the_control():
    raw = _c(tag="textarea", text="dear team, please find", cls="notes-box")
    ctrl = _page([raw])["controls"][0]
    assert ctrl["name"] == "notes box"
    assert ctrl["needs_pom"] is True


def test_button_like_input_value_is_its_caption():
    # value IS the rendered caption for button/submit/reset inputs — those
    # keep resolving by name with no POM entry.
    raw = _c(tag="input", type="submit", text="Sign In")
    ctrl = _page([raw])["controls"][0]
    assert ctrl["name"] == "sign in"
    assert ctrl["needs_pom"] is False
    assert ctrl.get("submit") is True


def test_collector_js_gates_value_behind_button_like_types():
    # the in-page collector must not carry the old unconditional fallback
    text_block = probe._COLLECT_JS.split("text:")[1].split("label:")[0]
    assert "node.innerText || node.value" not in text_block
    assert "'button', 'submit', 'reset'" in probe._COLLECT_JS


# --- submit signal rendering + ranking ----------------------------------------

def _login_controls():
    return [_c(tag="button", cls="login-options-toggle-btn"),  # machine-named
            _c(tag="a", href="/help", text="Help Centre"),
            _c(tag="button", type="submit", text="Sign In")]


def test_submit_flag_renders_and_ranks_copy_ready_steps_first():
    pg = _page(_login_controls())
    lines = probe.render({"pages": [pg], "errors": []})
    assert "sign in (submit)" in lines
    steps = probe._step_lines(pg["controls"])
    step_rows = [ln.strip() for ln in steps
                 if ln.strip().startswith(("clicks", "enters", "selects"))]
    assert step_rows[0] == 'clicks "sign in"'
    payload = probe.compact_payload({"pages": [pg], "errors": []})
    assert payload["pages"][0]["suggested_steps"][0] == 'clicks "sign in"'


# --- P0-2: failed --do actions are loud, halting, and block authoring ---------

def _failing_do_page():
    """A one-action transaction whose fill raises; a second action follows."""
    page, order = _fake_page([{"controls": [], "headings": []}])
    loc = page.locator.return_value
    loc.first.fill.side_effect = RuntimeError("detached")
    pg = _pg_with([{"kind": "field", "name": "account id",
                    "selector": "input#acc", "visible": True,
                    "needs_pom": False, "step": "s"}])
    probe._do(page, pg, probe.parse_do(
        ["enter 1 in account id", "click account id"]), timeout_ms=1000)
    return pg, order


def test_do_failure_halts_the_transaction_and_records_metadata():
    pg, order = _failing_do_page()
    assert any("do: enter account id" in w for w in pg["do_warnings"])
    assert ("click", None) not in order        # later actions NOT attempted
    assert pg["do_completed"] == 0
    df = pg["do_failed"]
    assert df["index"] == 0
    assert df["selector"] == "input#acc"       # the resolved selector, named
    assert df["skipped"] == ["do: click account id"]


def test_do_warnings_render_in_text_and_compact_output():
    pg, _ = _failing_do_page()
    pg["author_ready"] = probe._author_ready(pg)
    rendered = probe.render({"pages": [pg], "errors": []})
    assert "do: enter account id" in rendered
    assert "transaction halted at action 1" in rendered
    assert "not attempted: do: click account id" in rendered
    payload = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert payload["do_warnings"]
    assert payload["do_failed"]["selector"] == "input#acc"


def test_failed_do_blocks_author_ready_in_both_verdicts():
    pg, _ = _failing_do_page()
    assert probe._author_ready(pg) is False
    assert probe._compact_author_ready(pg, cap=25) is False
    pg["author_ready"] = False
    rendered = probe.render({"pages": [pg], "errors": []})
    assert ("author_ready: false — transaction did not reach requested state"
            in rendered)


def test_explicit_expect_miss_blocks_author_ready():
    pg = _pg_with([])
    pg["expect"] = [{"text": "Dashboard", "found": False},
                    {"text": "Welcome", "found": True}]
    assert probe._author_ready(pg) is False
    assert probe._compact_author_ready(pg, cap=25) is False
    pg["expect"] = [{"text": "Welcome", "found": True}]
    assert probe._author_ready(pg) is True


# --- P0-3: probe and runtime share ONE select implementation ------------------

def test_probe_do_selects_through_the_runtime_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "select_on",
                        lambda page, loc, value: calls.append(value))
    page, _ = _fake_page([{"controls": [], "headings": []}])
    pg = _pg_with([{"kind": "dropdown", "name": "region",
                    "selector": "select#reg", "visible": True,
                    "needs_pom": False, "step": "s"}])
    probe._do(page, pg, probe.parse_do(["select East from region"]),
              timeout_ms=1000)
    assert calls == ["East"]
    assert not pg.get("do_warnings")


def test_select_on_falls_back_to_clicking_options_for_custom_dropdowns():
    page, loc = MagicMock(), MagicMock()
    # native path first
    actions.select_on(page, loc, "East")
    loc.select_option.assert_called_once_with(label="East")
    # non-<select> host → open-and-click fallback, same as the runtime step
    loc2 = MagicMock()
    loc2.select_option.side_effect = Exception(
        "Element is not a <select> element")
    opt = MagicMock()
    opt.count.return_value = 1
    page.get_by_role.return_value = opt
    actions.select_on(page, loc2, "East")
    loc2.click.assert_called_once()            # opened the custom dropdown
    opt.first.click.assert_called_once()       # picked the option row
    # an unrelated failure still raises — never silently swallowed
    loc3 = MagicMock()
    loc3.select_option.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        actions.select_on(page, loc3, "East")


def test_expect_is_skipped_when_the_transaction_failed():
    pg = {"do_warnings": ["do: select device from kind: not a <select>"],
          "do_failed": {"action": "do: select device from kind"}}
    reason = probe._skip_expect_reason(pg)
    assert "transaction failed" in reason
    assert "do: select device from kind" in reason
    # a clean transaction evaluates --expect normally
    assert probe._skip_expect_reason({}) is None


# --- P1-1: deterministic goal target matching ---------------------------------

def _blocks(controls):
    return [({"controls": controls}, "initial", None)]


def test_exact_name_still_wins():
    controls = [{"name": "sign in", "submit": True, "visible": True},
                {"name": "login options toggle btn"}]
    ctrl, _, _, note = goal_mod._locate("login options toggle btn",
                                        _blocks(controls))
    assert note is None and ctrl["name"] == "login options toggle btn"


def test_generic_login_resolves_to_the_submit_control_not_a_lookalike():
    # The review regression: first-substring-wins picked the machine-named
    # lookalike; the runtime auth synonym ("login" → "sign in") must win.
    controls = [{"name": "login options toggle btn"},
                {"name": "sign in", "submit": True, "visible": True}]
    ctrl, _, _, note = goal_mod._locate("login", _blocks(controls))
    assert note is None and ctrl["name"] == "sign in"


def test_submit_intent_falls_back_to_the_unique_visible_submit_control():
    # No exact/synonym name match anywhere — but exactly one visible submit
    # control exists, and the target names a submit-shaped intent.
    controls = [{"name": "remember me"},
                {"name": "authenticate now", "submit": True, "visible": True}]
    ctrl, _, _, note = goal_mod._locate("log in", _blocks(controls))
    assert note is None and ctrl["name"] == "authenticate now"


def test_several_substring_candidates_block_as_ambiguous():
    controls = [{"name": "login help"}, {"name": "login options"}]
    ctrl, _, _, note = goal_mod._locate("login", _blocks(controls))
    assert ctrl is None
    assert "ambiguous" in note and "login help" in note


def test_unique_substring_match_still_resolves():
    controls = [{"name": "employee id field"}, {"name": "store number"}]
    ctrl, _, _, note = goal_mod._locate("employee id", _blocks(controls))
    assert note is None and ctrl["name"] == "employee id field"


def test_evidence_and_compiler_agree_on_the_resolved_target():
    g = {"scenario": "log in",
         "actions": [{"do": "click", "target": "login"}],
         "checks": []}
    assert goal_mod.validate(g) == []
    ev = goal_mod.evidence(g, {"pages": [_page(_login_controls())],
                               "errors": []})
    assert ev["blocking"] == []
    assert ev["proven"]["click:login"] == "sign in"
    feat, _ = goal_mod.compile_goal(g, ev, "APP")
    assert 'User clicks "sign in"' in feat
    assert "toggle" not in feat


def test_evidence_blocks_on_ambiguous_target():
    g = {"scenario": "x",
         "actions": [{"do": "click", "target": "login"}],
         "checks": []}
    result = {"pages": [_page([_c(tag="button", text="Login Help"),
                               _c(tag="button", text="Login Options")])],
              "errors": []}
    ev = goal_mod.evidence(g, result)
    assert any("ambiguous" in b for b in ev["blocking"])


# --- P1-2: no-navigation verdict ----------------------------------------------

def _stub_page(url="https://x/login", click=None):
    page = SimpleNamespace(url=url)
    if click is not None:
        page._noodle_click = click
    return page


def test_stuck_click_flags_a_submit_like_click_that_never_navigated():
    page = _stub_page(click=("sign in", "https://x/login"))
    note = actions.stuck_click(page)
    assert note == ("[no-navigation] clicking 'sign in' left the page "
                    "unchanged (URL still /login)")


def test_stuck_click_stays_quiet_without_a_submit_intent_or_after_navigation():
    # non-submit click names may legitimately not navigate
    assert actions.stuck_click(
        _stub_page(click=("open menu", "https://x/login"))) is None
    # the click DID navigate
    assert actions.stuck_click(
        _stub_page(url="https://x/home",
                   click=("sign in", "https://x/login"))) is None
    # no click recorded at all
    assert actions.stuck_click(_stub_page()) is None


def test_classify_no_navigation_is_wrong_action_target():
    e = _entry(message="Expected to see 'Transaction' — not found",
               warnings=["[no-navigation] clicking 'sign in' left the page "
                         "unchanged (URL still /login)"])
    v = rr.classify(e)
    assert v["category"] == "wrong-action-target"
    assert v["confidence"] == "high"
    assert "sign in" in v["reason"]
    assert "wrong-action-target" in rr.CATEGORIES


def test_navigation_mismatch_still_beats_no_navigation():
    e = _entry(message="Could not find element: 'x'",
               warnings=["[navigation-mismatch] expected /a, current /b",
                         "[no-navigation] clicking 'sign in' left the page "
                         "unchanged (URL still /b)"])
    assert rr.classify(e)["category"] == "navigation-mismatch"

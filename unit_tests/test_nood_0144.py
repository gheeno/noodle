"""NOOD_0144 — one-pass discovery for stateful flows + honest contracts.

A reviewed session burned 6 failed runs on a config-panel flow. Root causes
pinned here, all browser-free:

  P1  probe phrase contract: a suggested phrase built from machine identity
      (humanized id/testid/class) is invisible to the runtime resolver —
      it must ship a POM entry, never a bare "copy-ready" phrase
  P2  probe --do: one stateful fill → select → save transaction per probe,
      delta-snapshotted after every action ("Save → login appears" is
      discovered, not guessed); {env:} in values resolves engine-side
  P3  RCA: "intercepts pointer events" = blocked-by-overlay, NOT the
      NOOD_0123 hidden/duplicate-twin verdict
  P4  author_test hygiene: goal-mode overwrite prunes env keys no feature
      references and deletes an orphaned generated POM; feature mode
      reports them; unused POM keys and the static-only meaning of
      `ready` are surfaced
"""
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import probe as probe_mod
from noodle.repl import core
from noodle.reporting import rca_report


def _raw(**over):
    c = {"tag": "input", "id": "", "role": "", "type": "text", "name": "",
         "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
         "cls": "", "href": "", "text": "", "label": "", "visible": True}
    c.update(over)
    return c


# --- P1: machine-sourced names must ship a POM entry -------------------------

def test_machine_named_control_gets_pom_even_when_text_hides_it():
    # >40-char text keeps _needs_pom happy while the NAME comes from the id —
    # the exact shape that produced an unresolvable "copy-ready" phrase.
    raw = {"controls": [_raw(id="cfg1", tag="button", type="",
                             text="x" * 50)], "headings": []}
    pg = probe_mod.summarize(raw, url="u", title="t")
    c = pg["controls"][0]
    assert c["machine_name"] and c["needs_pom"]
    assert "pom" in c and pg["pom_yaml"]          # working selector shipped

def test_readable_named_control_is_unchanged():
    raw = {"controls": [_raw(label="account id")], "headings": []}
    c = probe_mod.summarize(raw, url="u", title="t")["controls"][0]
    assert not c.get("machine_name") and not c["needs_pom"]


def test_name_source_tracks_the_producing_handle():
    assert probe_mod._name_and_source(_raw(label="Zip"))[1] == "label"
    assert probe_mod._name_and_source(_raw(text="Save"))[1] == "text"
    assert probe_mod._name_and_source(_raw(id="cfg1"))[1] == "id"
    assert probe_mod._name_and_source(_raw(cls="e2e_save"))[1] == "cls"


def test_machine_named_control_leaves_the_copy_ready_step_slice():
    raw = {"controls": [_raw(id="cfg1", tag="button", type="",
                             text="y" * 50),
                        _raw(label="username")], "headings": []}
    pg = probe_mod.summarize(raw, url="u", title="t")
    steps = "\n".join(probe_mod._step_lines(pg["controls"]))
    assert "username" in steps and "cfg1" not in steps


# --- P2: the --do transaction ------------------------------------------------

def test_parse_do_grammar():
    assert probe_mod.parse_do(
        ["enter 123 in account id", "select East from region",
         "click save settings"]) == [
        ("enter", "account id", "123"), ("select", "region", "East"),
        ("click", "save settings", None)]


def test_parse_do_rejects_junk_before_any_browser():
    with pytest.raises(ValueError, match="frobnicate"):
        probe_mod.parse_do(["frobnicate the widget"])
    r = probe_mod.probe(["http://x"], do=["frobnicate the widget"])
    assert r["pages"] == [] and "frobnicate" in r["errors"][0]["error"]


def _pg_with(controls):
    return {"url": "https://app.example/x", "title": "t",
            "controls": controls, "pom_yaml": "", "headings": [],
            "next_pages": []}


def _fake_page(raws):
    order = []
    page = MagicMock()
    loc = MagicMock()
    loc.first.fill.side_effect = lambda v, **k: order.append(("fill", v))
    loc.first.select_option.side_effect = \
        lambda **k: order.append(("select", k.get("label")))
    loc.first.click.side_effect = lambda **k: order.append(("click", None))
    loc.first.dispatch_event.side_effect = \
        lambda *a, **k: order.append(("click", None))
    page.locator.return_value = loc

    def _evaluate(js, *a, **k):
        if "__noodleMo" in js:
            return True
        return raws.pop(0)
    page.evaluate.side_effect = _evaluate
    page.url = "https://app.example/x"
    page.title.return_value = "t"
    return page, order


def test_do_executes_in_order_and_snapshots_only_real_deltas():
    controls = [
        {"kind": "field", "name": "account id", "selector": "input#acc",
         "visible": True, "needs_pom": False, "step": "s"},
        {"kind": "dropdown", "name": "region", "selector": "select#reg",
         "visible": True, "needs_pom": False, "step": "s"},
        {"kind": "button", "name": "save settings", "selector": "button#save",
         "visible": True, "needs_pom": False, "step": "s"}]
    # fills/select reveal nothing; the save click reveals the login form
    raws = [{"controls": [], "headings": []},
            {"controls": [], "headings": []},
            {"controls": [_raw(label="username"),
                          _raw(tag="button", type="submit", text="Sign In")],
             "headings": ["Sign In"]}]
    page, order = _fake_page(raws)
    pg = _pg_with(controls)
    probe_mod._do(page, pg, probe_mod.parse_do(
        ["enter 123 in account id", "select East from region",
         "click save settings"]), timeout_ms=1000)
    assert order == [("fill", "123"), ("select", "East"), ("click", None)]
    revealed = pg["revealed"]                 # empty deltas add no noise
    assert [r["revealed_by"] for r in revealed] == ["do: click save settings"]
    assert {c["name"] for c in revealed[0]["controls"]} == \
        {"username", "sign in"}


def test_do_failure_warns_and_halts():
    # NOOD_0145 — a failing action HALTS the transaction (prior evidence
    # stays): running later actions against a state the caller never reached
    # produced evidence that read as if the flow completed.
    page, order = _fake_page([{"controls": [], "headings": []}])
    loc = page.locator.return_value
    loc.first.fill.side_effect = RuntimeError("detached")
    pg = _pg_with([{"kind": "field", "name": "account id",
                    "selector": "input#acc", "visible": True,
                    "needs_pom": False, "step": "s"}])
    probe_mod._do(page, pg, probe_mod.parse_do(
        ["enter 1 in account id", "click account id"]), timeout_ms=1000)
    assert any("do: enter account id" in w for w in pg["do_warnings"])
    assert ("click", None) not in order       # later actions NOT attempted
    assert pg["do_failed"]["skipped"] == ["do: click account id"]


def test_do_values_never_echo_into_the_payload():
    raws = [{"controls": [_raw(label="ok")], "headings": []}]
    page, _ = _fake_page(raws)
    pg = _pg_with([{"kind": "field", "name": "pin", "selector": "input#p",
                    "visible": True, "needs_pom": False, "step": "s"}])
    probe_mod._do(page, pg, probe_mod.parse_do(["enter hunter2 in pin"]),
                  timeout_ms=1000)
    assert "hunter2" not in str(pg)


def test_core_probe_page_resolves_env_refs_in_do(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(probe_mod, "probe",
                        lambda urls, **kw: captured.update(kw)
                        or {"pages": [], "errors": []})
    monkeypatch.setenv("ACC_CODE", "z9")
    core.probe_page("http://x", do=["enter {env:ACC_CODE} in account id"],
                    workspace=str(tmp_path))
    assert captured["do"] == ["enter z9 in account id"]


def test_core_probe_page_blocks_on_unresolved_env_ref(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(probe_mod, "probe",
                        lambda urls, **kw: called.append(1))
    monkeypatch.delenv("NOPE_KEY_0144", raising=False)
    r = core.probe_page("http://x", do=["enter {env:NOPE_KEY_0144} in f"],
                        workspace=str(tmp_path))
    assert not called and "NOPE_KEY_0144" in r["errors"][0]["error"]


# --- P3: blocked-by-overlay outranks the hidden/duplicate verdict ------------

_OVERLAY_MSG = (
    "Timeout 30000ms exceeded.\n"
    '<div class="modal-backdrop fade show"> intercepts pointer events\n'
    "waiting for element to be visible, enabled and stable")


def test_intercepting_overlay_gets_its_own_verdict():
    v = rca_report.classify({"scenario": "s", "message": _OVERLAY_MSG,
                             "trace": "", "warnings": []})
    assert v["category"] == "blocked-by-overlay"
    assert v["confidence"] == "high"
    assert "modal-backdrop" in v["reason"]
    assert "locator" in v["fix"]              # says: do NOT re-guess it


def test_plain_hidden_element_still_maps_to_locator_rot():
    v = rca_report.classify({"scenario": "s", "trace": "", "warnings": [],
                             "message": "element is not visible"})
    assert v["category"] == "locator-rot"


def test_blocked_by_overlay_is_a_known_category():
    assert "blocked-by-overlay" in rca_report.CATEGORIES


# --- P4: author_test package hygiene + honest ready --------------------------

def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    "    And User clicks the login button\n"
    '    Then User should see "Dashboard"\n'
)
_POM = 'match: {}\nlogin button:\n  css: "button"\nold unused key:\n  css: "#x"\n'


def _author(ws, **over):
    kw = dict(app_name="Shop", base_url="http://localhost:9",
              feature_path="login", feature_content=_FEATURE,
              pom_content=_POM, workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


def test_feature_mode_reports_stale_env_keys_without_pruning(tmp_path):
    ws = _ws(tmp_path)
    assert _author(ws)["ok"]
    env_p = ws / "noodle_tests/web/shop/resources/shop_environments.yaml"
    env_p.write_text(env_p.read_text() + "STALE_X: v\n")
    r = _author(ws, overwrite=True)
    assert r["stale_env_keys"] == ["STALE_X"]
    assert "STALE_X" in env_p.read_text()     # NOOD_0129 merge contract kept


def test_unused_pom_keys_and_ready_meaning_are_surfaced(tmp_path):
    r = _author(_ws(tmp_path))
    assert r["unused_pom_keys"] == ["old unused key"]
    assert "static" in r["ready_means"] and "run" in r["ready_means"]


_GOAL = {"scenario": "sees the dashboard", "checks": [{"see": "Dashboard"}]}


def _goal_probe_result(*_a, **_k):
    return {"pages": [{"url": "http://localhost:9/", "title": "t",
                       "controls": [], "headings": ["Dashboard"],
                       "pom_yaml": "", "permission_prompts": [],
                       "popups_closed": 0}], "errors": []}


def test_goal_overwrite_prunes_stale_env_and_orphaned_pom(tmp_path,
                                                          monkeypatch):
    ws = _ws(tmp_path)
    assert _author(ws)["ok"]                  # lap 1 leaves a generated POM
    app = ws / "noodle_tests/web/shop"
    env_p = app / "resources/shop_environments.yaml"
    pom_p = app / "resources/pageobjects/login_pom.yaml"
    env_p.write_text(env_p.read_text() + "STALE_X: v\n")
    assert pom_p.is_file()
    monkeypatch.setattr(core, "probe_page", _goal_probe_result)
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="login", goal=_GOAL, overwrite=True,
                         workspace=str(ws))
    assert r["ok"] and r["ready"], r
    assert r["pruned_env_keys"] == ["STALE_X"]
    assert "STALE_X" not in env_p.read_text()
    assert not pom_p.exists()                 # orphaned POM removed
    assert r["removed_stale_pom"]
    # the app URL key survives — referenced by the compiled feature
    assert "shop" in env_p.read_text().lower()


def test_goal_overwrite_keeps_keys_other_features_reference(tmp_path,
                                                            monkeypatch):
    ws = _ws(tmp_path)
    assert _author(ws)["ok"]
    app = ws / "noodle_tests/web/shop"
    (app / "features/other.feature").write_text(
        '@web\nFeature: O\n  Scenario: s\n    Given User is on "{env:API_BASE}"\n')
    env_p = app / "resources/shop_environments.yaml"
    env_p.write_text(env_p.read_text() + "API_BASE: http://api:1\n")
    monkeypatch.setattr(core, "probe_page", _goal_probe_result)
    r = core.author_test(app_name="Shop", base_url="http://localhost:9",
                         feature_path="login", goal=_GOAL, overwrite=True,
                         workspace=str(ws))
    assert r["ok"], r
    assert "API_BASE" in env_p.read_text()    # another feature needs it
    assert "pruned_env_keys" not in r

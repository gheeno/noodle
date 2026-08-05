"""NOOD_0230 — the search→pick→add→verify flow: honest re-binds, one
destination click, a deterministic local grid, and duplicate-feature hygiene.

Three identical live-retail runs of the drill's TC4 gave two distinct
failures and a green on unchanged engine code. The postmortem's shippable
slices, pinned here:

  F1-lite  an untargeted pick re-bound to the card that carries the add
           action is LEGAL but never silent: the payload warns, and the
           intent trace stops counting the pick as verified (the compiled
           steps act on a different item than the probe's pick evidence)
  F4       an item_in_destination check emits its own destination-open
           (settle + click); when the goal's own next action IS that click,
           the destination was opened twice — the second time after the
           assertion, proving nothing at ~20s a lap. The explicit click is
           the opener now. The settle survives per NOOD_0218: its removal
           was measured RED (it protects the in-flight mutation request).
  F0       the flow joins `noodle benchmark --gate` on the engine's own
           fixture grid, and a failed attempt's evidence can be preserved
           (NOODLE_REG_KEEP_ATTEMPTS=1) instead of overwritten
  Plan 1   duplicate-feature hygiene: `noodle remove` (file + POM sidecar +
           accumulated results + intent contract), and report_scope names
           the app-vs-run gap a package-green claim must reconcile
"""
import json

import pytest

from noodle import regression, regression_fixture
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.reporting import scope

# --------------------------------------------------------------------------
# shared probe fixture — the NOOD_0212 detour shape plus a destination link
# --------------------------------------------------------------------------


def _card(caption, action=None):
    it = {"caption": caption, "selector": f"#tile-{caption[:4]}",
          "href": "x", "actions": []}
    if action:
        it["actions"] = [{"name": action[0], "selector": action[1]}]
    return it


def _probe(picked="Aqua Blaster"):
    return {"pages": [{
        "url": "https://shop.example.com/en.html", "title": "t",
        "controls": [{"name": "cart", "selector": '[aria-label="Cart"]',
                      "kind": "link", "visible": True}],
        "headings": [], "pom_yaml": "", "permission_prompts": [],
        "popups_closed": 0,
        "search": {
            "term": "toy", "headings": [], "controls": [],
            "result_items": [
                _card(picked),                              # no add action
                _card("Science Kit", ("Add", '[aria-label="Add"]'))],
            "picked": {"controls": [], "headings": [picked],
                       "pom_yaml": "", "result_items": [],
                       "picked_caption": picked,
                       "picked_selector": "#tile1"}}}],
        "errors": []}


def _goal(extra_actions=(), check=None):
    return {"scenario": "search toy add to cart",
            "actions": [{"do": "search", "term": "toy", "id": "s"},
                        {"do": "pick", "id": "p"},
                        {"do": "add_to", "destination": "cart",
                         "item_from": "p", "id": "a"},
                        *extra_actions],
            "checks": [check or {"item_in_destination": "cart",
                                 "expected_from": "p", "after": "a"}]}


# --------------------------------------------------------------------------
# F4 — the explicit destination click is the check's opener
# --------------------------------------------------------------------------

def _compiled_lines(goal):
    ev = goal_mod.evidence(goal, _probe())
    assert ev["blocking"] == [], ev["blocking"]
    feat, _ = goal_mod.compile_goal(goal, ev, "SHOP")
    return feat, feat.splitlines(), ev


def test_explicit_destination_click_becomes_the_checks_opener():
    goal = _goal(extra_actions=[{"do": "click", "target": "cart", "id": "c"}])
    feat, lines, _ = _compiled_lines(goal)
    assert feat.count('User clicks "cart"') == 1
    idle = next(i for i, ln in enumerate(lines) if "network to be idle" in ln)
    cart = next(i for i, ln in enumerate(lines) if 'clicks "cart"' in ln)
    then = next(i for i, ln in enumerate(lines) if "the user sees" in ln)
    # the NOOD_0218 settle survives, immediately before the ONE click, and
    # the identity assertion lands on the page that click opens
    assert idle == cart - 1
    assert then == cart + 1


def test_no_explicit_click_keeps_the_engine_added_opener():
    goal = _goal()
    feat, lines, _ = _compiled_lines(goal)
    assert feat.count('User clicks "cart"') == 1
    idle = next(i for i, ln in enumerate(lines) if "network to be idle" in ln)
    cart = next(i for i, ln in enumerate(lines) if 'clicks "cart"' in ln)
    assert idle == cart - 1


def test_a_non_adjacent_click_never_dedupes():
    """An action BETWEEN the anchor and the destination click could change
    the state the assertion depends on — anything but the immediate next
    action keeps the engine-added opener."""
    goal = _goal(extra_actions=[{"do": "go_back", "id": "b"},
                                {"do": "click", "target": "cart", "id": "c"}])
    ev = goal_mod.evidence(goal, _probe())
    feat, _ = goal_mod.compile_goal(goal, ev, "SHOP")
    assert feat.count('User clicks "cart"') == 2


def test_the_opener_suppresses_the_observation_prerequisite():
    goal = _goal(extra_actions=[{"do": "click", "target": "cart", "id": "c"}])
    ev = goal_mod.evidence(goal, _probe())
    reqs = [p["required_by"] for p in
            goal_mod.intent_summary(goal, ev)["required_prerequisites"]]
    assert "observation:item_in_destination" not in reqs
    # ...and stays claimed when the engine really does add the click
    goal2 = _goal()
    ev2 = goal_mod.evidence(goal2, _probe())
    reqs2 = [p["required_by"] for p in
             goal_mod.intent_summary(goal2, ev2)["required_prerequisites"]]
    assert reqs2 == ["observation:item_in_destination"]


def test_an_unanchored_check_dedupes_against_the_final_click():
    goal = _goal(extra_actions=[{"do": "click", "target": "cart", "id": "c"}],
                 check={"item_in_destination": "cart", "expected_from": "p"})
    feat, lines, _ = _compiled_lines(goal)
    assert feat.count('User clicks "cart"') == 1
    then = next(i for i, ln in enumerate(lines) if "the user sees" in ln)
    cart = next(i for i, ln in enumerate(lines) if 'clicks "cart"' in ln)
    assert then == cart + 1


def test_the_evidence_marker_follows_the_moved_check():
    goal = _goal(extra_actions=[{"do": "click", "target": "cart", "id": "c"}])
    goal["checks"][0]["evidence"] = "screenshot"
    feat, lines, _ = _compiled_lines(goal)
    import re
    m = re.search(r"@evidence:steps=(\d+)", feat)
    assert m, feat
    steps = [ln for ln in lines
             if ln.strip().split(" ", 1)[0] in ("Given", "When", "And", "Then")]
    assert "the user sees" in steps[int(m.group(1)) - 1]


# --------------------------------------------------------------------------
# F1-lite — a re-bound pick is reported, never silent
# --------------------------------------------------------------------------

def test_a_rebound_pick_warns_and_records_its_origin():
    ev = goal_mod.evidence(_goal(), _probe())
    assert ev["bound_targets"]["p"]["rebound_from"] == "Aqua Blaster"
    assert ev["bound_targets"]["p"]["caption"] == "Science Kit"
    assert any('bound "Aqua Blaster"' in w and '"Science Kit"' in w
               for w in ev["warnings"])


def test_a_rebound_pick_fails_the_intent_trace():
    goal = _goal()
    ev = goal_mod.evidence(goal, _probe())
    trace = goal_mod.intent_trace(goal, ev)
    pick = next(t for t in trace if t["node"] == "actions[1]")
    assert pick["ok"] is False
    assert "re-bound" in pick["evidence"]
    assert not all(t["ok"] for t in trace)   # what drags intent_verified false


def test_a_pick_that_never_swapped_stays_verified():
    """The card carrying the add IS the picked item — no swap, no noise."""
    probe = _probe()
    items = probe["pages"][0]["search"]["result_items"]
    items[0]["actions"] = [{"name": "Add", "selector": '[aria-label="Add"]'}]
    goal = _goal()
    ev = goal_mod.evidence(goal, probe)
    assert ev["blocking"] == []
    assert "rebound_from" not in ev["bound_targets"]["p"]
    assert not any("re-bound" in str(t.get("evidence"))
                   for t in goal_mod.intent_trace(goal, ev))


# --------------------------------------------------------------------------
# env-key collision — two features of one app, different start pages
# --------------------------------------------------------------------------

def _nav_goal(url):
    return {"scenario": "s", "navigation": [url],
            "actions": [{"do": "click", "target": "x"}], "checks": []}


def test_an_env_key_holding_another_url_is_never_repurposed():
    """The gate's own two fixture cases collided here: tc5's authoring
    re-pointed the app key tc4's Given resolved through, and the package
    run went red on a feature that was green an hour earlier."""
    shop = "http://127.0.0.1:5000/shop.html"
    keys = goal_mod.navigation_env(
        _nav_goal(shop), "app", base_url=shop,
        existing={"app": "http://127.0.0.1:5000/index.html"})
    assert keys == [("APP_SHOP", shop)]


def test_a_key_holding_the_same_url_is_still_reused():
    home = "http://127.0.0.1:5000/index.html"
    keys = goal_mod.navigation_env(
        _nav_goal(home), "app", base_url=home,
        existing={"app": home})
    assert keys == [("APP", home)]


def test_a_conflicting_derived_key_suffixes_past_the_holder():
    shop = "http://127.0.0.1:5000/shop.html"
    keys = goal_mod.navigation_env(
        _nav_goal(shop), "app", base_url=shop,
        existing={"app": "http://x/other.html",
                  "APP_SHOP": "http://x/legacy.html"})
    assert keys == [("APP_SHOP2", shop)]


def test_two_features_of_one_app_keep_their_own_navigation(tmp_path,
                                                           monkeypatch):
    """End to end through author_test: the second feature's authoring mints
    its own key, leaves the first feature's app-key value alone, and the
    NOOD_0135 URL-fidelity gate verifies the key the compiled feature
    actually navigates with — not the app key it deliberately left behind."""
    import yaml

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n",
                                    encoding="utf-8")

    def _probe(name, heading):
        return {"pages": [{
            "url": "http://h/x", "title": "t",
            "controls": [{"name": name, "selector": "#a", "kind": "link",
                          "visible": True}],
            "headings": [heading], "pom_yaml": "",
            "permission_prompts": [], "popups_closed": 0}], "errors": []}

    def _goal(url, target, text):
        return {"scenario": f"open {target}", "navigation": [url],
                "actions": [{"do": "click", "target": target, "id": "c1"}],
                "checks": [{"see": text, "after": "start"}]}

    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _probe("go shopping", "Home"))
    r1 = core.author_test(app_name="Shop", base_url="http://h/index.html",
                          feature_path="home_flow",
                          goal=_goal("http://h/index.html", "go shopping",
                                     "Home"),
                          workspace=str(ws))
    assert r1["ok"] and r1["ready"], r1.get("blocking")
    assert '{env:SHOP}' in r1["compiled"]["feature"]

    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _probe("first hit", "Results"))
    r2 = core.author_test(app_name="Shop", base_url="http://h/results.html",
                          feature_path="results_flow",
                          goal=_goal("http://h/results.html", "first hit",
                                     "Results"),
                          workspace=str(ws))
    assert r2["ok"] and r2["ready"], r2.get("blocking")
    assert '{env:SHOP_RESULTS}' in r2["compiled"]["feature"]
    env = yaml.safe_load(
        (ws / "noodle_tests" / "web" / "shop" / "resources"
         / "shop_environments.yaml").read_text(encoding="utf-8"))
    assert env["shop"] == "http://h/index.html"
    assert env["SHOP_RESULTS"] == "http://h/results.html"


# --------------------------------------------------------------------------
# Plan 1 — report_scope names the app-vs-run gap
# --------------------------------------------------------------------------

def test_scope_note_names_the_app_vs_run_gap(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_nood_0229 import _mark, _write_result
    d = tmp_path / "results"
    d.mkdir()
    app = tmp_path / "features"
    app.mkdir()
    (app / "A.feature").write_text("Feature: A\n", encoding="utf-8")
    (app / "B.feature").write_text("Feature: B\n", encoding="utf-8")
    _write_result(d, "u1", "A", "s1", 1_000)
    _write_result(d, "u2", "B", "s1", 2_000)
    _mark(d, "run-2", 2_000)
    sc = scope.report_scope(d, app)
    assert sc["features_in_app"] == 2 and sc["features_run_count"] == 1
    assert "this run verified 1 of the 2 feature file(s)" in sc["note"]
    assert "noodle remove" in sc["note"]


# --------------------------------------------------------------------------
# Plan 1 — noodle remove
# --------------------------------------------------------------------------

@pytest.fixture
def rm_ws(tmp_path):
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                          encoding="utf-8")
    app = tmp_path / "noodle_tests" / "web" / "pkg"
    (app / "features").mkdir(parents=True)
    (app / "resources" / "pageobjects").mkdir(parents=True)
    feat = app / "features" / "stale_flow.feature"
    feat.write_text("@web\nFeature: stale\n\n  Scenario: s\n", encoding="utf-8")
    (app / "resources" / "pageobjects" / "stale_flow_pom.yaml").write_text(
        "pages: {}\n", encoding="utf-8")
    results = tmp_path / "artifacts" / "allure-results"
    results.mkdir(parents=True)
    rel = "noodle_tests/web/pkg/features/stale_flow.feature"
    (results / "u9-result.json").write_text(json.dumps({
        "uuid": "u9", "historyId": "h", "name": "s", "fullName": "stale: s",
        "start": 1, "stop": 2, "status": "failed",
        "labels": [{"name": "featureFile", "value": rel}]}), encoding="utf-8")
    state = {"intent_contracts": {rel: {"blocked": True, "blocking": ["x"],
                                        "intent_verified": False}}}
    (tmp_path / ".noodle").mkdir()
    (tmp_path / ".noodle" / "agent_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    return tmp_path, rel


def test_remove_feature_takes_its_lies_with_it(rm_ws):
    ws, rel = rm_ws
    r = core.remove_feature(rel, workspace=str(ws))
    assert r["ok"], r
    assert rel in r["removed"]
    assert not (ws / rel).exists()
    assert not (ws / "noodle_tests" / "web" / "pkg" / "resources"
                / "pageobjects" / "stale_flow_pom.yaml").exists()
    assert r["results_purged"] == 1
    assert not list((ws / "artifacts" / "allure-results").glob("*-result.json"))
    contracts = (core.load_state(str(ws)).get("intent_contracts") or {})
    assert rel not in contracts


def test_remove_feature_refuses_bad_paths(rm_ws):
    ws, rel = rm_ws
    assert not core.remove_feature("noodle.yaml", workspace=str(ws))["ok"]
    assert not core.remove_feature("../outside.feature",
                                   workspace=str(ws))["ok"]
    missing = core.remove_feature(
        "noodle_tests/web/pkg/features/nope.feature", workspace=str(ws))
    assert not missing["ok"] and "does not exist" in missing["error"]


# --------------------------------------------------------------------------
# F0 — the deterministic grid joins the gate, attempts can be preserved
# --------------------------------------------------------------------------

def test_the_benchmark_covers_the_search_pick_add_shape():
    tc5 = next(p for p in regression.PROMPTS
               if p["id"] == "tc5_search_pick_add")
    assert "Click the first result" in tc5["content"]
    assert "Add it to the cart" in tc5["content"]
    assert "Click cart" in tc5["content"]
    assert "{FIXTURE}/shop.html" in tc5["content"]


def test_fixture_search_leg_is_the_result_card_shape():
    """The results page must be what build_result_items extracts: repeated
    anchor class, distinct hrefs, captioned; the product page must carry the
    mutation control and the destination link; the cart must render state."""
    results = regression_fixture.PAGES["results.html"]
    assert results.count('class="result-link"') == 3
    hrefs = {seg.split('"')[0] for seg in results.split('href="')[1:]}
    assert len(hrefs) == 3          # three distinct product pages
    assert "3 results" in results
    product = regression_fixture.PAGES["gadget_alpha.html"]
    assert "add to cart" in product and 'href="cart.html"' in product
    assert "Alpha Gadget" in product
    assert "picked_item" in regression_fixture.PAGES["cart.html"]
    assert 'type="search"' in regression_fixture.PAGES["shop.html"]


def test_the_tc5_prompt_expands_to_the_pick_add_contract():
    from noodle.repl import prompt_expander as pe
    tc5 = next(p for p in regression.PROMPTS
               if p["id"] == "tc5_search_pick_add")
    exp = pe.expand(tc5["content"].replace("{FIXTURE}",
                                           "http://127.0.0.1:9999"))
    assert exp["ok"], exp.get("conflicts") or exp.get("error")
    dos = [a["do"] for a in exp["goal"]["actions"]]
    assert dos == ["search", "pick", "add_to", "click"]
    check = exp["goal"]["checks"][0]
    assert check["item_in_destination"] == "cart"
    assert check["after"] == exp["goal"]["actions"][2]["id"]


def test_keep_attempts_preserves_the_evidence(tmp_path, monkeypatch):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "probe_goal.json").write_text(
        "{}", encoding="utf-8")
    res = {"author": {"ready": False, "blocking": ["x"]}}
    monkeypatch.delenv("NOODLE_REG_KEEP_ATTEMPTS", raising=False)
    regression._keep_attempt(str(tmp_path), "tc9", 1, res)
    assert not (tmp_path / "attempts").exists()      # opt-in stays opt-in
    monkeypatch.setenv("NOODLE_REG_KEEP_ATTEMPTS", "1")
    regression._keep_attempt(str(tmp_path), "tc9", 1, res)
    kept = tmp_path / "attempts" / "tc9_lap1"
    assert json.loads((kept / "payload.json").read_text(encoding="utf-8"))
    assert (kept / "probe_goal.json").is_file()

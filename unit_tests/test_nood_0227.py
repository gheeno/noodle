"""NOOD_0227 — the 65.9-AIC blowout remediation (RCA tracks A–E).

The reference scenario — a four-page click/enter e-commerce flow with one
row-scoped click — cost 27 tool calls because goal mode drove the page with
a click-only, `within:`-blind, silently-failing executor and validated a
multi-page flow against a single page's inventory. These pin the fixes:

  B1/B2  searchless goals route reveal clicks through the probe's own
         transaction executor: `within:` compiles to the row grammar, and
         search-shaped goals keep the reveal path (do runs post-search)
  B3     a halted do-chain blocks the FAILED action with the halt context;
         actions the chain never reached get NO wrong-page near-miss noise
  B4/B5  the inventory spans every walked page-state and blockers say where
         they searched
  B6     probe.perform walks plain click/enter flows (it silently no-opped)
  performed-honesty  a do-walked block alone never fakes probe.perform:
         the flag + a completed chain are both required
  C1     one blocked goal no longer poisons its whole app package
  C2     reset_intent clears a stale contract without hand-editing state
  C3     run_will_proceed says up front when the run gate would refuse
  A2/A3  vocabulary sections + structured evidence-value fields
  D1     checks[].evidence compiles to tag metadata, never step prose
  D4     a navigation step counts page-level for evidence validity
  E1     the page graph records probe evidence and annotates blockers
  E3     decoration-insensitive text assertion (emoji-prefixed headings)
"""
import re
from types import SimpleNamespace

from noodle.repl import goal as goal_mod
from noodle.repl import page_graph
from noodle.repl import validate as _validate


def _ctrl(name, selector, kind="button", **extra):
    c = {"name": name, "selector": selector, "kind": kind,
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _pg(controls=None, headings=None, url="https://x/", revealed=None,
        **over):
    pg = {"url": url, "title": "t", "controls": controls or [],
          "headings": headings or [], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    if revealed is not None:
        pg["revealed"] = revealed
    pg.update(over)
    return pg


# --- B1/B2: reveal clicks ride the do-chain, within: survives ---------------

def test_within_scoped_click_rides_the_row_grammar():
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "add to cart",
                         "within": "ITEM_A", "id": "a"},
                        {"do": "click", "target": "proceed to checkout",
                         "id": "p"}],
            "checks": []}
    args = goal_mod.probe_args(goal)
    assert args["click"] is None
    assert args["do"] == ["click add to cart in the row containing ITEM_A",
                          "click proceed to checkout"]


def test_the_emitted_strings_parse_as_the_probe_grammar():
    from noodle.agents.web.probe import parse_do
    parsed = parse_do(["click add to cart in the row containing ITEM_A"])
    assert parsed == [("click_row", "add to cart", "ITEM_A")]


def test_search_goals_keep_the_reveal_path():
    """A search goal's do-chain deliberately runs AFTER the search
    (NOOD_0168); pushing pre-search reveal clicks into it would reorder the
    flow, so those goals keep the click channel."""
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "shop menu"},
                        {"do": "search", "term": "toys"}],
            "checks": []}
    args = goal_mod.probe_args(goal)
    assert args["click"] == ["shop menu"]
    assert args["do"] is None


# --- B6: probe.perform now walks click/enter flows --------------------------

def _order_goal(perform=True):
    return {"scenario": "order",
            **({"probe": {"perform": True}} if perform else {}),
            "actions": [
                {"do": "click", "target": "add to cart",
                 "within": "ITEM_A", "id": "a"},
                {"do": "click", "target": "proceed to checkout", "id": "p"},
                {"do": "enter", "target": "name", "value": "n", "id": "n"},
                {"do": "click", "target": "place order", "id": "o"}],
            "checks": []}


def test_perform_walks_the_click_enter_tail():
    """RC-2: the gate was add_to/press_key/go_back only, so the commonest
    flow shape returned [] and the documented opt-in silently no-opped."""
    assert goal_mod.perform_do(_order_goal()) == [
        "enter n in name", "click place order"]
    args = goal_mod.probe_args(_order_goal())
    assert args["do"] == ["click add to cart in the row containing ITEM_A",
                          "click proceed to checkout",
                          "enter n in name", "click place order"]


def test_perform_stays_opt_in():
    assert goal_mod.perform_do(_order_goal(perform=False)) == []


def test_perform_add_to_flow_is_unchanged():
    goal = {"scenario": "s", "probe": {"perform": True},
            "actions": [{"do": "search", "term": "t"},
                        {"do": "pick", "id": "r"},
                        {"do": "add_to", "destination": "cart",
                         "item_from": "r"},
                        {"do": "click", "target": "checkout"}],
            "checks": []}
    assert goal_mod.perform_do(goal) == ["click checkout"]


# --- B3: honest halts — the failed action blocks, the rest defers -----------

def _halted_result():
    return {"pages": [_pg(
        controls=[_ctrl("add to cart", "button#add")],
        headings=["ITEM_A"],
        do_requested=2, do_completed=0,
        do_warnings=["do: click add to cart in row containing ITEM_A: boom"],
        do_failed={"index": 0,
                   "action": "do: click add to cart in row containing ITEM_A",
                   "selector": "button#add",
                   "error": "No row containing 'ITEM_A' found",
                   "skipped": ["do: click proceed to checkout"]})],
        "errors": []}


def test_halted_chain_blocks_the_failed_action_with_the_halt_context():
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "add to cart",
                         "within": "ITEM_A", "id": "a"},
                        {"do": "click", "target": "proceed to checkout",
                         "id": "p"}],
            "checks": []}
    ev = goal_mod.evidence(goal, _halted_result())
    halted = [b for b in ev["blocking"]
              if "probe transaction halted at this step" in b]
    assert halted and "0 of 2 action(s) completed" in halted[0]
    assert "No row containing 'ITEM_A' found" in halted[0]
    assert "never ran: do: click proceed to checkout" in halted[0]


def test_actions_the_chain_never_reached_get_no_near_miss_noise():
    """RC-1's cruelty: the skipped step used to block with the LANDING page's
    vocabulary — every candidate a wrong repair. It now defers, visibly."""
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "add to cart",
                         "within": "ITEM_A", "id": "a"},
                        {"do": "click", "target": "proceed to checkout",
                         "id": "p"}],
            "checks": []}
    ev = goal_mod.evidence(goal, _halted_result())
    assert not any(b.startswith('click "proceed to checkout"')
                   for b in ev["blocking"])
    assert ev["proven_phase"]["click:proceed to checkout"] == "unreached"


def test_reveal_path_click_failures_surface_as_warnings():
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "shop menu"},
                        {"do": "search", "term": "toys"}],
            "checks": []}
    result = {"pages": [_pg(
        controls=[_ctrl("shop menu", "a#menu")],
        click_warnings=['--click "shop menu": intercepted by overlay'],
        search={"term": "toys", "results_summary": None})], "errors": []}
    ev = goal_mod.evidence(goal, result)
    assert any("intercepted by overlay" in w for w in ev["warnings"])


# --- performed-honesty: blocks alone never fake probe.perform ---------------

def _walked_result(heading="Saved"):
    rev = [{"controls": [_ctrl("name", "input#n", kind="field"),
                         _ctrl("place order", "button#go")],
            "headings": [], "pom_yaml": "",
            "revealed_by": "do: click proceed", "performed": True,
            "url": "https://x/checkout"},
           {"controls": [], "headings": [heading], "pom_yaml": "",
            "revealed_by": "do: click place order", "performed": True,
            "url": "https://x/done"}]
    return {"pages": [_pg(controls=[_ctrl("proceed", "a#go")],
                          revealed=rev, do_requested=3, do_completed=3)],
            "errors": []}


def _walk_goal(perform):
    return {"scenario": "s",
            **({"probe": {"perform": True}} if perform else {}),
            "actions": [{"do": "click", "target": "proceed", "id": "p"},
                        {"do": "enter", "target": "name", "value": "x",
                         "id": "n"},
                        {"do": "click", "target": "place order", "id": "o"}],
            "checks": [{"see": "Saved"}]}


def test_perform_on_a_completed_chain_proves_the_end_state():
    ev = goal_mod.evidence(_walk_goal(perform=True), _walked_result())
    assert ev["blocking"] == []
    assert "see:Saved" in ev["proven"]


def test_walked_blocks_without_the_flag_still_defer_post_gate_checks():
    """The default path walks pre-gate clicks through the do-chain, so
    performed-phase blocks now exist WITHOUT probe.perform. They must not
    clear the runtime gate: the probe never typed the data."""
    ev = goal_mod.evidence(_walk_goal(perform=False), _walked_result())
    assert ev["blocking"] == []
    assert "see:Saved" not in ev["proven"]
    assert 'the user sees "Saved"' in ev["runtime_asserted"]


# --- B5: blockers say where they searched -----------------------------------

def test_miss_blockers_name_the_searched_page_states():
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "nonexistent"}],
            "checks": []}
    result = {"pages": [_pg(
        controls=[_ctrl("proceed", "a#go")],
        revealed=[{"controls": [_ctrl("place order", "button#x")],
                   "headings": [], "pom_yaml": "",
                   "revealed_by": "do: click proceed", "performed": True,
                   "url": "https://x/checkout"}])], "errors": []}
    ev = goal_mod.evidence(goal, result)
    miss = [b for b in ev["blocking"] if b.startswith('click "nonexistent"')]
    assert miss and "searched 2 probed page-state(s)" in miss[0]
    assert "https://x/checkout" in miss[0]


# --- A2/A3: vocabulary sections + structured constraints --------------------

def test_evidence_values_are_a_structured_field():
    v = goal_mod.vocabulary()
    assert v["check_keys_detail"]["evidence"]["values"] == \
        ["screenshot", "none"]
    assert v["goal_evidence_values"] == ["all", "assertions", "off"]
    assert set(v["check_kinds"]["one_of"]) == set(goal_mod._CHECK_KINDS)


def test_vocabulary_sections_are_bounded_slices():
    sec = goal_mod.vocabulary_section("checks")
    assert "check_kinds" in sec and "check_keys_detail" in sec
    assert "actions" not in sec
    assert goal_mod.vocabulary_section("actions") == {
        "actions": goal_mod.vocabulary()["actions"]}
    assert goal_mod.vocabulary_section("nope") is None


# --- C2: reset_intent — no more hand-editing agent_state.json ---------------

def test_reset_intent_clears_one_or_all(tmp_path):
    from noodle.repl import core
    ws = str(tmp_path)
    core.save_state({"intent_contracts": {
        "noodle_tests/web/shop/features/a.feature": {"blocked": True},
        "noodle_tests/web/shop/features/b.feature": {"blocked": False}}}, ws)
    r = core.reset_intent("a.feature", workspace=ws)
    assert r["ok"] and r["cleared"] == [
        "noodle_tests/web/shop/features/a.feature"]
    left = core.load_state(ws)["intent_contracts"]
    assert list(left) == ["noodle_tests/web/shop/features/b.feature"]
    assert core.reset_intent(workspace=ws)["ok"]
    assert core.load_state(ws)["intent_contracts"] == {}


def test_reset_intent_names_the_recorded_contracts_on_a_miss(tmp_path):
    from noodle.repl import core
    ws = str(tmp_path)
    core.save_state({"intent_contracts": {"x/y.feature": {"blocked": True}}},
                    ws)
    r = core.reset_intent("nope.feature", workspace=ws)
    assert r["ok"] is False and r["recorded"] == ["x/y.feature"]


# --- C1 + C3: feature-scoped contracts, run_will_proceed up front -----------

def _blocked_shop_probe():
    return {"pages": [_pg(controls=[_ctrl("Choose options", "button#c")])],
            "errors": []}


def test_blocked_goal_no_longer_poisons_the_whole_app(tmp_path, monkeypatch):
    from noodle.repl import core
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n",
                                    encoding="utf-8")
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: _blocked_shop_probe())
    blocked = core.author_test(
        app_name="Shop", base_url="http://localhost:9", feature_path="t",
        goal={"scenario": "s",
              "actions": [{"do": "click", "target": "not on the page"}],
              "checks": [{"see": "nope"}]},
        workspace=str(ws))
    assert blocked["blocking"]
    manual = ('@web\nFeature: F\n\n  Scenario: s\n'
              '    Given User is on "{env:SHOP}"\n'
              '    When User clicks "Choose options"\n')
    calls = []
    monkeypatch.setattr(core, "run_and_report",
                        lambda *a, **kw: calls.append(1) or
                        {"ok": True, "passed": 1})
    # same feature: the gate still refuses, and said so up front (C3)
    r_same = core.author_test(app_name="Shop", base_url="http://localhost:9",
                              feature_path="t", feature_content=manual,
                              overwrite=True, run_after_author=True,
                              workspace=str(ws))
    assert r_same["ok"] is False and calls == []
    assert r_same["author"]["ready"] is True
    assert r_same["author"]["run_will_proceed"] is False
    assert "--reset-intent" in r_same["run"]["skipped"]
    # DIFFERENT feature, same app: no app-wide poison (C1) — it runs
    r_other = core.author_test(app_name="Shop", base_url="http://localhost:9",
                               feature_path="u", feature_content=manual,
                               run_after_author=True, workspace=str(ws))
    assert calls == [1] and r_other["ok"] is True
    assert r_other["author"]["run_will_proceed"] is True


# --- D1: per-step evidence is tag metadata, never step prose ----------------

def test_check_evidence_compiles_to_tag_metadata():
    goal = {"scenario": "s", "actions": [],
            "checks": [{"see": "Welcome", "evidence": "screenshot"},
                       {"see": "Footer", "evidence": "none"}]}
    ev = goal_mod.evidence(
        goal, {"pages": [_pg(headings=["Welcome", "Footer"])], "errors": []})
    feat, _ = goal_mod.compile_goal(goal, ev, "APP")
    assert "take a screenshot" not in feat and "no screenshot" not in feat
    want = re.search(r"@evidence:steps=(\d+)", feat)
    skip = re.search(r"@evidence:skip=(\d+)", feat)
    steps = [ln.strip() for ln in feat.splitlines()
             if ln.strip().startswith(("Given", "When", "Then", "And",
                                       "But"))]
    assert want and 'sees "Welcome"' in steps[int(want.group(1)) - 1]
    assert skip and 'sees "Footer"' in steps[int(skip.group(1)) - 1]
    chk = _validate.check_feature(feat)      # the tag is legal Gherkin
    assert chk["error"] is None and _validate.unmatched(chk) == []


def test_step_directives_round_trip():
    from noodle.reporting import evidence as report_ev
    want, skip = report_ev.step_directives(
        ["web", "evidence:steps=2,7", "evidence:skip=3"])
    assert want == {2, 7} and skip == {3}
    assert report_ev.step_directives(["evidence", "no_evidence"]) == \
        (set(), set())
    # the new tags never trip the whole-scenario @evidence gate
    assert report_ev.wanted(["evidence:steps=2"], requested=False,
                            is_last_step=False, has_page=True) is False


# --- D4: navigation counts page-level for evidence validity -----------------

def test_navigation_sets_the_page_level_flag(monkeypatch):
    from noodle.orchestrator import runner
    urls = []
    monkeypatch.setattr(runner.actions, "navigate",
                        lambda page, url: urls.append(url))
    ctx = SimpleNamespace(page=SimpleNamespace(), _vars={})
    runner.execute_step('is on "https://x.test/"', ctx)
    assert urls == ["https://x.test/"]
    assert ctx._noodle_page_level_assert is True


def test_element_steps_do_not_inherit_the_exemption(monkeypatch):
    from noodle.orchestrator import runner
    sizes = []
    page = SimpleNamespace(set_viewport_size=lambda s: sizes.append(s))
    ctx = SimpleNamespace(page=page, _vars={})
    runner.execute_step('browser resolution is set to "800x600"', ctx)
    assert ctx._noodle_page_level_assert is False


# --- E3: decoration-insensitive text assertions -----------------------------

def test_decor_norm_strips_edge_decoration_only():
    from noodle.agents.web.actions import _decor_lines_contain, _decor_norm
    assert _decor_norm("  🍔 Home Page ✨ ") == "home page"
    lines = [_decor_norm("🍔 Home Page")]
    assert _decor_lines_contain(lines, "Home Page")
    assert _decor_lines_contain(lines, "🎉 Home Page")   # both sides normed
    # inner punctuation still has to match — literal stays literal
    assert not _decor_lines_contain([_decor_norm("Order 123")], "Order #123")
    # a tiny want must not match everything
    assert not _decor_lines_contain([_decor_norm("anything")], "a")


# --- E1: the page graph -----------------------------------------------------

def _graph_probe():
    return {"pages": [_pg(url="https://x/menu",
                          controls=[_ctrl("Add to cart", "button#a")],
                          headings=["Pizzas"])], "errors": []}


def test_page_graph_records_and_looks_up(tmp_path):
    ws = str(tmp_path)
    page_graph.record(ws, "shop", _graph_probe())
    hit = page_graph.lookup(ws, "shop", "add to cart")
    assert hit and hit["url"] == "https://x/menu"
    assert page_graph.lookup(ws, "shop", "add to cart",
                             exclude_urls=["https://x/menu"]) is None
    assert page_graph.lookup(ws, "shop", "checkout") is None


def test_page_graph_annotates_unrouted_blockers(tmp_path):
    ws = str(tmp_path)
    page_graph.record(ws, "shop", _graph_probe())
    out = page_graph.annotate_blocking(
        ws, "shop",
        ['click "add to cart": no probed control matches that name',
         'click "add to cart": hidden until "menu" is opened'],
        current_urls=["https://x/"])
    assert "page graph: last seen on https://x/menu" in out[0]
    assert "page graph" not in out[1]          # reachability is not routing
    # suffix-only: the repair parsers keep matching
    assert goal_mod.unrouted_targets([out[0]]) == [out[0]]


def test_page_graph_ranks_route_repair_candidates(tmp_path):
    ws = str(tmp_path)
    page_graph.record(ws, "shop", _graph_probe())
    ranked = page_graph.rank_candidates(
        ws, "shop", ["https://x/about", "https://x/menu"], ["add to cart"])
    assert ranked[0] == "https://x/menu"
    assert set(ranked) == {"https://x/about", "https://x/menu"}


# --- the split (RCA §7) ------------------------------------------------------

def test_traversal_validation_lives_in_its_own_module():
    from noodle.repl import goal_evidence
    assert goal_mod.evidence is goal_evidence.evidence
    assert goal_mod.probe_args is goal_evidence.probe_args


# --- A4: the failure payload names the resolved element ---------------------

def test_failure_payload_names_the_last_resolved_element(monkeypatch):
    from noodle import hooks
    from noodle.agents.web import locator

    class _Loc:
        def __repr__(self):
            return "<Locator frame=x selector='button#add'>"

        def inner_text(self, timeout=None):
            return "  add to\n cart  "

    monkeypatch.setattr(locator, "_last_match", ("add to cart", _Loc()))
    monkeypatch.setattr(locator, "_last_match_url", "https://x/catalogue")
    monkeypatch.setattr(locator, "_match_seq", 7)
    ctx = SimpleNamespace(_match_seq_at_step_start=7)   # not this step's
    line = hooks._resolved_element_line(ctx)
    assert "resolved element (from an earlier step)" in line
    assert "'add to cart'" in line and "button#add" in line
    assert "text: 'add to cart'" in line
    assert "https://x/catalogue" in line
    # this step's own resolution drops the earlier-step label
    ctx2 = SimpleNamespace(_match_seq_at_step_start=6)
    assert "(from an earlier step)" not in hooks._resolved_element_line(ctx2)
    monkeypatch.setattr(locator, "_last_match", None)
    assert hooks._resolved_element_line(ctx) == ""


# --- A5: the shell-improvisation footprint self-reports ---------------------

def test_foreign_artifacts_reports_untracked_files(tmp_path):
    from noodle import workspace_policy as wp
    ws = tmp_path
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                    encoding="utf-8")
    feats = ws / "noodle_tests" / "web" / "shop" / "features"
    feats.mkdir(parents=True)
    owned = feats / "authored.feature"
    owned.write_text("@web\nFeature: f\n", encoding="utf-8")
    wp.record(str(ws), [owned])
    (feats / "hand_written.feature").write_text("@web\nFeature: h\n",
                                               encoding="utf-8")
    (feats / "scratch.sh").write_text("echo hi\n", encoding="utf-8")
    sample = ws / "noodle_tests" / "sample_app" / "features"
    sample.mkdir(parents=True)
    (sample / "login.feature").write_text("Feature: s\n", encoding="utf-8")
    found = wp.foreign_artifacts(str(ws))
    assert "noodle_tests/web/shop/features/hand_written.feature" in found
    assert "noodle_tests/web/shop/features/scratch.sh" in found
    assert not any("authored.feature" in f for f in found)
    assert not any("sample_app" in f for f in found)      # scaffold exempt


def test_foreign_artifacts_silent_without_a_manifest(tmp_path):
    from noodle import workspace_policy as wp
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                          encoding="utf-8")
    f = tmp_path / "noodle_tests" / "a" / "features"
    f.mkdir(parents=True)
    (f / "hand.feature").write_text("Feature: h\n", encoding="utf-8")
    assert wp.foreign_artifacts(str(tmp_path)) == []


def test_strict_gate_carries_the_foreign_note(tmp_path, monkeypatch):
    from noodle import workspace_policy as wp
    ws = tmp_path
    (ws / "noodle.yaml").write_text(
        "tests_dir: noodle_tests\nworkspace_strict: true\n", encoding="utf-8")
    feats = ws / "noodle_tests" / "web" / "shop" / "features"
    feats.mkdir(parents=True)
    owned = feats / "authored.feature"
    owned.write_text("@web\nFeature: f\n", encoding="utf-8")
    wp.record(str(ws), [owned])
    (feats / "scratch.py").write_text("print()\n", encoding="utf-8")
    monkeypatch.delenv("NOODLE_WORKSPACE_STRICT", raising=False)
    g = wp.gate(str(ws))
    assert g["ok"] is True                                # advisory, never a gate
    assert g["foreign"] == ["noodle_tests/web/shop/features/scratch.py"]
    assert "no author transaction wrote" in g["foreign_note"]


# --- SC-3: the multi-page benchmark case ------------------------------------

def test_the_benchmark_covers_the_blowout_shape():
    """The RCA's regression guard: the suite that passed while the 65.9-AIC
    defect shipped now carries the exact shape it shipped on."""
    from noodle import regression
    tc4 = next(p for p in regression.PROMPTS
               if p["id"] == "tc4_multipage_checkout")
    assert tc4["mode"] == "prompt"
    assert "{FIXTURE}" in tc4["content"]
    assert 'in the card containing "Turbo Widget"' in tc4["content"]
    assert "place order" in tc4["content"]


def test_fixture_shop_serves_and_tears_down(tmp_path):
    import urllib.request

    from noodle import regression_fixture
    srv, base = regression_fixture.serve(str(tmp_path))
    try:
        html = urllib.request.urlopen(f"{base}/index.html",
                                      timeout=5).read().decode()
        assert "Widget Depot" in html
        cat = urllib.request.urlopen(f"{base}/catalogue.html",
                                     timeout=5).read().decode()
        assert cat.count("add to cart") == 3            # the per-card grid
        assert "cart-panel" in cat                      # the DOM-mutation panel
    finally:
        srv.shutdown()

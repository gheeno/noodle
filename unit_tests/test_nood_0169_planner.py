"""NOOD_0169 — the definitive simple-prompt plan: three-pass prompt
compiler (normalize → translate → typed dataflow), intent-contract review,
bounded planner outcomes, one optional model-fallback call, result-readiness
/ extraction diagnostics, semantic mutation-prerequisite proof, and the
repair provenance gate. The exact session prompt is the regression fixture;
nothing here is site-specific."""
import json
import os
from unittest.mock import MagicMock, PropertyMock

import pytest

from noodle.agents.web import probe as probe_mod
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

# --- the exact regression prompt (verbatim from the session) -----------------

_FIXTURE = (
    "- Open url `www.example.com/cookie.html`\n"
    "- Then url `https://staging.example.com/home.html`\n"
    "1. Open the website (and close all pop ups on home page)\n"
    "2. Search for a toy\n"
    "3. Add to cart\n"
    "- Verify: Toy is added to cart and take screenshot for verification\n")


def test_exact_regression_prompt_produces_the_expected_plan():
    exp = pe.expand(_FIXTURE)
    assert exp["ok"], exp
    g = exp["goal"]
    assert g["navigation"] == [
        "https://www.example.com/cookie.html",
        "https://staging.example.com/home.html"]
    assert sorted(g["dismissals"]) == ["location_prompt", "popups"]
    assert g["actions"] == [
        {"do": "search", "id": "search1", "term": "toy"},
        {"do": "pick", "id": "pick1", "from": "search1",
         "strategy": "first_actionable"},
        {"do": "add_to", "id": "add1", "item_from": "pick1",
         "destination": "cart"}]
    assert g["checks"] == [{"item_in_destination": "cart",
                            "expected_from": "pick1", "after": "add1",
                            "evidence": "screenshot"}]
    # semantic parity gate: the goal sails through the NOOD_0168 pipeline
    norm, _ = goal_mod.normalize(g)
    assert goal_mod.validate(norm) == []
    assert pe.review_contract(exp)["ok"]


def test_backticked_open_url_is_navigation():
    exp = pe.expand("- Open url `www.example.com/a.html`\n- search for toy\n"
                    "- add to cart\n- verify cart has toy")
    assert exp["ok"], exp
    assert exp["goal"]["navigation"] == ["https://www.example.com/a.html"]


def test_then_url_appends_ordered_navigation():
    exp = pe.expand("1. open url a.example.com\n2. Then url b.example.com\n"
                    "3. search for toy\n4. add to cart\n"
                    "5. verify cart has toy")
    assert exp["ok"], exp
    assert exp["goal"]["navigation"] == ["https://a.example.com",
                                         "https://b.example.com"]


def test_parenthetical_close_popups_becomes_a_dismissal():
    exp = pe.expand("1. open url shop.example.com\n"
                    "2. Open the website (and close all pop ups)\n"
                    "3. search for toy\n4. add to cart\n"
                    "5. verify cart has toy")
    assert exp["ok"], exp
    assert "popups" in exp["goal"]["dismissals"]


def test_verify_label_with_colon_parses():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. Verify: toy is added to cart")
    assert exp["ok"], exp
    assert exp["goal"]["checks"][0]["item_in_destination"] == "cart"


def test_screenshot_suffix_is_evidence_not_assertion_text():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n"
                    "4. verify toy is added to cart and take screenshot "
                    "for verification")
    assert exp["ok"], exp
    chk = exp["goal"]["checks"][0]
    assert chk["evidence"] == "screenshot"
    assert "screenshot" not in json.dumps(chk["item_in_destination"])
    assert not any("for verification" in json.dumps(c)
                   for c in exp["goal"]["checks"])


def test_fully_explicit_prompt_uses_fast_path_without_inference():
    exp = pe.expand("1. go to shop.example.com\n2. search for wagon\n"
                    "3. add the red wagon to cart\n"
                    "4. verify the cart has the red wagon")
    assert exp["ok"], exp
    assert exp["translation_mode"] == "deterministic-fast-path"
    assert exp["inferences"] == []


def test_unknown_clause_is_unresolved_by_name():
    exp = pe.expand("1. go to shop.example.com\n2. frobnicate the cart")
    assert not exp["ok"]
    assert exp["unresolved"] and exp["unresolved"][0]["clause"] == "clause-2"
    assert not exp["conflicts"]


def test_expansion_is_byte_deterministic():
    a = json.dumps(pe.expand(_FIXTURE), sort_keys=True)
    b = json.dumps(pe.expand(_FIXTURE), sort_keys=True)
    assert a == b


# --- context / typed dataflow -------------------------------------------------

def test_two_searches_mint_distinct_ids_and_bind_nearest():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. search for wagon\n"
                    "5. add to wishlist")
    assert exp["ok"], exp
    acts = exp["goal"]["actions"]
    s = [a for a in acts if a["do"] == "search"]
    assert [x["id"] for x in s] == ["search1", "search2"]
    picks = {a["id"]: a for a in acts if a["do"] == "pick"}
    adds = [a for a in acts if a["do"] == "add_to"]
    assert picks[adds[0]["item_from"]]["from"] == "search1"
    assert picks[adds[1]["item_from"]]["from"] == "search2"


def test_next_sibling_disambiguates_between_two_searches():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. search for wagon\n4. add to cart\n"
                    "5. verify toy is added to cart")
    assert exp["ok"], exp
    acts = exp["goal"]["actions"]
    pick = next(a for a in acts if a["do"] == "pick")
    assert pick["from"] == "search1"     # the verify names 'toy'


def test_two_equally_compatible_searches_block():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. search for wagon\n4. add to cart")
    assert not exp["ok"]
    assert exp["conflicts"]
    assert "equally compatible" in exp["conflicts"][0]["reason"]


def test_previous_next_conflict_blocks():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. verify laptop is added to cart")
    assert not exp["ok"]
    assert exp["conflicts"] and not exp["unresolved"]


def test_distant_search_outside_window_is_not_borrowed():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. click deals\n4. click banner\n5. add to cart")
    assert not exp["ok"]
    assert any("context window" in c["reason"] for c in exp["conflicts"])


def test_add_without_any_search_is_a_typed_rejection():
    exp = pe.expand("1. go to shop.example.com\n2. add to cart")
    assert not exp["ok"]
    assert exp["conflicts"] and not exp["unresolved"]


def test_picked_item_is_reused_for_a_second_destination():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. add to wishlist")
    assert exp["ok"], exp
    adds = [a for a in exp["goal"]["actions"] if a["do"] == "add_to"]
    assert adds[0]["item_from"] == adds[1]["item_from"] == "pick1"
    assert sum(1 for a in exp["goal"]["actions"] if a["do"] == "pick") == 1


def test_verification_of_an_unrelated_destination_blocks():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. verify wishlist has toy")
    assert not exp["ok"]
    assert any("conflicting destination" in c["reason"]
               for c in exp["conflicts"])


def test_every_inference_carries_provenance_and_support():
    exp = pe.expand(_FIXTURE)
    assert exp["inferences"]
    for inf in exp["inferences"]:
        assert inf["provenance"] == "context-inferred"
        assert inf["source_clauses"] and inf["consumer"]
    assert all(c["status"] != "unresolved" for c in exp["coverage"])


def test_no_surface_label_originates_from_translation():
    exp = pe.expand(_FIXTURE)
    assert not any(a["do"] in ("click", "enter", "select")
                   for a in exp["goal"]["actions"])


# --- generic flows through the same pipeline (no domain rules) -----------------

@pytest.mark.parametrize("prompt,expected_click", [
    ("1. go to app.example.com\n2. enter admin in the username field\n"
     "3. enter secret in the password field\n4. click sign in\n"
     "5. verify Welcome back", "sign in"),
    ("1. go to app.example.com\n2. enter Jane in the name field\n"
     "3. click save\n4. verify Saved successfully", "save"),
    ("1. go to app.example.com\n2. enter Jane in the filter field\n"
     "3. click first row\n4. click delete\n5. verify No records found",
     "delete"),
    ("1. go to app.example.com\n2. select report.pdf from the file picker\n"
     "3. click upload\n4. verify Upload complete", "upload"),
])
def test_generic_flows_are_coherent_without_domain_rules(prompt,
                                                         expected_click):
    exp = pe.expand(prompt)
    assert exp["ok"], exp
    assert exp["translation_mode"] == "deterministic-fast-path"
    assert any(a["do"] == "click" and a["target"] == expected_click
               for a in exp["goal"]["actions"])
    assert pe.review_contract(exp)["ok"]


# --- intent-contract review -----------------------------------------------------

def _exp_stub(goal, **over):
    base = {"ok": True, "goal": goal,
            "clauses": [{"id": "clause-1", "text": "go to x.example.com",
                         "line": 1, "evidence": False}],
            "coverage": [{"clause": "clause-1", "status": "navigation"}],
            "inferences": []}
    base.update(over)
    return base


def test_review_rejects_forward_reference_flows():
    goal = {"scenario": "s", "actions": [
        {"do": "pick", "id": "p1", "from": "s1",
         "strategy": "first_actionable"},
        {"do": "search", "id": "s1", "term": "toy"},
        {"do": "add_to", "id": "a1", "item_from": "p1",
         "destination": "cart"}], "checks": []}
    rev = pe.review_contract(_exp_stub(goal))
    assert not rev["ok"]


def test_review_rejects_uncovered_clauses():
    goal = {"scenario": "s", "actions": [
        {"do": "search", "id": "s1", "term": "toy"}],
        "checks": [{"count": "results", "min": 1}]}
    exp = _exp_stub(goal, clauses=[
        {"id": "clause-1", "text": "search for toy", "line": 1,
         "evidence": False},
        {"id": "clause-2", "text": "something", "line": 2,
         "evidence": False}],
        coverage=[{"clause": "clause-1", "status": "action"}])
    rev = pe.review_contract(exp)
    assert not rev["ok"]
    assert any("clause-2" in p for p in rev["problems"])


def test_review_rejects_orphan_inference():
    goal = {"scenario": "s", "actions": [
        {"do": "search", "id": "s1", "term": "toy"}],
        "checks": [{"count": "results", "min": 1}]}
    exp = _exp_stub(goal, inferences=[
        {"node": "p9", "provenance": "context-inferred",
         "source_clauses": ["clause-1"]}])
    rev = pe.review_contract(exp)
    assert not rev["ok"]
    assert any("orphan" in p for p in rev["problems"])


def test_review_rejects_sourceless_surface_click():
    goal = {"scenario": "s", "actions": [
        {"do": "click", "target": "here"}], "checks": [],
        "allow_no_assertion": True}
    rev = pe.review_contract(_exp_stub(goal))
    assert not rev["ok"]
    assert any("no source clause" in p for p in rev["problems"])


def test_review_requires_requested_evidence_on_a_check():
    goal = {"scenario": "s", "actions": [
        {"do": "search", "id": "s1", "term": "toy"}],
        "checks": [{"count": "results", "min": 1}]}
    exp = _exp_stub(goal, clauses=[
        {"id": "clause-1", "text": "search for toy", "line": 1,
         "evidence": True}])
    exp["coverage"] = [{"clause": "clause-1", "status": "action"}]
    rev = pe.review_contract(exp)
    assert not rev["ok"]
    assert any("screenshot" in p for p in rev["problems"])


# --- bounded cost / planner outcomes -------------------------------------------

_COMPLETE = ("1. go to shop.example.com\n2. search for wagon\n"
             "3. add the red wagon to cart\n"
             "4. verify the cart has the red wagon")


def test_complete_prompt_uses_zero_model_calls_and_one_probe(tmp_path,
                                                             monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    calls = {"probe": 0}

    def fake_probe(*a, **k):
        calls["probe"] += 1
        return {"pages": [], "errors": []}
    monkeypatch.setattr(core, "probe_page", fake_probe)
    monkeypatch.setattr("noodle.repl.prompt_expander.model_fallback",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("model fallback must not run")))
    res = core.author_test(prompt=_COMPLETE, workspace=str(tmp_path))
    assert calls["probe"] == 1
    assert res["planner"]["budgets"]["interpretation_model_calls"] == 0
    assert res["prompt_expansion"]["translation_mode"] == \
        "deterministic-fast-path"


def test_unresolved_prompt_makes_exactly_one_model_call(tmp_path,
                                                        monkeypatch):
    asks = []
    monkeypatch.setattr("noodle.llm.client.ask",
                        lambda p, system=None: asks.append(p) or "not json")
    monkeypatch.setenv("NOODLE_MODEL", "test-model")
    res = core.author_test(prompt="1. go to shop.example.com\n"
                                  "2. frobnicate the cart",
                           workspace=str(tmp_path))
    assert len(asks) == 1
    assert res["ok"] is False
    assert "JSON" in res["error"]
    assert res["planner"]["budgets"]["interpretation_model_calls"] == 1


def test_no_model_returns_needs_interpretation_without_calls(tmp_path,
                                                             monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    called = []
    monkeypatch.setattr("noodle.llm.client.ask",
                        lambda *a, **k: called.append(1))
    res = core.author_test(prompt="1. go to shop.example.com\n"
                                  "2. frobnicate the cart",
                           workspace=str(tmp_path))
    assert res["ok"] is False and res["needs_interpretation"]
    assert res["planner"]["state"] == "NEEDS_INTERPRETATION"
    assert res["unresolved"] and not called


def test_conflict_never_goes_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "test-model")
    called = []
    monkeypatch.setattr("noodle.llm.client.ask",
                        lambda *a, **k: called.append(1))
    res = core.author_test(prompt="1. go to shop.example.com\n"
                                  "2. search for toy\n3. add to cart\n"
                                  "4. verify laptop is added to cart",
                           workspace=str(tmp_path))
    assert res["ok"] is False and res["conflicts"] and not called
    assert res["planner"]["state"] == "CONTRACT_BLOCKED"


def test_planner_blocked_payload_names_the_unresolved_node(tmp_path,
                                                           monkeypatch):
    pgstub = {"url": "https://shop.example.com", "title": "",
              "controls": [], "headings": [], "revealed": []}
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: {"pages": [pgstub], "errors": []})
    res = core.author_test(prompt=_COMPLETE, workspace=str(tmp_path))
    assert res["planner"]["state"] == "EVIDENCE_MISSING"
    assert res["planner"]["unresolved"]
    assert res["planner"]["next_action"]


# --- model fallback (mocked ask) -------------------------------------------------

_MF_PROMPT = ("1. go to shop.example.com\n2. procure a toy\n"
              "3. add to cart\n4. verify cart has toy")

_MF_GOAL = {"scenario": "toy flow",
            "navigation": ["https://shop.example.com"],
            "dismissals": ["popups"],
            "actions": [
                {"do": "search", "id": "s1", "term": "toy"},
                {"do": "pick", "id": "p1", "from": "s1",
                 "strategy": "first_actionable"},
                {"do": "add_to", "id": "a1", "item_from": "p1",
                 "destination": "cart"}],
            "checks": [{"item_in_destination": "cart",
                        "expected_from": "p1", "after": "a1"}]}


def _mf_coverage():
    return {"clause-1": "navigation", "clause-2": "s1",
            "clause-3": "a1", "clause-4": "check"}


def test_model_fallback_valid_output_passes_the_same_review(monkeypatch):
    monkeypatch.setattr(
        "noodle.llm.client.ask",
        lambda p, system=None: json.dumps({"goal": _MF_GOAL,
                                           "coverage": _mf_coverage()}))
    exp = pe.model_fallback(_MF_PROMPT)
    assert exp["ok"], exp
    assert exp["translation_mode"] == "model-fallback"
    assert exp["base_url"] == "https://shop.example.com"
    assert all(i["provenance"] == "model-interpreted"
               for i in exp["inferences"])
    assert pe.review_contract(exp)["ok"]


def test_model_fallback_invalid_json_refused(monkeypatch):
    monkeypatch.setattr("noodle.llm.client.ask",
                        lambda p, system=None: "```json\n{broken\n```")
    exp = pe.model_fallback(_MF_PROMPT)
    assert not exp["ok"] and "JSON" in exp["error"]


def test_model_fallback_unknown_action_rejected(monkeypatch):
    bad = dict(_MF_GOAL, actions=[{"do": "warp"}], checks=[])
    monkeypatch.setattr(
        "noodle.llm.client.ask",
        lambda p, system=None: json.dumps({"goal": bad,
                                           "coverage": _mf_coverage()}))
    exp = pe.model_fallback(_MF_PROMPT)
    assert not exp["ok"] and "review" in exp["error"]


def test_model_fallback_missing_coverage_rejected(monkeypatch):
    cov = _mf_coverage()
    del cov["clause-2"]
    monkeypatch.setattr(
        "noodle.llm.client.ask",
        lambda p, system=None: json.dumps({"goal": _MF_GOAL,
                                           "coverage": cov}))
    exp = pe.model_fallback(_MF_PROMPT)
    assert not exp["ok"]
    assert any(u["clause"] == "clause-2" for u in exp["unresolved"])


def test_model_fallback_invented_click_rejected_as_orphan(monkeypatch):
    bad = dict(_MF_GOAL)
    bad["actions"] = _MF_GOAL["actions"] + [{"do": "click",
                                             "target": "click here"}]
    monkeypatch.setattr(
        "noodle.llm.client.ask",
        lambda p, system=None: json.dumps({"goal": bad,
                                           "coverage": _mf_coverage()}))
    exp = pe.model_fallback(_MF_PROMPT)
    assert not exp["ok"] and "no source clause" in exp["error"]


# --- probe fixtures: readiness, extraction, mutation proof -----------------------

def test_result_items_warning_is_typed():
    links = [{"tag": "a", "visible": True, "href": "/x", "text": "Toy"}]
    w = probe_mod.result_items_warning(1163, [], links)
    assert w["category"] == "positive-summary-without-items"
    assert w["summary_count"] == 1163
    assert w["raw_candidate_counts"]["visible_links"] == 1
    assert probe_mod.result_items_warning(1163, [{"caption": "x"}],
                                          links) is None
    assert probe_mod.result_items_warning(0, [], links) is None
    assert probe_mod.result_items_warning(None, [], links) is None


def test_result_items_carry_extraction_provenance():
    raw = [{"tag": "a", "visible": True, "href": f"/p/{i}",
            "text": f"Product {i}", "cls": "card-link"} for i in range(3)]
    items = probe_mod.build_result_items(raw)
    assert items
    for it in items:
        assert "repeated_structure" in it["why"]
        assert "stable_href" in it["why"]


def test_generic_pick_refuses_positive_summary_without_items():
    page = MagicMock()
    pg = {"search": {"term": "toy", "controls": [], "headings": [],
                     "result_items_warning": {
                         "category": "positive-summary-without-items",
                         "summary_count": 1163,
                         "raw_candidate_counts": {}}},
          "controls": [], "headings": []}
    probe_mod._pick(page, pg, "toy", "*", 1000)
    assert "1163" in pg["search"]["pick_warning"]
    assert "positive-summary-without-items" in pg["search"]["pick_warning"]
    page.locator.assert_not_called()


def test_pick_warning_maps_to_result_items_missing():
    goal = {"scenario": "s", "actions": [
        {"do": "search", "id": "s1", "term": "toy"},
        {"do": "pick", "id": "p1", "from": "s1",
         "strategy": "first_actionable"},
        {"do": "add_to", "id": "a1", "item_from": "p1",
         "destination": "cart"}],
        "checks": [{"item_in_destination": "cart", "expected_from": "p1",
                    "after": "a1"}]}
    probe = {"pages": [{
        "url": "u", "title": "", "controls": [], "headings": [],
        "revealed": [],
        "search": {"term": "toy", "controls": [], "headings": [],
                   "pick_warning": "results summary reports 1163 results "
                   "but no stable result item could be extracted "
                   "(positive-summary-without-items)"}}], "errors": []}
    ev = goal_mod.evidence(goal, probe)
    assert any(b.startswith("pick:") for b in ev["blocking"])
    assert goal_mod.next_action(ev["blocking"]) == "result_items_missing"


def _prereq(name, sel, **over):
    c = {"name": name, "selector": sel, "kind": "button", "visible": True}
    c.update(over)
    return c


def test_prereq_candidates_are_semantic_not_first_button():
    controls = [
        _prereq("Give feedback", "#f"),                  # excluded purpose
        _prereq("Sign in", "#s"),                        # excluded purpose
        _prereq("Menu", "#m", chrome=True),              # global chrome
        _prereq("Save", "#sub", submit=True),            # submit
        _prereq("Add to wishlist", "#w"),                # mutating verb
        _prereq("random control", "#r"),                 # no disclosure signal
        _prereq("Choose options", "#c"),                 # disclosure name
        _prereq("Colour", "#v", expanded="false"),       # ARIA state
    ]
    out = probe_mod._prereq_candidates(controls)
    assert [c["selector"] for c in out] == ["#c", "#v"]


def test_same_page_identity_ignores_query_and_fragment():
    same = probe_mod._same_page_identity
    assert same("https://x/p/1?a=b", "https://x/p/1#frag")
    assert same("https://x/p/1/", "https://x/p/1")
    assert not same("https://x/p/1",
                    "https://x/en/customer-service/right-to-repair.html")
    assert not same("https://x/p/1", "https://y/p/1")


def _mutation_page(urls):
    page = MagicMock()
    type(page).url = PropertyMock(side_effect=urls)
    return page


def test_prove_mutation_rejects_navigating_candidate(monkeypatch):
    monkeypatch.setattr(probe_mod, "_settle", lambda *a, **k: None)
    monkeypatch.setattr(probe_mod, "_arm", lambda p: None)
    page = _mutation_page(["https://x/p/1",
                           "https://x/en/customer-service/repair.html"])
    blk = {"controls": [_prereq("Choose options", "#c")], "headings": []}
    probe_mod._prove_mutation(page, blk, "cart", 1000)
    assert "mutation_path" not in blk
    page.goto.assert_called_once()
    assert page.goto.call_args[0][0] == "https://x/p/1"


def test_prove_mutation_success_records_url_evidence(monkeypatch):
    monkeypatch.setattr(probe_mod, "_settle", lambda *a, **k: None)
    monkeypatch.setattr(probe_mod, "_arm", lambda p: None)
    monkeypatch.setattr(
        probe_mod, "_diff_snapshot",
        lambda *a, **k: {"controls": [
            {"name": "Add to cart", "selector": "#atc", "kind": "button",
             "visible": True}], "headings": []})
    page = _mutation_page(["https://x/p/1", "https://x/p/1?opt=red",
                           "https://x/p/1?opt=red"])
    blk = {"controls": [_prereq("Choose options", "#c")], "headings": []}
    probe_mod._prove_mutation(page, blk, "cart", 1000)
    plan = blk["mutation_path"]
    assert plan["prerequisite"]["name"] == "Choose options"
    assert plan["control"]["selector"] == "#atc"
    assert plan["evidence"]["url_before"] == "https://x/p/1"
    assert plan["evidence"]["url_after"] == "https://x/p/1?opt=red"
    assert plan["evidence"]["revealed_selector"] == "#atc"


# --- navigation health ------------------------------------------------------------

def _health_page(url, status):
    return {"url": url, "title": "", "http_status": status, "controls": [],
            "headings": [], "revealed": []}


_NAV_GOAL = {"scenario": "s", "navigation": ["https://a/x", "https://a/y"],
             "actions": [], "checks": [], "allow_no_assertion": True}


def test_setup_404_warns_but_never_blocks():
    probe = {"pages": [_health_page("https://a/x", 404),
                       _health_page("https://a/y", 200)], "errors": []}
    ev = goal_mod.evidence(_NAV_GOAL, probe)
    setup = ev["navigation_health"][0]
    assert setup["role"] == "setup" and "warning" in setup
    assert not any(b.startswith("navigation") for b in ev["blocking"])


def test_final_action_page_404_blocks():
    probe = {"pages": [_health_page("https://a/x", 200),
                       _health_page("https://a/y", 404)], "errors": []}
    ev = goal_mod.evidence(_NAV_GOAL, probe)
    assert any("final action page" in b for b in ev["blocking"])
    assert goal_mod.next_action(ev["blocking"]) == "fix_navigation_contract"


def test_setup_page_controls_never_enter_action_vocabulary():
    setup = _health_page("https://a/x", 200)
    setup["controls"] = [{"name": "click here", "selector": "#ch",
                          "kind": "link", "visible": True}]
    probe = {"pages": [setup, _health_page("https://a/y", 200)],
             "errors": []}
    ev = goal_mod.evidence(_NAV_GOAL, probe)
    assert "click here" not in ev["controls"]


# --- repair provenance gate ---------------------------------------------------------

_FEATURE = ('@web\nFeature: f\n  Scenario: s\n'
            '    Given User is on "https://x.example"\n')


def test_write_feature_refuses_a_contracted_feature(tmp_path):
    rel = "tests/web/app/features/t.feature"
    core.save_state({"intent_contracts": {
        rel: {"blocked": True, "intent_verified": False}}}, str(tmp_path))
    res = core.write_feature(rel, _FEATURE, workspace=str(tmp_path))
    assert res["ok"] is False
    assert res["next_action"] == "fix_blocked_goal"
    assert not (tmp_path / rel).exists()


def test_write_feature_expert_override_still_writes(tmp_path):
    rel = "tests/web/app/features/t.feature"
    core.save_state({"intent_contracts": {
        rel: {"blocked": True, "intent_verified": False}}}, str(tmp_path))
    res = core.write_feature(rel, _FEATURE, workspace=str(tmp_path),
                             allow_unverified_intent=True)
    assert res["ok"] is True
    assert (tmp_path / rel).exists()


def test_write_feature_uncontracted_path_unchanged(tmp_path):
    rel = "tests/web/app/features/free.feature"
    res = core.write_feature(rel, _FEATURE, workspace=str(tmp_path))
    assert res["ok"] is True


# --- live acceptance (opt-in) ---------------------------------------------------------

@pytest.mark.skipif(not os.getenv("NOODLE_LIVE_ACCEPTANCE"),
                    reason="opt-in live retail acceptance — set "
                           "NOODLE_LIVE_ACCEPTANCE=1")
def test_live_exact_prompt_single_call_green(tmp_path):
    core.init_workspace(str(tmp_path))
    res = core.author_test(prompt=_FIXTURE, run_after_author=True,
                           workspace=str(tmp_path))
    author, run = res["author"], res["run"]
    assert author["ready"] and author["intent_verified"], author
    assert run["passed"] > 0 and run["failed"] == 0, run
    assert run.get("verified") is not False, run
    assert "click here" not in json.dumps(res).lower()

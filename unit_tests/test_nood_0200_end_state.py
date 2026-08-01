"""NOOD_0200 (cont.) — end-state evidence: stop validating the last page
against the first one.

The defect: `goal.evidence()` routed an UNANCHORED check (which NOOD_0158
compiles after the LAST action) to the landing-page snapshot. On a goal whose
actions carry the page past the probe's reach (fill → save → fill → submit,
or search → pick → add_to), that was wrong in both directions: it BLOCKED
end-state text that is genuinely on the post-submit page, and it PROVED text
that merely also exists on the landing page (footer/nav wording) — a green,
`verified: true` test that verified nothing.

Synthetic fixtures only — generic control names, no site/product/vertical
names anywhere (the domain-agnostic acceptance criterion). No browser, no
LLM, no network.
"""
import inspect

import pytest

from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl.goal import _beyond_probe_reach, _locate, compile_goal, evidence

BASE = "https://app.example.com/"

# A single-page gated form: a panel toggle reveals the fields; everything the
# ACTIONS touch is probe-proven, but the flow's enters/submits carry the page
# to a state the probe never opened.
_PG = {
    "url": BASE, "title": "portal",
    "headings": ["service portal", "welcome"],
    "controls": [
        {"name": "panel toggle", "selector": "#t", "kind": "button",
         "visible": True},
    ],
    "revealed": [
        {"revealed_by": "panel toggle", "headings": [],
         "controls": [
             {"name": "serial field", "selector": "#s", "kind": "input",
              "visible": True},
             {"name": "save button", "selector": "#sv", "kind": "button",
              "visible": True},
             {"name": "id field", "selector": "#i", "kind": "input",
              "visible": True},
             {"name": "secret field", "selector": "#p", "kind": "input",
              "visible": True},
             {"name": "submit button", "selector": "#sub", "kind": "button",
              "visible": True, "submit": True},
         ]},
    ],
}


def _gated_goal(checks):
    return {
        "scenario": "gated flow end state",
        "navigation": [BASE],
        "actions": [
            {"do": "click", "id": "open1", "target": "panel toggle"},
            {"do": "enter", "id": "e1", "target": "serial field",
             "value": "123"},
            {"do": "click", "id": "save1", "target": "save button"},
            {"do": "enter", "id": "e2", "target": "id field", "value": "u"},
            {"do": "enter", "id": "e3", "target": "secret field",
             "value": "s"},
            {"do": "click", "id": "sub1", "target": "submit button"},
        ],
        "checks": checks,
    }


def _probe():
    return {"pages": [dict(_PG)]}


# --- AC2.1 — unanchored checks on a gated flow are run-proven, never blocked


def test_end_state_checks_route_to_the_run_not_to_blocking():
    ev = evidence(_gated_goal([{"see": "workspace ready"}]), _probe())
    assert ev["blocking"] == []
    assert any("workspace ready" in s for s in ev["runtime_asserted"])
    assert not any("see" in k for k in ev["proven"])


# --- AC2.2 — the false-positive guard: landing-page wording must not "prove"
# an end-state check


def test_landing_page_text_cannot_prove_an_end_state_check():
    """'service portal' IS on the landing page (heading). Before the fix the
    evidence pass matched it there and shipped a green that asserted it on
    the post-submit page — the serious direction of the bug."""
    ev = evidence(_gated_goal([{"see": "service portal"}]), _probe())
    assert "see:service portal" not in ev["proven"]
    assert any("service portal" in s for s in ev["runtime_asserted"])
    assert ev["blocking"] == []


# --- AC2.3 — `after: start` still pins a check to the landing page


def test_after_start_keeps_landing_page_proof():
    ev = evidence(_gated_goal([{"see": "service portal", "after": "start"}]),
                  _probe())
    assert ev["proven"].get("see:service portal") == "service portal"
    assert ev["proven_phase"].get("see:service portal") == "initial"
    assert ev["blocking"] == []


# --- AC2.4 — flows the probe genuinely reached keep pre-run verification


def test_search_only_goal_keeps_probe_proof():
    goal = {"scenario": "find widgets", "navigation": [BASE],
            "actions": [{"do": "search", "id": "s1", "term": "widget"}],
            "checks": [{"see": "widget results"}]}
    pg = dict(_PG)
    pg["search"] = {"term": "widget", "headings": ["widget results"],
                    "controls": []}
    ev = evidence(goal, {"pages": [pg]})
    assert ev["proven"].get("see:widget results") == "widget results"
    assert ev["proven_phase"].get("see:widget results") == "search"
    assert not any("widget results" in s for s in ev["runtime_asserted"])


@pytest.mark.parametrize("actions,beyond", [
    ([{"do": "search", "term": "x"}], False),
    ([{"do": "suggest", "term": "x", "option": "y"}], False),
    ([{"do": "search", "term": "x"}, {"do": "pick"}], False),
    ([{"do": "search", "term": "x"}, {"do": "pick"},
      {"do": "add_to", "destination": "cart"}], True),
    ([{"do": "click", "target": "a"}, {"do": "enter", "target": "b",
      "value": "v"}, {"do": "click", "target": "c"}], True),
])
def test_beyond_probe_reach_reads_the_action_grammar_only(actions, beyond):
    assert _beyond_probe_reach(actions) is beyond


# --- Fix 2 — blocking messages name a legal next input


def test_a_missed_check_names_its_repairs():
    goal = {"scenario": "static page", "navigation": [BASE],
            "actions": [], "checks": [{"see": "text that is nowhere"}]}
    ev = evidence(goal, _probe())
    assert len(ev["blocking"]) == 1
    msg = ev["blocking"][0]
    assert "no probed heading or control shows that text" in msg
    assert "after: start" in msg          # the once-undiscoverable escape
    assert "the run then proves it" in msg


def test_ambiguity_names_the_candidate_it_refuses_to_guess():
    controls = [{"name": "toy blaster", "selector": "#a", "visible": True},
                {"name": "toy rocket", "selector": "#b", "visible": True}]
    blocks = [({"controls": controls, "headings": []}, "initial", None)]
    _, _, _, note = _locate("toy", blocks)
    assert "ambiguous" in note
    # NOOD_0209 — candidates are ranked closest-first now, not listed in
    # probe order; the note must still LEAD with a concrete candidate.
    assert 'if you meant "toy rocket"' in note


# --- Fix 6 — compiled POM is pinned to its own feature, never folder-global


def test_compiled_pom_is_page_scoped_and_feature_carries_the_pin():
    goal = _gated_goal([{"see": "workspace ready"}])
    ev = evidence(goal, _probe())
    assert ev["blocking"] == []
    feature, pom = compile_goal(goal, ev, "APP")
    assert feature.splitlines()[0] == "@web @page:gated_flow_end_state"
    assert pom is not None
    assert "match: {}" not in pom
    assert "pages:" in pom
    assert "  gated_flow_end_state:" in pom.splitlines()[2]


def test_the_pin_resolves_the_scoped_pom_and_siblings_stay_invisible(tmp_path):
    """End to end through the real resolver: the pinned scenario sees its own
    compiled block; an unpinned sibling scenario does not — so two compiled
    features with the same key names can no longer shadow each other."""
    from noodle.agents.web import pom as pom_module
    pod = tmp_path / "features"
    (tmp_path / "resources" / "pageobjects").mkdir(parents=True)
    pod.mkdir()
    (tmp_path / "resources" / "pageobjects" / "flow_a_pom.yaml").write_text(
        "pages:\n  flow_a:\n    save button:\n      css: '#a'\n",
        encoding="utf-8")
    (tmp_path / "resources" / "pageobjects" / "flow_b_pom.yaml").write_text(
        "pages:\n  flow_b:\n    save button:\n      css: '#b'\n",
        encoding="utf-8")
    pom_module.set_context(str(pod))
    try:
        pom_module.set_active_page("flow_b")
        assert pom_module._lookup("save button", BASE) == {"css": "#b"}
        pom_module.set_active_page("flow_a")
        assert pom_module._lookup("save button", BASE) == {"css": "#a"}
        pom_module.set_active_page(None)
        assert pom_module._lookup("save button", BASE) is None
    finally:
        pom_module.set_active_page(None)
        pom_module.set_context(None)
        pom_module._load_yaml.cache_clear()


# --- Fix 3 — provenance rides the payload


def test_proven_phase_is_returned_by_evidence():
    ev = evidence(_gated_goal([{"see": "service portal",
                                "after": "start"}]), _probe())
    assert ev["proven_phase"] == {"see:service portal": "initial"}


# --- Fix 4 — a blocked author still writes the report pair


def test_write_authoring_blocked_renders_both_reports(tmp_path):
    from noodle.reporting import rca_report
    rca_report.write_authoring_blocked(tmp_path / "reports", {
        "feature": "features/gated.feature",
        "blocking": ['check "workspace ready": no probed heading …'],
        "next_action": "fix_blocked_goal",
        "intent_trace": [{"ok": False, "node": "check"}]})
    md = (tmp_path / "reports" / "rca.md").read_text(encoding="utf-8")
    page = (tmp_path / "reports" / "rca.html").read_text(encoding="utf-8")
    assert "authoring blocked" in md and "workspace ready" in md
    assert "authoring blocked" in page and "fix_blocked_goal" in page
    assert "No run happened" in md


def test_blocked_author_path_calls_the_writer():
    src = inspect.getsource(core._author_test_impl)
    assert "write_authoring_blocked" in src


# --- Fix 5 (bounded slice) — no 30s dispatch hang in the probe


def test_probe_dispatch_events_are_time_bounded():
    from noodle.agents.web import probe
    src = inspect.getsource(probe)
    assert 'dispatch_event("click")' not in src
    assert 'dispatch_event("click", timeout=' in src


# --- the docstring rule that holds it together


def test_beyond_probe_reach_is_domain_agnostic():
    """The classifier reads the action grammar only — no site, product,
    field or vertical name may ever creep into it (AC3)."""
    src = inspect.getsource(goal_mod._beyond_probe_reach)
    assert "action grammar" in src.lower()

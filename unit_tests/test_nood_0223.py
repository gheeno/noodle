"""NOOD_0223 — remove the manual patch paths.

Every item here closes one place where the engine handed back a problem and
the caller's cheapest next move was to edit a file by hand:

* workspace_policy — the engine now knows which bytes it wrote, so a hand
  edit is a fact it can state instead of silently discarding.
* repair_goal's two new shapes — a target the page names differently, and a
  control behind a trigger, are both cured from probe evidence the blocker
  was already carrying.
* to_spec_yaml — an ambiguous prompt comes back as the next call's argument,
  not as prose to translate.
* _autofix_run — a red run gets an engine lap, bounded and narrow, instead of
  an RCA plus a fix-it-yourself instruction.
* probe's goal_skeleton — the handoff to author is a paste, not a
  transcription.

The negative tests matter more than the positive ones. Each repair is the
engine changing a test on the user's behalf, and the standing lesson from
NOOD_0212 is that a rule which guesses eats real assertions — so every one of
these declines when the evidence is not conclusive.
"""
import json

import pytest
import yaml

from noodle import config, workspace_policy
from noodle.agents.web import probe
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.reporting import rca_report

# --- item 1: workspace strict mode -------------------------------------------

def _ws(tmp_path):
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n")
    f = tmp_path / "noodle_tests" / "app" / "features"
    f.mkdir(parents=True)
    feat = f / "flow.feature"
    feat.write_text("Feature: F\n  Scenario: S\n    Given x\n")
    return feat


def test_manifest_records_and_detects_a_hand_edit(tmp_path):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    assert workspace_policy.drift(str(tmp_path)) == []
    feat.write_text("Feature: F\n  Scenario: S\n    Given hand-edited\n")
    assert workspace_policy.drift(str(tmp_path)) == [
        {"path": "noodle_tests/app/features/flow.feature", "kind": "modified"}]


def test_a_deleted_engine_file_is_drift_too(tmp_path):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    feat.unlink()
    assert workspace_policy.drift(str(tmp_path))[0]["kind"] == "deleted"


def test_untracked_files_never_count_as_drift(tmp_path):
    """A workspace scaffolded by `noodle init` ships features the engine never
    authored. A mode whose first act is to condemn the scaffold gets switched
    off before it ever catches a real edit."""
    _ws(tmp_path)                      # written, never recorded
    assert workspace_policy.drift(str(tmp_path)) == []


def test_strict_is_off_by_default_and_arms_three_ways(tmp_path, monkeypatch):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    feat.write_text("edited\n")
    monkeypatch.delenv("NOODLE_WORKSPACE_STRICT", raising=False)
    assert workspace_policy.gate(str(tmp_path))["ok"] is True     # default off
    assert workspace_policy.gate(str(tmp_path), True)["ok"] is False   # flag
    monkeypatch.setenv("NOODLE_WORKSPACE_STRICT", "1")
    assert workspace_policy.gate(str(tmp_path))["ok"] is False         # env
    monkeypatch.delenv("NOODLE_WORKSPACE_STRICT")
    (tmp_path / "noodle.yaml").write_text(
        "tests_dir: noodle_tests\nworkspace_strict: true\n")
    assert workspace_policy.gate(str(tmp_path))["ok"] is False         # yaml


def test_an_explicit_false_beats_the_workspace_opt_in(tmp_path):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    feat.write_text("edited\n")
    (tmp_path / "noodle.yaml").write_text(
        "tests_dir: noodle_tests\nworkspace_strict: true\n")
    assert workspace_policy.gate(str(tmp_path), False)["ok"] is True


def test_strict_block_names_the_file_and_the_way_back(tmp_path):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    feat.write_text("edited\n")
    err = workspace_policy.gate(str(tmp_path), True)["error"]
    assert "flow.feature" in err and "noodle author" in err


def test_workspace_strict_is_a_known_config_key():
    """config.load warns on unknown keys — the policy must not trip its own
    typo guard."""
    assert config.DEFAULTS["workspace_strict"] is False


def test_run_test_refuses_before_launching_a_browser(tmp_path, monkeypatch):
    feat = _ws(tmp_path)
    workspace_policy.record(str(tmp_path), [feat])
    feat.write_text("edited\n")

    def _boom(*a, **k):                      # the browser must never start
        raise AssertionError("run launched despite workspace drift")

    monkeypatch.setattr(core, "_engine", _boom)
    r = core.run_test("x", workspace=str(tmp_path), workspace_strict=True)
    assert r["ok"] is False and r["workspace_drift"]


def test_a_corrupt_manifest_reads_as_no_ownership(tmp_path):
    """Never a false accusation: an unreadable manifest means the engine owns
    nothing, not that everything drifted."""
    _ws(tmp_path)
    (tmp_path / ".noodle").mkdir(exist_ok=True)
    (tmp_path / ".noodle" / "authored.json").write_text("{not json")
    assert workspace_policy.load(str(tmp_path)) == {}
    assert workspace_policy.gate(str(tmp_path), True)["ok"] is True


# --- item 4: action targets reworded to the probed control name --------------

def _goal(actions, checks=None):
    return {"scenario": "s", "navigation": ["https://e.test/"],
            "actions": list(actions), "checks": checks or [{"see": "Cart"}]}


NEAR = ' — did you mean "Add to cart"? (probed controls here: "Add to cart")'


def test_action_target_is_reworded_to_the_probed_name():
    g = _goal([{"do": "click", "target": "add to order"}])
    rep = goal_mod.repair_goal(
        g, ['click "add to order": no probed control matches that name' + NEAR])
    assert rep["rewritten_targets"] == [{"from": "add to order",
                                         "to": "Add to cart"}]
    assert rep["goal"]["actions"] == [{"do": "click", "target": "Add to cart"}]


def test_a_reword_keeps_every_asked_for_check():
    g = _goal([{"do": "click", "target": "add to order"}],
              [{"see": "Cart"}, {"count": "results", "min": 1}])
    rep = goal_mod.repair_goal(
        g, ['click "add to order": no probed control matches that name' + NEAR])
    assert rep["goal"]["checks"] == g["checks"]
    assert rep["dropped_checks"] == []


def test_no_near_miss_means_no_reword():
    """Without a probed twin there is nothing to rewrite TO, and inventing one
    is how a test ends up driving the wrong control."""
    g = _goal([{"do": "click", "target": "add to order"}])
    assert goal_mod.repair_goal(
        g, ['click "add to order": no probed control matches that name']) is None


def test_only_the_named_action_is_reworded():
    g = _goal([{"do": "click", "target": "Search"},
               {"do": "click", "target": "add to order"}])
    rep = goal_mod.repair_goal(
        g, ['click "add to order": no probed control matches that name' + NEAR])
    assert rep["goal"]["actions"][0] == {"do": "click", "target": "Search"}


# --- item 3: the prerequisite click the probe proved -------------------------

HIDDEN = ('click "Sign out": hidden until "Account menu" is opened — '
          "add a click on it before this action")
DISCOVERED = ('click "Sign out": only found via automatic discovery '
              '(revealed by "Account menu"), not by a requested action — '
              "add an explicit click that opens it before this action")


@pytest.mark.parametrize("blocker", [HIDDEN, DISCOVERED])
def test_the_trigger_click_is_inserted_before_the_blocked_action(blocker):
    g = _goal([{"do": "click", "target": "Sign out"}])
    rep = goal_mod.repair_goal(g, [blocker])
    assert rep["prerequisite_clicks"] == [{"click": "Account menu",
                                           "before": "Sign out"}]
    assert rep["goal"]["actions"] == [{"do": "click", "target": "Account menu"},
                                      {"do": "click", "target": "Sign out"}]


def test_the_insert_lands_at_the_blocked_action_not_at_the_front():
    g = _goal([{"do": "search", "term": "x"},
               {"do": "click", "target": "Sign out"}])
    rep = goal_mod.repair_goal(g, [HIDDEN])
    assert [a.get("do") for a in rep["goal"]["actions"]] == \
        ["search", "click", "click"]
    assert rep["goal"]["actions"][1]["target"] == "Account menu"


def test_an_existing_trigger_click_is_not_duplicated():
    g = _goal([{"do": "click", "target": "Account menu"},
               {"do": "click", "target": "Sign out"}])
    assert goal_mod.repair_goal(g, [HIDDEN]) is None


def test_a_hover_already_counts_as_opening_the_trigger():
    """NOOD_0188 — hover menus are the other way a control legitimately
    becomes reachable; adding a click on top would change the flow."""
    g = _goal([{"do": "hover", "target": "Account menu"},
               {"do": "click", "target": "Sign out"}])
    assert goal_mod.repair_goal(g, [HIDDEN]) is None


# --- the shared gate: nothing unexplained may ride along ---------------------

def test_one_unexplained_blocker_withdraws_the_whole_repair():
    """A repair is offered only when it accounts for EVERY blocker. Half a fix
    that authors clean is worse than a block: it runs, and it proves less than
    was asked."""
    g = _goal([{"do": "click", "target": "Sign out"}])
    assert goal_mod.repair_goal(
        g, [HIDDEN, 'click "Other": ambiguous — 7 controls share that name']) \
        is None


def test_a_goal_without_actions_does_not_acquire_an_empty_list():
    g = {"scenario": "s", "navigation": ["https://e.test/"],
         "checks": [{"see": "Cart"}, {"see": "Ghost"}]}
    rep = goal_mod.repair_goal(
        g, ['check "Ghost": no probed heading or control shows that text'])
    assert "actions" not in rep["goal"]


def test_action_and_check_repairs_compose_in_one_lap():
    g = _goal([{"do": "click", "target": "add to order"}],
              [{"see": "Cart"}, {"see": "Ghost"}])
    rep = goal_mod.repair_goal(g, [
        'click "add to order": no probed control matches that name' + NEAR,
        'check "Ghost": no probed heading or control shows that text'])
    assert rep["rewritten_targets"] and rep["dropped_checks"] == ["Ghost"]
    assert rep["goal"]["checks"] == [{"see": "Cart"}]


# --- item 5: the machine rewrite ---------------------------------------------

UNRESOLVED = [{"clause": "step-4", "text": "wiggle the thing",
               "reason": "outside the supported grammar",
               "suggested": 'click "thing"'}]


# --- narration that carries its own expected values --------------------------
#
# Found by the NOOD_0223 benchmark: the table form of a brief compiled one
# check per value (NOOD_0199) while the same brief written inline with quotes
# compiled NONE — and authored a green test with zero assertions. Two
# notations for one intent disagreed, and the silent one won.

BASE = '1. go to https://shop.example/\n2. search for "Vaccu"\n'


def _prompt_checks(step):
    from noodle.repl import prompt_expander as pe
    r = pe.expand(BASE + f"3. {step}\n")
    assert r["ok"], r.get("error")
    return r["goal"]["checks"]


def test_quoted_values_inside_narration_are_asserted():
    assert _prompt_checks(
        'Then the results page appears with these products: "Alpha Widget" '
        'and "Beta Widget"') == [{"see": "Alpha Widget"},
                                 {"see": "Beta Widget"}]


def test_a_single_quoted_value_inside_narration_counts_too():
    assert _prompt_checks('Then the results page shows "Alpha Widget"') == \
        [{"see": "Alpha Widget"}]


def test_the_table_form_still_compiles_the_same_checks():
    """The two notations must agree — that they didn't is the whole defect."""
    from noodle.repl import prompt_expander as pe
    r = pe.expand(BASE + "3. Then the results page appears with these products"
                         "\n   | Alpha Widget |\n   | Beta Widget |\n")
    assert r["goal"]["checks"] == [{"see": "Alpha Widget"},
                                   {"see": "Beta Widget"}]


def test_unquoted_narration_is_still_dropped():
    """NOOD_0199 intact: scene-setting between two real steps carries no
    assertion, and inventing one from prose is how a test asserts a sentence
    no page renders."""
    assert _prompt_checks(
        "Then a suggestion bar appears below the search bar") == []


def test_a_quote_inside_the_narration_lead_is_not_an_assertion():
    """Only spans AFTER the narration verb are values the narration
    introduces. A quote in the lead names the instrument, not page text."""
    from noodle.repl import prompt_expander as pe
    r = pe.expand(BASE + '3. Then the "search" results page appears\n')
    assert (r["goal"]["checks"] if r["ok"] else []) == []


def test_the_rewrite_of_narration_is_announced():
    """An engine that turns framing into assertions owes the reader a line
    saying which text it decided to assert."""
    from noodle.repl import prompt_expander as pe
    r = pe.expand(BASE + '3. Then the results page shows "Alpha Widget"\n')
    assert any("narration introducing 1 quoted value" in a
               for a in r["assumptions"])


def test_the_rewrite_is_a_valid_spec_document():
    doc = goal_mod.to_spec_yaml(
        {"scenario": "partial", "navigation": ["https://e.test/"],
         "actions": [{"do": "search", "term": "vac"}]}, UNRESOLVED)
    parsed = yaml.safe_load(doc)
    assert parsed["goal"]["actions"] == [{"do": "search", "term": "vac"}]


def test_unresolved_clauses_ride_as_comments_not_as_yaml():
    """They must be visible to the reader and invisible to the parser — that
    is what makes the document both a diagnosis and the next call's argument."""
    doc = goal_mod.to_spec_yaml({"scenario": "partial"}, UNRESOLVED)
    assert "# UNRESOLVED step 4: 'wiggle the thing'" in doc
    assert 'suggested: click "thing"' in doc
    assert "wiggle" not in yaml.safe_dump(yaml.safe_load(doc))


def test_a_fully_resolved_goal_carries_no_comment_block():
    doc = goal_mod.to_spec_yaml({"scenario": "s"}, [])
    assert not doc.startswith("#")


def test_a_blocked_prompt_returns_the_paste_ready_document(tmp_path):
    r = core.author_test(prompt="1. go to https://e.test/\n2. wiggle the thing\n",
                         workspace=str(tmp_path))
    assert r["ok"] is False
    assert "goal_yaml" in r, "a partial parse must come back as a spec"
    doc = yaml.safe_load(r["goal_yaml"])
    assert doc["goal"]["navigation"] == ["https://e.test"]
    assert "wiggle the thing" in r["goal_yaml"], \
        "the clause the engine could not place must be named in the document"


# --- item 2: the bounded runtime self-heal lap -------------------------------

def _entry(message, warnings=()):
    return {"message": message, "trace": "", "warnings": list(warnings),
            "step": "Then User should see 'Welcome to Wikipedia'"}


NEAR_MISS_MSG = ("Expected to see 'Welcome to Wikipedia' on page — not found. "
                 "[near-miss] the page renders: 'Welcome to Wikipedia,'; "
                 "'the free encyclopedia'")


def test_assertion_rewrite_extracts_what_the_page_rendered():
    r = rca_report.assertion_rewrite(_entry(NEAR_MISS_MSG))
    assert r["expected"] == "Welcome to Wikipedia"
    assert r["rendered"][0] == "Welcome to Wikipedia,"


def test_assertion_rewrite_and_classify_agree():
    """One pattern, read twice — a second copy would drift into an auto-fix
    that rewrites an assertion the report calls fine."""
    e = _entry(NEAR_MISS_MSG)
    assert rca_report.classify(e)["category"] == "assertion-wording"
    assert rca_report.assertion_rewrite(e) is not None


def test_assertion_rewrite_is_none_without_a_near_miss():
    assert rca_report.assertion_rewrite(
        _entry("Expected to see 'X' on page — not found.")) is None


def test_the_lap_rewrites_only_the_check_the_run_named():
    g = _goal([], [{"see": "Welcome to Wikipedia"}, {"see": "Main Page"}])
    fixed, applied = core._autofix_goal(
        g, [{"expected": "Welcome to Wikipedia",
             "rendered": ["Welcome to Wikipedia,"]}])
    assert applied == [{"from": "Welcome to Wikipedia",
                        "to": "Welcome to Wikipedia,"}]
    assert fixed["checks"][1] == {"see": "Main Page"}


def test_a_rewrite_matching_no_check_changes_nothing():
    g = _goal([], [{"see": "Main Page"}])
    fixed, applied = core._autofix_goal(
        g, [{"expected": "Something else", "rendered": ["Whatever"]}])
    assert applied == [] and fixed["checks"] == g["checks"]


def _red(monkeypatch, categories, tmp_path, rewrites=()):
    """A red atomic result plus a stubbed classifier — no browser, no run."""
    monkeypatch.setattr(rca_report, "collect",
                        lambda d=None: [{"n": i} for i in range(len(categories))])
    cats = list(categories)
    monkeypatch.setattr(rca_report, "classify",
                        lambda e: {"category": cats[e["n"]]})
    monkeypatch.setattr(rca_report, "assertion_rewrite",
                        lambda e: list(rewrites)[e["n"]]
                        if e["n"] < len(rewrites) else None)
    return {"ok": False, "author": {"ready": True},
            "run": {"ok": False, "failed": 1}}


def test_zero_laps_is_the_default_and_adds_no_audit(tmp_path, monkeypatch):
    r = _red(monkeypatch, ["locator-rot"], tmp_path)
    out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=0)
    assert "auto_fix" not in out


def test_an_app_side_failure_never_gets_a_lap(tmp_path, monkeypatch):
    """Re-authoring against a broken app is the green-forcing retry loop
    config.dev_fix_attempts forbids."""
    for cat in ("app-regression", "app-rejected-action", "test-data",
                "environment-flap", "navigation-mismatch", "mutation-failed",
                "unknown"):
        r = _red(monkeypatch, [cat], tmp_path)
        monkeypatch.setattr(core, "_author_test_impl", lambda **k: pytest.fail(
            f"{cat} must not trigger a re-author lap"))
        out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                                feature_path="f",
                                kw={"workspace": str(tmp_path)}, max_laps=1)
        assert out["auto_fix"]["laps_used"] == 0
        assert cat in out["auto_fix"]["skipped"]


def test_one_app_side_failure_blocks_the_whole_run(tmp_path, monkeypatch):
    """A mixed run is a red run. Papering over the fixable half would ship a
    green report for a broken app."""
    r = _red(monkeypatch, ["assertion-wording", "app-regression"], tmp_path,
             rewrites=[{"expected": "Cart", "rendered": ["Basket"]}])
    monkeypatch.setattr(core, "_author_test_impl", lambda **k: pytest.fail(
        "a mixed run must not be auto-fixed"))
    out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=1)
    assert out["auto_fix"]["laps_used"] == 0


def test_a_lap_reauthors_and_records_what_the_engine_edited(tmp_path,
                                                            monkeypatch):
    r = _red(monkeypatch, ["assertion-wording"], tmp_path,
             rewrites=[{"expected": "Cart", "rendered": ["Basket"]}])
    seen = {}

    def _impl(**kw):
        seen["goal"] = kw["goal"]
        seen["overwrite"] = kw["overwrite"]
        return {"ok": True, "author": {"ready": True, "feature": "f.feature",
                                       "pom": "f_pom.yaml"},
                "run": {"ok": True, "passed": 1, "failed": 0}}

    monkeypatch.setattr(core, "_author_test_impl", _impl)
    out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=1)
    assert seen["overwrite"] is True, "a lap must replace, never stack"
    assert seen["goal"]["checks"] == [{"see": "Basket"}]
    assert out["run"]["ok"] is True
    audit = out["auto_fix"]
    assert audit["laps_used"] == 1
    assert audit["engine_edits"] == ["f.feature", "f_pom.yaml"]
    assert audit["laps"][0]["rewritten_checks"] == [{"from": "Cart",
                                                     "to": "Basket"}]


def test_laps_stop_at_the_bound(tmp_path, monkeypatch):
    r = _red(monkeypatch, ["locator-rot"], tmp_path)
    calls = []

    def _impl(**kw):
        calls.append(1)
        return {"ok": False, "author": {"ready": True, "feature": "f.feature"},
                "run": {"ok": False, "failed": 1}}

    monkeypatch.setattr(core, "_author_test_impl", _impl)
    out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=2)
    assert len(calls) == 2 and out["auto_fix"]["laps_used"] == 2


def test_feature_content_mode_declines_with_a_reason(tmp_path, monkeypatch):
    r = _red(monkeypatch, ["locator-rot"], tmp_path)
    out = core._autofix_run(r, goal=None, app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=1)
    assert "nothing to recompile" in out["auto_fix"]["skipped"]


def test_a_green_run_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(rca_report, "collect", lambda d=None: pytest.fail(
        "a green run must not be classified"))
    out = core._autofix_run({"ok": True, "run": {"ok": True}}, goal=_goal([]),
                            app_name="a", base_url="u", feature_path="f",
                            kw={"workspace": str(tmp_path)}, max_laps=1)
    assert out["auto_fix"]["laps_used"] == 0


def test_a_lap_that_authors_dirty_keeps_the_original_diagnosis(tmp_path,
                                                               monkeypatch):
    r = _red(monkeypatch, ["locator-rot"], tmp_path)
    monkeypatch.setattr(core, "_author_test_impl",
                        lambda **k: {"ok": False,
                                     "author": {"ready": False,
                                                "blocking": ["nope"]}})
    out = core._autofix_run(r, goal=_goal([]), app_name="a", base_url="u",
                            feature_path="f", kw={"workspace": str(tmp_path)},
                            max_laps=1)
    assert out["run"]["failed"] == 1        # the ORIGINAL run, not the retry
    assert "does not author cleanly" in out["auto_fix"]["skipped"]


# --- item 6: the probe's runnable goal skeleton ------------------------------

PROBED = {"url": "https://e.test/", "permission_prompts": ["geolocation"],
          "popups_closed": 1,
          "search": {"term": "vac", "results_summary": {"text": "12 results"}},
          "expect": [{"text": "Hoover", "found": True},
                     {"text": "Ghost", "found": False}]}


def test_the_skeleton_is_a_goal_the_engine_accepts():
    skel = probe._goal_skeleton(PROBED, [("click", "Add to cart", None)])
    skel["scenario"] = "probed flow"
    normalized, _ = goal_mod.normalize(skel)
    assert goal_mod.validate(normalized) == []


def test_the_skeleton_speaks_goal_notation_not_gherkin():
    skel = probe._goal_skeleton(PROBED, [("click", "Add to cart", None)])
    assert skel["navigation"] == ["https://e.test/"]
    assert skel["dismissals"] == ["location_prompt", "popups"]
    assert {"do": "search", "term": "vac"} in skel["actions"]
    assert {"do": "click", "target": "Add to cart"} in skel["actions"]


def test_every_do_verb_maps_to_its_goal_action():
    skel = probe._goal_skeleton(
        {"url": "https://e.test/"},
        [("click_row", "Add", "Widget"), ("enter", "Email", "a@b.c"),
         ("select", "Size", "Large")])
    assert skel["actions"] == [
        {"do": "click", "target": "Add", "within": "Widget"},
        {"do": "enter", "target": "Email", "value": "a@b.c"},
        {"do": "select", "target": "Size", "option": "Large"}]


def test_only_proven_checks_ride_the_skeleton():
    """An invented check is worse than no check, because it passes."""
    skel = probe._goal_skeleton(PROBED, None)
    assert {"see": "Hoover"} in skel["checks"]
    assert {"see": "Ghost"} not in skel["checks"]


def test_a_results_page_gets_the_stable_floor_not_the_snapshot():
    skel = probe._goal_skeleton(PROBED, None)
    assert {"count": "results", "min": 1} in skel["checks"]
    assert not any("12" in json.dumps(c) for c in skel["checks"])


def test_a_typeahead_pick_is_one_action_not_two_steps():
    skel = probe._goal_skeleton(
        {"url": "https://e.test/",
         "suggest": {"term": "Vaccu", "suggestions": ["Vacuum cleaner"]}}, None)
    assert skel["actions"] == [{"do": "suggest", "term": "Vaccu",
                                "option": "Vacuum cleaner"}]


def test_the_skeleton_rides_only_the_author_ready_payload():
    """A blocked probe must never hand over a 'runnable' skeleton that isn't —
    the producer sets the key only on the ready path, so a compact payload
    without it stays without it."""
    page = {"url": PROBED["url"], "title": "T", "controls": [], "headings": []}
    out = probe._compact_page({**page, "author_ready": False}, 25, False)
    assert "goal_skeleton" not in out
    out = probe._compact_page({**page, "author_ready": True,
                               "goal_skeleton": {"navigation": ["u"]}},
                              25, False)
    assert out["goal_skeleton"] == {"navigation": ["u"]}


# --- item 7: ship is an alias, not a second implementation -------------------

def test_ship_delegates_to_the_same_author_door(monkeypatch):
    from typer.testing import CliRunner

    from noodle.cli import app
    seen = {}

    def _fake(**kw):
        seen.update(kw)
        return {"ok": True, "author": {"ready": True, "feature": "f"},
                "run": {"ok": True, "passed": 1, "failed": 0}}

    monkeypatch.setattr(core, "author_test", _fake)
    monkeypatch.setattr(core, "collapse_green", lambda r, workspace=".": r)
    res = CliRunner().invoke(app, ["ship", "1. go to https://e.test/"])
    assert res.exit_code == 0
    assert seen["run_after_author"] is True
    assert seen["auto_fix"] == 1, "ship's whole point is the bounded lap"
    assert seen["overwrite"] is True, "re-shipping an AC is the expected motion"


def test_ship_is_thin(monkeypatch):
    """If this ever grows its own pipeline it becomes a second thing to keep
    honest. It may call author_test and format the result — nothing else."""
    import inspect

    from noodle import cli
    body = inspect.getsource(cli.ship)
    assert body.count("core.") == 2      # author_test + collapse_green
    assert "_author_test_impl" not in body and "run_and_report" not in body

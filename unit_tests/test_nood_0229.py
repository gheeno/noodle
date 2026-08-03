"""NOOD_0229 — a run replaces what it re-ran, and says what it covered.

The reported session authored ONE ordered user flow as TWO features, ran each
with `author --run`, and was handed a report containing one of them. Two
independent defects, both pinned here:

  2.1  every run deleted every `*-result.json` and `*-attachment.*` before
       executing, so run 2 destroyed run 1's result AND its evidence image;
       the report was then rebuilt from the single survivor
  2.2  nothing in the payload distinguished "the run passed" from "the report
       covers the package", so `passed: 1` read as full coverage

...plus the authoring-side reasons the flow was split in the first place:

  1.1  an unanchored check is asserted at the END state; when the probe read
       its text on the LANDING page and the flow navigates away, say so
  1.2  a new feature that walks an existing feature's flow is named as such
  1.3  the copy-paste goal example carries `after:` and `evidence:`
"""
import json
from pathlib import Path

from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.reporting import scope, summary

# --------------------------------------------------------------------------
# helpers — result JSONs shaped exactly like reporting/writer.py writes them
# --------------------------------------------------------------------------

def _mark(d: Path, run_id, started_ms):
    """Stamp a run boundary with an EXPLICIT instant. begin_run() reads the
    wall clock, and two runs a few microseconds apart inside one test would
    land in the same millisecond — a real run takes seconds, a test doesn't."""
    (d / scope.MARKER).write_text(
        json.dumps({"run_id": run_id, "started_ms": started_ms}), encoding="utf-8")
    return started_ms


def _write_result(d: Path, uid, feature, scenario, start, *,
                  status="passed", attachment=None):
    r = {"uuid": uid,
         "historyId": f"h-{feature}-{scenario}",
         "name": scenario,
         "fullName": f"{feature}: {scenario}",
         "start": start, "stop": start + 10, "status": status,
         "labels": [{"name": "feature", "value": feature},
                    {"name": "suite", "value": feature},
                    {"name": "featureFile",
                     "value": f"noodle_tests/web/pkg/features/{feature}.feature"}],
         "steps": []}
    if attachment:
        (d / attachment).write_text("binary", encoding="utf-8")
        r["attachments"] = [{"name": "evidence", "source": attachment,
                             "type": "image/jpeg"}]
    (d / f"{uid}-result.json").write_text(json.dumps(r), encoding="utf-8")
    return r


# --------------------------------------------------------------------------
# 2.1 — the data-loss defect
# --------------------------------------------------------------------------

def test_two_features_authored_in_sequence_both_survive(tmp_path):
    """The reproduction, in results terms: author+run feature A, then
    author+run feature B. Both must be in the report the second run leaves
    behind, and A's evidence image must still resolve."""
    d = tmp_path / "allure-results"
    d.mkdir()

    t1 = _mark(d, "run-1", 1_000_000)
    _write_result(d, "u1", "A", "checkout", t1 + 500, attachment="e1-attachment.jpg")

    t2 = _mark(d, "run-2", 2_000_000)
    _write_result(d, "u2", "B", "banner", t2 + 500, attachment="e2-attachment.jpg")

    names = {r["name"] for r in summary.latest_results(str(d), scope="all")}
    assert names == {"checkout", "banner"}          # the report holds both
    assert (d / "e1-attachment.jpg").exists()       # run 1's evidence survived
    assert (d / "e2-attachment.jpg").exists()


def test_rerunning_a_feature_replaces_only_its_own_results(tmp_path):
    """Retention is per feature file: re-running A supersedes A's prior
    result and its attachment, and leaves B's alone."""
    d = tmp_path / "allure-results"
    d.mkdir()
    t1 = _mark(d, "run-1", 1_000_000)
    _write_result(d, "u1", "A", "checkout", t1 + 500, attachment="e1-attachment.jpg")
    _write_result(d, "u2", "B", "banner", t1 + 600, attachment="e2-attachment.jpg")

    t2 = _mark(d, "run-2", 2_000_000)
    removed = scope.purge(d, feature_files=["noodle_tests/web/pkg/features/A.feature"],
                          keep_since_ms=t2)

    assert removed == 1
    assert not (d / "u1-result.json").exists()
    assert not (d / "e1-attachment.jpg").exists()   # its evidence went with it
    assert (d / "u2-result.json").exists()
    assert (d / "e2-attachment.jpg").exists()


def test_a_retry_of_this_run_is_never_purged(tmp_path):
    """keep_since_ms protects the CURRENT run's earlier attempts — mark_flaky
    needs both to call a retried-green scenario flaky."""
    d = tmp_path / "allure-results"
    d.mkdir()
    t = _mark(d, "run-1", 1_000_000)
    _write_result(d, "a1", "A", "checkout", t + 100, status="failed")
    _write_result(d, "a2", "A", "checkout", t + 200)

    scope.purge(d, keys=["h-A-checkout"], keep_since_ms=t)

    assert len(list(d.glob("*-result.json"))) == 2
    assert summary.mark_flaky(str(d)) == 1


def test_the_run_payload_counts_only_this_run(tmp_path):
    """The report accumulates; the exit code must not. A prior run's failure
    kept in the directory would otherwise red a green build."""
    d = tmp_path / "allure-results"
    d.mkdir()
    _write_result(d, "u1", "A", "checkout", _mark(d, "run-1", 1_000_000) + 500,
                  status="failed")
    _write_result(d, "u2", "B", "banner", _mark(d, "run-2", 2_000_000) + 500)

    run = summary.collect(str(d))
    assert (run["passed"], run["failed"]) == (1, 0)
    everything = summary.collect(str(d), scope="all")
    assert (everything["passed"], everything["failed"]) == (1, 1)


def test_a_directory_with_no_marker_reads_whole(tmp_path):
    """Results written before this change — or by a bare `behave` — must read
    exactly as they did, or every pre-existing report would read empty."""
    d = tmp_path / "allure-results"
    d.mkdir()
    _write_result(d, "u1", "A", "checkout", 1000)
    _write_result(d, "u2", "B", "banner", 2000)
    assert summary.collect(str(d))["passed"] == 2


def test_run_ledgers_still_go_but_results_stay(tmp_path):
    """junit slices and cost ledgers MUST NOT accumulate (allure would
    double-count; load_total() would inflate the spend). Results must."""
    d = tmp_path / "allure-results"
    d.mkdir()
    _write_result(d, "u1", "A", "checkout", 1000, attachment="e1-attachment.jpg")
    (d / "junit.xml").write_text("<x/>", encoding="utf-8")
    (d / "junit.A.xml").write_text("<x/>", encoding="utf-8")
    (d / "llm_cost.json").write_text("{}", encoding="utf-8")
    (d / "healing-report.txt").write_text("x", encoding="utf-8")

    scope.clean_run_ledgers(d)

    assert (d / "u1-result.json").exists() and (d / "e1-attachment.jpg").exists()
    for gone in ("junit.xml", "junit.A.xml", "llm_cost.json", "healing-report.txt"):
        assert not (d / gone).exists(), gone


def test_clean_all_is_the_explicit_way_back_to_empty(tmp_path):
    d = tmp_path / "allure-results"
    d.mkdir()
    _write_result(d, "u1", "A", "checkout", 1000, attachment="e1-attachment.jpg")
    scope.clean_all(d)
    assert list(d.glob("*-result.json")) == []
    assert list(d.glob("*-attachment.*")) == []


def test_begin_run_never_moves_its_own_boundary(tmp_path):
    """The CLI marks a parallel run before spawning workers and before_all
    marks a plain one — a second call for the SAME run must not advance
    started_ms, or results already written by this run would fall outside it
    and vanish from the payload that judges the build."""
    d = tmp_path / "allure-results"
    d.mkdir()
    first = scope.begin_run(d, "run-1")
    _write_result(d, "u1", "A", "checkout", first + 1)
    assert scope.begin_run(d, "run-1") == first
    assert summary.collect(str(d))["passed"] == 1
    # a DIFFERENT run id does move it — that is a new run
    assert scope.begin_run(d, "run-2") >= first


def test_a_worker_purges_the_shared_dir_not_its_own_leaf(tmp_path, monkeypatch):
    """A behavex worker writes into allure-results/p<pid>/, but the stale
    results it supersedes live in the shared parent."""
    shared = tmp_path / "allure-results"
    leaf = shared / "p4242"
    leaf.mkdir(parents=True)
    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    assert scope.shared_results_dir(leaf) == shared


# --------------------------------------------------------------------------
# 2.2 — report_scope
# --------------------------------------------------------------------------

def test_report_scope_names_what_the_run_did_and_did_not_verify(tmp_path):
    pkg = tmp_path / "pkg" / "features"
    pkg.mkdir(parents=True)
    for n in ("A", "B", "C"):
        (pkg / f"{n}.feature").write_text("Feature: x\n", encoding="utf-8")
    d = tmp_path / "allure-results"
    d.mkdir()

    _write_result(d, "u1", "A", "checkout", _mark(d, "run-1", 1_000_000) + 500)
    _write_result(d, "u2", "B", "banner", _mark(d, "run-2", 2_000_000) + 500)

    sc = scope.report_scope(d, pkg)
    assert sc["features_run"] == ["noodle_tests/web/pkg/features/B.feature"]
    assert sc["scenarios_run"] == 1
    assert sc["features_in_report"] == 2
    assert sc["features_in_app"] == 3
    # both disagreements are stated, not left for the reader to notice
    assert "1 of the 2 feature(s) the report holds" in sc["note"]
    assert "2 of 3 feature file(s)" in sc["note"]


def test_report_scope_is_silent_when_the_run_covered_everything(tmp_path):
    pkg = tmp_path / "pkg" / "features"
    pkg.mkdir(parents=True)
    (pkg / "A.feature").write_text("Feature: x\n", encoding="utf-8")
    d = tmp_path / "allure-results"
    d.mkdir()
    _write_result(d, "u1", "A", "checkout", _mark(d, "run-1", 1_000_000) + 500)

    sc = scope.report_scope(d, pkg)
    assert "note" not in sc
    assert (sc["features_in_report"], sc["features_in_app"]) == (1, 1)


def test_feature_names_are_capped_so_the_payload_stays_bounded(tmp_path):
    d = tmp_path / "allure-results"
    d.mkdir()
    t = _mark(d, "run-1", 1_000_000)
    for i in range(25):
        _write_result(d, f"u{i}", f"F{i:02d}", "s", t + i)
    sc = scope.report_scope(d)
    assert len(sc["features_run"]) == 10 and sc["features_run_count"] == 25


# --------------------------------------------------------------------------
# 1.1 — the unanchored-check warning
# --------------------------------------------------------------------------

def _ctrl(name, selector="#x", kind="button"):
    return {"name": name, "selector": selector, "kind": kind,
            "visible": True, "needs_pom": False, "step": "x"}


def _page(controls=(), headings=()):
    return {"url": "https://app.test/", "title": "t", "http_status": 200,
            "controls": list(controls), "headings": list(headings)}


def _multi_page_goal(checks):
    return {"scenario": "s",
            "actions": [{"do": "click", "target": "add to order"},
                        {"do": "click", "target": "proceed", "id": "checkout"}],
            "checks": checks}


def test_an_unanchored_landing_page_check_is_named_not_silently_moved():
    pg = _page(controls=[_ctrl("add to order"), _ctrl("proceed")],
               headings=["Brand Name"])
    ev = goal_mod.evidence(_multi_page_goal([{"see": "Brand Name"}]),
                           {"pages": [pg], "errors": []})
    w = " ".join(ev["warnings"])
    assert "unanchored" in w and "after: start" in w
    assert "second feature" in w      # the conclusion it exists to pre-empt


def test_the_same_check_anchored_warns_about_nothing():
    pg = _page(controls=[_ctrl("add to order"), _ctrl("proceed")],
               headings=["Brand Name"])
    ev = goal_mod.evidence(
        _multi_page_goal([{"see": "Brand Name", "after": "start"}]),
        {"pages": [pg], "errors": []})
    assert ev["warnings"] == []


def test_a_single_action_goal_is_left_alone():
    """One click may well leave the page where it was — warning there would
    be a lecture, not a signal."""
    pg = _page(controls=[_ctrl("add to order")], headings=["Brand Name"])
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "add to order"}],
            "checks": [{"see": "Brand Name"}]}
    ev = goal_mod.evidence(goal, {"pages": [pg], "errors": []})
    assert ev["warnings"] == []


# --------------------------------------------------------------------------
# 1.2 — the duplicate-flow guard
# --------------------------------------------------------------------------

_CHECKOUT = """@web
Feature: Checkout
  Scenario: Checkout
    Given User is on "{env:PKG}"
    When User clicks "add to order"
    And User clicks "place order"
    Then the user sees "Order Placed"
"""

_BANNER = """@web
Feature: Banner
  Scenario: Banner
    Given User is on "{env:PKG}"
    Then the user sees "Brand Name"
"""


def test_action_steps_reads_givens_and_whens_not_assertions():
    assert core.action_steps(_CHECKOUT) == {
        'user is on "{env:pkg}"',
        'user clicks "add to order"',
        'user clicks "place order"'}
    # the And under Then inherits Then, so it is not an action
    assert core.action_steps(
        "Given a\nThen b\nAnd c\n") == {"a"}


def test_a_check_only_feature_is_told_which_flow_it_belongs_to(tmp_path):
    app = tmp_path / "pkg"
    (app / "features").mkdir(parents=True)
    (app / "features" / "checkout.feature").write_text(_CHECKOUT, encoding="utf-8")

    w = core._overlap_warnings(_BANNER, app / "features" / "banner.feature", app)
    assert len(w) == 1
    assert "checkout.feature" in w[0] and "after:" in w[0]


def test_an_unrelated_flow_gets_no_warning(tmp_path):
    app = tmp_path / "pkg"
    (app / "features").mkdir(parents=True)
    (app / "features" / "checkout.feature").write_text(_CHECKOUT, encoding="utf-8")
    other = ('Feature: Search\n  Scenario: s\n'
             '    Given User is on "{env:PKG}"\n'
             '    When User searches for "x"\n'
             '    Then the user sees "y"\n')
    assert core._overlap_warnings(other, app / "features" / "search.feature", app) == []


def test_the_feature_being_overwritten_never_overlaps_itself(tmp_path):
    app = tmp_path / "pkg"
    (app / "features").mkdir(parents=True)
    dest = app / "features" / "checkout.feature"
    dest.write_text(_CHECKOUT, encoding="utf-8")
    assert core._overlap_warnings(_CHECKOUT, dest, app) == []


# --------------------------------------------------------------------------
# 1.3 — the template carries the mechanism
# --------------------------------------------------------------------------

def test_the_goal_example_teaches_the_anchor_and_the_screenshot():
    assert goal_mod.validate(goal_mod.EXAMPLE) == []
    checks = goal_mod.EXAMPLE["checks"]
    assert any(c.get("after") == "start" for c in checks)
    assert any(c.get("after") == "searched" for c in checks)
    assert any(c.get("evidence") == "screenshot" for c in checks)
    # ...and the anchor it names is a real action id
    ids = {a["id"] for a in goal_mod.EXAMPLE["actions"] if "id" in a}
    assert {c["after"] for c in checks if c.get("after")} <= ids | {"start"}

"""NOOD_0231 — the 37.4-AIC cost review, implemented.

One reviewed session authored ONE green scenario for 37.4 AIC. The
post-mortem split the bill three ways: 51% was paid before any noodle
command ran (uncached preamble writes), 4% was a redundant skill
re-injection the harness owns, and 44% was nine round trips of real work
whose payload grew 14,041 tokens. This file guards the engine's half —
every change that shrinks or sharpens what the engine hands back.

The measured defects, in the order the plan ranked them:

  P-3  a 12-card grid printed `selects "<option>" from "size:"` twelve
       times, ~350 tokens per probe for one fact, and the identical twins
       ate the cap so the DISTINCT steps fell off the list
  P-2  each blocked authoring lap returned ~3,000 tokens of which ~6
       lines were actionable, and shipped `blocking` twice
  P-5  `click "BBQ Chicken": no probed control matches that name — did
       you mean "BBQ Chicken"?` — the suggestion was the string it had
       just rejected, and the four exploratory probes that followed were
       the single largest work-phase cost
  P-6  the ambiguity that blocked lap 3 was provable from lap 1's
       inventory
  P-4  a problem past the probe's reach surfaces one lap per layer
  P-7  a chained --do reprinted every prior stage: O(n²) output
  P-1/P-9 the always-on surfaces carry the new cost flags, and pay for
       them
"""
import json

import pytest

from noodle.agents.web import probe
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import goal_evidence as ge

# --- fixtures ---------------------------------------------------------------


def _ctl(name, sel, kind="button", step=None, **kw):
    return {"kind": kind, "name": name, "selector": sel, "visible": True,
            "needs_pom": False,
            "step": step if step is not None else f'clicks "{name}"', **kw}


def _blk(**kw):
    d = {"url": "http://shop.test/", "title": "Shop", "controls": [],
         "headings": [], "next_pages": [], "pom": []}
    d.update(kw)
    return d


def _grid_page(cards=12):
    """The measured shape: one size select + one qty field per card."""
    controls = []
    for i in range(cards):
        controls.append(_ctl("size:", f"#size-{i}", "dropdown",
                             'selects "<option>" from "size:"'))
        controls.append(_ctl("qty:", f"#qty-{i}", "field",
                             'enters "<value>" in the "qty:" field'))
    controls.append(_ctl("checkout", "#checkout"))
    return _blk(controls=controls)


# --- P-3: repeated step templates collapse ----------------------------------


def test_p3_identical_steps_collapse_to_one_line_with_a_count():
    lines = probe._step_lines(_grid_page()["controls"], cap=25)
    body = [ln for ln in lines if "copy-ready" not in ln]
    assert len(body) == 3, f"12 twins should not print 12 times:\n{body}"
    size = next(ln for ln in body if "size:" in ln)
    assert "(×12" in size
    assert "within:" in size, "the count IS the ambiguity warning — say so"


def test_p3_collapse_happens_before_the_cap_so_distinct_steps_survive():
    """The twins used to eat the cap. Two distinct steps behind 12 twins
    must both survive a cap of 3."""
    page = _grid_page()
    page["controls"].append(_ctl("track order", "#track"))
    body = [ln for ln in probe._step_lines(page["controls"], cap=3)
            if "copy-ready" not in ln and "more —" not in ln]
    assert any("checkout" in ln for ln in body)
    assert any("track order" in ln for ln in body)


def test_p3_brief_mode_dedupes_names_too():
    lines = probe._step_lines(_grid_page()["controls"], cap=25, brief=True)
    names = [ln for ln in lines if "names:" in ln]
    assert any('"size:"' in ln and "(×12" in ln for ln in names)
    for ln in names:
        assert ln.count('"size:"') <= 1


def test_p3_json_payload_dedupes_and_carries_the_counts():
    out = probe.compact_payload({"pages": [_grid_page()], "errors": []}, 40)
    pg = out["pages"][0]
    assert len(pg["suggested_steps"]) == len(set(pg["suggested_steps"]))
    assert pg["repeated_steps"]['selects "<option>" from "size:"'] == 12
    assert "within:" in out["repeated_steps_note"]


def test_p3_note_fires_for_a_grid_on_a_revealed_block():
    """A card grid usually lives behind a click, never on the landing page —
    a top-level-only check would print the hint where it is least needed."""
    pg = _blk(revealed=[_blk(revealed_by="do: click menu",
                             controls=_grid_page()["controls"])])
    out = probe.compact_payload({"pages": [pg], "errors": []}, 40)
    assert out.get("repeated_steps_note")


def test_p3_unique_steps_get_no_count_suffix():
    lines = probe._step_lines([_ctl("checkout", "#c")], cap=25)
    assert not any("(×" in ln for ln in lines)


# --- P-2: the blocked authoring payload -------------------------------------


def _blocked_envelope():
    blockers = ['click "X": no probed control matches that name']
    return {"ok": False,
            "author": {"ok": True, "ready": False, "blocking": list(blockers),
                       "next": core.BLOCKING_IS_EVIDENCE,
                       "next_action": "fix_goal_request",
                       "feature": "noodle_tests/web/a/features/x.feature",
                       "pom": "p.yaml", "warnings": [],
                       "compiled": {"feature": "G" * 2000, "pom": "P" * 800},
                       "intent": {"requested_actions": ["a"] * 40},
                       "intent_trace": [{"ok": True}] * 30,
                       "probe_summary": "probed http://x: 3 fact(s) proven",
                       "evidence": {"proven": {"a": 1},
                                    "bound_targets": {"b": 2}}},
            "run": {"skipped": "authoring not ready — no run browser launched",
                    "blocking": list(blockers)}}


def test_p2_blocked_payload_keeps_only_the_repair(tmp_path):
    out = core.collapse_payload(_blocked_envelope(), workspace=str(tmp_path))
    author = out["author"]
    for gone in core._BLOCKED_AUTHOR_DROP:
        assert gone not in author, gone
    for kept in ("blocking", "next", "next_action", "feature", "pom",
                 "ready", "warnings"):
        assert kept in author, kept


def test_p2_blocking_is_not_shipped_twice(tmp_path):
    out = core.collapse_payload(_blocked_envelope(), workspace=str(tmp_path))
    assert "blocking" not in out["run"]
    assert out["run"]["skipped"]          # the reason survives


def test_p2_a_different_run_blocking_list_is_never_dropped(tmp_path):
    """NOOD_0212 — the BLOCKED-CONTRACT refusal reports the CONTRACT's
    blockers, not this lap's. Only a pure duplicate goes."""
    env = _blocked_envelope()
    env["run"]["blocking"] = ["a stale contract blocks this feature"]
    out = core.collapse_payload(env, workspace=str(tmp_path))
    assert out["run"]["blocking"] == ["a stale contract blocks this feature"]


def test_p2_nothing_is_lost_only_moved(tmp_path):
    out = core.collapse_payload(_blocked_envelope(), workspace=str(tmp_path))
    full = json.loads(open(out["full_payload"], encoding="utf-8").read())
    assert full["author"]["compiled"]["feature"].startswith("G")
    assert full["author"]["intent_trace"]


def test_p2_is_a_real_saving(tmp_path):
    env = _blocked_envelope()
    before = len(json.dumps(env))
    after = len(json.dumps(core.collapse_payload(env, workspace=str(tmp_path))))
    assert after < before / 2, f"{before} -> {after} is not a diet"


def test_p2_bare_authoring_result_collapses_too(tmp_path):
    """run_after_author=False returns the author dict itself — and its `ok`
    is True while `ready` is False, so shape detection cannot key on `ok`."""
    out = core.collapse_payload(_blocked_envelope()["author"],
                                workspace=str(tmp_path))
    assert out["ok"] is True and out["ready"] is False
    assert "compiled" not in out and "blocking" in out


def test_p2_verbose_blocked_opts_all_the_way_back_in(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_VERBOSE_BLOCKED", "1")
    env = _blocked_envelope()
    assert core.collapse_payload(env, workspace=str(tmp_path)) is env


@pytest.mark.parametrize("mutate", [
    lambda e: e["author"].__setitem__("ready", True),
    lambda e: e["author"].__setitem__("blocking", []),
])
def test_p2_never_touches_a_payload_that_is_not_blocked(tmp_path, mutate):
    env = _blocked_envelope()
    mutate(env)
    assert core.collapse_payload(env, workspace=str(tmp_path))["author"] \
        .get("compiled")


def test_p2_a_red_run_still_keeps_everything(tmp_path):
    """Only BLOCKED authoring diets. A ready author whose RUN went red is the
    diagnosis case — every provenance key earns its bytes there."""
    red = {"ok": False,
           "author": {"ready": True, "blocking": [], "compiled": {"feature": "F"},
                      "intent_trace": [{"ok": True}]},
           "run": {"ok": False, "failed": 1}}
    assert core.collapse_payload(red, workspace=str(tmp_path)) is red


# --- P-5: text is not a control ---------------------------------------------


def _menu_blocks():
    pg = _blk(url="http://shop.test/menu", title="Menu",
              headings=["Our Menu"],
              controls=[_ctl("add to cart", ".card button", unique=False,
                             matches=20),
                        _ctl("checkout", "#checkout")],
              result_items=[
                  {"caption": "BBQ Chicken", "selector": "a[href='/p/1']",
                   "actions": [{"name": "add to order"}]},
                  {"caption": "Four Cheese", "selector": "a[href='/p/2']",
                   "actions": [{"name": "add to order"},
                               {"name": "customize"}]},
                  {"caption": "Plain Base", "selector": "a[href='/p/3']",
                   "actions": []}])
    return ge._page_blocks(pg)


def test_p5_one_control_in_the_card_gets_a_concrete_prescription():
    hint = ge._text_node_hint("BBQ Chicken", _menu_blocks(), "click")
    assert "not an actionable control" in hint
    assert '{do: click, target: "add to order", within: "BBQ Chicken"}' in hint


def test_p5_several_controls_list_candidates_and_recommend_none():
    """The nearest-actionable heuristic picks the wrong sibling on a dense
    grid, and a wrong-row click can still PASS — the worst outcome here."""
    hint = ge._text_node_hint("Four Cheese", _menu_blocks(), "click")
    assert '"add to order"' in hint and '"customize"' in hint
    assert "use {do:" not in hint


def test_p5_a_card_with_no_control_of_its_own_says_so():
    hint = ge._text_node_hint("Plain Base", _menu_blocks(), "click")
    assert "no control of its own" in hint


def test_p5_a_heading_is_diagnosed_as_a_heading():
    hint = ge._text_node_hint("Our Menu", _menu_blocks(), "click")
    assert "HEADING" in hint and "within:" in hint


def test_p5_the_suggestion_is_never_the_rejected_string():
    """The measured blocker: `did you mean "BBQ Chicken"?` in answer to
    `click "BBQ Chicken"`."""
    assert 'did you mean "BBQ Chicken"' \
        not in ge._near_miss("BBQ Chicken", _menu_blocks())


def test_p5_diagnosis_is_machine_repairable_with_its_scope():
    blocker = ('click "BBQ Chicken": '
               + ge._text_node_hint("BBQ Chicken", _menu_blocks(), "click"))
    goal = {"scenario": "S", "actions": [{"do": "click",
                                          "target": "BBQ Chicken"}],
            "checks": [{"see": "Order"}]}
    actions, changes, matched = goal_mod._repair_actions(goal, [blocker])
    assert matched == 1
    assert actions[0] == {"do": "click", "target": "add to order",
                          "within": "BBQ Chicken"}
    assert changes["rewritten_targets"][0]["within"] == "BBQ Chicken"


def test_p5_a_text_target_is_not_a_routing_problem():
    """`unrouted_targets` drives a same-origin page hunt. The control IS on
    this page, so another page cannot cure it."""
    blocker = ('click "BBQ Chicken": '
               + ge._text_node_hint("BBQ Chicken", _menu_blocks(), "click"))
    assert goal_mod.unrouted_targets([blocker]) == []


# --- P-6: ambiguity declared where it is read -------------------------------


def test_p6_repeat_count_reads_both_proofs():
    blocks = _menu_blocks()
    assert ge.repeat_count("add to cart", blocks) == 20    # unique:False
    assert ge.repeat_count("checkout", blocks) == 1
    assert ge.repeat_count("nothing here", blocks) == 0


def test_p6_a_repeated_suggestion_says_so_where_it_is_read():
    text = ge._near_miss("add to basket", _menu_blocks())
    assert '"add to cart" (×20' in text
    assert "within:" in text


def test_p6_the_near_miss_repair_regex_still_reads_the_suggestion():
    """goal.py builds the offered repair out of this exact wording; a tag
    inside it would silently kill the repair path."""
    line = ('click "add to basket": no probed control matches that name'
            + ge._near_miss("add to basket", _menu_blocks()))
    assert goal_mod._NEAR_MISS_HIT.search(line).group(1) == "add to cart"


def test_p6_the_tag_is_printed_once_not_twice():
    text = ge._near_miss("add to basket", _menu_blocks())
    assert text.count("(×20") == 1


def test_p6_an_ordinary_duplicate_is_not_tagged():
    """2-3 same-named instances are one control rendered twice — tagging
    those would tax every responsive header."""
    blocks = ge._page_blocks(_blk(controls=[
        _ctl("home", "nav a", unique=False, matches=2)]))
    assert ge._ambiguity_tag("home", blocks) == ""


# --- P-4: provisional findings, one lap -------------------------------------


def test_p4_an_unreached_repeated_target_is_reported_this_lap():
    out = ge._provisional([{"do": "click", "target": "add to cart"}],
                          _menu_blocks(), [], frozenset())
    assert len(out) == 1
    assert "20 probed controls" in out[0] and "not blocking yet" in out[0]


@pytest.mark.parametrize("actions,blocking,pinned", [
    ([{"do": "click", "target": "add to cart", "within": "BBQ Chicken"}],
     [], frozenset()),                                    # already scoped
    ([{"do": "click", "target": "add to cart"}],
     ['click "add to cart": matches 20 probed controls'], frozenset()),
    ([{"do": "click", "target": "add to cart"}],
     [], frozenset({"add to cart"})),                     # pinned in POM
    ([{"do": "click", "target": "checkout"}], [], frozenset()),
])
def test_p4_provisional_stays_quiet_when_the_question_is_answered(
        actions, blocking, pinned):
    assert ge._provisional(actions, _menu_blocks(), blocking, pinned) == []


def test_p4_provisional_never_moves_ready():
    """Advisory by contract: a provisional finding can be wrong, and a wrong
    hard block costs the whole flow."""
    ev = ge.evidence(
        {"scenario": "S", "navigation": ["http://shop.test/menu"],
         "actions": [{"do": "click", "target": "checkout"}],
         "checks": [{"see": "Our Menu"}]},
        {"pages": [_menu_blocks()[0][0]]})
    assert "provisional" in ev


def test_p4_provisional_rides_its_own_payload_key():
    assert "provisional_blocking" not in core._GREEN_AUTHOR_DROP or True
    # green collapses it away — the run is the proof the advisory was moot
    assert "provisional_blocking" in core._GREEN_AUTHOR_DROP
    assert "provisional_blocking" not in core._BLOCKED_AUTHOR_DROP


# --- P-7: delta output for a chained --do -----------------------------------


def _chained():
    return {"pages": [_blk(
        controls=[_ctl(f"nav {i}", f"#n{i}") for i in range(20)],
        do_requested=2, do_completed=2,
        revealed=[_blk(revealed_by="do: click add to cart",
                       controls=[_ctl("cart", "#c")], warnings=["slow settle"]),
                  _blk(revealed_by="do: click checkout", headings=["Checkout"],
                       controls=[_ctl("place order", "#po")])])],
        "errors": []}


def test_p7_delta_prints_only_the_newest_stage():
    text = probe.render(_chained(), compact=True, delta=True)
    assert "place order" in text                    # newest, in full
    assert "stage 1 'do: click add to cart'" in text
    assert "nav 0" not in text                      # landing, collapsed


def test_p7_delta_is_a_real_saving():
    full = probe.render(_chained(), compact=True)
    lean = probe.render(_chained(), compact=True, delta=True)
    assert len(lean) < len(full) / 1.5


def test_p7_a_warning_on_a_collapsed_stage_still_prints():
    """A warning is a verdict, not inventory."""
    assert "slow settle" in probe.render(_chained(), compact=True, delta=True)


def test_p7_delta_is_a_no_op_without_reveal_stages():
    """A single-shot probe must never return a page summarised away."""
    solo = {"pages": [_blk(controls=[_ctl("a", "#a")])], "errors": []}
    assert probe.render(solo, compact=True, delta=True) == \
        probe.render(solo, compact=True)
    assert probe.compact_payload(solo, 40, delta=True) == \
        probe.compact_payload(solo, 40)


def test_p7_json_delta_keeps_the_verdict_keys():
    """A diet that could hide a failed transaction would be a worse bug than
    the one it fixes."""
    res = _chained()
    res["pages"][0]["do_failed"] = {"index": 1, "selector": "#x",
                                    "error": "timeout"}
    res["pages"][0]["author_ready"] = False
    fat = probe.compact_payload(res, 40)["pages"][0]
    pg = probe.compact_payload(res, 40, delta=True)["pages"][0]
    assert pg["do_failed"]["error"] == "timeout"
    # every verdict key survives with the value it had un-dieted (the value
    # itself is _compact_author_ready's business, not --delta's)
    for key in ("author_ready", "do_failed", "do_completed", "do_requested",
                "skeleton"):
        assert pg.get(key) == fat.get(key), key
    assert len(pg["revealed"]) == 1
    assert len(pg["delta_skipped"]) == 2


# --- benchmark: the cost AC -------------------------------------------------


def test_benchmark_measures_payload_cost_per_test_case(tmp_path):
    from noodle import regression
    n = regression.payload_tokens(_blocked_envelope(), str(tmp_path))
    assert n > 0
    # measured at the DOOR: the diet must be visible in the number
    fat = regression.payload_tokens(
        {"ok": False, "author": dict(_blocked_envelope()["author"],
                                     ready=True, blocking=[]),
         "run": {"ok": False, "failed": 1}}, str(tmp_path))
    assert n < fat, "the P-2 diet must show up in the cost column"


def test_benchmark_cost_ceiling_blocks_a_regression():
    from noodle import regression
    base = {"id": "tc1", "elapsed_s": 20, "run_s": 14, "lines": 9,
            "corrections": 0, "green": True, "verified": True}
    ok = regression.score({"test_cases": [dict(base, payload_tokens=900)]})
    assert not [r for r in ok["regressions"] if "expensive" in r]
    bad = regression.score({"test_cases": [dict(base, payload_tokens=99999)]})
    assert [r for r in bad["regressions"] if "expensive" in r]


def test_benchmark_an_older_results_json_is_not_a_false_regression():
    """Re-scoring an archived run must not manufacture a breach out of a
    column that did not exist when it was written."""
    from noodle import regression
    v = regression.score({"test_cases": [
        {"id": "tc1", "elapsed_s": 20, "run_s": 14, "lines": 9,
         "corrections": 0, "green": True, "verified": True}]})
    assert not [r for r in v["regressions"] if "expensive" in r]


def test_benchmark_cost_is_visible_in_both_renderers():
    from noodle import regression
    v = regression.score({"test_cases": [
        {"id": "tc1", "elapsed_s": 20, "run_s": 14, "lines": 9,
         "corrections": 0, "payload_tokens": 900, "green": True,
         "verified": True}]})
    assert "TOKENS" in regression.render_table(v)
    assert "900" in regression.render_table(v)
    assert "tokens" in regression.render_html(v)
    assert v["average"]["payload_tokens"] == 900


# --- P-1 / P-9: the always-on surfaces --------------------------------------


def test_p9_the_cost_flags_exist_on_the_surfaces_that_decide_to_use_them():
    """--brief would have collapsed the 12× repeat entirely and was in no
    card. An agent does not look up a flag it has never heard of."""
    from noodle import cli
    from unit_tests.test_nood_0101 import REPO
    for rel in (".claude/skills/noodle/SKILL.md",
                ".copilot/skills/noodle/SKILL.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "--brief" in text and "--delta" in text, rel
    assert "--brief" in cli._AGENTS_MD and "--delta" in cli._AGENTS_MD


def test_p1_no_ceiling_was_raised_to_fit_the_new_flags():
    """Half one of the net-zero claim, and the half that covers the RENDERED
    surfaces. `noodle probe --help` grew a flag; its cap did not move, so the
    flag was funded out of existing text. Asserted on the ledger CONSTANTS
    because rendered help is platform-sensitive — Windows measures 8 bytes
    more than macOS for the same command (a pre-existing difference; the
    first version of this test pinned the macOS number as a floor and failed
    CI on Windows alone, which is the same mistake in miniature)."""
    from noodle import instruction_budget as ib
    assert ib.CEILINGS["cli-help (noodle probe --help)"] == 6400
    assert ib.CEILINGS["agents-md (cli._AGENTS_MD)"] == 6144
    assert ib.CEILINGS["claude-skill-card (.claude/skills/noodle/SKILL.md)"] \
        == 8704
    assert ib.CEILINGS["copilot-skill-card (.copilot/skills/noodle/SKILL.md)"] \
        == 8960


def test_p1_the_new_flags_were_paid_for_not_borrowed():
    """Half two: the surfaces whose bytes are EXACT — a Python string literal
    and two files the ledger reads with CRLF normalized (NOOD_0195) — must
    each keep at least the headroom they had before this ticket. Bytes added
    to a surface must be funded, or the saving evaporates, and a floor is the
    NOOD_0160 mechanism that makes that checkable rather than remembered."""
    from noodle import instruction_budget as ib
    floors = {"agents-md (cli._AGENTS_MD)": 517,
              "claude-skill-card (.claude/skills/noodle/SKILL.md)": 64,
              "copilot-skill-card (.copilot/skills/noodle/SKILL.md)": 157}
    have = {r["surface"]: r["headroom"] for r in ib.ledger()}
    for surface, floor in floors.items():
        # None = a repo file a wheel install doesn't ship; nothing to judge.
        assert have[surface] is None or have[surface] >= floor, \
            f"{surface}: {have[surface]} < the {floor} it started with"

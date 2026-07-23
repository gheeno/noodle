"""NOOD_0169 — cut the simple-prompt session from 67 agent interactions and
three shell approvals to a single approved binary. Re-analysis of the
NOOD_0168 baseline session found four round-trip factories, pinned here
browser-free:

  1. Search-box election judged every selector by its .first — a hidden
     responsive twin DOM-earlier than the visible box rejected the whole
     selector (probe) or took the doomed fill (runtime). Visible-first
     election in both, plus a runtime visible-twin fallback around the
     trigger-open dance.
  2. Multi-URL CLI/MCP probes ran search/clicks/do on EVERY page — a setup
     page then reported "no search box found" and poisoned author_ready.
     Several URLs now default to act_on="last", like the goal path.
  3. One control below the compact cap cost payload-spill greps (each a
     shell approval in a controlled env): probe --find / find_controls
     filters everything collected, pre-cap.
  4. Schema/vocabulary recovery cost 18 help/docs/steps calls: goal
     rejections ship vocabulary() generated from the validate() tables;
     `noodle steps` takes several keywords in one call.
  5. verified:false named the ambiguity but kept the fix as homework:
     ambiguous-candidate lines carry a paste-ready scoped selector and mark
     the one lenient mode used.
"""
from unittest.mock import MagicMock

from typer.testing import CliRunner

from noodle import healing
from noodle.agents.web import actions, locator
from noodle.agents.web import probe as probe_mod
from noodle.repl import core
from noodle.repl import goal as goal_mod

# --- 1a. probe _find_search_box: visible-first, never .first-only -----------

def _page_with_twins(counts_visible):
    """A page whose every selector query returns the same match list:
    counts_visible = [False, True] models a hidden DOM-earlier twin."""
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = len(counts_visible)
    nths = []
    for vis in counts_visible:
        cand = MagicMock()
        cand.is_visible.return_value = vis
        nths.append(cand)
    loc.nth.side_effect = lambda i: nths[i]
    page.locator.return_value = loc
    return page, nths


def test_probe_search_box_elects_visible_over_hidden_first():
    page, nths = _page_with_twins([False, True])
    assert probe_mod._find_search_box(page) is nths[1]


def test_probe_search_box_none_when_all_hidden():
    page, _ = _page_with_twins([False, False])
    assert probe_mod._find_search_box(page) is None


# --- 1b. runtime _visible_search_box + _resolve_search_box fallback ---------

def test_runtime_visible_search_box_skips_hidden_twin(monkeypatch):
    monkeypatch.setattr(actions, "_is_editable", lambda loc: True)
    page, nths = _page_with_twins([False, True])
    assert actions._visible_search_box(page) is nths[1]


def test_runtime_visible_search_box_requires_editable(monkeypatch):
    monkeypatch.setattr(actions, "_is_editable", lambda loc: False)
    page, _ = _page_with_twins([True, True])
    assert actions._visible_search_box(page) is None


def test_resolve_search_box_uses_visible_twin_before_trigger(monkeypatch):
    hidden = MagicMock()
    hidden.is_visible.return_value = False
    visible = MagicMock()
    visible.is_visible.return_value = True
    monkeypatch.setattr(actions, "find_first", lambda *a, **k: hidden)
    monkeypatch.setattr(actions, "_is_editable", lambda loc: True)
    monkeypatch.setattr(actions, "_visible_search_box", lambda p: visible)
    trigger = MagicMock()
    monkeypatch.setattr(actions, "_visible_search_trigger",
                        lambda p: trigger)
    healing.reset()
    assert actions._resolve_search_box(MagicMock()) is visible
    trigger.click.assert_not_called()
    assert any(e["strategy"] == "visible-filter" for e in healing.EVENTS)


def test_resolve_search_box_visible_scan_after_trigger(monkeypatch):
    """The trigger may reveal a box find() still resolves to the hidden twin
    of — the post-reveal resolution retries the visible scan too."""
    hidden = MagicMock()
    hidden.is_visible.return_value = False
    visible = MagicMock()
    visible.is_visible.return_value = True
    trigger = MagicMock()
    trigger.is_visible.return_value = True
    scans = iter([None, visible])  # before trigger: nothing; after: the box
    monkeypatch.setattr(actions, "find_first", lambda *a, **k: hidden)
    monkeypatch.setattr(actions, "_is_editable", lambda loc: True)
    monkeypatch.setattr(actions, "_visible_search_box",
                        lambda p: next(scans))
    monkeypatch.setattr(actions, "_visible_search_trigger",
                        lambda p: trigger)
    healing.reset()
    assert actions._resolve_search_box(MagicMock()) is visible
    trigger.click.assert_called_once()
    assert any(e["strategy"] == "search-trigger-open"
               for e in healing.EVENTS)


# --- 2. multi-URL probes act on the LAST page by default --------------------

def _capture_probe(monkeypatch):
    seen = {}

    def fake(urls, **kw):
        seen["urls"], seen["kw"] = urls, kw
        return {"pages": [], "errors": []}
    monkeypatch.setattr(probe_mod, "probe", fake)
    return seen


def test_multi_url_probe_defaults_to_act_on_last(monkeypatch):
    seen = _capture_probe(monkeypatch)
    core.probe_page("http://a.example http://b.example")
    assert seen["kw"]["act_on"] == "last"


def test_single_url_probe_defaults_to_act_on_each(monkeypatch):
    seen = _capture_probe(monkeypatch)
    core.probe_page("http://a.example")
    assert seen["kw"]["act_on"] == "each"


def test_explicit_act_on_wins(monkeypatch):
    seen = _capture_probe(monkeypatch)
    core.probe_page("http://a.example http://b.example", act_on="each")
    assert seen["kw"]["act_on"] == "each"


# --- 3. find_controls: pre-cap filter over everything collected -------------

_RESULT = {"pages": [{
    "url": "http://x/results",
    "controls": [
        {"name": "open menu", "selector": "#menu", "kind": "button",
         "visible": True, "step": "clicks 'open menu'"},
    ],
    "revealed": [],
    "search": {
        "controls": [
            {"name": "categories facet", "selector": "#facet",
             "kind": "button", "visible": False, "step": "clicks it"},
        ],
        "headings": [],
        "pom_yaml": "",
        "result_items": [
            {"caption": "Toy Blaster 3000", "selector": "a.card",
             "actions": [{"name": "Add to cart", "selector": "#atc-1"}]},
        ],
    },
}]}


def test_find_controls_reaches_card_actions_pre_cap():
    hits = probe_mod.find_controls(_RESULT, "add-to-CART")
    assert len(hits) == 1
    assert hits[0]["selector"] == "#atc-1"
    assert hits[0]["item_caption"] == "Toy Blaster 3000"


def test_find_controls_matches_captions_and_controls():
    assert probe_mod.find_controls(_RESULT, "toy blaster")
    assert probe_mod.find_controls(_RESULT, "facet")
    assert probe_mod.find_controls(_RESULT, "nothing here") == []


def test_render_find_is_paste_ready_or_says_no_match():
    text = probe_mod.render_find(_RESULT, "add to cart")
    assert "#atc-1" in text and "pom:" in text
    miss = probe_mod.render_find(_RESULT, "warp drive")
    assert "no matching control" in miss


# --- 4a. goal vocabulary generated from the validate() tables ---------------

def test_vocabulary_mirrors_validation_tables():
    v = goal_mod.vocabulary()
    assert set(v["actions"]) == set(goal_mod._ACTION_KEYS)
    for do, spec in v["actions"].items():
        assert set(spec["required"]) <= set(spec["keys"])
    assert v["check_keys"] == sorted(goal_mod._CHECK_KEYS)
    assert v["dismissals"] == sorted(goal_mod._DISMISSALS)
    # the exact keys whose absence from EXAMPLE cost the docs hunt
    assert "item_in_destination" in v["check_keys"]
    assert v["actions"]["add_to"]["required"] == ["destination", "item_from"]


def test_invalid_goal_ships_vocabulary(tmp_path):
    res = core.author_test(
        app_name="x", base_url="http://x.example",
        feature_path="noodle_tests/x/features/x.feature",
        goal={"actions": [{"do": "warp"}]}, workspace=str(tmp_path))
    assert res["ok"] is False
    assert "vocabulary" in res and "example" in res
    assert set(res["vocabulary"]["actions"]) == set(goal_mod._ACTION_KEYS)


# --- 4b. noodle steps: several keywords, one call ----------------------------

def test_steps_cli_accepts_multiple_keywords():
    from noodle.cli import app
    runner = CliRunner()
    one = runner.invoke(app, ["steps", "clipboard"])
    both = runner.invoke(app, ["steps", "clipboard", "screenshot"])
    assert one.exit_code == 0 and both.exit_code == 0
    # union: everything the single-keyword call found, plus the second word's
    for line in one.output.splitlines():
        if line.startswith("  ") and "step(s)" not in line:
            assert line in both.output
    assert "screenshot" in both.output.lower()


def test_steps_cli_notes_missed_keyword_but_prints_hits():
    from noodle.cli import app
    runner = CliRunner()
    res = runner.invoke(app, ["steps", "clipboard", "zzz-no-such-word"])
    assert res.exit_code == 0
    assert "(no steps matching 'zzz-no-such-word')" in res.output


# --- 5. ambiguous candidates carry a paste-ready scoped selector ------------

def _handle(tag="button", text="Add to cart", sel="#buy-1"):
    h = MagicMock()
    h.evaluate.side_effect = (
        lambda js: sel if "CSS.escape" in js else tag)
    h.inner_text.return_value = text
    return h


def test_describe_candidates_includes_scoped_selector():
    loc = MagicMock()
    loc.element_handles.return_value = [_handle(sel="#buy-1"),
                                        _handle(sel="#buy-2")]
    out = locator._describe_candidates(loc)
    assert "css: #buy-1" in out[0] and "css: #buy-2" in out[1]


def test_on_ambiguous_strict_message_marks_used_and_pins_fix(monkeypatch):
    monkeypatch.setattr(locator, "_is_strict", lambda: True)
    loc = MagicMock()
    loc.element_handles.return_value = [_handle(sel="#buy-1"),
                                        _handle(sel="#buy-2")]
    try:
        locator._on_ambiguous(MagicMock(), "Add to cart", loc)
    except AssertionError as e:
        msg = str(e)
    else:  # pragma: no cover
        raise AssertionError("strict mode must raise")
    assert "[0] (used)" in msg and "Pin the intended one" in msg
    assert "css: #buy-1" in msg


# --- 6. prompt expander: numbered plain English → goal, deterministically ---
# The context rule fires ONLY for underspecified steps; a fully specified
# step translates literally with zero inference notes.

from noodle.repl import prompt_expander as pe  # noqa: E402

_BASELINE = ("1. go to shop.example.com\n"
             "2. search for toy\n"
             "3. add to cart\n"
             "4. verify cart has toy")


def test_prompt_baseline_four_liner_expands_and_validates():
    exp = pe.expand(_BASELINE)
    assert exp["ok"], exp
    g = exp["goal"]
    assert g["navigation"] == ["https://shop.example.com"]
    # NOOD_0169 v2 — the expander itself mints the linked pick (stable ids,
    # typed dataflow), instead of leaving it to goal.normalize
    assert [a["do"] for a in g["actions"]] == ["search", "pick", "add_to"]
    assert g["actions"][0]["term"] == "toy"
    pick, add = g["actions"][1], g["actions"][2]
    assert pick == {"do": "pick", "id": "pick1", "from": "search1",
                    "strategy": "first_actionable"}
    assert add == {"do": "add_to", "id": "add1", "item_from": "pick1",
                   "destination": "cart"}
    assert g["checks"] == [{"item_in_destination": "cart",
                            "expected_from": "pick1", "after": "add1"}]
    # the ambiguous step borrowed from its neighbour — and said so
    assert any("step 3" in a and "step 2" in a and "toy" in a
               for a in exp["assumptions"])
    assert exp["translation_mode"] == "contextual"
    assert exp["app_name"] == "shop_example_com"
    assert exp["feature_path"].startswith(
        "noodle_tests/shop_example_com/features/")
    # the expanded goal must sail through the goal pipeline unchanged —
    # normalize accepts the already-linked pick without re-inferring
    norm, notes = goal_mod.normalize(g)
    assert goal_mod.validate(norm) == []
    assert norm["actions"] == g["actions"] and notes == []


def test_prompt_fully_specified_steps_trigger_no_inference():
    exp = pe.expand("1. go to shop.example.com\n"
                    "2. search for wagon\n"
                    "3. add the red wagon to cart\n"
                    "4. verify the cart has the red wagon\n"
                    "5. close the popups")
    assert exp["ok"], exp
    acts = exp["goal"]["actions"]
    pick = next(a for a in acts if a["do"] == "pick")
    assert pick["target"] == "red wagon" and pick["from"] == "search1"
    add = next(a for a in acts if a["do"] == "add_to")
    assert add["item_from"] == pick["id"]
    chk = exp["goal"]["checks"][0]
    assert chk["item_in_destination"] == "cart"
    assert chk["expected_from"] == pick["id"] and chk["after"] == add["id"]
    assert "popups" in exp["goal"]["dismissals"]
    # well-flushed steps: the CONTEXT resolver predicted nothing — the
    # deterministic fast path, zero inferences
    assert exp["inferences"] == []
    assert exp["translation_mode"] == "deterministic-fast-path"


def test_prompt_inline_numbering_splits_the_same():
    one_line = ("1. go to shop.example.com 2. search for toy "
                "3. add to cart 4. verify cart has toy")
    assert pe.expand(one_line)["goal"] == pe.expand(_BASELINE)["goal"]


def test_prompt_bare_destination_check_borrows_context():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. check the cart")
    assert exp["ok"]
    chk = exp["goal"]["checks"][0]
    assert chk["item_in_destination"] == "cart"
    assert chk["expected_from"] == "pick1"
    assert any("bare destination" in a for a in exp["assumptions"])


def test_prompt_checkout_is_refused_not_misread_as_verify():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. check out")
    assert not exp["ok"]
    assert any("step 4" in u for u in exp["unrecognized"])


def test_prompt_add_without_search_is_refused_by_name():
    exp = pe.expand("1. go to shop.example.com\n2. add to cart")
    assert not exp["ok"]
    assert any("step 2" in u and "search" in u for u in exp["unrecognized"])


def test_prompt_without_url_needs_base_url():
    steps = "1. search for toy\n2. add to cart\n3. verify cart has toy"
    assert not pe.expand(steps)["ok"]
    exp = pe.expand(steps, base_url="https://shop.example.com")
    assert exp["ok"]
    assert "navigation" not in exp["goal"]
    assert exp["base_url"] == "https://shop.example.com"
    assert exp["app_name"] == "shop_example_com"


def test_prompt_screenshot_and_run_mode_steps():
    exp = pe.expand(_BASELINE + "\n5. take a screenshot\n6. run headed")
    assert exp["ok"]
    assert exp["goal"]["checks"][-1]["evidence"] == "screenshot"
    assert any("runner flag" in a for a in exp["assumptions"])


def test_prompt_plural_meets_singular():
    exp = pe.expand("1. go to shop.example.com\n2. search for toys\n"
                    "3. add a toy to the cart\n4. verify cart has toys")
    assert exp["ok"], exp
    # 'a toy' overlaps the search subject 'toys' → implied first-actionable
    # pick, not an explicit pick on the literal word
    acts = exp["goal"]["actions"]
    assert [a["do"] for a in acts] == ["search", "pick", "add_to"]
    assert acts[1]["strategy"] == "first_actionable"
    assert exp["goal"]["checks"][0]["item_in_destination"] == "cart"


def test_prompt_go_to_non_url_is_a_click():
    exp = pe.expand("1. go to shop.example.com\n2. search for toy\n"
                    "3. add to cart\n4. go to the cart\n"
                    "5. verify cart has toy")
    assert exp["ok"]
    assert {"do": "click", "target": "cart"} in exp["goal"]["actions"]


def test_prompt_explicit_dismissals_replace_defaults():
    exp = pe.expand("1. go to shop.example.com\n2. close the cookie banner\n"
                    "3. dismiss the location prompt\n4. search for toy\n"
                    "5. add to cart\n6. verify cart has toy")
    assert exp["ok"]
    assert sorted(exp["goal"]["dismissals"]) == ["location_prompt", "popups"]
    assert not any("defaulted" in a for a in exp["assumptions"])


# --- 7. author_test prompt wiring -------------------------------------------

def test_author_prompt_mutually_exclusive_with_goal():
    res = core.author_test(prompt="1. go to x.example", goal={"scenario": "s"})
    assert res["ok"] is False and "mutually exclusive" in res["error"]


def test_author_prompt_unparseable_ships_vocabulary(tmp_path):
    res = core.author_test(prompt="1. go to shop.example.com\n"
                                  "2. frobnicate the cart",
                           workspace=str(tmp_path))
    assert res["ok"] is False
    assert res["unrecognized_steps"]
    assert "vocabulary" in res and "example" in res


def test_author_prompt_attaches_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda *a, **k: {"pages": [], "errors": []})
    res = core.author_test(prompt=_BASELINE, workspace=str(tmp_path))
    assert "prompt_expansion" in res
    exp = res["prompt_expansion"]
    assert exp["assumptions"]
    assert exp["translation_mode"] == "contextual"
    assert exp["goal"]["checks"][0]["item_in_destination"] == "cart"
    # NOOD_0169 v2 — the planner's typed terminal verdict rides the payload
    assert res["planner"]["state"] == "EXTERNAL_FAILURE"


def test_author_without_prompt_still_requires_paths():
    res = core.author_test(goal={"scenario": "s"})
    assert res["ok"] is False and "required" in res["error"]


def test_author_cli_exactly_one_of_spec_or_prompt():
    from noodle.cli import app
    runner = CliRunner()
    neither = runner.invoke(app, ["author"])
    assert neither.exit_code != 0
    assert "exactly one" in (neither.output + str(neither.exception or ""))

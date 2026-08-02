"""NOOD_0217 — cut the agent cost of authoring, kill the wasted repair lap.

A 4-TC cold benchmark (engine 1.0.0a31) went green on every case but showed
two cost bugs, pinned here:

  F1 — the 8 KB payload budget was structurally unenforceable for authoring:
       `bound()` built its trim set from TOP-LEVEL lists/strings only, and the
       authoring envelope's weight sits in nested dicts (`author`, `run`), so
       every call overflowed with "nothing is trimmable". And nothing guarded
       it: no test asserted the envelope FITS — that absence is why every
       ticket that grew `author`/`run` raised agent cost with no signal.
  F2 — the prompt expander lacked the control-noun stripping the step grammar
       has: `verify you see the textbox "X"` asserted the literal
       'textbox "X"' — text no page renders — costing a full red-run +
       re-author lap on normal human phrasing.
"""
import json
from pathlib import Path

from noodle import payload_budget
from noodle.repl import core
from noodle.repl.prompt_expander import expand

# --- F1: nested-aware bound() ------------------------------------------------

def test_bound_trims_nested_dict_values():
    """The regression itself: a payload dominated by a nested dict used to
    come back WHOLE with 'nothing is trimmable'."""
    p = {"ok": True, "ready": True, "author": {"big": "x" * 20_000}}
    out = payload_budget.bound(p, budget=8_000)
    assert payload_budget.size(out) <= 8_000, "still over budget"
    assert "author.big" in out["payload_note"]
    assert out["ok"] is True and out["ready"] is True
    assert p["author"]["big"] == "x" * 20_000, "input mutated"


def test_bound_never_trims_blocking():
    """`blocking` is the repair path's whole evidence — protected at any
    depth, even when it is the largest value."""
    p = {"blocking": ["fix this control name: " + "b" * 80] * 20,
         "author": {"blocking": ["nested blocker"] * 20, "pad": "y" * 3_000}}
    out = payload_budget.bound(p, budget=2_000)
    assert out["blocking"] == p["blocking"]
    assert out["author"]["blocking"] == p["author"]["blocking"]


def test_bound_top_level_lists_still_trim():
    """The pre-0217 behaviour survives the rewrite."""
    p = {"items": [f"row {i} " + "z" * 100 for i in range(200)]}
    out = payload_budget.bound(p, budget=4_000)
    assert payload_budget.size(out) <= 4_000
    assert "items" in out["payload_note"]


# --- F1: green-path collapse -------------------------------------------------

def _fat_green() -> dict:
    """A green, verified atomic envelope at the size the benchmark measured
    (10–12 KB at indent=2) — heavy keys are diagnosis provenance."""
    return {
        "ok": True,
        "author": {
            "ready": True, "blocking": [], "intent_verified": True,
            "feature": "noodle_tests/shop/features/add_to_cart.feature",
            "pom": "noodle_tests/shop/poms/add_to_cart.yaml",
            "warnings": [],
            "compiled": {"feature": "Feature: cart\n" + '    And User clicks "x"\n' * 40,
                         "pom": "pages:\n" + "  el:\n    css: 'div.x > span'\n" * 30},
            "intent": {"requested": [f"requirement {i}" for i in range(15)]},
            "intent_trace": [{"requirement": f"r{i}", "ok": True,
                              "evidence": "probe " * 12} for i in range(12)],
            "probe_summary": "probed https://example.test: 9 fact(s) proven, "
                             "2 check(s) run-proven, 2 popup(s) closed",
            "evidence": {"proven": {f"check:{i}": "matched text " * 4
                                    for i in range(9)},
                         "runtime_asserted": ["a", "b"], "popups_closed": 2,
                         "permission_prompts": ["geolocation"]},
        },
        "run": {"ok": True, "passed": 1, "failed": 0, "verified": True,
                "seconds": 12.3, "exit_code": 0,
                "output": "behave output line that nobody reads on green\n" * 30,
                "healing_events": [],
                "report": "output/last/reports/allure-report/index.html",
                "rca_html": "output/last/reports/rca.html",
                "rca_md": "output/last/reports/rca.md",
                "evidence": [{"step": "then", "status": "passed",
                              "path": f"e{i}.png"} for i in range(3)],
                "served": {"urls": ["http://localhost:8710/allure",
                                    "http://localhost:8710/rca.html"]}},
        "prompt_expansion": {
            "goal": {"scenario": "cart", "navigation": ["https://example.test"],
                     "actions": [{"do": "click", "target": f"t{i}"}
                                 for i in range(6)],
                     "checks": [{"see": "added to cart"}] * 4},
            "translation_mode": "deterministic",
            "assumptions": ["read 'a toy' as the first matching result"],
            "coverage": [{"clause": f"clause-{i}", "status": "ok"}
                         for i in range(10)],
            "inferences": [{"note": "inference detail " * 6}] * 5},
        "planner": {"state": "VERIFIED",
                    "budgets": {"interpretation_model_calls": 0,
                                "probes": 1, "runs": 1}},
    }


def _fat_red() -> dict:
    r = _fat_green()
    r["ok"] = False
    r["run"]["ok"] = False
    r["run"]["failed"] = 1
    r["run"]["verified"] = False
    r["run"]["rca_compact"] = {"verdict": "app-rejected",
                               "failures": [{"step": "see", "why": "missing"}]}
    return r


def test_green_envelope_fits_the_budget_at_cli_rendering(tmp_path):
    """THE missing guard: the CLI prints --json at indent=2; the collapsed
    green envelope must fit whole (no trim note, nothing lost)."""
    out = core.collapse_green(_fat_green(), workspace=str(tmp_path))
    assert payload_budget.size(out, indent=2) <= payload_budget.budget_bytes()
    bounded = payload_budget.bound(out, indent=2)
    assert "payload_note" not in bounded


def test_green_collapse_drops_diagnosis_keys_and_points_at_the_full_payload(tmp_path):
    out = core.collapse_green(_fat_green(), workspace=str(tmp_path))
    for k in ("compiled", "intent", "intent_trace", "probe_summary"):
        assert k not in out["author"], k
    for k in ("output", "healing_events"):
        assert k not in out["run"], k
    for k in ("coverage", "inferences", "goal"):
        assert k not in out["prompt_expansion"], k
    # evidence collapses to counts, not content
    assert out["author"]["evidence"]["proven"] == 9
    assert out["author"]["evidence"]["popups_closed"] == 2
    # the verdict + pointers an agent actually reads survive whole
    assert out["run"]["served"]["urls"]
    assert out["author"]["feature"].endswith(".feature")
    assert out["planner"]["state"] == "VERIFIED"
    # nothing is lost: the full envelope is on disk where the payload says
    full = Path(out["full_payload"])
    assert full.is_file()
    assert json.loads(full.read_text())["author"]["compiled"]["feature"]


def test_red_envelope_is_never_collapsed(tmp_path):
    """The failure path keeps everything — that is when it earns its bytes."""
    red = _fat_red()
    out = core.collapse_green(red, workspace=str(tmp_path))
    assert out is red
    assert out["author"]["intent_trace"]
    assert out["run"]["rca_compact"]["failures"]


def test_unverified_green_is_never_collapsed(tmp_path):
    """verified:false behind a pass is NOT a pass — keep the diagnosis."""
    r = _fat_green()
    r["run"]["verified"] = False
    assert core.collapse_green(r, workspace=str(tmp_path)) is r
    r2 = _fat_green()
    r2["author"]["intent_verified"] = False
    assert core.collapse_green(r2, workspace=str(tmp_path)) is r2


def test_non_atomic_result_is_untouched(tmp_path):
    r = {"ok": True, "ready": True, "feature": "f.feature", "blocking": []}
    assert core.collapse_green(r, workspace=str(tmp_path)) is r


def test_red_envelope_fits_the_budget_via_nested_trim():
    """Red keeps every key — but the bound() backstop can now actually trim
    it under the budget instead of declaring nothing trimmable."""
    for indent in (None, 2):
        out = payload_budget.bound(_fat_red(), indent=indent)
        assert payload_budget.size(out, indent=indent) \
            <= payload_budget.budget_bytes(), f"indent={indent}"
        assert "payload_note" in out
        assert out["run"]["rca_compact"]["failures"], "diagnosis shed"


def test_mcp_author_door_green_and_red_fit_the_budget(tmp_path, monkeypatch):
    """The whole door, both verdicts: what the MCP host inlines is ≤ budget."""
    from noodle.mcp import server as mcp_server
    for fixture in (_fat_green, _fat_red):
        monkeypatch.setattr(mcp_server.core, "author_test",
                            lambda **kw: fixture())
        out = mcp_server.author_test(prompt="go to https://example.test",
                                     workspace=str(tmp_path))
        assert payload_budget.size(out) <= payload_budget.budget_bytes(), \
            fixture.__name__


# --- F2: control-noun stripping in the claim grammar -------------------------

def _checks(prompt: str) -> list[dict]:
    r = expand(f"go to https://example.test then {prompt}")
    assert r["ok"], r.get("error")
    return r["goal"]["checks"]


def test_noun_before_quote_asserts_the_bare_text():
    """The TC2 lap: the noun names the instrument, the quote is the claim."""
    assert _checks('verify you see the textbox "Please enter your email '
                   'address"') == [{"see": "Please enter your email address"}]
    assert _checks('verify the button "Submit order" is visible') \
        == [{"see": "Submit order"}]
    assert _checks('check the field "Order number" appears') \
        == [{"see": "Order number"}]
    assert _checks('verify the page shows the textbox "Email"') \
        == [{"see": "Email"}]


def test_noun_strip_is_echoed_in_assumptions():
    r = expand('go to https://example.test then verify you see the '
               'textbox "Email"')
    assert any("instrument" in a for a in r["assumptions"])


def test_state_assertions_are_never_rewritten():
    """'is checked'/'is disabled' is not a presence tail — rewriting it would
    turn a clean literal into a wrong one. Behaviour unchanged from main."""
    assert _checks('verify the checkbox "I agree" is checked') \
        == [{"see": 'checkbox "I agree" is checked'}]
    assert _checks('verify the "Add to cart" button is disabled') \
        == [{"see": '"Add to cart" button is disabled'}]


def test_two_textboxes_conjunction_still_splits():
    """NOOD_0212's plural path keeps working beside the new singular one."""
    assert _checks('on the next page verify you see the two textboxes with '
                   '"Please enter your email address" and "please enter an '
                   'order number"') \
        == [{"see": "Please enter your email address"},
            {"see": "please enter an order number"}]


def test_goal_label_beside_numbered_steps_is_a_title():
    """The 0217 benchmark's TC1 friction: PROMPT_TEMPLATE shape minus its
    'Steps a human would take:' header. The goal summary minted a SECOND
    search action ('product via search bar suggestion box' as the term) —
    one CONTRACT_BLOCKED lap, then a probe lap where that term swallowed
    the suggest binding. Numbered steps are the flow; the label is a title."""
    r = expand(
        "App under test: CanadianTire\n"
        "Base URL: https://shop.example.test/\n"
        "User goal: Search for a product via search bar suggestion box\n"
        "1. Go to the URL https://shop.example.test/\n"
        '2. Use the search bar , search for "Vaccu" ( needs to be incomplete )\n'
        '3. Click the Suggestion : "Vaccum cleaner"\n'
        '4. Verify the results page shows "BISSELL" and "Hoover"')
    assert r["ok"], r.get("error")
    assert [a["do"] for a in r["goal"]["actions"]] == ["suggest"]
    assert "suggestion box" not in json.dumps(r["goal"])
    assert len(r["goal"]["checks"]) == 2


def test_goal_label_is_a_title_even_when_the_steps_never_search():
    """The numbered flow wins whole — nothing is invented from the summary."""
    r = expand("User goal: Search for a product\n"
               "1. go to https://shop.example.test\n"
               '2. verify "Welcome"')
    assert r["ok"], r.get("error")
    assert not r["goal"].get("actions")
    assert r["goal"]["checks"] == [{"see": "Welcome"}]


def test_goal_label_without_numbered_steps_is_still_the_flow():
    """The one-line `Goal: …` door keeps working: with no numbered steps the
    label IS the flow, whether the rest is metadata or prose navigation."""
    r = expand("base url: https://shop.example.test\n"
               "User goal: search for shoes")
    assert r["ok"], r.get("error")
    assert [(a["do"], a["term"]) for a in r["goal"]["actions"]] \
        == [("search", "shoes")]
    r2 = expand("User goal: search for shoes\n"
                "go to https://shop.example.test")
    assert [(a["do"], a["term"]) for a in r2["goal"]["actions"]] \
        == [("search", "shoes")]


def test_control_noun_list_has_one_definition():
    """Drift guard: the expander IMPORTS the step grammar's noun list, and
    every full inline alternation in patterns.py starts with that constant."""
    from noodle.repl import prompt_expander
    from noodle.resolver import patterns
    assert patterns.CONTROL_NOUNS in prompt_expander._NOUN_CLAIM.pattern
    assert patterns.CONTROL_NOUNS in prompt_expander._NOUN_BEFORE_QUOTE.pattern
    src = Path(patterns.__file__).read_text(encoding="utf-8")
    import re
    runs = [r for r in re.findall(r"button\|(?:[a-z]+\|)+[a-z]+", src)
            if "dropdown" in r]
    assert runs, "the inline alternations vanished — update this guard"
    assert all(r.startswith(patterns.CONTROL_NOUNS) for r in runs)

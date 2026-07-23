"""NOOD_0163 — a goal whose flow spans pages binds each check to its page.

Two bugs, one symptom (a green probe, a red first run, a hand-patch):
`after: start` did not exist, so a check on the LANDING page compiled behind
the actions (NOOD_0158 made an unanchored check observe the end state); and
every unnamed count/any_of check defaulted to the POM key "result titles",
where `setdefault` kept the first selector — so the second page's assertion
re-used the first page's locator.
"""
import re

from noodle.repl import goal as G

_EV = {"proven": {}, "controls": {}, "bound_targets": {},
       "resolved_controls": {}, "permission_prompts": [], "popups_closed": 0,
       "headings": [], "results_summary": {"selector": "#count"}}


def _goal(after="start"):
    return {"scenario": "Landing text survives a search",
            "actions": [{"do": "search", "id": "s", "term": "<term>"}],
            "checks": [{"any_of": ["<landing text>"], "after": after},
                       {"count": "results", "min": 1, "after": "s"},
                       {"any_of": ["<result text>"]}]}


def _compile(after="start"):
    feature, pom = G.compile_goal(_goal(after), _EV, "APP")
    steps = [ln.strip() for ln in feature.splitlines() if ln.startswith("    ")]
    return steps, pom


def test_start_anchored_check_precedes_the_action():
    assert G.validate(_goal()) == []
    steps, _ = _compile()
    first_then = next(i for i, s in enumerate(steps) if "at least" in s)
    search = next(i for i, s in enumerate(steps) if "searches for" in s)
    assert first_then < search, steps


def test_unanchored_check_still_observes_the_end_state():
    steps, _ = _compile(after=None)
    first_then = next(i for i, s in enumerate(steps) if "at least" in s)
    search = next(i for i, s in enumerate(steps) if "searches for" in s)
    assert first_then > search, steps


def test_each_distinct_locator_gets_its_own_pom_key():
    steps, pom = _compile()
    names = [re.search(r'"([^"]+)"$', s).group(1)
             for s in steps if "should see at least" in s]
    assert len(set(names)) == 2, names
    for n in names:
        assert f"{n}:" in pom, pom


def test_unknown_anchor_names_start_in_the_error():
    errs = G.validate(_goal("nope"))
    assert any("'start'" in e for e in errs), errs


def test_start_is_reserved_as_an_action_id():
    g = _goal()
    g["actions"][0]["id"] = "start"
    errs = G.validate(g)
    assert any("reserved" in e for e in errs), errs


# --- the ledger measures content, not paint (NOOD_0163) ----------------------
# CI renders `--help` in colour and a laptop doesn't: 396 ANSI escapes, ~2 KB,
# which failed the NOOD_0162 help ceilings on main while passing locally.

def test_cli_help_measurement_ignores_colour(monkeypatch):
    from noodle import instruction_budget as ib

    plain = ib._cli_help("probe")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert ib._cli_help("probe") == plain

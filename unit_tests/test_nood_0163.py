"""NOOD_0163 — a goal whose flow spans pages binds each check to its page.

Two bugs, one symptom (a green probe, a red first run, a hand-patch):
`after: start` did not exist, so a check on the LANDING page compiled behind
the actions (NOOD_0158 made an unanchored check observe the end state); and
every unnamed count/any_of check defaulted to the POM key "result titles",
where `setdefault` kept the first selector — so the second page's assertion
re-used the first page's locator.
"""

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
    landing = next(i for i, s in enumerate(steps) if "<landing text>" in s)
    search = next(i for i, s in enumerate(steps) if "searches for" in s)
    assert landing < search, steps


def test_unanchored_check_still_observes_the_end_state():
    steps, _ = _compile(after=None)
    landing = next(i for i, s in enumerate(steps) if "<landing text>" in s)
    search = next(i for i, s in enumerate(steps) if "searches for" in s)
    assert landing > search, steps


def test_each_any_of_compiles_its_own_inline_step():
    # NOOD_0197 — any_of no longer synthesizes a POM key at all, so the
    # shared-"result titles"-key collision this file was written for cannot
    # recur: each disjunction carries its members inline in its own step.
    steps, pom = _compile()
    disjunctive = [s for s in steps if "sees any of" in s]
    assert len(disjunctive) == 2, steps
    assert any("<landing text>" in s for s in disjunctive)
    assert any("<result text>" in s for s in disjunctive)
    # The count check keeps its own POM key; nothing else lands in the POM.
    assert "results:" in pom and "result titles" not in pom, pom


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

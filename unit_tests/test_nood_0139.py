"""NOOD_0139 — goal-authoring correctness for multi-stage flows.

NOOD_0137 could mark a goal ready while omitting a prerequisite or accepting
evidence that did not satisfy the requested minimum. These browser-free
regressions pin the four correctness fixes + the honest runtime-only contract:

  G1  a revealed field with needs_pom=false STILL gets a POM entry, and the
      panel's reveal click compiles before the controls it exposes
  G2  a control found only by automatic discovery blocks — no explicit reveal
  G3  declared action order is preserved; a reveal control is reachable only
      via its click (phase-correct)
  G4  an observed count below the requested minimum blocks before any run
  G5  fewer distinct any_of alternatives than `min` is never marked proven
  G6  a check anchored after data the probe never entered is runtime_asserted,
      kept verbatim in the feature, never claimed proven
  G7  the probe executes reveal clicks but NOT commit (save/submit) clicks
"""
from noodle.repl import goal as goal_mod
from noodle.repl import validate as _validate


def _ctrl(name, selector, kind="button", needs_pom=False, **extra):
    c = {"name": name, "selector": selector, "kind": kind,
         "visible": True, "needs_pom": needs_pom, "step": "x"}
    c.update(extra)
    return c


def _rev(revealed_by, controls, headings=None, **extra):
    r = {"controls": controls, "headings": headings or [], "pom_yaml": "",
         "revealed_by": revealed_by}
    r.update(extra)
    return r


def _result(controls=None, revealed=None, search=None, **over):
    pg = {"url": "https://x/", "title": "t", "controls": controls or [],
          "headings": [], "pom_yaml": "", "permission_prompts": [],
          "popups_closed": 0}
    if revealed is not None:
        pg["revealed"] = revealed
    if search is not None:
        pg["search"] = search
    pg.update(over)
    return {"pages": [pg], "errors": []}


def _compile(goal, result):
    ev = goal_mod.evidence(goal, result)
    feat, pom = goal_mod.compile_goal(goal, ev, "APP")
    return ev, feat, pom


# --- G1: revealed config controls — POM regardless of needs_pom ---------------

def _settings_goal():
    return {"scenario": "set delivery preferences",
            "actions": [
                {"do": "click", "target": "open settings", "id": "open"},
                {"do": "enter", "target": "delivery postal code",
                 "value": "K1A 0B1", "id": "zip"},
                {"do": "select", "target": "store location",
                 "option": "Downtown", "id": "store"},
                {"do": "click", "target": "save preferences", "id": "save"}],
            "checks": []}


def _settings_probe():
    revealed = [_rev("open settings", [
        _ctrl("delivery postal code", "input#zip", kind="field",
              needs_pom=False),                       # the dropped-field bug
        _ctrl("store location", "select#store", kind="dropdown",
              options=["Nearest", "Downtown", "Airport"]),
        _ctrl("save preferences", "button#save")])]
    return _result(controls=[_ctrl("open settings", "button#gear")],
                   revealed=revealed)


def test_revealed_needs_pom_false_field_still_gets_a_pom_entry():
    ev, feat, pom = _compile(_settings_goal(), _settings_probe())
    assert ev["blocking"] == []
    # every action target — including the needs_pom=false field — is POM'd
    for key, sel in [("open settings", "button#gear"),
                     ("delivery postal code", "input#zip"),
                     ("store location", "select#store"),
                     ("save preferences", "button#save")]:
        assert f"{key}:" in pom and sel in pom, key


def test_reveal_click_compiles_before_the_controls_it_exposes():
    _, feat, _ = _compile(_settings_goal(), _settings_probe())
    order = [feat.index(s) for s in (
        'clicks "open settings"',
        'enters "K1A 0B1" in the "delivery postal code" field',
        'selects "Downtown" from "store location"',
        'clicks "save preferences"')]
    assert order == sorted(order)                     # declared order preserved
    # engine invents nothing between save and the requested next action
    assert "close" not in feat.lower()


def test_settings_flow_steps_all_match_the_pattern_table():
    _, feat, _ = _compile(_settings_goal(), _settings_probe())
    chk = _validate.check_feature(feat)
    assert chk["error"] is None and _validate.unmatched(chk) == []


def test_requested_option_must_be_among_the_enumerated_options():
    goal = _settings_goal()
    goal["actions"][2]["option"] = "Moon Base"        # not enumerated
    ev, _, _ = _compile(goal, _settings_probe())
    assert any("Moon Base" in b and "not among" in b for b in ev["blocking"])


# --- G2: automatic discovery without an explicit reveal blocks -----------------

def test_discovered_control_without_explicit_click_blocks():
    revealed = [_rev("settings", [_ctrl("delivery postal code", "input#zip",
                                        kind="field")], discovered=True)]
    probe = _result(controls=[_ctrl("settings", "button#gear")],
                    revealed=revealed)
    goal = {"scenario": "s",
            "actions": [{"do": "enter", "target": "delivery postal code",
                         "value": "x", "id": "zip"}],       # no click "settings"
            "checks": []}
    ev, _, _ = _compile(goal, probe)
    assert any("delivery postal code" in b and "automatic discovery" in b
               for b in ev["blocking"])


def test_same_control_reachable_once_an_explicit_reveal_is_added():
    revealed = [_rev("settings", [_ctrl("delivery postal code", "input#zip",
                                        kind="field")], discovered=True)]
    probe = _result(controls=[_ctrl("settings", "button#gear")],
                    revealed=revealed)
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "settings", "id": "o"},
                        {"do": "enter", "target": "delivery postal code",
                         "value": "x", "id": "zip"}],
            "checks": []}
    ev, _, _ = _compile(goal, probe)
    assert ev["blocking"] == []                       # explicit click satisfies it


# --- G3: phase-correct reachability -------------------------------------------

def test_reveal_control_is_not_reachable_from_initial_phase():
    """The exact NOOD_0137 gap: a revealed control treated as initially
    reachable. Without its reveal click, targeting it must block."""
    revealed = [_rev("open panel", [_ctrl("hidden field", "input#h",
                                          kind="field")])]   # explicit, not disc.
    probe = _result(controls=[_ctrl("open panel", "button#p")], revealed=revealed)
    goal = {"scenario": "s",
            "actions": [{"do": "enter", "target": "hidden field",
                         "value": "x", "id": "f"}],    # no click "open panel"
            "checks": []}
    ev, _, _ = _compile(goal, probe)
    assert any('hidden field' in b and "open panel" in b for b in ev["blocking"])


# --- G4: observed count below the requested minimum ---------------------------

def _search_probe(count, text=None):
    return _result(search={"term": "hats", "controls": [], "headings": [],
                           "results_summary": {"text": text or f"{count} results",
                                               "selector": "[id=s]",
                                               "count": count}})


def _count_goal(minimum):
    return {"scenario": "s",
            "actions": [{"do": "search", "term": "hats", "id": "s"}],
            "checks": [{"count": "results summary", "min": minimum, "after": "s"}]}


def test_observed_count_below_minimum_blocks_before_run():
    ev, feat, _ = _compile(_count_goal(3), _search_probe(1))
    assert any("below the requested minimum 3" in b for b in ev["blocking"])
    assert "should be at least 3" in feat        # request still compiled verbatim


def test_observed_count_at_or_above_minimum_is_proven():
    ev, _, _ = _compile(_count_goal(3), _search_probe(40))
    assert ev["blocking"] == []
    assert ev["proven"].get("count:results summary") == "40 results"


def test_unparsable_count_summary_blocks():
    ev, _, _ = _compile(_count_goal(1),
                        _search_probe(None, text="results found"))
    assert any("unable to parse" in b for b in ev["blocking"])


# --- G5: any_of minimum — distinct alternatives, not one match ----------------

def test_single_visible_alternative_never_proves_min_two():
    probe = _result(controls=[_ctrl("Nike Air Max", "a#n", kind="link")])
    goal = {"scenario": "s", "actions": [],
            "checks": [{"any_of": ["Nike", "Adidas"], "min": 2}]}
    ev, _, _ = _compile(goal, probe)
    assert "any_of[0]" not in ev["proven"]
    assert any("below the requested minimum 2" in b for b in ev["blocking"])


def test_two_distinct_alternatives_meet_min_two():
    probe = _result(controls=[_ctrl("Nike Air", "a#n", kind="link"),
                              _ctrl("Adidas Boost", "a#a", kind="link")])
    goal = {"scenario": "s", "actions": [],
            "checks": [{"any_of": ["Nike", "Adidas"], "min": 2}]}
    ev, _, _ = _compile(goal, probe)
    assert ev["blocking"] == [] and "any_of[0]" in ev["proven"]


# --- G6: runtime-only post-transition assertions ------------------------------

def _login_goal():
    return {"scenario": "sign in and land on dashboard",
            "actions": [{"do": "enter", "target": "username",
                         "value": "{env:USER}", "id": "u"},
                        {"do": "enter", "target": "password",
                         "value": "{env:PASS}", "id": "p"},
                        {"do": "click", "target": "sign in", "id": "login"}],
            "checks": [{"see": "My Dashboard", "after": "login"}]}


def _login_probe():
    return _result(controls=[_ctrl("username", "input#u", kind="field"),
                             _ctrl("password", "input#p", kind="field"),
                             _ctrl("sign in", "button#go")])


def test_post_gate_check_is_runtime_asserted_not_proven():
    ev, feat, _ = _compile(_login_goal(), _login_probe())
    assert ev["blocking"] == []                       # no false block
    assert 'the user sees "My Dashboard"' in ev["runtime_asserted"]
    assert "see:My Dashboard" not in ev["proven"]     # never claimed observed
    assert 'the user sees "My Dashboard"' in feat     # preserved in the feature


# --- G7: probe executes reveals, not commits ----------------------------------

def test_probe_skips_commit_clicks_after_data_entry():
    args = goal_mod.probe_args(_settings_goal())
    # "open settings" is a reveal (before the enter); "save preferences" is a
    # commit after data entry — the probe must not click it and mutate state
    assert args["click"] == ["open settings"]
    assert args["open_native_controls"] is True       # select needs options


def test_probe_still_executes_reveal_only_goals():
    goal = {"scenario": "s",
            "actions": [{"do": "click", "target": "menu", "id": "m"},
                        {"do": "click", "target": "products", "id": "p"}],
            "checks": []}
    assert goal_mod.probe_args(goal)["click"] == ["menu", "products"]

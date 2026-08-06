"""NOOD_0234 — zero blocked benchmark shapes: the grammar takes requests the
way people write them.

`noodle benchmark` (headless — the deterministic compiler, nobody driving)
refused 2 of its 5 shapes outright and delivered a third that ran red. A
shape people actually send and the engine cannot take is an engine defect,
not a note in a table, so each refusal was traced to a mechanism:

§1 a hard-wrapped paragraph was shredded at its wrap points, so the
   credentials and the search term arrived as verbless fragments;
§2 credentials on a header line (`user: a / b`) were harvested for URLs and
   then thrown away, so a bare `log in` had nothing to borrow;
§3 an assertion with no subject of its own ("it should be there") refused,
   though `add_to` has resolved that same back-reference since NOOD_0169 —
   and "search IT for X" kept the pronoun as part of the term;
§4 a temporal lead-in ("Once the catalogue loads, …") hid the verb behind it
   from the ^-anchored verb table;
§5 every check behind a walked login gate was routed to the run, so a prose
   assertion could never narrow to what the probe had just read off the
   page and compiled as an unrunnable literal;
§6 `search <box> for <term>` kept the box's own name inside the term — the
   one thing parse time cannot judge and the probe can;
§7 a search that proved nothing at all still authored, and died at first run.

Two invariants hold across all of it, and they are what the tests are for:
a HIT proves, a MISS defers and never blocks (so the benchmark's
deliberately-wrong spec still reaches the run and fails there), and every
narrowing carries provenance into the payload.

Fixtures are shapes, not sites; the five benchmark specs are LOADED from
docs/benchmark-specs.md, never transcribed. No browser, no LLM.
"""
import pytest

from noodle import benchmark
from noodle.agents.web import probe as probe_mod
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as px

# --- the five specs, read from the document that defines them ----------------

SPECS = {s["id"]: s["spec"] for s in benchmark.load_specs()}


def _goal_of(spec_id: str) -> dict:
    exp = px.expand(SPECS[spec_id])
    assert exp["ok"], (spec_id, exp["unresolved"])
    return exp["goal"]


def _terms(goal, do="search"):
    return [a.get("term") for a in goal["actions"] if a["do"] == do]


# --- §1 a hard-wrapped paragraph is one paragraph ----------------------------

def test_flush_left_prose_is_rejoined_at_its_wrap_points():
    text = ("Please sign in to the members area at https://x.test/ using the "
            "account\nada with the password Secret1!. Once the list loads, "
            "search it\nfor Widget.")
    assert [c["text"] for c in px._clauses(text)] == [
        "Please sign in to the members area at https://x.test/ using the "
        "account ada with the password Secret1!",
        "search it for Widget"]


def test_one_bare_step_per_line_is_never_glued_into_one_clause():
    # the regression this rule could have caused: each line IS a step, and a
    # step carries a verb — which is exactly what stops the join
    text = ("go to https://a-fairly-long-example-host.test/catalogue\n"
            "search for kettles\n"
            "verify at least 1 result")
    assert len(px._clauses(text)) == 3


@pytest.mark.parametrize("nxt", [
    "1. click continue", "- click continue", "| Widget |",
    "user: ada / Secret1!", "Base URL: https://x.test", "Notes:",
    "Note: run this headless", "Steps:"])
def test_reflow_never_joins_a_line_that_opens_a_structure(nxt):
    prev = "Some prose that ran out of column width and kept going"
    assert px._wraps_onto(prev, nxt) is False


def test_a_line_that_ends_its_sentence_is_never_continued():
    prev = "Some prose that ran out of column width and stopped here."
    assert px._wraps_onto(prev, "and then some more of it") is False


def test_a_short_line_ended_because_someone_pressed_enter():
    assert px._wraps_onto("close the popup", "Widget") is False


def test_the_numbered_control_spec_is_untouched_by_the_reflow():
    lines = SPECS["steps"].splitlines()
    joined = px._join_wrapped([(i + 1, ln) for i, ln in enumerate(lines)
                               if ln.strip()])
    assert [ln for _, ln in joined] == [ln for ln in lines if ln.strip()]


# --- §2 credentials on a header line feed a bare login -----------------------

def _brief(header="user: ada / Secret1!"):
    return f"url: https://x.test/\n{header}\n\n1. log in\n2. search for Widget"


def test_a_bare_login_borrows_the_briefs_own_credential_line():
    exp = px.expand(_brief())
    assert exp["ok"], exp["unresolved"]
    assert exp["goal"]["actions"][:3] == [
        {"do": "enter", "target": "username", "value": "{env:USERNAME}"},
        {"do": "enter", "target": "password", "value": "{env:PASSWORD}"},
        {"do": "click", "target": "login"}]
    assert exp["secrets"]["USERNAME"] == "ada"
    assert exp["secrets"]["PASSWORD"] == "Secret1!"


def test_the_borrow_names_the_line_it_came_from():
    exp = px.expand(_brief())
    assert any("came from the brief's own 'user:' line" in a
               and "no credential was invented" in a
               for a in exp["assumptions"])


def test_no_credential_VALUE_is_ever_echoed_into_an_assumption():
    # assumptions ride a SUCCESSFUL authoring payload, and one of these two
    # values is a password (NOOD_0228: an assumption is an emitted surface)
    exp = px.expand(_brief())
    blob = "\n".join(exp["assumptions"] + exp["unrecognized"])
    assert "Secret1!" not in blob


@pytest.mark.parametrize("header,user,pw", [
    ("user: ada / Secret1!", "ada", "Secret1!"),
    ("username: ada | Secret1!", "ada", "Secret1!"),
    ("account: ada, Secret1!", "ada", "Secret1!"),
    ("user: ada\npassword: Secret1!", "ada", "Secret1!")])
def test_the_pair_is_read_from_the_shapes_people_write(header, user, pw):
    exp = px.expand(_brief(header))
    assert exp["ok"], exp["unresolved"]
    assert exp["secrets"]["USERNAME"] == user
    assert exp["secrets"]["PASSWORD"] == pw


def test_a_label_with_no_parseable_pair_still_refuses_the_login():
    exp = px.expand(_brief("credentials: see the vault"))
    assert not exp["ok"]
    assert any("needs both credentials" in u["reason"]
               for u in exp["unresolved"])


def test_a_url_valued_login_label_is_still_a_url_not_an_account():
    exp = px.expand("login: https://x.test/in\n1. search for Widget")
    assert exp["ok"], exp["unresolved"]
    assert exp["base_url"] == "https://x.test/in"


def test_a_credential_line_with_no_login_step_is_named_not_spent():
    exp = px.expand("url: https://x.test/\nuser: ada / Secret1!\n"
                    "1. search for Widget")
    assert exp["ok"], exp["unresolved"]
    assert any("no login step to spend" in a for a in exp["assumptions"])
    assert not any(a["do"] == "enter" for a in exp["goal"]["actions"])


# --- §3 anaphora: the assertion, and the search's object ---------------------

def test_an_assertion_with_no_subject_binds_to_the_search_before_it():
    exp = px.expand("go to https://x.test/\n1. search for Widget\n"
                    "2. it should be there")
    assert exp["ok"], exp["unresolved"]
    assert exp["goal"]["checks"] == [{"see": "Widget"}]
    assert any("the subject is the term the flow searched for" in a
               for a in exp["assumptions"])


@pytest.mark.parametrize("claim", [
    "it should be there", "it should be visible", "that should appear",
    "they should be listed", "the result should be shown", "verify it"])
def test_the_presence_claims_people_write_all_bind(claim):
    exp = px.expand(f"go to https://x.test/\n1. search for Widget\n2. {claim}")
    assert exp["ok"], (claim, exp["unresolved"])
    assert exp["goal"]["checks"] == [{"see": "Widget"}]


def test_an_anaphor_with_nothing_before_it_still_refuses():
    exp = px.expand("go to https://x.test/\n1. it should be there")
    assert not exp["ok"]
    assert any("no earlier step names what 'it' is" in u["reason"]
               for u in exp["unresolved"])


@pytest.mark.parametrize("claim", [
    "it should cost $3.99", "it should be in the cart", "the total should be 5",
    "it should say Sold out"])
def test_a_claim_that_names_its_own_text_is_not_an_anaphor(claim):
    assert px._ANAPHORIC_CLAIM.match(claim) is None


def test_a_should_claim_with_a_subject_still_splits_into_its_two_texts():
    exp = px.expand("go to https://x.test/\n1. search for Widget\n"
                    "2. verify the total should be $3.99")
    assert exp["ok"], exp["unresolved"]
    assert {"see": "$3.99"} in exp["goal"]["checks"]
    assert {"see": "Widget"} not in exp["goal"]["checks"]


def test_search_it_for_x_searches_for_x():
    exp = px.expand("go to https://x.test/\n1. search it for Widget")
    assert _terms(exp["goal"]) == ["Widget"]


def test_a_noun_subject_keeps_its_whole_term_at_parse_time():
    # "search gifts for mum" — parse time cannot tell a box name from half a
    # term (NOOD_0232 tried and reverted); only the probe can, and only then
    exp = px.expand("go to https://x.test/\n1. search gifts for mum")
    assert _terms(exp["goal"]) == ["gifts for mum"]


# --- §4 a temporal lead-in never blocks the step it qualifies ----------------

@pytest.mark.parametrize("lead", [
    "Once the list loads", "After the page appears", "When the results open",
    "As soon as the catalogue has loaded"])
def test_a_temporal_lead_in_is_stripped_from_the_step_it_leads(lead):
    exp = px.expand(f"go to https://x.test/\n1. {lead}, search for Widget")
    assert exp["ok"], (lead, exp["unresolved"])
    assert _terms(exp["goal"]) == ["Widget"]


def test_a_lead_in_naming_a_USER_action_is_not_stripped():
    # "After clicking sign in" names a step the flow would silently lose
    exp = px.expand("go to https://x.test/\n"
                    "1. After clicking sign in, search for Widget")
    assert not exp["ok"]


def test_a_lead_in_standing_alone_is_narration_once_actions_exist():
    exp = px.expand("go to https://x.test/\n1. search for Widget\n"
                    "2. Once the list loads\n3. verify Widget")
    assert exp["ok"], exp["unresolved"]
    assert any("names when the next step runs" in a
               for a in exp["assumptions"])


def test_a_conditional_dismissal_still_wins_over_the_lead_in():
    # "When <thing> appears, close it" opens with the same word and owns its
    # whole clause — _CONDITIONAL is checked first, and stays first
    text = ("go to https://x.test/\n"
            "1. When the cookie banner appears, close it\n"
            "2. search for Widget")
    assert [c["text"] for c in px._clauses(text)] == [
        "go to https://x.test/", "close cookie banner", "search for Widget"]
    assert px.expand(text)["ok"]


# --- §5/§7 fixtures: a gated goal the probe walked ---------------------------

def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _gated_goal(check, term="Widget"):
    return {"scenario": "A member searches behind the sign-in",
            "actions": [{"do": "enter", "target": "username", "value": "u"},
                        {"do": "enter", "target": "password", "value": "p"},
                        {"do": "click", "target": "login", "id": "signin"},
                        {"do": "search", "term": term, "id": "searched"}],
            "checks": [check]}


def _walked(result_headings=("Widget Deluxe",), summary=(2, "2 results"),
            expect=None, items=(("Widget Deluxe", "#r1"),)):
    """A probe payload after a successful gate walk: the login prelude was
    typed, the search landed, the results page was snapshotted."""
    search = {"term": "Widget", "url": "https://x/results", "controls": [],
              "headings": list(result_headings), "pom_yaml": ""}
    if summary is not None:
        search["results_summary"] = {"text": summary[1], "count": summary[0],
                                     "selector": ".count", "pom_yaml": "",
                                     "suggested_assertion": "x"}
    if items:
        search["result_items"] = [{"caption": c, "selector": s}
                                  for c, s in items]
    pg = {"url": "https://x/", "title": "Sign in",
          "controls": [_ctrl("username", "#u", kind="field"),
                       _ctrl("password", "#p", kind="field"),
                       _ctrl("login", "#in", submit=True)],
          "headings": ["Members"], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0,
          "revealed": [{"url": "https://x/home", "title": "Lounge",
                        "controls": [], "headings": ["Lounge"],
                        "pom_yaml": "", "performed": True,
                        "gate_walk": True, "revealed_by": "do: click login"}],
          "search": search, "do_requested": 3, "do_completed": 3}
    if expect is not None:
        pg["expect"] = expect
    return {"pages": [pg], "errors": []}


# --- §5 a walked gate lets the search page prove its own checks --------------

def test_a_check_the_probe_read_off_the_results_page_is_proven():
    goal = _gated_goal({"see": "Widget Deluxe"})
    ev = goal_mod.evidence(goal, _walked())
    assert ev["blocking"] == []
    assert ev["proven"].get("see:Widget Deluxe") == "Widget Deluxe"
    assert ev["proven_phase"]["see:Widget Deluxe"] == "search"


def test_a_prose_assertion_narrows_to_the_term_the_flow_searched_for():
    goal = _gated_goal({"see": "the Widget listing is what you are left "
                                "looking at"})
    ev = goal_mod.evidence(goal, _walked())
    assert ev["blocking"] == []
    assert goal["checks"][0]["see"] == "Widget"
    assert ev["narrowed"] == [
        {"from": "the Widget listing is what you are left looking at",
         "to": "Widget", "probed": "Widget Deluxe"}]


def test_a_narrowing_needs_the_page_to_render_the_term_too():
    goal = _gated_goal({"see": "the Widget listing is what you are left "
                                "looking at"})
    ev = goal_mod.evidence(goal, _walked(result_headings=("Nothing here",),
                                         items=()))
    assert ev["narrowed"] == []
    assert any("Widget listing" in s for s in ev["runtime_asserted"])


def test_a_miss_behind_a_walked_gate_defers_and_never_blocks():
    goal = _gated_goal({"see": "Order confirmed"})
    ev = goal_mod.evidence(goal, _walked())
    assert ev["blocking"] == []
    assert any("Order confirmed" in s for s in ev["runtime_asserted"])


def test_an_any_of_miss_behind_a_walked_gate_defers_too():
    goal = _gated_goal({"any_of": ["Order confirmed", "Receipt"]})
    ev = goal_mod.evidence(goal, _walked())
    assert ev["blocking"] == []
    assert ev["runtime_asserted"]


def test_a_landing_page_check_is_not_proven_by_the_search_page():
    # `after: start` scopes to the landing page — the walk proves nothing
    # there (NOOD_0233), and this must stay true
    goal = _gated_goal({"see": "Widget Deluxe", "after": "start"})
    ev = goal_mod.evidence(goal, _walked())
    assert any("Widget Deluxe" in b for b in ev["blocking"])


def test_an_unwalked_goal_keeps_todays_reach_deferral_verbatim():
    result = _walked()
    result["pages"][0]["revealed"] = []          # no gate walk happened
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}), result)
    assert ev["blocking"] == []
    assert any("Widget Deluxe" in s for s in ev["runtime_asserted"])
    assert ev["proven"].get("see:Widget Deluxe") is None


# --- §6 the box's own name licenses the cut ----------------------------------

@pytest.mark.parametrize("term,box,want", [
    ("movies for The Shining", "Search movies", "The Shining"),
    ("gifts for mum", "Search", None),
    ("gifts for mum", "search gifts", "mum"),
    ("The Shining", "Search movies", None),
    ("movies", "Search movies", None),
    ("Widget", "", None),
    ("", "Search movies", None)])
def test_a_term_is_narrowed_only_by_the_boxs_own_name(term, box, want):
    assert probe_mod.narrow_search_term(term, box) == want


def test_the_probes_correction_reaches_the_compiled_goal():
    goal = _gated_goal({"see": "Widget"}, term="wares for Widget")
    result = _walked()
    result["pages"][0]["search"].update(term="Widget",
                                        term_narrowed_from="wares for Widget",
                                        box_name="Search wares")
    ev = goal_mod.evidence(goal, result)
    assert _terms(goal) == ["Widget"]
    assert any("search term narrowed" in w and "Search wares" in w
               for w in ev["warnings"])


def test_an_unnarrowed_search_says_nothing_about_narrowing():
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}), _walked())
    assert not any("term narrowed" in w for w in ev["warnings"])
    assert ev["term_narrowed"] == []


# --- §8 a narrowing is a CORRECTION, and is counted as one -------------------
#
# The independent session review caught this: the benchmark printed CORR 0 on
# a run where the engine had weakened one assertion and rewritten one search
# term. `docs/benchmark-specs.md` defines CORR as "every repair the engine
# made on your behalf: … a dropped or rewritten check", and both of those
# rode a free-text warning that no counter could see.

def test_a_narrowed_term_is_structured_not_just_narrated():
    goal = _gated_goal({"see": "Widget"}, term="wares for Widget")
    result = _walked()
    result["pages"][0]["search"].update(term="Widget",
                                        term_narrowed_from="wares for Widget",
                                        box_name="Search wares")
    ev = goal_mod.evidence(goal, result)
    assert ev["term_narrowed"] == [
        {"from": "wares for Widget", "to": "Widget", "box": "Search wares"}]


def test_the_benchmark_counts_a_narrowing_as_a_correction():
    author = {"rewritten_checks": [{"from": "the Widget listing is what you "
                                            "are left looking at",
                                    "to": "Widget"}],
              "narrowed_terms": [{"from": "wares for Widget",
                                  "to": "Widget", "box": "Search wares"}]}
    n, named = benchmark._corrections(author, {}, {})
    assert n == 2
    what = " | ".join(x["what"] for x in named)
    assert "check(s) reworded" in what
    # a narrowed TERM is not a control name reworded to the probed one, and
    # saying so named the wrong repair to whoever read the detail
    assert "search term(s) narrowed" in what
    assert "control name(s) reworded" not in what


def test_a_narrowing_whose_probe_text_IS_the_narrowing_reads_sensibly():
    # an --expect verdict is an exact full-text hit, so `to` == `probed`;
    # the old template then said "found 'Alien' inside 'Alien'"
    goal = _gated_goal({"see": "the Widget listing is what you are left "
                                "looking at"})
    result = _walked(result_headings=(), items=(),
                     expect=[{"text": "Widget", "found": True}])
    ev = goal_mod.evidence(goal, result)
    assert ev["narrowed"] == [
        {"from": "the Widget listing is what you are left looking at",
         "to": "Widget", "probed": "Widget"}]


# --- §7 a search that proved nothing -----------------------------------------

def _proved_nothing(walked=True):
    result = _walked(result_headings=(), summary=None, items=(),
                     expect=[{"text": "Widget Deluxe", "found": False}])
    if not walked:
        result["pages"][0]["revealed"] = []
    return result


def test_a_search_that_proved_nothing_behind_a_gate_warns_and_defers():
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}),
                           _proved_nothing())
    assert ev["blocking"] == []
    assert any("proved nothing on the results page" in w
               for w in ev["warnings"])


def test_a_search_that_proved_nothing_with_full_reach_blocks():
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}),
                           _proved_nothing(walked=False))
    assert any("proved nothing on the results page" in b
               for b in ev["blocking"])


def test_any_one_sign_of_life_keeps_the_search_authorable():
    # a page with no count element and no --expect verdicts is the ordinary
    # case, not a dead one — this gate must never fire on it
    result = _walked(summary=None, expect=None)
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}), result)
    assert ev["blocking"] == []
    assert not any("proved nothing" in w for w in ev["warnings"])


def test_the_zero_results_gate_still_blocks_verbatim():
    ev = goal_mod.evidence(_gated_goal({"see": "Widget Deluxe"}),
                           _walked(summary=(0, "0 results"), items=()))
    assert any("observed 0 results" in b for b in ev["blocking"])


# --- §8 the five specs, end to end -------------------------------------------

def test_every_benchmark_spec_compiles():
    refused = {s["id"]: px.expand(s["spec"])["unresolved"]
               for s in benchmark.load_specs()}
    assert {k: v for k, v in refused.items() if v} == {}


def test_the_paragraph_logs_in_searches_and_asserts():
    goal = _goal_of("paragraph")
    assert [a["do"] for a in goal["actions"]] == \
        ["enter", "enter", "click", "search"]
    assert _terms(goal) == ["Alien"]
    assert goal["checks"] == [
        {"see": "Alien listing is what you are left looking at"}]


def test_the_ambiguous_spec_logs_in_from_its_header_line():
    goal = _goal_of("ambiguous")
    assert goal["actions"][:3] == [
        {"do": "enter", "target": "username", "value": "{env:USERNAME}"},
        {"do": "enter", "target": "password", "value": "{env:PASSWORD}"},
        {"do": "click", "target": "login"}]
    assert _terms(goal) == ["Ghostbusters"]
    assert goal["checks"] == [{"see": "Ghostbusters"}]


def test_the_one_liner_keeps_its_whole_term_until_the_probe_sees_the_box():
    goal = _goal_of("one_liner")
    assert _terms(goal) == ["movies for The Shining"]
    assert probe_mod.narrow_search_term("movies for The Shining",
                                        "Search movies") == "The Shining"


def test_the_control_spec_is_byte_identical():
    goal = _goal_of("steps")
    assert [a["do"] for a in goal["actions"]] == \
        ["enter", "enter", "click", "enter"]
    assert goal["checks"] == [{"see": "Jaws"}]


# --- §9 the deliberately-wrong spec still fails, by design -------------------

def test_the_expected_fail_spec_still_authors_its_wrong_assertion_verbatim():
    goal = _goal_of("expected_fail")
    assert goal["checks"] == [{"see": "Casablanca"}]
    assert [a["do"] for a in goal["actions"]] == \
        ["enter", "enter", "click", "enter"]


def test_the_wrong_assertion_reaches_the_run_and_is_never_narrowed():
    goal = _goal_of("expected_fail")
    result = _walked(result_headings=("Halloween",),
                     items=(("Halloween", "#r1"),),
                     expect=[{"text": "Casablanca", "found": False}])
    ev = goal_mod.evidence(goal, result)
    assert ev["blocking"] == []
    assert ev["narrowed"] == []
    assert any("Casablanca" in s for s in ev["runtime_asserted"])
    assert goal["checks"] == [{"see": "Casablanca"}]

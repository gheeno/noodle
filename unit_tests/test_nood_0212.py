"""NOOD_0212 — a pasted brief authors without a rewrite lap, and the escape
hatches for an ambiguous control actually work.

Every case here came from a real session in which the engine refused, or
authored something that died at run time, on a prompt a human wrote by hand.
The expensive ones refuse at clause 1: each retry re-pays the whole transcript,
so a brief that needs three laps costs roughly three times what it should.
"""
import pytest
import yaml

from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

URL = "https://shop.example.com/en.html"


# --- the brief scaffolding that used to refuse --------------------------------

@pytest.mark.parametrize("line", [
    # bare section headers — the clause splitter strips the trailing colon, so
    # _META_LABEL (which requires one) never saw them
    "Web Test",
    "AC :",
    "AC:",
    "Acceptance Criteria :",
    "Pre-requisites :",
    # addressed to the agent, not the browser
    "Generate a Noodle test in this workspace",
    "Create a test for the checkout flow",
    # about how the test should be written or reported
    "Agent rules: follow AGENTS.md (token economy + step-writing rules)",
    "pre-requisites in Background:, step-dictionary sentences only, validate",
    "before running, finish with the Allure + RCA report links and nothing else",
])
def test_brief_scaffolding_is_metadata_not_a_refusal(line):
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] == "metadata"


@pytest.mark.parametrize("line,kind", [
    ("click the Continue button", "click"),
    ("search for a toy", "search"),
    ("add to cart", "add_to"),
    ("verify the page shows Welcome", "verify"),
    (f"go to {URL}", "nav"),
])
def test_real_steps_keep_their_verb(line, kind):
    """The rescue runs only after every verb has had its turn — a step always
    wins. Without this the new patterns would eat real instructions."""
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] == kind


@pytest.mark.parametrize("line", [
    # the word "test"/"generate" appears, but the clause is an ATTEMPTED STEP
    "create a test account",
    "generate a report from the dashboard",
    "create a new account",
    "write the summary into the notes field",
])
def test_near_misses_still_refuse_rather_than_being_swallowed(line):
    """These are unparsed today — the point is that they keep refusing with a
    rewrite hint. Silently filing a real instruction as brief noise would drop
    it from the test without anyone noticing."""
    assert pe._parse_clause(pe._clauses(line)[0])["kind"] != "metadata"


# --- a URL that never says the word "url" -------------------------------------

def test_ui_label_carries_the_base_url_and_its_back_reference_resolves():
    """`UI : <url>` … `go to UI` — the brief's own alias. Refused before for
    want of a URL, because only a label containing the word "url" counted."""
    exp = pe.expand(f"Web Test\nUI : {URL}\nAC :\n1. go to UI\n"
                    "2. assert that the page returns 200")
    assert not exp.get("unresolved")
    assert exp["goal"]["navigation"] == [URL]


def test_url_label_wrapped_in_prose_yields_the_url_not_the_sentence():
    """"base url : run this on <url>" used to compile a click on the whole
    sentence, so the brief ended with no URL and its own "open the website"
    line refused."""
    exp = pe.expand(f"base url : Noodle run on {URL}\n"
                    "1. Open the website\n2. Search for a toy")
    assert not exp.get("unresolved")
    assert exp["goal"]["navigation"] == [URL]


def test_a_brief_with_no_url_anywhere_still_refuses():
    """The fix must not paper over the genuine case."""
    exp = pe.expand("1. Open the website\n2. Search for a toy")
    assert [u["text"] for u in exp["unresolved"]] == ["Open the website"]


# --- caller-supplied POM survives goal mode -----------------------------------

def test_caller_pom_entries_are_folded_into_the_compiled_page_block():
    """core.py used to rebind the caller's pom_content to compile_goal's
    return value, dropping it without a word — so the documented way to pin a
    control the compiler cannot infer silently did nothing."""
    goal = {"scenario": "order status", "navigation": [URL],
            "actions": [{"do": "click", "id": "c1", "target": "order status"}],
            "checks": [{"see": "Please enter your email address"}]}
    ev = {"proven": {}, "proven_phase": {}, "blocking": [], "runtime_asserted": []}
    _, pom = goal_mod.compile_goal(
        goal, ev, "SHOP",
        extra_pom="order status:\n  css: 'a[data-id=\"order-status\"]'\n")
    pages = yaml.safe_load(pom)["pages"]
    entry = next(iter(pages.values()))
    assert entry["order status"]["css"] == 'a[data-id="order-status"]'


def test_caller_pom_rides_the_page_pin_not_a_filename_scope():
    """Folded INSIDE the @page block on purpose: the pin spans every URL the
    scenario visits, so a control clicked to REACH a page still resolves. A
    sibling pageobjects/<page>_pom.yaml is scoped to its own filename, which
    never covers the page the control actually sits on."""
    goal = {"scenario": "order status", "navigation": [URL],
            "actions": [{"do": "click", "id": "c1", "target": "order status"}],
            "checks": [{"see": "hello"}]}
    ev = {"proven": {}, "proven_phase": {}, "blocking": [], "runtime_asserted": []}
    _, pom = goal_mod.compile_goal(goal, ev, "SHOP",
                                   extra_pom="order status:\n  css: 'a.x'\n")
    assert "pages:" in pom and "match:" not in pom


@pytest.mark.parametrize("text", [None, "", "   ", "not: [valid", "- a\n- b"])
def test_unusable_caller_pom_is_ignored_rather_than_crashing(text):
    assert goal_mod._flat_pom_entries(text) == {}


def test_a_caller_authored_pages_block_is_left_alone():
    """They scoped it themselves; re-nesting it would change the ask."""
    assert goal_mod._flat_pom_entries("pages:\n  mine:\n    a: b\n") == {}


# --- the wordings the regression drill's own briefs use -----------------------

def test_search_phrased_target_first_is_still_a_search():
    """"In the search bar, type 'Vaccu'" — the enter verb wants
    "<value> into <target>", so the inverted order refused."""
    n = pe._parse_clause(pe._clauses(
        'In the search bar, type "Vaccu" — deliberately incomplete')[0])
    assert n["kind"] == "search" and n["term"] == "Vaccu"


def test_suggestion_named_before_the_word_suggestion_pairs_with_the_search():
    """"Click the vacuum-cleaner suggestion, worded exactly as the site
    renders it" used to compile a click on that whole descriptive phrase."""
    exp = pe.expand(f"1. go to {URL}\n"
                    '2. In the search bar, type "Vaccu"\n'
                    "3. Click the vacuum-cleaner suggestion, worded exactly "
                    "as the site renders it\n"
                    '4. verify the page shows "Some Vacuum"')
    act = exp["goal"]["actions"][0]
    assert act["do"] == "suggest"
    assert act["term"] == "Vaccu" and act["option"] == "vacuum-cleaner"


def test_results_page_narration_is_scene_setting_not_a_step():
    assert pe._parse_clause(pe._clauses(
        "The results page lists these products")[0])["kind"] == "observation"


def test_a_wrapped_directive_paragraph_does_not_refuse_on_its_tail():
    """The clause splitter works line by line, so "…finish with the Allure +
    RCA report links and" / "nothing else" arrived as two clauses; the head
    matched as scaffolding and the verbless tail refused on its own."""
    exp = pe.expand(f"1. go to {URL}\n2. verify the page shows \"Hello\"\n"
                    "Follow AGENTS.md — validate before running, finish with "
                    "the Allure + RCA report links and\nnothing else.")
    assert not exp.get("unresolved")


def test_an_outcome_worded_verify_is_never_silently_dropped():
    """Guard on a rule that was tried and REVERTED. Dropping a "Verify:" line
    that looks like a goal restatement also ate "order is placed successfully",
    a real assertion — a test that proves less than it claims is worse than a
    lap spent fixing the wording. If this fails, that rule came back."""
    exp = pe.expand("1. go to https://x.example\n"
                    "2. verify order is placed successfully, subtotal is "
                    "shown, items listed")
    assert len(exp["goal"]["checks"]) == 3

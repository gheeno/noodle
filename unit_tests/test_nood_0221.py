"""NOOD_0221 — "Verify: Suggestion search works" is a coverage summary.

Briefs end with a line naming what the test is ABOUT — the same register as
the `AC:` and `Note:` lines already classified as metadata. No page renders
that sentence, so it compiled to a see-check that could only be dropped
later: the 0218/0220 benchmark's TC1 was the one row reading
`intent_verified: false`, on a test that had proven every real assertion
asked of it.

The rule is deliberately NARROW, because NOOD_0212's lesson is that a wording
rule which guesses eats real assertions. Two conditions, both required:
the clause ENDS in the summary verb, and it carries NO quoted text. Quoting
is the escape hatch, and the assumption line says so.
"""
import pytest

from noodle.repl import prompt_expander as pe

BASE = "1. go to https://app.example\n2. search for \"widgets\"\n"


def _expand(*steps):
    r = pe.expand(BASE + "".join(f"{i + 3}. {s}\n"
                                 for i, s in enumerate(steps)))
    assert r["ok"], r.get("error")
    return r


def _checks(*steps):
    return _expand(*steps)["goal"]["checks"]


# --- what the rule skips -----------------------------------------------------

@pytest.mark.parametrize("summary", [
    "Verify: Suggestion search works",
    "verify search works",
    "verify the checkout works.",
    "verify login is working",
    "verify checkout functions correctly",
    "verify the cart functions as intended",
    "verify search works properly",
    "verify the flow is functional",
])
def test_a_summary_line_compiles_to_no_assertion(summary):
    checks = _checks('verify "Deluxe Widget"', summary)
    assert checks == [{"see": "Deluxe Widget"}]


def test_the_skip_is_announced_with_its_escape_hatch():
    exp = _expand('verify "Deluxe Widget"', "Verify: Suggestion search works")
    note = next(a for a in exp["assumptions"] if "Suggestion search" in a)
    assert "summary of what the test covers" in note
    assert "quote the exact wording to assert it" in note


def test_the_clause_is_covered_not_refused():
    """A skipped summary must not read as an unparsed clause — that would
    surface as an unresolved step and cost the very lap this removes."""
    exp = _expand('verify "Deluxe Widget"', "Verify: Suggestion search works")
    assert exp["coverage"][-1]["status"] == "metadata"
    assert not exp.get("unresolved")
    assert not exp.get("unrecognized")


# --- what the rule must NOT touch --------------------------------------------

def test_quoting_is_the_escape_hatch():
    """Quoted content is a claim about the page, always — even when it ends
    in the summary verb. A real page printing "Free Shipping works" stays
    assertable."""
    assert _checks('verify "Free Shipping works"') == [
        {"see": "Free Shipping works"}]


@pytest.mark.parametrize("claim,expected", [
    # adjacent phrasings deliberately left OUT of the family: real pages
    # print all of these, so skipping them would drop a genuine assertion
    ("verify the totals are correct", "totals are correct"),
    ("verify payment is successful", "payment is successful"),
    ("verify the order is complete", "order is complete"),
    # the verb is not at the END, so the clause is ordinary page text
    ("verify works of art", "works of art"),
])
def test_adjacent_wording_keeps_its_assertion(claim, expected):
    assert {"see": expected} in _checks(claim)


def test_a_prompt_that_is_only_a_summary_still_blocks():
    """The skip must not manufacture a green: a brief whose ONLY assertion was
    a summary line has nothing to test, and the NOOD_0199 guard says so."""
    r = pe.expand("1. go to https://app.example\n2. verify search works")
    assert not r["ok"]
    assert "nothing to test" in r["error"]

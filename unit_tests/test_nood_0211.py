"""NOOD_0211 — evidence by Gherkin keyword, and the prompt clauses that used
to refuse a complete brief.

The driving case: an AC that reads perfectly to a human refused at the
`--prompt` door, and satisfying "each assertion must contain an evidence
screenshot" meant pasting `( take a screenshot )` onto every line.
"""
from noodle.repl.prompt_expander import (
    _drop_shared_noun,
    _strip_containment,
    evidence_intent,
    expand,
)
from noodle.reporting import evidence

# --- the evidence gate -----------------------------------------------------

def _wanted(**kw):
    base = dict(tags=(), requested=False, is_last_step=False, has_page=True,
                is_assertion=False)
    return evidence.wanted(**{**base, **kw})


def test_assertions_mode_captures_a_then_and_skips_a_when(monkeypatch):
    """behave reports `Then` and an `And` chained under it as step_type
    'then' — that IS the "this step is an assertion" signal."""
    monkeypatch.setenv("NOODLE_EVIDENCE", "assertions")
    assert _wanted(is_assertion=True)
    assert not _wanted(is_assertion=False)


def test_the_tag_does_the_same_without_touching_the_env():
    assert _wanted(tags=("web", "evidence:assertions"), is_assertion=True)
    assert not _wanted(tags=("web", "evidence:assertions"), is_assertion=False)


def test_no_evidence_beats_the_assertions_tag():
    """An explicit "no screenshots" is a decision. It must not be
    second-guessed — not by the mode, not by the always-capture-the-last
    rule, not by an assertions directive."""
    assert not _wanted(tags=("evidence:assertions", "no_evidence"),
                       is_assertion=True)
    assert not _wanted(tags=("no_evidence",), is_last_step=True)
    assert not _wanted(tags=("no_evidence",), requested=True)


def test_the_default_is_unchanged():
    """Existing suites must not suddenly shoot every assertion."""
    assert _wanted(is_last_step=True)
    assert not _wanted(is_assertion=True)      # default 'last', not 'assertions'


def test_an_explicit_marker_still_wins_over_the_mode(monkeypatch):
    monkeypatch.setenv("NOODLE_EVIDENCE", "off")
    assert _wanted(requested=True)


def test_a_bad_mode_degrades_to_last_not_to_silence(monkeypatch):
    monkeypatch.setenv("NOODLE_EVIDENCE", "assertion")   # typo, singular
    assert evidence.mode() == "last"


def test_no_page_means_no_shot():
    """@api/@appium/@visual scenarios have no Playwright page."""
    assert not _wanted(has_page=False, is_assertion=True, requested=True)


# --- evidence directives in the prompt -------------------------------------

def test_the_note_line_is_read_as_a_directive():
    assert evidence_intent(
        "Note : each assertion must contain an evidence screenshot") \
        == "assertions"


def test_no_screenshot_is_respected():
    for t in ("NO SCREENSHOT", "no screenshots please",
              "without screenshots", "screenshots are not required"):
        assert evidence_intent(t) == "off", t


def test_off_beats_assertions_when_a_brief_says_both():
    assert evidence_intent(
        "screenshot every assertion. actually, no screenshots") == "off"


def test_an_ordinary_brief_asks_for_nothing():
    assert evidence_intent("go to example.com and search for kettles") is None


def test_the_directive_survives_the_clause_splitter():
    """THE regression: the splitter treats a trailing "evidence screenshot"
    as a per-step MARKER and strips it, so by parse time the directive had
    been shortened to "Note : each assertion must contain an" — the exact
    string the old refusal quoted back. Scanning the raw brief avoids it."""
    r = expand("1. go to https://example.com\n2. verify \"Hi\"\n"
               "Note : each assertion must contain an evidence screenshot.")
    assert r["ok"]
    assert r["goal"].get("evidence") == "assertions"
    assert r["unrecognized"] == []


def test_no_screenshot_reaches_the_goal():
    r = expand("1. go to https://example.com\n2. verify \"Hi\"\nNO SCREENSHOT")
    assert r["goal"].get("evidence") == "off"


# --- the two clauses that refused ------------------------------------------

def test_using_search_bar_without_to_or_comma_parses():
    """The other _INSTRUMENT arms lean on "to" or a comma to bound the
    preamble; this phrasing has neither."""
    r = expand("1. go to https://example.com\n"
               "2. Using search bar search for Steve Jobs")
    assert r["ok"] and r["unrecognized"] == []
    assert r["goal"]["actions"] == [
        {"do": "search", "id": "search1", "term": "Steve Jobs"}]


def test_other_instrument_phrasings_still_parse():
    for line in ("Use the search bar to search for kettles",
                 "Using the search bar, search for kettles",
                 "via the search box search for kettles"):
        r = expand(f"1. go to https://example.com\n2. {line}")
        assert r["ok"], line
        assert r["goal"]["actions"][0]["term"] == "kettles", line


# --- page status is not a text claim ---------------------------------------

def test_assert_page_returns_200_becomes_a_status_check():
    """Compiled to `the user sees "UI page returns 200"` — a literal no page
    renders — so a routine AC blocked on evidence that could never exist."""
    r = expand("1. go to https://example.com\n2. assert that UI page returns 200")
    assert {"page_status": 200, "after": "start"} in r["goal"]["checks"]


def test_the_page_status_check_compiles_to_the_web_step():
    from noodle.repl.goal import _check_step
    assert _check_step({"page_status": 200})[0] == "the page should return 200"


def test_a_rest_status_check_is_untouched():
    from noodle.repl.goal import _check_step
    assert _check_step({"status": 200})[0] == "the response status should be 200"


# --- prose that names where to look ----------------------------------------

def test_a_page_subject_is_stripped_from_the_assertion():
    assert _strip_containment("page contains Steve Jobs") == "Steve Jobs"
    assert _strip_containment(
        "Main Page contains : From today's feature article") \
        == "From today's feature article"


def test_a_non_page_subject_is_left_alone():
    """'the error message contains X' — the subject IS the element being
    asserted about, not a place to look."""
    for t in ("the error message contains timeout",
              "the cart contains toy",
              "the response body contains 'total'"):
        assert _strip_containment(t) == t, t


# --- a category noun shared across conjuncts -------------------------------

def test_a_shared_trailing_noun_is_dropped_from_its_peer():
    """'Born and Died dates' splits to 'Born' + 'Died dates'; the page shows
    'Died'. Blocking on the second half of a phrase whose first half already
    proved out is the whole failure."""
    assert _drop_shared_noun(
        ["page contains Steve Jobs", "Born", "Died dates", "we can see it"]) \
        == ["page contains Steve Jobs", "Born", "Died", "we can see it"]


def test_it_fires_mid_list_not_only_at_the_tail():
    assert _drop_shared_noun(["Name", "Address fields"]) == ["Name", "Address"]


def test_it_never_strands_a_determiner():
    """'the dates and times' must not become 'the' and 'times'."""
    assert _drop_shared_noun(["the dates", "times"]) == ["the dates", "times"]
    assert _drop_shared_noun(["all values", "totals"]) == ["all values", "totals"]


def test_a_single_member_is_untouched():
    assert _drop_shared_noun(["Died dates"]) == ["Died dates"]


# --- the whole brief -------------------------------------------------------

def test_the_original_ac_expands_with_nothing_unrecognized():
    """End to end: the brief that used to return NEEDS_INTERPRETATION."""
    r = expand("""1. go to https://en.wikipedia.org/wiki/Main_Page
2. assert that UI page returns 200
3. assert hat UI contains "Welcome to Wikipedia" banner
4. assert that Main Page contains : From today's feature article
5. Using search bar search for Steve Jobs
6. assert that page contains Steve Jobs, Born and Died dates, and we can see his signature.

Note : each assertion must contain an evidence screenshot.""")
    assert r["ok"] and r["unrecognized"] == [] and r["conflicts"] == []
    g = r["goal"]
    assert g["evidence"] == "assertions"
    assert {"page_status": 200, "after": "start"} in g["checks"]
    assert g["actions"] == [{"do": "search", "id": "search1",
                             "term": "Steve Jobs"}]
    seen = [c.get("see") for c in g["checks"]]
    for expected in ("Welcome to Wikipedia", "Steve Jobs", "Born", "Died",
                     "his signature"):
        assert expected in seen, expected

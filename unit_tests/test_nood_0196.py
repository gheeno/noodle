"""NOOD_0196 — an --expect verdict answers for a goal that never searches.

The feature-regression benchmark went REGRESSED on tc3 while the test it
generated ran green and verified: the compiler blocked authoring with

    check "From Wikipedia, the free encyclopedia": no probed heading or
    control shows that text

against a probe payload whose own expect verdict recorded that exact string
as `found: true`. NOOD_0195 scoped expect verdicts to checks at the search
landing page, which is right when the goal searches — but with no search in
the goal the probe ENDS on the initial page, so the verdict describes the
very page the check observes. Discarding it there blocked every check on
body prose (Wikipedia's `#siteSub`), which the structured capture of
headings and controls never carries.
"""
from noodle.repl import goal as G

_SUB = "From Wikipedia, the free encyclopedia"


def _probe(expect):
    return {"errors": [], "pages": [
        {"url": "https://en.wikipedia.org/wiki/Vacuum_cleaner", "title": "t",
         "controls": [], "pom_yaml": "", "permission_prompts": [],
         "popups_closed": 0, "headings": ["Vacuum cleaner"],
         "expect": expect}]}


def test_expect_proves_body_text_when_the_goal_never_searches():
    # tc3's shape: api call + navigation + a check on prose no heading carries.
    goal = {"scenario": "s",
            "actions": [{"do": "api", "method": "GET", "url": "https://x/a"}],
            "checks": [{"status": 200}, {"see": _SUB}]}
    ev = G.evidence(goal, _probe([{"text": _SUB, "found": True}]))
    assert ev["blocking"] == [], ev["blocking"]
    assert f"see:{_SUB}" in ev["proven"]
    assert f'the user sees "{_SUB}"' in G.compile_goal(goal, ev, "APP")[0]


def test_a_not_found_expect_still_blocks():
    # The gate only widens for a verdict the probe actually returned FOUND.
    goal = {"scenario": "s", "actions": [], "checks": [{"see": _SUB}]}
    ev = G.evidence(goal, _probe([{"text": _SUB, "found": False}]))
    assert any(_SUB in b for b in ev["blocking"]), ev["blocking"]

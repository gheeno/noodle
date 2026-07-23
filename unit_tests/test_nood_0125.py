"""NOOD_0125 — PROMPT_TEMPLATE is a task brief, not a second copy of AGENTS.md.

The old prompt duplicated rules 1-8 (which every agent client already reads
from the auto-loaded workspace AGENTS.md) and demanded procedural "Steps a
human would take". This contract asserts the deduped prompt: one pointer to
AGENTS.md, the task facts an agent can't infer, and the both-reports output
contract — with no rule block and no procedural steps. No browser, no LLM."""
from noodle.agents.web import probe
from noodle.cli import _PROMPT_TEMPLATE
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# NOOD_0125 — probe's results-count suggestion is a stable floor, not a
# snapshot. A barebones prompt ("make sure we see more than 1 item") must
# generate a test that stays green when the live count shifts — baking today's
# "45 results" into ">= 45" turns the next run red for no regression, and the
# fix-loop churn that follows is exactly the AIC budget we watch.

def test_probe_summary_assertion_is_a_stable_floor():
    step = probe._summary_assertion()
    assert step == "the number in 'results summary' should be at least 1"
    # resolves deterministically — a suggestion the resolver can't match would
    # recreate the guess-and-fail loop
    assert pattern_match(normalize_phrasing(step)), step


def test_probe_does_not_bake_live_count_into_the_assertion():
    # the observed count stays visible as context in the render, but never
    # inside the copy-paste assertion
    sr = probe.summarize({"controls": [], "headings": []},
                         url="https://shop.example/s?q=widgets")
    sr["term"] = "widgets"
    sr["results_summary"] = {
        "text": "45 results", "selector": 'span[class~="count"]', "count": 45,
        "pom_yaml": 'results summary:\n  css: "span"\n',
        "suggested_assertion": probe._summary_assertion(),
    }
    home = probe.summarize({"controls": [], "headings": []},
                           url="https://shop.example/")
    home["search"] = sr
    out = probe.render({"pages": [home], "errors": []})
    assert '"45 results"' in out                     # observed count = context
    assert "should be at least 1" in out             # assertion = stable floor
    assert "should be at least 45" not in out        # snapshot never baked in


def test_prompt_is_task_brief_not_operating_manual():
    tpl = _flat(_PROMPT_TEMPLATE)

    # points to AGENTS.md and names the Noodle task
    assert "AGENTS.md" in tpl
    assert "Use Noodle to create and run this test." in tpl

    # keeps the facts an agent can't infer
    for field in (
        "App under test:",
        "Base URL:",
        "User goal:",
        "Verify:",
        "Credentials/config:",
        "Shell commands in replies: [ok | do not output the shell command]",
    ):
        assert field in tpl, field

    # both-reports output contract, red-run RCA not suppressed
    assert "Allure and RCA report links" in tpl
    assert "red run" in tpl and "compact RCA reason" in tpl

    # no procedural steps and no duplicated AGENTS.md rule block
    assert "Steps a human would take" not in tpl
    assert "Agent rules, in order" not in tpl
    for rule_fragment in ("max 2 sentences", "max 10", "probe_page before", "Validate the .feature"):
        assert rule_fragment not in tpl, rule_fragment

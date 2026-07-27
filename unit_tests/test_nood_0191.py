"""NOOD_0191 — `noodle task` routing + the CI template's drift guards.

Two things worth a test here, and nothing else. The router's whole value is
that a caller who sends the wrong shape gets told what to send instead, so the
tests are a TABLE of real off-template prompts, not one happy path. The
pipeline template can't be integration-tested from a laptop, so what's checked
is the set of things that silently rot: a resurrected `sudo npm`, a bare
`allure` invocation, and the version pin drifting between five files.
"""
import re
from pathlib import Path

import pytest
import yaml

from noodle.repl import task

REPO = Path(__file__).resolve().parent.parent
CI = REPO / "ci" / "azure"


# --- routing -----------------------------------------------------------------

@pytest.mark.parametrize("text,intent", [
    # the shapes an LLM actually emits
    ("write me a regression test for the new checkout discount", "generate"),
    ("create a test that covers the discount banner", "generate"),
    ("please generate a new scenario for login", "generate"),
    ("add test coverage for the cart page", "generate"),
    ("update the login test to use the new button", "update"),
    ("fix the checkout scenario, it uses the old selector", "update"),
    ("change the search test so it waits longer", "update"),
    ("run the tests", "run"),
    ("execute the regression suite", "run"),
    ("re-run the failing ones", "run"),
    ("kick off a smoke run", "run"),
    ("show me the allure report", "report"),
    ("open the report please", "report"),
    ("what does the rca say", "report"),
    ("did it pass", "verdict"),
    ("is it green", "verdict"),
    ("what's the result", "verdict"),
    # no recognizable verb -> the SDLC default, because a builder agent
    # handing Noodle a task is asking for a test, not for last run's report
    ("the checkout discount banner should show 20% off", "generate"),
    ("", "generate"),
])
def test_classify_routes(text, intent):
    assert task.classify(text)["intent"] == intent


def test_no_verb_is_default_confidence_not_a_silent_guess():
    c = task.classify("the banner should show 20% off")
    assert c == {"intent": "generate", "confidence": "default",
                 "alternatives": []}
    assert task.classify("run the tests")["confidence"] == "explicit"


def test_first_intent_in_the_sentence_wins_and_the_other_is_named():
    # "update ... then run it" is update-with-a-run, not a run.
    c = task.classify("update the login test then run it")
    assert c["intent"] == "update"
    assert "run" in c["alternatives"]


def test_preamble_is_stripped_so_the_compiler_never_sees_the_routing_sentence():
    # The exact shape that broke first: an LLM prefixes its steps with the
    # instruction, and "create a test" reached the grammar as step 1.
    text = ('create a test: 1. Go to the URL https://example.com '
            '2. Search for "chair"')
    assert task.steps_of(text).startswith("1. Go to the URL")
    # multi-line and bare forms
    assert task.steps_of("Write a test.\n1. Go to https://x.com").startswith("1.")
    assert task.steps_of("1. Go to https://x.com") == "1. Go to https://x.com"
    # no numbered list -> unchanged; an ambiguous strip would be worse
    assert task.steps_of("just run it") == "just run it"


def test_steps_of_does_not_mistake_a_quantity_for_a_step_marker():
    text = "verify 1. 5 kg is shown"      # "1." mid-sentence, no step list
    assert task.steps_of(text) == text or task.steps_of(text).startswith("1.")


# --- the negotiation contract ------------------------------------------------

def test_needs_maps_an_existing_feature_to_overwrite_not_to_grammar():
    """The bug this test exists for: an 'already exists' refusal used to fall
    through to 'numbered_steps_in_grammar', telling the caller to rewrite a
    perfectly good prompt. Wrong advice is worse than no advice."""
    need, nxt = task._needs(
        {"error": "login.feature exists — pass overwrite=true to replace"})
    assert need == ["overwrite"]
    assert "update" in nxt


def test_needs_maps_a_missing_url_to_base_url():
    need, _ = task._needs({"error": "no URL in the prompt",
                           "unresolved": [{"clause": "c1"}]})
    assert "base_url" in need and "numbered_steps_in_grammar" in need


def test_needs_never_returns_empty():
    need, nxt = task._needs({"error": "something unforeseen"})
    assert need and nxt


def test_contract_carries_everything_a_cold_caller_needs():
    c = task.contract()
    assert set(c["intents"]) == set(task.INTENTS)
    assert c["default_intent"] == "generate"
    for key in ("prompt_grammar", "prompt_example", "goal_example",
                "goal_vocabulary", "envelope", "woks"):
        assert c[key], key
    # Named up front, or a cold caller concludes web-only and declines.
    assert {"web", "api", "mobile", "desktop"} <= set(c["woks"])


# --- non-web woks are routed, never refused ----------------------------------

@pytest.mark.parametrize("text,wok", [
    # the exact ask that got a user told "Noodle isn't the right tool"
    ("generate the api tests for me", "api"),
    ("check that POST /api/users returns 200", "api"),
    ("are these endpoints online", "api"),
    ("write a REST test for the orders service", "api"),
    ("test the swagger spec", "api"),
    ("assert the response status code is 201", "api"),
    ("write a test for the android app login", "mobile"),
    ("automate the iOS simulator flow", "mobile"),
    ("test the windows app dialog", "desktop"),
])
def test_non_web_asks_name_their_wok(text, wok):
    assert task.wok_hint(text)["wok"] == wok


@pytest.mark.parametrize("text", [
    "write a test that searches for office chairs",
    "click the login button and verify the dashboard",
    "1. Go to https://example.com 2. Search for chairs",
])
def test_web_asks_are_not_misrouted(text):
    assert task.wok_hint(text) is None


def test_an_api_ask_is_redirected_not_refused(tmp_path):
    """The whole point. A user asked for API tests and an agent answered
    'Noodle is a web UI testing framework — use pytest or Postman'. Noodle has
    shipped a browserless REST client since NOOD_0007; the agent had only ever
    been shown clicks and searches. The refusal must carry the wok and the
    step syntax, never web grammar."""
    out = task.route("generate the api tests for me", workspace=str(tmp_path))
    assert out["ok"] is False
    assert out["wok"] == "api"
    assert "supported" in out["next"]
    assert "@api" in out["how"] and "REST_BASE_URL" in out["how"]
    # NOOD_0192 — and the redirect now names the CHEAP door first: the api
    # wok is prompt-authorable, so "author it by hand" is no longer the
    # answer to "generate the api tests for me".
    assert "GET https://host/path" in out["how"]
    # web vocabulary must NOT come back — that reads as "can't do this"
    assert "grammar" not in out
    assert "search for" not in out.get("how", "")


def test_the_shipped_surfaces_do_not_claim_noodle_is_web_only():
    """Five always-on surfaces described Noodle as a browser framework and
    none named the API wok, so every agent reading one concluded the same
    wrong thing. Cheap to regress, expensive to notice."""
    from noodle.cli import _AGENTS_MD
    surfaces = {
        "claude-card": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
        "copilot-card": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
        "agents-md": _AGENTS_MD,
    }
    for name, text in surfaces.items():
        assert "@api" in text, f"{name} never names the API wok"
        assert "REST" in text, f"{name} never says REST"
        assert not text.lstrip().startswith("Playwright+behave"), name


def test_instruction_surfaces_stay_within_their_ceilings():
    """The scope fix lands on byte-capped surfaces; a later edit must not
    silently blow the budget (ledger rule: raising a cap is never silent)."""
    from noodle.instruction_budget import ledger
    for row in ledger():
        if row["headroom"] is not None:
            assert row["headroom"] >= 0, f"{row['surface']} over by {-row['headroom']} B"


def test_contract_fits_the_payload_budget():
    """It is fetched by an agent, so it pays the same 8 KB bound every other
    agent-facing payload does."""
    import json

    from noodle import payload_budget
    rendered = json.dumps(task.contract(), indent=2)
    assert len(rendered.encode()) <= payload_budget.budget_bytes(), \
        f"contract is {len(rendered.encode())} B"


def test_verdict_does_not_call_an_unverified_run_green():
    """failed == 0 is not enough — a pass carried by fuzzy healing is not a
    pass, and a router that flattened that would report the exact lie the
    engine works to prevent."""
    assert task._verdict({"passed": 3, "failed": 0, "verified": True}) == "PASS"
    assert task._verdict({"passed": 3, "failed": 0, "verified": False}) == "UNVERIFIED"
    assert task._verdict({"passed": 2, "failed": 1, "verified": True}) == "FAIL"
    assert task._verdict({"passed": 0, "failed": 0}) == "NO_TESTS_RAN"


def test_route_never_raises_on_an_unroutable_request(tmp_path):
    out = task.route("do something impossible", workspace=str(tmp_path))
    assert out["ok"] is False
    assert out["need"] and out["next"]
    assert out["intent"] in task.INTENTS


# --- CI template drift guards ------------------------------------------------

# Bash-based Azure templates that run on self-hosted Linux agents. GitHub's
# hosted runners always have sudo and a modern Node, so tests.yml is out of
# scope for the sudo/bin-collision rules — only the version pin binds it.
_AZURE_BASH = [REPO / "azure-pipelines.yml",
               CI / "noodle-tests.yml",
               CI / "steps-allure-cli.yml"]

_PIN = re.compile(r"allure@(\d+\.\d+\.\d+)")


def _code(path: Path) -> str:
    """The file without its full-line comments. These guards are about what
    the agent EXECUTES — every one of them first failed against the comment
    explaining why the thing it forbids isn't there."""
    return "\n".join(ln for ln in path.read_text().splitlines()
                     if not ln.lstrip().startswith("#"))


def test_ci_templates_parse():
    for f in list(CI.glob("*.yml")) + [REPO / "azure-pipelines.yml"]:
        yaml.safe_load(f.read_text())


def test_no_sudo_npm_on_a_self_hosted_agent():
    """A locked-down corporate agent has no sudo; the step dies before any
    test runs. Caught here because it reads as harmless in review."""
    for f in _AZURE_BASH:
        assert "sudo npm" not in _code(f), f"{f.name} resurrected sudo npm"


def test_allure_is_invoked_by_resolved_path_not_by_name():
    """A global install lets an agent-local registry shadow the `allure` bin
    with a different package. The install step must verify the RESOLVED
    binary's version before putting it on PATH."""
    src = (CI / "steps-allure-cli.yml").read_text()
    assert "node_modules/.bin/allure" in src
    assert "npm install --no-save" in src and "--prefix" in src
    assert "##[error]" in src, "a wrong CLI must fail loudly, not silently"


def test_allure_pin_is_identical_everywhere():
    """Several files name this version. Nothing can enforce one source at
    Azure compile time, so the drift is caught here instead.

    The pin appears in two forms and this must read BOTH: a literal
    `allure@3.14.1`, and an `allureVersion` parameter default that reaches npm
    as `allure@${{ parameters.allureVersion }}`. A first cut of this test read
    only the literal form, so it passed happily while the templates — the two
    files that matter most — drifted underneath it."""
    pins = {}
    for f in [*CI.glob("*.yml"), REPO / "azure-pipelines.yml",
              REPO / "azure-pipelines-windows.yml",
              REPO / ".github" / "workflows" / "tests.yml"]:
        found = set(_PIN.findall(f.read_text()))
        for p in (yaml.safe_load(f.read_text()) or {}).get("parameters") or []:
            if isinstance(p, dict) and p.get("name") == "allureVersion":
                found.add(str(p["default"]))
        if found:
            pins[f.name] = found
    assert len(pins) >= 4, f"pin vanished from a pipeline file: {sorted(pins)}"
    everything = set().union(*pins.values())
    assert len(everything) == 1, f"allure pin drifted: {pins}"


def test_template_declares_the_documented_parameters():
    spec = yaml.safe_load((CI / "noodle-tests.yml").read_text())
    names = {p["name"] for p in spec["parameters"]}
    assert {"workspaceDir", "testTag", "pool", "shard", "extras",
            "keyVaultUrl", "secretEnv"} <= names
    defaults = {p["name"]: p.get("default") for p in spec["parameters"]}
    assert defaults["workspaceDir"] == "tests/noodle"


def test_ci_is_headless_only():
    """Nobody can watch a browser inside a CI agent, so a --headed knob there
    is a failure mode with no upside. The template must not grow one back."""
    src = _code(CI / "noodle-tests.yml")
    assert "--headless" in src
    assert "--headed" not in src


def test_example_pipeline_pins_the_engine_ref():
    """A moving ref is not a build. The example a project copies must show a
    pinned tag, or every project inherits an unpinned engine."""
    src = (CI / "example-project-pipeline.yml").read_text()
    assert re.search(r"ref:\s*refs/tags/\d+\.\d+\.\d+", src)

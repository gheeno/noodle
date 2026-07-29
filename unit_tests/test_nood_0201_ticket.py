"""NOOD_0201 — a ticket payload is the test plan: read it, correct it, or refuse.

The failure these pin: an AI-SDLC agent handed Noodle a raw JIRA issue whose
criteria said POST /greeting while the service served POST /greeting/new. The
authored test 404'd, and the 404 looked like a product bug. Everything here is
offline — the fixture is committed, the endpoint list is passed in.
"""
import json
from pathlib import Path

import pytest

from noodle import ticket
from noodle.repl import goal as goal_mod
from noodle.repl import validate

REPO = Path(__file__).resolve().parents[1]
FIXTURE = (REPO / "sample_feature_tests" / "api" / "resources" / "tickets"
           / "busterblock_reviews_story.json")
# What BusterBlock really serves (its committed openapi.json is the source).
SERVED = ["GET /api/health", "POST /api/auth/login", "GET /api/movies",
          "GET /api/movies/{id}", "GET /api/reviews", "POST /api/reviews/new",
          "GET /api/reviews/{id}", "DELETE /api/reviews/{id}"]


@pytest.fixture
def parsed():
    return ticket.parse(FIXTURE.read_text(encoding="utf-8"))


# --- reading the payload ----------------------------------------------------------

def test_the_demo_premise_stays_intact():
    """The whole point is that the ticket's wording and the served route
    DISAGREE. If someone 'fixes' the fixture or the app, these tests still pass
    while proving nothing — so pin the disagreement itself."""
    assert "/api/review with" in FIXTURE.read_text(encoding="utf-8"), \
        "the fixture must keep saying POST /api/review (singular)"
    spec = json.loads((REPO / "test-apps" / "busterblock"
                       / "openapi.json").read_text(encoding="utf-8"))
    assert "/api/reviews/new" in spec["paths"], \
        "the app must keep serving the create route at /api/reviews/new"
    assert "post" not in spec["paths"].get("/api/reviews", {}), \
        "POST /api/reviews must stay unserved — it is the trap"


def test_adf_is_walked_and_the_spec_link_boilerplate_is_stripped(parsed):
    assert parsed["ok"] and parsed["key"] == "BB-42"
    assert parsed["summary"].startswith("Customer reviews")
    assert parsed["issue_type"] == "Story" and parsed["project"] == "BusterBlock API"
    body = " ".join(parsed["description"])
    assert "As a renter" in body                     # the real intent survives
    for noise in ("SPEC_LINK", "Spec Anchor", "Generated Sha256",
                  "Anchor Heading"):
        assert noise not in body, f"{noise} is provenance, never test intent"


def test_acceptance_criteria_split_into_given_when_then(parsed):
    acs = {a["id"]: a for a in parsed["acceptance_criteria"]}
    assert list(acs) == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]
    assert "valid JSON body" in " ".join(acs["AC-2"]["given"])
    assert "request is processed" in " ".join(acs["AC-2"]["when"])
    assert "returns a JSON response containing" in " ".join(acs["AC-2"]["then"])


def test_endpoints_the_ticket_names_are_extracted(parsed):
    named = {(e["method"], e["path"]) for e in parsed["endpoints_mentioned"]}
    assert ("POST", "/api/review") in named      # the ticket's own wording
    assert ("GET", "/api/reviews") in named


def test_non_ticket_payloads_fall_through_rather_than_erroring():
    assert ticket.parse("not json at all")["ok"] is False
    assert ticket.parse('{"hello": "world"}')["ok"] is False
    assert ticket.parse(["a", "list"])["ok"] is False
    # a bare fields block is still a ticket
    assert ticket.parse({"summary": "x", "acceptanceCriteria": None})["ok"] is True


# --- resolving the ticket's wording against reality --------------------------------

def test_match_endpoint_corrects_the_route_the_ticket_got_wrong():
    m = ticket.match_endpoint("POST", "/api/review", SERVED)
    assert m["matched"] == "POST /api/reviews/new"
    assert m["corrected"] is True and m["confidence"] == "resolved"
    # the original NOOD_0201 report, verbatim
    atlas = ticket.match_endpoint("POST", "/greeting",
                                 ["POST /greeting/new", "GET /greetings"])
    assert atlas["matched"] == "POST /greeting/new" and atlas["corrected"]


def test_match_endpoint_prefers_the_exact_route_when_there_is_one():
    m = ticket.match_endpoint("GET", "/api/reviews", SERVED)
    assert m["matched"] == "GET /api/reviews"
    assert m["confidence"] == "exact" and m["corrected"] is False


def test_match_endpoint_asks_instead_of_picking_a_side():
    tie = ticket.match_endpoint("POST", "/order",
                               ["POST /orders/new", "POST /order/create"])
    assert tie["matched"] is None and tie["confidence"] == "ambiguous"
    assert len(tie["candidates"]) == 2 and "which one" in tie["question"]
    miss = ticket.match_endpoint("POST", "/invoices", SERVED)
    assert miss["matched"] is None and miss["confidence"] == "none"
    assert "no endpoint the app serves resembles" in miss["question"]


def test_method_scopes_the_match():
    # GET /api/reviews exists; a DELETE criterion must not silently match it
    m = ticket.match_endpoint("DELETE", "/api/reviews", SERVED)
    assert m["matched"] == "DELETE /api/reviews/{id}"


# --- planning: what is testable, what is honestly not ------------------------------

def test_plan_produces_authorable_goals_with_the_corrected_route(parsed):
    plan = ticket.plan(parsed, endpoints=SERVED,
                       base_url="http://localhost:3333")
    goals = {g["id"]: g for g in plan["goals"]}
    assert set(goals) == {"AC-2", "AC-3", "AC-5"}
    assert goals["AC-2"]["endpoint"] == "POST /api/reviews/new"
    assert any("app serves POST /api/reviews/new" in a
               for a in goals["AC-2"]["assumptions"])
    # the happy path carries the field names the criterion listed
    checks = goals["AC-2"]["goal"]["checks"]
    assert {"status": 201, "after": "call"} in checks
    named = {c["response_contains"] for c in checks if "response_contains" in c}
    assert {"id", "name", "date"} <= named
    # the rejection path is a client error with no field assertions
    assert goals["AC-3"]["goal"]["checks"] == [{"status": 400, "after": "call"}]
    assert any("rejection" in a for a in goals["AC-3"]["assumptions"])


def test_plan_refuses_the_criteria_the_api_cannot_show(parsed):
    plan = ticket.plan(parsed, endpoints=SERVED,
                       base_url="http://localhost:3333")
    skipped = {s["id"]: s["reason"] for s in plan["not_automatable"]}
    # AC-1 is a coding-standards criterion; AC-4 asserts database state
    assert set(skipped) == {"AC-1", "AC-4"}
    for reason in skipped.values():
        assert "names no HTTP method and path" in reason


def test_plan_asks_for_what_the_ticket_cannot_answer(parsed):
    plan = ticket.plan(parsed, endpoints=SERVED,
                       base_url="http://localhost:3333")
    assert any("what request body" in q for q in plan["questions"])
    # the placeholder is visible, never a plausible-looking invented payload
    ac2 = next(g for g in plan["goals"] if g["id"] == "AC-2")
    assert "<value>" in ac2["goal"]["actions"][0]["body"]
    # no base URL → the discovery route is named, not guessed
    assert any("api-scan" in q for q in ticket.plan(parsed, endpoints=SERVED)["questions"])


def test_plan_flags_pagination_as_unfinished_rather_than_claiming_it(parsed):
    plan = ticket.plan(parsed, endpoints=SERVED, base_url="http://x")
    ac5 = next(g for g in plan["goals"] if g["id"] == "AC-5")
    assert any("pagination" in a for a in ac5["assumptions"]), \
        "'ordered by date descending' is not asserted by a bare 200 — say so"


def test_every_planned_goal_is_schema_valid_and_compiles_to_resolvable_steps(parsed):
    """The end-to-end claim: a raw ticket becomes a test the engine can run,
    with no model call anywhere in the chain."""
    plan = ticket.plan(parsed, endpoints=SERVED,
                       base_url="http://localhost:3333")
    for entry in plan["goals"]:
        g = entry["goal"]
        # the author fills the one thing the ticket never said
        for a in g["actions"]:
            if a.get("body") == '{"name":"<value>"}':
                a["body"] = '{"name":"Ticket Tina"}'
        norm, _ = goal_mod.normalize(g)
        assert goal_mod.validate(norm) == [], entry["id"]
        assert goal_mod.needs_browser(norm) is False   # pure API: no browser
        feature, pom = goal_mod.compile_goal(
            norm, goal_mod.browserless_evidence(norm), "BUSTERBLOCK_API")
        assert feature.startswith("@api\n") and pom is None
        check = validate.check_feature(feature)
        assert check["error"] is None, entry["id"]
        assert validate.unmatched(check) == [], entry["id"]


# --- the committed samples this ticket ships ---------------------------------------

@pytest.mark.parametrize("rel", [
    "sample_feature_tests/api/features/busterblock_api.feature",
    "sample_feature_tests/api/features/busterblock_batch.feature",
    "sample_feature_tests/api/features/busterblock_from_ticket.feature",
    "sample_feature_tests/web/busterblock/features/api_seeds_ui_verifies.feature",
])
def test_every_new_sample_parses_and_every_step_resolves(rel):
    """A sample nobody can run is documentation that lies. These are the four
    features NOOD_0201 ships; all of them ran green against a live BusterBlock,
    and this keeps every step in them matched as the pattern table changes."""
    text = (REPO / rel).read_text(encoding="utf-8")
    check = validate.check_feature(text)
    assert check["error"] is None, check["error"]
    assert validate.unmatched(check) == []


def test_the_combo_sample_is_tagged_web_not_api():
    """@api means 'start no browser', which would kill the UI half. The most
    common cross-wok mistake, and a sample must never model it."""
    text = (REPO / "sample_feature_tests" / "web" / "busterblock" / "features"
            / "api_seeds_ui_verifies.feature").read_text(encoding="utf-8")
    assert text.startswith("@web ")
    assert "@api" not in text.splitlines()[0]


def test_the_batch_sample_does_not_paste_repeated_calls():
    """The defect this ticket opened with: 21 near-identical call lines. The
    sample that replaces it must not quietly regrow them."""
    text = (REPO / "sample_feature_tests" / "api" / "features"
            / "busterblock_batch.feature").read_text(encoding="utf-8")
    calls = [ln for ln in text.splitlines()
             if "performs a POST call at '/api/reviews/new'" in ln]
    assert len(calls) <= 4, "batches, not pasted calls"
    assert "repeated 20 times expecting status 201" in text
    assert "for each row expecting status 201:" in text

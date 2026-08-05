"""NOOD_0232 — the spec-shape benchmark.

`noodle benchmark --gate` varies the FLOW and holds the phrasing pinned at
one value; `noodle benchmark` varies the phrasing and holds the app still.
This file pins the parts of that which a later change could quietly break,
and every one of them is here because a benchmark run got it wrong first:

  specs live in the doc   the prompts are parsed out of docs/benchmark-specs.md
                          at run time. A benchmark whose prompts are a Python
                          list has two copies of every prompt the moment it is
                          documented, and the copy a human pastes stops being
                          the one measured.
  a spec that must fail   B5's assertion is wrong on purpose. Every other spec
                          is written to succeed, so nothing else here can catch
                          an engine that reaches green by dropping what it
                          could not prove — the worst failure the tool has.
  blocked, not scored     a shape the compiler cannot take today is recorded
                          with its measured rejection. A gate that is red on
                          its own backlog is a gate nobody reads.
  distinct titles         two specs over the same actions with different
                          assertions used to compile to the SAME scenario name,
                          the same Allure historyId, and so the second EVICTED
                          the first from the report. Requirement D — every spec
                          in one report — is unreachable without that fix.
"""
from pathlib import Path

import pytest

from noodle import benchmark as bench
from noodle.instruction_budget import _ANSI_RE


def _case(sid="steps", expect="pass", outcome="passed", **kw):
    base = {"id": sid, "ref": "B2", "label": "step by step", "expect": expect,
            "outcome": outcome, "feature": f"features/spec_{sid}.feature",
            "elapsed_s": 13.0, "run_s": 11.0, "development_s": 2.0,
            "payload_tokens": 814, "corrections": 0, "correction_detail": [],
            "lines": 21, "passed": 1, "failed": 0, "verified": True,
            "intent_verified": True, "unverified_reasons": [],
            "llm_cost": None, "why": None, "unresolved": []}
    return base | kw


def _blocked(sid, **kw):
    return _case(sid, expect="blocked",
                 **{"outcome": "blocked", "feature": None, "elapsed_s": 0.1,
                    "run_s": None, "development_s": None,
                    "payload_tokens": 500, "corrections": None, "lines": None,
                    "passed": 0, "verified": False,
                    "why": "prompt step(s) not understood"} | kw)


def _failing(sid="expected_fail", **kw):
    return _case(sid, expect="fail",
                 **{"outcome": "failed", "run_s": 29.0, "development_s": 2.5,
                    "payload_tokens": 1928, "passed": 0, "failed": 1,
                    "verified": False,
                    "unverified_reasons": ["1 scenario(s) failed"]} | kw)


def _results(cases, *, report=None):
    return {"workspace": "/tmp/bench", "cases": cases,
            "llm_mode": "deterministic only (NOODLE_MODEL unset)",
            "app_under_test": "BusterBlock @ http://127.0.0.1:3333",
            "build": "noodle 1.0.0a46 (source tree)",
            "report": report if report is not None else
            {"seconds": 0.5, "ok": True, "urls": ["http://x/"],
             "features": 2, "scenarios": 2, "expected_features": 2}}


# ── the specs are the document ──────────────────────────────────────────────

def test_specs_are_parsed_from_the_document_not_hard_coded():
    """The whole reason the prompts live in markdown. A second copy in Python
    is a copy that drifts, and the one that drifts is always the one a human
    pasted from."""
    src = (bench.__file__ and open(bench.__file__, encoding="utf-8").read())
    for spec in bench.load_specs():
        assert spec["spec"] not in src, (
            f"{spec['id']}'s prompt text is duplicated inside benchmark.py — "
            f"it must exist only in {bench.SPEC_DOC}")


def test_every_shape_the_ticket_asked_for_is_present():
    """B1-B5: a paragraph, step by step, one sentence, a short ambiguous spec,
    and a spec that must fail."""
    specs = {s["id"]: s for s in bench.load_specs()}
    assert set(specs) == {"paragraph", "steps", "one_liner", "ambiguous",
                          "expected_fail"}
    assert specs["expected_fail"]["expect"] == "fail"
    assert specs["steps"]["expect"] == "pass"


def test_every_spec_declares_a_known_expect_and_a_non_empty_block():
    for s in bench.load_specs():
        assert s["expect"] in bench.EXPECTS, s["id"]
        assert s["spec"].strip(), f"{s['id']} has an empty block"
        assert bench.BASE_URL in s["spec"], (
            f"{s['id']} does not target BusterBlock — every spec must, or the "
            "rows are not comparable")


def test_a_document_with_no_markers_is_a_setup_fault_not_a_verdict(tmp_path):
    doc = tmp_path / "specs.md"
    doc.write_text("# no markers here\n\n```\n1. go somewhere\n```\n",
                   encoding="utf-8")
    with pytest.raises(bench.BenchmarkError):
        bench.load_specs(doc)


def test_a_marker_with_no_block_is_rejected(tmp_path):
    doc = tmp_path / "specs.md"
    # encoding= is not decoration: load_specs reads utf-8 explicitly, so a
    # default write encodes this em dash as cp1252 0x97 on Windows and the
    # read dies with UnicodeDecodeError before the rejection under test is
    # ever reached (the NOOD_0195 rule, on the writing side).
    doc.write_text("<!-- spec: lonely | expect: pass -->\n### B9 — nothing\n",
                   encoding="utf-8")
    with pytest.raises(bench.BenchmarkError):
        bench.load_specs(doc)


# ── the spec that must fail ─────────────────────────────────────────────────

def test_a_spec_that_must_fail_and_does_is_a_pass():
    v = bench.score(_results([_case(), _failing()]))
    assert v["verdict"] == "PASS"
    assert v["tally"] == {"passed": 1, "failed": 1, "blocked": 0, "error": 0}


def test_a_spec_that_must_fail_but_goes_green_is_the_headline_regression():
    """The worst outcome the benchmark can produce. A false assertion that
    reports green means the engine reached a pass without proving what was
    asked — and every other green in the table is worth less for it."""
    v = bench.score(_results([_case(), _failing(outcome="passed", failed=0,
                                                passed=1, verified=True)]))
    assert v["verdict"] == "REGRESSED"
    assert any("went GREEN on an assertion that is false" in r
               for r in v["regressions"])


def test_a_spec_that_must_fail_but_never_authored_is_a_regression():
    """Blocked is not the same as failed. A B5 that stops authoring stops
    testing failure handling, silently."""
    v = bench.score(_results([_case(),
                              _failing(outcome="blocked", feature=None,
                                       why="not understood")]))
    assert v["verdict"] == "REGRESSED"
    assert any("expected to author and fail, blocked" in r
               for r in v["regressions"])


def test_a_red_spec_is_not_charged_for_carrying_its_diagnosis():
    """A red run's payload carries the RCA the caller acts on. Scoring B5 on
    payload size would score the engine for doing its job, and the fix a
    reader would reach for is to return less about the failure."""
    v = bench.score(_results([_case(), _failing(payload_tokens=9999)]))
    assert v["verdict"] == "PASS"
    assert not [r for r in v["regressions"] if "expensive" in r]


# ── blocked shapes ──────────────────────────────────────────────────────────

def test_a_blocked_shape_is_recorded_not_scored():
    v = bench.score(_results([
        _case(), _blocked("paragraph", why="clause splitting is line-based")]))
    assert v["verdict"] == "PASS"
    assert not v["regressions"]
    assert any("line-based" in (b["why"] or "") for b in v["blocked"])


def test_a_blocked_shape_carries_its_rejected_clauses():
    """The rejected clauses and the rewrites offered against them ARE the
    actionable half of a blocked run — a refusal that coaches costs one cheap
    lap, one that does not costs a human."""
    v = bench.score(_results([_case(), _blocked(
        "one_liner", unresolved=[{"text": "log in as x", "suggested": None},
                                 {"text": "search it", "suggested": 'search for "<term>"'}])]))
    out = bench.render_table(v)
    assert "log in as x" in out
    assert 'search for "<term>"' in out


def test_a_blocked_shape_that_starts_working_is_celebrated_not_failed():
    v = bench.score(_results([_case(), _case("paragraph", expect="blocked")]))
    assert v["verdict"] == "PASS"
    assert any("promote it to `expect: pass`" in c for c in v["gaps_closed"])
    assert bench.SPEC_DOC in v["gaps_closed"][0]


def test_budgets_do_not_grade_a_blocked_shape():
    """A refusal has no development time worth a ceiling — grading it rewards
    refusing faster."""
    v = bench.score(_results([_case(), _blocked("ambiguous", elapsed_s=9999.0,
                                                payload_tokens=99999)]))
    assert v["verdict"] == "PASS"


def test_a_blocked_shape_reports_no_corrections_rather_than_zero():
    """Nothing was generated, so nothing could have been corrected. A 0 there
    is a measured-looking lie in the direction that flatters the engine."""
    v = bench.score(_results([_case(), _blocked("paragraph")]))
    blocked = next(c for c in v["cases"] if c["id"] == "paragraph")
    assert blocked["corrections"] is None
    assert not blocked["failures"]
    assert v["average"]["corrections"] == 0      # averaged over what was seen
    assert "—" in bench.render_table(v)


# ── the measurements the ticket asked for ───────────────────────────────────

def test_development_time_excludes_the_generated_tests_own_run_time():
    """The 'under two minutes' criterion is about generating the test, not
    about how long the site takes to drive."""
    b = bench.budget()
    assert b["max_development_s"] == 120
    v = bench.score(_results([_case(development_s=119.0, run_s=600.0)]))
    assert v["verdict"] == "PASS"
    v = bench.score(_results([_case(development_s=121.0, run_s=1.0)]))
    assert any("slow development" in r for r in v["regressions"])


def test_corrections_are_counted_and_named():
    """A spec that goes green after four repairs is not the same product as
    one that went green first time, and both end `passed`."""
    author = {"rewritten_targets": [{"from": "sign in", "to": "login"}],
              "dropped_checks": [{"see": "nope"}]}
    run = {"healing_events": [{}, {}], "flaky": []}
    n, detail = bench._corrections(author, run, {})
    assert n == 4
    assert any("DROPPED" in d["what"] for d in detail)
    assert any("self-healed" in d["what"] for d in detail)


def test_too_many_corrections_names_which_ones():
    """'2 corrections > 1' sends a reader to the logs; naming them does not."""
    v = bench.score(_results([_case(
        corrections=5,
        correction_detail=[{"n": 5, "what": "locator(s) self-healed at run time"}])]))
    assert any("self-healed" in r for r in v["regressions"])


def test_an_unverified_pass_is_not_a_pass_and_carries_its_reason():
    v = bench.score(_results([_case(
        verified=False,
        unverified_reasons=["'search films' healed via partial-text"])]))
    assert v["verdict"] == "REGRESSED"
    assert any("partial-text" in r for r in v["regressions"])
    assert "unverified" in bench.render_table(v)


def test_token_cost_is_what_the_caller_receives(tmp_path):
    """Collapsed AND bounded, in that order — the pair of transforms every
    agent door applies before a payload crosses the wire. Measuring the raw
    envelope reports a cost nobody pays, and reports it as growing on builds
    where only the diagnosis the door already trims got bigger."""
    import json

    from noodle import payload_budget
    # `run.healing_events`, not `blocking`: the door's bound never trims
    # `blocking` (it is the one list an author must read in full), so a fixture
    # built on it would assert that the bound does nothing.
    fat = {"ok": False,
           "run": {"healing_events": ["a locator that had to be healed" * 20
                                      for _ in range(200)]}}
    raw = len(json.dumps(fat).encode()) / 4
    tokens = bench._tokens(fat, str(tmp_path))
    assert tokens < raw / 10, "the door's bound was not applied"
    assert tokens * 4 <= payload_budget.budget_bytes() + 400


def test_llm_spend_is_reported_not_scored():
    """Payload tokens are the engine's own and get a ceiling; what the engine
    spends with a model moves with the model, so it is printed instead."""
    v = bench.score(_results([_case(llm_cost={"calls": 1, "input_tokens": 900,
                                              "output_tokens": 120,
                                              "usd": 0.0031,
                                              "model": "anthropic/x"})]))
    assert v["verdict"] == "PASS"
    assert v["llm"]["calls"] == 1
    assert "~$0.0031" in bench.render_table(v)


def test_no_llm_spend_says_none_rather_than_omitting_the_line():
    """A missing cost line reads as 'not measured', not as 'free'."""
    assert "LLM     none" in bench.render_table(bench.score(_results([_case()])))


# ── the one report ──────────────────────────────────────────────────────────

def test_a_report_missing_a_spec_that_ran_is_a_regression():
    """Requirement D, checked rather than assumed. This is the defect that
    started it: two features, one in the served report, silently."""
    v = bench.score(_results(
        [_case(), _failing()],
        report={"seconds": 0.5, "ok": True, "urls": ["http://x/"],
                "features": 1, "scenarios": 1, "expected_features": 2}))
    assert v["verdict"] == "REGRESSED"
    assert any("does not cover every spec that ran" in r
               for r in v["regressions"])


def test_an_unhosted_report_is_a_regression():
    v = bench.score(_results([_case()], report={"seconds": 0.2, "ok": False,
                                                "urls": [], "error": "boom"}))
    assert v["verdict"] == "REGRESSED"
    assert any(r.startswith("report:") and "boom" in r for r in v["regressions"])


def test_two_specs_differing_only_in_their_assertion_get_distinct_titles():
    """The eviction bug, at its root. `see` was the ONE check kind that never
    reached the scenario title, so two tests over the same actions with
    different assertions compiled to the same Feature name, the same Allure
    historyId, and the second replaced the first in the accumulating report.
    Action VALUES stay out on purpose — a title carrying `enter 'Popcorn1!'`
    would print a credential into every report."""
    from noodle.repl import prompt_expander
    base = ('1. Go to the URL http://127.0.0.1:3333/\n'
            '2. Enter "reel_ryan" in the "username" field\n'
            '3. Enter "Popcorn1!" in the "password" field\n'
            '4. Click "login"\n'
            '5. Enter "{term}" in the "search movies" field\n'
            "6. Verify: {want}")
    a = prompt_expander.expand(base.format(term="Jaws", want="Jaws"))
    b = prompt_expander.expand(base.format(term="Halloween",
                                           want="Casablanca"))
    assert a["goal"]["scenario"] != b["goal"]["scenario"]
    assert "Jaws" in a["goal"]["scenario"]
    assert "Casablanca" in b["goal"]["scenario"]
    assert "Popcorn1!" not in a["goal"]["scenario"]


# ── rendering ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("renderer", [bench.render_table, bench.render_html])
def test_renderers_tell_a_blocked_shape_apart_from_a_failure(renderer):
    """⛔ not ❌ — rendering a backlog item identically to a regression is how
    a reader is trained to ignore red."""
    out = renderer(bench.score(_results([
        _case(), _blocked("paragraph", why="clause splitting is line-based")])))
    assert "⛔" in out
    assert "line-based" in out


@pytest.mark.parametrize("renderer", [bench.render_table, bench.render_html])
def test_renderers_tell_an_intended_failure_apart_from_a_break(renderer):
    """A `failed` row means opposite things depending on what was asked for.
    One word for both trains a reader to skim past both."""
    def _row(out, sid):
        # The ROW, not the whole page — the tally line legitimately says
        # "N failed as intended" and would satisfy a substring check over the
        # whole output whatever the row said.
        return next(ln for ln in out.replace("<tr>", "\n<tr>").splitlines()
                    if sid in ln and "tally" not in ln)

    intended = renderer(bench.score(_results([_case(), _failing()])))
    assert "failed as intended" in _row(intended, "expected_fail")
    broken = renderer(bench.score(_results([_case(outcome="failed", failed=1,
                                                  passed=0, verified=False)])))
    assert "failed as intended" not in _row(broken, "steps")


def test_the_table_names_the_build_and_the_interpretation_path():
    """Requirement F. A verdict pasted into a ticket travels without its
    build-stamped folder, and a shape blocked with no model configured is a
    different finding from one blocked with a model."""
    out = bench.render_table(bench.score(_results([_case()])))
    assert "1.0.0a46" in out
    assert "deterministic only" in out
    assert "BusterBlock" in out


def test_llm_mode_names_the_missing_extra_separately(monkeypatch):
    """Three states, not two: a machine with no model AND no `llm` extra
    cannot even attempt the model path, and its rejections say so in import
    errors rather than in the documented 'set NOODLE_MODEL' advice."""
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "litellm", None)
    assert "NOODLE_MODEL unset" in bench._llm_mode()
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    assert bench._llm_mode() == "NOODLE_MODEL=anthropic/claude-sonnet-5"


# ── the grammar gaps the benchmark found ────────────────────────────────────

LOGIN = ('On http://127.0.0.1:3333/ log in as reel_ryan with password '
         'Popcorn1! then search movies for The Shining and verify The '
         'Shining is shown.')


def _expand(text):
    from noodle.repl import prompt_expander
    return prompt_expander.expand(text)


def test_log_in_is_a_verb_and_lowers_to_the_three_surface_steps():
    """The gap behind all three blocked shapes. Measured before the fix: this
    one sentence refused on exactly the `log in` clause while `search movies
    for The Shining` and `verify The Shining is shown` in the SAME sentence
    both parsed — so neither prose nor one-liners were the blocker."""
    r = _expand(LOGIN)
    assert r["ok"], r.get("error")
    acts = [(a["do"], a.get("target"), a.get("value"))
            for a in r["goal"]["actions"]]
    # ...and the credentials it carried go to secrets like any other, so the
    # verb cannot become a new way to inline a password into a .feature.
    assert acts[:3] == [("enter", "username", "{env:USERNAME}"),
                        ("enter", "password", "{env:PASSWORD}"),
                        ("click", "login", None)]
    assert r["secrets"] == {"USERNAME": "reel_ryan", "PASSWORD": "Popcorn1!"}
    # the three control names were INFERRED, so they are declared
    assert any("inferred from the verb" in a for a in r["assumptions"])


def test_a_login_clause_hands_back_the_url_it_carried():
    """"On <url> log in as …" holds the flow's only URL. Swallowing it made
    the whole prompt refuse for want of a URL it contained — the most
    confusing refusal there is."""
    assert _expand(LOGIN)["goal"]["navigation"] == ["http://127.0.0.1:3333"]


def test_a_login_missing_a_credential_is_coached_never_guessed():
    """Guessing the other half would author a test proving the app accepts a
    blank password."""
    r = _expand("go to http://x.test/\nlog in\nverify Home")
    assert not r["ok"]
    bad = [u for u in r["unresolved"] if u["text"] == "log in"]
    assert bad and "needs both credentials" in bad[0]["reason"]
    from noodle.repl import prompt_expander
    assert prompt_expander._suggest("log in") == (
        'rewrite as: log in as "<user>" with password "<password>"')


@pytest.mark.parametrize("text,kind", [
    ('click "login"', "click"),
    ('enter "bob" in the "login" field', "enter"),
    ("verify you are logged in", "verify"),
])
def test_the_login_verb_never_steals_an_explicit_verb(text, kind):
    """It is LAST in the table for this reason: it is the loosest pattern
    there, and every explicit verb must win first."""
    from noodle.repl import prompt_expander
    node = prompt_expander._parse_clause(
        {"id": "clause-1", "text": text, "line": 1, "evidence": False})
    assert node["kind"] == kind


def test_prose_that_merely_mentions_a_login_never_becomes_login_actions():
    """Scene-setting carries no credentials and no imperative. Inventing a
    three-step login out of it would be worse than refusing it — so neither
    phrasing may reach the goal as actions."""
    from noodle.repl import prompt_expander as pe
    assert pe._parse_clause({"id": "clause-1", "line": 1, "evidence": False,
                             "text": "the login page appears"})["kind"] != "login"
    r = _expand("go to http://x.test/\nlogin page appears\nverify Home")
    assert not [a for a in ((r.get("goal") or {}).get("actions") or [])
                if a.get("target") in pe._LOGIN_CONTROLS]


def test_the_affordance_contract_exemption_is_narrow():
    """The rule (a translation may not invent a control label) still stands.
    The login lowering is exempt ONLY for its three canonical names and ONLY
    when the prompt actually asked to log in."""
    from noodle.repl import prompt_expander as pe
    assert pe.review_contract(_expand(LOGIN))["ok"]
    # same goal shape, no login anywhere in the text -> still refused
    forged = _expand('go to http://x.test/\nsearch for "cat"\nverify "cat"')
    forged["goal"]["actions"].insert(0, {"do": "click", "target": "login"})
    problems = pe.review_contract(forged)["problems"]
    assert any("has no source clause" in p for p in problems)
    # ...and a login prompt still cannot smuggle in a FOURTH control
    smuggled = _expand(LOGIN)
    smuggled["goal"]["actions"].append({"do": "click", "target": "wire funds"})
    assert any("has no source clause" in p
               for p in pe.review_contract(smuggled)["problems"])


def test_search_keeps_the_whole_tail_as_the_term():
    """A `search <box> for <term>` verb was tried and REVERTED. It reached the
    right box for "search movies for The Shining" but turned "search gifts for
    mum" into a search for "mum" — silently changing WHICH TERM is searched on
    a phrasing that was correct before. Wrong-and-visible (no search box found)
    beats wrong-and-hidden."""
    r = _expand('go to http://x.test/\nsearch gifts for mum\nverify Gifts')
    term = next(a for a in r["goal"]["actions"] if a["do"] == "search")["term"]
    assert term == "gifts for mum"


def test_a_plain_search_for_is_unchanged():
    """The bounded leading noun must not swallow "search for the cheapest
    flight for two people"."""
    r = _expand('go to http://x.test/\nsearch for the cheapest flight for '
                'two people\nverify Results')
    search = next(a for a in r["goal"]["actions"] if a["do"] == "search")
    assert search["term"] == "cheapest flight for two people"


# ── the credential leak ─────────────────────────────────────────────────────

LEAKY = ('1. Go to the URL http://x.test/\n'
         '2. Enter "reel_ryan" in the "username" field\n'
         '3. Enter "Popcorn1!" in the "password" field\n'
         '4. Click "login"\n'
         '5. Verify: Welcome')


def test_a_credential_typed_into_a_prompt_never_reaches_the_goal():
    """Found by reading the benchmark's own output: a spec authored verbatim
    from `Enter "Popcorn1!" in the "password" field` shipped that password
    into a COMMITTED .feature. The engine had the mechanism the whole time —
    the goal path uses it — but the prompt path, the one a pasted spec goes
    through, could not reach it."""
    r = _expand(LEAKY)
    assert r["ok"], r.get("error")
    values = [a.get("value") for a in r["goal"]["actions"] if a["do"] == "enter"]
    assert values == ["{env:USERNAME}", "{env:PASSWORD}"]
    assert r["secrets"] == {"USERNAME": "reel_ryan", "PASSWORD": "Popcorn1!"}
    # The GOAL is what becomes the .feature, the POM and the probe's
    # do-strings, so it is the surface that must be clean. `clauses` echoes the
    # caller's own prompt back to the caller who typed it, which discloses
    # nothing they do not already hold — and a full filesystem walk of an
    # authored workspace finds the literals in the gitignored secrets file and
    # nowhere else (verified live; the write needs a browser, so it is the
    # benchmark that covers it end to end).
    import json
    blob = json.dumps({k: v for k, v in r.items()
                       if k not in ("secrets", "clauses")}, default=str)
    assert "Popcorn1!" not in blob and "reel_ryan" not in blob


def test_the_lift_is_declared_not_silent():
    """A value that moved has to say where it went, or the next reader edits
    the .feature looking for a password that is not there."""
    assert any("gitignored secrets file" in a and "_PASSWORD" in a
               for a in _expand(LEAKY)["assumptions"])


@pytest.mark.parametrize("field,key", [
    ("password", "PASSWORD"), ("the password", "PASSWORD"),
    ("passphrase", "PASSWORD"), ("username", "USERNAME"),
    ("user name", "USERNAME"), ("login", "USERNAME"), ("account", "USERNAME"),
    ("email address", "EMAIL"), ("api key", "API_TOKEN"), ("otp", "OTP"),
])
def test_credential_fields_map_to_one_canonical_key(field, key):
    """Two phrasings of one field must not mint two keys for one credential."""
    from noodle.repl import prompt_expander
    assert prompt_expander.secret_key_for(field) == key


def test_an_email_on_a_checkout_form_is_customer_data_not_a_credential():
    """The over-reach this rule caused before it had two tiers: an `Email`
    field beside Full name and Postcode is a customer's address, and lifting
    it pushed that into a secrets file and left the test asking for a value
    nobody set. An identity field is only a credential when a SECRET field
    appears in the same flow — which is what tells a login form from a
    checkout form."""
    r = _expand('1. go to https://shop.example.com\n'
                '2. fill the customer information: Full name - A Person, '
                'Email: a@b.co, Postcode: SW1A 1AA\n'
                '3. verify Widget A')
    assert not r["secrets"]
    assert [a["value"] for a in r["goal"]["actions"] if a["do"] == "enter"] \
        == ["A Person", "a@b.co", "SW1A 1AA"]
    # ...and the same email IS lifted when a password sits beside it
    login = _expand('1. go to https://shop.example.com\n'
                    '2. Enter "a@b.co" in the "email" field\n'
                    '3. Enter "hunter2" in the "password" field\n'
                    '4. Verify: Welcome')
    assert login["secrets"] == {"EMAIL": "a@b.co", "PASSWORD": "hunter2"}


@pytest.mark.parametrize("field", ["search movies", "full name", "postal code",
                                   "street address", "quantity"])
def test_an_ordinary_field_value_is_left_alone(field):
    """Over-reaching would push a search term into a secrets file and leave
    the test asking for a value nobody set."""
    from noodle.repl import prompt_expander
    assert prompt_expander.secret_key_for(field) is None


def test_two_different_values_for_one_field_get_distinct_keys():
    """A negative-path spec enters a good password then a bad one. Reusing the
    key would silently overwrite the first and author a test that logs in
    twice with the same value."""
    r = _expand('1. Go to the URL http://x.test/\n'
                '2. Enter "right" in the "password" field\n'
                '3. Enter "wrong" in the "password" field\n'
                '4. Verify: Denied')
    assert r["secrets"] == {"PASSWORD": "right", "PASSWORD_2": "wrong"}


def test_an_existing_env_reference_is_not_re_lifted():
    """A caller who already wired their own vault key keeps it."""
    r = _expand('1. Go to the URL http://x.test/\n'
                '2. Enter "{env:VAULT_PW}" in the "password" field\n'
                '3. Verify: Welcome')
    assert not r["secrets"]
    assert r["goal"]["actions"][0]["value"] == "{env:VAULT_PW}"


def test_the_benchmarks_own_artifacts_mask_the_credentials_it_seeded():
    """The benchmark leaked one itself. A blocked spec is reported with the
    clauses the compiler rejected, quoted verbatim, so the password inside a
    spec rode into results.json, both verdict files and the verdict.html that
    gets SERVED. Exact-value masking, not a heuristic — the benchmark knows
    which values it seeded, so it never has to guess which token in a sentence
    was the secret nor mangle a movie title that looks like one."""
    import json

    body = json.dumps({"unresolved": [
        {"text": 'Enter "Popcorn1!" in the "password" field'}]})
    out = bench.redact(body)
    assert "Popcorn1!" not in out and "***" in out
    assert bench.redact("Jaws is still Jaws") == "Jaws is still Jaws"


def test_every_artifact_path_is_masked_not_just_the_render():
    """A source guard on a leak with four write sites: masking at the render
    would still let json.dumps of the same data reach disk unmasked."""
    import inspect

    from noodle import cli
    src = inspect.getsource(cli)
    for site in ("benchmark_results.json", "benchmark_verdict.json"):
        i = src.index(site)
        assert "bench.redact" in src[i:i + 260], f"{site} is written unmasked"


def test_the_lifted_keys_are_app_scoped_so_two_apps_cannot_collide():
    """A bare PASSWORD collides across app packages: hooks._load_package_env
    loads each package's secrets with override=True but MEMOIZES per
    directory, so two apps' features interleaved in one process leave the
    second app's PASSWORD in place for the first — a test silently logging in
    with another app's credential. Same app-scoping the base_url key already
    uses."""
    import inspect

    from noodle.repl import core
    src = inspect.getsource(core._author_test_door)
    assert 'pre = f"{_norm_app(app_name).upper()}_" if app_name else ""' in src
    # the goal's own {env:} refs are rewritten to match, or the test would
    # reference a key nothing ever sets
    assert 'a["value"] = f"{{env:{pre}{v[5:-1]}}}"' in src


def test_the_author_door_forwards_the_lift_to_the_secrets_writer():
    """A source guard: the write itself needs a workspace and a live app, and
    is covered end to end by the benchmark. What must not regress silently is
    the WIRING — an expansion that lifts a credential and an author call that
    drops it on the floor would put `{env:PASSWORD}` in a test with nothing
    ever setting it."""
    import inspect

    from noodle.repl import core
    src = inspect.getsource(core._author_test_door)
    assert "if pending_secrets:" in src
    assert 'kw["required_secret_keys"]' in src
    assert "_preseed_secrets(" in src


def test_a_gated_search_runs_the_do_chain_first():
    """A source guard, deliberately: the probe's phase ordering lives inline
    in one long orchestration function with no seam to fake a page at.

    NOOD_0168 runs the do-chain AFTER the search, because there the chain is
    the steps that FOLLOW a search. Behind a login gate the chain is how you
    reach the search box, and the search otherwise hunts the login page and
    refuses with "no search box found" on a site that plainly has one. The
    rescue must stay EVIDENCE-gated — only when the search actually failed —
    so the NOOD_0168 ordering is untouched on every flow that works today.
    """
    import inspect

    from noodle.agents.web import probe
    src = inspect.getsource(probe)
    assert 'if pg.get("search_warning") and do_actions:' in src
    assert "if do_actions and not did_do:" in src


# ── the agent-driven session ────────────────────────────────────────────────

def _session(tmp_path, ledger_lines, specs=None):
    import json
    (tmp_path / ".noodle").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".noodle" / "benchmark_session.json").write_text(json.dumps({
        "workspace": str(tmp_path), "base_url": bench.BASE_URL, "pid": None,
        "started": 0.0,
        "specs": specs or [{"id": "paragraph", "ref": "B1", "label": "a para",
                            "expect": "blocked"},
                           {"id": "expected_fail", "ref": "B5",
                            "label": "must fail", "expect": "fail"}]}),
        encoding="utf-8")
    (tmp_path / ".noodle" / "benchmark_ledger.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in ledger_lines), encoding="utf-8")
    return str(tmp_path)


def _entry(spec="paragraph", started=0.0, seconds=2.0, **kw):
    return {"spec": spec, "started": started, "ended": started + seconds,
            "seconds": seconds, "feature": f"f/spec_{spec}.feature",
            "authored": True, "ready": True, "run_s": 10, "passed": 1,
            "failed": 0, "verified": True, "engine_repairs": 0,
            "tokens": 800, "blocked_by": None} | kw


def test_a_ledger_entry_is_written_per_attempt_not_per_spec(tmp_path,
                                                            monkeypatch):
    """The ledger counts WORK, so a spec the agent had to re-send three times
    is three lines. Collapsing them to one would hide the whole measurement."""
    ws = _session(tmp_path, [])
    for i in range(3):
        bench.record(ws, feature_path="spec_paragraph.feature",
                     started=float(i), seconds=1.0,
                     result={"author": {"feature": "f.feature", "ready": True},
                             "run": {"passed": 1, "seconds": 3}})
    v = bench.score(bench.session_table(ws))
    para = next(c for c in v["cases"] if c["id"] == "paragraph")
    assert para["attempts"] == 3
    assert para["corrections"] == 2          # two re-sends = two corrections


def test_recording_is_a_no_op_without_an_open_session(tmp_path):
    """It runs on EVERY author_test in the product. It must cost nothing and
    raise nothing when no benchmark is in flight."""
    bench.record(str(tmp_path), feature_path="x.feature", started=0.0,
                 seconds=1.0, result={"author": {}, "run": {}})
    assert not (tmp_path / bench.LEDGER_FILE).exists()
    bench.record(None, feature_path=None, started=0.0, seconds=0.0, result={})


def test_an_attempt_for_an_unknown_spec_is_ignored(tmp_path):
    """A session authoring its own unrelated test must not land in the table.
    The mapping is the feature FILENAME, never call order — which a session
    that retries one spec or works out of order would scramble."""
    ws = _session(tmp_path, [])
    bench.record(ws, feature_path="something_else.feature", started=0.0,
                 seconds=1.0, result={"author": {"feature": "x"}, "run": {}})
    assert not [c for c in bench.session_table(ws)["cases"]
                if c.get("attempts")]


def test_development_time_includes_the_agents_own_turnaround(tmp_path):
    """What the user waits for. Both ends are ENGINE timestamps, so the agent
    cannot flatter it by forgetting how long it thought for."""
    ws = _session(tmp_path, [
        _entry(started=0.0, seconds=2.0, run_s=None, passed=0),
        _entry(started=100.0, seconds=3.0, run_s=10)])
    para = next(c for c in bench.session_table(ws)["cases"]
                if c["id"] == "paragraph")
    assert para["development_s"] == 93.0     # 103 end - 0 start - 10s run
    assert para["engine_s"] == -5.0 or para["engine_s"] == pytest.approx(-5.0)


def test_a_session_holds_every_shape_to_pass(tmp_path):
    """`expect: blocked` describes the HEADLESS run. In a session the agent is
    the interpreter, so a shape it could not get through is the agent failing
    — not a tracked engine gap to celebrate closing."""
    ws = _session(tmp_path, [_entry(), _entry(spec="expected_fail", passed=0,
                                              failed=1, verified=False)])
    v = bench.score(bench.session_table(ws))
    assert not v["gaps_closed"], "a session must not report closed gaps"
    # every spec judged on its own row; the only regression left is the report,
    # which a bare tmp workspace has no artifacts to build
    assert all(not c["failures"] for c in v["cases"])
    assert v["regressions"] == [
        "report: the one Allure report over all five specs was not built "
        "and hosted"]


def test_the_table_separates_the_agents_time_from_the_engines(tmp_path):
    """The question this column exists to answer. In a session `DEV` is wall
    clock and is mostly the AGENT thinking; without `ENGINE` beside it, a spec
    whose agent paused for two minutes reads identically to one the engine was
    slow at — and the natural next guess is that the later specs were 'fast
    because the first one warmed something up'."""
    ws = _session(tmp_path, [
        _entry(started=0.0, seconds=2.0, run_s=None, passed=0),   # blocked lap
        _entry(started=120.0, seconds=5.0, run_s=1)])             # agent thought
    v = bench.score(bench.session_table(ws))
    para = next(c for c in v["cases"] if c["id"] == "paragraph")
    assert para["development_s"] == 124.0        # wall clock, agent included
    assert para["engine_s"] == 6.0               # 2 + 5 author, minus the 1s run
    out = bench.render_table(v)
    assert "ENGINE" in out
    # headless has no agent, so the two would be the same number — no column
    assert "ENGINE" not in bench.render_table(bench.score(_results([_case()])))


def test_a_spec_the_session_never_sent_is_named(tmp_path):
    ws = _session(tmp_path, [_entry()])
    v = bench.score(bench.session_table(ws))
    missing = next(c for c in v["cases"] if c["id"] == "expected_fail")
    assert missing["outcome"] == "not attempted"
    assert v["verdict"] == "REGRESSED"


def test_session_table_without_a_session_is_a_setup_fault(tmp_path):
    with pytest.raises(bench.BenchmarkError):
        bench.session_table(str(tmp_path))


def test_the_apps_credentials_are_seeded_into_the_workspace(tmp_path):
    """Setup, not an authoring argument. `secret_values` on the author call is
    too late: the goal's {env:} values must RESOLVE for the probe to walk past
    the login gate, and the probe runs inside the same transaction that would
    have written them — which then rolls back on the block it caused."""
    p = bench._seed_secrets(tmp_path)
    assert p.name == "secrets.env"
    body = p.read_text(encoding="utf-8")
    assert "BUSTERBLOCK_USER=reel_ryan" in body
    bench._seed_secrets(tmp_path)                 # idempotent
    assert body == p.read_text(encoding="utf-8")


def test_the_authoring_probe_is_given_its_workspace():
    """A source guard on a one-word defect with no seam to test at: the
    authoring probe never passed `workspace=`, so `{env:}` in its do-actions
    resolved against the CURRENT DIRECTORY and a credentialed login goal
    refused with "set in the workspace env files first" — naming files it had
    never looked at. Unreachable from a CLI standing in the workspace, and
    unavoidable for every agent, which always passes `workspace=`."""
    import inspect

    from noodle.repl import core
    src = inspect.getsource(core._author_test_impl)
    assert "workspace=workspace, **p_args" in src


# ── the three sharp edges from the code review ──────────────────────────────

def test_an_unverified_pid_is_never_signalled(tmp_path, monkeypatch):
    """A session spans many turns, so between --session and --table the app can
    die and the OS can hand its pid to something else. Unverifiable means
    DON'T: leaking a dev server somebody can stop by hand beats killing their
    editor."""
    killed = []
    monkeypatch.setattr(bench.os, "kill", lambda p, s: killed.append(p))

    def _arm():
        p = tmp_path / ".noodle" / "benchmark_session.json"
        _session(tmp_path, [])
        p.write_text(p.read_text(encoding="utf-8").replace('"pid": null', '"pid": 4242'),
                     encoding="utf-8")

    monkeypatch.setattr(bench, "owns_pid", lambda pid: False)
    _arm()
    note = bench.session_stop(str(tmp_path))
    assert not killed
    assert note and "left running" in note
    # ...and it IS signalled once ownership is confirmed
    monkeypatch.setattr(bench, "owns_pid", lambda pid: True)
    _arm()
    assert bench.session_stop(str(tmp_path)) is None
    assert killed == [4242]


def test_owns_pid_says_no_when_it_cannot_check(monkeypatch):
    """No `ps` (Windows) → False, not a guess."""
    def _boom(*a, **k):
        raise OSError("no ps here")
    monkeypatch.setattr(bench.subprocess, "run", _boom)
    assert bench.owns_pid(1234) is False
    assert bench.owns_pid("not-a-pid") is False


@pytest.mark.parametrize("text,user,password", [
    ('log in as "Pat Tester" with password "correct horse battery staple"',
     "Pat Tester", "correct horse battery staple"),
    ("sign in as 'a b' with password 'c d e'", "a b", "c d e"),
    ("log in as reel_ryan with password Popcorn1!", "reel_ryan", "Popcorn1!"),
])
def test_a_quoted_credential_is_taken_whole(text, user, password):
    """The unquoted form stops at the first space, which authored a login with
    the first WORD of a passphrase — a test that fails for a reason nobody
    could see in the Gherkin."""
    from noodle.repl import prompt_expander
    n = prompt_expander._parse_clause(
        {"id": "clause-1", "text": text, "line": 1, "evidence": False})
    assert (n["user"], n["password"]) == (user, password)


def test_an_orphaned_pom_page_key_is_reported(tmp_path):
    """A page key is bound to a feature by its @page: tag, and the tag comes
    from the scenario title — so re-authoring under a new title leaves a
    HAND-WRITTEN entry unreachable. The generated POM rewrites itself; a
    shared one does not, and it failed SILENTLY: locators still there, still
    valid, never consulted."""
    from noodle.repl import core
    app = tmp_path / "web" / "shop"
    (app / "features").mkdir(parents=True)
    (app / "resources" / "pageobjects").mkdir(parents=True)
    (app / "features" / "a.feature").write_text(
        "@web @page:current_title\nFeature: x\n", encoding="utf-8")
    (app / "resources" / "pageobjects" / "shared_pom.yaml").write_text(
        "pages:\n  current_title:\n    a:\n      css: '#a'\n"
        "  stale_title:\n    b:\n      css: '#b'\n", encoding="utf-8")
    w = core._orphan_pom_warnings(app)
    assert len(w) == 1
    assert "stale_title" in w[0] and "current_title" not in w[0]
    # every key pinned -> silence, so it can never become background noise
    (app / "features" / "b.feature").write_text(
        "@web @page:stale_title\nFeature: y\n", encoding="utf-8")
    assert core._orphan_pom_warnings(app) == []


def test_orphan_detection_survives_a_package_it_cannot_read(tmp_path):
    """Advisory means advisory: it must never be the thing that fails an
    author call."""
    from noodle.repl import core
    assert core._orphan_pom_warnings(tmp_path / "nope") == []


# ── defects a code review found in the above ────────────────────────────────

def test_a_refused_attempt_is_still_an_attempt(tmp_path, monkeypatch):
    """The ledger's headline defect. `author_test` returns EARLY from four
    places — a refused expansion (the commonest), an unusable mode combination,
    a missing app/base_url, a blocked contract — so recording at the bottom of
    the implementation logged only the attempts that got far enough. A spec the
    engine refused twice and then took reported `attempts: 1, corrections: 0`
    and started its clock at the successful lap. The refusals ARE the
    measurement: they are what the caller paid a round trip for."""
    from noodle.repl import core
    _session(tmp_path, [], specs=[{"id": "x", "ref": "B1",
                                  "label": "l", "expect": "pass"}])
    r = core.author_test(prompt="Please have a look around and see how it is.",
                         feature_path="spec_x.feature", workspace=str(tmp_path))
    assert r["ok"] is False, "fixture must exercise the early-return path"
    entries = (tmp_path / bench.LEDGER_FILE).read_text(
        encoding="utf-8").strip().splitlines()
    assert len(entries) == 1, "the refused attempt was not recorded"
    import json
    e = json.loads(entries[0])
    assert e["authored"] is False and e["blocked_by"]


def test_a_flat_pom_has_no_page_keys_to_orphan(tmp_path):
    """The orphan warning read a FLAT pom.yaml — the documented hand-written
    shape, `username:` with `  css: "#username"` under it — as page keys called
    "css", and reported them on every author call in that package. Page keys
    live under a `pages:` block and nowhere else."""
    from noodle.repl import core
    app = tmp_path / "web" / "shop"
    (app / "features").mkdir(parents=True)
    (app / "resources" / "pageobjects").mkdir(parents=True)
    (app / "features" / "a.feature").write_text("@web @page:t\nFeature: x\n",
                                                encoding="utf-8")
    (app / "resources" / "pageobjects" / "flat_pom.yaml").write_text(
        'username:\n  css: "#username"\npassword:\n  css: "#password"\n',
        encoding="utf-8")
    assert core._orphan_pom_warnings(app) == []


def test_the_real_repo_poms_produce_no_orphan_noise():
    """The regression that proved it: the repo's own sample POMs are flat, so
    the warning fired on every author call against them."""
    from noodle.repl import core
    root = Path(core.__file__).resolve().parents[2] / "sample_feature_tests"
    if not root.is_dir():
        pytest.skip("sample_feature_tests not present in this checkout")
    noisy = {str(app.relative_to(root)): w
             for app in root.rglob("*/resources/pageobjects")
             if (w := core._orphan_pom_warnings(app.parent.parent))}
    assert not noisy, f"orphan warning fires on shipped POMs: {noisy}"


def test_a_second_session_is_refused_rather_than_orphaning_the_first(tmp_path,
                                                                    monkeypatch):
    """Opening a second session scaffolded a new workspace, saw the port
    already answered so recorded pid None, and overwrote the marker — leaving
    the first session's real pid unreachable and BusterBlock alive through
    every later --table."""
    from typer.testing import CliRunner

    from noodle import cli
    marker = tmp_path / ".open_session"
    marker.write_text("/somewhere/already/open", encoding="utf-8")
    monkeypatch.setattr(cli, "_session_marker", lambda: marker)
    res = CliRunner().invoke(cli.app, ["benchmark", "--session"])
    assert res.exit_code == 2
    assert "already open" in (res.output + str(res.stderr or ""))


# ── defects a TESTER found by driving the framework ─────────────────────────

def test_both_doors_lift_credentials_not_just_the_prompt_one():
    """The prompt door lifted and the goal door did not, so the identical
    value in the identical field landed in a COMMITTED .feature depending only
    on which door the caller used — and the goal door warned about nothing."""
    from noodle.repl import prompt_expander as pe
    actions = [{"do": "enter", "target": "username", "value": "bob"},
               {"do": "enter", "target": "password", "value": "hunter2"},
               {"do": "enter", "target": "search", "value": "widgets"}]
    secrets, notes = pe.lift_credentials(actions)
    assert secrets == {"USERNAME": "bob", "PASSWORD": "hunter2"}
    assert [a["value"] for a in actions] == [
        "{env:USERNAME}", "{env:PASSWORD}", "widgets"]
    assert len(notes) == 2
    # a caller who already wired their own reference keeps it
    wired = [{"do": "enter", "target": "password", "value": "{env:MY_PW}"}]
    assert pe.lift_credentials(wired) == ({}, [])


def test_lifted_credentials_land_before_the_probe_needs_them(tmp_path):
    """Lifting alone turned a working `probe: {perform: true}` goal into a
    blocked one: the probe needs those values RESOLVED to walk past a login
    gate, and it runs inside the transaction that would have written them."""
    from noodle.repl import core
    core._preseed_secrets(str(tmp_path), {"APP_X_PASSWORD": "s3cret"})
    body = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert "APP_X_PASSWORD=s3cret" in body
    # never clobbers a value already there — a hand-edited workspace outranks
    # a literal somebody left in a prompt
    core._preseed_secrets(str(tmp_path), {"APP_X_PASSWORD": "different"})
    assert (tmp_path / "secrets.env").read_text(
        encoding="utf-8").count("APP_X_PASSWORD") == 1
    assert "s3cret" in (tmp_path / "secrets.env").read_text(encoding="utf-8")


def test_two_tests_behind_one_login_get_distinct_filenames():
    """The derived name was a blind 40-char cut of the scenario, so every test
    behind the same login preamble became `enter_username_..._click_logi` —
    the second refused, the engine advised `overwrite: true`, and following
    that ADVICE silently replaced a passing test with an unrelated one."""
    base = ('1. Go to the URL http://x.test/\n'
            '2. Enter "bob" in the "username" field\n'
            '3. Enter "pw" in the "password" field\n'
            '4. Click "login"\n5. {step}\n6. Verify: {want}')
    a = _expand(base.format(step='Select "Horror" from "filter by genre"',
                            want="Halloween"))["feature_path"]
    b = _expand(base.format(step='Click "view cart"',
                            want="Your cart is empty"))["feature_path"]
    assert a != b, "two different tests still derive one filename"
    assert "halloween" in a and "cart" in b


def test_the_slug_keeps_the_assertion_when_it_must_choose():
    """The checks are what tell two tests apart, so the ACTION head is what
    gets squeezed — never the assertion."""
    from noodle.repl import prompt_expander as pe
    slug = pe.feature_slug(["click a very long control name indeed here"],
                           ["verify Halloween"], "ignored", cap=40)
    assert slug.endswith("verify_halloween")
    assert len(slug) <= 40
    # no checks at all -> the plain cut, unchanged
    assert pe.feature_slug(["click x"], [], "click x") == "click_x"


def test_a_scaffolded_workspace_never_records_a_typer_sentinel_as_the_model(
        tmp_path):
    """The session benchmark found this by running: every workspace scaffolded
    since NOOD_0190 carried

        NOODLE_MODEL=<typer.models.OptionInfo object at 0x…>

    in its .env, because `_scaffold_benchmark_ws` calls init() as a plain
    function and an unpassed typer.Option arrives as its OptionInfo — which is
    TRUTHY, so `if not llm` took the --llm branch and f-stringified the
    sentinel into the file. Downstream that is worse than a missing model:
    _model_configured reads it as configured, so the engine SPENDS its one
    interpretation call, litellm rejects a model name that is a repr, and the
    refusal blames the model instead of naming the grammar gap. The advice
    every rejection prints — "set NOODLE_MODEL" — was unfollowable, because a
    value was already there."""
    from noodle import cli
    cli.init(path=str(tmp_path))          # exactly how the benchmark calls it
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OptionInfo" not in env, env
    # and the no-model default is preserved: commented out, patterns-only, $0
    assert "#NOODLE_MODEL=" in env
    from noodle.repl import core
    assert core._model_configured(str(tmp_path)) is False


def test_benchmark_is_a_registered_cli_command():
    from typer.testing import CliRunner

    from noodle import cli
    # NOOD_0163/0228, re-learned here: measure CONTENT, not paint. Rich treats
    # GitHub Actions as a terminal and a laptop shell as not one, so in CI it
    # paints `--specs` as `--` + `specs` with a style break between them and a
    # substring check finds neither. Strip with the engine's OWN stripper —
    # the one `instruction_budget._cli_help` measures help with — so this test
    # and the byte ceilings never disagree about what a byte is.
    out = _ANSI_RE.sub(
        "", CliRunner().invoke(cli.app, ["benchmark", "--help"]).output)
    assert "--specs" in out
    assert "BusterBlock" in out


def test_a_setup_fault_exits_2_rather_than_reporting_a_regression(monkeypatch):
    """A missing `npm ci` is not an engine regression. Reporting one would
    blame the engine for the operator's missing step, and the next reader
    would start bisecting."""
    from typer.testing import CliRunner

    from noodle import cli
    monkeypatch.setattr(bench, "load_specs", lambda *a, **k: (_ for _ in ()).throw(
        bench.BenchmarkError("BusterBlock's dependencies are not installed")))
    res = CliRunner().invoke(cli.app, ["benchmark"])
    assert res.exit_code == 2

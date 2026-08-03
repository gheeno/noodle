"""NOOD_0225 — the engine decides when a step gets an evidence screenshot,
by reading the brief.

The driving session: an AC whose step 2 read "Verify pizza banner logo is
present - take evidence screenshot on this step." The model authoring it did
not know what to do with that phrase, invented `[evidence: screenshot]`,
argued with itself about whether that was "step decorator syntax", and
shipped a step that resolved green with no picture attached. Nothing failed —
which is the worst outcome, because the brief asked for proof and the report
had none.

The rules this file pins, both directions:
  1. the last step of a passing scenario always carries evidence;
  2. a step that ASKS for a screenshot gets one, in whatever words;
  3. a step or brief that DECLINES one gets none, in whatever words.
"""
from noodle.repl import goal as G
from noodle.repl.prompt_expander import (
    _EVIDENCE,
    _EVIDENCE_NEG,
    evidence_intent,
    expand,
    review_contract,
)
from noodle.reporting import evidence
from noodle.resolver.patterns import (
    EVIDENCE_MARKER_RE,
    EVIDENCE_SUPPRESS_RE,
    _pre_clean,
)


def _tag(extra: dict) -> str:
    """The compiled feature's tag line for a one-assertion goal."""
    g = {"scenario": "banner check", "actions": [], "checks": [{"see": "x"}],
         **extra}
    ev = G.evidence(g, {"pages": [{"url": "https://e.com", "headings": ["x"],
                                   "texts": ["x"]}]})
    return G.compile_goal(g, ev, "APP_URL")[0].splitlines()[0]


# --- rule 3: the refusal the engine used to ignore --------------------------


def test_do_not_add_screenshots_is_a_refusal():
    """THE bug the ticket opened with on the negative side: the off-pattern
    accepted only take/capture/include, so the two most natural spellings of
    a refusal read as no directive at all and the default still shot the
    last step."""
    for t in ("DO NOT ADD SCREENSHOTS", "DO NOT ADD evidence",
              "do not attach screenshots", "don't add any screenshots",
              "don't attach any evidence", "never add screenshots",
              "no need for screenshots", "evidence is not required",
              "skip screenshots", "omit evidence"):
        assert evidence_intent(t) == "off", t


def test_the_refusal_survives_into_the_goal():
    exp = expand("1. Go to https://shop.example.com\n"
                 "2. Verify the banner is present\n"
                 "Note: DO NOT ADD SCREENSHOTS\n")
    assert exp["goal"]["evidence"] == "off"
    assert not any(c.get("evidence") == "screenshot"
                   for c in exp["goal"]["checks"])


def test_off_compiles_to_the_silencing_tag():
    assert "@no_evidence" in _tag({"evidence": "off"})


# --- rule 2: the ask the engine used to ignore ------------------------------


def test_the_reported_step_still_asks_for_its_shot():
    """"2. Verify pizza banner logo is present - take evidence screenshot on
    this step." — the exact line from the brief."""
    exp = expand("1. Go to https://pizza.example.com\n"
                 "2. Verify pizza banner logo is present - take evidence "
                 "screenshot on this step.\n"
                 "3. Then verify order is placed successfully\n")
    banner = exp["goal"]["checks"][0]
    assert banner["evidence"] == "screenshot"
    # the marker's words are lifted OUT of the assertion, not asserted
    assert "screenshot" not in banner["see"]


def test_attach_evidence_asks_for_the_same_thing():
    """The brief's own second phrasing. "attach" was in no verb list, so
    "attach evidence" — one line below a phrase that worked — produced
    nothing."""
    for t in ("- attach evidence", "- attach a screenshot", "- with evidence",
              "- capture evidence", "- include a screenshot",
              "- grab a screenshot", "- save evidence"):
        assert _EVIDENCE.search(t), t


def test_bare_evidence_only_counts_at_the_end_of_a_clause():
    """The span is STRIPPED, not merely flagged, so an unanchored match on
    the ordinary word would delete words the tester meant."""
    assert not _EVIDENCE.search("verify the page for evidence of the order")
    assert _EVIDENCE.search("verify the page - attach evidence")


def test_per_each_step_means_every_step_not_every_assertion():
    """The runtime has had @evidence (every passed step) since NOOD_0153;
    `steps?` sat in the per-assertion pattern, so the brief asking for the
    MOST evidence compiled to the narrower mode and action steps shipped
    none."""
    for t in ("Attach screenshot per each step",
              "attach evidence per each step",
              "take a screenshot on every step",
              "screenshot each single step"):
        assert evidence_intent(t) == "all", t
    assert evidence_intent(
        "Note : each assertion must contain an evidence screenshot") \
        == "assertions"


def test_all_compiles_to_the_every_step_tag():
    tag = _tag({"evidence": "all"})
    assert "@evidence" in tag and "@evidence:assertions" not in tag
    assert "@evidence:assertions" in _tag({"evidence": "assertions"})
    assert "evidence" not in _tag({})            # the default stays untagged


# --- the per-step refusal, and the step it used to eat ----------------------


def test_a_step_that_declines_evidence_keeps_its_assertion():
    """THE regression on this side: any line an evidence pattern touched was
    classified as a run directive and DROPPED. "Verify the banner is present
    - no screenshot needed" lost its assertion outright AND switched the
    whole run's evidence off — the tester asked for one step without a
    picture and got neither the picture nor the check."""
    exp = expand("1. Go to https://shop.example.com\n"
                 "2. Verify the banner is present - no screenshot needed\n"
                 "3. Click Add to cart\n"
                 "4. Verify the cart shows 1 item\n")
    checks = exp["goal"]["checks"]
    assert exp["goal"].get("evidence") is None      # not a RUN-wide directive
    assert checks[0]["see"] == "banner"             # the assertion survived
    assert checks[0]["evidence"] == "none"          # and it declined its shot
    assert "needed" not in checks[0]["see"]         # no residue in the text


def test_the_negative_is_read_before_the_positive():
    """"no screenshot needed" contains the word the positive marker looks
    for; order is the whole guard."""
    assert _EVIDENCE.search("no screenshot needed")       # overlaps by design
    assert _EVIDENCE_NEG.search("no screenshot needed")   # and wins


def test_none_compiles_the_opt_out_as_tag_metadata_not_step_prose():
    """NOOD_0227 (D1) — checks[].evidence is metadata end to end now: the
    step body stays clean prose and the directive rides the scenario tag
    (@evidence:steps= / @evidence:skip=), which
    reporting/evidence.step_directives reads back at run time. The engine
    never emits the prose marker again — EVIDENCE_MARKER_RE stays read-only,
    for hand-authored features."""
    from noodle.repl.goal import _check_step
    assert not _check_step({"see": "x", "evidence": "none"})[0].endswith(")")
    assert not _check_step(
        {"see": "x", "evidence": "screenshot"})[0].endswith(")")
    assert not _check_step({"see": "x"})[0].endswith(")")


# --- the syntax the model invented ------------------------------------------


def test_the_bracket_spelling_a_model_reaches_for_is_understood():
    """`[evidence: screenshot]` was not recognised and nothing said so: the
    step resolved green with no picture. Every shape now means the same."""
    for tail in ("( take a screenshot )", "[take a screenshot]",
                 "[evidence: screenshot]", "- take a screenshot",
                 "(screenshot)", "- attach evidence", "[screenshot]"):
        step = f'the user should see "Cart" {tail}'
        assert EVIDENCE_MARKER_RE.search(step), tail
        assert _pre_clean(step) == 'the user should see "Cart"', tail


def test_the_opt_out_has_the_same_spellings():
    for tail in ("( no screenshot )", "[no evidence]",
                 "- do not attach a screenshot", "( screenshot not required )"):
        step = f'the user should see "Cart" {tail}'
        assert EVIDENCE_SUPPRESS_RE.search(step), tail
        # the refusal is matched FIRST everywhere (runner.execute_step,
        # patterns._pre_clean), so a tail carrying both words never reads as
        # a request — and either way the inner step resolves clean.
        assert _pre_clean(step) == 'the user should see "Cart"', tail


def test_step_text_is_not_mistaken_for_a_marker():
    """The markers are stripped, so a false positive deletes real step text.
    A quoted value carrying the word must survive untouched."""
    for step in ('the user should see "no evidence found"',
                 'the user clicks "Upload evidence"',
                 'the user should see "Total - screenshot"',
                 'the user enters "screenshot" in the search box'):
        assert not EVIDENCE_MARKER_RE.search(step), step
        assert not EVIDENCE_SUPPRESS_RE.search(step), step
        assert _pre_clean(step) == step


# --- rule 1: the last step always carries evidence --------------------------


def _wanted(**kw):
    base = dict(tags=(), requested=False, is_last_step=False, has_page=True,
                is_assertion=False, suppressed=False)
    return evidence.wanted(**{**base, **kw})


def test_the_last_step_is_covered_under_the_assertions_mode_too(monkeypatch):
    """"a shot on every assertion" asks for MORE evidence than the default,
    never less — a scenario ending on an action must not end with none."""
    monkeypatch.setenv("NOODLE_EVIDENCE", "assertions")
    assert _wanted(is_last_step=True, is_assertion=False)
    assert _wanted(tags=("evidence:assertions",), is_last_step=True,
                   is_assertion=False)


def test_a_per_step_opt_out_beats_the_last_step_rule():
    """rule 3 outranks rule 1: an explicit "not this one" is a decision."""
    assert not _wanted(is_last_step=True, suppressed=True)
    assert not _wanted(requested=True, suppressed=True)
    assert not _wanted(tags=("evidence",), suppressed=True)


def test_the_opt_out_never_manufactures_a_shot():
    """It only ever subtracts — a suppressed step in a scenario that wanted
    nothing anyway is still nothing."""
    assert not _wanted(suppressed=True)
    assert not _wanted(suppressed=False)      # not last, no marker, no tag


# --- the goal schema speaks the caller's words ------------------------------


def test_the_goal_level_mode_is_canonicalized():
    for raw, canon in (("every step", "all"), ("all steps", "all"),
                       ("none", "off"), ("no screenshots", "off"),
                       (False, "off"), (True, "all"),
                       ("each assertion", "assertions")):
        g, notes = G.normalize({"scenario": "s", "evidence": raw})
        assert g["evidence"] == canon, raw
        assert notes if raw != canon else True


def test_a_check_level_refusal_is_canonicalized_before_the_positive():
    g, _ = G.normalize({"scenario": "s",
                        "checks": [{"see": "x", "evidence": "no screenshot"},
                                   {"see": "y", "evidence": "take a screenshot"},
                                   {"see": "z", "evidence": False}]})
    assert [c["evidence"] for c in g["checks"]] == ["none", "screenshot",
                                                    "none"]


def test_validate_accepts_both_check_level_values_and_nothing_else():
    assert not G.validate({"scenario": "s", "actions": [],
                           "checks": [{"see": "x", "evidence": "none"}]})
    errs = G.validate({"scenario": "s", "actions": [],
                       "checks": [{"see": "x", "evidence": "maybe"}]})
    assert any("evidence must be" in e for e in errs)


# --- the contract, in both directions ---------------------------------------


def test_the_contract_polices_a_declined_brief_too():
    """A brief that declines evidence is making a decision, so a goal that
    still shoots breaks the contract as much as one that drops a requested
    shot."""
    exp = expand("1. Go to https://shop.example.com\n"
                 "2. Verify the banner is present\n"
                 "Note: no screenshots\n")
    assert review_contract(exp)["ok"]
    exp["goal"]["checks"][0]["evidence"] = "screenshot"     # drift
    assert not review_contract(exp)["ok"]


def test_the_contract_still_catches_a_dropped_request():
    exp = expand("1. Go to https://shop.example.com\n"
                 "2. Verify the banner is present - take a screenshot\n")
    assert review_contract(exp)["ok"]
    exp["goal"]["checks"][0].pop("evidence")                # drift
    assert not review_contract(exp)["ok"]

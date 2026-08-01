"""NOOD_0208 — the three NOOD_0207 follow-ups, all one defect wearing three
hats: the engine knew something and didn't say it.

§1 The probe stops at the first state-writing action, so the page the test
   ASSERTS ON was never snapshotted and the first authored wording was a
   guess. NOOD_0207 attached the exact repair to the resulting red run, which
   turned a misdiagnosis into one cheap lap — but the lap was still there.
   `probe: {perform: true}` hands those actions to the `--do` transaction
   (NOOD_0144) that has always been able to perform them, so the destination
   page becomes real evidence.

§2 A landing page can carry a CTA literally named "add to <dest>" that merely
   NAVIGATES to the page where you add it. It qualifies as the mutation
   control by name, the compiler emits the click, and the run fails one step
   later on an item that was never added — blaming the next control. A
   button/submit sibling now outranks it; when it is the only candidate the
   risk is NAMED rather than refused (NOOD_0207's lesson: a heuristic must not
   turn a working green into a block), and `perform: true` settles it by
   clicking.

§3 The benchmark printed "final run not green" while the actionable line —
   `run: playwright install chromium` — sat unread in the payload. That is
   the same diagnose-without-repair defect, live in the tool that measures it,
   and it cost a full engine bisect on a one-command environment problem.

No browser, no LLM, no network.
"""
from types import SimpleNamespace

from noodle import regression
from noodle.agents.web import probe as probe_mod
from noodle.repl import goal as goal_mod


def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _page(controls=None, headings=None, **over):
    pg = {"url": "https://x/", "title": "t", "controls": controls or [],
          "headings": headings or [], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    pg.update(over)
    return pg


# --- §1 perform: the destination page becomes evidence ----------------------

def _flow_goal(**probe_opts):
    g = {"scenario": "Mutate then continue",
         "actions": [{"do": "add_to", "id": "a1", "destination": "basket",
                      "within": "Widget G"},
                     {"do": "click", "target": "Continue to checkout"},
                     {"do": "enter", "target": "Postcode", "value": "SW1A"},
                     {"do": "click", "target": "Place order"}],
         "checks": [{"see": "Order placed"}]}
    if probe_opts:
        g["probe"] = probe_opts
    return g


def test_perform_is_off_unless_the_goal_opts_in():
    # it writes state on someone's app — nothing may turn it on implicitly
    assert goal_mod.perform_do(_flow_goal()) == []
    args = goal_mod.probe_args(_flow_goal())
    assert args["perform"] is False and args["do"] is None


def test_perform_emits_the_post_gate_chain_as_do_actions():
    assert goal_mod.perform_do(_flow_goal(perform=True)) == [
        "click Continue to checkout",
        "enter SW1A in Postcode",
        "click Place order"]


def test_the_add_to_itself_is_left_to_the_probes_own_mutation_step():
    # its control resolves from probe evidence, which does not exist yet when
    # probe_args runs — the probe takes the destination as `mutate` instead
    dos = goal_mod.perform_do(_flow_goal(perform=True))
    assert not any("add" in d.lower() and "basket" in d.lower() for d in dos)
    assert goal_mod.probe_args(_flow_goal(perform=True))["mutate"] == "basket"


def test_nothing_before_the_gate_is_ever_performed():
    g = {"scenario": "s",
         "actions": [{"do": "click", "target": "Accept"},
                     {"do": "add_to", "destination": "basket",
                      "within": "Widget G"},
                     {"do": "click", "target": "Checkout"}],
         "checks": [{"see": "x"}], "probe": {"perform": True}}
    assert goal_mod.perform_do(g) == ["click Checkout"]


def test_a_goal_with_no_gate_performs_nothing():
    g = {"scenario": "s", "actions": [{"do": "click", "target": "a"}],
         "checks": [{"see": "x"}], "probe": {"perform": True}}
    assert goal_mod.perform_do(g) == []


def test_the_chain_stops_at_an_action_the_do_grammar_cannot_express():
    # pretending otherwise would claim evidence for a page never reached
    g = {"scenario": "s",
         "actions": [{"do": "add_to", "destination": "d", "within": "W"},
                     {"do": "click", "target": "Next"},
                     {"do": "upload", "target": "File", "file": "a.pdf"},
                     {"do": "click", "target": "Submit"}],
         "checks": [{"see": "x"}], "probe": {"perform": True}}
    assert goal_mod.perform_do(g) == ["click Next"]


def test_the_performed_chain_is_capped():
    acts = [{"do": "add_to", "destination": "d", "within": "W"}]
    acts += [{"do": "click", "target": f"Step {i}"} for i in range(20)]
    g = {"scenario": "s", "actions": acts, "checks": [{"see": "x"}],
         "probe": {"perform": True}}
    assert len(goal_mod.perform_do(g)) == goal_mod._PERFORM_CAP


def test_probe_validates_the_perform_key():
    g = _flow_goal(perform=True)
    assert goal_mod.validate(g) == []
    g["probe"] = {"perfrom": True}
    assert any("unknown field" in e for e in goal_mod.validate(g))


def test_a_performed_block_is_reachable_not_a_hidden_panel():
    """The distinction the whole opt-in rests on.

    A `reveal` block needs an explicit opening click before its controls are
    reachable. A `performed` block is a state the FLOW walked to — requiring a
    reveal click for it would block the very actions that produced it."""
    pg = _page(headings=["Basket"])
    pg["revealed"] = [
        {"controls": [_ctrl("Place order", "#po")],
         "headings": ["Order summary"], "revealed_by": "do: click Checkout",
         "performed": True},
        {"controls": [_ctrl("Help", "#h")], "headings": [],
         "revealed_by": "Support menu"}]
    blocks = goal_mod._page_blocks(pg)
    phases = {b[1] for b in blocks}
    assert "performed" in phases and "reveal" in phases
    performed = next(b for b in blocks if b[1] == "performed")
    assert performed[2] is None      # no trigger — nothing to click first


def test_a_control_on_a_performed_page_resolves_without_a_reveal_click():
    pg = _page(controls=[_ctrl("Add to basket", ".card button")],
               headings=["Widget G"])
    pg["revealed"] = [{"controls": [_ctrl("Place order", "#po")],
                       "headings": ["Order summary"],
                       "revealed_by": 'do: click Place order',
                       "performed": True}]
    goal = {"scenario": "s",
            "actions": [{"do": "add_to", "id": "a1", "destination": "basket",
                         "within": "Widget G"},
                        {"do": "click", "target": "Place order"}],
            "checks": [{"see": "Order summary"}],
            "probe": {"perform": True}}
    ev = goal_mod.evidence(goal, {"pages": [pg], "errors": []})
    assert ev["blocking"] == [], ev["blocking"]
    # and the destination wording is PROVEN, not deferred to the run
    assert ev["proven"].get("see:Order summary") == "Order summary"


# --- §2 the navigation CTA that reads like a mutation -----------------------

def test_a_button_outranks_a_navigation_shaped_link_naming_the_same_thing():
    link = _ctrl("add to cart", "#nav-cta", kind="link",
                 href="https://x/cart-page")
    button = _ctrl("add to cart", "#real", kind="button")
    ctrl, why = goal_mod.mutation_control([link, button], "cart")
    assert ctrl is not None and ctrl["selector"] == "#real"


def test_navigation_shaped_is_about_structure_not_the_name():
    assert goal_mod.navigation_shaped(
        _ctrl("add to cart", "#a", kind="link", href="/cart"))
    # a real button, an in-page anchor, and a submit are all fine
    assert not goal_mod.navigation_shaped(_ctrl("add to cart", "#b"))
    assert not goal_mod.navigation_shaped(
        _ctrl("add to cart", "#c", kind="link", href="#"))
    assert not goal_mod.navigation_shaped(
        _ctrl("add to cart", "#d", kind="link", href="javascript:void(0)"))
    assert not goal_mod.navigation_shaped(
        _ctrl("add to cart", "#e", kind="link", href="/x", submit=True))


def test_a_lone_navigation_shaped_link_warns_and_still_compiles():
    """Never a block on its own — plenty of real mutate-controls are anchors
    that POST server-side, and NOOD_0207's lesson is that a heuristic must not
    turn a working green into a refusal."""
    pg = _page(controls=[_ctrl("add to cart", "#cta", kind="link",
                               href="https://x/cart-page")],
               headings=["Widget G"])
    goal = {"scenario": "s",
            "actions": [{"do": "add_to", "id": "a1", "destination": "cart",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    ev = goal_mod.evidence(goal, {"pages": [pg], "errors": []})
    assert ev["blocking"] == []
    assert any("only navigates" in w and "perform: true" in w
               for w in ev["warnings"])


def test_a_proven_dead_click_blocks_instead_of_compiling():
    pg = _page(controls=[_ctrl("add to cart", "#cta", kind="link",
                               href="https://x/cart-page")],
               headings=["Widget G"])
    # the probe clicked it: no navigation, no delta — it did nothing
    pg["mutation_proof"] = {"control": "add to cart", "navigated": False,
                            "delta": False, "settled": "idle"}
    goal = {"scenario": "s",
            "actions": [{"do": "add_to", "id": "a1", "destination": "cart",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    ev = goal_mod.evidence(goal, {"pages": [pg], "errors": []})
    bad = next(b for b in ev["blocking"] if "add to cart" in b)
    assert "did not change at all" in bad


def test_a_proven_working_mutation_compiles_with_no_warning():
    pg = _page(controls=[_ctrl("add to cart", "#cta")],
               headings=["Widget G"])
    pg["mutation_proof"] = {"control": "add to cart", "navigated": False,
                            "delta": True, "settled": "mutation"}
    goal = {"scenario": "s",
            "actions": [{"do": "add_to", "id": "a1", "destination": "cart",
                         "within": "Widget G"}],
            "checks": [{"see": "Widget G"}]}
    ev = goal_mod.evidence(goal, {"pages": [pg], "errors": []})
    assert ev["blocking"] == [] and ev["warnings"] == []


def test_perform_mutation_records_what_the_click_actually_did():
    """The proof `_prove_mutation` could never give: it records a directly
    observed control WITHOUT clicking it, so it proves existence, never
    behaviour."""
    calls = []

    class _Loc:
        first = property(lambda self: self)

        def click(self, timeout=None):
            calls.append("click")

    pg = _page(controls=[_ctrl("add to cart", "#cta")])
    page = SimpleNamespace(
        url="https://x/", locator=lambda s: _Loc(),
        title=lambda: "t", evaluate=lambda *a, **k: {"controls": [],
                                                     "headings": []})
    probe_mod._perform_mutation(page, pg, "cart", 1000)
    assert calls == ["click"]
    proof = pg["mutation_proof"]
    assert proof["control"] == "add to cart"
    assert proof["navigated"] is False and proof["delta"] is False


def test_perform_mutation_is_advisory_and_never_raises():
    pg = _page(controls=[_ctrl("add to cart", "#cta")])

    class _Boom:
        first = property(lambda self: self)

        def click(self, timeout=None):
            raise RuntimeError("detached")

    page = SimpleNamespace(url="https://x/", locator=lambda s: _Boom(),
                           title=lambda: "t",
                           evaluate=lambda *a, **k: {"controls": [],
                                                     "headings": []})
    probe_mod._perform_mutation(page, pg, "cart", 1000)
    assert "mutation_proof" not in pg
    assert any("could not click" in w for w in pg["warnings"])


def test_perform_mutation_is_silent_when_no_control_resolves():
    pg = _page(controls=[_ctrl("help", "#h")])
    probe_mod._perform_mutation(None, pg, "cart", 1000)   # never touched
    assert "mutation_proof" not in pg


# --- §3 the benchmark names its own cause -----------------------------------

def test_a_blocked_case_reports_the_blocker_not_the_generic_skip():
    tc = {"id": "tc1", "elapsed_s": 5, "run_s": None, "corrections": 0,
          "lines": 10, "green": False, "verified": False,
          "error": "probe returned no page evidence: browser engine "
                   "'chromium' could not be launched — run: playwright "
                   "install chromium"}
    v = regression.score({"test_cases": [tc]})
    line = next(r for r in v["regressions"] if r.startswith("tc1:"))
    assert "playwright install chromium" in line
    assert "playwright install chromium" in regression.render_table(v)


def test_the_failure_line_survives_a_multi_line_blocker():
    tc = {"id": "tc1", "elapsed_s": 5, "run_s": None, "corrections": 0,
          "lines": 10, "green": False, "verified": False,
          "error": "first line is the actionable one\nbox drawing\nmore noise"}
    v = regression.score({"test_cases": [tc]})
    line = next(r for r in v["regressions"] if r.startswith("tc1:"))
    assert line.endswith("first line is the actionable one")


def test_a_green_case_still_reads_clean():
    tcs = [{"id": p["id"], "elapsed_s": 20, "run_s": 14, "lines": 9,
            "corrections": 0, "green": True, "verified": True}
           for p in regression.PROMPTS]
    v = regression.score({"test_cases": tcs})
    assert v["verdict"] == "PASS" and v["regressions"] == []


def test_a_case_with_no_recorded_cause_says_only_what_it_knows():
    tc = {"id": "tc1", "elapsed_s": 5, "run_s": 3, "corrections": 0,
          "lines": 10, "green": False, "verified": False}
    v = regression.score({"test_cases": [tc]})
    assert "tc1: final run not green" in v["regressions"]

"""NOOD_0226 — a goal whose own first step is a sign-in could not be authored.

Measured on `main` @ d34a902, byte-identically, with a two-field sign-in and
three steps behind it:

    _runtime_gate(actions)  -> 0      (the first `enter`)
    _evidence_gate(actions) -> None   (no add_to / press_key / go_back)
    probe_args(goal)        -> click: None, do: None

So the probe loaded the landing page and nothing else, and every step behind
the sign-in blocked — offering the SIGN-IN page's controls as the near misses
that should repair them. The compiled Gherkin was already correct; the engine
simply refused to admit it wrote one, and the repair it invited ("rename your
target to one of these") is never the right move. Checks in that same
position deferred to the run (NOOD_0207) — actions blocked. That asymmetry is
what this ticket removes.

§1 `_reach_gate` — the boundary drawn from what the probe ACTUALLY did: the
   evidence gate, or the first COMMIT click (a click at or after the runtime
   gate, which probe_args refuses to perform by its own rule). Whichever
   comes first.
§2 a step past that boundary defers to the run instead of blocking, on
   exactly the terms NOOD_0207 already gave checks. A step at or before it is
   untouched — NOOD_0156's "an unprobed control after a pick still blocks"
   and NOOD_0212's `within:` gate both stand.
§3 the blocker says where the evidence stops, so a reader cannot mistake an
   earlier page's vocabulary for the app's.
§4 a selector-shaped target resolves BY SELECTOR. `_norm` strips punctuation,
   so '[id="add-to-cart"]' normalized to 'id add to cart' and the substring
   pass bound any control merely NAMED "add to cart" — a different element,
   with a different selector, picked by luck and shipped as proven.

No app, product or brand name appears in any fixture: the shapes are a
two-field sign-in, a commit click, and a page of cards.
"""
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

# --- fixtures: the shape, not a domain ---------------------------------------

def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _signin_page():
    """The only page the probe can reach on a gated flow: the sign-in form."""
    pg = {"url": "https://x/", "title": "Sign in",
          "controls": [_ctrl("username", "#username", kind="input"),
                       _ctrl("password", "#password", kind="input"),
                       _ctrl("login", "#login", submit=True)],
          "headings": ["Members"], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    return {"pages": [pg], "errors": []}


def _gated_goal(**over):
    goal = {"scenario": "A member acts on the page behind the sign-in",
            "actions": [{"do": "enter", "target": "username", "value": "u"},
                        {"do": "enter", "target": "password", "value": "p"},
                        {"do": "click", "target": "login", "id": "signin"},
                        {"do": "click", "target": "Add to Cart",
                         "id": "add1", "within": "Item One"},
                        {"do": "click", "target": "view cart", "id": "cart"}],
            "checks": [{"see": "Catalog", "after": "signin"},
                       {"any_of": ["Item One"], "after": "cart"}]}
    goal.update(over)
    return goal


def _blocks(result):
    return goal_mod._page_blocks(result["pages"][0])


# --- §1 the boundary is what the probe did, not which verbs appear -----------

def test_a_signin_prelude_had_no_boundary_at_all():
    """The measured starting point. Both gates are unchanged by this ticket —
    they are quoted here because the defect was their COMBINATION: index 0
    plus None left nothing to defer against."""
    acts = _gated_goal()["actions"]
    assert goal_mod._runtime_gate(acts) == 0        # the first enter
    assert goal_mod._evidence_gate(acts) is None    # no state-writing verb


def test_the_reach_gate_is_the_commit_click():
    acts = _gated_goal()["actions"]
    # the sign-in click is the last step with page evidence: its own control
    # is on the sign-in page, everything after it is not.
    assert goal_mod._reach_gate(acts) == 2


def test_the_reach_gate_never_moves_past_the_evidence_gate():
    """NOOD_0207's boundary still wins when it comes first — the add_to here
    is index 3, the only click is index 4."""
    acts = [{"do": "search", "term": "t"}, {"do": "pick", "id": "p"},
            {"do": "enter", "target": "f", "value": "v"},
            {"do": "add_to", "destination": "d", "item_from": "p"},
            {"do": "click", "target": "Place order"}]
    assert goal_mod._evidence_gate(acts) == 3
    assert goal_mod._reach_gate(acts) == 3


def test_a_reveal_click_before_the_runtime_gate_is_not_a_commit():
    """The probe PERFORMS those clicks (probe_args forwards them), so they
    are evidence, not a boundary."""
    acts = [{"do": "click", "target": "open panel"},
            {"do": "enter", "target": "serial", "value": "1"},
            {"do": "click", "target": "save"}]
    assert goal_mod._reach_gate(acts) == 2          # the save, not the open


def test_a_goal_with_no_gate_at_all_has_no_boundary():
    acts = [{"do": "click", "target": "a"}, {"do": "click", "target": "b"}]
    assert goal_mod._reach_gate(acts) is None


# --- §2 past the boundary a step defers to the run, exactly as a check does --

def test_the_signin_flow_authors_instead_of_blocking():
    ev = goal_mod.evidence(_gated_goal(), _signin_page())
    assert ev["blocking"] == [], ev["blocking"]
    # the two post-sign-in steps are the run's to prove, and say so
    assert ev["proven_phase"]["click:Add to Cart"] == "runtime"
    assert ev["proven_phase"]["click:view cart"] == "runtime"
    # the sign-in itself is probe-proven — the boundary step keeps its proof
    assert ev["proven"]["click:login"] == "login"


def test_the_deferred_steps_compile_with_the_authors_own_wording():
    goal = _gated_goal()
    ev = goal_mod.evidence(goal, _signin_page())
    feature, _ = goal_mod.compile_goal(goal, ev, "BASE_URL")
    assert 'User enters "u" in the "username" field' in feature
    assert 'User clicks "login"' in feature
    # the row-scoped form survives the deferral (NOOD_0222's runtime row-climb
    # is what resolves it)
    assert 'User clicks "Add to Cart" in the row containing "Item One"' \
        in feature
    assert 'User clicks "view cart"' in feature


def test_a_within_anchor_past_the_boundary_is_not_blocked_on_missing_text():
    """NOOD_0212's gate fired on `_eg is None`, which a sign-in flow always
    is — so the row text was demanded of the SIGN-IN page."""
    blocking = goal_mod.evidence(_gated_goal(), _signin_page())["blocking"]
    assert not any("Item One" in b for b in blocking), blocking


def test_a_within_anchor_before_the_boundary_still_blocks():
    """NOOD_0212 unchanged where the probe COULD have checked the scope: an
    unverified row anchor on a probed page authored ready:true and died at
    run time with "No row containing '<text>' found"."""
    goal = _gated_goal(actions=[
        {"do": "click", "target": "login", "within": "No Such Row"},
        {"do": "enter", "target": "username", "value": "u"}])
    blocking = goal_mod.evidence(goal, _signin_page())["blocking"]
    assert any("No Such Row" in b for b in blocking), blocking


def test_a_deferred_step_is_proven_by_the_run_not_reported_missing():
    """intent_trace called it "missing", which drags intent_verified false on
    every multi-page flow — including the ones the deferral makes authorable.
    Same contract the api arm (NOOD_0192) and NOOD_0188's no-control verbs
    already have: the label names which proof the step rests on."""
    goal = _gated_goal()
    ev = goal_mod.evidence(goal, _signin_page())
    trace = goal_mod.intent_trace(goal, ev)
    by_node = {t["node"]: t for t in trace}
    assert by_node["actions[2]"]["evidence"] == "probe:control"
    assert by_node["actions[3]"]["evidence"] == "runtime:control"
    assert by_node["actions[4]"]["evidence"] == "runtime:control"
    assert all(t["ok"] for t in trace), trace


# --- §3 the blocker says where the evidence stops ---------------------------

def test_a_blocker_on_the_boundary_step_names_the_reach_limit():
    goal = _gated_goal(actions=[
        {"do": "enter", "target": "username", "value": "u"},
        {"do": "click", "target": "sign on"},        # not the probed name
        {"do": "click", "target": "view cart"}])
    bad = next(b for b in goal_mod.evidence(goal, _signin_page())["blocking"]
               if "sign on" in b)
    assert "last step the probe has page evidence for" in bad
    assert "1 step(s) after it" in bad
    # and it still carries the repair for the step it CAN speak to
    assert '"login"' in bad


def test_the_clause_is_silent_when_the_goal_ends_at_the_boundary():
    goal = _gated_goal(actions=[
        {"do": "enter", "target": "username", "value": "u"},
        {"do": "click", "target": "sign on"}],
        checks=[{"see": "Members"}])
    bad = next(b for b in goal_mod.evidence(goal, _signin_page())["blocking"]
               if "sign on" in b)
    assert "page evidence" not in bad, bad


def test_a_still_blocking_step_past_the_boundary_disowns_the_vocabulary():
    """An ambiguity note is real evidence and still blocks (NOOD_0207) — but
    it was computed on a page this step never visits, and the reader has to
    be told that before acting on the names in it."""
    result = _signin_page()
    result["pages"][0]["controls"].append(
        _ctrl("Add to Cart", ".card button", unique=False, matches=20))
    goal = _gated_goal(actions=[
        {"do": "enter", "target": "username", "value": "u"},
        {"do": "click", "target": "login"},
        {"do": "click", "target": "Add to Cart"},
        {"do": "click", "target": "view cart"}])
    bad = next(b for b in goal_mod.evidence(goal, result)["blocking"]
               if "Add to Cart" in b)
    assert "matches 20" in bad                       # the note survives
    assert "never loaded" in bad
    assert "that earlier page's" in bad


def test_a_near_miss_after_a_pick_comes_off_the_landed_page():
    """Measured on a 1000-control listing: a blocked add-to-cart on the
    product page was answered with the RESULTS page's filter names. `_locate`
    already resolves picked-page-first (NOOD_0156); the shortlist that
    survives a failure to resolve has to agree with it, or the one repair it
    invites is a rename to a control this step never sees."""
    results = {"url": "https://x/s", "title": "results", "term": "t",
               "controls": [_ctrl("Electric fans", "#f1"),
                            _ctrl("Sesame street (2)", "#f2")],
               "headings": ["Item One"],
               "result_items": [{"caption": "Item One", "selector": "#i1",
                                 "actions": []}],
               "picked": {"url": "https://x/p", "title": "product",
                          "controls": [_ctrl("Select a store", "#atc"),
                                       _ctrl("Add to wishlist", "#wl")],
                          "headings": ["Item One"]}}
    pg = {"url": "https://x/", "title": "t", "controls": [],
          "headings": [], "search": results,
          "permission_prompts": [], "popups_closed": 0}
    goal = {"scenario": "s",
            "actions": [{"do": "search", "term": "t"},
                        {"do": "pick", "id": "p1", "target": "Item One"},
                        {"do": "click", "target": "add to basket"}],
            "checks": [{"see": "Item One"}]}
    bad = next(b for b in
               goal_mod.evidence(goal, {"pages": [pg], "errors": []})["blocking"]
               if "add to basket" in b)
    assert "Add to wishlist" in bad          # the landed page's vocabulary
    assert "Electric fans" not in bad        # not the results page's
    assert "Sesame street" not in bad


# --- §4 a selector is an instruction, not a caption --------------------------

def test_a_selector_target_never_binds_a_lookalike_by_name():
    """The measured luck-match: '[id="add-to-cart"]' normalizes to
    'id add to cart', which word-boundary containment matched against a
    control NAMED "Add to cart" whose selector was '.card button'."""
    blocks = [({"controls": [_ctrl("Add to cart", ".card button")]},
               "initial", None)]
    ctrl, _, _, note = goal_mod._locate('[id="add-to-cart"]', blocks)
    assert ctrl is None
    assert "selector, not a name" in note
    assert "pom_content" in note                     # the block names its fix


def test_a_selector_target_binds_the_control_that_carries_it():
    blocks = [({"controls": [_ctrl("Add to cart", ".card button"),
                             _ctrl("Select a store", '[id="add-to-cart"]')]},
               "initial", None)]
    ctrl, _, _, note = goal_mod._locate('[id="add-to-cart"]', blocks)
    assert note is None
    assert ctrl["name"] == "Select a store"          # by selector, exactly


def test_an_id_selector_is_recognised_but_a_caption_is_not():
    assert goal_mod._SELECTOR_SHAPED.match("#add-to-cart")
    assert goal_mod._SELECTOR_SHAPED.match('[data-testid="save"]')
    # names that merely start with selector punctuation stay names
    assert not goal_mod._SELECTOR_SHAPED.match("#1 Best Seller")
    assert not goal_mod._SELECTOR_SHAPED.match(".NET SDK")


# --- §5 a comma joins actions, and it was not a connector -------------------

def test_two_enters_in_one_step_are_two_actions():
    """Measured live on a sign-in brief: the step split at the ` and ` only,
    so the first enter's greedy target swallowed the second clause whole and
    authoring blocked as ambiguous — on the ordinary way a brief writes a
    login."""
    assert pe.split_steps(
        "Enter member1 in username, enter secret1 in password, "
        "and click Login") == [
        "Enter member1 in username", "enter secret1 in password",
        "click Login"]


def test_a_comma_only_splits_when_both_halves_carry_a_verb():
    """The gate that keeps this from eating ordinary prose. `_split_compound`
    already required it for ` and `; the comma inherits it unchanged."""
    # a list inside one search term
    assert pe.split_steps("search for cat, dog toys") == \
        ["search for cat, dog toys"]
    # a compound assertion — the verify grammar splits its own members later
    one = "Verify order is placed, subtotal should be $4.99"
    assert pe.split_steps(one) == [one]
    # a comma inside a URL is data
    assert pe.split_steps("go to https://x.test/a,b") == \
        ["go to https://x.test/a,b"]


def test_a_comma_joined_click_pair_is_two_clicks():
    assert pe.split_steps("click Checkout, click Place Order") == \
        ["click Checkout", "click Place Order"]


# --- §6 the row-scoped click the prompt grammar could not reach -------------

def test_a_row_scoped_click_parses_into_within():
    """The goal schema has `within:` and the step dictionary has the row form
    (NOOD_0222) — only the prompt grammar lacked it, so the generic click
    swallowed the whole phrase and the run died looking for a control named
    "Add to Cart in the row containing <title>"."""
    goal = pe.expand("go to https://x.test/\n"
                     "Click Add to Cart in the row containing Item One\n"
                     "verify Item One")["goal"]
    assert goal["actions"] == [
        {"do": "click", "target": "Add to Cart", "within": "Item One"}]


def test_the_row_form_does_not_eat_an_ordinary_click():
    goal = pe.expand("go to https://x.test/\nClick Add to Cart\n"
                     "verify Item One")["goal"]
    assert goal["actions"] == [{"do": "click", "target": "Add to Cart"}]


# --- §7 a mid-flow check belongs where the brief put it ---------------------

def test_a_check_between_two_actions_is_anchored_to_the_first():
    """An unanchored check compiles after the LAST action (NOOD_0158), so
    "open the cart / verify the item / check out" asserted the cart's
    contents on the page checkout left behind. Measured live: three correct
    assertions, all run one page too late, red run for a flow that worked."""
    out = pe.expand("go to https://x.test/\nclick view cart\n"
                    "verify Item One\nclick Checkout\nverify Order placed")
    goal = out["goal"]
    mid = next(c for c in goal["checks"] if c.get("see") == "Item One")
    anchor = goal["actions"][0]
    assert anchor["id"] and mid["after"] == anchor["id"]
    # the trailing check keeps its end-of-scenario position — anchoring it
    # would change compiled order for every flow that already works
    last = next(c for c in goal["checks"] if c.get("see") == "Order placed")
    assert "after" not in last


def test_a_check_before_the_first_action_still_anchors_to_start():
    """NOOD_0199 unchanged."""
    goal = pe.expand("go to https://x.test/\nverify Welcome\n"
                     "click Sign in")["goal"]
    assert goal["checks"][0]["after"] == "start"


# --- §8 add_to past the reach boundary says what is actually wrong ----------

def test_add_to_past_the_boundary_does_not_offer_the_signin_page():
    """`add_to` lowers to a control whose name only the author knows, so it
    cannot defer like a click can — but "the page offers: username, password,
    login" invites a repair onto the sign-in page, which is never right."""
    goal = _gated_goal(actions=[
        {"do": "enter", "target": "username", "value": "u"},
        {"do": "click", "target": "login"},
        {"do": "add_to", "id": "a1", "within": "Item One",
         "destination": "order"}])
    bad = next(b for b in goal_mod.evidence(goal, _signin_page())["blocking"]
               if b.startswith("add_to"))
    assert "the page offers" not in bad
    assert 'click "<its name>"' in bad          # the repair that works
    assert "probe: {perform: true}" in bad      # and the other one

"""NOOD_0233 — walk the login gate before looking behind it.

Authoring "log in, then search" against a gated app blocked at authoring
time with `search: no search box found` — a message naming the wrong thing.
The probe only looked at the STARTING page, which on a gated app is the
login form; the search box it refused to believe in sat one submit-click
away, and the goal's own actions said exactly how to get there. The
workaround (`probe: {perform: true}`) walks EVERY remaining action, which
merely writing a test for "log in, add to cart, place order" must never do
(NOOD_0156: author-time state mutation).

What ships instead:

§1 `gate_chain` — the goal's login prelude (credential-shaped enters + one
   submit click) detected by SHAPE, rendered as probe do-strings, STOPPING
   at the gate. Signup shapes refused: their submit CREATES an account.
§2 `probe_args` hands it to the probe as `gate_do`, a RESCUE-ONLY channel:
   never run in the NOOD_0168 post-search position, so no goal that authors
   today can newly fail.
§3 the wall is NAMED: "no search box found" on a page that visibly shows a
   password field now says so, and an undeclared wall coaches the exact
   repair — including whether workspace credentials already exist (named,
   never used: walking a wall the goal never declared would author a
   feature that cannot reach its own evidence at run time).
§4 the walk is transport, not evidence: gate-walk states are excluded from
   every proof scope, so an `after: start` check can never be "proven" by a
   post-login page (NOOD_0195: a ready:true that checked nothing).

No app, product or brand name appears in any fixture. No browser, no LLM.
"""
from noodle.agents.web import probe as probe_mod
from noodle.repl import goal as goal_mod

# --- fixtures: the shape, not a domain ---------------------------------------


def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _login_actions():
    return [{"do": "enter", "target": "username", "value": "u"},
            {"do": "enter", "target": "password", "value": "p"},
            {"do": "click", "target": "login", "id": "signin"}]


def _gated_search_goal(**over):
    goal = {"scenario": "A member searches behind the sign-in",
            "actions": _login_actions()
            + [{"do": "search", "term": "Rare Widget", "id": "searched"}],
            "checks": [{"count": "results", "min": 1, "after": "searched"}]}
    goal.update(over)
    return goal


def _signin_pg(**over):
    pg = {"url": "https://x/", "title": "Sign in",
          "controls": [_ctrl("username", "#username", kind="field"),
                       _ctrl("password", "#password", kind="field"),
                       _ctrl("login", "#login", submit=True)],
          "headings": ["Members"], "pom_yaml": "",
          "permission_prompts": [], "popups_closed": 0}
    pg.update(over)
    return pg


_WALL = {"fields": ["username", "password"], "submit": "login"}


# --- §1 gate_chain: detection by shape, chain stops at the gate --------------

def test_the_login_prelude_becomes_a_chain_that_stops_at_the_gate():
    # the goal continues into a mutation tail — NONE of it may be walked
    g = _gated_search_goal(actions=_login_actions() + [
        {"do": "search", "term": "Rare Widget", "id": "searched"},
        {"do": "add_to", "id": "a1", "destination": "cart",
         "within": "Rare Widget"},
        {"do": "click", "target": "place order"}])
    assert goal_mod.gate_chain(g) == [
        "enter u in username", "enter p in password", "click login"]


def test_a_single_secret_gate_is_a_gate():
    g = {"scenario": "s", "checks": [],
         "actions": [{"do": "enter", "target": "PIN", "value": "1234"},
                     {"do": "click", "target": "unlock"},
                     {"do": "search", "term": "x"}]}
    assert goal_mod.gate_chain(g) == ["enter 1234 in PIN", "click unlock"]


def test_a_form_with_a_non_credential_field_is_not_a_gate():
    g = _gated_search_goal(actions=[
        {"do": "enter", "target": "first name", "value": "Ada"},
        {"do": "enter", "target": "password", "value": "p"},
        {"do": "click", "target": "continue"},
        {"do": "search", "term": "x"}])
    assert goal_mod.gate_chain(g) == []


def test_a_signup_shaped_submit_is_never_walked():
    # its click CREATES an account — author-time state mutation (NOOD_0156)
    for submit in ("Sign up", "Register", "Create account", "Join now"):
        g = _gated_search_goal(actions=[
            {"do": "enter", "target": "email", "value": "a@b.c"},
            {"do": "enter", "target": "password", "value": "p"},
            {"do": "click", "target": submit},
            {"do": "search", "term": "x"}])
        assert goal_mod.gate_chain(g) == [], submit


def test_two_secret_fields_read_as_signup_confirm_not_login():
    g = _gated_search_goal(actions=[
        {"do": "enter", "target": "password", "value": "p"},
        {"do": "enter", "target": "password", "value": "p"},
        {"do": "click", "target": "continue"},
        {"do": "search", "term": "x"}])
    assert goal_mod.gate_chain(g) == []


def test_a_long_fill_is_a_form_not_a_gate():
    enters = [{"do": "enter", "target": t, "value": "v"}
              for t in ("username", "email", "account", "password")]
    g = _gated_search_goal(actions=enters + [
        {"do": "click", "target": "login"}, {"do": "search", "term": "x"}])
    assert goal_mod.gate_chain(g) == []


def test_enters_with_no_submit_click_are_not_a_gate():
    g = _gated_search_goal(actions=[
        {"do": "enter", "target": "username", "value": "u"},
        {"do": "enter", "target": "password", "value": "p"},
        {"do": "search", "term": "x"}])
    assert goal_mod.gate_chain(g) == []


# --- §2 probe_args: rescue-only channel, nothing else changes ----------------

def test_a_login_bearing_search_goal_gets_gate_do_without_any_opt_in():
    args = goal_mod.probe_args(_gated_search_goal())
    assert args["gate_do"] == [
        "enter u in username", "enter p in password", "click login"]
    assert args["do"] is None and args["perform"] is False


def test_perform_true_supersedes_the_gate_chain():
    # its own chain already walks the whole tail, prelude included
    args = goal_mod.probe_args(_gated_search_goal(probe={"perform": True}))
    assert args["gate_do"] is None
    assert args["do"] == [
        "enter u in username", "enter p in password", "click login"]


def test_a_searchless_login_goal_sends_no_gate_chain():
    # nothing consumes it: NOOD_0226 already defers post-gate steps to the run
    g = {"scenario": "s", "actions": _login_actions(),
         "checks": [{"see": "Members"}]}
    assert goal_mod.probe_args(g)["gate_do"] is None


def test_a_plain_search_goal_is_byte_identical_to_today():
    g = {"scenario": "s", "checks": [],
         "actions": [{"do": "search", "term": "x", "id": "s1"}]}
    assert goal_mod.probe_args(g)["gate_do"] is None


def test_a_suggest_goal_with_a_login_prelude_also_gets_the_chain():
    g = {"scenario": "s", "checks": [],
         "actions": _login_actions()
         + [{"do": "suggest", "term": "Rar", "option": "Rare Widget"}]}
    assert goal_mod.probe_args(g)["gate_do"] is not None


# --- §3 the wall is named, and the repair coached ----------------------------

def _wall_result(**pg_over):
    warning = ('--search "Rare Widget": no search box found — the page '
               'shows a login form; the search box is behind it')
    pg = _signin_pg(search_warning=warning, login_wall=dict(_WALL), **pg_over)
    return {"pages": [pg], "errors": []}


def test_an_undeclared_wall_blocks_with_the_wall_named_not_no_search_box():
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "search", "term": "Rare Widget", "id": "s1"}]}
    [b] = goal_mod.evidence(goal, _wall_result())["blocking"]
    assert "login wall" in b and "username, password" in b
    assert "no workspace credentials found" in b


def test_an_undeclared_wall_names_workspace_credentials_that_would_walk_it():
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "search", "term": "Rare Widget", "id": "s1"}]}
    [b] = goal_mod.evidence(goal, _wall_result(),
                            env_keys=["SHOP", "SHOP_USER",
                                      "SHOP_PASSWORD"])["blocking"]
    assert "SHOP_USER, SHOP_PASSWORD" in b.replace("SHOP_PASSWORD, SHOP_USER",
                                                   "SHOP_USER, SHOP_PASSWORD")
    assert "SHOP," not in b          # the base-URL key is not a credential


def test_a_declared_login_is_not_re_coached():
    # the walk reports its own failure; "add a login step" would misdirect
    [b] = goal_mod.evidence(_gated_search_goal(), _wall_result())["blocking"]
    assert b.startswith("search: ")
    assert "Add the login prelude" not in b


def test_the_suggest_wall_gets_the_same_clause():
    warning = ('--suggest "Rar": no search box found — the page shows a '
               'login form; the search box is behind it')
    pg = _signin_pg(suggest_warning=warning, login_wall=dict(_WALL))
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "suggest", "term": "Rar",
                         "option": "Rare Widget"}]}
    [b] = goal_mod.evidence(goal, {"pages": [pg], "errors": []})["blocking"]
    assert "login wall" in b


def test_no_wall_evidence_means_no_wall_clause():
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "search", "term": "Rare Widget", "id": "s1"}]}
    pg = _signin_pg(search_warning='--search "Rare Widget": no search box '
                                   'found')
    [b] = goal_mod.evidence(goal, {"pages": [pg], "errors": []})["blocking"]
    assert "login wall" not in b


# --- §4 the walk is transport, not evidence ----------------------------------

def _walked_result():
    """The probe payload after a SUCCESSFUL rescue: gate walked, search
    landed, results found. The gate-walk block carries post-login text."""
    pg = _signin_pg(
        revealed=[{"url": "https://x/home", "title": "Members Lounge",
                   "controls": [_ctrl("account menu", "#menu")],
                   "headings": ["Members Lounge"], "pom_yaml": "",
                   "performed": True, "gate_walk": True,
                   "revealed_by": "do: click login"}],
        search={"term": "Rare Widget", "url": "https://x/results",
                "controls": [], "headings": ["Results"],
                "results_summary": {"text": "2 results", "count": 2,
                                    "selector": ".count", "pom_yaml": "",
                                    "suggested_assertion": "x"}},
        search_after_gate="no search box on the landing page — walked the "
                          "goal's own login steps, then searched",
        do_requested=3, do_completed=3)
    return {"pages": [pg], "errors": []}


def test_a_login_bearing_goal_authors_after_the_rescue():
    ev = goal_mod.evidence(_gated_search_goal(), _walked_result())
    assert ev["blocking"] == []
    assert ev["proven"].get("search") == "Rare Widget"
    # the count check stays runtime-asserted — the gate stands (NOOD_0195)
    assert any("result" in s.lower() for s in ev["runtime_asserted"])


def test_the_payload_says_the_probe_logged_in_first():
    ev = goal_mod.evidence(_gated_search_goal(), _walked_result())
    assert any("walked the goal's own login steps" in w
               for w in ev["warnings"])


def test_gate_walk_states_prove_nothing():
    # "Members Lounge" exists ONLY on the post-login state the walk drove
    # through; an `after: start` (landing page) check must not see it
    goal = _gated_search_goal(
        checks=[{"any_of": ["Members Lounge"], "after": "start"}])
    ev = goal_mod.evidence(goal, _walked_result())
    assert any("Members Lounge" in b for b in ev["blocking"])


def test_gate_blocks_are_their_own_phase():
    blocks = goal_mod._page_blocks(_walked_result()["pages"][0])
    assert [ph for _, ph, _ in blocks] == ["initial", "gate", "search"]


# --- §5 the probe-side pieces ------------------------------------------------

class _FakePage:
    def __init__(self, wall):
        self._wall = wall

    def evaluate(self, js):
        if isinstance(self._wall, Exception):
            raise self._wall
        return self._wall


def test_no_search_box_on_a_wall_names_the_wall():
    pg = {}
    probe_mod._no_search_box(pg, _FakePage(dict(_WALL)), "search_warning",
                             "Rare Widget", "--search")
    assert pg["login_wall"] == _WALL
    assert "shows a login form" in pg["search_warning"]


def test_no_search_box_without_a_wall_keeps_the_old_message_verbatim():
    for page in (_FakePage(None), _FakePage(RuntimeError("boom"))):
        pg = {}
        probe_mod._no_search_box(pg, page, "search_warning",
                                 "Rare Widget", "--search")
        assert pg["search_warning"] == \
            '--search "Rare Widget": no search box found'
        assert "login_wall" not in pg


def test_walk_gate_tags_every_state_it_drove_through(monkeypatch):
    def fake_do(page, pg, actions, timeout_ms):
        pg.setdefault("revealed", []).append(
            {"performed": True, "controls": [], "headings": []})
        pg["do_requested"], pg["do_completed"] = len(actions), len(actions)
        return page

    monkeypatch.setattr(probe_mod, "_do", fake_do)
    pg = {"revealed": [{"revealed_by": "earlier reveal"}]}
    page, ok = probe_mod._walk_gate("page", pg, [("enter", "username", "u")],
                                    1000)
    assert ok and page == "page"
    assert "gate_walk" not in pg["revealed"][0]      # only the walk's own
    assert pg["revealed"][1]["gate_walk"] is True


def test_a_failed_walk_reports_not_ok(monkeypatch):
    def fake_do(page, pg, actions, timeout_ms):
        pg["do_failed"] = {"index": 0, "action": "do: enter username",
                           "selector": "#u", "error": "x", "skipped": []}
        return page

    monkeypatch.setattr(probe_mod, "_do", fake_do)
    _, ok = probe_mod._walk_gate("page", {}, [("enter", "username", "u")],
                                 1000)
    assert not ok


def test_a_bad_gate_chain_is_refused_before_any_browser_launches():
    out = probe_mod.probe(["http://127.0.0.1:1/"], gate_do=["bogus thing"])
    assert out["pages"] == []
    assert any("bad do action" in e["error"] for e in out["errors"])


# --- §6 wrong credentials: let it fail, bounded, observed --------------------

def _arm_rescue(monkeypatch, pg, walled_attempts, rejection="Bad login"):
    """Fake the walk + phase: the phase stays walled for the first
    `walled_attempts` re-runs, then opens. Returns the call-count box."""
    calls = {"do": 0, "again": 0}

    def fake_do(page, p, actions, timeout_ms):
        calls["do"] += 1
        p.setdefault("revealed", []).append(
            {"performed": True, "controls": [], "headings": []})
        return page

    def again(page):
        calls["again"] += 1
        if calls["again"] <= walled_attempts:
            pg["search_warning"] = "walled"
            pg["login_wall"] = dict(_WALL)

    monkeypatch.setattr(probe_mod, "_do", fake_do)
    monkeypatch.setattr(probe_mod, "_login_rejection", lambda p: rejection)
    return calls, again


def test_wrong_credentials_fail_after_exactly_two_observed_attempts(
        monkeypatch):
    pg = {"search_warning": "walled", "login_wall": dict(_WALL)}
    calls, again = _arm_rescue(monkeypatch, pg, walled_attempts=99)
    probe_mod._rescue_through_gate(
        "page", pg, [("enter", "username", "u")], 1000,
        "search_warning", again, "search_after_gate", "walked, searched")
    assert calls["do"] == 2                       # the bound, not one more
    assert pg["gate_attempts"] == 2
    assert pg["login_wall"]["rejection"] == "Bad login"
    assert pg["search_warning"]                   # still walled — let it fail
    assert "search_after_gate" not in pg


def test_a_swallowed_first_submit_succeeds_on_the_second_attempt(monkeypatch):
    pg = {"search_warning": "walled", "login_wall": dict(_WALL)}
    calls, again = _arm_rescue(monkeypatch, pg, walled_attempts=1)
    probe_mod._rescue_through_gate(
        "page", pg, [("enter", "username", "u")], 1000,
        "search_warning", again, "search_after_gate", "walked, searched")
    assert calls["do"] == 2 and "search_warning" not in pg
    assert pg["search_after_gate"].startswith("walked, searched (2 attempts")


def test_a_first_attempt_success_keeps_the_plain_note(monkeypatch):
    pg = {"search_warning": "walled", "login_wall": dict(_WALL)}
    calls, again = _arm_rescue(monkeypatch, pg, walled_attempts=0)
    probe_mod._rescue_through_gate(
        "page", pg, [("enter", "username", "u")], 1000,
        "search_warning", again, "search_after_gate", "walked, searched")
    assert calls["do"] == 1
    assert pg["search_after_gate"] == "walked, searched"


def test_a_structurally_failed_chain_never_retries(monkeypatch):
    # control not found: an identical chain is a guessed fix (do_failed
    # already names the exact step) — and no rejection is invented
    def fake_do(page, pg, actions, timeout_ms):
        pg["do_failed"] = {"index": 0, "action": "do: enter username",
                           "selector": "#u", "error": "x", "skipped": []}
        return page

    monkeypatch.setattr(probe_mod, "_do", fake_do)
    pg = {"search_warning": "walled", "login_wall": dict(_WALL)}
    probe_mod._rescue_through_gate(
        "page", pg, [("enter", "username", "u")], 1000,
        "search_warning", lambda p: None, "search_after_gate", "n")
    assert "rejection" not in pg["login_wall"]


def test_the_blocker_carries_the_pages_own_rejection_words():
    warning = ('--search "Rare Widget": no search box found — the page '
               'shows a login form; the search box is behind it')
    pg = _signin_pg(search_warning=warning,
                    login_wall={**_WALL, "rejection": "Invalid credentials"},
                    gate_attempts=2)
    [b] = goal_mod.evidence(_gated_search_goal(),
                            {"pages": [pg], "errors": []})["blocking"]
    assert 'rejected it: "Invalid credentials"' in b and "2×" in b
    assert "fix the credential values" in b
    assert "Add the login prelude" not in b       # the steps exist; the
                                                  # values are the defect


def test_no_rejection_captured_means_no_rejection_clause():
    goal = {"scenario": "s", "checks": [],
            "actions": [{"do": "search", "term": "Rare Widget", "id": "s1"}]}
    [b] = goal_mod.evidence(goal, _wall_result())["blocking"]
    assert "rejected it" not in b

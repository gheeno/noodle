"""NOOD_0137 — constrained goal authoring: the engine, not the model, owns
Gherkin, POM, step order, and scope.

The 29.185-AIC regression session (docs/benchmark-nood-0131.md) failed
three runs on model-authored integration mistakes that instructions had
already warned against: substituted popup steps, a manual search-trigger
click beside the composite search step, and a dropped `match: {}` POM
header. Wording can't fix that for every model tier; compilation can. The
host LLM maps the user's prompt to a small validated `goal` object and the
engine deterministically compiles it into the artifacts — so those failure
modes become structurally impossible, whichever model is driving.

Everything here is pure Python over a probe result — unit-testable without
a browser. core.author_test wires it to the real probe/run.
"""
import difflib
import re

import yaml

# NOOD_0227 — the traversal/validation half lives in goal_evidence
# (RCA §7: goal.py had outgrown review); every name is re-exported
# here so goal.<name> stays the stable API for core/probe/tests.
from noodle.repl.goal_evidence import (  # noqa: F401
    _ADDITIVE_ITEM_RE,
    _DUPLICATE_CEILING,
    _ITEM_ACTION_EXCLUDE_RE,
    _NORM_RE,
    _PERFORM_CAP,
    _PROBE_FOLLOWS,
    _SELECTOR_SHAPED,
    _SUBMIT_INTENT_RE,
    _additive_item_action,
    _auth_synonyms,
    _beyond_probe_reach,
    _block_texts,
    _check_scope,
    _click_do_string,
    _closest_first,
    _contains,
    _do_fail_clause,
    _do_label_target,
    _evidence_gate,
    _find_control,
    _find_text,
    _iter_controls,
    _locate,
    _mutating_name,
    _near_miss,
    _norm,
    _observed_count,
    _page_blocks,
    _reach_clause,
    _reach_gate,
    _reach_label,
    _reveal_click_before,
    _runtime_gate,
    _searched_clause,
    _shared_phrase,
    bind_result,
    block_mutation_candidates,
    evidence,
    item_actions,
    mutation_control,
    navigation_env,
    navigation_shaped,
    navigation_urls,
    perform_do,
    probe_args,
    same_url,
)

# --- goal schema -------------------------------------------------------------

_GOAL_KEYS = {"scenario", "actions", "checks", "dismissals", "probe",
              # NOOD_0156 — explicit opt-out for deliberate workflow/setup
              # scenarios; the default is assertion-required (a goal with
              # actions but no checks gets a generated postcondition, or
              # blocks when none can be derived — infer_postcondition).
              "allow_no_assertion",
              # NOOD_0156 (intent contract v2) — ordered requested URLs. Every
              # entry compiles to its own navigation Given (stored in the app
              # environments.yaml, referenced as {env:...}), so a multi-URL
              # prompt requirement can never silently collapse to one page.
              "navigation",
              # NOOD_0211 — run-wide evidence policy from a prompt directive
              # ("each assertion must contain an evidence screenshot" / "no
              # screenshots"). Compiles to ONE scenario tag instead of a
              # ( take a screenshot ) marker pasted onto every line.
              "evidence"}

# NOOD_0225 — "all" (every passed step) joins the enum. The runtime tag
# @evidence has meant this since NOOD_0153; the goal schema simply couldn't
# say it, so a brief asking for "a screenshot on each step" compiled to the
# narrower per-assertion mode and the action steps shipped nothing.
_EVIDENCE_MODES = {"all", "assertions", "off"}
_ACTION_KEYS = {"search": {"do", "id", "term"},
                # NOOD_0141 — typeahead pick: type `term`, click the row whose
                # text matches `option`. Compiles to the composite suggestion
                # step + the intent assertion; probed via probe --suggest.
                "suggest": {"do", "id", "term", "option"},
                # NOOD_0156 — evidence-bound result selection: "click any
                # matching result" binds to ONE concrete probe-observed result
                # caption (bind_result). The binding is a bound target, not a
                # new intent — the same caption feeds the destination
                # assertion, so the user's generic request stays traceable.
                # `from` ties the pick to the search action's result set;
                # `strategy: first_actionable` (default) = the first stable
                # result item, membership in the result region — never the
                # first control whose caption happens to repeat the term.
                "pick": {"do", "id", "target", "from", "strategy"},
                # NOOD_0156 (intent contract v2) — semantic mutation: "add the
                # picked item to <destination>". The ENGINE lowers it to exact
                # observed controls (landed-page mutation control, plus at most
                # one probe-PROVEN prerequisite reveal); the host never invents
                # surface steps like 'Choose options'.
                # NOOD_0207 — `within` is the searchless shape: a catalogue or
                # card grid has no search box, so demanding a prior search
                # asked for a control that does not exist. The item's own text
                # scopes the mutation instead.
                "add_to": {"do", "id", "item_from", "destination", "within"},
                # NOOD_0207 — `within: "<text unique to the row/card>"` scopes
                # a REPEATED control (one Edit per table row, one Add per
                # card) to the instance the author means. Without it the only
                # way past the ambiguity gate was a hand-written POM selector,
                # which is the one repair a test author cannot automate.
                "click": {"do", "id", "target", "within"},
                "enter": {"do", "id", "target", "value", "within"},
                "select": {"do", "id", "target", "option"},
                # NOOD_0188 — the form/navigation verbs. The runtime has had
                # steps for all of these since NOOD_0186; only the cheap
                # deterministic authoring path couldn't reach them, so any
                # test with a checkbox, an upload, a hover menu, a date
                # picker or an Enter press dropped to hand-written Gherkin
                # (which is never intent-verified). This is the wiring, not
                # new capability.
                "check": {"do", "id", "target"},
                "uncheck": {"do", "id", "target"},
                "hover": {"do", "id", "target"},
                "upload": {"do", "id", "target", "file"},
                "press_key": {"do", "id", "key", "target"},
                "pick_date": {"do", "id", "target", "date"},
                "go_back": {"do", "id"},
                # NOOD_0192 — the cross-wok verb. REST steps are browserless
                # and legal in ANY scenario, so one action kind unlocks both
                # missing shapes at once: a PURE API test (api actions only →
                # @api, no browser, no probe) and the common mix — call the
                # API, then prove the UI shows it. The runtime has had the
                # rest_* steps since NOOD_0029; only the cheap deterministic
                # authoring path was web-only, so every API test — the whole
                # api wok — dropped to hand-written Gherkin, which is never
                # intent-verified and never measured for size.
                # NOOD_0201 — batch shapes: `rows` (list of uniform objects →
                # one data-table step, {key} placeholders in url/body) or
                # `repeat` (int → `repeated N times`, {i} = 1-based counter).
                # `expect_status` asserts EVERY call in the batch — a trailing
                # status check after N calls only ever saw the last one.
                # `headers`/`auth`/`store` close the three reasons an API goal
                # used to drop to hand-written feature_content: a protected
                # endpoint, a content type, and chaining an id from one
                # response into the next call's URL.
                # NOOD_0216 — `timeout` ("within N seconds"), and `wait_until`
                # ({status, contains?}): the action compiles to the polling
                # step alone (rest_wait_until issues the calls itself), which
                # closes the async-endpoint gap goals used to drop to
                # feature_content for.
                "api": {"do", "id", "method", "url", "body",
                        "rows", "repeat", "expect_status",
                        "headers", "auth", "store", "timeout", "wait_until"}}
_ACTION_REQUIRED = {"search": {"term"}, "suggest": {"term", "option"},
                    "pick": set(), "click": {"target"},
                    # NOOD_0207 — `item_from` left the required set: it is one
                    # of TWO ways to name the item (the other is `within`), and
                    # validate() below requires exactly one of them.
                    "add_to": {"destination"},
                    "enter": {"target", "value"}, "select": {"target", "option"},
                    "check": {"target"}, "uncheck": {"target"},
                    "hover": {"target"}, "upload": {"target", "file"},
                    "press_key": {"key"}, "pick_date": {"target", "date"},
                    "go_back": set(), "api": {"url"}}
_PICK_STRATEGIES = {"first_actionable"}
# NOOD_0201 — a {var:NAME} the engine will write: same shape the runner
# uppercases into its captured store.
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# NOOD_0192 — what the REST client's rest_call step accepts. GET is the
# default so `{do: api, url: ...}` is the whole minimal call.
_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# NOOD_0188 — actions that name a surface control, so they resolve through the
# probe's canonical spelling and earn a POM entry. `press_key` is here only
# when it carries an optional target (focus first); `go_back` never is.
_TARGETED_ACTIONS = ("click", "enter", "select", "check", "uncheck",
                     "hover", "upload", "pick_date")
# NOOD_0156 — "field" checks ({field, value}): the target field/control shows
# the entered/selected value. Always runtime-asserted (the probe never types
# data); the kind generated for assertion-free enter/select goals.
# NOOD_0156 — "item_in_destination" checks ({item_in_destination: "cart",
# expected_from: <pick id>, evidence?: screenshot}): the BOUND result caption
# must be visible in the named destination. Identity, never a count — a
# cart-count assertion cannot satisfy "the selected toy is in the cart".
# `evidence: screenshot` (any check kind) compiles the existing NOOD_0153
# "( take a screenshot )" marker onto the verification step itself.
# NOOD_0188 — "not_see" (text absent: the empty-state/removal case, which
# `see` cannot express) and "url_contains" (the flow landed where it should —
# the navigation half of a journey, previously unassertable from a goal).
# NOOD_0192 — the api wok's two assertions: "status" (the response code) and
# "response_contains" (a string in the body). Both are runtime-asserted by
# nature — the probe drives a browser, it never calls the API — and a `status`
# check is what makes a pure-API goal satisfy the assertion-required rule
# without a browser ever launching.
_CHECK_KEYS = {"see", "count", "any_of", "field", "value", "min", "name",
               "after", "item_in_destination", "expected_from", "evidence",
               "not_see", "url_contains", "status", "response_contains",
               "json", "equals", "contains", "items", "schema",
               # NOOD_0211 — the PAGE's own HTTP status (web), as distinct
               # from "status" which is the REST wok's last-call status.
               "page_status"}
# The check KINDS — exactly one per check. Single source (NOOD_0188): this
# tuple was hand-repeated in validate(), its error string and intent_trace(),
# so a new kind silently fell through to the any_of branch in three places.
# NOOD_0201 — "json": a typed assertion on the response body ({json: <dotted
# path>} plus exactly one of equals/contains/items) — substring checks can't
# tell "count": 20 from "count": 200.
# NOOD_0216 — "schema": the whole response validated against a JSON Schema
# file in the app's resources/ (rest_assert_schema — shape, not substrings).
_CHECK_KINDS = ("see", "not_see", "count", "any_of", "field",
                "item_in_destination", "url_contains", "status",
                "response_contains", "json", "page_status", "schema")
# NOOD_0163 — the landing-page anchor. NOOD_0158 made an unanchored check
# observe the END state, which is right for the outcome but left a check on
# text the LANDING page shows with nowhere to go: it compiled after the
# actions and asserted a first-page string against the page the flow ended
# on (one red run, then a hand-patch). `after: start` emits it before the
# first action, so a goal spanning pages binds every check to the page it
# was observed on.
_START = "start"
_DISMISSALS = {"popups", "location_prompt", "notifications_prompt"}
# NOOD_0208 — `perform: true` lets the probe EXECUTE the goal's post-gate
# actions instead of stopping at the first state-writing one, so the
# destination page becomes real evidence. Opt-in because it mutates the target
# app; see perform_do().
_PROBE_KEYS = {"discover", "perform"}

# NOOD_0161 — the minimal valid goal, one copy-pasteable object. The reviewed
# session spent 25 model inferences recovering this shape (goal passed as a
# string, then as an empty object, then 36 KB of CLI help, a failed rg, and
# repeated docs queries). Every invalid-goal error now ships this alongside
# the errors, and both skill cards carry it inline — schema recovery costs
# zero round trips. Values are <angle-bracket> placeholders on purpose: the
# example teaches the SHAPE, and nothing in it is domain- or site-specific.
EXAMPLE = {
    "scenario": "Search returns matching results",
    "dismissals": ["location_prompt", "popups"],
    "actions": [{"do": "search", "term": "<search term>"}],
    "checks": [{"count": "results", "min": 1},
               {"any_of": ["<expected text>", "<alternative wording>"]}],
}


def vocabulary() -> dict:
    """NOOD_0169 — the COMPLETE goal vocabulary, generated from the exact
    tables validate() enforces, so it cannot drift. Shipped beside EXAMPLE on
    every goal rejection: EXAMPLE teaches the minimal shape, but the keys it
    doesn't use (add_to, item_in_destination, expected_from, evidence) cost a
    reviewed session an 8-call docs hunt before the first author attempt.

    NOOD_0227 (A3) — the load-bearing constraints are structured FIELDS, not
    prose: "what may evidence: take" was buried mid-sentence in `notes`, and
    the one fact cost a reviewed session four python3 pipes to mine. `notes`
    keeps only what has no structured home."""
    return {
        "goal_keys": sorted(_GOAL_KEYS),
        "actions": {do: {"keys": sorted(keys),
                         "required": sorted(_ACTION_REQUIRED.get(do) or ())}
                    for do, keys in sorted(_ACTION_KEYS.items())},
        "check_keys": sorted(_CHECK_KEYS),
        "check_kinds": {"one_of": list(_CHECK_KINDS),
                        "rule": "exactly one kind per check"},
        "check_keys_detail": {
            "evidence": {"values": ["screenshot", "none"],
                         "scope": "per-check — a shot on THAT step; never "
                                  "hand-write screenshot steps or tags"},
            "after": {"values": ["start", "<action id>"],
                      "scope": "anchors a check to its page; start = the "
                               "landing page"},
            "expected_from": {"values": ["<pick id>"],
                              "scope": "pairs with item_in_destination"}},
        "goal_evidence_values": sorted(_EVIDENCE_MODES),
        "goal_evidence_default": "one shot on the last step",
        "navigation_semantics": "ordered URLs; actions run on the last",
        "dismissals": sorted(_DISMISSALS),
        "notes": "goal-level evidence sets the whole run; probe: "
                 "{discover: true} auto-reveals unnamed triggers, "
                 "{perform: true} performs the data-entry/commit steps so "
                 "every walked page is proven evidence (writes state on the "
                 "app — opt-in, never default)",
        "probe_keys": sorted(_PROBE_KEYS),
    }


# NOOD_0227 (A2) — the --section names, each a subset of vocabulary(). One
# fact should cost one bounded read, not a monolithic blob plus a pipe.
_VOCAB_SECTIONS = {
    "goal": ("goal_keys", "goal_evidence_values", "goal_evidence_default",
             "navigation_semantics", "notes"),
    "actions": ("actions",),
    "checks": ("check_keys", "check_kinds", "check_keys_detail",
               "goal_evidence_values"),
    "probe": ("probe_keys", "notes"),
    "dismissals": ("dismissals",),
}


def vocabulary_section(name: str) -> dict | None:
    """One named slice of vocabulary() (NOOD_0227, A2), or None for an
    unknown name — the caller lists the legal ones."""
    keys = _VOCAB_SECTIONS.get(str(name or "").strip().lower())
    if keys is None:
        return None
    v = vocabulary()
    return {k: v[k] for k in keys if k in v}


def vocabulary_hint() -> dict:
    """NOOD_0207 — the POINTER that replaces the dictionary on rejections.

    vocabulary() is ~2 KB of JSON and rode every rejection alongside
    VERBS_HELP; four rejections is ~18 KB, and — because cost is
    calls x context and the transcript only grows — that payload was then
    re-sent on every later call in the turn. EXAMPLE, goal_partial and the
    per-blocker repairs are the parts a reader acts on; the full tables are
    one flag away for the rare case that needs them."""
    return {"vocabulary_hint": "noodle author --vocabulary "
                               "(full goal schema + prompt grammar)",
            "verbs": "|".join(sorted(_ACTION_KEYS))}

# NOOD_0156 follow-up — lenient input canon. A tester's goal arrives in
# their words ("closes the popup if it appears", "close location prompt"),
# not our enum. Rejecting free text with an enum lecture costs one full
# author round trip per phrasing miss and hits non-native-English authors
# hardest. normalize() maps the OBVIOUS loose forms onto the canonical
# schema deterministically (keyword canon, no LLM) and reports every rewrite
# so the caller sees exactly what the engine understood. Anything it cannot
# map is left untouched for validate() to reject as before.
_DISMISSAL_CANON = (
    ("location_prompt", ("location", "geo")),
    ("notifications_prompt", ("notification",)),
    ("popups", ("popup", "pop up", "pop-up", "modal", "overlay", "banner",
                "cookie", "consent")),
)


def normalize(goal) -> tuple[dict, list[str]]:
    """(goal, notes) — canonicalized copy of the goal plus one human-readable
    note per rewrite. Deterministic and conservative: only unambiguous
    rewrites happen; everything else passes through to validate()."""
    if not isinstance(goal, dict):
        return goal, []
    g, notes = dict(goal), []
    # NOOD_0225 — the run-wide evidence policy in the caller's words. A host
    # LLM writes `evidence: "screenshot"`, `"every step"`, `"none"` or `false`
    # far more often than the three enum values, and rejecting those cost a
    # whole author round trip to learn a word list the engine can just read.
    ev = g.get("evidence")
    if ev is not None and ev not in _EVIDENCE_MODES:
        low = str(ev).casefold()
        canon = None
        if isinstance(ev, bool):
            canon = "all" if ev else "off"
        elif re.search(r"\b(?:no|none|off|without|skip|never|false)\b", low):
            canon = "off"
        elif re.search(r"\b(?:step|steps|all|every|always|true)\b", low):
            canon = "all"
        elif re.search(r"\b(?:assert\w*|check\w*|verif\w*|screenshots?)\b",
                       low):
            canon = "assertions"
        if canon:
            g["evidence"] = canon
            notes.append(f"evidence {ev!r} → {canon!r}")
    dis = g.get("dismissals")
    if isinstance(dis, list):
        out = []
        for d in dis:
            if d in _DISMISSALS or not isinstance(d, str):
                out.append(d)
                continue
            low = d.casefold()
            canon = next((key for key, words in _DISMISSAL_CANON
                          if any(w in low for w in words)), None)
            out.append(canon or d)
            if canon and canon != d:
                notes.append(f"dismissal {d!r} → {canon!r}")
        # the canon can collapse two phrasings onto one key — dedupe, in order
        seen: set = set()
        g["dismissals"] = [d for d in out
                           if not (d in seen or seen.add(d))]
    acts_in = g.get("actions")
    if isinstance(acts_in, list):
        actions = [dict(a) if isinstance(a, dict) else a for a in acts_in]
        acts = [a for a in actions if isinstance(a, dict)]
        # NOOD_0207 — a `within`-scoped add_to already names its item; wiring
        # an implied pick into it would bolt a search-driven binding onto the
        # searchless shape.
        add_missing = [a for a in acts if a.get("do") == "add_to"
                       and not a.get("item_from") and not a.get("within")]
        has_pick = any(a.get("do") == "pick" for a in acts)
        search_at = next((i for i, a in enumerate(actions)
                          if isinstance(a, dict) and a.get("do") == "search"),
                         None)
        if len(add_missing) == 1 and not has_pick and search_at is not None:
            # NOOD_0168 — the simple-prompt shape: search → add_to with no
            # pick spelled out. "Add something matching the search to the
            # destination" IMPLIES picking one result first; expand the goal
            # instead of walling it off on a schema error (the reviewed
            # session died exactly here and fell back to hand-authoring).
            ids = {a.get("id") for a in acts}
            pid = next(p for p in ("p", "picked", "picked_result")
                       if p not in ids)
            actions.insert(search_at + 1, {"do": "pick", "id": pid})
            add_missing[0]["item_from"] = pid
            g["actions"] = actions
            notes.append("add_to without item_from → inserted the implied "
                         f"pick {pid!r} (any result of the search) and "
                         "wired item_from to it")
    # NOOD_0213 — a brief that names its URL twice (a "base URL:" line AND a
    # "go to <url>" step) yielded two navigation entries, and each compiles to
    # its own Given: the run loaded the same page twice, under two env keys
    # (<APP> and <APP>_EN, one value). Only CONSECUTIVE repeats collapse —
    # A → B → A is a deliberate return trip, not a duplicate.
    nav = g.get("navigation")
    if isinstance(nav, list) and len(nav) > 1:
        def _u(n):
            return n.get("url") if isinstance(n, dict) else n
        out = []
        for n in nav:
            u, prev = _u(n), (_u(out[-1]) if out else None)
            if isinstance(u, str) and isinstance(prev, str) and same_url(u, prev):
                notes.append(f"navigation: dropped the repeated entry {u!r} "
                             "(the previous step already navigates there)")
                continue
            out.append(n)
        g["navigation"] = out
    # NOOD_0192 — an api assertion belongs beside its call, not stranded at
    # the end of a browser flow. With exactly one api action there is only one
    # response it could mean, so anchor it there; with more, the author says.
    acts_in = g.get("actions")
    api_idx = [i for i, a in enumerate(acts_in)
               if isinstance(a, dict) and a.get("do") == "api"] \
        if isinstance(acts_in, list) else []
    api_id = None
    if len(api_idx) == 1:
        api_id = acts_in[api_idx[0]].get("id")
        if api_id is None:
            acts = [dict(a) if isinstance(a, dict) else a for a in acts_in]
            taken = {a.get("id") for a in acts if isinstance(a, dict)}
            api_id = next(i for i in ("call", "call1", "api_call")
                          if i not in taken)
            acts[api_idx[0]]["id"] = api_id
            g["actions"] = acts
    checks = g.get("checks")
    acts_now = [a for a in (g.get("actions") or []) if isinstance(a, dict)]
    add_to = [a for a in acts_now if a.get("do") == "add_to"]
    pick_ids = [a.get("id") for a in acts_now
                if a.get("do") == "pick" and a.get("id")]
    if isinstance(checks, list):
        new_checks = []
        for c in checks:
            if not isinstance(c, dict):
                new_checks.append(c)
                continue
            c = dict(c)
            # NOOD_0192 — bind the api assertion to the sole api call.
            if ("status" in c or "response_contains" in c or "json" in c
                    or "schema" in c) \
                    and c.get("after") is None and api_id:
                c["after"] = api_id
                notes.append(f"api check anchored after {api_id!r} (the "
                             "goal's only api call)")
            # item_in_destination: true — "in the destination" with the
            # destination left implicit; unambiguous when exactly ONE add_to
            # action names it.
            if c.get("item_in_destination") is True and len(add_to) == 1:
                dest = add_to[0].get("destination")
                if isinstance(dest, str) and dest.strip():
                    c["item_in_destination"] = dest
                    notes.append("item_in_destination: true → "
                                 f"{dest!r} (from the add_to action)")
            # NOOD_0168 — an item_in_destination check with no expected_from
            # provenance is unambiguous when exactly one pick exists: the
            # picked item is the only thing that could have landed there.
            if isinstance(c.get("item_in_destination"), str) \
                    and not c.get("expected_from") and len(pick_ids) == 1:
                c["expected_from"] = pick_ids[0]
                notes.append("item_in_destination without expected_from → "
                             f"expected_from {pick_ids[0]!r} (the sole pick)")
            # evidence: any phrase that asks for a screenshot means screenshot
            # NOOD_0225 — and any phrase that DECLINES one means 'none'. The
            # negative is tested first: "no screenshot" contains "screenshot".
            ev = c.get("evidence")
            if isinstance(ev, str) and ev not in ("screenshot", "none"):
                low = ev.casefold()
                if re.search(r"\b(?:no|none|off|without|skip|never|false)\b",
                             low):
                    c["evidence"] = "none"
                    notes.append(f"evidence {ev!r} → 'none'")
                elif "screenshot" in low or "evidence" in low \
                        or "capture" in low:
                    c["evidence"] = "screenshot"
                    notes.append(f"evidence {ev!r} → 'screenshot'")
            elif ev is False:
                c["evidence"] = "none"
                notes.append("evidence false → 'none'")
            elif ev is True:
                c["evidence"] = "screenshot"
                notes.append("evidence true → 'screenshot'")
            new_checks.append(c)
        g["checks"] = new_checks
    return g, notes


def validate(goal) -> list[str]:
    """Every structural error in the goal, all at once — checked BEFORE any
    browser launches. [] means valid."""
    if not isinstance(goal, dict):
        # NOOD_0161 — name what arrived: the reviewed session passed a YAML
        # string (pom_content's shape), and "must be an object" alone didn't
        # tell it that.
        return [f"goal must be an object, got {type(goal).__name__} — pass "
                "the mapping itself, not a YAML/JSON string"]
    errs = []
    for k in set(goal) - _GOAL_KEYS:
        errs.append(f"unknown goal field {k!r}")
    if not isinstance(goal.get("scenario"), str) or not goal.get("scenario", "").strip():
        errs.append("scenario is required (a non-empty string)")
    actions = goal.get("actions") or []
    checks = goal.get("checks") or []
    if not isinstance(actions, list) or not isinstance(checks, list):
        return errs + ["actions and checks must be lists"]
    ids, searches, suggests = set(), 0, 0
    pick_ids, seen_search, search_ids = set(), False, set()
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            errs.append(f"actions[{i}] must be an object")
            continue
        do = a.get("do")
        if do not in _ACTION_KEYS:
            # NOOD_0195 — point at the nearest verb. Every rejection already
            # ships vocabulary(), but a list of 15 doesn't say which one the
            # invented name meant ('pick_suggestion' → 'suggest', one composite
            # action, not the two the author reached for).
            near = difflib.get_close_matches(str(do), sorted(_ACTION_KEYS),
                                             1, 0.5)
            errs.append(f"actions[{i}]: unknown do {do!r} "
                        + (f"— did you mean {near[0]!r}? " if near else "")
                        + f"(valid: {', '.join(sorted(_ACTION_KEYS))})")
            continue
        for k in set(a) - _ACTION_KEYS[do]:
            errs.append(f"actions[{i}] ({do}): unknown field {k!r}")
        for k in _ACTION_REQUIRED[do]:
            v = a.get(k)
            if v is not None and not isinstance(v, str):
                # NOOD_0207 — a present-but-unquoted scalar ({value: 5000})
                # reported "value is required", which is false: the value was
                # right there. The reader's repair is one pair of quotes, and
                # it cost a full round trip to learn that.
                errs.append(f"actions[{i}] ({do}): {k} must be a string — "
                            f"quote it in YAML (got {type(v).__name__})")
            elif not isinstance(v, str) or not v.strip():
                errs.append(f"actions[{i}] ({do}): {k} is required")
        if do == "search":
            searches += 1
            seen_search = True
            if a.get("id") is not None:
                search_ids.add(a["id"])
        elif do == "suggest":
            suggests += 1
        elif do == "pick":
            # A pick binds against search-results evidence — nothing exists
            # to bind to before a search action runs.
            if not seen_search:
                errs.append(f"actions[{i}] (pick): a pick selects one search "
                            "result — it must come after a search action")
            src = a.get("from")
            if src is not None and src not in search_ids:
                errs.append(f"actions[{i}] (pick): from={src!r} names no "
                            "earlier search action id")
            strat = a.get("strategy")
            if strat is not None and strat not in _PICK_STRATEGIES:
                errs.append(f"actions[{i}] (pick): unknown strategy {strat!r} "
                            f"(valid: {', '.join(sorted(_PICK_STRATEGIES))})")
            if a.get("id") is not None:
                pick_ids.add(a["id"])
        elif do == "add_to":
            # NOOD_0156 — the mutation acts on the BOUND pick result: without
            # an earlier pick there is no item to add, and the check side
            # would have no identity to assert.
            src = a.get("item_from")
            if isinstance(src, str) and src.strip() and src not in pick_ids:
                errs.append(f"actions[{i}] (add_to): item_from={src!r} names "
                            "no earlier pick action id")
            # NOOD_0207 — the item must be named ONE of two ways, and the
            # error says both. Demanding a pick (and so a search) was
            # unsatisfiable on a grid with no search box.
            if not (isinstance(src, str) and src.strip()) \
                    and not str(a.get("within") or "").strip():
                errs.append(
                    f"actions[{i}] (add_to): name the item — either "
                    "item_from: <id of an earlier pick> (search-driven) or "
                    'within: "<text unique to the item\'s card/row>" '
                    "(searchless)")
        elif do == "api":
            # NOOD_0192 — the method is the only closed set here; the url may
            # be absolute or a path relative to {var:REST_BASE_URL}, which is
            # the REST client's own contract, not ours to re-litigate.
            m = a.get("method")
            if m is not None and str(m).upper() not in _HTTP_METHODS:
                errs.append(f"actions[{i}] (api): unknown method {m!r} "
                            f"(valid: {', '.join(_HTTP_METHODS)})")
            if "'" in str(a.get("url", "")):
                errs.append(f"actions[{i}] (api): url must not contain a "
                            "single quote (it delimits the compiled step)")
            # NOOD_0201 — batch validation. Cells become Gherkin table text,
            # so they must be scalar and pipe/newline-free; the repeat ceiling
            # mirrors the runner's batch cap.
            rows, repeat = a.get("rows"), a.get("repeat")
            if rows is not None and repeat is not None:
                errs.append(f"actions[{i}] (api): rows and repeat are two "
                            "batch shapes — use one")
            if repeat is not None and (not isinstance(repeat, int)
                                       or isinstance(repeat, bool)
                                       or not 1 <= repeat <= 1000):
                errs.append(f"actions[{i}] (api): repeat must be an integer "
                            "1..1000 (volume belongs to the perf wok)")
            if rows is not None:
                if not (isinstance(rows, list) and rows
                        and all(isinstance(r, dict) and r for r in rows)):
                    errs.append(f"actions[{i}] (api): rows must be a "
                                "non-empty list of objects")
                else:
                    keys = set(rows[0])
                    if any(set(r) != keys for r in rows):
                        errs.append(f"actions[{i}] (api): every row must "
                                    "carry the same keys")
                    cells = [x for r in rows for x in [*r, *r.values()]]
                    if any(not isinstance(x, (str, int, float))
                           or isinstance(x, bool)
                           or "|" in str(x) or "\n" in str(x) for x in cells):
                        errs.append(f"actions[{i}] (api): row keys/values "
                                    "must be scalars free of '|' and "
                                    "newlines (they become a Gherkin table)")
            # NOOD_0201 — headers/auth/store. Values ride single-quoted step
            # text, so a single quote or newline in one would break out of the
            # compiled step; {env:}/{var:} refs are the point (a token never
            # belongs in a goal literal) and pass through untouched.
            def _clean_val(v) -> bool:
                return isinstance(v, str) and "'" not in v and "\n" not in v

            hdrs = a.get("headers")
            if hdrs is not None:
                if not isinstance(hdrs, dict) or not hdrs:
                    errs.append(f"actions[{i}] (api): headers must be a "
                                "non-empty object of name → value")
                elif not all(_clean_val(k) and _clean_val(v)
                             for k, v in hdrs.items()):
                    errs.append(f"actions[{i}] (api): header names/values must "
                                "be strings free of single quotes and newlines "
                                "(use {env:KEY} for secrets)")
            auth = a.get("auth")
            if auth is not None:
                scheme = auth.get("scheme") if isinstance(auth, dict) else None
                if scheme not in ("bearer", "basic"):
                    errs.append(f"actions[{i}] (api): auth must be "
                                "{scheme: 'bearer', token: '{env:KEY}'} or "
                                "{scheme: 'basic', user: ..., password: ...}")
                elif set(auth) - {"scheme", "token", "user", "password"}:
                    errs.append(f"actions[{i}] (api): auth has unknown "
                                f"field(s) {sorted(set(auth) - {'scheme', 'token', 'user', 'password'})}")
                elif scheme == "bearer" and not _clean_val(auth.get("token")):
                    errs.append(f"actions[{i}] (api): bearer auth needs a "
                                "token string (use {env:KEY} — never a "
                                "literal secret in a goal)")
                elif scheme == "basic" and not (_clean_val(auth.get("user"))
                                                and _clean_val(auth.get("password"))):
                    errs.append(f"actions[{i}] (api): basic auth needs user "
                                "and password strings (use {env:KEY})")
            store = a.get("store")
            if store is not None:
                if not isinstance(store, dict) or not store:
                    errs.append(f"actions[{i}] (api): store must be a "
                                "non-empty object of VAR → json path")
                elif not all(_VAR_NAME.match(str(k)) and _clean_val(v)
                             for k, v in store.items()):
                    errs.append(f"actions[{i}] (api): store keys must be "
                                "variable names ([A-Za-z_][A-Za-z0-9_]*) and "
                                "values json paths free of quotes")
            es = a.get("expect_status")
            if es is not None:
                if rows is None and repeat is None:
                    errs.append(f"actions[{i}] (api): expect_status only "
                                "rides a batch (rows/repeat) — a single call "
                                "asserts with a status check")
                elif not isinstance(es, int) or isinstance(es, bool) \
                        or not 100 <= es <= 599:
                    errs.append(f"actions[{i}] (api): expect_status must be "
                                "an HTTP status code (an integer, 100-599)")
            # NOOD_0216 — per-call budget and polling.
            to = a.get("timeout")
            if to is not None and (isinstance(to, bool)
                                   or not isinstance(to, (int, float))
                                   or not 0 < to <= 3600):
                errs.append(f"actions[{i}] (api): timeout must be seconds "
                            "(a number, 0 < t <= 3600)")
            wu = a.get("wait_until")
            if wu is not None:
                if any(a.get(k) is not None for k in
                       ("body", "rows", "repeat", "expect_status", "store")):
                    errs.append(f"actions[{i}] (api): wait_until is the whole "
                                "action (it polls the url itself) — drop "
                                "body/rows/repeat/expect_status/store")
                elif not isinstance(wu, dict) or set(wu) - {"status", "contains"} \
                        or "status" not in wu:
                    errs.append(f"actions[{i}] (api): wait_until must be "
                                "{status: <code>, contains?: '<text>'}")
                elif not isinstance(wu["status"], int) \
                        or isinstance(wu["status"], bool) \
                        or not 100 <= wu["status"] <= 599:
                    errs.append(f"actions[{i}] (api): wait_until.status must "
                                "be an HTTP status code (an integer, 100-599)")
                elif "contains" in wu and not _clean_val(wu["contains"]):
                    errs.append(f"actions[{i}] (api): wait_until.contains "
                                "must be a string free of single quotes")
        aid = a.get("id")
        if aid is not None:
            if aid in ids:
                errs.append(f"duplicate action id {aid!r}")
            if aid == _START:
                # reserved: it means "before every action" on a check's anchor
                errs.append(f"actions[{i}]: id {_START!r} is reserved — it is "
                            "the landing-page anchor for checks")
            ids.add(aid)
    if searches > 1:
        errs.append("at most one search action per goal (one bounded probe "
                    "transaction)")
    if searches + suggests > 1 and searches <= 1:
        # NOOD_0141 — both drive the one search box in the one bounded probe:
        # a submit AND a suggestion pick in one goal is two flows, two tests.
        errs.append("at most one search or suggest action per goal (one "
                    "bounded probe transaction)")
    if searches:
        # The engine's search step opens, fills, and submits in one composite
        # step; a manual trigger click beside it resolves hidden responsive
        # twins and times out (red run 2 of the regression session).
        for i, a in enumerate(actions):
            if isinstance(a, dict) and a.get("do") == "click" and \
                    "search" in _norm(a.get("target")):
                errs.append(
                    f"actions[{i}]: search is composite — remove the manual "
                    f"search-trigger click {a.get('target')!r}")
    if suggests:
        # NOOD_0141 — the suggestion step is composite too: it opens the box,
        # types, waits for the list, clicks the navigating row. A manual click
        # on a search trigger or a suggestion row beside it is the exact
        # hand-rolled chain the step exists to replace.
        for i, a in enumerate(actions):
            tn = _norm(a.get("target")) if isinstance(a, dict) else ""
            if isinstance(a, dict) and a.get("do") == "click" and \
                    ("search" in tn or "suggest" in tn):
                errs.append(
                    f"actions[{i}]: suggestion picking is composite — remove "
                    f"the manual click {a.get('target')!r}")
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            errs.append(f"checks[{i}] must be an object")
            continue
        for k in set(c) - _CHECK_KEYS:
            errs.append(f"checks[{i}]: unknown field {k!r}")
        kinds = [k for k in _CHECK_KINDS if k in c]
        if len(kinds) != 1:
            errs.append(f"checks[{i}]: exactly one of "
                        + " | ".join(_CHECK_KINDS))
            continue
        kind = kinds[0]
        if "evidence" in c and c["evidence"] not in ("screenshot", "none"):
            errs.append(f"checks[{i}]: evidence must be 'screenshot' (the "
                        "NOOD_0153 step marker) or 'none' (this step declines "
                        "one) when present")
        if kind == "item_in_destination":
            # Identity intent: the bound pick result must appear in the
            # destination. expected_from is the provenance link back to the
            # pick — a count can never satisfy this check kind.
            if not isinstance(c["item_in_destination"], str):
                errs.append(f"checks[{i}]: item_in_destination must be the "
                            "destination name (a string; '' = current view)")
            src = c.get("expected_from")
            if not isinstance(src, str) or not src.strip():
                errs.append(f"checks[{i}]: expected_from is required — the "
                            "id of the pick action whose bound result this "
                            "check asserts")
            elif src not in pick_ids:
                errs.append(f"checks[{i}]: expected_from={src!r} names no "
                            "pick action id")
            if "value" in c or "min" in c:
                errs.append(f"checks[{i}]: value/min do not apply to "
                            "item_in_destination checks")
            after = c.get("after")
            if after is not None and after != _START and after not in ids:
                errs.append(f"checks[{i}]: after={after!r} names no action id "
                            f"(or {_START!r}, the landing page)")
            continue
        if "expected_from" in c:
            errs.append(f"checks[{i}]: expected_from only applies to "
                        "item_in_destination checks — a count/see check "
                        "cannot claim item identity")
        if kind in ("status", "response_contains", "json", "schema") and \
                not any(isinstance(a, dict) and a.get("do") == "api"
                        for a in actions):
            # NOOD_0192 — an api assertion with no api action asserts against
            # whatever response happened to be last, which is nothing.
            errs.append(f"checks[{i}]: {kind} needs an api action in the "
                        "goal — there is no response to assert against")
        if kind == "schema" and (not isinstance(c["schema"], str)
                                 or not c["schema"].strip()
                                 or "'" in c["schema"]):
            # NOOD_0216 — a path into the app's resources/, single-quote free
            # (it delimits the compiled step).
            errs.append(f"checks[{i}]: schema must be a file path in the "
                        "app's resources/ (e.g. 'schemas/review.json')")
        if kind == "json":
            # NOOD_0201 — exactly one comparator per json check.
            ops = [k for k in ("equals", "contains", "items") if k in c]
            if len(ops) != 1:
                errs.append(f"checks[{i}]: json checks need exactly one of "
                            "equals | contains | items")
            elif ops[0] == "items":
                n = c["items"]
                if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                    errs.append(f"checks[{i}]: items must be a non-negative "
                                "integer (the expected element count)")
            elif not isinstance(c[ops[0]], str):
                errs.append(f"checks[{i}]: {ops[0]} must be a string (the "
                            "runner compares numbers/booleans/null by JSON "
                            "meaning)")
        elif any(k in c for k in ("equals", "contains", "items")):
            errs.append(f"checks[{i}]: equals/contains/items only apply to "
                        "json checks")
        if kind == "any_of":
            alts = c["any_of"]
            if not isinstance(alts, list) or not alts or \
                    not all(isinstance(x, str) and x.strip() for x in alts):
                errs.append(f"checks[{i}]: any_of must be a non-empty list of "
                            "strings")
        elif kind in ("status", "page_status"):
            # NOOD_0211 — page_status validates like status (both are HTTP
            # codes); the generic string branch below rejected the integer.
            code = c[kind]
            if not isinstance(code, int) or isinstance(code, bool) \
                    or not 100 <= code <= 599:
                errs.append(f"checks[{i}]: {kind} must be an HTTP status "
                            "code (an integer, 100-599)")
        elif not isinstance(c[kind], str) or not c[kind].strip():
            errs.append(f"checks[{i}]: {kind} must be a non-empty string")
        if kind == "field":
            if not isinstance(c.get("value"), str) or not c["value"].strip():
                errs.append(f"checks[{i}]: field checks require a value "
                            "(the text the field should contain)")
        elif "value" in c:
            errs.append(f"checks[{i}]: value only applies to field checks")
        if kind in ("count", "any_of"):
            m = c.get("min", 1)
            if not isinstance(m, int) or isinstance(m, bool) or m < 1:
                errs.append(f"checks[{i}]: min must be a positive integer")
        elif "min" in c:
            errs.append(f"checks[{i}]: min only applies to count/any_of")
        after = c.get("after")
        if after is not None and after != _START and after not in ids:
            errs.append(f"checks[{i}]: after={after!r} names no action id "
                        f"(or {_START!r}, the landing page)")
    nav = goal.get("navigation")
    if nav is not None:
        if not isinstance(nav, list) or not nav:
            errs.append("navigation must be a non-empty list of URLs "
                        "(strings or {url: ...} objects), in requested order")
        else:
            for i, n in enumerate(nav):
                u = n.get("url") if isinstance(n, dict) else n
                if not isinstance(u, str) or not u.strip():
                    errs.append(f"navigation[{i}]: must be a URL string or "
                                "{url: ...}")
                elif isinstance(n, dict) and set(n) - {"url"}:
                    errs.append(f"navigation[{i}]: unknown field(s) "
                                f"{sorted(set(n) - {'url'})}")
    for d in goal.get("dismissals") or []:
        if d not in _DISMISSALS:
            errs.append(f"unknown dismissal {d!r} "
                        f"(valid: {', '.join(sorted(_DISMISSALS))})")
    probe_opts = goal.get("probe") or {}
    if not isinstance(probe_opts, dict):
        errs.append("probe must be an object")
    else:
        for k in set(probe_opts) - _PROBE_KEYS:
            errs.append(f"probe: unknown field {k!r}")
    if "allow_no_assertion" in goal and \
            not isinstance(goal["allow_no_assertion"], bool):
        errs.append("allow_no_assertion must be true or false")
    if "evidence" in goal and goal["evidence"] not in _EVIDENCE_MODES:
        errs.append("evidence must be one of "
                    f"{sorted(_EVIDENCE_MODES)} (omit for the default: one "
                    "shot on the last assertion)")
    return errs


def needs_browser(goal: dict) -> bool:
    """NOOD_0192 — False only for a PURE-API goal: every action is an api
    call and every check is an api assertion. One predicate, used by the
    authoring transaction (skip the probe, no browser ever launches) and by
    the compiler (tag @api, emit no navigation Given) — two decisions that
    must never disagree about the same goal."""
    actions = [a for a in (goal.get("actions") or []) if isinstance(a, dict)]
    checks = [c for c in (goal.get("checks") or []) if isinstance(c, dict)]
    return (not actions
            or bool(goal.get("navigation"))
            or any(a.get("do") != "api" for a in actions)
            or any(not ({"status", "response_contains", "json", "schema"} & set(c))
                   for c in checks))


def browserless_evidence(goal: dict) -> dict:
    """The evidence dict for a goal that launches no browser — the same
    evidence() pass over an empty page, so api actions/checks land in
    `runtime_asserted` by exactly the code path a mixed goal uses."""
    return evidence(goal, {"pages": [{}]})




# --- automatic postcondition synthesis (NOOD_0156) ---------------------------

# What each last-meaningful-action kind can deterministically prove, per the
# false-positive mitigation decision table. Only probe-observed evidence ever
# becomes an assertion — confirmation text is never invented.

def _ensure_last_action_id(actions: list[dict]) -> str:
    """The last action's id, assigning a fresh synthetic one when the author
    gave none — generated checks must anchor AFTER the state-changing action."""
    ids = {a.get("id") for a in actions if a.get("id") is not None}
    last = actions[-1]
    if last.get("id") is None:
        n = 1
        while f"a{n}" in ids:
            n += 1
        last["id"] = f"a{n}"
    return last["id"]


def infer_postcondition(goal: dict, ev: dict) -> dict:
    """Derive an explicit postcondition for a goal that has actions but no
    checks (NOOD_0156). Pure — goal + the evidence() dict in, a plan out:

      {"actions": [...],        # copy; last action gains an id when needed
       "checks": [...],         # user checks verbatim, or the generated one
       "generated": [...],      # [{after, reason, check}] — [] when nothing
       "blocking": [...]}       # reasons synthesis refused; goal must block

    Rules: user-supplied checks are NEVER replaced or broadened; explicit
    allow_no_assertion opts out; the generated check verifies a state change
    of the LAST meaningful action from probe-observed evidence only (results
    summary, revealed headings, canonical control names) and is emitted into
    the .feature — never a hidden runtime check. No derivable postcondition →
    blocking with suggested candidates, because missing evidence must not
    become a guess."""
    actions = [dict(a) for a in (goal.get("actions") or [])]
    checks = [dict(c) for c in (goal.get("checks") or [])]
    out = {"actions": actions, "checks": checks, "generated": [],
           "blocking": []}
    if checks or goal.get("allow_no_assertion") or not actions:
        return out
    aid = _ensure_last_action_id(actions)
    last = actions[-1]
    do = last.get("do")
    proven = ev.get("proven") or {}

    if do == "search":
        rsum = ev.get("results_summary")
        obs = _observed_count(rsum) if rsum else None
        if obs is not None and obs >= 1:
            out["checks"] = [{"count": "results summary", "min": 1,
                              "after": aid}]
            out["generated"] = [{
                "after": aid,
                "reason": "search action had no user-supplied postcondition",
                "check": "the number in 'results summary' should be at "
                         "least 1"}]
        else:
            out["blocking"].append(
                f'search "{last.get("term", "")}" has no user-supplied '
                "postcondition and the probe captured no positive results "
                "summary to generate one from — add an explicit check (a "
                "known result heading, or a count on a probed summary "
                "element), or fix the search evidence first")
        return out

    if do == "suggest":
        canon = proven.get(f'suggest:{last.get("term", "")}') \
            or last.get("option", "")
        if canon:
            out["checks"] = [{"see": canon, "after": aid}]
            out["generated"] = [{
                "after": aid,
                "reason": "typeahead pick had no user-supplied postcondition",
                "check": f'the landed page shows "{canon}"'}]
        else:
            out["blocking"].append(
                "suggest action has no user-supplied postcondition and no "
                "captured suggestion to anchor one to")
        return out

    if do == "pick":
        # NOOD_0156 — the bound caption IS the deterministic postcondition:
        # the landed page must show the exact result the pick selected.
        cap = (ev.get("bound_targets") or {}).get(
            last.get("id") or "result", {}).get("caption")
        if cap:
            out["checks"] = [{"see": cap, "after": aid}]
            out["generated"] = [{
                "after": aid,
                "reason": "result pick had no user-supplied postcondition",
                "check": f'the landed page shows the bound result "{cap}"'}]
        else:
            out["blocking"].append(
                "pick has no user-supplied postcondition and no bound result "
                "caption to anchor one to — the probe pick did not bind")
        return out

    if do == "add_to":
        # NOOD_0156 — the natural postcondition of a semantic mutation is
        # ITEM IDENTITY in the destination: the bound caption must be visible
        # there. Never a count — a count cannot prove which item was added.
        src, dest = last.get("item_from", ""), last.get("destination", "")
        scope = str(last.get("within") or "").strip()
        if scope and not src:
            # NOOD_0207 — the searchless shape has no bound caption, but it
            # DOES have the anchor the author supplied: that text identifies
            # the item, so its presence is the postcondition. Identity, never
            # a count — same rule as the pick path.
            out["checks"] = [{"any_of": [scope], "after": aid}]
            out["generated"] = [{
                "after": aid,
                "reason": "add_to had no user-supplied postcondition",
                "check": f'the page shows "{scope}" (the scoped item — '
                         "identity, never a count)"}]
            return out
        if (ev.get("bound_targets") or {}).get(src, {}).get("caption"):
            out["checks"] = [{"item_in_destination": dest,
                              "expected_from": src, "after": aid}]
            out["generated"] = [{
                "after": aid,
                "reason": "add_to had no user-supplied postcondition",
                "check": f'the bound result is visible in "{dest}" '
                         "(identity, never a count)"}]
        else:
            out["blocking"].append(
                "add_to has no user-supplied postcondition and no bound "
                "result caption to anchor one to — the pick did not bind")
        return out

    if do in ("enter", "select"):
        value = last.get("value") if do == "enter" else last.get("option")
        target = proven.get(f'{do}:{last.get("target", "")}') \
            or last.get("target", "")
        out["checks"] = [{"field": target, "value": value, "after": aid}]
        out["generated"] = [{
            "after": aid,
            "reason": f"{do} action had no user-supplied postcondition",
            "check": f'the "{target}" field should contain "{value}"'}]
        return out

    if do == "pick_date":
        # NOOD_0188 — same shape as enter/select: the control must show what
        # the calendar set. Deterministic, no invention.
        target = proven.get(f'pick_date:{last.get("target", "")}') \
            or last.get("target", "")
        out["checks"] = [{"field": target, "value": last.get("date"),
                          "after": aid}]
        out["generated"] = [{
            "after": aid,
            "reason": "pick_date action had no user-supplied postcondition",
            "check": f'the "{target}" field should contain '
                     f'"{last.get("date")}"'}]
        return out

    if do == "api":
        # NOOD_0201 — a batch with expect_status asserts EVERY call already;
        # nothing to generate, nothing to block. NOOD_0216 — wait_until IS
        # the assertion (the polling step fails if the condition never holds).
        if (last.get("rows") or last.get("repeat")) and last.get("expect_status"):
            return out
        if last.get("wait_until"):
            return out
        # NOOD_0192 — an unasserted API call proves nothing: a 500 with a
        # body is still "a call that happened". The postcondition is the
        # author's to state, and it is one word.
        out["blocking"].append(
            "the api call has no assertion — add a check saying what the "
            "response must be (status: 200, response_contains: '<text>', or "
            "a typed json: '<path>' check); a rows/repeat batch asserts "
            "per-call with expect_status")
        return out

    if do in ("check", "uncheck", "upload", "hover", "press_key", "go_back"):
        # NOOD_0188 — these have no deterministic self-evident postcondition
        # (a checkbox's own state isn't proof the app DID anything, and a
        # hover/back reveals whatever the app decides). Inventing one would be
        # exactly the guessed-confirmation failure NOOD_0156 closed for
        # clicks, so block with the same shape and let the author say what
        # should be true.
        out["blocking"].append(
            f"{do} has no user-supplied postcondition and none can be derived "
            f"deterministically — add a check saying what should be true "
            f"after it (see / not_see / url_contains / count)")
        return out

    # click — state-changing or navigating. Deterministic only when the probe
    # itself observed what this click reveals (an explicit reveal transaction
    # with a captured heading). Anything else would be an invented
    # confirmation — block with suggestions instead.
    target = last.get("target", "")
    canon = proven.get(f"click:{target}")
    heads = (ev.get("revealed_headings") or {}).get(_norm(canon or target)) \
        or (ev.get("revealed_headings") or {}).get(_norm(target))
    if heads:
        out["checks"] = [{"see": heads[0], "after": aid}]
        out["generated"] = [{
            "after": aid,
            "reason": "state-changing click had no user-supplied "
                      "postcondition",
            "check": f'the revealed content shows "{heads[0]}" '
                     "(probe-observed)"}]
        return out
    candidates = [h for h in (ev.get("headings") or [])[:3]]
    out["blocking"].append(
        f'click "{target}" is state-changing but has no user-supplied '
        "postcondition, and the probe evidence proves no observable outcome "
        "to generate one from — add a checks entry for the expected durable "
        "state (created record, count delta, destination content"
        + (f'; probed headings include {candidates!r}' if candidates else "")
        + "), or set allow_no_assertion: true for a deliberate "
        "workflow-only scenario")
    return out


# --- intent provenance (NOOD_0156) -------------------------------------------

def intent_summary(goal: dict, ev: dict) -> dict:
    """The three intent buckets the compiled test is built from — pure, for
    the author_test payload:

      requested_actions       — the user's actions, verbatim from the goal;
      bound_targets           — generic requests bound to concrete probe
                                evidence (a binding, never a new intent);
      required_prerequisites  — every extra step the compiler may emit beyond
                                the request, each with required_by + evidence
                                provenance. Nothing else is ever generated —
                                an extra action without provenance cannot
                                compile (there is no code path for it)."""
    reqs = []
    for a in goal.get("actions") or []:
        if not isinstance(a, dict):
            continue
        reqs.append({k: a[k] for k in
                     ("do", "id", "target", "term", "value", "option",
                      # NOOD_0188 — the new verbs' payload keys, or the intent
                      # contract would silently drop what was actually asked.
                      "file", "key", "date",
                      # NOOD_0192 — the api call's own payload.
                      "url", "method")
                     if a.get(k) is not None})
    prereqs = []
    for p in ev.get("permission_prompts") or []:
        prereqs.append({"action": f"close the {p} prompt",
                        "required_by": "navigation",
                        "evidence": "probe observed the permission prompt"})
    if ev.get("popups_closed"):
        prereqs.append({"action": "close popup if it appears",
                        "required_by": "navigation",
                        "evidence": f"probe closed {ev['popups_closed']} "
                                    "popup(s) reaching the page"})
    for d in goal.get("dismissals") or []:
        prereqs.append({"action": f"dismiss {d}", "required_by": "user request",
                        "evidence": "requested in goal.dismissals"})
    # NOOD_0156 — a mutation prerequisite may appear ONLY when the probe
    # proved the reveal (clicking it made the requested mutation control
    # appear). Its provenance rides here so the trace names the exact click.
    for plan in (ev.get("mutation_plans") or {}).values():
        pre = plan.get("prerequisite")
        if pre:
            prereqs.append({
                "action": pre.get("name", ""),
                "required_by": "mutation:add_to",
                "evidence": plan.get(
                    "evidence",
                    "click revealed the requested mutation control")})
    for c in goal.get("checks") or []:
        dest = c.get("item_in_destination") if isinstance(c, dict) else None
        if dest:
            canon = (ev.get("proven") or {}).get(f"destination:{dest}")
            if canon:
                prereqs.append({
                    "action": f'open "{canon}"',
                    "required_by": "observation:item_in_destination",
                    "evidence": "destination control probed — required only "
                                "to verify the requested result there"})
    return {"requested_actions": reqs,
            "bound_targets": ev.get("bound_targets") or {},
            "required_prerequisites": prereqs}


def intent_trace(goal: dict, ev: dict) -> list[dict]:
    """request requirement → goal node → probe evidence, one compact entry
    per intent-contract requirement. IDs and short references only — raw
    evidence stays in artifacts. `ok: false` on any entry means the original
    contract is NOT fully represented by compilable, provenance-backed
    steps, whatever the rest of the payload claims."""
    blocking = ev.get("blocking") or []
    proven = ev.get("proven") or {}
    bound = ev.get("bound_targets") or {}
    mplans = ev.get("mutation_plans") or {}
    trace = []
    nav_ok = not any(b.startswith("navigation") for b in blocking)
    for i, url in enumerate(navigation_urls(goal)):
        trace.append({"requirement": f"open {url}",
                      "node": f"navigation[{i}]",
                      "evidence": "probe:navigation" if nav_ok else "missing",
                      "ok": nav_ok})
    for d in goal.get("dismissals") or []:
        trace.append({"requirement": f"dismiss {d}", "node": "dismissals",
                      "evidence": "goal.dismissals", "ok": True})
    for i, a in enumerate(goal.get("actions") or []):
        if not isinstance(a, dict):
            continue
        do = a.get("do")
        aid = a.get("id")
        if do == "search":
            ok, evid = "search" in proven, "probe:search"
        elif do == "suggest":
            ok, evid = f'suggest:{a.get("term", "")}' in proven, "probe:typeahead"
        elif do == "pick":
            ok, evid = (aid or "result") in bound, "probe:search-results"
        elif do == "add_to":
            key = aid or f"add_to:{a.get('destination', '')}"
            ok, evid = key in mplans, "probe:mutation-path"
        elif do == "api":
            # NOOD_0192 — the call is proven by the run, not the page probe;
            # its assertion is the check entry below. Never "missing" for
            # want of a control it doesn't have.
            ok, evid = True, "runtime:rest-call"
        elif not a.get("target"):
            # NOOD_0188 — press_key/go_back name no control: they act on the
            # focused element or on history, so there is nothing for a probe
            # to resolve and the compiled step carries no locator. Keying them
            # on a target they don't have reported "missing" and dragged
            # intent_verified false for every goal that used one.
            ok, evid = True, "deterministic:no-control"
        else:
            key = f'{do}:{a.get("target", "")}'
            if key in proven:
                ok, evid = True, "probe:control"
            elif (ev.get("proven_phase") or {}).get(key) == "runtime":
                # NOOD_0226 — deferred past the probe's reach (NOOD_0207/0226):
                # proven by the RUN, exactly like the api arm above and
                # NOOD_0188's no-control verbs. Reporting it "missing" dragged
                # intent_verified false for every multi-page flow — including
                # the ones the deferral exists to make authorable — and the
                # evidence label is what says which proof this step rests on.
                ok, evid = True, "runtime:control"
            else:
                ok, evid = False, "missing"
        what = a.get("target") or a.get("term") or a.get("destination") \
            or a.get("key") or a.get("url") or ""
        trace.append({"requirement": f"{do} {what}".strip(),
                      "node": f"actions[{i}]",
                      "evidence": evid if ok else "missing", "ok": bool(ok)})
    runtime = ev.get("runtime_asserted") or []
    for i, c in enumerate(goal.get("checks") or []):
        if not isinstance(c, dict):
            continue
        kind = next((k for k in _CHECK_KINDS if k in c), "?")
        if kind == "see":
            ok = f"see:{c['see']}" in proven or any(
                c["see"] in s for s in runtime)
        elif kind in ("not_see", "url_contains", "page_status"):
            # NOOD_0188 — both are runtime-only by nature: the probe can't
            # prove an absence, and it never follows the flow to the landing
            # URL. Verified by the run, so they must appear in `runtime`.
            ok = any(str(c[kind]) in s for s in runtime)
        elif kind == "count":
            ok = f"count:{c['count']}" in proven or any(
                c["count"] in s for s in runtime)
        elif kind == "item_in_destination":
            cap = bound.get(c.get("expected_from", ""), {}).get("caption", "")
            ok = bool(cap) and any(cap in s for s in runtime)
        elif kind in ("status", "response_contains", "json", "schema"):
            # NOOD_0192 — proven by the REST client at run time; it must
            # appear verbatim in the runtime-asserted list (json: NOOD_0201,
            # keyed on its path; schema: NOOD_0216, keyed on its file).
            ok = any(str(c[kind]) in s for s in runtime)
        else:
            ok = f"any_of[{i}]" in proven or bool(runtime)
        # NOOD_0197 — the check's own term(s) ride the trace so RCA can map a
        # failing compiled step back to this node (an any_of carries every
        # member — the "multi-term check, suspect the compilation" signal).
        terms = ([str(x) for x in c["any_of"]] if kind == "any_of"
                 else [str(c.get(kind, ""))])
        entry = {"requirement": f"check {kind}", "node": f"checks[{i}]",
                 "terms": terms,
                 "evidence": (("runtime:rest-client"
                               if kind in ("status", "response_contains",
                                           "json", "schema")
                               else "probe+runtime") if ok else "missing"),
                 "ok": bool(ok)}
        if c.get("evidence") == "screenshot":
            entry["screenshot"] = True
        trace.append(entry)
    return trace


# NOOD_0156 — ONE typed next_action per blocked payload, so the driving agent
# repairs the named gap instead of choosing an exploration strategy from
# prose (the 72.8-AIC session's repeated probe-and-grep loop).
_NEXT_ACTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("external_app_failure", ("probe returned no page evidence",)),
    ("fix_navigation_contract", ("navigation:",)),
    # NOOD_0169 — result extraction is checked BEFORE the mutation path: a
    # pick that never bound also blocks the downstream add_to, and the typed
    # repair must name the ROOT gap (the 1163-results/zero-items state), not
    # the cascade.
    ("result_items_missing", ("pick:", "search-result caption",
                              "result item", "no results block",
                              'search "', "search:")),
    ("mutation_path_missing", ("add_to", "mutation")),
    ("destination_missing", ("item_in_destination",)),
)


def next_action(blocking: list[str]) -> str | None:
    """The single machine-actionable repair code for a blocked goal — None
    when nothing blocks."""
    for code, needles in _NEXT_ACTION_RULES:
        if any(n in b for b in blocking or [] for n in needles):
            return code
    return "fix_goal_request" if blocking else None


_UNPROVABLE_SEE = "no probed heading or control shows"
# NOOD_0218 — the wording _near_miss stamps into a blocking line when the
# probe DID find a close twin of the asked-for text. repair_goal reads it
# back: matches both "probed texts here" and "probed controls here".
_NEAR_MISS_HIT = re.compile(r' — did you mean "(.+?)"\? \(probed ')

# NOOD_0218 — the blocker family a same-origin navigation can cure: "this
# name/text/mutation-control is not on the probed page". Ambiguity,
# reachability and bind failures are excluded — another page won't fix those.
_UNROUTED_MARKS = ("no probed control matches that name",
                   "no probed text on this page identifies that",
                   "no probed control mutates into")


def unrouted_targets(blocking: list[str]) -> list[str]:
    """The blocking entries that say the target simply isn't on the probed
    page — the ones core._route_repair may resolve by probing the page's own
    same-origin links (NOOD_0218)."""
    return [b for b in (blocking or [])
            if any(m in b for m in _UNROUTED_MARKS)]


# NOOD_0223 — the two ACTION blocker families a repair can cure without
# re-probing anything. Both are cases where the evidence pass already knows
# the answer and only ever handed back the question.
#
#   reveal  — the control exists but is behind a trigger the goal never
#             clicks. The blocker NAMES the trigger, so the fix is a
#             prerequisite click, not a re-author by a human.
#   target  — the name is simply not the page's name for the control
#             ("add to order" vs "Add to cart"). _near_miss already ranked
#             the probed vocabulary; taking its top pick is the same move
#             NOOD_0218 made for see-checks, one field earlier.
_REVEAL_HIDDEN = re.compile(r'^(\w+) "(.+?)": hidden until "(.+?)" is opened')
_REVEAL_DISCOVERED = re.compile(
    r'^(\w+) "(.+?)": only found via automatic discovery '
    r'\(revealed by "(.+?)"\)')
_ACTION_MISS = re.compile(
    r'^(\w+) "(.+?)": no probed control matches that name')


def _action_index(actions: list, do: str, target: str, used: set) -> int | None:
    """The first not-yet-repaired action matching a blocker's `do "target"`."""
    for i, a in enumerate(actions):
        if i in used or not isinstance(a, dict):
            continue
        if str(a.get("do")) == do and _norm(a.get("target")) == _norm(target):
            return i
    return None


def _repair_actions(goal: dict, blocking: list[str]) -> tuple[list, dict, int]:
    """(actions, changes, matched) — `goal`'s actions with every curable
    reveal/near-miss blocker applied. `matched` counts the blocking lines
    consumed, so the caller can insist that NOTHING was left unexplained
    before it offers the repair."""
    actions = list((goal or {}).get("actions") or [])
    pre_click: dict[int, str] = {}
    retarget: dict[int, str] = {}
    used, matched = set(), 0
    for b in blocking or []:
        m = _REVEAL_HIDDEN.match(b) or _REVEAL_DISCOVERED.match(b)
        if m:
            i = _action_index(actions, m.group(1), m.group(2), set())
            if i is None or i in pre_click:
                continue
            # The trigger a click already opens is not a missing prerequisite;
            # the evidence pass would not have blocked. Guard anyway — a
            # duplicated click is a wasted step at run time.
            if _reveal_click_before(actions, actions[i], m.group(3)):
                continue
            pre_click[i] = m.group(3)
            matched += 1
            continue
        m = _ACTION_MISS.match(b)
        if m and (near := _NEAR_MISS_HIT.search(b)):
            i = _action_index(actions, m.group(1), m.group(2), used)
            if i is None:
                continue
            used.add(i)
            retarget[i] = near.group(1)
            matched += 1
    if not (pre_click or retarget):
        return actions, {}, 0
    out = []
    for i, a in enumerate(actions):
        if i in pre_click:
            out.append({"do": "click", "target": pre_click[i]})
        out.append({**a, "target": retarget[i]} if i in retarget else a)
    changes = {}
    if pre_click:
        changes["prerequisite_clicks"] = [
            {"click": t, "before": actions[i].get("target")}
            for i, t in sorted(pre_click.items())]
    if retarget:
        changes["rewritten_targets"] = [
            {"from": actions[i].get("target"), "to": t}
            for i, t in sorted(retarget.items())]
    return out, changes, matched


def repair_goal(goal: dict, blocking: list[str]) -> dict | None:
    """A ready-to-send copy of `goal` with every blocker the probe's own
    evidence can cure already applied — offered ONLY when those are the
    goal's ONLY blockers and at least one check survives.

    Four shapes, in ascending order of how much they cost the contract:

    * `rewritten_targets` (NOOD_0223) — an action target reworded to the
      probed control name. Costs nothing: same action, page's vocabulary.
    * `prerequisite_clicks` (NOOD_0223) — a click INSERTED before an action
      whose control the probe found hidden behind a named trigger. Costs
      nothing either: it adds the step a human would have taken, and the
      probe is what proved the trigger opens it.
    * `rewritten_checks` (NOOD_0218) — a see-check reworded to the probe's
      near-miss. The assertion survives, in the page's wording.
    * `dropped_checks` — no near-miss existed. This is the only shape that
      weakens the contract, and callers force `intent_verified: false` on it.

    NOOD_0213: the engine still never drops an asked-for verify on its own
    initiative (NOOD_0212 — the wording rule that tried ate real assertions);
    dropping stays an explicit caller choice, now one field away instead of a
    hand-rebuilt goal costing a full lap.

    ponytail: the near-miss top pick is difflib's, cutoff 0.6 — high enough
    that "add to order" reaches "Add to cart" and low enough that two
    unrelated controls do not. A reword is always announced, never silent, so
    a wrong pick shows up in the payload rather than in a mystery step."""
    checks = (goal or {}).get("checks") or []
    dropped, rewritten, keep, matched = [], [], [], 0
    for c in checks:
        line = next((b for b in blocking or [] if "see" in c
                     and b.startswith(f'check "{c["see"]}": ')
                     and _UNPROVABLE_SEE in b), None)
        if line is None:
            keep.append(c)
            continue
        matched += 1
        if m := _NEAR_MISS_HIT.search(line):
            rewritten.append({"from": c["see"], "to": m.group(1)})
            keep.append({**c, "see": m.group(1)})
        else:
            dropped.append(c["see"])
    actions, act_changes, act_matched = _repair_actions(goal, blocking)
    matched += act_matched
    if not (dropped or rewritten or act_changes) or not keep \
            or matched != len(blocking or []):
        return None
    notes = []
    if act_changes.get("rewritten_targets"):
        notes.append("action target(s) reworded to the probed control name")
    if act_changes.get("prerequisite_clicks"):
        notes.append("the prerequisite click(s) the probe proved open the "
                     "hidden control, inserted before it")
    if rewritten:
        notes.append("unprovable text check(s) reworded to the probe's "
                     "near-miss")
    if dropped:
        notes.append("text check(s) with no near-miss dropped — or fix their "
                     "wording to probed evidence and keep them")
    # `actions` is set only when something in it changed: a goal that never
    # had an actions key must not acquire an empty one here (validate reads
    # presence, not just contents).
    return {"goal": {**goal, "checks": keep,
                     **({"actions": actions} if act_changes else {})},
            "dropped_checks": dropped,
            **({"rewritten_checks": rewritten} if rewritten else {}),
            **act_changes,
            "note": "re-author with repair.goal (overwrite=true): "
                    + "; ".join(notes)}


def to_spec_yaml(goal: dict, unresolved: list[dict] | None = None) -> str:
    """NOOD_0223 — the accepted goal as a paste-ready `--spec` document, with
    every clause the engine could NOT place carried as a comment above it.

    A blocked prompt used to hand back three things a caller had to reconcile
    by hand: a free-text error, a list of `assumptions`, and a `goal_partial`
    dict in JSON. Reconciling them meant re-typing the whole request in
    another notation and hoping the retype matched what the engine had already
    understood — the exact drift this ticket exists to remove. This emits ONE
    document instead: valid YAML on its own (the comments are comments), so
    the repair is "fill in the commented lines and send it back", not "write
    the goal again".

    The output is a spec, not a bare goal, because a spec is what `--spec`
    takes; navigation[0] derives app_name/base_url/feature_path (NOOD_0213),
    so nothing else has to be supplied.

    ponytail: unresolved clauses land as a header block rather than inline at
    their step position — the partial goal does not carry a clause→action
    index, and inventing one would put a rewrite hint next to the wrong step,
    which is worse than putting it at the top."""
    import yaml as _yaml
    head = []
    for u in unresolved or []:
        step = str(u.get("clause") or "").replace("-", " ").strip() or "clause"
        head.append(f'# UNRESOLVED {step}: {u.get("text") or ""!r}'.rstrip())
        if reason := u.get("reason"):
            head.append(f"#   reason: {reason}")
        if sug := u.get("suggested"):
            head.append(f"#   suggested: {sug}")
    if head:
        head = [f"# {len(unresolved)} prompt step(s) are NOT in this goal — "
                "add them below, then re-send:",
                "#   noodle author --spec '<this document>' --run",
                *head, "#"]
    body = _yaml.safe_dump({"goal": goal or {}}, default_flow_style=False,
                           sort_keys=False, allow_unicode=True)
    return "\n".join([*head, body.rstrip()]) + "\n"


# --- deterministic compiler --------------------------------------------------

_PERM_STEP = {"geolocation": "the user closes the location prompt",
              "notifications": "the user closes the notifications prompt"}
_DISMISS_PERM = {"location_prompt": "geolocation",
                 "notifications_prompt": "notifications"}
_POPUP_STEP = "closes the popup if it appears within 10 seconds"


def _yaml_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _check_step(c: dict, captions: dict | None = None) -> tuple[str, str | None]:
    """(step body, pom name needed or None) for one check. `captions` maps
    pick-action ids to their bound result captions (item checks). A check with
    evidence: screenshot gets the NOOD_0153 marker ON the verification step —
    the capture attaches to the assertion it proves, never a separate step."""
    if "see" in c:
        body, pom = f'the user sees "{c["see"]}"', None
    elif "not_see" in c:
        # NOOD_0188 — absence. The empty-state/removal half of a journey
        # ("the item is gone from the cart", "no error is shown") had no
        # expressible form, so those goals dropped to hand-written Gherkin.
        body, pom = f'the user should not see "{c["not_see"]}"', None
    elif "status" in c:
        # NOOD_0192 — the api wok's assertions, in the REST client's own
        # phrasing (resolver/patterns.py rest_assert_status / rest_assert_body).
        body, pom = f"the response status should be {c['status']}", None
    elif "response_contains" in c:
        body, pom = (f"the response body should contain "
                     f"'{c['response_contains']}'", None)
    elif "json" in c:
        # NOOD_0201 — typed JSON assertion (rest_assert_json[_count]).
        if "items" in c:
            body = (f"the response json '{c['json']}' should have "
                    f"{c['items']} items")
        elif "contains" in c:
            body = (f"the response json '{c['json']}' should contain "
                    f"'{c['contains']}'")
        else:
            body = (f"the response json '{c['json']}' should equal "
                    f"'{c['equals']}'")
        pom = None
    elif "schema" in c:
        # NOOD_0216 — the whole response validated against a JSON Schema file
        # in the app's resources/ (rest_assert_schema).
        body, pom = f"the response should match the schema '{c['schema']}'", None
    elif "page_status" in c:
        # NOOD_0211 — "assert the UI page returns 200", a routine AC that used
        # to compile to a literal-text assertion on the sentence itself
        # ("the user sees \"UI page returns 200\"") and block, because no page
        # renders that string. Runtime-only like url_contains: a probe
        # snapshot cannot stand in for the navigation's own response.
        body, pom = f"the page should return {c['page_status']}", None
    elif "url_contains" in c:
        # NOOD_0188 — "the flow landed where it should". Navigation was
        # driveable from a goal but never assertable from one.
        body, pom = f'the url should contain "{c["url_contains"]}"', None
    elif "count" in c:
        body, pom = (f"the number in '{c['count']}' should be at least "
                     f"{c.get('min', 1)}", c["count"])
    elif "field" in c:
        # NOOD_0156 — the entered/selected value must actually be in the
        # target control (vocabulary: assert_field_value).
        body, pom = (f'the "{c["field"]}" field should contain '
                     f'"{c["value"]}"', None)
    elif "item_in_destination" in c:
        # NOOD_0156 — identity assertion on the BOUND caption: the compiler
        # reuses the exact caption the pick selected, so a count ("Cart (1)")
        # can never stand in for "the selected toy is in the cart".
        cap = (captions or {}).get(c.get("expected_from", ""),
                                   c.get("expected_from", ""))
        body, pom = f'the user sees "{cap}"', None
    elif "any_of" in c:
        # NOOD_0197 — a disjunction stays a disjunction. This used to compile
        # to either N conjunctive `sees` steps (NOOD_0195 literal path — a
        # logic inversion: "A or B" went red on a page correctly showing only
        # A) or a union-selector count. One step, resolved by
        # assert_any_visible, which records the satisfying member.
        alts = ", ".join(f'"{a}"' for a in c["any_of"])
        want = c.get("min", 1)
        body = (f"the user sees any of {alts}" if want == 1
                else f"the user sees at least {want} of {alts}")
        pom = None
    else:
        # NOOD_0188 — was a silent `else: any_of`, so a new kind added to the
        # tables but not here compiled a bogus count assertion that still
        # matched the pattern table.
        raise ValueError(
            f"_check_step has no branch for check {sorted(c)} — add one for "
            "the new kind (a fallthrough would compile a wrong assertion)")
    # NOOD_0227 (D1) — the engine no longer serialises checks[].evidence into
    # the step TEXT. "( take a screenshot )" was run configuration smuggled
    # into prose: emitted here, stripped back out by a ~15-line regex family
    # (resolver/patterns EVIDENCE_MARKER_RE), and carried as noise in every
    # step label the reports show. compile_goal now records the directive as
    # step metadata on the scenario tag (@evidence:steps=… / @evidence:skip=…,
    # read by reporting/evidence.step_directives); the regex family stays
    # READ-ONLY for hand-authored features (NOOD_0153's contract holds).
    return body, pom


def _action_step(a: dict, target: str) -> str:
    """`target` is the PROBED canonical control name when one matched — the
    spelling that actually resolves at run time — else the goal's own.

    NOOD_0188 — every `do` now has an explicit branch and the tail RAISES.
    It used to fall through to `select`, so an action added to the tables but
    not here compiled a plausible-but-wrong select step that still matched the
    pattern table — i.e. the compiler-agreement test could not catch it."""
    do = a["do"]
    # NOOD_0207 — `within` compiles to the row/item-scoped steps the pattern
    # table has carried since NOOD_0011 (click_in_row / fill_in_row). Only
    # click and enter have a scoped runtime step; the schema allows `within`
    # on exactly those two for that reason.
    scope = str(a.get("within") or "").strip()
    if do == "search":
        return f'User searches for "{a["term"]}"'
    if do == "click":
        return (f'User clicks "{target}" in the row containing "{scope}"'
                if scope else f'User clicks "{target}"')
    if do == "enter":
        return (f'User enters "{a["value"]}" in the "{target}" field in the '
                f'row containing "{scope}"' if scope
                else f'User enters "{a["value"]}" in the "{target}" field')
    if do == "select":
        return f'User selects "{a["option"]}" from "{target}"'
    if do == "check":
        return f'User checks the "{target}" checkbox'
    if do == "uncheck":
        return f'User unchecks the "{target}" checkbox'
    if do == "hover":
        return f'User hovers over the "{target}"'
    if do == "upload":
        return f'User uploads "{a["file"]}" to the "{target}"'
    if do == "press_key":
        return f'User presses the {a["key"]} key'
    if do == "pick_date":
        return f'User selects "{a["date"]}" from the "{target}" calendar'
    if do == "go_back":
        return "User goes back"
    if do == "api":
        # NOOD_0216 — "within N seconds" rides any REST step (NOODLE_REST_TIMEOUT
        # otherwise), and wait_until IS the whole action: rest_wait_until polls
        # the url itself, so no separate call step is emitted.
        within = (f" within {a['timeout']:g} seconds" if a.get("timeout")
                  else "")
        wu = a.get("wait_until")
        if wu:
            contains = (f" and the body contains '{wu['contains']}'"
                        if wu.get("contains") else "")
            return (f"waits until a {str(a.get('method', 'GET')).upper()} "
                    f"call at '{a['url']}' returns status {wu['status']}"
                    + contains + within)
        # NOOD_0192 — rest_call. The url may be absolute or a path relative to
        # {var:REST_BASE_URL}; both are the same step.
        step = (f"performs a {str(a.get('method', 'GET')).upper()} call at "
                f"'{a['url']}'"
                + (f" with body '{a['body']}'" if a.get("body") else ""))
        # NOOD_0201 — batch shapes compile to ONE step (rest_call_each /
        # rest_call_repeat), never N pasted lines; `expecting status` makes
        # the runner check every call in the batch.
        expect = (f" expecting status {a['expect_status']}"
                  if a.get("expect_status") else "")
        if a.get("rows"):
            return step + " for each row" + expect + within + ":"
        if a.get("repeat"):
            return step + f" repeated {a['repeat']} times" + expect + within
        return step + within
    raise ValueError(
        f"_action_step has no branch for do={do!r} — add one (a silent "
        "fallthrough would compile a wrong-but-matching step)")


def _flat_pom_entries(pom_text: str | None) -> dict:
    """NOOD_0212 — the caller's pom_content as {key: selector}, or {}.

    Only top-level entries: a caller who already wrote their own `pages:`
    block has done the scoping themselves, and re-nesting it under this
    feature's pin would change what they asked for.
    """
    if not pom_text or not pom_text.strip():
        return {}
    try:
        data = yaml.safe_load(pom_text)
    except Exception:
        return {}                      # core.py reports the YAML error itself
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if k not in ("pages", "match") and isinstance(v, (str, dict))}


def compile_goal(goal: dict, ev: dict, base_url_key: str,
                 nav_keys: list[str] | None = None,
                 extra_pom: str | None = None) -> tuple[str, str | None]:
    """(feature_text, pom_text | None) — deterministically compiled, never
    model-authored. Observed prerequisites (permission prompts, popups) merge
    with requested dismissals and deduplicate; the POM is a pages: block
    pinned by the feature's @page tag (NOOD_0200) — active on every URL the
    scenario visits, invisible to sibling features.
    NOOD_0156 — `nav_keys` (from navigation_env) emits ONE ordered navigation
    Given per requested URL; without a navigation contract the single
    base-URL Given is unchanged."""
    steps: list[tuple[str, str]] = []
    # NOOD_0201 — Gherkin data tables for api `rows` actions, keyed by the
    # index of the step they ride under. A separate channel on purpose: step
    # bodies stay one line each (the NOOD_0177 injection gate below), and
    # every cell was validated scalar and pipe/newline-free.
    tables: dict[int, list[str]] = {}

    def _table_lines(a: dict) -> list[str]:
        keys = list(a["rows"][0])
        return ["| " + " | ".join(str(x) for x in keys) + " |",
                *("| " + " | ".join(str(r[k]) for k in keys) + " |"
                  for r in a["rows"])]

    def _append_api(a: dict):
        # NOOD_0201 — session setup rides its own steps ahead of the call
        # (rest_set_header / rest_set_auth are session-scoped in the runner),
        # and `store` extractions follow it. One goal action, up to N steps —
        # which is exactly why these were unreachable from a goal before.
        for name, value in (a.get("headers") or {}).items():
            steps.append(("Given", f"sets a request header '{name}' to "
                                   f"'{value}'"))
        auth = a.get("auth") or {}
        if auth.get("scheme") == "bearer":
            steps.append(("Given", "sets the bearer token to "
                                   f"'{auth['token']}'"))
        elif auth.get("scheme") == "basic":
            steps.append(("Given", f"uses basic auth with '{auth['user']}' "
                                   f"and '{auth['password']}'"))
        steps.append(("When", _action_step(a, "")))
        if a.get("rows"):
            tables[len(steps) - 1] = _table_lines(a)
        for var, path in (a.get("store") or {}).items():
            steps.append(("When", f"extracts '{path}' from the response "
                                  f"storing in {{var:{var}}}"))

    dismissals = goal.get("dismissals") or []
    pom_entries: dict[str, list[str]] = {}
    checks = goal.get("checks") or []
    actions = goal.get("actions") or []
    # NOOD_0227 (D1) — per-step evidence directives, collected as 1-based
    # step positions while the steps are emitted and carried on the scenario
    # tag line — run configuration as metadata, never step-text prose.
    ev_want: list[int] = []
    ev_skip: list[int] = []
    # NOOD_0192 — is a browser involved at all? An api-only goal compiles to
    # @api: no navigation Given, no dismissals, no POM — and hooks then starts
    # no browser for it, which is exactly what makes a pure-API suite runnable
    # on a browser-free CI image.
    web = needs_browser(goal) or bool(nav_keys)
    # The API calls asked for BEFORE the first web action are a preamble:
    # "fetch or seed over REST, then prove it in the UI" is the commonest
    # cross-wok shape, and emitting the navigation first would invert the
    # user's own order. Everything after the first web action stays in place.
    pre = 0
    while pre < len(actions) and actions[pre]["do"] == "api":
        pre += 1
    bound = ev.get("bound_targets") or {}
    captions = {k: v.get("caption", "") for k, v in bound.items()}

    def _emit_check(c: dict):
        dest = c.get("item_in_destination") if "item_in_destination" in c \
            else None
        if dest:
            # Observation navigation, not user intent: the destination must be
            # opened to verify the result there — provenance lives in
            # intent_summary(), and the click uses the probed canonical name.
            # NOOD_0156 follow-up — settle FIRST: the mutation that put the
            # item there rides an async request, and navigating away the
            # instant the click returns aborts it in flight (net::ERR_ABORTED
            # on the cart POST — reproduced headed AND headless). Network-
            # quiet is ~free when nothing is in flight.
            canon = (ev.get("proven") or {}).get(f"destination:{dest}") or dest
            steps.append(("When", "User waits for the network to be idle"))
            steps.append(("When", f'User clicks "{canon}"'))
            dctrl = ev.get("controls", {}).get(_norm(canon))
            if dctrl and dctrl.get("selector"):
                pom_entries.setdefault(
                    dctrl["name"], dctrl.get("pom")
                    or [f'{dctrl["name"]}:',
                        f'  css: {_yaml_str(dctrl["selector"])}'])
        body, pom_name = _check_step(c, captions)
        steps.append(("Then", body))
        if c.get("evidence") == "screenshot":
            ev_want.append(len(steps))
        elif c.get("evidence") == "none":
            # NOOD_0225 — the opt-out is still per-step: the run-wide default
            # ('last') shoots whichever step ends the scenario, so a check
            # that declined a picture and happened to land last still got one.
            ev_skip.append(len(steps))
        if pom_name is None:
            return
        if "count" in c:
            rsum = ev.get("results_summary")
            if rsum:
                pom_entries.setdefault(
                    pom_name, [f"{pom_name}:",
                               f'  css: {_yaml_str(rsum["selector"])}'])

    def _anchored(aid):
        for c in checks:
            if aid is not None and c.get("after") == aid:
                _emit_check(c)

    # NOOD_0212 — picks whose add_to bound the results-page card action. Their
    # navigation click is a detour away from that control, so the pick branch
    # below emits the binding without the click.
    _mp = ev.get("mutation_plans") or {}
    _adds_on_results = {
        a.get("item_from") for a in actions
        if a.get("do") == "add_to" and a.get("item_from")
        and (_mp.get(a.get("id")
                     or f"add_to:{a.get('destination', '')}") or {}
             ).get("on_results")}

    # NOOD_0201 — a relative api url joins {var:REST_BASE_URL} at run time;
    # without this Given the join is "" + path and the call never leaves the
    # machine. The env key is the same one the app's environments.yaml stores
    # the (possibly discovered) base URL under.
    if any(a.get("do") == "api"
           and not str(a.get("url", "")).startswith("http")
           for a in actions):
        steps.append(("Given", "sets {var:REST_BASE_URL} to "
                               f"'{{env:{base_url_key}}}'"))

    # NOOD_0192 — the REST preamble, ahead of any navigation.
    for a in actions[:pre]:
        _append_api(a)
        _anchored(a.get("id"))

    if web:
        for k in (nav_keys or [base_url_key]):
            steps.append(("Given", f'User is on "{{env:{k}}}"'))
        perms = list(dict.fromkeys(
            [*ev.get("permission_prompts", []),
             *(_DISMISS_PERM[d] for d in dismissals if d in _DISMISS_PERM)]))
        for perm in perms:
            if perm in _PERM_STEP:
                steps.append(("When", _PERM_STEP[perm]))
        if ev.get("popups_closed") or "popups" in dismissals:
            steps.append(("When", _POPUP_STEP))

    # NOOD_0163 — the landing page is the only page an action can't anchor to,
    # so `after: start` is emitted here, before anything is clicked.
    for c in checks:
        if c.get("after") == _START:
            _emit_check(c)

    for a in actions[pre:]:
        if a["do"] == "api":
            _append_api(a)
            _anchored(a.get("id"))
            continue
        if a["do"] == "add_to":
            # NOOD_0156 — semantic mutation, lowered to the exact probed
            # chain: the (at most one) probe-proven prerequisite reveal, then
            # the resolved mutation control. Both carry probe selectors; a
            # prerequisite without reveal evidence never reaches this point
            # (evidence() blocks instead of planning one).
            aid = a.get("id") or f"add_to:{a.get('destination', '')}"
            plan = (ev.get("mutation_plans") or {}).get(aid) or {}
            chain = [c for c in (plan.get("prerequisite"),
                                 plan.get("control")) if c]
            scope = plan.get("within")
            for ctrl in chain:
                if scope:
                    # NOOD_0207 — the row/item-scoped step (click_in_row) has
                    # been in the pattern table since NOOD_0011; goal mode
                    # simply never reached it. NO POM entry: the probed
                    # selector matches every card, so writing it would
                    # reintroduce the wrong-instance bug this fixes.
                    steps.append(("When", f'User clicks "{ctrl["name"]}" in '
                                          f'the row containing "{scope}"'))
                    continue
                steps.append(("When", f'User clicks "{ctrl["name"]}"'))
                if ctrl.get("selector"):
                    pom_entries.setdefault(
                        ctrl["name"],
                        ctrl.get("pom") or [f'{ctrl["name"]}:',
                                            f'  css: {_yaml_str(ctrl["selector"])}'])
            if a.get("id") is not None:
                for c in checks:
                    if c.get("after") == a["id"]:
                        _emit_check(c)
            continue
        if a["do"] == "pick":
            # NOOD_0156 — the bound target: one concrete result caption from
            # probe evidence stands in for the generic "any matching result",
            # POM'd with the exact probed selector so the click is
            # deterministic. The same caption feeds the item assertion.
            b = bound.get(a.get("id") or "result") or {}
            cap = b.get("caption") or a.get("target") or "result"
            # NOOD_0212 — when the add_to this pick feeds resolved on the
            # RESULTS page (the card's own Add button), opening the product
            # first is a detour: it navigates away from the very control the
            # plan binds. Keep the binding — the caption still names the item
            # and still feeds item_in_destination — and drop only the click.
            if a.get("id") not in _adds_on_results:
                steps.append(("When", f'User clicks "{cap}"'))
                if b.get("selector"):
                    pom_entries.setdefault(
                        cap, [f"{cap}:", f'  css: {_yaml_str(b["selector"])}'])
            aid = a.get("id")
            if aid is not None:
                for c in checks:
                    if c.get("after") == aid:
                        _emit_check(c)
            continue
        if a["do"] == "suggest":
            # NOOD_0141 — the canonical probe-captured spelling wins over the
            # goal's paraphrase, and the intent assertion ("a partial term
            # still yields this suggestion") rides in front for free: it is
            # probe-proven, and it fails EARLY with the visible list when the
            # typeahead breaks, instead of at the click.
            canon = ev.get("proven", {}).get(f'suggest:{a["term"]}') \
                or a["option"]
            steps.append(("Then", f'the search suggestions for "{a["term"]}" '
                                  f'include "{canon}"'))
            steps.append(("When", f'User selects the "{canon}" suggestion '
                                  f'for "{a["term"]}"'))
            aid = a.get("id")
            if aid is not None:
                for c in checks:
                    if c.get("after") == aid:
                        _emit_check(c)
            continue
        ctrl = None
        # NOOD_0188 — every TARGETED action resolves through the probe's
        # canonical name and gets a POM entry below. This tuple used to be
        # the three original verbs, so a new targeted action would silently
        # skip both (unstable locator, no POM key).
        if a["do"] in _TARGETED_ACTIONS:
            # NOOD_0145 — the evidence pass already resolved this target
            # (exact/synonym/submit rules); reuse ITS verdict so the compiled
            # step names the same control instead of re-matching by substring.
            # NOOD_0156 — the resolved control DICT wins outright: scoped
            # resolution may have picked a landed-page control over a
            # same-named results-page twin, and only the dict carries the
            # right selector.
            ctrl = (ev.get("resolved_controls") or {}).get(
                f'{a["do"]}:{a["target"]}')
            if ctrl is None:
                res_name = (ev.get("proven") or {}).get(
                    f'{a["do"]}:{a["target"]}')
                if res_name:
                    ctrl = ev.get("controls", {}).get(_norm(res_name))
            # NOOD_0209 — refuse rather than guess: when the evidence pass
            # BLOCKED this very target, the best-effort fallback still bound
            # it (the observed artifact tied an enter target named "-" to a
            # quantity stepper — a file an operator could hand-run). A
            # blocked target keeps the author's own wording and mints no POM
            # entry; the blocker, not a guessed binding, is the deliverable.
            blocked = any(
                b.startswith(f'{a["do"]} "{a.get("target", "")}"')
                for b in ev.get("blocking") or ())
            if not blocked:
                ctrl = ctrl or ev.get("controls", {}).get(_norm(a["target"])) \
                    or _find_control(
                        a["target"],
                        [{"controls": list(ev.get("controls", {}).values())}])
        target = ctrl["name"] if ctrl else a.get("target", "")
        steps.append(("When", _action_step(a, target)))
        # POM every goal action target with a stable selector — NOT gated on
        # needs_pom (which is about probe presentation, not a runtime-lookup
        # guarantee). A probe-visible control can still lack a runtime accessible
        # name; the deterministic selector is what makes it resolvable.
        # NOOD_0207 — a `within`-scoped action gets NO POM entry: the probed
        # selector matches every row/card, so pinning it would re-bind the
        # step to instance 1 and undo the scoping.
        if ctrl and ctrl.get("selector") and not a.get("within"):
            # NOOD_0177 — quote the KEY as well as the value. The key is
            # page-derived text; unquoted it could carry a ':' (ScannerError,
            # breaking authoring outright) or, before _clean_name, a newline
            # that added attacker-chosen POM entries. A POM key silently
            # re-binds every future step that uses that phrase, so this one
            # persists across runs.
            pom_entries.setdefault(
                ctrl["name"], ctrl.get("pom")
                or [f'{_yaml_str(ctrl["name"])}:',
                    f'  css: {_yaml_str(ctrl["selector"])}'])
        aid = a.get("id")
        if aid is not None:
            for c in checks:
                if c.get("after") == aid:
                    _emit_check(c)

    # NOOD_0158 — an unanchored check observes the END state. These used to be
    # emitted BEFORE the action loop, so a goal whose checks omitted `after`
    # asserted the outcome against the landing page and failed on the first
    # run ("not found. URL: <base_url>") — the author's only tell being a red
    # run and a re-author with `after` added. A check is what proves the goal
    # worked; nothing to prove exists until the actions have run. Placement
    # before an action stays expressible — that is what `after: <id>` is for.
    for c in checks:
        if c.get("after") is None:
            _emit_check(c)

    # NOOD_0200 — a tag-safe slug of the scenario names the compiled POM's
    # page block AND rides the feature as a @page pin, so the block resolves
    # for THIS feature only (see the pom emission below).
    slug = re.sub(r"[^a-z0-9]+", "_", goal["scenario"].lower()).strip("_") \
        or "goal"
    # NOOD_0211 — the evidence policy rides as ONE scenario tag. The
    # alternative the engine used to force — `( take a screenshot )` appended
    # to every assertion — put run configuration into the step text of every
    # line, which reads as noise and is trivially forgotten on step nine.
    evidence_tag = {"all": " @evidence",
                    "assertions": " @evidence:assertions",
                    "off": " @no_evidence"}.get(goal.get("evidence"), "")
    # NOOD_0227 (D1) — checks[].evidence rides the SAME tag channel, as
    # 1-based step positions: run configuration stays metadata end to end
    # (reporting/evidence.step_directives reads it back at run time).
    if ev_want:
        evidence_tag += " @evidence:steps=" + ",".join(map(str, ev_want))
    if ev_skip:
        evidence_tag += " @evidence:skip=" + ",".join(map(str, ev_skip))
    tag = ("@web" if web else "@api") \
        + (f" @page:{slug}" if web and pom_entries else "") \
        + evidence_tag
    lines = [tag, f"Feature: {goal['scenario']}", "",
             f"  Scenario: {goal['scenario']}"]
    prev = None
    for idx, (kw, body) in enumerate(steps):
        # NOOD_0177 — second gate on the same channel probe._clean_name closes.
        # A step body is ONE Gherkin line; a body carrying \n or \r would append
        # extra lines to the compiled .feature when the join below runs, and the
        # pattern table would happily resolve an injected `runs the command …`
        # line into subprocess.run(shell=True). Fail loudly rather than emit a
        # feature nobody authored. (api `rows` tables ride the separate
        # `tables` channel below, cells already validated pipe/newline-free.)
        if "\n" in body or "\r" in body:
            raise ValueError(
                "refusing to compile a step body containing a line break — "
                f"page-derived text leaked a newline into: {body!r}")
        lines.append(f"    {kw if kw != prev else 'And'} {body}")
        lines.extend(f"      {tl}" for tl in tables.get(idx, ()))
        prev = kw
    feature = "\n".join(lines) + "\n"

    # NOOD_0212 — a caller-supplied pom_content used to be dropped on the floor
    # the moment `goal:` was also present (the caller's variable was simply
    # rebound to this function's return), so the documented way to pin a
    # control the compiler cannot infer silently did nothing. Folded in here
    # instead of alongside, for two reasons: the caller's keys must WIN over
    # the compiled ones (that is the whole point of supplying them), and
    # riding inside the @page block gives them the pin's reach — every URL the
    # scenario visits — rather than the filename scoping a sibling flat file
    # would have imposed, which never covers the page a nav control sits on.
    for key, sel in _flat_pom_entries(extra_pom).items():
        pom_entries[key] = [f"{key}:", *(f"  {k}: {v}" for k, v in sel.items())] \
            if isinstance(sel, dict) else [f"{key}: {sel}"]

    pom = None
    if pom_entries:
        body = [line for entry in pom_entries.values() for line in entry]
        # NOOD_0200 — pinned to this feature, not folder-global: `match: {}`
        # made every compiled POM active on every URL, so a second compiled
        # feature in the same app shadowed the first's same-named keys
        # (alphabetically-first wins — warning noise today, silently wrong
        # element tomorrow). A pages: block with no match: resolves ONLY via
        # the scenario's @page pin (hooks.page_pin), which spans every page
        # the scenario navigates — the same reach match: {} gave, invisible
        # to sibling features. Keys the app hand-shares stay in the app's
        # resources/pom.yaml, which this file never shadows.
        pom = "\n".join([f"# Page object — compiled from goal "
                         f"'{goal['scenario']}'",
                         "pages:",
                         f"  {slug}:   # resolved via the feature's "
                         f"@page:{slug} pin",
                         *(f"    {ln}".rstrip() for ln in body)]) + "\n"
    return feature, pom

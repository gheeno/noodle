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
    reviewed session an 8-call docs hunt before the first author attempt."""
    return {
        "goal_keys": sorted(_GOAL_KEYS),
        "actions": {do: {"keys": sorted(keys),
                         "required": sorted(_ACTION_REQUIRED.get(do) or ())}
                    for do, keys in sorted(_ACTION_KEYS.items())},
        "check_keys": sorted(_CHECK_KEYS),
        "dismissals": sorted(_DISMISSALS),
        "notes": "one of " + "|".join(_CHECK_KINDS) + " per "
                 "check; evidence: 'screenshot'|'none' on any check (ask for "
                 "or decline a shot on THAT step); goal-level evidence: "
                 + "|".join(sorted(_EVIDENCE_MODES))
                 + " sets the whole run (default: one shot on the last step) "
                 "— never hand-write a screenshot step or tag, the engine "
                 "reads the brief and compiles both; after: "
                 "start|<action id> anchors a check to its page; "
                 "item_in_destination pairs with expected_from: <pick id>; "
                 "navigation: [<url>, ...] = ordered URLs, actions run on "
                 "the last; probe: {discover: true} auto-reveals unnamed "
                 "triggers, {perform: true} PERFORMS the post-mutation steps "
                 "so the destination page is proven evidence (it writes state "
                 "on the app — opt-in, never default)",
        "probe_keys": sorted(_PROBE_KEYS),
    }


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

# NOOD_0156 — Unicode-aware: `[\W_]` keeps letters/digits of ANY script
# (the old [^a-z0-9] erased every non-ASCII caption to "", so goal matching
# and result binding silently failed on non-English sites). casefold() is
# the universal case mapping (ß→ss, İ→i̇) — the engine must work for any
# web app in any language, not just Latin-script ones.
_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _norm(s) -> str:
    return _NORM_RE.sub(" ", str(s or "").casefold()).strip()


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


def same_url(a: str, b: str) -> bool:
    """Scheme- and trailing-slash-insensitive URL equality — one derivation
    shared by navigation dedup (normalize) and env-key reuse."""
    def _bare(u: str) -> str:
        return u.rstrip("/").removeprefix("https://").removeprefix("http://")
    return _bare(a) == _bare(b)


def navigation_urls(goal: dict) -> list[str]:
    """The goal's ordered requested URLs, normalized to plain strings.
    [] when the goal has no navigation contract (single-URL goals keep the
    caller-supplied base URL)."""
    out = []
    for n in goal.get("navigation") or []:
        u = n.get("url") if isinstance(n, dict) else n
        if isinstance(u, str) and u.strip():
            out.append(u.strip())
    return out


def navigation_env(goal: dict, app: str,
                   base_url: str | None = None) -> list[tuple[str, str]]:
    """Ordered (ENV_KEY, url) pairs for the goal's navigation contract — the
    compiler emits only {env:KEY} references; the URLs live in the app
    environments.yaml. Keys derive from the app + each URL's last path
    segment (universal — no site-specific names), deduplicated by suffix.
    NOOD_0209 — a navigation URL that IS the app's base URL reuses the app's
    own key instead of minting a second one (<APP>_HOME next to <APP> was
    systematic: two keys, one value, and the extra one never pruned)."""
    from urllib.parse import urlsplit

    taken, out = set(), []
    for u in navigation_urls(goal):
        if base_url and same_url(u, base_url):
            key = app.upper()
            if key not in taken:
                taken.add(key)
                out.append((key, u))
                continue
        path = urlsplit(u if "://" in u else f"https://{u}").path.strip("/")
        stem = path.rsplit("/", 1)[-1] if path else ""
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        stem = re.sub(r"[\W_]+", "_", stem, flags=re.UNICODE).strip("_").upper() \
            or "HOME"
        key, n = f"{app.upper()}_{stem}", 2
        while key in taken:
            key, n = f"{app.upper()}_{stem}{n}", n + 1
        taken.add(key)
        out.append((key, u))
    return out


def probe_args(goal: dict) -> dict:
    """The ONE bounded probe transaction this goal needs — nothing broader.
    Permission prompts, popups, standard search, and requested assertions do
    NOT imply discovery; discover only on goal.probe.discover. Native-control
    enumeration only when a select action needs options.

    Only REVEAL clicks (those before the first enter/select) are executed by
    the probe. A click after data entry is a commit (save/submit) — probing it
    would mutate application state, so it stays a runtime-only action (Risk 1)."""
    actions = goal.get("actions") or []
    search = next((a["term"] for a in actions if a["do"] == "search"), None)
    suggest = next((a["term"] for a in actions if a["do"] == "suggest"), None)
    # NOOD_0156 — a pick asks the probe to click the ONE bound result and
    # snapshot the landed page (read-only navigation, never a mutation), so
    # later requested actions resolve against real landed-page evidence.
    pick = next(((a.get("target") or "*") for a in actions
                 if a["do"] == "pick"), None)
    # NOOD_0156 — a requested add_to asks the probe to PROVE the mutation
    # path on the landed page (find the exact mutation control, or one
    # bounded prerequisite reveal) — never to perform the mutation itself.
    mutate = next((a.get("destination") for a in actions
                   if a["do"] == "add_to"), None)
    gate = _runtime_gate(actions)
    clicks = [a["target"] for i, a in enumerate(actions)
              if a["do"] == "click" and (gate is None or i < gate)]
    # NOOD_0195 — FOLLOW the suggestion (NOOD_0142), don't just read the list.
    # Without this the probe stopped at the typeahead and closed it, so a
    # `suggest` goal only ever saw the LANDING page: every check on the page
    # the suggestion navigates to had no evidence source at all. Verified live
    # — a control literally named "a" then "proved" two 41-character product
    # titles through _find_text's reverse containment, and `ready: true` rested
    # on that. Following is read-only navigation, the same contract `pick`
    # already has, and it costs one page load.
    follow = next((a["option"] for a in actions if a["do"] == "suggest"), None)
    # NOOD_0195 — ask the probe to VERIFY the exact strings the checks name
    # (--expect, NOOD_0142: a full-text search of the page it ended on). The
    # structured captures are lossy — result captions truncate at ~60 chars,
    # so a 68-character product title could never be proven whole from them,
    # and the literal upgrade that needs full-render proof could never fire on
    # the flow it was written for. An expect verdict is exact and cheap: one
    # FOUND/NOT-FOUND per string, no extra page load.
    # ponytail: capped at 8 — the strings ride the probe payload, and a goal
    # naming more than 8 literals is asking for a suite, not a scenario.
    expect = [t for c in (goal.get("checks") or []) if isinstance(c, dict)
              for t in ([c["see"]] if isinstance(c.get("see"), str)
                        else [x for x in (c.get("any_of") or [])
                              if isinstance(x, str)])][:8]
    return {"search": search, "suggest": suggest, "pick": pick,
            "mutate": mutate, "follow": follow, "expect": expect or None,
            "click": clicks or None,
            "do": perform_do(goal) or None,
            "open_native_controls": any(a["do"] == "select" for a in actions),
            "discover": bool((goal.get("probe") or {}).get("discover")),
            "perform": bool((goal.get("probe") or {}).get("perform"))}


# NOOD_0208 — how many post-gate actions the probe will perform. Bounded
# because each one is a real click on a real app: a runaway chain would drive
# an unbounded transaction against someone's production site.
_PERFORM_CAP = 8


def perform_do(goal: dict) -> list[str]:
    """NOOD_0208 — the goal's post-gate actions as probe `do` strings, or [].

    Opt-in only (`probe: {perform: true}`). The probe deliberately stops at
    the first state-writing action, so every page beyond it — the confirmation
    the test asserts on — was never snapshotted, and the first authored
    wording was a GUESS. That cost one red run per flow even after NOOD_0207
    attached the exact repair to it.

    Turning it on hands those same actions to the existing `--do` transaction
    (NOOD_0144), which performs each one, settles, and diff-snapshots the
    result. Two things fall out for free: the destination page becomes real
    evidence (so the assertion is proven, not guessed), and a click that only
    NAVIGATED instead of mutating is visible as its own delta — the
    navigation-CTA trap.

    This PERFORMS MUTATIONS on the target app. That is why it is opt-in and
    capped, and why nothing turns it on by default."""
    if not (goal.get("probe") or {}).get("perform"):
        return []
    actions = [a for a in (goal.get("actions") or []) if isinstance(a, dict)]
    gate = _evidence_gate(actions)
    if gate is None:
        return []
    out = []
    for a in actions[gate:]:
        do, target = a.get("do"), a.get("target")
        if do == "add_to":
            # the mutation control resolves at evidence time, not here — the
            # destination word is what the probe already takes as `mutate`.
            continue
        if do == "click" and target:
            out.append(f"click {target}")
        elif do == "enter" and target:
            out.append(f"enter {a.get('value', '')} in {target}")
        elif do == "select" and target:
            out.append(f"select {a.get('option', '')} from {target}")
        else:
            # An action the do-grammar can't express ends the chain: the
            # states after it were never reached, and pretending otherwise
            # would claim evidence for a page the probe never saw.
            break
        if len(out) >= _PERFORM_CAP:
            break
    return out


def _evidence_gate(actions: list) -> int | None:
    """NOOD_0207 — index of the first action that writes state the probe
    deliberately never performs, so every page BEYOND it was never
    snapshotted. An action there naming an unprobed control is a statement
    about the probe's reach, not about the app: it is deferred to the run,
    exactly as checks in that position already were. Without this, every
    legitimate multi-page flow (mutate → checkout → form → submit) was
    unauthorable, and the reader burned laps probing for a name authoring was
    never going to accept.

    Deliberately NARROWER than _runtime_gate, which also lists `pick`,
    `enter` and `select`: the probe DOES follow a pick's navigation and
    snapshot the landed page, and it does resolve the fields an enter/select
    names — so a control unprobed there is genuinely absent, and blocking is
    right (test_nood_0156 pins that)."""
    for i, a in enumerate(actions):
        if a.get("do") in ("add_to", "press_key", "go_back"):
            return i
    return None


def _reach_gate(actions: list) -> int | None:
    """NOOD_0226 — index of the LAST action whose page the probe snapshotted.
    Every action AFTER it runs on a page the probe never loaded, so an
    unmatched name there is a statement about the probe's reach, not about the
    app — and a name that DOES match came off a page this step never visits.

    Two boundaries, whichever comes first:

    * `_evidence_gate` — the first state-writing verb the probe never performs
      (NOOD_0207); everything past it was already deferred on these terms.
    * the first COMMIT click — a click at or after the runtime gate.
      probe_args forwards only the clicks BEFORE that gate, by its own rule:
      "a click after data entry is a commit (save/submit) — probing it would
      mutate application state". So the page such a click lands on was never
      loaded, and nothing after it has evidence either.

    The login prelude is the everyday case and it had no boundary at all:
    enter / enter / click sign-in gates the runtime at index 0 and names no
    state-writing verb, so `_evidence_gate` was None, no click was forwarded
    to the probe, and every post-login step blocked with the SIGN-IN page's
    controls offered as near misses. Measured on `main` @ d34a902: the goal
    compiled correctly and the engine refused to admit it.

    The boundary action itself stays fully verified — its own control is on a
    page the probe did see (the sign-in button is on the sign-in page), so
    NOOD_0156's "an unprobed control after a pick still blocks" is untouched;
    only what follows it moves to the run."""
    eg = _evidence_gate(actions)
    rg = _runtime_gate(actions)
    commit = None if rg is None else next(
        (i for i, a in enumerate(actions)
         if i > rg and a.get("do") == "click"), None)
    gates = [g for g in (eg, commit) if g is not None]
    return min(gates) if gates else None


def _reach_clause(actions: list, reach: int | None, i: int) -> str:
    """NOOD_0226 — the one sentence a blocker needs when the goal walks past
    the probe's reach: which step the evidence ends on.

    Without it the probed vocabulary a blocker carries reads as "the app's
    controls", and the repair it invites is renaming a LATER step to an
    earlier page's control — the exact wrong move, and the one the login
    repro handed the reader (`username`, `password`, `login` offered as near
    misses for an add-to-cart click). Emitted only where it changes the
    repair: on the boundary step when later steps exist, and on any step past
    it that still blocks (a stated ambiguity/reachability note). A goal that
    ends at the boundary gets nothing — bytes on a payload with nothing to
    say."""
    if reach is None or reach >= len(actions) - 1 or i < reach:
        return ""
    label = _reach_label(actions, reach)
    where = f" ({label})" if label else ""
    if i > reach:
        return (f" — the probe's page evidence ends at step {reach + 1}"
                f"{where}; this step runs on a page it never loaded, so the "
                "controls named above are that earlier page's, not this one's")
    return (" — this is the last step the probe has page evidence for"
            + where
            + f"; the {len(actions) - reach - 1} step(s) after it run on pages "
              "it never loaded and are resolved by the run")


def _reach_label(actions: list, reach: int | None) -> str:
    """The boundary action in the goal's own words, for a blocker that has to
    say where the evidence stops."""
    if reach is None or not (0 <= reach < len(actions)):
        return ""
    a = actions[reach] if isinstance(actions[reach], dict) else {}
    what = a.get("target") or a.get("destination") or a.get("term") or ""
    do = str(a.get("do") or "")
    return f'{do} "{what}"' if what else do


def _runtime_gate(actions: list) -> int | None:
    """Index of the first action whose effect the probe does NOT perform:
    enter/select values are never typed, and everything AFTER a pick runs on
    the landed page the probe only snapshots (NOOD_0156 — the add-to-cart
    click itself would mutate state, so the probe never performs it). Every
    check anchored at or after the gate is runtime-asserted (proven by the
    run), never claimed probe-proven.

    NOOD_0195 — `suggest` LEFT this list. It was here because the probe typed
    the term, read the list and closed it without clicking through
    (NOOD_0141); probe_args now passes `follow`, so the probe lands on the
    suggestion's own results page — the same position `search` leaves it in,
    and `search` never gated. While it did gate, every check after a suggest
    was silently routed to runtime-asserted: never proven, never blocked,
    never eligible for the literal upgrade. That is a `ready: true` that
    checked nothing. A follow that finds no matching row sets
    `suggest_warning`, which blocks below before any check is evaluated.

    NOOD_0188 — the new form/navigation verbs join it on the same rule: every
    one of them either writes state (check/uncheck/upload/pick_date), can
    submit (press_key) or moves the page out from under the snapshot
    (go_back). `hover` is deliberately NOT here — like a reveal click it only
    exposes controls, which is exactly what the probe is for."""
    for i, a in enumerate(actions):
        if a.get("do") in ("enter", "select", "pick", "add_to",
                           "check", "uncheck", "upload", "press_key",
                           "pick_date", "go_back"):
            return i
    return None


# NOOD_0156 — evidence-bound result selection. Pure: the probe calls this to
# decide WHICH result to click, and the evidence pass records the same caption
# as the bound target — one rule, no drift.

# NOOD_0200 — actions the probe's own transaction carries the page through, so
# the snapshot it ends on IS that action's page. Everything else at or after the
# runtime gate is performed only by the run.
_PROBE_FOLLOWS = ("search", "suggest", "pick", "hover")


def _beyond_probe_reach(actions: list, performed: bool = False) -> bool:
    """True when the goal's actions carry the page past the last state the
    probe snapshotted — so an END-state check has no probe evidence at all and
    belongs to the run, not to `blocking` and not to `proven`.

    Domain-agnostic by construction: it reads the action grammar only. A goal
    that just searches (or follows a suggestion, or picks a result) keeps its
    pre-run verification untouched — the probe really did land on that page.

    NOOD_0208 — `performed` (probe.perform) makes this false by construction:
    the probe executed those very actions and snapshotted where they landed,
    so there is no "past the probe's reach" left to route around. Without this
    the opt-in would collect the destination evidence and then decline to use
    it, which is the whole lap it exists to remove."""
    if performed:
        return False
    gate = _runtime_gate(actions)
    if gate is None:
        return False
    return any(a.get("do") not in _PROBE_FOLLOWS for a in actions[gate:])


def bind_result(controls: list[dict], term: str,
                target: str | None = None,
                items: list[dict] | None = None) -> tuple[dict | None, str | None]:
    """(control, why_not) — bind a generic "pick a result" request to ONE
    concrete result control from the post-search collection.

    NOOD_0156 (intent contract v2) — when the probe extracted structured
    `result_items`, binding is STRUCTURAL: membership in the search-result
    region is the provenance, so a valid result (a branded doll, game, or
    truck) binds even when its caption never repeats the generic query word.
    `first_actionable` preserves DOM order and prefers the first item that
    carries a card-scoped action; a named target filters by caption. The
    legacy flat-control path below keeps the lexical term match — without
    region structure it is the only provenance available.

    Deterministic either way. Anything weaker returns (None, reason) — a
    block, never a guess at a non-item control."""
    if items:
        gn = _norm("" if target in (None, "*") else target)
        stable = [it for it in items
                  if _norm(it.get("caption")) and it.get("selector")]
        if gn:
            stable = [it for it in stable
                      if any(t in _norm(it["caption"]) for t in gn.split())]
            if not stable:
                return None, (f"no result item caption matches {target!r} — "
                              "name one of the probed result captions exactly")
        if not stable:
            return None, ("result items were collected but none carries a "
                          "stable caption + selector — cannot bind a "
                          "deterministic result")
        # first_actionable: DOM order, preferring an item whose card already
        # proves an action path; plain first stable item otherwise.
        cand = next((it for it in stable if it.get("actions")), stable[0])
        ctrl = {"name": cand["caption"], "selector": cand["selector"],
                "kind": "link"}
        if cand.get("actions"):
            ctrl["actions"] = cand["actions"]
        return ctrl, None
    tn = _norm(term)
    gn = _norm("" if target in (None, "*") else target)
    counts: dict[str, int] = {}
    for c in controls or []:
        n = _norm(c.get("name"))
        if n:
            counts[n] = counts.get(n, 0) + 1
    matched, ambiguous = [], 0
    for c in controls or []:
        n = _norm(c.get("name"))
        if not n or c.get("kind") not in ("link", "button"):
            continue
        hit = (tn and tn in n) or (gn and any(t in n for t in gn.split()))
        if not hit:
            continue
        if not c.get("selector") or counts[n] > 1:
            ambiguous += 1
            continue
        matched.append(c)
    if matched:
        return matched[0], None
    if ambiguous:
        return None, (f"{ambiguous} matching result caption(s) lack a unique "
                      "stable caption+selector — cannot bind a deterministic "
                      "result; refine the term or name the exact caption")
    return None, ("no probed search-result caption matches the term"
                  + (f" or {target!r}" if gn else "")
                  + " — cannot bind a concrete result to pick")


def _mutating_name(name: str) -> bool:
    """The probe's locale-aware mutating-verb gate, imported lazily so this
    module stays importable without the web stack."""
    try:
        from noodle.agents.web.probe import _is_mutating
    except Exception:                                    # pragma: no cover
        return False
    return _is_mutating(name)


def navigation_shaped(c: dict) -> bool:
    """NOOD_0208 — this control qualified as a mutation ONLY by its name, and
    it is a plain link to somewhere else.

    The trap: a landing page carries a CTA literally named "add to <dest>"
    that merely NAVIGATES to the page where you add it. `mutation_control`
    accepts it (the name matches the mutating-verb gate), the compiler emits
    the click, and the run fails one step later on an item that was never
    added — with the blame landing on the next control.

    Deliberately advisory, never a block on its own: plenty of real
    mutate-controls are anchors that POST server-side, and NOOD_0207's lesson
    is that a heuristic must not turn a working green into a refusal. It
    demotes such a candidate behind any button/submit sibling, and names the
    risk plus the opt-in (`probe: {perform: true}`) that settles it by
    clicking."""
    href = str(c.get("href") or "").strip()
    return (c.get("kind") == "link" and not c.get("submit")
            and bool(href) and not href.startswith(("#", "javascript:")))


# NOOD_0212 — a card's own action button is already proven to act on ONE item,
# so it need not spell the destination the way a flat page control must: retail
# grids label the control "Add" (or "+") and put the word "cart" nowhere near
# it. _MUTATING_RE is the WRONG gate here — it matches "remove", "delete" and
# "save", which are precisely the sibling card actions that must never bind as
# an add. This one is additive-only, with the destructive/deferral siblings
# named explicitly so a locale addition can't quietly let one through.
_ADDITIVE_ITEM_RE = re.compile(
    r"(?:^|\b)(add|\+|buy|purchase|"
    r"hinzuf\w*|kaufen|ajouter|acheter|a[ñn]adir|agregar|comprar|"
    r"aggiungi|acquista|adicionar|toevoegen|kopen)(?:\b|$)", re.I)
_ITEM_ACTION_EXCLUDE_RE = re.compile(
    r"\b(remove|delete|save|wish\s?list|favou?rite|compare|later|share|"
    r"quick\s?view|entfernen|l[öo]schen|supprimer|eliminar|rimuovi)\b", re.I)


def item_actions(blk: dict) -> list[dict]:
    """NOOD_0212 — the probe stores a card's own buttons in
    `result_items[].actions[]`, NEVER in `blk["controls"]`.

    NOOD_0195 fixed exactly this shape for text (captions live in
    `result_items` too, and a `see:` naming a real product hard-blocked while
    the probe held the caption all along). The mutation prover was still
    reading `controls` only, so a results page whose every card carries an
    "Add" button reported "no probed control mutates into 'cart'" — and the
    reader burned laps probing for a name that was in the payload the whole
    time. `--find add` printed it as `[result-item-action]`; this is the same
    list, handed to the prover."""
    out = []
    for it in blk.get("result_items") or []:
        for a in it.get("actions") or []:
            if not a.get("name"):
                continue
            out.append({**a, "item_caption": it.get("caption"),
                        "origin": "result-item-action"})
    return out


def block_mutation_candidates(blk: dict) -> list[dict]:
    """Everything on one probed block that could perform a mutation: the flat
    controls plus each card's own actions. One list, so the prover and the
    "the page offers:" hint can never again disagree about what was seen."""
    return list(blk.get("controls") or []) + item_actions(blk)


def _additive_item_action(c: dict) -> bool:
    n = c.get("name") or ""
    return bool(_ADDITIVE_ITEM_RE.search(n)) \
        and not _ITEM_ACTION_EXCLUDE_RE.search(n)


def mutation_control(controls: list[dict], destination: str,
                     scoped: bool = False) \
        -> tuple[dict | None, str | None]:
    """(control, why_not) — THE probed control that performs the requested
    mutation into `destination` ("add to cart"-shaped). One rule shared by
    the probe's mutation proof and the evidence pass — no drift. A candidate
    must name the destination PLUS more (a bare "cart" control opens the
    destination, it doesn't mutate into it) and be mutation-shaped (button /
    submit / a locale-aware mutating verb). A few same-named visible
    instances are responsive duplicates of one control — first visible
    binds (NOOD_0168); MANY distinct instances are one-per-card — block,
    never pick one."""
    dn = _norm(destination)
    if not dn:
        return None, "no destination named"
    cands = []
    for c in controls or []:
        if c.get("origin") == "result-item-action":
            continue          # NOOD_0212 — considered below, as a fallback
        n = _norm(c.get("name"))
        if not n or dn not in n or n == dn:
            continue
        if c.get("kind") == "button" or c.get("submit") \
                or _mutating_name(c.get("name", "")):
            cands.append(c)
    if not cands:
        # NOOD_0212 — no flat control names the destination; fall back to the
        # cards' own actions. Deliberately a FALLBACK, not a peer: where a
        # page carries a real "Add to cart" control the existing binding must
        # not change, and a card action can never introduce a new ambiguity
        # into a page that already resolved. The destination word is not
        # required here — being an action ON a result item is the proof that
        # a flat control has to earn by naming what it acts on.
        cands = [c for c in controls or []
                 if c.get("origin") == "result-item-action"
                 and _additive_item_action(c)]
    if not cands:
        return None, (f"no probed control mutates into {destination!r} "
                      "(nothing names the destination beyond opening it)")
    # NOOD_0208 — a button/submit sibling outranks a navigation-shaped link
    # naming the same thing. Order only: when the link is the ONLY candidate
    # it still wins, and evidence() warns instead of refusing.
    if any(not navigation_shaped(c) for c in cands):
        cands = [c for c in cands if not navigation_shaped(c)]
    names = {_norm(c.get("name")) for c in cands}
    if len(names) > 1:
        # NOOD_0207 — a label that STARTS with the destination word names it
        # ("cart now" navigates to the cart); one that mentions it after a
        # verb acts on it ("add … to cart"). Position is the discriminator —
        # _is_mutating on the name with the destination stripped returns
        # False for both, so it cannot separate them.
        acting = {n for n in names if not n.startswith(dn)}
        if len(acting) == 1:
            names = acting
            cands = [c for c in cands if _norm(c.get("name")) in acting]
    if len(names) > 1:
        return None, ("ambiguous — several distinct probed controls could "
                      "perform the mutation: "
                      + ", ".join(sorted(names)[:4])
                      + "; name the exact control")
    sels = {c.get("selector") for c in cands if c.get("selector")}
    if scoped and cands:
        # NOOD_0207 — the searchless shape. `within` already says WHICH
        # instance, so a grid of N same-named controls is the expected shape,
        # not a block: the run scopes the click to the anchored card. Bind
        # the first visible instance for its selector/name only; the compiled
        # step never uses that selector (compile_goal skips the POM entry
        # precisely because it matches every card).
        return next((c for c in cands if c.get("visible")), cands[0]), None
    if len(sels) > 1:
        # NOOD_0168 — responsive pages render the same-named buy control
        # more than once (buy box + sticky bar, often BOTH visible). After a
        # pick the landed page holds ONE item, so same-named duplicates
        # perform the SAME mutation — bind the first visible instance. A
        # wrong bind cannot survive: the probe clicks it to prove the
        # before/after delta, and item_in_destination must still find the
        # picked caption in the destination. Many distinct instances is a
        # card GRID (one per item), not a duplicate — that stays a block.
        vis = {c["selector"]: c for c in cands
               if c.get("visible") and c.get("selector")}
        if vis and len(vis) <= 3:
            return next(iter(vis.values())), None
        return None, (f"{len(cands)} probed instances share the mutation "
                      "control name — scope the mutation to one concrete "
                      'item/card: add within: "<text unique to that '
                      'item\'s card/row>"')
    return cands[0], None


# --- evidence ----------------------------------------------------------------

def _block_texts(blk: dict) -> list[str]:
    texts = list(blk.get("headings", []))
    for c in blk.get("controls", []):
        if c.get("name"):
            texts.append(c["name"])
    # NOOD_0195 — search-result captions. A results page keeps its product
    # titles in structured `result_items`, never in headings or control names,
    # so a `see`/`any_of` naming a real product hard-blocked with "no probed
    # heading or control shows that text" while the probe was holding the
    # caption all along. Verified live on a retail site: both requested
    # products were in result_items, neither was reachable from here.
    # (Captions are truncated to ~60 chars by the probe — _find_text's reverse
    # direction still matches longer titles, and since NOOD_0197 an `any_of`
    # compiles to one disjunctive step regardless, so truncation costs nothing.)
    for it in blk.get("result_items") or []:
        if it.get("caption"):
            texts.append(it["caption"])
    return texts


def _page_blocks(pg: dict) -> list[tuple[dict, str, str | None]]:
    """Provenance-tagged blocks of one probed page: (block, phase, trigger).

    phase is 'initial' | 'reveal' | 'discovered' | 'performed' | 'search'. A
    revealed block the probe reached by AUTOMATIC discovery (auto/discovered)
    is 'discovered' — its controls are never reachable without an explicit
    goal click that opens them. An explicitly-clicked reveal keeps phase
    'reveal' and carries the trigger name so the compiler can require that
    click first.

    NOOD_0208 — 'performed' is the state the probe WALKED to by executing the
    goal's own post-gate actions (probe.perform). It carries no trigger: it is
    reachable because those actions are in the test, so requiring a reveal
    click for it would block the very flow that produced it."""
    blocks: list[tuple[dict, str, str | None]] = [(pg, "initial", None)]
    for rev in pg.get("revealed", []):
        if rev.get("performed"):
            blocks.append((rev, "performed", None))
            continue
        phase = "discovered" if (rev.get("discovered") or rev.get("auto"))\
            else "reveal"
        blocks.append((rev, phase, rev.get("revealed_by")))
    if pg.get("search"):
        blocks.append((pg["search"], "search", None))
        # NOOD_0156 — the page the probe's bound result-pick landed on:
        # reachable at run time only after the pick action re-clicks it.
        if pg["search"].get("picked"):
            blocks.append((pg["search"]["picked"], "picked", None))
    return blocks


def _reveal_click_before(actions: list, action: dict, trigger: str | None) -> bool:
    """True when an explicit click action targeting `trigger` precedes
    `action` — the prerequisite that legitimately makes a revealed control
    reachable at run time. NOOD_0188 — a `hover` counts too: hover menus are
    the other way a control legitimately becomes reachable, and treating them
    as unreachable blocked the exact flow NOOD_0186 added steps for."""
    tn = _norm(trigger)
    if not tn:
        return False
    for x in actions:
        if x is action:
            return False
        if x.get("do") in ("click", "hover"):
            xn = _norm(x.get("target"))
            if xn and (xn == tn or tn in xn or xn in tn):
                return True
    return False


# NOOD_0145 — a login/submit-shaped intent may fall back to THE unique visible
# submit control. Matched against the normalized target only; deliberately
# narrow (no "continue"/"next" — those name non-submitting controls too often).
_SUBMIT_INTENT_RE = re.compile(r"\b(log ?in|sign ?in|sign ?up|register|submit)\b")


def _auth_synonyms(target: str) -> list[str]:
    """The runtime's own auth-verb synonyms ("login" → "sign in"), so goal
    matching and run-time healing agree on what a login intent may resolve to.
    Imported lazily to keep this module importable without the web stack."""
    try:
        from noodle.agents.web.locator import _synonym_candidates
    except Exception:                                    # pragma: no cover
        return []
    return _synonym_candidates(target)


# NOOD_0207 — how many same-named instances still count as ONE control. A
# responsive page renders its header twice (desktop bar + collapsed menu), and
# NOOD_0168 already settled the rule for the mutation path: a FEW same-named
# visible instances are duplicates of one control, MANY are one-per-card. This
# is that ceiling, shared — without it the ambiguity gate below blocked an
# ordinary two-instance header link that resolves and runs green (the
# feature-regression benchmark's tc2 caught exactly that).
_DUPLICATE_CEILING = 3


def _iter_controls(blocks: list):
    for blk, phase, trigger in blocks:
        for c in blk.get("controls", []):
            yield c, phase, trigger


def _contains(t: str, cn: str) -> bool:
    """NOOD_0209 — guarded bidirectional containment for substring matching.

    Bare `t in cn or cn in t` let a control named "e" match nearly every
    target and let "edit" match "credit" (a substring of a word is not that
    word): the resulting ambiguity blockers named controls with nothing to do
    with the request, and each one cost the calling agent a rewrite lap. Two
    guards: a 1-2 character name carries no intent, and a containment must
    land on word boundaries — in BOTH directions, so a "cart" target still
    meets an "add to cart" control. Exact-name matching stays ungated
    upstream: a control genuinely named "OK" still matches "OK"."""
    if not cn or len(cn) < 3:
        return False
    return bool(re.search(rf"\b{re.escape(t)}\b", cn)
                or re.search(rf"\b{re.escape(cn)}\b", t))


def _closest_first(target: str, names: list[str]) -> list[str]:
    """NOOD_0209 — rank candidate names by closeness to the target before any
    list is truncated for display; an arbitrary cut used to lead the blocker
    with the least likely candidate."""
    return sorted(names, key=lambda n: difflib.SequenceMatcher(
        None, (target or "").casefold(), (n or "").casefold()).ratio(),
        reverse=True)


def _near_miss(target: str, blocks: list, kind: str = "control") -> str:
    """NOOD_0207 — ' — did you mean "X"? (probed here: …)', or ''.

    Every unmatched-target blocker was holding the probed vocabulary that
    fixes it and shipped only the problem statement, so the repair was a
    re-probe instead of a one-word edit. The `suggest:` branch already did
    exactly this for typeahead options; this is that pattern, everywhere.

    ponytail: difflib over the probed names, cap 8 — the names ride the
    payload, and a shortlist longer than that is a probe dump, not a hint."""
    # accepts either shape a caller holds: provenance tuples (_page_blocks)
    # or plain block dicts (the check scopes).
    plain = [b[0] if isinstance(b, tuple) else b for b in blocks or ()]
    names = list(dict.fromkeys(
        n for blk in plain for n in _block_texts(blk) if n))
    if not names:
        return ""
    ranked = _closest_first(target, names)
    near = difflib.get_close_matches(target or "", names, 1, 0.6)
    shown = ", ".join(f'"{n}"' for n in ranked[:8])
    return ((f' — did you mean "{near[0]}"?' if near else "")
            + f" (probed {kind}s here: {shown}"
            + (f", +{len(ranked) - 8} more" if len(ranked) > 8 else "") + ")")


# NOOD_0226 — deliberately narrow: an attribute selector, or an id selector
# that cannot be read as a caption. Bare class selectors are NOT here — ".NET
# SDK" and "#1 Best Seller" are control names on real pages, and treating
# either as a selector would block a legitimate target.
_SELECTOR_SHAPED = re.compile(r'^(\[.+\]|#[A-Za-z_][\w-]*)$')


def _locate(target: str, blocks: list, scoped: bool = False,
            pinned: bool = False) \
        -> tuple[dict | None, str | None, str | None, str | None]:
    """(control, phase, trigger, blocking_note) for a goal action target.

    `scoped` — the action carries a `within:` anchor (NOOD_0207), so a
    repeated control is exactly what it means to address; the ambiguity gate
    below is what `within:` is the answer to, and must not fire on it.

    `pinned` — the caller pinned this target in pom_content (NOOD_0212), the
    other way to settle ambiguity; every gate is answered by the selector.

    NOOD_0145 — deterministic match order, replacing first-substring-wins
    (which picked a machine-named lookalike, e.g. "login options toggle btn",
    over the visible "sign in" submit control for a generic "login" target):

      1. exact canonical name — UNLESS the probe captured several distinct
         controls sharing that name (NOOD_0156: repeated per-card controls
         like "Add to cart"), or PROVED one selector matches many nodes
         (NOOD_0207); an unscoped repeated control blocks instead of
         silently acting on whichever instance resolves first
      2. exact runtime auth-synonym name ("login" → "sign in")
      3. login/submit intent: THE unique visible submit control
      4. unique substring match (either direction)
      5. several distinct substring candidates → block as ambiguous (note),
         never guess the first one
    """
    t = _norm(target)
    if not t:
        return None, None, None, None
    # NOOD_0226 — a selector is an exact instruction, so resolve it as one.
    # _norm strips punctuation ('[id="add-to-cart"]' → 'id add to cart'), so
    # the substring pass below matched ANY control merely NAMED "add to cart"
    # and bound ITS selector — a different element, chosen by luck. Measured:
    # a target naming one product page's CTA id resolved to a result card's
    # button, compiled that card's selector into the POM, and ran green for
    # the wrong reason. Exact selector match or nothing.
    if _SELECTOR_SHAPED.match(target.strip()):
        want = target.strip()
        hit = next((x for x in _iter_controls(blocks)
                    if str(x[0].get("selector") or "").strip() == want), None)
        if hit:
            return (*hit, None)
        return None, None, None, (
            "no probed control carries that selector — this is a selector, "
            "not a name, so it is never matched by wording; use the visible "
            "caption the probe reported, or pin the selector in pom_content")
    exact = [(c, phase, trigger) for c, phase, trigger in
             _iter_controls(blocks) if _norm(c.get("name")) == t]
    if exact:
        # Distinct selectors = genuinely different elements sharing one name
        # (one per result card/row). The same control snapshotted twice
        # (identical selector, or no selector captured) stays a unique match.
        sels = {c.get("selector") for c, _, _ in exact if c.get("selector")}
        # NOOD_0207 — the probe already PROVED the ambiguity. A card grid
        # stores ONE representative selector per control family plus
        # `unique: False` / `matches: N` (probe.py:2212), and prints
        # "⚠ selector matches N nodes". N identical nodes collapse to one
        # selector string, so the len(sels) test above scored them a unique
        # match, compiled the POM, reached ready: true — and the run acted on
        # instance 1. Fires only on the probe's own proof, never a heuristic.
        # The ceiling is what keeps this a bug-catcher rather than a tax on
        # every responsive header: 2-3 same-named instances are one control
        # rendered twice and resolve to the same destination; more than that
        # is a per-item family, where instance 1 is a coin flip.
        amb = next((c for c, _, _ in exact if c.get("unique") is False
                    and (c.get("matches") or 0) > _DUPLICATE_CEILING), None)
        if pinned:
            return (*exact[0], None)
        if scoped:
            # NOOD_0222 — the structurally-different gate below exists
            # precisely to say "a within: anchor will not resolve at run
            # time" (NOOD_0209) — but supplying within: skipped it, so the
            # exact goal it warns against authored ready:true and died in
            # the run as "No row containing '<text>' found": a red run per
            # instance of the most expensive ordering there is. Same page,
            # same evidence, decided at author time instead.
            shapes = {re.sub(r"\d+", "N", s) for s in sels}
            if amb is None and len(shapes) > 1:
                return None, None, None, (
                    f"matches {len(exact)} structurally different probed "
                    "controls sharing this exact name (distinct selectors) — "
                    "these are not one control repeated per row/card, so the "
                    "within: anchor will not resolve at run time; drop "
                    "within: and pin the intended instance via pom_content "
                    "(its selector answers \"which one\")")
            return (*exact[0], None)
        if len(sels) > 1 or amb is not None:
            # NOOD_0212 — same NAME and same DESTINATION is not an ambiguity.
            # The ceiling comment above already states the principle ("one
            # control rendered twice ... resolve to the same destination");
            # for links the probe captured the href, so it can be CHECKED
            # rather than assumed from a count. A site whose header renders
            # its nav once for desktop and once for the collapsed menu hit
            # this on every such link, and the advice it printed ("name the
            # instance by nearby unique text") has no answer when both
            # instances sit in chrome with no nearby text of their own.
            # Distinct hrefs stay a block: those really are different pages.
            hrefs = {str(c.get("href") or "").strip() for c, _, _ in exact}
            if (amb is None and len(hrefs) == 1 and "" not in hrefs
                    and all(c.get("kind") == "link" for c, _, _ in exact)):
                c, ph, tr = next((x for x in exact if x[0].get("visible")),
                                 exact[0])
                return ({**c, "collapsed_from": len(exact)}, ph, tr, None)
            n = len(exact) if len(sels) > 1 else amb["matches"]
            # NOOD_0209 — tell the two repeated shapes apart before advising.
            # A per-item FAMILY (one selector shape stamped once per card, or
            # the probe's own unique:False proof) is what `within:` answers.
            # Structurally DIFFERENT controls sharing a label (a page-level
            # control plus per-section ones — distinct selector shapes) are
            # not in rows: `within:` advice there authored ready:true and the
            # run died with "No row containing '<text>' found".
            shapes = {re.sub(r"\d+", "N", s) for s in sels}
            if amb is None and len(shapes) > 1:
                return None, None, None, (
                    f"matches {n} structurally different probed controls "
                    "sharing this exact name (distinct selectors) — these "
                    "are not one control repeated per row/card, so a "
                    "within: anchor will not resolve at run time; instead, "
                    "name the instance by nearby unique text or probe the "
                    "page (--discover) and use the distinguishing control")
            return None, None, None, (
                f"matches {n} probed controls sharing this exact "
                "name — a repeated control; scope the action to one concrete "
                'instance: add within: "<text unique to the intended '
                'row/card>"')
        return (*exact[0], None)
    for alt in _auth_synonyms(t):
        an = _norm(alt)
        for c, phase, trigger in _iter_controls(blocks):
            if _norm(c.get("name")) == an:
                return c, phase, trigger, None
    if _SUBMIT_INTENT_RE.search(t):
        submits = [(c, ph, tr) for c, ph, tr in _iter_controls(blocks)
                   if c.get("submit") and c.get("visible", True)]
        if len({_norm(c.get("name")) for c, _, _ in submits}) == 1:
            return (*submits[0], None)
    subs, names = [], []
    for c, phase, trigger in _iter_controls(blocks):
        cn = _norm(c.get("name"))
        if _contains(t, cn):                     # NOOD_0209 — guarded
            subs.append((c, phase, trigger))
            if cn not in names:
                names.append(cn)
    if len(names) == 1:
        return (*subs[0], None)
    if len(names) > 1:
        # NOOD_0200 — name the candidate it would have picked and why it
        # won't: "ambiguous" alone cost the reviewed session a full
        # round-trip guessing which spelling the engine wanted.
        # NOOD_0209 — likeliest first: the truncated list is what the calling
        # agent acts on, and an arbitrary cut misdirected the repair.
        ranked = _closest_first(t, names)
        return None, None, None, (
            "ambiguous — matches " + ", ".join(f'"{n}"' for n in ranked[:4])
            + ("…" if len(ranked) > 4 else "")
            + f'; not guessing between them — if you meant "{ranked[0]}", '
            "say exactly that, otherwise use the exact probed control name")
    return None, None, None, None


def _observed_count(rsum: dict) -> int | None:
    """The number the results-summary asserts, from its parsed `count` or, as a
    fallback, the first number in its text (NOOD_0141 — US and European
    formats). None when neither yields one."""
    n = rsum.get("count")
    if isinstance(n, int) and not isinstance(n, bool):
        return n
    from noodle.agents.web.probe import parse_number
    parsed = parse_number(str(rsum.get("text") or ""))
    return int(parsed) if parsed is not None else None


def _find_text(needle: str, blocks: list[dict]) -> str | None:
    """The probed page shows the requested text — either in full (`n in tn`),
    or as a shortened rendering of it (`tn in n`, a truncated caption).

    NOOD_0195 — the reverse direction needs a floor. Bare character
    containment let a control literally named "a" satisfy a 41-character
    product title, and evidence() recorded that as proven: a live goal reached
    `ready: true` on a one-letter match. A fragment must be at least two whole
    words of the needle; anything a single word long is noise, and a
    single-word needle is provable by the forward direction anyway."""
    n = _norm(needle)
    if not n:
        return None
    for blk in blocks:
        for t in _block_texts(blk):
            tn = _norm(t)
            if n in tn or (tn in n and len(tn.split()) >= 2):
                return t
    return None


def _shared_phrase(needle: str, blocks: list[dict],
                   floor: int = 2) -> tuple[str, str] | None:
    """NOOD_0199 — (longest run of the needle a probed text contains, that
    text), or None. The decorated-text case: a human asks to verify "Weekly
    Flyer image" and the page renders "Banner 2 of 8 View the Weekly Flyer
    now." Neither contains the other, so `_find_text` blocks — on a page that
    demonstrably shows what was asked about. This is the "assert the smallest
    stable substring" rule the workspace already tells authors to apply, done
    by the engine WITH provenance instead of refusing.

    ponytail: contiguous word runs, longest first, floor of two whole words —
    the same floor `_find_text` uses against one-letter matches. Word-level
    fuzzy matching (stemming, edit distance) is the upgrade path if real
    prompts turn out to need it; nothing measured has."""
    words = (needle or "").split()
    if len(words) <= floor:
        return None                # a one/two-word ask has no shorter form
    texts = [(t, _norm(t)) for blk in blocks for t in _block_texts(blk)]
    for size in range(len(words) - 1, floor - 1, -1):
        for start in range(len(words) - size + 1):
            run = " ".join(words[start:start + size])
            rn = _norm(run)
            if not rn:
                continue
            for t, tn in texts:
                if rn in tn:
                    return run, t
    return None


def _find_control(target: str, blocks: list[dict]) -> dict | None:
    t = _norm(target)
    for blk in blocks:
        for c in blk.get("controls", []):
            cn = _norm(c.get("name"))
            if t and (t == cn or t in cn or cn in t):
                return c
    return None


def _check_scope(check: dict, goal: dict) -> str:
    """'search' when the check observes the page the search/suggest landed on,
    else 'initial'.

    NOOD_0195 — `suggest` counts, not just `search`: probe_args now follows the
    suggestion, so that page is real evidence. And an UNANCHORED check scopes
    there too: NOOD_0158 made it assert the END state and compile_goal emits it
    after every action, but the evidence pass still matched it against the
    landing page — so it was checked against one page and run against another.
    An `after:` anchor before the search still scopes to the landing page,
    which is what `after: start` is for."""
    actions = goal.get("actions") or []
    search_i = next((i for i, a in enumerate(actions)
                     if a["do"] in ("search", "suggest")), None)
    if search_i is None:
        return "initial"
    after = check.get("after")
    if after is None:
        return "search"
    anchor_i = next((i for i, a in enumerate(actions)
                     if a.get("id") == after), -1)
    return "search" if anchor_i >= search_i else "initial"


def evidence(goal: dict, probe_result: dict, pinned=frozenset()) -> dict:
    """Match every requested action/check against what the probe proved.

    `pinned` — normalized POM keys the caller supplied in pom_content. A
    pinned target has already been disambiguated by hand, so the ambiguity
    gate stands down for it (NOOD_0212).
    Returns {blocking, proven, runtime_asserted, permission_prompts,
    popups_closed, results_summary, controls}. An unproven request BLOCKS — it
    is never dropped or broadened — EXCEPT a check anchored after data the probe
    never entered, which becomes a `runtime_asserted` check the run must pass."""
    def _empty(blocking: list[str]) -> dict:
        return {"blocking": blocking,
                "proven": {}, "runtime_asserted": [], "permission_prompts": [],
                "popups_closed": 0, "results_summary": None, "controls": {},
                "bound_targets": {}, "resolved_controls": {},
                "mutation_plans": {}, "navigation_health": [],
                "revealed_headings": {}, "headings": [], "narrowed": [],
                "warnings": []}

    pages = probe_result.get("pages") or []
    if not pages:
        errs = "; ".join(e.get("error", "?") for e in
                         probe_result.get("errors", [])) or "no pages probed"
        return _empty([f"probe returned no page evidence: {errs}"])
    # NOOD_0156 — ordered navigation contract: every requested URL must have
    # loaded, and the goal's actions/checks are proven against the LAST page
    # (the one the flow acts on). A dropped URL blocks before any authoring.
    nav = navigation_urls(goal)
    nav_health: list[dict] = []
    nav_block = None
    if nav:
        if len(pages) < len(nav):
            errs = "; ".join(f'{e.get("url", "?")}: {e.get("error", "?")}'
                             for e in probe_result.get("errors", []))
            return _empty([
                f"navigation: only {len(pages)} of {len(nav)} requested "
                "URLs loaded" + (f" — {errs}" if errs else "")])
        pg = pages[len(nav) - 1]
        # NOOD_0169 — navigation health: requested setup URLs are preserved
        # even when broken (with a warning — the user asked for them; their
        # controls never join the action page's vocabulary, which is built
        # from the LAST page only), but a broken FINAL action page blocks:
        # actions must never be authored against a 404.
        for i, (u, p) in enumerate(zip(nav, pages)):
            status = p.get("http_status")
            entry = {"url": u,
                     "role": "action" if i == len(nav) - 1 else "setup",
                     "status": status, "title": p.get("title", "")}
            if isinstance(status, int) and status >= 400:
                if entry["role"] == "setup":
                    entry["warning"] = (
                        f"setup URL returned HTTP {status} — preserved as "
                        "requested; setup-page controls never enter the "
                        "action page's vocabulary")
                else:
                    nav_block = (
                        f"navigation: the final action page returned HTTP "
                        f"{status} ({u}) — actions are never authored "
                        "against a broken page; fix the URL or app state")
            nav_health.append(entry)
    else:
        pg = pages[0]
    blocks = _page_blocks(pg)
    actions = goal.get("actions") or []
    # Reachable controls for the compiler — initial + explicit reveals + search
    # (+ the picked landed page, NOOD_0156). Discovered/auto blocks are
    # excluded: found by the probe clicking around, not by a requested action,
    # so never authored as reachable.
    controls = {}
    for blk, phase, _ in blocks:
        if phase == "discovered":
            continue
        for c in blk.get("controls", []):
            controls.setdefault(_norm(c.get("name")), c)
    # NOOD_0208 — 'performed' joins the check scope, and that IS the point of
    # the opt-in: those blocks are the pages the flow walks to AFTER the
    # mutation, so a check on the confirmation wording is proven here instead
    # of guessed and paid for with a red run.
    initial_scope = [blk for blk, ph, _ in blocks
                     if ph in ("initial", "reveal", "performed")]
    search_scope = [blk for blk, ph, _ in blocks if ph == "search"]
    picked_blk = next((blk for blk, ph, _ in blocks if ph == "picked"), None)
    # NOOD_0195 — the probe's --expect verdicts: an exact full-text search of
    # the page the probe ENDED on, so they only ever answer for a check that
    # observes that page (scope 'search'). Where a structured capture is lossy
    # this is the authoritative source, and a FOUND is full-render proof — the
    # string is in the rendered text, entire.
    expect_found = {_norm(e.get("text")) for e in (pg.get("expect") or [])
                    if e.get("found")}
    searched = any(a["do"] in ("search", "suggest") for a in actions)

    blocking, proven, runtime, bound, resolved = [], {}, [], {}, {}
    warnings: list[str] = []       # NOOD_0208 — real but unproven risks
    # NOOD_0200 — provenance per proven CHECK: which probe phase's page the
    # match came from (initial / search). Recording it is the structural
    # guard against the end-state false positive: a check whose compiled
    # scope and proven phase drift apart is visible in the payload (and
    # pinned by tests) instead of shipping a green that verified nothing.
    proven_phase: dict = {}
    narrowed: list[dict] = []      # NOOD_0199 — see-checks cut to their
                                   # probe-proven substring, never silently
    mplans: dict[str, dict] = {}
    # NOOD_0226 — the last action index the probe has page evidence for; read
    # once, used by every gate below so they cannot drift apart.
    reach = _reach_gate(actions)
    if nav_block:
        blocking.append(nav_block)
    for i, a in enumerate(actions):
        if a["do"] == "api":
            # NOOD_0192 — nothing for a page probe to prove: the probe drives
            # a browser, the call is HTTP. Its correctness is proven at run
            # time by the status/body assertion that must accompany it, so it
            # neither blocks authoring nor claims probe evidence.
            continue
        if a["do"] == "add_to":
            # NOOD_0156 — semantic mutation lowering: resolve the requested
            # "add the picked item to <destination>" to the exact probed
            # mutation control on the landed page — plus at most one
            # probe-PROVEN prerequisite reveal (recorded by the probe with a
            # before/after delta). No proven chain = block; a candidate
            # prerequisite is never compiled on a guess.
            aid = a.get("id") or f"add_to:{a.get('destination', '')}"
            src = a.get("item_from", "")
            scope = str(a.get("within") or "").strip()
            if scope and not src:
                # NOOD_0207 — the searchless shape: no pick, so no landed
                # page; the mutation control lives on the CURRENT page,
                # repeated once per card, and `within` says which card.
                # NOOD_0212 — cards' own actions join the flat controls, and
                # the hint below enumerates THE SAME list: it used to print
                # `blk["controls"]` in block order, so a searchless add_to on
                # a results page advertised the landing page's chrome ("skip
                # to main content", "play", "pause") as what the page offers.
                cand = [c for blk, _, _ in blocks
                        for c in block_mutation_candidates(blk)]
                ctrl, why = mutation_control(cand, a["destination"],
                                             scoped=True)
                if ctrl is None:
                    if reach is not None and i > reach:
                        # NOOD_0226 — past the probe's reach there is no
                        # mutation control to find: the page carrying it was
                        # never loaded. Saying "the page offers: username,
                        # password, login" invites a repair onto the SIGN-IN
                        # page, which is never right. `add_to` cannot defer
                        # like a click can — it lowers to a control whose name
                        # only the author knows — so it still blocks, but with
                        # the two repairs that actually work.
                        blocking.append(
                            f'add_to "{a["destination"]}": the mutation '
                            f"control is behind step {reach + 1} "
                            f'({_reach_label(actions, reach)}), which the '
                            "probe does not perform — so it was never probed "
                            "and no candidate here is one. Name the control "
                            'instead: click "<its name>" with within: '
                            f'"{scope}", or opt in with probe: {{perform: '
                            "true}} to let the probe walk the flow first")
                        continue
                    names = list(dict.fromkeys(
                        c["name"] for c in cand if c.get("name")))[:8]
                    blocking.append(
                        f'add_to "{a["destination"]}": {why}'
                        + ("; the page offers: "
                           + ", ".join(repr(n) for n in names) if names else ""))
                    continue
                if not _find_text(scope, [blk for blk, _, _ in blocks]):
                    blocking.append(
                        f'add_to within "{scope}": no probed text on this page '
                        "identifies that item"
                        + _near_miss(scope, blocks, "text"))
                    continue
                # NOOD_0208 — with probe.perform the control was CLICKED, so
                # "does it mutate or merely navigate" is answered instead of
                # assumed. A click that neither changed the page nor produced
                # a delta did nothing: compiling it would fail one step later
                # on an item that was never added, blaming the next control.
                proof = pg.get("mutation_proof") or {}
                if proof and not proof.get("delta") \
                        and not proof.get("navigated"):
                    blocking.append(
                        f'add_to "{a["destination"]}": the probe clicked '
                        f'"{proof.get("control")}" and the page did not '
                        "change at all — that control does not perform the "
                        "mutation; name the control that does, or add the "
                        "step that opens it first")
                    continue
                if not proof and navigation_shaped(ctrl):
                    # unproven and navigation-shaped: say so, never refuse —
                    # plenty of real mutate-controls are anchors that POST.
                    warnings.append(
                        f'add_to "{a["destination"]}": "{ctrl["name"]}" is a '
                        "link to another page, and qualified as the mutation "
                        "control by its NAME alone — if it only navigates, "
                        "this run fails at the next step. Prove it with "
                        "probe: {perform: true}.")
                mplans[aid] = {"prerequisite": None, "control": ctrl,
                               "within": scope,
                               "evidence": "mutation control observed on the "
                                           "current page, scoped by `within`"}
                proven[f"add_to:{aid}"] = ctrl["name"]
                continue
            if src not in bound:
                blocking.append(
                    f'add_to "{a.get("destination", "")}": item_from '
                    f"{src!r} has no bound result — the pick did not bind")
                continue
            picked = (pg.get("search") or {}).get("picked") or {}
            plan, why = picked.get("mutation_path"), None
            if not plan or not plan.get("control"):
                ctrl, why = mutation_control(picked.get("controls") or [],
                                             a["destination"])
                if ctrl is not None:
                    plan = {"prerequisite": None, "control": ctrl,
                            "evidence": "mutation control observed on the "
                                        "landed page"}
            if not plan or not plan.get("control"):
                # NOOD_0212 — the landed page has no add control, but the
                # RESULTS page card the pick came from may carry its own. On
                # most retail grids that card button IS the add-to-cart, and
                # opening the product first is a detour the goal never asked
                # for: the implied pick (NOOD_0168) inserted the navigation,
                # then the prover looked for the button on the page that
                # navigation left. Bind the card's action, scoped to the very
                # caption the pick bound, and compile_goal drops the detour.
                srch = (pg.get("search") or {})
                cap = (bound.get(src) or {}).get("caption")
                # A pick with no `target` means "any matching result" — the
                # implied pick NOOD_0168 inserts is exactly that shape. There,
                # a card the probe happened to click but which offers no add
                # (in-store-only stock is the common one) must not decide the
                # flow: any addable card satisfies the goal equally, so bind
                # one and RE-BIND the caption to it, keeping the assertion and
                # the mutation on the same product. A pick that NAMES its
                # target keeps the strict match — silently adding a different
                # product would answer a question the author didn't ask.
                pick_act = next((x for x in actions if x.get("do") == "pick"
                                 and (x.get("id") or "result") == src), None)
                any_result = not (pick_act or {}).get("target")
                cards = item_actions(srch)
                if not any_result and cap:
                    cards = [c for c in cards
                             if _norm(c.get("item_caption")) == _norm(cap)]
                ctrl2, _why2 = mutation_control(cards, a["destination"])
                if ctrl2 is not None:
                    chosen = ctrl2.get("item_caption") or cap
                    if chosen and chosen != cap and src in bound:
                        bound[src]["caption"] = chosen
                        bound[src]["evidence"] = (
                            "probe:search-results (the card carrying the "
                            "add action)")
                        proven[f"pick:{src}"] = chosen
                    plan = {"prerequisite": None, "control": ctrl2,
                            "on_results": True, "within": chosen,
                            "evidence": "the picked result's own card action "
                                        "on the results page"}
            if not plan or not plan.get("control"):
                # NOOD_0167 — name what the landed page DOES offer: a
                # reviewed session dead-ended on this generic blocker while
                # the page's tiles carried a differently-named control the
                # whole time. Vocabulary from the probe's own evidence, so
                # the reader's next move is a rename, not a re-probe.
                names = list(dict.fromkeys(
                    c["name"] for c in ((picked.get("controls") or [])
                                        + item_actions(picked))
                    if c.get("name")))[:8]
                blocking.append(
                    f'add_to "{a["destination"]}": no proven mutation path '
                    "on the landed page, and the picked result's own card "
                    "carries no add-shaped action either"
                    + (f" — {why}" if why else "")
                    + " — fix the probe evidence; an unproven intermediate "
                    "step is never guessed"
                    + ("; the landed page offers: "
                       + ", ".join(repr(n) for n in names) if names else ""))
                continue
            mplans[aid] = plan
            proven[f"add_to:{aid}"] = plan["control"]["name"]
            continue
        if a["do"] == "pick":
            # NOOD_0156 — the probe already bound + clicked ONE result
            # (bind_result); this pass records the binding as a bound target
            # with probe provenance, or blocks — never guesses a caption.
            sr = pg.get("search") or {}
            aid = a.get("id") or "result"
            if sr.get("pick_warning"):
                blocking.append(f"pick: {sr['pick_warning']}")
            elif not sr:
                blocking.append("pick: the probe performed no search — "
                                "nothing to pick a result from")
            elif not sr.get("picked"):
                blocking.append("pick: the probe captured no picked-result "
                                "evidence — no landed-page snapshot to bind")
            else:
                cap = sr["picked"].get("picked_caption", "")
                proven[f"pick:{aid}"] = cap
                bound[aid] = {
                    "caption": cap,
                    "selector": sr["picked"].get("picked_selector", ""),
                    "requested_as": a.get("target") or "any matching result",
                    "evidence": "probe:search-results (clicked and landed)"}
            continue
        if a["do"] == "search":
            if pg.get("search_warning"):
                blocking.append(f"search: {pg['search_warning']}")
            elif pg.get("search"):
                proven["search"] = pg["search"]["term"]
            else:
                blocking.append("search: the probe performed no search — "
                                "no results-page evidence")
            continue
        if a["do"] == "suggest":
            # NOOD_0141 — the requested option must be among the CAPTURED
            # suggestions; the canonical page spelling (exact match first,
            # else substring) is what the compiler emits, so the step clicks
            # the string that actually renders, not the prompt's paraphrase.
            if pg.get("suggest_warning"):
                blocking.append(f"suggest: {pg['suggest_warning']}")
                continue
            sg = pg.get("suggest")
            if not sg:
                blocking.append("suggest: the probe captured no typeahead — "
                                "no suggestion evidence")
                continue
            want = _norm(a["option"])
            canon = next((s for s in sg["suggestions"]
                          if _norm(s) == want), None) \
                or next((s for s in sg["suggestions"]
                         if want in _norm(s) or _norm(s) in want), None)
            if canon is None:
                # NOOD_0195 — name the nearest captured spelling. A one-edit
                # miss is the commonest cause (a site's typeahead is misspelled
                # and the prompt, or the agent "correcting" it, is not), and
                # without the pointer the repair is a re-derivation from an
                # eight-item list rather than a one-word edit.
                near = difflib.get_close_matches(a["option"],
                                                 sg["suggestions"], 1, 0.6)
                blocking.append(
                    f'suggest: option {a["option"]!r} not among the captured '
                    f'suggestions {sg["suggestions"][:8]}'
                    + (f' — did you mean {near[0]!r}?' if near else ""))
            else:
                proven[f'suggest:{a["term"]}'] = canon
            continue
        # NOOD_0188 — actions that name NO control (press_key, go_back) have
        # nothing for the probe to resolve: they act on the focused element or
        # on history. The generic path below keys everything on a["target"],
        # so without this they raised KeyError mid-author.
        if not a.get("target"):
            continue
        after_pick = any(x.get("do") == "pick" for x in actions[:i])
        # NOOD_0207 — a `within:` anchor says the repeated control IS the
        # intent, so the ambiguity gate (which `within` exists to answer)
        # must not fire on it.
        scope = str(a.get("within") or "").strip()
        # NOOD_0212 — `within:` means "the row/card containing this TEXT", and
        # supplying it SKIPS the ambiguity gate (see _locate's `scoped` arm).
        # An unverified scope therefore authored ready:true and died at run
        # time with "No row containing '<text>' found" — the most expensive
        # ordering there is. Region words (header, nav, footer) are the common
        # miss: they name a part of the page, which `within:` cannot express.
        # Deferred on the same terms as a missing control below: after a pick,
        # or past the probe's reach, the probe never saw this page at all.
        if (scope and not after_pick and (reach is None or i <= reach)
                and not _find_text(scope, [blk for blk, _, _ in blocks])):
            blocking.append(
                f'{a["do"]} "{a["target"]}" within "{scope}": no probed text '
                "on this page identifies that row or card — `within:` scopes "
                "to the TEXT of one repeated row/card and cannot name a "
                "region of the page (header, nav, footer)"
                + _near_miss(scope, blocks, "text"))
            continue
        ctrl = phase = trigger = note = None
        # NOOD_0212 — a caller who pinned this target in pom_content has
        # ALREADY answered "which of the two?", the only question the
        # ambiguity gate asks. Blocking anyway left the gate's own advice —
        # "use the distinguishing control" — with nothing behind it: handing
        # over the selector IS that, and there was no other way to say so.
        # NOOD_0222 — within: and a pom_content pin settle ambiguity on
        # different terms (a pin answers every gate; within: only per-row
        # repetition), so _locate now takes them apart.
        is_pinned = _norm(a.get("target")) in pinned
        if after_pick and picked_blk is not None:
            # NOOD_0156 — an action after the pick happens on the landed page:
            # resolve there FIRST, so the landed page's single "Add to cart"
            # wins over the results page's repeated per-card twins.
            ctrl, phase, trigger, note = _locate(
                a["target"], [(picked_blk, "picked", None)], bool(scope),
                is_pinned)
        if ctrl is None and note is None:
            ctrl, phase, trigger, note = _locate(a["target"], blocks,
                                                 bool(scope), is_pinned)
        if ctrl is None:
            # NOOD_0207 — past the probe's reach it never snapshotted this
            # page at all, so "no probed control matches" is a fact about the
            # probe, not the app: defer to the run, as checks in the same
            # position already are. Only for a plain missing name — a stated
            # ambiguity/reachability `note` is real evidence and still blocks.
            # NOOD_0226 — `reach` replaces the evidence gate here. It was the
            # only boundary, and it recognises three state-writing verbs, so a
            # flow whose page change is an ordinary COMMIT CLICK (sign in,
            # continue, place order) had no boundary at all and every step
            # after it blocked.
            if note is None and reach is not None and i > reach:
                # No probe evidence to record, and none to invent: the step
                # compiles with the author's own wording and the runtime's
                # find() resolves it — proven by the run or red by the run.
                proven_phase[f'{a["do"]}:{a["target"]}'] = "runtime"
                continue
            # NOOD_0226 — after a pick, the near miss comes off the LANDED
            # page, which is the only page this step runs on. Ranking the
            # whole probe meant a blocked add-to-cart on a product page was
            # answered with the RESULTS page's filter names ("electric fans",
            # "sesame street (2)"), measured on a 1000-control listing: an
            # agent told to read `blocking` as probe evidence (NOOD_0214) is
            # then holding a shortlist from a page it never visits, and every
            # candidate in it is a wrong repair. Falls back to the full probe
            # only when the landed page yielded no vocabulary at all.
            scope_blocks = ([(picked_blk, "picked", None)]
                            if after_pick and picked_blk is not None
                            and _block_texts(picked_blk) else blocks)
            blocking.append(f'{a["do"]} "{a["target"]}": '
                            + (note or "no probed control matches that name"
                               + _near_miss(a["target"], scope_blocks))
                            + _reach_clause(actions, reach, i))
            continue
        if ctrl.get("collapsed_from"):
            # NOOD_0212 — resolved, not guessed: every instance links to the
            # same place. Said out loud because the compiled POM pins ONE of
            # them, and a reader comparing the feature to the page should not
            # have to rediscover why the other was safe to ignore.
            warnings.append(
                f'{a["do"]} "{a["target"]}": rendered '
                f'{ctrl["collapsed_from"]} times (responsive header/nav '
                "duplicates), every instance linking to the same "
                "destination — bound the visible one")
        if scope and not _find_text(scope, [blk for blk, _, _ in blocks]):
            blocking.append(
                f'{a["do"]} within "{scope}": no probed text on this page '
                "identifies that row/card" + _near_miss(scope, blocks, "text"))
            continue
        if phase == "picked" and not after_pick:
            blocking.append(
                f'{a["do"]} "{a["target"]}": only reachable on the page a '
                "result pick lands on — add a pick action before this one")
            continue
        if phase in ("reveal", "discovered") and \
                not _reveal_click_before(actions, a, trigger):
            # Reachable ONLY when an explicit click opens `trigger` first (§2
            # rule 1). Automatic discovery alone never makes a hidden control
            # reachable (rule 3) — say so precisely.
            if phase == "discovered":
                blocking.append(
                    f'{a["do"]} "{a["target"]}": only found via automatic '
                    f'discovery (revealed by "{trigger}"), not by a requested '
                    "action — add an explicit click that opens it before this "
                    "action")
            else:
                blocking.append(
                    f'{a["do"]} "{a["target"]}": hidden until "{trigger}" is '
                    "opened — add a click on it before this action")
            continue
        proven[f'{a["do"]}:{a["target"]}'] = ctrl["name"]
        # NOOD_0156 — the exact control this pass resolved (scoped resolution:
        # a landed-page control wins over a results-page twin sharing its
        # name); the compiler reuses THIS dict so the POM selector can't
        # silently re-resolve to the wrong instance.
        resolved[f'{a["do"]}:{a["target"]}'] = ctrl
        if a["do"] == "select" and ctrl.get("options"):
            if not any(_norm(a["option"]) == _norm(o) for o in ctrl["options"]):
                blocking.append(
                    f'select "{a["target"]}": option {a["option"]!r} not among '
                    f'the enumerated options {ctrl["options"][:8]}')
    rsum = (pg.get("search") or {}).get("results_summary")
    # NOOD_0156 — zero search results block authoring outright: "There are 0
    # results available" is missing evidence, and missing evidence never
    # becomes a guess (the NOOD_0156 session authored a full add-to-cart
    # flow on top of exactly this).
    if rsum is not None and any(a.get("do") == "search" for a in actions):
        obs = _observed_count(rsum)
        if obs == 0:
            term = next((a.get("term") for a in actions
                         if a.get("do") == "search"), "")
            blocking.append(
                f'search "{term}": the probe observed 0 results '
                f'({rsum.get("text")!r}) — authoring against zero search '
                "evidence is blocked; change the term or fix the search flow "
                "first")
    gate = _runtime_gate(actions)
    # NOOD_0208 — when the probe PERFORMED the flow, the pages past the gate
    # are snapshotted, so the checks on them get proven here rather than
    # deferred. `performed` is read off the evidence, not the goal: the flag
    # is a request, a performed block is the proof it was honoured.
    performed = any(ph == "performed" for _, ph, _ in blocks)
    beyond_reach = _beyond_probe_reach(actions, performed)
    if performed:
        gate = None
    captions = {k: v["caption"] for k, v in bound.items()}
    for i, c in enumerate(goal.get("checks") or []):
        if "status" in c or "response_contains" in c or "json" in c \
                or "schema" in c:
            # NOOD_0192 — same reason as the api action: runtime-proven by
            # the REST client, never by the page probe (json: NOOD_0201,
            # schema: NOOD_0216).
            runtime.append(_check_step(c)[0])
            continue
        if "item_in_destination" in c:
            # NOOD_0156 — identity in the destination is always runtime-proven
            # (the probe never mutates state), but its INPUTS are validated
            # here: the binding must exist, and a named destination must be a
            # probed control (the observation click has provenance or blocks).
            src = c.get("expected_from", "")
            if src not in captions:
                blocking.append(
                    f"check item_in_destination: expected_from {src!r} has no "
                    "bound result caption — the pick did not bind")
                continue
            dest = c.get("item_in_destination") or ""
            if dest:
                dctrl, dphase, _, dnote = _locate(dest, blocks)
                if dctrl is None:
                    blocking.append(
                        f'check item_in_destination "{dest}": '
                        + (dnote or "no probed control opens that "
                                    "destination — cannot verify there"))
                    continue
                proven[f"destination:{dest}"] = dctrl["name"]
                proven_phase[f"destination:{dest}"] = dphase or "initial"
            runtime.append(_check_step(c, captions)[0])
            continue
        after = c.get("after")
        anchor_i = next((j for j, a in enumerate(actions)
                         if a.get("id") == after), -1) if after is not None else -1
        if gate is not None and (anchor_i >= gate
                                 or (after is None and beyond_reach)):
            # Anchored after data the probe never entered — the probe cannot
            # honestly prove it; the run must. Preserved verbatim, never dropped.
            #
            # NOOD_0200 — an UNANCHORED check is the END state (NOOD_0158
            # compiles it after the last action). When the goal's own actions
            # carry the page past the probe's reach, the probe holds no
            # evidence for that page at ALL, so matching the check against the
            # landing snapshot was wrong in both directions: it blocked text
            # that only exists downstream, and it "proved" text that merely
            # happens to appear on the landing page (footer/nav wording) while
            # the compiled step asserts it somewhere else entirely. Route it
            # to the run, which is the only honest witness. `after: start`
            # still pins a check to the landing page.
            runtime.append(_check_step(c)[0])
            continue
        if "field" in c:
            # NOOD_0156 — a field-shows-value check is always runtime-proven:
            # the probe never types data, so there is nothing to prove it by.
            runtime.append(_check_step(c)[0])
            continue
        if "not_see" in c or "url_contains" in c or "page_status" in c:
            # NOOD_0188 — absence and landing-URL are runtime-only by nature:
            # a probe snapshot cannot prove a thing is ABSENT (it may simply
            # not have rendered yet), and the probe never follows the flow to
            # its landing URL. Blocking on them would be a false negative.
            runtime.append(_check_step(c)[0])
            continue
        at_end = _check_scope(c, goal) == "search"
        scope = search_scope if at_end else initial_scope
        # An expect verdict answers only for the page the probe ended on —
        # the search landing page when the goal searches, else the initial
        # page (NOOD_0196). Only a check anchored BEFORE a search must ignore
        # it; discarding it for every non-search goal blocked body text the
        # probe had literally proven, because the structured capture carries
        # headings and controls, not prose.
        expect = expect_found if at_end or not searched else set()
        if "see" in c:
            hit = c["see"] if _norm(c["see"]) in expect \
                else _find_text(c["see"], scope)
            if hit is None and (nar := _shared_phrase(c["see"], scope)):
                # NOOD_0199 — narrow to the probe-proven part rather than
                # block. The check dict is rewritten in place ON PURPOSE: the
                # compiled step must assert what the evidence supports, and
                # `narrowed` carries the change into the payload's warnings so
                # it is never a silent weakening.
                run, hit = nar
                narrowed.append({"from": c["see"], "to": run, "probed": hit})
                c["see"] = run
            if hit is None:
                # NOOD_0200 — every blocking entry names a legal next input:
                # a bare rejection reads as "your text is wrong" when the
                # real meaning may be "wrong page", and the one legal escape
                # was undiscoverable (the reviewed session dropped four TRUE
                # assertions over exactly this).
                blocking.append(
                    f'check "{c["see"]}": no probed heading or control shows '
                    "that text on the page it is scoped to — fix the text to "
                    "probed evidence, anchor it to the page it belongs to "
                    "(`after: <action id>`, `after: start` for the landing "
                    "page), or leave it unanchored on a flow whose later "
                    "actions only the run performs (the run then proves it)"
                    # NOOD_0207 — three named repairs still cost a lap when
                    # none of them says what the page DOES show.
                    + _near_miss(c["see"], scope, "text"))
            else:
                # NOOD_0220 — prove and compile must agree. `_find_text` also
                # matches the REVERSE direction (the page renders a shorter
                # form than the ask: "BeanCounter ERP banner" proven by
                # "BeanCounter ERP"), and the runtime asserts by SUBSTRING —
                # so compiling the original wording asserts text the page
                # does not render. The payload said proven, the run went red,
                # and the reader paid a lap to discover the engine's own
                # evidence disagreed with its own Gherkin. Narrow to what was
                # actually proven, with the same NOOD_0199 provenance: never
                # silent, and the invariant is now checkable — every compiled
                # `see` is a substring of the text that proved it.
                if _norm(c["see"]) not in _norm(hit):
                    narrowed.append({"from": c["see"], "to": hit,
                                     "probed": hit})
                    c["see"] = hit
                proven[f"see:{c['see']}"] = hit
                proven_phase[f"see:{c['see']}"] = \
                    "search" if at_end else "initial"
        elif "count" in c:
            want = c.get("min", 1)
            if rsum is None:
                blocking.append(f'check count "{c["count"]}": the probe found '
                                "no results-summary element")
            else:
                obs = _observed_count(rsum)
                if obs is None:
                    blocking.append(
                        f'check count "{c["count"]}": unable to parse an observed '
                        f'count from the summary {rsum.get("text")!r}')
                elif obs < want:
                    blocking.append(
                        f'check count "{c["count"]}": probe observed {obs}, below '
                        f"the requested minimum {want}")
                else:
                    proven[f"count:{c['count']}"] = rsum["text"]
                    proven_phase[f"count:{c['count']}"] = "search"
        else:  # any_of — distinct matching alternatives, not one match ≥ min
            want = c.get("min", 1)
            blks = scope or initial_scope
            texts = set()
            for alt in c["any_of"]:
                hit = alt if _norm(alt) in expect else _find_text(alt, blks)
                if hit is not None:
                    # NOOD_0220 — deliberately NOT narrowed, unlike the `see`
                    # arm above. NOOD_0195 measured the difference: an any_of
                    # runs over RESULT TITLES, whose probe capture truncates,
                    # so a short probed form is usually the capture being
                    # lossy rather than the page rendering less. Narrowing
                    # there would weaken a correct assertion; the disjunction
                    # stays whole and the run records which member rendered.
                    texts.add(_norm(hit))
            if len(texts) >= want:
                # NOOD_0197 — the proven members feed ONE disjunctive step
                # (`sees any of …`); the NOOD_0195 per-member literal expansion
                # is gone — it compiled "A or B" into "A and B".
                proven[f"any_of[{i}]"] = sorted(texts)
                proven_phase[f"any_of[{i}]"] = \
                    "search" if at_end else "initial"
            else:
                blocking.append(
                    "check any_of " + "/".join(c["any_of"])
                    + f": {len(texts)} distinct alternative(s) in the probed "
                    f"evidence, below the requested minimum {want}")
    # NOOD_0156 — heading evidence for postcondition synthesis: what a click
    # provably revealed (keyed by normalized trigger name), and the probed
    # headings overall (suggestion material when synthesis has to block).
    revealed_headings: dict[str, list[str]] = {}
    for blk, phase, trig in blocks:
        if phase == "reveal" and trig:
            heads = [h for h in blk.get("headings", []) if str(h).strip()]
            if heads:
                revealed_headings.setdefault(_norm(trig), []).extend(heads)
    headings = [h for blk, ph, _ in blocks if ph in ("initial", "reveal", "search")
                for h in blk.get("headings", []) if str(h).strip()]
    return {"blocking": blocking, "proven": proven,
            "proven_phase": proven_phase, "runtime_asserted": runtime,
            "permission_prompts": pg.get("permission_prompts", []),
            "popups_closed": pg.get("popups_closed", 0),
            "results_summary": rsum, "controls": controls,
            "bound_targets": bound, "resolved_controls": resolved,
            "mutation_plans": mplans, "navigation_health": nav_health,
            "revealed_headings": revealed_headings, "headings": headings,
            "narrowed": narrowed,
            # NOOD_0208 — risks that are real but unproven. A block would be a
            # heuristic refusing a working flow (NOOD_0207's lesson); silence
            # would be the diagnose-without-repair defect. This is the third
            # option: say it, ship it, name the opt-in that settles it.
            "warnings": warnings}


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
    if c.get("evidence") == "screenshot":
        body += " ( take a screenshot )"
    elif c.get("evidence") == "none":
        # NOOD_0225 — the opt-out has to be ON the step. The run-wide default
        # ('last') shoots whichever step ends the scenario, so a check that
        # declined a picture and happened to land last still got one.
        body += " ( no screenshot )"
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

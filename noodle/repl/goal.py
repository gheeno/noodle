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
import re

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
              "navigation"}
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
                "add_to": {"do", "id", "item_from", "destination"},
                "click": {"do", "id", "target"},
                "enter": {"do", "id", "target", "value"},
                "select": {"do", "id", "target", "option"}}
_ACTION_REQUIRED = {"search": {"term"}, "suggest": {"term", "option"},
                    "pick": set(), "click": {"target"},
                    "add_to": {"item_from", "destination"},
                    "enter": {"target", "value"}, "select": {"target", "option"}}
_PICK_STRATEGIES = {"first_actionable"}
# NOOD_0156 — "field" checks ({field, value}): the target field/control shows
# the entered/selected value. Always runtime-asserted (the probe never types
# data); the kind generated for assertion-free enter/select goals.
# NOOD_0156 — "item_in_destination" checks ({item_in_destination: "cart",
# expected_from: <pick id>, evidence?: screenshot}): the BOUND result caption
# must be visible in the named destination. Identity, never a count — a
# cart-count assertion cannot satisfy "the selected toy is in the cart".
# `evidence: screenshot` (any check kind) compiles the existing NOOD_0153
# "( take a screenshot )" marker onto the verification step itself.
_CHECK_KEYS = {"see", "count", "any_of", "field", "value", "min", "name",
               "after", "item_in_destination", "expected_from", "evidence"}
# NOOD_0163 — the landing-page anchor. NOOD_0158 made an unanchored check
# observe the END state, which is right for the outcome but left a check on
# text the LANDING page shows with nowhere to go: it compiled after the
# actions and asserted a first-page string against the page the flow ended
# on (one red run, then a hand-patch). `after: start` emits it before the
# first action, so a goal spanning pages binds every check to the page it
# was observed on.
_START = "start"
_DISMISSALS = {"popups", "location_prompt", "notifications_prompt"}
_PROBE_KEYS = {"discover"}

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
        "notes": "one of see|count|any_of|field|item_in_destination per "
                 "check; evidence: 'screenshot' on any check; after: "
                 "start|<action id> anchors a check to its page; "
                 "item_in_destination pairs with expected_from: <pick id>; "
                 "navigation: [<url>, ...] = ordered URLs, actions run on "
                 "the last",
    }

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
        add_missing = [a for a in acts if a.get("do") == "add_to"
                       and not a.get("item_from")]
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
            ev = c.get("evidence")
            if isinstance(ev, str) and ev != "screenshot" \
                    and "screenshot" in ev.casefold():
                c["evidence"] = "screenshot"
                notes.append(f"evidence {ev!r} → 'screenshot'")
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
            errs.append(f"actions[{i}]: unknown do {do!r} "
                        f"(valid: {', '.join(sorted(_ACTION_KEYS))})")
            continue
        for k in set(a) - _ACTION_KEYS[do]:
            errs.append(f"actions[{i}] ({do}): unknown field {k!r}")
        for k in _ACTION_REQUIRED[do]:
            if not isinstance(a.get(k), str) or not a[k].strip():
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
        kinds = [k for k in ("see", "count", "any_of", "field",
                             "item_in_destination") if k in c]
        if len(kinds) != 1:
            errs.append(f"checks[{i}]: exactly one of see | count | any_of "
                        "| field | item_in_destination")
            continue
        kind = kinds[0]
        if "evidence" in c and c["evidence"] != "screenshot":
            errs.append(f"checks[{i}]: evidence must be 'screenshot' "
                        "(the NOOD_0153 step marker) when present")
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
        if kind == "any_of":
            alts = c["any_of"]
            if not isinstance(alts, list) or not alts or \
                    not all(isinstance(x, str) and x.strip() for x in alts):
                errs.append(f"checks[{i}]: any_of must be a non-empty list of "
                            "strings")
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
    return errs


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


def navigation_env(goal: dict, app: str) -> list[tuple[str, str]]:
    """Ordered (ENV_KEY, url) pairs for the goal's navigation contract — the
    compiler emits only {env:KEY} references; the URLs live in the app
    environments.yaml. Keys derive from the app + each URL's last path
    segment (universal — no site-specific names), deduplicated by suffix."""
    from urllib.parse import urlsplit
    taken, out = set(), []
    for u in navigation_urls(goal):
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
    return {"search": search, "suggest": suggest, "pick": pick,
            "mutate": mutate,
            "click": clicks or None,
            "open_native_controls": any(a["do"] == "select" for a in actions),
            "discover": bool((goal.get("probe") or {}).get("discover"))}


def _runtime_gate(actions: list) -> int | None:
    """Index of the first action whose effect the probe does NOT perform:
    enter/select values are never typed, a suggestion CLICK-THROUGH never
    happens (--suggest types and reads the list, then closes it — NOOD_0141),
    and everything AFTER a pick runs on the landed page the probe only
    snapshots (NOOD_0156 — the add-to-cart click itself would mutate state,
    so the probe never performs it). Every check anchored at or after the
    gate is runtime-asserted (proven by the run), never claimed probe-proven."""
    for i, a in enumerate(actions):
        if a.get("do") in ("enter", "select", "suggest", "pick", "add_to"):
            return i
    return None


# NOOD_0156 — evidence-bound result selection. Pure: the probe calls this to
# decide WHICH result to click, and the evidence pass records the same caption
# as the bound target — one rule, no drift.

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


def mutation_control(controls: list[dict], destination: str) \
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
        n = _norm(c.get("name"))
        if not n or dn not in n or n == dn:
            continue
        if c.get("kind") == "button" or c.get("submit") \
                or _mutating_name(c.get("name", "")):
            cands.append(c)
    if not cands:
        return None, (f"no probed control mutates into {destination!r} "
                      "(nothing names the destination beyond opening it)")
    names = {_norm(c.get("name")) for c in cands}
    if len(names) > 1:
        return None, ("ambiguous — several distinct probed controls could "
                      "perform the mutation: "
                      + ", ".join(sorted(names)[:4])
                      + "; name the exact control")
    sels = {c.get("selector") for c in cands if c.get("selector")}
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
                      "item/card first")
    return cands[0], None


# --- evidence ----------------------------------------------------------------

def _block_texts(blk: dict) -> list[str]:
    texts = list(blk.get("headings", []))
    for c in blk.get("controls", []):
        if c.get("name"):
            texts.append(c["name"])
    return texts


def _page_blocks(pg: dict) -> list[tuple[dict, str, str | None]]:
    """Provenance-tagged blocks of one probed page: (block, phase, trigger).

    phase is 'initial' | 'reveal' | 'discovered' | 'search'. A revealed block
    the probe reached by AUTOMATIC discovery (auto/discovered) is 'discovered'
    — its controls are never reachable without an explicit goal click that
    opens them. An explicitly-clicked reveal keeps phase 'reveal' and carries
    the trigger name so the compiler can require that click first."""
    blocks: list[tuple[dict, str, str | None]] = [(pg, "initial", None)]
    for rev in pg.get("revealed", []):
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
    reachable at run time."""
    tn = _norm(trigger)
    if not tn:
        return False
    for x in actions:
        if x is action:
            return False
        if x.get("do") == "click":
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


def _iter_controls(blocks: list):
    for blk, phase, trigger in blocks:
        for c in blk.get("controls", []):
            yield c, phase, trigger


def _locate(target: str, blocks: list) \
        -> tuple[dict | None, str | None, str | None, str | None]:
    """(control, phase, trigger, blocking_note) for a goal action target.

    NOOD_0145 — deterministic match order, replacing first-substring-wins
    (which picked a machine-named lookalike, e.g. "login options toggle btn",
    over the visible "sign in" submit control for a generic "login" target):

      1. exact canonical name — UNLESS the probe captured several distinct
         controls sharing that name (NOOD_0156: repeated per-card controls
         like "Add to cart"); an unscoped repeated control blocks instead of
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
    exact = [(c, phase, trigger) for c, phase, trigger in
             _iter_controls(blocks) if _norm(c.get("name")) == t]
    if exact:
        # Distinct selectors = genuinely different elements sharing one name
        # (one per result card/row). The same control snapshotted twice
        # (identical selector, or no selector captured) stays a unique match.
        sels = {c.get("selector") for c, _, _ in exact if c.get("selector")}
        if len(sels) > 1:
            return None, None, None, (
                f"matches {len(exact)} probed controls sharing this exact "
                "name — a repeated control; scope the action to one concrete "
                "instance (an explicit POM selector for that item/card) or "
                "use the exact instance's own name")
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
        if cn and (t in cn or cn in t):
            subs.append((c, phase, trigger))
            if cn not in names:
                names.append(cn)
    if len(names) == 1:
        return (*subs[0], None)
    if len(names) > 1:
        return None, None, None, (
            "ambiguous — matches " + ", ".join(f'"{n}"' for n in names[:4])
            + ("…" if len(names) > 4 else "")
            + "; use the exact probed control name")
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
    n = _norm(needle)
    for blk in blocks:
        for t in _block_texts(blk):
            tn = _norm(t)
            if n and (n in tn or tn in n):
                return t
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
    """'search' when the check anchors at/after the search action, else
    'initial'."""
    actions = goal.get("actions") or []
    search_i = next((i for i, a in enumerate(actions)
                     if a["do"] == "search"), None)
    if search_i is None:
        return "initial"
    after = check.get("after")
    if after is None:
        return "initial"
    anchor_i = next((i for i, a in enumerate(actions)
                     if a.get("id") == after), -1)
    return "search" if anchor_i >= search_i else "initial"


def evidence(goal: dict, probe_result: dict) -> dict:
    """Match every requested action/check against what the probe proved.
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
                "revealed_headings": {}, "headings": []}

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
    initial_scope = [blk for blk, ph, _ in blocks if ph in ("initial", "reveal")]
    search_scope = [blk for blk, ph, _ in blocks if ph == "search"]
    picked_blk = next((blk for blk, ph, _ in blocks if ph == "picked"), None)

    blocking, proven, runtime, bound, resolved = [], {}, [], {}, {}
    mplans: dict[str, dict] = {}
    if nav_block:
        blocking.append(nav_block)
    for i, a in enumerate(actions):
        if a["do"] == "add_to":
            # NOOD_0156 — semantic mutation lowering: resolve the requested
            # "add the picked item to <destination>" to the exact probed
            # mutation control on the landed page — plus at most one
            # probe-PROVEN prerequisite reveal (recorded by the probe with a
            # before/after delta). No proven chain = block; a candidate
            # prerequisite is never compiled on a guess.
            aid = a.get("id") or f"add_to:{a.get('destination', '')}"
            src = a.get("item_from", "")
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
                # NOOD_0167 — name what the landed page DOES offer: a
                # reviewed session dead-ended on this generic blocker while
                # the page's tiles carried a differently-named control the
                # whole time. Vocabulary from the probe's own evidence, so
                # the reader's next move is a rename, not a re-probe.
                names = list(dict.fromkeys(
                    c["name"] for c in (picked.get("controls") or [])
                    if c.get("name")))[:8]
                blocking.append(
                    f'add_to "{a["destination"]}": no proven mutation path '
                    "on the landed page"
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
                blocking.append(
                    f'suggest: option {a["option"]!r} not among the captured '
                    f'suggestions {sg["suggestions"][:8]}')
            else:
                proven[f'suggest:{a["term"]}'] = canon
            continue
        after_pick = any(x.get("do") == "pick" for x in actions[:i])
        ctrl = phase = trigger = note = None
        if after_pick and picked_blk is not None:
            # NOOD_0156 — an action after the pick happens on the landed page:
            # resolve there FIRST, so the landed page's single "Add to cart"
            # wins over the results page's repeated per-card twins.
            ctrl, phase, trigger, note = _locate(
                a["target"], [(picked_blk, "picked", None)])
        if ctrl is None and note is None:
            ctrl, phase, trigger, note = _locate(a["target"], blocks)
        if ctrl is None:
            blocking.append(f'{a["do"]} "{a["target"]}": '
                            + (note or "no probed control matches that name"))
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
    captions = {k: v["caption"] for k, v in bound.items()}
    for i, c in enumerate(goal.get("checks") or []):
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
                dctrl, _, _, dnote = _locate(dest, blocks)
                if dctrl is None:
                    blocking.append(
                        f'check item_in_destination "{dest}": '
                        + (dnote or "no probed control opens that "
                                    "destination — cannot verify there"))
                    continue
                proven[f"destination:{dest}"] = dctrl["name"]
            runtime.append(_check_step(c, captions)[0])
            continue
        after = c.get("after")
        anchor_i = next((j for j, a in enumerate(actions)
                         if a.get("id") == after), -1) if after is not None else -1
        if gate is not None and anchor_i >= gate:
            # Anchored after data the probe never entered — the probe cannot
            # honestly prove it; the run must. Preserved verbatim, never dropped.
            runtime.append(_check_step(c)[0])
            continue
        if "field" in c:
            # NOOD_0156 — a field-shows-value check is always runtime-proven:
            # the probe never types data, so there is nothing to prove it by.
            runtime.append(_check_step(c)[0])
            continue
        scope = search_scope if _check_scope(c, goal) == "search" else initial_scope
        if "see" in c:
            hit = _find_text(c["see"], scope)
            if hit is None:
                blocking.append(f'check "{c["see"]}": no probed heading or '
                                "control shows that text")
            else:
                proven[f"see:{c['see']}"] = hit
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
        else:  # any_of — distinct matching alternatives, not one match ≥ min
            want = c.get("min", 1)
            texts = set()
            for alt in c["any_of"]:
                hit = _find_text(alt, scope or initial_scope)
                if hit is not None:
                    texts.add(_norm(hit))
            if len(texts) >= want:
                proven[f"any_of[{i}]"] = sorted(texts)
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
    return {"blocking": blocking, "proven": proven, "runtime_asserted": runtime,
            "permission_prompts": pg.get("permission_prompts", []),
            "popups_closed": pg.get("popups_closed", 0),
            "results_summary": rsum, "controls": controls,
            "bound_targets": bound, "resolved_controls": resolved,
            "mutation_plans": mplans, "navigation_health": nav_health,
            "revealed_headings": revealed_headings, "headings": headings}


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
                     ("do", "id", "target", "term", "value", "option")
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
        else:
            ok, evid = f'{do}:{a.get("target", "")}' in proven, "probe:control"
        what = a.get("target") or a.get("term") or a.get("destination") or ""
        trace.append({"requirement": f"{do} {what}".strip(),
                      "node": f"actions[{i}]",
                      "evidence": evid if ok else "missing", "ok": bool(ok)})
    runtime = ev.get("runtime_asserted") or []
    for i, c in enumerate(goal.get("checks") or []):
        if not isinstance(c, dict):
            continue
        kind = next((k for k in ("see", "count", "any_of", "field",
                                 "item_in_destination") if k in c), "?")
        if kind == "see":
            ok = f"see:{c['see']}" in proven or any(
                c["see"] in s for s in runtime)
        elif kind == "count":
            ok = f"count:{c['count']}" in proven or any(
                c["count"] in s for s in runtime)
        elif kind == "item_in_destination":
            cap = bound.get(c.get("expected_from", ""), {}).get("caption", "")
            ok = bool(cap) and any(cap in s for s in runtime)
        else:
            ok = f"any_of[{i}]" in proven or bool(runtime)
        entry = {"requirement": f"check {kind}", "node": f"checks[{i}]",
                 "evidence": ("probe+runtime" if ok else "missing"),
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


# --- deterministic compiler --------------------------------------------------

_PERM_STEP = {"geolocation": "the user closes the location prompt",
              "notifications": "the user closes the notifications prompt"}
_DISMISS_PERM = {"location_prompt": "geolocation",
                 "notifications_prompt": "notifications"}
_POPUP_STEP = "closes the popup if it appears within 10 seconds"


def _yaml_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


_JS_RE_SPECIAL = re.compile(r"([.*+?^${}()|\[\]\\/])")


def _any_of_selector(alts: list[str]) -> str:
    """ONE constrained selector carrying EVERY requested alternative — link
    text, title, and alt attributes (where result-tile captions live).
    JS-regex escaping, not re.escape: Python escapes spaces, which a
    unicode-mode JS RegExp rejects."""
    pat = "|".join(_JS_RE_SPECIAL.sub(r"\\\1", a) for a in alts)
    parts = [f'a:text-matches("{pat}", "i")']
    for a in alts:
        safe = a.replace('"', '\\"')
        parts += [f'[title*="{safe}" i]', f'[alt*="{safe}" i]']
    return ", ".join(parts)


def _check_step(c: dict, captions: dict | None = None) -> tuple[str, str | None]:
    """(step body, pom name needed or None) for one check. `captions` maps
    pick-action ids to their bound result captions (item checks). A check with
    evidence: screenshot gets the NOOD_0153 marker ON the verification step —
    the capture attaches to the assertion it proves, never a separate step."""
    if "see" in c:
        body, pom = f'the user sees "{c["see"]}"', None
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
    else:
        name = c.get("name") or "result titles"
        body, pom = f'should see at least {c.get("min", 1)} "{name}"', name
    if c.get("evidence") == "screenshot":
        body += " ( take a screenshot )"
    return body, pom


def _action_step(a: dict, target: str) -> str:
    """`target` is the PROBED canonical control name when one matched — the
    spelling that actually resolves at run time — else the goal's own."""
    if a["do"] == "search":
        return f'User searches for "{a["term"]}"'
    if a["do"] == "click":
        return f'User clicks "{target}"'
    if a["do"] == "enter":
        return f'User enters "{a["value"]}" in the "{target}" field'
    return f'User selects "{a["option"]}" from "{target}"'


def compile_goal(goal: dict, ev: dict, base_url_key: str,
                 nav_keys: list[str] | None = None) -> tuple[str, str | None]:
    """(feature_text, pom_text | None) — deterministically compiled, never
    model-authored. Observed prerequisites (permission prompts, popups) merge
    with requested dismissals and deduplicate; the POM always opens with
    `match: {}` so a scenario that navigates never scopes its keys away.
    NOOD_0156 — `nav_keys` (from navigation_env) emits ONE ordered navigation
    Given per requested URL; without a navigation contract the single
    base-URL Given is unchanged."""
    steps: list[tuple[str, str]] = [
        ("Given", f'User is on "{{env:{k}}}"')
        for k in (nav_keys or [base_url_key])]
    dismissals = goal.get("dismissals") or []
    perms = list(dict.fromkeys(
        [*ev.get("permission_prompts", []),
         *(_DISMISS_PERM[d] for d in dismissals if d in _DISMISS_PERM)]))
    for perm in perms:
        if perm in _PERM_STEP:
            steps.append(("When", _PERM_STEP[perm]))
    if ev.get("popups_closed") or "popups" in dismissals:
        steps.append(("When", _POPUP_STEP))

    pom_entries: dict[str, list[str]] = {}
    checks = goal.get("checks") or []
    actions = goal.get("actions") or []
    bound = ev.get("bound_targets") or {}
    captions = {k: v.get("caption", "") for k, v in bound.items()}

    # NOOD_0163 — one POM key per distinct locator. Every unnamed `any_of`
    # check defaulted to the key "result titles", and `pom_entries.setdefault`
    # keeps the FIRST selector — so a goal checking two pages compiled its
    # second assertion against the first page's locator: the probe proved both
    # texts, the run asserted one of them twice. Distinct selectors now get
    # distinct keys; identical ones still share. (A `count` check keys off its
    # own `count` name, and `see`/`field`/item checks need no POM at all.)
    locator_names: dict[str, str] = {}

    def _named(c: dict) -> dict:
        if "any_of" not in c or c.get("name"):
            return c
        name = locator_names.get(sel := _any_of_selector(c["any_of"]))
        if name is None:
            name = "result titles" + (f" {len(locator_names) + 1}"
                                      if locator_names else "")
            locator_names[sel] = name
        return {**c, "name": name}

    def _emit_check(c: dict):
        c = _named(c)
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
        else:
            pom_entries.setdefault(
                pom_name, [f"{pom_name}:",
                           f'  css: {_yaml_str(_any_of_selector(c["any_of"]))}'])

    # NOOD_0163 — the landing page is the only page an action can't anchor to,
    # so `after: start` is emitted here, before anything is clicked.
    for c in checks:
        if c.get("after") == _START:
            _emit_check(c)

    for a in actions:
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
            for ctrl in chain:
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
        if a["do"] in ("click", "enter", "select"):
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
            ctrl = ctrl or ev.get("controls", {}).get(_norm(a["target"])) \
                or _find_control(a["target"],
                                 [{"controls": list(ev.get("controls", {}).values())}])
        target = ctrl["name"] if ctrl else a.get("target", "")
        steps.append(("When", _action_step(a, target)))
        # POM every goal action target with a stable selector — NOT gated on
        # needs_pom (which is about probe presentation, not a runtime-lookup
        # guarantee). A probe-visible control can still lack a runtime accessible
        # name; the deterministic selector is what makes it resolvable.
        if ctrl and ctrl.get("selector"):
            pom_entries.setdefault(
                ctrl["name"], ctrl.get("pom")
                or [f'{ctrl["name"]}:', f'  css: {_yaml_str(ctrl["selector"])}'])
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

    lines = ["@web", f"Feature: {goal['scenario']}", "",
             f"  Scenario: {goal['scenario']}"]
    prev = None
    for kw, body in steps:
        lines.append(f"    {kw if kw != prev else 'And'} {body}")
        prev = kw
    feature = "\n".join(lines) + "\n"

    pom = None
    if pom_entries:
        body = [line for entry in pom_entries.values() for line in entry]
        pom = "\n".join([f"# Page object — compiled from goal "
                         f"'{goal['scenario']}'",
                         "match: {}   # active on EVERY url — the scenario "
                         "may span pages",
                         *body]) + "\n"
    return feature, pom
